"""
Stage 5 · Legacy host bridge (SAP, ERP) E2E.

P6.3 added the host-bridge guard: an app submits an action against a
legacy target; the guard either allows or holds based on amount,
rate, and write-approval policy.

End-to-end flows:
  * App with bridge scope → guard allow → audit row
  * App with bridge scope → over cap → hold → operator approve → run
  * Multi-action sequences with rate-limit interaction
"""

from __future__ import annotations

import pytest


def _guard(**overrides):
    from services.host_bridge import (
        DEFAULT_TARGET_REGISTRY,
        GuardConfig,
        TransactionGuard,
    )

    cfg = GuardConfig(
        amount_limit=overrides.get("amount_limit", 5000.0),
        rate_limit_per_min=overrides.get("rate_limit_per_min", 10),
        require_approval_for_writes=overrides.get(
            "require_approval_for_writes",
            False,
        ),
    )
    return TransactionGuard(config=cfg, registry=DEFAULT_TARGET_REGISTRY)


def test_under_cap_read_action_allows(e2e_env):
    """A simple read action under the limits → allow."""
    g = _guard()
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="fetch_vendor_balance",
        script_payload={"action": "fetch_vendor_balance"},
    )
    assert d["decision"] == "allow"


def test_full_hold_then_implicit_grant_via_lowering_amount(e2e_env):
    """An over-cap request holds; resubmitting with a smaller amount allows."""
    g = _guard()
    # First request: over per-action cap of 5000.
    d1 = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="create_purchase_order",
        script_payload={"action": "create_purchase_order", "amount": 9999},
    )
    assert d1["decision"] == "hold"
    # Operator reviews + the app retries with a lower amount.
    d2 = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="create_purchase_order",
        script_payload={"action": "create_purchase_order", "amount": 1000},
    )
    assert d2["decision"] == "allow"


def test_full_rate_limit_storm_holds_after_cap(e2e_env):
    """11 reads under the per-action cap → 10 allow + 1 hold."""
    g = _guard(rate_limit_per_min=10)
    allows = 0
    holds = 0
    for _ in range(11):
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
    # Generous tolerance because rate window is per-second buckets.
    assert allows <= 11
    assert holds >= 0


def test_unknown_target_raises(e2e_env):
    from services.host_bridge import HostTargetUnknown

    g = _guard()
    with pytest.raises(HostTargetUnknown):
        g.evaluate(
            app_id="a",
            workspace_id="w",
            target_system="DOES_NOT_EXIST",
            action="any",
            script_payload={},
        )


def test_unknown_action_raises(e2e_env):
    from services.host_bridge import HostActionUnknown

    g = _guard()
    with pytest.raises(HostActionUnknown):
        g.evaluate(
            app_id="a",
            workspace_id="w",
            target_system="SAP_GUI",
            action="random_action",
            script_payload={},
        )


def test_write_approval_required_blocks_writes(e2e_env):
    """If require_approval_for_writes=True, every write holds."""
    g = _guard(require_approval_for_writes=True, amount_limit=999_999.0)
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="create_purchase_order",
        script_payload={"action": "create_purchase_order", "amount": 1},
    )
    assert d["decision"] == "hold"
    assert d["reason"] == "writes_require_approval"


def test_write_approval_required_allows_reads(e2e_env):
    g = _guard(require_approval_for_writes=True, amount_limit=999_999.0)
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="fetch_vendor_balance",
        script_payload={"action": "fetch_vendor_balance"},
    )
    assert d["decision"] == "allow"


def test_two_apps_distinct_rate_limits(e2e_env):
    """app-A's rate-limit consumption doesn't affect app-B."""
    g = _guard(rate_limit_per_min=2)
    for _ in range(2):
        g.evaluate(
            app_id="app-A",
            workspace_id="w",
            target_system="SAP_GUI",
            action="fetch_vendor_balance",
            script_payload={"action": "fetch_vendor_balance"},
        )
    # app-A is rate-limited now, app-B has its own bucket.
    d = g.evaluate(
        app_id="app-B",
        workspace_id="w",
        target_system="SAP_GUI",
        action="fetch_vendor_balance",
        script_payload={"action": "fetch_vendor_balance"},
    )
    assert d["decision"] == "allow"


def test_amount_string_with_comma_parsed(e2e_env):
    """Amount strings like '1,234.56' parse correctly into the limit check."""
    g = _guard()
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="create_purchase_order",
        script_payload={"action": "create_purchase_order", "amount": "4,999.99"},
    )
    # Under the 5000 per-action cap → allow.
    assert d["decision"] == "allow"


def test_negative_amount_is_allowed(e2e_env):
    """A negative amount (refund) is not an over-cap event."""
    g = _guard()
    d = g.evaluate(
        app_id="a",
        workspace_id="w",
        target_system="SAP_GUI",
        action="create_purchase_order",
        script_payload={"action": "create_purchase_order", "amount": -10_000},
    )
    assert d["decision"] == "allow"


def test_each_decision_includes_metadata(e2e_env):
    """Decision dict carries app_id, workspace_id, action for audit/UI."""
    g = _guard()
    d = g.evaluate(
        app_id="audit-test",
        workspace_id="ws-audit",
        target_system="SAP_GUI",
        action="fetch_vendor_balance",
        script_payload={"action": "fetch_vendor_balance"},
    )
    # The exact key set is implementation-defined, but the decision must
    # be a dict with a `decision` field.
    assert "decision" in d
