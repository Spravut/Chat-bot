#!/usr/bin/env python3
"""
RabbitMQ vs Redis — broker benchmark.

Runs a matrix of (broker × message_size × target_rate) combinations.
Each run lasts TEST_DURATION seconds. Collects: throughput, avg latency,
p95 latency, sent/received/lost counts.
"""

import base64
import json
import os
import random
import string
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import List

import pika
import redis as redis_lib

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
REDIS_HOST    = os.getenv("REDIS_HOST",    "localhost")

TEST_DURATION = 10   # seconds per run
WARMUP_SECS   = 2    # first N seconds excluded from latency stats
STREAM_MAXLEN = 5_000  # max entries kept in Redis stream (prevents OOM)

MSG_SIZES    = [128, 1_024, 10_240, 102_400]   # 128 B · 1 KB · 10 KB · 100 KB
TARGET_RATES = [1_000, 5_000, 10_000]          # messages / second
BROKERS      = ["rabbitmq", "redis"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_payload(size: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=size))


def fmt_size(b: int) -> str:
    if b >= 1_048_576:
        return f"{b // 1_048_576}MB"
    if b >= 1_024:
        return f"{b // 1_024}KB"
    return f"{b}B"


class RateLimiter:
    """Token-bucket-style rate limiter using monotonic clock."""

    def __init__(self, rate: int):
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._next = time.monotonic()

    def wait(self) -> None:
        if self._interval == 0.0:
            return
        self._next += self._interval
        sleep = self._next - time.monotonic()
        if sleep > 0:
            time.sleep(sleep)


# ── Result container ──────────────────────────────────────────────────────────

@dataclass
class RunResult:
    broker:      str
    msg_size:    int
    target_rate: int
    duration:    int = TEST_DURATION
    sent:        int = 0
    received:    int = 0
    errors:       int = 0
    latencies_ms: List[float] = field(default_factory=list)
    peak_backlog: int   = 0
    avg_backlog:  float = 0.0
    peak_mem_mb:  float = 0.0

    @property
    def lost(self) -> int:
        return max(0, self.sent - self.received)

    @property
    def throughput(self) -> float:
        return self.received / self.duration

    @property
    def avg_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        s = sorted(self.latencies_ms)
        return s[int(len(s) * 0.95)]


# ── RabbitMQ producer / consumer ──────────────────────────────────────────────

def _rmq_producer(queue: str, result: RunResult, payload: str,
                  stop: threading.Event) -> None:
    conn = pika.BlockingConnection(
        pika.ConnectionParameters(host=RABBITMQ_HOST, heartbeat=600,
                                  blocked_connection_timeout=60)
    )
    ch = conn.channel()
    ch.queue_declare(queue=queue, durable=False)
    rl  = RateLimiter(result.target_rate)
    sent = 0

    while not stop.is_set():
        body = json.dumps({"ts": time.time(), "data": payload}).encode()
        try:
            ch.basic_publish(
                exchange="", routing_key=queue, body=body,
                properties=pika.BasicProperties(delivery_mode=1),
            )
            sent += 1
        except Exception:
            result.errors += 1
        rl.wait()

    result.sent = sent
    try:
        conn.close()
    except Exception:
        pass


def _rmq_consumer(queue: str, result: RunResult,
                  stop: threading.Event, warmup_until: float) -> None:
    conn = pika.BlockingConnection(
        pika.ConnectionParameters(host=RABBITMQ_HOST, heartbeat=600,
                                  blocked_connection_timeout=60)
    )
    ch = conn.channel()
    ch.queue_declare(queue=queue, durable=False)
    ch.basic_qos(prefetch_count=50)

    received  = 0
    latencies: List[float] = []

    def on_msg(ch_, method, _props, body):
        nonlocal received
        try:
            msg = json.loads(body)
            if time.time() > warmup_until:
                latencies.append((time.time() - msg["ts"]) * 1000)
            received += 1
            ch_.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            pass

    ch.basic_consume(queue=queue, on_message_callback=on_msg)

    while not stop.is_set():
        conn.process_data_events(time_limit=0.1)

    # Drain: consume whatever is left in the queue.
    # Stop as soon as no message arrives for 2 s (queue empty) or 20 s hard cap.
    drain_hard = time.monotonic() + 20.0
    last_count = received
    idle_since  = time.monotonic()
    while time.monotonic() < drain_hard:
        conn.process_data_events(time_limit=0.2)
        if received > last_count:
            last_count = received
            idle_since  = time.monotonic()
        elif time.monotonic() - idle_since > 2.0:
            break

    result.received    = received
    result.latencies_ms = latencies
    try:
        conn.close()
    except Exception:
        pass


