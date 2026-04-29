#!/usr/bin/env python3
"""
Cache Strategy Benchmark
Strategies: Cache-Aside, Write-Through, Write-Back
"""
import asyncio
import asyncpg
import redis.asyncio as aioredis
import random
import time
import json
from dataclasses import dataclass
from typing import Optional

# ─── Config ──────────────────────────────────────────────────────────────────

REDIS_URL = "redis://localhost:6379/0"
DB_DSN    = "postgresql://bench:bench@localhost:5432/bench"

NUM_KEYS       = 100   # number of user rows to work with
DURATION       = 15    # seconds per scenario
CONCURRENCY    = 20    # concurrent async workers
CACHE_TTL      = 60    # Redis key TTL in seconds
FLUSH_INTERVAL = 1.0   # Write-Back: seconds between DB flushes

SCENARIOS = [
    ("read-heavy",  0.80),
    ("balanced",    0.50),
    ("write-heavy", 0.20),
]

STRATEGIES = ["cache-aside", "write-through", "write-back"]


# ─── Metrics ─────────────────────────────────────────────────────────────────

@dataclass
class Metrics:
    total_requests: int   = 0
    total_latency_ms: float = 0.0
    cache_hits:   int = 0
    cache_misses: int = 0
    db_reads:     int = 0
    db_writes:    int = 0

    def record(self, latency_ms: float, cache_hit: Optional[bool] = None,
               db_read: bool = False, db_write: bool = False):
        self.total_requests  += 1
        self.total_latency_ms += latency_ms
        if cache_hit is True:
            self.cache_hits += 1
        elif cache_hit is False:
            self.cache_misses += 1
        if db_read:
            self.db_reads += 1
        if db_write:
            self.db_writes += 1

    def summary(self, elapsed: float) -> dict:
        total_cache = self.cache_hits + self.cache_misses
        return {
            "throughput_rps": round(self.total_requests / elapsed, 1),
            "avg_latency_ms": round(self.total_latency_ms / max(self.total_requests, 1), 3),
            "db_reads":       self.db_reads,
            "db_writes":      self.db_writes,
            "cache_hits":     self.cache_hits,
            "cache_misses":   self.cache_misses,
            "hit_rate_pct":   round(self.cache_hits / max(total_cache, 1) * 100, 1),
            "total_requests": self.total_requests,
        }


# ─── Strategy 1: Cache-Aside (Lazy Loading / Write-Around) ───────────────────
#
# READ:  check cache → on miss, load from DB + populate cache
# WRITE: write DB only → invalidate cache entry

async def ca_read(key: int, pool, redis, metrics: Metrics):
    t0 = time.perf_counter()
    cached = await redis.get(f"user:{key}")
    if cached:
        metrics.record((time.perf_counter() - t0) * 1000, cache_hit=True)
        return json.loads(cached)
    row = await pool.fetchrow("SELECT id, value FROM users WHERE id = $1", key)
    if row:
        await redis.setex(f"user:{key}", CACHE_TTL,
                          json.dumps({"id": row["id"], "value": row["value"]}))
    metrics.record((time.perf_counter() - t0) * 1000, cache_hit=False, db_read=True)
    return row


async def ca_write(key: int, value: str, pool, redis, metrics: Metrics):
    t0 = time.perf_counter()
    await pool.execute(
        "UPDATE users SET value = $1, updated_at = NOW() WHERE id = $2", value, key)
    await redis.delete(f"user:{key}")          # invalidate stale entry
    metrics.record((time.perf_counter() - t0) * 1000, db_write=True)


# ─── Strategy 2: Write-Through ───────────────────────────────────────────────
#
# READ:  check cache → on miss, load from DB + populate cache
# WRITE: write DB AND cache synchronously

