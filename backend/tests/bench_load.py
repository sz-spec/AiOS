"""
bench_load.py — Backend Load Simulation
========================================
Fires 50 concurrent requests at /api/health and /api/v-core/status.
Asserts P95 < 200ms.  Run with:

    python -m pytest backend/tests/bench_load.py -v
    # or standalone:
    python backend/tests/bench_load.py
"""

import asyncio
import statistics
import time

import httpx
import pytest

BASE_URL = "http://127.0.0.1:8000"
CONCURRENCY = 50
P95_LIMIT_MS = 200.0
ENDPOINTS = ["/health", "/api/v-core/workflow-actions"]


async def _fire(client: httpx.AsyncClient, url: str) -> float:
    """Send one GET and return latency in ms."""
    start = time.monotonic()
    resp = await client.get(url)
    elapsed_ms = (time.monotonic() - start) * 1000.0
    assert resp.status_code in (200, 307), f"{url} returned {resp.status_code}"
    return elapsed_ms


async def _run_load(endpoint: str) -> dict:
    """Fire CONCURRENCY requests at a single endpoint and collect stats."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        tasks = [_fire(client, endpoint) for _ in range(CONCURRENCY)]
        latencies = await asyncio.gather(*tasks)

    latencies_sorted = sorted(latencies)
    p50 = latencies_sorted[len(latencies_sorted) // 2]
    p95_idx = int(len(latencies_sorted) * 0.95) - 1
    p95 = latencies_sorted[max(p95_idx, 0)]
    p99_idx = int(len(latencies_sorted) * 0.99) - 1
    p99 = latencies_sorted[max(p99_idx, 0)]

    return {
        "endpoint": endpoint,
        "requests": CONCURRENCY,
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        "mean_ms": statistics.mean(latencies),
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
    }


# ── pytest entry points ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_p95():
    """P95 latency for /health must be under 200ms."""
    stats = await _run_load("/health")
    print(
        f"\n  /health  —  P50={stats['p50_ms']:.1f}ms  P95={stats['p95_ms']:.1f}ms  P99={stats['p99_ms']:.1f}ms"
    )
    assert (
        stats["p95_ms"] < P95_LIMIT_MS
    ), f"/health P95 = {stats['p95_ms']:.1f}ms exceeds {P95_LIMIT_MS}ms limit"


@pytest.mark.asyncio
async def test_vcore_workflow_actions_p95():
    """P95 latency for /api/v-core/workflow-actions must be under 200ms."""
    stats = await _run_load("/api/v-core/workflow-actions")
    print(
        f"\n  /api/v-core/workflow-actions  —  P50={stats['p50_ms']:.1f}ms  P95={stats['p95_ms']:.1f}ms  P99={stats['p99_ms']:.1f}ms"
    )
    assert (
        stats["p95_ms"] < P95_LIMIT_MS
    ), f"/api/v-core/workflow-actions P95 = {stats['p95_ms']:.1f}ms exceeds {P95_LIMIT_MS}ms limit"


# ── standalone runner ─────────────────────────────────────────────────


async def _main():
    print(f"Load test: {CONCURRENCY} concurrent requests per endpoint")
    print(f"Target: P95 < {P95_LIMIT_MS}ms\n")

    for ep in ENDPOINTS:
        stats = await _run_load(ep)
        status = "PASS" if stats["p95_ms"] < P95_LIMIT_MS else "FAIL"
        print(
            f"  [{status}] {ep:30s}  "
            f"min={stats['min_ms']:6.1f}  mean={stats['mean_ms']:6.1f}  "
            f"P50={stats['p50_ms']:6.1f}  P95={stats['p95_ms']:6.1f}  "
            f"P99={stats['p99_ms']:6.1f}  max={stats['max_ms']:6.1f}"
        )

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(_main())