# ── Redis producer / consumer ─────────────────────────────────────────────────

def _redis_producer(stream: str, result: RunResult, payload: str,
                    stop: threading.Event) -> None:
    r  = redis_lib.Redis(host=REDIS_HOST, decode_responses=True)
    rl = RateLimiter(result.target_rate)
    sent = 0

    while not stop.is_set():
        try:
            r.xadd(stream, {"ts": str(time.time()), "data": payload},
                   maxlen=STREAM_MAXLEN, approximate=True)
            sent += 1
        except Exception:
            result.errors += 1
        rl.wait()

    result.sent = sent
    r.close()


def _redis_consumer(stream: str, group: str, result: RunResult,
                    stop: threading.Event, warmup_until: float) -> None:
    r = redis_lib.Redis(host=REDIS_HOST, decode_responses=True)
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis_lib.exceptions.ResponseError:
        pass  # group already exists — carry on

    received  = 0
    latencies: List[float] = []

    while not stop.is_set():
        try:
            entries = r.xreadgroup(group, "c1", {stream: ">"}, count=200, block=100)
            if not entries:
                continue
            for _, msgs in entries:
                for msg_id, fields in msgs:
                    try:
                        if time.time() > warmup_until:
                            latencies.append((time.time() - float(fields["ts"])) * 1000)
                        received += 1
                        r.xack(stream, group, msg_id)
                    except Exception:
                        pass
        except Exception:
            if not stop.is_set():
                time.sleep(0.05)

    # Drain: read whatever the broker still has ready in the stream.
    # Use a short block timeout and a wall-clock deadline so the drain
    # can never hang indefinitely (block=0 would block forever on an empty stream).
    drain_deadline = time.monotonic() + 3.0
    while time.monotonic() < drain_deadline:
        try:
            entries = r.xreadgroup(group, "c1", {stream: ">"}, count=200, block=200)
            if not entries or not entries[0][1]:
                break
            for _, msgs in entries:
                for msg_id, fields in msgs:
                    try:
                        latencies.append((time.time() - float(fields["ts"])) * 1000)
                        received += 1
                        r.xack(stream, group, msg_id)
                    except Exception:
                        pass
        except Exception:
            break

    result.received    = received
    result.latencies_ms = latencies
    r.close()


# ── Monitors (backlog + memory, sampled every second) ────────────────────────

_RMQ_CREDS = base64.b64encode(b"guest:guest").decode()


def _rmq_api(path: str):
    url = f"http://{RABBITMQ_HOST}:15672/api/{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {_RMQ_CREDS}")
    with urllib.request.urlopen(req, timeout=1) as resp:
        return json.loads(resp.read())


def _monitor_rmq(queue: str, result: RunResult, stop: threading.Event) -> None:
    depths: List[int]   = []
    mems:   List[float] = []

    while not stop.is_set():
        try:
            q = _rmq_api(f"queues/%2F/{queue}")
            depths.append(q.get("messages", 0))
        except Exception:
            pass
        try:
            nodes = _rmq_api("nodes")
            if nodes:
                mems.append(nodes[0].get("mem_used", 0) / 1_048_576)
        except Exception:
            pass
        time.sleep(1)

    result.peak_backlog = max(depths, default=0)
    result.avg_backlog  = sum(depths) / len(depths) if depths else 0.0
    result.peak_mem_mb  = max(mems, default=0.0)


def _monitor_redis(stream: str, result: RunResult, stop: threading.Event) -> None:
    r = redis_lib.Redis(host=REDIS_HOST, decode_responses=True)
    depths: List[int]   = []
    mems:   List[float] = []

    while not stop.is_set():
        try:
            depths.append(r.xlen(stream))
        except Exception:
            pass
        try:
            mems.append(r.info("memory")["used_memory"] / 1_048_576)
        except Exception:
            pass
        time.sleep(1)

    result.peak_backlog = max(depths, default=0)
    result.avg_backlog  = sum(depths) / len(depths) if depths else 0.0
    result.peak_mem_mb  = max(mems, default=0.0)
    r.close()


# ── Single test run ───────────────────────────────────────────────────────────