async def wt_read(key: int, pool, redis, metrics: Metrics):
    t0 = time.perf_counter()
    cached = await redis.get(f"user:{key}")
    if cached:
        metrics.record((time.perf_counter() - t0) * 1000, cache_hit=True)
        return json.loads(cached)
    row = await pool.fetchrow("SELECT id, value FROM users WHERE id = $1", key)
    if row:
        await redis.setex(f"user:{key}", CACHE_TTL,
                          json.dumps({"id": row["id"], "value": row["value"]}))
    metrics.record((time.perf_counter() - t0) * 1000, cache_hit=False, db_read=True)
    return row


async def wt_write(key: int, value: str, pool, redis, metrics: Metrics):
    t0 = time.perf_counter()
    await pool.execute(
        "UPDATE users SET value = $1, updated_at = NOW() WHERE id = $2", value, key)
    await redis.setex(f"user:{key}", CACHE_TTL,
                      json.dumps({"id": key, "value": value}))   # keep cache current
    metrics.record((time.perf_counter() - t0) * 1000, db_write=True)


# ─── Strategy 3: Write-Back (Write-Behind) ───────────────────────────────────
#
# READ:  check cache → on miss, load from DB + populate cache
# WRITE: write cache only; DB is flushed asynchronously in batches

class WriteBackBuffer:
    def __init__(self):
        self._dirty: dict[int, str] = {}
        self._lock = asyncio.Lock()
        self.total_flushed = 0
        self.flush_rounds  = 0

    async def put(self, key: int, value: str):
        async with self._lock:
            self._dirty[key] = value

    async def flush(self, pool) -> int:
        async with self._lock:
            if not self._dirty:
                return 0
            items = list(self._dirty.items())
            self._dirty.clear()

        async with pool.acquire() as conn:
            async with conn.transaction():
                for key, value in items:
                    await conn.execute(
                        "UPDATE users SET value = $1, updated_at = NOW() WHERE id = $2",
                        value, key)

        n = len(items)
        self.total_flushed += n
        self.flush_rounds  += 1
        return n

    async def flush_loop(self, pool, stop_event: asyncio.Event):
        while not stop_event.is_set():
            await asyncio.sleep(FLUSH_INTERVAL)
            n = await self.flush(pool)
            if n:
                print(f"    [Write-Back] flush #{self.flush_rounds}: "
                      f"{n} records -> DB  (total flushed: {self.total_flushed})")
        n = await self.flush(pool)   # final flush after test ends
        if n:
            print(f"    [Write-Back] final flush: {n} records -> DB")


async def wb_read(key: int, pool, redis, metrics: Metrics):
    t0 = time.perf_counter()
    cached = await redis.get(f"user:{key}")
    if cached:
        metrics.record((time.perf_counter() - t0) * 1000, cache_hit=True)
        return json.loads(cached)
    row = await pool.fetchrow("SELECT id, value FROM users WHERE id = $1", key)
    if row:
        await redis.setex(f"user:{key}", CACHE_TTL,
                          json.dumps({"id": row["id"], "value": row["value"]}))
    metrics.record((time.perf_counter() - t0) * 1000, cache_hit=False, db_read=True)
    return row


async def wb_write(key: int, value: str, pool, redis, metrics: Metrics,
                   buffer: WriteBackBuffer):
    t0 = time.perf_counter()
    await redis.setex(f"user:{key}", CACHE_TTL,
                      json.dumps({"id": key, "value": value}))   # cache first
    await buffer.put(key, value)                                  # queue for DB
    # no db_write counted — happens asynchronously
    metrics.record((time.perf_counter() - t0) * 1000)


# ─── Load Generator ──────────────────────────────────────────────────────────

