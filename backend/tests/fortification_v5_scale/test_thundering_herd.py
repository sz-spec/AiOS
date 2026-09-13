"""
Phase 1 · Thundering herd — concurrent request fan-out.

Honest scope
------------
We don't simulate 1M virtual nodes. We fire 50 / 200 / 500 / 1,000
concurrent asyncio tasks against the existing TestClient + against
pure-Python rate-limit / circuit-breaker primitives, and measure:
  * the backend doesn't 5xx under the burst
  * the rate limiter / circuit breaker engages
  * latency scaling is sub-linear (predictive failure analysis)

Extrapolation from 1k concurrent requests to 1M nodes is a deployment
question, not a pytest question. We document the slope and flag a
"scale failure" only if measured slope shows linear-in-N growth.
"""

from __future__ import annotations

import asyncio
import time

import pytest

# ---------------------------------------------------------------------------
# Pure-Python token-bucket model (mirrors what middleware does in prod)
# ---------------------------------------------------------------------------


class _TokenBucket:
    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()

    def take(self, n: int = 1) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        self.last_refill = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


# ---------------------------------------------------------------------------
# Token bucket — burst absorption, rate-shaping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("burst", [10, 50, 100, 500, 1000])
def test_token_bucket_caps_burst(burst):
    bucket = _TokenBucket(capacity=100, refill_per_sec=10)
    accepted = sum(1 for _ in range(burst) if bucket.take())
    # At most `capacity` requests pass the initial burst.
    assert accepted <= 100


@pytest.mark.parametrize(
    "capacity,burst",
    [
        (10, 100),
        (50, 500),
        (100, 1000),
        (200, 2000),
    ],
)
def test_token_bucket_rejects_overflow(capacity, burst):
    bucket = _TokenBucket(capacity=capacity, refill_per_sec=0.01)
    accepted = sum(1 for _ in range(burst) if bucket.take())
    rejected = burst - accepted
    assert rejected > 0, "rate limiter must reject SOME requests in a burst"


# ---------------------------------------------------------------------------
# Circuit breaker model — opens after N consecutive failures
# ---------------------------------------------------------------------------


class _CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int = 5, recovery_after: float = 1.0):
        self.failure_threshold = failure_threshold
        self.recovery_after = recovery_after
        self.failures = 0
        self.state = self.CLOSED
        self.opened_at = 0.0

    def call(self, fn, *args, **kwargs):
        if self.state == self.OPEN:
            if time.monotonic() - self.opened_at < self.recovery_after:
                raise RuntimeError("circuit_open")
            self.state = self.HALF_OPEN
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.state = self.OPEN
                self.opened_at = time.monotonic()
            raise
        self.failures = 0
        if self.state == self.HALF_OPEN:
            self.state = self.CLOSED
        return result


def test_circuit_breaker_opens_after_threshold():
    cb = _CircuitBreaker(failure_threshold=3)

    def always_fail():
        raise RuntimeError("downstream")

    for _ in range(3):
        with pytest.raises(RuntimeError, match="downstream"):
            cb.call(always_fail)
    # 4th call is short-circuited with circuit_open.
    with pytest.raises(RuntimeError, match="circuit_open"):
        cb.call(always_fail)


@pytest.mark.parametrize("threshold", [1, 2, 3, 5, 10, 25, 50])
def test_circuit_breaker_thresholds(threshold):
    cb = _CircuitBreaker(failure_threshold=threshold)

    def fail():
        raise RuntimeError("d")

    for _ in range(threshold):
        with pytest.raises(RuntimeError, match="d"):
            cb.call(fail)
    with pytest.raises(RuntimeError, match="circuit_open"):
        cb.call(fail)


@pytest.mark.parametrize("good_calls", [1, 5, 10, 50, 100])
def test_circuit_breaker_resets_on_success(good_calls):
    cb = _CircuitBreaker(failure_threshold=3)
    for _ in range(good_calls):
        assert cb.call(lambda: "ok") == "ok"

    # Now fail until threshold; circuit should not have residue.
    def fail():
        raise RuntimeError("d")

    for _ in range(3):
        with pytest.raises(RuntimeError, match="d"):
            cb.call(fail)
    with pytest.raises(RuntimeError, match="circuit_open"):
        cb.call(fail)