def run_one(broker: str, msg_size: int, target_rate: int) -> RunResult:
    result       = RunResult(broker=broker, msg_size=msg_size, target_rate=target_rate)
    payload      = make_payload(msg_size)
    stop         = threading.Event()
    warmup_until = time.time() + WARMUP_SECS
    run_id       = f"{broker}_{msg_size}_{target_rate}_{int(time.time())}"

    if broker == "rabbitmq":
        queue    = f"bench_{run_id}"
        consumer = threading.Thread(target=_rmq_consumer,
                                    args=(queue, result, stop, warmup_until), daemon=True)
        producer = threading.Thread(target=_rmq_producer,
                                    args=(queue, result, payload, stop), daemon=True)
        monitor  = threading.Thread(target=_monitor_rmq,
                                    args=(queue, result, stop), daemon=True)
    else:
        stream   = f"bench_{run_id}"
        group    = "grp"
        consumer = threading.Thread(target=_redis_consumer,
                                    args=(stream, group, result, stop, warmup_until), daemon=True)
        producer = threading.Thread(target=_redis_producer,
                                    args=(stream, result, payload, stop), daemon=True)
        monitor  = threading.Thread(target=_monitor_redis,
                                    args=(stream, result, stop), daemon=True)

    consumer.start()
    time.sleep(0.5)   # give consumer time to declare queue / create stream
    producer.start()
    monitor.start()

    time.sleep(TEST_DURATION)
    stop.set()

    producer.join(timeout=5)
    consumer.join(timeout=TEST_DURATION + 15)
    monitor.join(timeout=3)

    return result


# ── Wait for brokers ──────────────────────────────────────────────────────────

def wait_for_rabbitmq() -> None:
    for i in range(30):
        try:
            c = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
            c.close()
            print("RabbitMQ: ready")
            return
        except Exception:
            print(f"  Waiting for RabbitMQ... ({i + 1}/30)")
            time.sleep(2)
    raise RuntimeError("RabbitMQ not available after 60 s")


def wait_for_redis() -> None:
    r = redis_lib.Redis(host=REDIS_HOST)
    for i in range(20):
        try:
            r.ping()
            r.close()
            print("Redis: ready")
            return
        except Exception:
            print(f"  Waiting for Redis... ({i + 1}/20)")
            time.sleep(2)
    raise RuntimeError("Redis not available after 40 s")


# ── Results table ─────────────────────────────────────────────────────────────

def print_table(results: List[RunResult]) -> None:
    cols   = ["Broker", "Size", "Target/s", "Sent", "Recv", "Lost", "Tput/s",
              "Avg ms", "P95 ms", "BacklogPk", "MemPk MB"]
    widths = [12,        7,      9,          9,      9,      7,       9,
              9,          9,       10,          10]
    sep = " ".join("-" * w for w in widths)

    print("\n" + "=" * len(sep))
    print("BENCHMARK RESULTS")
    print("=" * len(sep))
    print(" ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print(sep)

    prev_size = None
    for r in results:
        if prev_size and r.msg_size != prev_size:
            print(sep)
        prev_size = r.msg_size

        row = [
            r.broker,
            fmt_size(r.msg_size),
            f"{r.target_rate:,}",
            f"{r.sent:,}",
            f"{r.received:,}",
            f"{r.lost:,}",
            f"{r.throughput:.0f}",
            f"{r.avg_ms:.1f}",
            f"{r.p95_ms:.1f}",
            f"{r.peak_backlog:,}",
            f"{r.peak_mem_mb:.1f}",
        ]
        print(" ".join(v.ljust(w) for v, w in zip(row, widths)))

    print("=" * len(sep))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    total = len(MSG_SIZES) * len(TARGET_RATES) * len(BROKERS)
    est   = total * (TEST_DURATION + 1) // 60

    print("=" * 55)
    print("  RabbitMQ vs Redis — Broker Benchmark")
    print(f"  {total} runs × {TEST_DURATION}s  ≈ {est} min total")
    print("=" * 55)

    wait_for_rabbitmq()
    wait_for_redis()

    results: List[RunResult] = []
    i = 0

    for msg_size in MSG_SIZES:
        for target_rate in TARGET_RATES:
            for broker in BROKERS:
                i += 1
                print(
                    f"\n[{i}/{total}]  broker={broker:<10}  "
                    f"size={fmt_size(msg_size):<6}  rate={target_rate:>6,}/s"
                )
                r = run_one(broker, msg_size, target_rate)
                results.append(r)
                print(
                    f"  → sent={r.sent:,}  recv={r.received:,}  lost={r.lost:,}  "
                    f"tput={r.throughput:.0f}/s  avg={r.avg_ms:.1f}ms  p95={r.p95_ms:.1f}ms  "
                    f"backlog_peak={r.peak_backlog:,}  mem_peak={r.peak_mem_mb:.1f}MB"
                )

    print_table(results)
    print("\nDone.")


if __name__ == "__main__":
    main()
