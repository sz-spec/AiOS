"""
Stage 1 · Atomic unit isolation for services/host_bridge.py.

Covers — purpose | guards file:line:
  TransactionGuard.evaluate decision matrix      | host_bridge.py:170-300
  _RateLimiter sliding-window edge cases         | host_bridge.py:131-160
  HostTargetUnknown / HostActionUnknown          | host_bridge.py:213-220
  GuardConfig.from_env                           | host_bridge.py:113-122
  TransactionGuard._extract_amount               | host_bridge.py:283-300
"""

from __future__ import annotations

import time

import pytest


def _guard(amount_limit=5000.0, rate_limit=10, registry=None):
    from services.host_bridge import (
        DEFAULT_TARGET_REGISTRY,
        GuardConfig,
        TransactionGuard,
    )

    return TransactionGuard(
        config=GuardConfig(amount_limit=amount_limit, rate_limit_per_min=rate_limit),
        registry=registry or DEFAULT_TARGET_REGISTRY,
    )


# ---------------------------------------------------------------------------
# Registry lookup
# ---------------------------------------------------------------------------


def test_unknown_target_raises(unit_env):
    from services.host_bridge import HostTargetUnknown

    with pytest.raises(HostTargetUnknown):
        _guard().evaluate(
            app_id="a",
            workspace_id="w",
            target_system="NOPE_ERP",
            action="x",
            script_payload={},
        )


def test_unknown_action_raises(unit_env):
    from services.host_bridge import HostActionUnknown

    with pytest.raises(HostActionUnknown):
        _guard().evaluate(
            app_id="a",
            workspace_id="w",
            target_system="SAP_GUI",
            action="delete_everything",
            script_payload={},
        )


# ---------------------------------------------------------------------------
# Amount cap — per-action ceiling beats global
# ---------------------------------------------------------------------------


def test_under_cap_allows(unit_env):
    g = _guard()
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="create_purchase_order",
        script_payload={"action": "create_purchase_order", "amount": 1000},
    )
    assert d["decision"] == "allow"


def test_over_per_action_cap_holds(unit_env):
    g = _guard(amount_limit=999_999.0)  # global is huge
    # but per-action cap on create_purchase_order is 5000 in the registry
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="create_purchase_order",
        script_payload={"action": "create_purchase_order", "amount": 8000},
    )
    assert d["decision"] == "hold"
    assert "amount_over_threshold" in d["reason"]


def test_no_amount_no_hold(unit_env):
    g = _guard()
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="fetch_vendor_balance",
        script_payload={"action": "fetch_vendor_balance", "vendor": "V1"},
    )
    assert d["decision"] == "allow"


def test_amount_zero_does_not_hold(unit_env):
    g = _guard()
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="create_purchase_order",
        script_payload={"action": "create_purchase_order", "amount": 0},
    )
    assert d["decision"] == "allow"


def test_amount_negative_does_not_hold(unit_env):
    """Negative amount → no hold (probably a refund). The Guard
    triggers on amount > cap, not |amount| > cap."""
    g = _guard()
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="create_purchase_order",
        script_payload={"action": "create_purchase_order", "amount": -10000},
    )
    assert d["decision"] == "allow"


# ---------------------------------------------------------------------------
# _RateLimiter sliding window
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_under_threshold(unit_env):
    from services.host_bridge import _RateLimiter

    rl = _RateLimiter()
    for _ in range(5):
        ok, _ = rl.check_and_record(("a", "tgt"), limit=10)
        assert ok is True


def test_rate_limiter_denies_at_threshold(unit_env):
    from services.host_bridge import _RateLimiter

    rl = _RateLimiter()
    for _ in range(3):
        ok, _ = rl.check_and_record(("a", "tgt"), limit=3)
        assert ok is True
    # 4th call hits the limit.
    ok, count = rl.check_and_record(("a", "tgt"), limit=3)
    assert ok is False
    assert count == 3


def test_rate_limiter_separate_keys_are_independent(unit_env):
    from services.host_bridge import _RateLimiter

    rl = _RateLimiter()
    for _ in range(3):
        rl.check_and_record(("a", "x"), limit=3)
    # Different key has its own counter.
    ok, _ = rl.check_and_record(("a", "y"), limit=3)
    assert ok is True


def test_rate_limiter_window_eviction(unit_env):
    """Stamps older than window_s are evicted; new call succeeds."""
    from services.host_bridge import _RateLimiter

    rl = _RateLimiter()
    for _ in range(3):
        rl.check_and_record(("a", "x"), limit=3, window_s=0.05)
    time.sleep(0.06)
    ok, _ = rl.check_and_record(("a", "x"), limit=3, window_s=0.05)
    assert ok is True


