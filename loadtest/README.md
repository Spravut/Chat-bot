# Load testing (Apache JMeter)

The bot exposes a Prometheus metrics endpoint on `:9100`. JMeter pummels it
under two scenarios (steady + spike) to demonstrate:

1. The bot stays responsive under concurrent HTTP load.
2. The metrics endpoint itself doesn't degrade as the application accumulates
   business activity.
3. Live values of `dating_bot_*` counters/histograms can be watched in
   Prometheus (http://localhost:9090) during the run.

## Running

```bash
# 1. Bring up the stack
docker-compose up -d

# 2. Run the test plan (CLI / non-GUI mode is recommended for benchmarks)
jmeter -n -t loadtest/dating_bot_load.jmx -l loadtest/results.jtl

# 3. Generate the HTML report
jmeter -g loadtest/results.jtl -o loadtest/report/
```

Open `loadtest/report/index.html` for percentile graphs.

## Scenarios

| Scenario | Threads | Ramp-up | Duration | Purpose                      |
|----------|---------|---------|----------|------------------------------|
| Steady   | 10      | 5s      | 60s      | Sustained load — p95 latency |
| Spike    | 30      | 3s      | 30s      | Burst handling — error rate  |

> **About these numbers**: `prometheus_client.start_http_server` uses Python's
> single-threaded `wsgiref.simple_server.WSGIServer`. It's designed to be
> scraped by Prometheus once every 15s, not to handle hundreds of concurrent
> clients. With ≥50 concurrent threads firing as fast as possible, the TCP
> accept backlog overflows and connections get rejected (Avg ≈ 0ms, 100%
> errors). The values above are tuned to exercise the endpoint meaningfully
> without exhausting the bundled WSGIServer — if you need higher concurrency,
> swap the metrics server for `aiohttp` or `uvicorn` with the prometheus
> ASGI app.

## What to look at

- **Summary Report → Avg / 90% / Error %** — the headline numbers.
- **Aggregate Report** — per-sampler breakdown.
- **Prometheus** (http://localhost:9090) while the test runs:
  - `rate(dating_bot_tg_updates_total[1m])` — Telegram update throughput.
  - `histogram_quantile(0.95, rate(dating_bot_ranking_query_seconds_bucket[1m]))`
    — ranking query p95 latency.
  - `dating_bot_feed_refills_total` — feed cache exhaustion rate.