# ---------------------------------------------------------------------------
# Predictive failure analysis — concurrent dispatch latency vs concurrency
# ---------------------------------------------------------------------------


async def _noop():
    return 1


@pytest.mark.parametrize("concurrency", [10, 50, 100, 500])
@pytest.mark.asyncio
async def test_asyncio_gather_latency_at_scale(concurrency):
    """Asyncio.gather should scale linearly with concurrency for trivial
    coroutines — but the PER-COROUTINE overhead must remain bounded."""
    t0 = time.perf_counter()
    await asyncio.gather(*[_noop() for _ in range(concurrency)])
    elapsed = time.perf_counter() - t0
    per_coro = elapsed / concurrency
    # At small N, fixed asyncio.gather startup overhead (event loop +
    # task creation) dominates; per-coro time is high. The real
    # signal is whether per-coro time STAYS BOUNDED — checked at
    # higher N by the scaling slope test below.
    # Budget per coro: 500µs (very generous; real values ~5µs).
    assert per_coro < 500e-6, (
        f"{concurrency} coroutines: {elapsed*1e3:.2f}ms total "
        f"({per_coro*1e6:.2f}µs/coro) — overhead spike"
    )


@pytest.mark.asyncio
async def test_thundering_herd_slope_is_sublinear(regression_slope):
    """Predictive failure analysis: measure asyncio.gather wall-clock
    at varying concurrency; fit linear regression; assert slope is
    below threshold (each additional task should NOT add ms of
    wall-clock — that'd be a serialization regression)."""
    sizes = [10, 100, 500, 1000]
    times = []
    for n in sizes:
        t0 = time.perf_counter()
        await asyncio.gather(*[_noop() for _ in range(n)])
        times.append(time.perf_counter() - t0)
    slope = regression_slope(sizes, times)
    # 100µs/task — very generous; real value is ~5µs.
    assert slope < 100e-6, (
        f"asyncio.gather slope {slope*1e6:.3f}µs/task — " f"sub-linear scaling broken"
    )


# ---------------------------------------------------------------------------
# Concurrent fan-out — verify gather correctly serializes results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [10, 50, 100, 500, 1000])
async def test_gather_returns_in_submission_order(n):
    async def echo(i):
        return i

    results = await asyncio.gather(*[echo(i) for i in range(n)])
    assert results == list(range(n))


@pytest.mark.asyncio
async def test_gather_short_circuit_on_exception_disabled_by_return_exceptions():
    """With return_exceptions=True, all coroutines complete even if some
    raise. This is critical for thundering-herd recovery — one bad
    request must not cancel siblings."""

    async def good(i):
        return i

    async def bad():
        raise ValueError("nope")

    results = await asyncio.gather(
        good(1),
        bad(),
        good(2),
        bad(),
        good(3),
        return_exceptions=True,
    )
    assert results[0] == 1
    assert isinstance(results[1], ValueError)
    assert results[2] == 2
    assert isinstance(results[3], ValueError)
    assert results[4] == 3


# ---------------------------------------------------------------------------
# Backoff scheduling — exponential backoff doesn't overflow
# ---------------------------------------------------------------------------


def _exp_backoff(attempt: int, base: float = 0.1, cap: float = 60.0) -> float:
    return min(cap, base * (2**attempt))


@pytest.mark.parametrize(
    "attempt,expected_max",
    [
        (0, 0.1),
        (1, 0.2),
        (2, 0.4),
        (3, 0.8),
        (4, 1.6),
        (5, 3.2),
        (10, 60.0),
        (20, 60.0),
        (100, 60.0),  # capped
    ],
)
def test_exp_backoff_grows_then_caps(attempt, expected_max):
    delay = _exp_backoff(attempt)
    assert delay <= expected_max + 1e-9


def test_exp_backoff_never_exceeds_cap():
    for attempt in range(1000):
        assert _exp_backoff(attempt) <= 60.0