def test_rate_limiter_reset(unit_env):
    from services.host_bridge import _RateLimiter

    rl = _RateLimiter()
    for _ in range(3):
        rl.check_and_record(("a", "x"), limit=3)
    rl.reset()
    ok, _ = rl.check_and_record(("a", "x"), limit=3)
    assert ok is True


# ---------------------------------------------------------------------------
# _extract_amount — robust parsing
# ---------------------------------------------------------------------------


def test_extract_amount_int(unit_env):
    from services.host_bridge import TransactionGuard

    amt, curr = TransactionGuard._extract_amount({"amount": 100})
    assert amt == 100.0


def test_extract_amount_float(unit_env):
    from services.host_bridge import TransactionGuard

    amt, _ = TransactionGuard._extract_amount({"amount": 99.99})
    assert amt == 99.99


def test_extract_amount_str_with_comma(unit_env):
    from services.host_bridge import TransactionGuard

    amt, _ = TransactionGuard._extract_amount({"amount": "1,234.56"})
    assert amt == 1234.56


def test_extract_amount_garbage_string_returns_none(unit_env):
    from services.host_bridge import TransactionGuard

    amt, _ = TransactionGuard._extract_amount({"amount": "not-a-number"})
    assert amt is None


def test_extract_amount_currency_passthrough(unit_env):
    from services.host_bridge import TransactionGuard

    _, curr = TransactionGuard._extract_amount({"amount": 1, "currency": "EUR"})
    assert curr == "EUR"


def test_extract_amount_non_dict_returns_none_pair(unit_env):
    from services.host_bridge import TransactionGuard

    amt, curr = TransactionGuard._extract_amount("not-a-dict")  # type: ignore[arg-type]
    assert amt is None and curr is None


# ---------------------------------------------------------------------------
# GuardConfig.from_env
# ---------------------------------------------------------------------------


def test_config_from_env_defaults(unit_env, monkeypatch):
    monkeypatch.delenv("VOS3_HOST_AMOUNT_LIMIT", raising=False)
    monkeypatch.delenv("VOS3_HOST_RATE_LIMIT_PER_MIN", raising=False)
    monkeypatch.delenv("VOS3_HOST_APPROVE_ALL_WRITES", raising=False)
    from services.host_bridge import GuardConfig

    c = GuardConfig.from_env()
    assert c.amount_limit == 5_000.0
    assert c.rate_limit_per_min == 10
    assert c.require_approval_for_writes is False


def test_config_from_env_overrides(unit_env, monkeypatch):
    monkeypatch.setenv("VOS3_HOST_AMOUNT_LIMIT", "100.0")
    monkeypatch.setenv("VOS3_HOST_RATE_LIMIT_PER_MIN", "3")
    monkeypatch.setenv("VOS3_HOST_APPROVE_ALL_WRITES", "1")
    from services.host_bridge import GuardConfig

    c = GuardConfig.from_env()
    assert c.amount_limit == 100.0
    assert c.rate_limit_per_min == 3
    assert c.require_approval_for_writes is True


# ---------------------------------------------------------------------------
# require_approval_for_writes — auto-hold on write actions
# ---------------------------------------------------------------------------


def test_require_approval_for_writes_holds_writes(unit_env):
    """With require_approval_for_writes=True, any write-action holds
    regardless of amount."""
    from services.host_bridge import (
        DEFAULT_TARGET_REGISTRY,
        GuardConfig,
        TransactionGuard,
    )

    g = TransactionGuard(
        config=GuardConfig(
            amount_limit=999_999.0,
            rate_limit_per_min=1000,
            require_approval_for_writes=True,
        ),
        registry=DEFAULT_TARGET_REGISTRY,
    )
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="create_purchase_order",
        # Amount is fine, BUT writes require approval.
        script_payload={"action": "create_purchase_order", "amount": 1},
    )
    assert d["decision"] == "hold"
    assert d["reason"] == "writes_require_approval"


def test_require_approval_for_writes_allows_reads(unit_env):
    """Read actions (writes=False) bypass the require_approval gate."""
    from services.host_bridge import (
        DEFAULT_TARGET_REGISTRY,
        GuardConfig,
        TransactionGuard,
    )

    g = TransactionGuard(
        config=GuardConfig(
            amount_limit=999_999.0,
            rate_limit_per_min=1000,
            require_approval_for_writes=True,
        ),
        registry=DEFAULT_TARGET_REGISTRY,
    )
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="fetch_vendor_balance",
        script_payload={"action": "fetch_vendor_balance"},
    )
    assert d["decision"] == "allow"