async def run_scenario(strategy: str, scenario: str, read_ratio: float,
                       pool, redis) -> dict:
    metrics    = Metrics()
    stop_event = asyncio.Event()
    buffer     = WriteBackBuffer() if strategy == "write-back" else None
    keys       = list(range(1, NUM_KEYS + 1))

    await redis.flushdb()   # clean slate for every test

    async def worker():
        while not stop_event.is_set():
            key   = random.choice(keys)
            value = f"v{random.randint(10000, 99999)}"
            do_read = random.random() < read_ratio

            if strategy == "cache-aside":
                if do_read:
                    await ca_read(key, pool, redis, metrics)
                else:
                    await ca_write(key, value, pool, redis, metrics)

            elif strategy == "write-through":
                if do_read:
                    await wt_read(key, pool, redis, metrics)
                else:
                    await wt_write(key, value, pool, redis, metrics)

            else:  # write-back
                if do_read:
                    await wb_read(key, pool, redis, metrics)
                else:
                    await wb_write(key, value, pool, redis, metrics, buffer)

    workers    = [asyncio.create_task(worker()) for _ in range(CONCURRENCY)]
    flush_task = asyncio.create_task(buffer.flush_loop(pool, stop_event)) if buffer else None

    t0 = time.time()
    await asyncio.sleep(DURATION)
    stop_event.set()
    await asyncio.gather(*workers, return_exceptions=True)
    if flush_task:
        await flush_task
    elapsed = time.time() - t0

    result = metrics.summary(elapsed)
    result["strategy"] = strategy
    result["scenario"] = scenario
    if buffer:
        result["wb_flush_rounds"]  = buffer.flush_rounds
        result["wb_total_flushed"] = buffer.total_flushed
    return result


async def reset_db(pool):
    await pool.execute(
        "UPDATE users SET value = 'initial_' || id::text, updated_at = NOW()")


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    sep = "=" * 72

    print(sep)
    print("  CACHE STRATEGY BENCHMARK")
    print(sep)
    print(f"  Workers: {CONCURRENCY}  |  Duration per test: {DURATION}s  |  Keys: {NUM_KEYS}")
    print(f"  Flush interval (Write-Back): {FLUSH_INTERVAL}s")
    print()

    pool  = await asyncpg.create_pool(DB_DSN, min_size=5, max_size=CONCURRENCY + 5)
    redis = await aioredis.from_url(REDIS_URL, decode_responses=True)

    all_results: list[dict] = []

    for strategy in STRATEGIES:
        print("-" * 72)
        print(f"  Strategy: {strategy.upper()}")
        print("-" * 72)

        for scenario, read_ratio in SCENARIOS:
            write_ratio = 1 - read_ratio
            label = f"  [{scenario}] read={int(read_ratio*100)}% / write={int(write_ratio*100)}%"
            print(f"{label} ...", flush=True)

            await reset_db(pool)
            result = await run_scenario(strategy, scenario, read_ratio, pool, redis)
            all_results.append(result)

            extra = ""
            if "wb_flush_rounds" in result:
                extra = (f"  |  flushes={result['wb_flush_rounds']} "
                         f"flushed={result['wb_total_flushed']}")
            print(f"    >> {result['throughput_rps']:>7} rps  "
                  f"latency={result['avg_latency_ms']}ms  "
                  f"hit={result['hit_rate_pct']}%  "
                  f"db_reads={result['db_reads']}  "
                  f"db_writes={result['db_writes']}"
                  f"{extra}")

        print()

    await pool.close()
    await redis.aclose()

    # ── Results table ──────────────────────────────────────────────────────────
    print(sep)
    print("  FULL RESULTS TABLE")
    print(sep)
    hdr = (f"{'Strategy':<14} {'Scenario':<12} {'RPS':>7} {'Lat(ms)':>9} "
           f"{'DB Reads':>9} {'DB Writes':>10} {'Hit%':>6}")
    print(hdr)
    print("-" * 72)

    for r in all_results:
        print(
            f"{r['strategy']:<14} {r['scenario']:<12} "
            f"{r['throughput_rps']:>7} {r['avg_latency_ms']:>9} "
            f"{r['db_reads']:>9} {r['db_writes']:>10} {r['hit_rate_pct']:>5}%"
        )

    print()
    print("  Done.")
    print(sep)


if __name__ == "__main__":
    asyncio.run(main())
