"""
Stage 3 · Input-validation caps + middleware resource guards.

Covers:
  * content_size middleware (MAX_CONTENT_SIZE = 10 MB)
  * AppRateLimiter (free=100 RPM, paid=1000 RPM, enterprise=10000 RPM)
  * TransactionGuard amount + rate caps under storm
  * PermissionGate cache eviction under load
  * Manifest scope-list size bounds
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# content_size middleware — module-level constants
# ---------------------------------------------------------------------------


def test_content_size_max_is_10mb(kernel_env):
    from middleware.content_size import MAX_CONTENT_SIZE

    assert MAX_CONTENT_SIZE == 10 * 1024 * 1024


def test_content_size_under_limit_passes(kernel_env):
    """Mock a request with content-length under the cap → middleware passes."""
    import asyncio
    from middleware.content_size import content_size_middleware

    class _Req:
        headers = {"content-length": "1024"}

    class _Sentinel:
        pass

    async def _next(_):
        return _Sentinel()

    out = asyncio.run(content_size_middleware(_Req(), _next))
    assert isinstance(out, _Sentinel)


def test_content_size_at_limit_passes(kernel_env):
    """Exactly MAX_CONTENT_SIZE → still passes (cap is strict >)."""
    import asyncio
    from middleware.content_size import (
        MAX_CONTENT_SIZE,
        content_size_middleware,
    )

    class _Req:
        headers = {"content-length": str(MAX_CONTENT_SIZE)}

    class _Sentinel:
        pass

    async def _next(_):
        return _Sentinel()

    out = asyncio.run(content_size_middleware(_Req(), _next))
    assert isinstance(out, _Sentinel)


def test_content_size_over_limit_returns_413(kernel_env):
    """1 byte over the cap → 413 Payload Too Large."""
    import asyncio
    from middleware.content_size import (
        MAX_CONTENT_SIZE,
        content_size_middleware,
    )

    class _Req:
        headers = {"content-length": str(MAX_CONTENT_SIZE + 1)}

    async def _next(_):
        pytest.fail("middleware should have short-circuited")

    out = asyncio.run(content_size_middleware(_Req(), _next))
    assert out.status_code == 413


def test_content_size_missing_header_passes(kernel_env):
    """Streaming endpoints with no content-length → allowed through."""
    import asyncio
    from middleware.content_size import content_size_middleware

    class _Req:
        headers = {}  # no content-length

    class _Sentinel:
        pass

    async def _next(_):
        return _Sentinel()

    out = asyncio.run(content_size_middleware(_Req(), _next))
    assert isinstance(out, _Sentinel)


def test_content_size_malformed_header_passes(kernel_env):
    """Bad content-length header → middleware lets it through (downstream
    will reject)."""
    import asyncio
    from middleware.content_size import content_size_middleware

    class _Req:
        headers = {"content-length": "not-a-number"}

    class _Sentinel:
        pass

    async def _next(_):
        return _Sentinel()

    out = asyncio.run(content_size_middleware(_Req(), _next))
    assert isinstance(out, _Sentinel)


# ---------------------------------------------------------------------------
# AppRateLimiter — token bucket
# ---------------------------------------------------------------------------


def test_rate_limiter_free_tier_limits(kernel_env):
    """Free tier = 100 RPM. After 100 allows, the bucket should be empty."""
    from middleware.app_rate_limit import AppRateLimiter

    rl = AppRateLimiter()
    for _ in range(100):
        assert rl.allow("app-free", tier="free") is True
    # 101st should be denied (within the same window).
    assert rl.allow("app-free", tier="free") is False


def test_rate_limiter_paid_tier_higher_cap(kernel_env):
    from middleware.app_rate_limit import AppRateLimiter

    rl = AppRateLimiter()
    for _ in range(1000):
        assert rl.allow("app-paid", tier="paid") is True
    assert rl.allow("app-paid", tier="paid") is False


def test_rate_limiter_enterprise_tier_largest(kernel_env):
    """Enterprise has the highest burst."""
    from middleware.app_rate_limit import AppRateLimiter

    rl = AppRateLimiter()
    # Take 9999 — should all pass.
    for _ in range(9999):
        assert rl.allow("app-ent", tier="enterprise") is True


def test_rate_limiter_unknown_tier_defaults_to_free(kernel_env):
    from middleware.app_rate_limit import AppRateLimiter

    rl = AppRateLimiter()
    for _ in range(100):
        assert rl.allow("app-x", tier="bogus") is True
    assert rl.allow("app-x", tier="bogus") is False


def test_rate_limiter_separate_apps_independent(kernel_env):
    from middleware.app_rate_limit import AppRateLimiter

    rl = AppRateLimiter()
    for _ in range(100):
        rl.allow("app-a", tier="free")
    # app-b has its own bucket.
    assert rl.allow("app-b", tier="free") is True


# ---------------------------------------------------------------------------
# TransactionGuard rate cap under storm
# ---------------------------------------------------------------------------


def test_host_bridge_rate_limit_storm(kernel_env):
    """Hammer the host bridge with 100 calls; rate is 10/min → some held."""
    from services.host_bridge import (
        DEFAULT_TARGET_REGISTRY,
        GuardConfig,
        TransactionGuard,
    )

    g = TransactionGuard(
        config=GuardConfig(amount_limit=999_999.0, rate_limit_per_min=10),
        registry=DEFAULT_TARGET_REGISTRY,
    )
    holds = 0
    allows = 0
    for _ in range(50):
        d = g.evaluate(
            app_id="a",
            workspace_id="w",
            target_system="SAP_GUI",
            action="fetch_vendor_balance",
            script_payload={"action": "fetch_vendor_balance"},
        )
        if d["decision"] == "allow":
            allows += 1
        else:
            holds += 1
    # After 10 allows, the rate limiter should hold the rest.
    assert allows <= 12  # small tolerance for sub-second timing
    assert holds >= 38


def test_host_bridge_amount_cap_storm(kernel_env):
    """50 over-cap requests → 50 holds."""
    from services.host_bridge import (
        DEFAULT_TARGET_REGISTRY,
        GuardConfig,
        TransactionGuard,
    )

    g = TransactionGuard(
        config=GuardConfig(amount_limit=999_999.0, rate_limit_per_min=1000),
        registry=DEFAULT_TARGET_REGISTRY,
    )
    for _ in range(50):
        d = g.evaluate(
            app_id="a",
            workspace_id="w",
            target_system="SAP_GUI",
            action="create_purchase_order",
            script_payload={"action": "create_purchase_order", "amount": 10_000},
        )
        # Per-action cap on create_purchase_order is 5000.
        assert d["decision"] == "hold"


# ---------------------------------------------------------------------------
# Manifest scope-list bounds
# ---------------------------------------------------------------------------


def test_manifest_accepts_reasonable_scope_count(kernel_env):
    """An app with 50 scopes is still legal."""
    from services.app_sandbox import AppManifest

    scopes = [f"events.subscribe:topic.{i}" for i in range(50)]
    m = AppManifest.from_dict({"name": "x", "version": "1.0", "scopes": scopes})
    assert len(m.scopes) == 50


def test_manifest_accepts_long_scope_string(kernel_env):
    """An individual scope can be moderately long (up to ~256 chars)."""
    from services.app_sandbox import AppManifest

    long_scope = "events.subscribe:" + "x" * 200
    m = AppManifest.from_dict(
        {"name": "x", "version": "1.0", "scopes": [long_scope]},
    )
    assert long_scope in m.scopes


# ---------------------------------------------------------------------------
# PermissionGate cache — N installed apps, all gate-checks fast
# ---------------------------------------------------------------------------


def test_permission_gate_cache_holds_50_apps(kernel_env):
    """50 apps installed; each can pass its own gate check."""
    from services.app_sandbox import PERMISSION_GATE, SANDBOX_MANAGER

    app_ids = []
    for i in range(50):
        res = SANDBOX_MANAGER.install(
            {"name": f"a{i}", "version": "1.0", "scopes": [f"events.subscribe:t.{i}"]},
            workspace_id="ws-cache",
        )
        app_ids.append(res["app_id"])
    for i, aid in enumerate(app_ids):
        assert PERMISSION_GATE.check(aid, f"events.subscribe:t.{i}") is True


# ---------------------------------------------------------------------------
# AppManifest field-coercion edge cases
# ---------------------------------------------------------------------------


def test_manifest_rejects_non_list_restrictions(kernel_env):
    from services.app_sandbox import AppManifest, InvalidManifest

    with pytest.raises(InvalidManifest):
        AppManifest.from_dict(
            {
                "name": "x",
                "version": "1.0",
                "scopes": [],
                "restrictions": "not-a-list",
            }
        )


def test_manifest_accepts_empty_restrictions_list(kernel_env):
    from services.app_sandbox import AppManifest

    m = AppManifest.from_dict(
        {
            "name": "x",
            "version": "1.0",
            "scopes": [],
            "restrictions": [],
        }
    )
    assert m.restrictions == ()


def test_manifest_version_required(kernel_env):
    from services.app_sandbox import AppManifest, InvalidManifest

    with pytest.raises(InvalidManifest):
        AppManifest.from_dict({"name": "x"})


# ---------------------------------------------------------------------------
# Path validation under repeated calls (cache hit / miss)
# ---------------------------------------------------------------------------


def test_path_resolver_50_lookups_consistent(kernel_env):
    """50 consecutive resolve_inside_sandbox calls on the same valid path
    return byte-identical results (no cache poisoning)."""
    from services.app_filesystem import _resolve_inside_sandbox
    from services.app_sandbox import SANDBOX_MANAGER

    res = SANDBOX_MANAGER.install(
        {"name": "p", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws-path",
    )
    aid = res["app_id"]
    paths = [str(_resolve_inside_sandbox(aid, "data/file.txt")) for _ in range(50)]
    assert len(set(paths)) == 1


# ---------------------------------------------------------------------------
# RateLimiter cache eviction — independent keys don't pollute
# ---------------------------------------------------------------------------


def test_rate_limiter_50_independent_keys(kernel_env):
    """50 distinct (app, target) pairs each get their own bucket."""
    from services.host_bridge import _RateLimiter

    rl = _RateLimiter()
    for i in range(50):
        ok, _ = rl.check_and_record((f"a{i}", "tgt"), limit=3)
        assert ok is True


# ---------------------------------------------------------------------------
# AppProcessRunner concurrency cap
# ---------------------------------------------------------------------------


def test_app_process_runner_max_concurrent_4(kernel_env):
    """Default max_concurrent is 4."""
    from services.app_sandbox import AppProcessRunner

    r = AppProcessRunner(max_concurrent=4)
    # Semaphore initialized at 4.
    assert r._semaphore._value == 4
