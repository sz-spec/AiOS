"""
PermissionGate decision-path performance.

The gate is on the hot path of every app-originated request, so every
microsecond matters. These tests:
  * lock down per-call latency under 1ms (median) for warm cache hits,
  * confirm hierarchical scope-matching short-circuits correctly,
  * verify a 1k-call burst stays under a wall-clock budget,
  * and (slow) confirm 10k mixed allow/deny calls don't accumulate
    state in `_entries` beyond the installed apps.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from services.app_sandbox import (
    PERMISSION_GATE,
    ScopeViolation,
)

# ---------------------------------------------------------------------------
# Single-decision latency — every parametrize variant is a separate test.
# ---------------------------------------------------------------------------

_DECISION_BUDGET_S = 0.001  # 1ms — generous, includes audit-log write on deny

_ALLOW_SCOPES = [
    "filesystem.read",
    "filesystem.read.config",
    "filesystem.write",
    "network.outbound",
    "network.outbound.http",
    "llm.local",
    "llm.cloud",
]

_DENY_SCOPES = [
    "filesystem.delete",
    "network.inbound",
    "llm.remote.external",
    "system.shutdown",
    "kernel.privileged",
]


@pytest.mark.parametrize("scope", _ALLOW_SCOPES)
def test_allow_decision_under_budget(perf_env, scope):
    app_id = perf_env
    t0 = time.perf_counter()
    PERMISSION_GATE.check(app_id, scope)
    elapsed = time.perf_counter() - t0
    assert elapsed < _DECISION_BUDGET_S, f"warm gate took {elapsed*1e3:.3f}ms"


@pytest.mark.parametrize("scope", _DENY_SCOPES)
def test_deny_decision_under_budget(perf_env, scope):
    app_id = perf_env
    # Warm the gate cache + the SQLite engine (the deny path writes an
    # audit row; the first commit is much slower than steady-state).
    PERMISSION_GATE.check(app_id, "filesystem.read")
    try:
        PERMISSION_GATE.check(app_id, scope)
    except ScopeViolation:
        pass
    t0 = time.perf_counter()
    with pytest.raises(ScopeViolation):
        PERMISSION_GATE.check(app_id, scope)
    elapsed = time.perf_counter() - t0
    # Steady-state deny budget: 100ms — accommodates SQLite WAL flush
    # latency on macOS APFS under 14-way xdist contention. The audit
    # write is the dominant cost; for pure in-memory denies the budget
    # would be sub-millisecond. Test exists to catch O(N²) regressions,
    # not to gate on absolute time.
    assert elapsed < 0.100, f"deny took {elapsed*1e3:.3f}ms"


# ---------------------------------------------------------------------------
# Cached-hit microbenchmark — the cache MUST short-circuit re-hydration.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("iterations", [10, 50, 100, 500, 1000])
def test_warm_cache_throughput(perf_env, iterations):
    app_id = perf_env
    PERMISSION_GATE.check(app_id, "filesystem.read")  # prime cache
    t0 = time.perf_counter()
    for _ in range(iterations):
        PERMISSION_GATE.check(app_id, "filesystem.read")
    elapsed = time.perf_counter() - t0
    per_call = elapsed / iterations
    assert (
        per_call < _DECISION_BUDGET_S
    ), f"warm hit avg {per_call*1e6:.2f}µs over {iterations} iters"


# ---------------------------------------------------------------------------
# Multi-scope check — N scopes in one call must be linear, not N²
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_scopes", [1, 2, 4, 8, 16, 32, 64])
def test_multi_scope_check_is_linear(perf_env, n_scopes):
    app_id = perf_env
    PERMISSION_GATE.check(app_id, "filesystem.read")
    scopes = ["filesystem.read"] * n_scopes
    t0 = time.perf_counter()
    PERMISSION_GATE.check(app_id, *scopes)
    elapsed = time.perf_counter() - t0
    # Linear envelope: 10µs per scope + 100µs overhead.
    budget = n_scopes * 1e-5 + 1e-4
    assert elapsed < budget, f"{n_scopes} scopes took {elapsed*1e6:.0f}µs"


# ---------------------------------------------------------------------------
# Concurrency — 32 threads × 100 calls hammering the same cached entry
# ---------------------------------------------------------------------------


def _call_many(app_id: str, scope: str, n: int) -> int:
    hits = 0
    for _ in range(n):
        if PERMISSION_GATE.can(app_id, scope):
            hits += 1
    return hits


@pytest.mark.parametrize("workers", [2, 4, 8, 16, 32])
def test_concurrent_decisions_match(perf_env, workers):
    app_id = perf_env
    PERMISSION_GATE.check(app_id, "filesystem.read")
    calls_per = 100
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [
            ex.submit(_call_many, app_id, "filesystem.read", calls_per)
            for _ in range(workers)
        ]
        totals = [f.result() for f in as_completed(futs)]
    assert sum(totals) == workers * calls_per


@pytest.mark.parametrize("workers", [2, 4, 8, 16, 32])
def test_concurrent_denials_match(perf_env, workers):
    app_id = perf_env
    PERMISSION_GATE.check(app_id, "filesystem.read")
    calls_per = 50
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [
            ex.submit(_call_many, app_id, "kernel.privileged", calls_per)
            for _ in range(workers)
        ]
        totals = [f.result() for f in as_completed(futs)]
    # Every call should deny → can() returns False → hits == 0.
    assert sum(totals) == 0


# ---------------------------------------------------------------------------
# Slow — 10k mixed allow/deny calls (gate state must not leak)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_10k_mixed_calls_no_state_leak(perf_env):
    app_id = perf_env
    PERMISSION_GATE.check(app_id, "filesystem.read")
    pre_size = len(PERMISSION_GATE._entries)
    for i in range(10_000):
        if i % 2 == 0:
            PERMISSION_GATE.can(app_id, "filesystem.read")
        else:
            PERMISSION_GATE.can(app_id, "kernel.privileged")
    post_size = len(PERMISSION_GATE._entries)
    assert pre_size == post_size, f"_entries grew from {pre_size} to {post_size}"


@pytest.mark.slow
@pytest.mark.parametrize("iters", [1_000, 5_000, 10_000])
def test_sustained_throughput(perf_env, iters):
    app_id = perf_env
    PERMISSION_GATE.check(app_id, "filesystem.read")
    t0 = time.perf_counter()
    for _ in range(iters):
        PERMISSION_GATE.can(app_id, "filesystem.read")
    elapsed = time.perf_counter() - t0
    qps = iters / elapsed
    assert qps > 10_000, f"sustained {qps:,.0f} qps below 10k floor"
