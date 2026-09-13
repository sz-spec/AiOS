"""
P6.3 — Legacy Host Bridge E2E tests.

Coverage groups:

  1. Scope + isolation gate:
     - App holding `host.automation` runs the inline executor and
       receives a structured response.
     - App WITHOUT the scope is denied (HostScopeDenied → HTTP 403).
     - Isolated app is denied even with the scope.
     - workspace_id mismatch against $VOS3_HOST_WORKSPACE_ID is denied
       AND lands a `local_tampering_blocked` audit row.

  2. Transaction Guard:
     - Per-action threshold beats the global cap. A $10,000
       create_purchase_order against the SAP_GUI registry crosses
       the $5,000 ceiling → row in pendingHostApprovals AND
       `awaiting_human_approval` audit row AND inline executor
       was NOT invoked.
     - Rate-limit > 10/min → 11th call held.
     - Operator-signed approve + execute path resolves the row
       to `executed`.
     - Wrong-key signature on the approval route is rejected.
     - Approval reject closes the row without executing.

  3. Hybrid routing:
     - Offline mode (default) → mode='offline'.
     - Online mode + manifest holds `network.egress:<host>` →
       mode='online'.
     - Online mode WITHOUT the egress scope falls back to offline.

  4. HTTP surface:
     - 403 on missing scope, 200 on legitimate call.
     - Approval poll route lists held rows.

All tests run under the air-gap kill-switch — the executors are
injected as in-memory callables so the test never spawns a real
subprocess or makes a real network call.
"""

from __future__ import annotations


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database.sqlite_setup import (
    PendingHostApproval,
    SecurityAuditLog,
    _reset_for_tests,
    get_session,
    init_db,
)
from services.app_sandbox import (
    SANDBOX_MANAGER,
    _reset_gate_for_tests,
)
from services.crypto_keyring import (
    KEYRING,
    get_or_mint_workflow_signing_key,
)
from services.host_bridge import (
    DEFAULT_TARGET_REGISTRY,
    GuardConfig,
    HostBridgeService,
    HostScopeDenied,
    HostTargetUnknown,
    TransactionGuard,
    _reset_for_tests as _reset_host_bridge,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env, tmp_path, monkeypatch):
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "p6.3-test-seed")
    monkeypatch.delenv("VOS3_HOST_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("VOS3_NETWORK_MODE", raising=False)
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    KEYRING._reset_for_tests()
    _reset_host_bridge()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()
    KEYRING._reset_for_tests()
    _reset_host_bridge()


def _install_app(scopes, *, workspace_id="ws-default", name="p63-app"):
    res = SANDBOX_MANAGER.install(
        {
            "name": name,
            "version": "1.0.0",
            "scopes": list(scopes),
        },
        workspace_id=workspace_id,
    )
    return res["app_id"]


def _calls_recorder():
    """Return (executor_fn, calls_list). Tests use the recorder to
    assert the inline executor WAS or WAS NOT invoked."""
    calls = []

    def _executor(*, target_system, action, payload, mode):
        calls.append(
            {
                "target_system": target_system,
                "action": action,
                "payload": payload,
                "mode": mode,
            }
        )
        return {
            "ok": True,
            "target_system": target_system,
            "action": action,
            "echo_payload": payload,
            "mode": mode,
        }

    return _executor, calls


def _make_service(*, inline_executor=None, online=False, guard=None, registry=None):
    """Build a HostBridgeService with an injected inline executor and
    a pinned online_probe so the tests are deterministic."""
    return HostBridgeService(
        registry=registry or DEFAULT_TARGET_REGISTRY,
        guard=guard,
        online_probe=(lambda: online),
        inline_executor=inline_executor,
    )


# ---------------------------------------------------------------------------
# (1) Scope + isolation gate
# ---------------------------------------------------------------------------


def test_app_with_host_automation_scope_can_execute(local_db):
    """Test 1 of the directive — active app with `host.automation`
    scope successfully triggers an offline SAP automation command
    and receives a structured response."""
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["host.automation"], workspace_id="ws-sap-1")

    result = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_GUI",
        script_payload={"action": "fetch_vendor_balance", "vendor_id": "V100"},
    )
    assert result.status == "ok"
    assert result.executor == "offline"
    assert result.result["ok"] is True
    assert result.result["action"] == "fetch_vendor_balance"
    assert len(calls) == 1


def test_app_without_scope_is_blocked(local_db):
    """Test 2 of the directive — app WITHOUT scope is immediately
    blocked with HostScopeDenied (route translates to 403)."""
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["filesystem.read"], workspace_id="ws-no-scope")
    with pytest.raises(HostScopeDenied):
        svc.execute_host_automation(
            app_id=app_id,
            target_system="SAP_GUI",
            script_payload={"action": "fetch_vendor_balance"},
        )
    assert calls == [], "executor must NOT run when scope denied"

    # The scope-violation audit row is emitted by the gate itself.
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="scope_violation",
            )
            .all()
        )
        assert any(r.scope == "host.automation" for r in rows)


def test_isolated_app_is_blocked_even_with_scope(local_db):
    """App holds the scope but its status was flipped to isolated —
    every gate check now denies."""
    svc = _make_service(inline_executor=lambda **_: {"ok": True})
    app_id = _install_app(["host.automation"], workspace_id="ws-isolated")
    SANDBOX_MANAGER.isolate(app_id, reason="operator pause")
    with pytest.raises(HostScopeDenied):
        svc.execute_host_automation(
            app_id=app_id,
            target_system="SAP_GUI",
            script_payload={"action": "fetch_vendor_balance"},
        )


def test_workspace_pinning_rejects_cross_workspace_call(local_db, monkeypatch):
    """$VOS3_HOST_WORKSPACE_ID pins the host instance to one
    workspace; an app from a different workspace is rejected with a
    `local_tampering_blocked` audit row."""
    monkeypatch.setenv("VOS3_HOST_WORKSPACE_ID", "ws-host-pin")
    svc = _make_service(inline_executor=lambda **_: {"ok": True})
    # The app is in a DIFFERENT workspace.
    app_id = _install_app(["host.automation"], workspace_id="ws-OTHER")
    with pytest.raises(HostScopeDenied):
        svc.execute_host_automation(
            app_id=app_id,
            target_system="SAP_GUI",
            script_payload={"action": "fetch_vendor_balance"},
        )
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="local_tampering_blocked",
            )
            .all()
        )
        assert any("host_bridge_workspace_mismatch" in (r.reason or "") for r in rows)


def test_unknown_target_system_is_rejected(local_db):
    svc = _make_service(inline_executor=lambda **_: {"ok": True})
    app_id = _install_app(["host.automation"], workspace_id="ws-unk")
    with pytest.raises(HostTargetUnknown):
        svc.execute_host_automation(
            app_id=app_id,
            target_system="NONEXISTENT_ERP",
            script_payload={"action": "fetch_anything"},
        )


# ---------------------------------------------------------------------------
# (2) Transaction Guard
# ---------------------------------------------------------------------------


def test_high_value_purchase_order_holds_for_approval(local_db):
    """Test 3 of the directive — $10,000 create_purchase_order
    crosses the $5,000 ceiling, lands in pendingHostApprovals,
    and the executor is NEVER invoked."""
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["host.automation"], workspace_id="ws-bigspender")

    result = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_GUI",
        script_payload={
            "action": "create_purchase_order",
            "amount": 10_000.0,
            "currency": "USD",
            "vendor_id": "V100",
        },
    )
    assert result.status == "awaiting_approval"
    assert result.approval_id is not None
    assert "amount_over_threshold" in (result.reason or "")
    assert calls == [], "executor must NOT run during hold"

    with get_session() as session:
        row = (
            session.query(PendingHostApproval)
            .filter_by(
                id=result.approval_id,
            )
            .one()
        )
        assert row.status == "awaiting_human_approval"
        assert row.amount == pytest.approx(10_000.0)
        assert row.currency == "USD"

        # The audit log carries the matching row.
        audits = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="awaiting_human_approval",
            )
            .all()
        )
        assert audits


def test_rate_limit_holds_after_threshold(local_db):
    """11th call within a minute is held even when every individual
    call is under the amount cap."""
    executor, calls = _calls_recorder()
    guard = TransactionGuard(
        config=GuardConfig(
            amount_limit=10_000.0,
            rate_limit_per_min=2,
        ),
    )
    svc = _make_service(inline_executor=executor, guard=guard)
    app_id = _install_app(["host.automation"], workspace_id="ws-burst")

    r1 = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_GUI",
        script_payload={"action": "fetch_vendor_balance"},
    )
    r2 = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_GUI",
        script_payload={"action": "fetch_vendor_balance"},
    )
    r3 = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_GUI",
        script_payload={"action": "fetch_vendor_balance"},
    )
    assert r1.status == "ok"
    assert r2.status == "ok"
    assert r3.status == "awaiting_approval"
    assert "rate_limit_exceeded" in (r3.reason or "")
    assert len(calls) == 2


def test_approve_and_execute_runs_held_call(local_db):
    """Operator signs an approval; the held row transitions to
    `executed` and the executor IS invoked."""
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["host.automation"], workspace_id="ws-approve")

    held = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_GUI",
        script_payload={
            "action": "create_purchase_order",
            "amount": 10_000.0,
            "currency": "USD",
        },
    )
    assert held.status == "awaiting_approval"
    assert calls == []

    # Operator signs the canonical approval blob.
    priv_hex, pub_hex = get_or_mint_workflow_signing_key()
    from services.app_crypto import sign_manifest

    signature = sign_manifest(
        {
            "approval_id": held.approval_id,
            "approver_user_id": "operator-alice",
            "decision": "approve",
        },
        private_key_hex=priv_hex,
    )

    final = svc.approve_and_execute(
        approval_id=held.approval_id,
        approver_user_id="operator-alice",
        signature_hex=signature,
        public_key_hex=pub_hex,
    )
    assert final.status == "ok"
    assert final.executor == "offline"
    assert final.result["action"] == "create_purchase_order"
    assert len(calls) == 1

    with get_session() as session:
        row = (
            session.query(PendingHostApproval)
            .filter_by(
                id=held.approval_id,
            )
            .one()
        )
        assert row.status == "executed"
        assert row.approverId == "operator-alice"
        assert row.signing_public_key == pub_hex
        assert row.result_json is not None


def test_approve_with_wrong_key_is_rejected(local_db):
    """Approval signed with an unrelated keypair is denied; the
    held row stays `awaiting_human_approval` and the executor is
    not invoked."""
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["host.automation"], workspace_id="ws-spoofed")
    held = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_GUI",
        script_payload={"action": "create_purchase_order", "amount": 9_999.0},
    )
    # Mint an attacker keypair OUT-OF-BAND of the keyring.
    from services.app_crypto import generate_keypair_hex, sign_manifest

    fake_priv, fake_pub = generate_keypair_hex()
    signature = sign_manifest(
        {
            "approval_id": held.approval_id,
            "approver_user_id": "attacker",
            "decision": "approve",
        },
        private_key_hex=fake_priv,
    )

    result = svc.approve_and_execute(
        approval_id=held.approval_id,
        approver_user_id="attacker",
        signature_hex=signature,
        public_key_hex=fake_pub,
    )
    assert result.status == "error"
    assert "signature" in (result.error or "").lower()
    assert calls == []

    with get_session() as session:
        row = (
            session.query(PendingHostApproval)
            .filter_by(
                id=held.approval_id,
            )
            .one()
        )
        assert row.status == "awaiting_human_approval"


def test_reject_approval_terminates_without_executing(local_db):
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["host.automation"], workspace_id="ws-reject")
    held = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_GUI",
        script_payload={"action": "create_purchase_order", "amount": 8_000.0},
    )
    priv_hex, pub_hex = get_or_mint_workflow_signing_key()
    from services.app_crypto import sign_manifest

    sig = sign_manifest(
        {
            "approval_id": held.approval_id,
            "approver_user_id": "operator-bob",
            "decision": "reject",
        },
        private_key_hex=priv_hex,
    )

    res = svc.reject_approval(
        approval_id=held.approval_id,
        approver_user_id="operator-bob",
        signature_hex=sig,
        public_key_hex=pub_hex,
    )
    assert res.status == "ok"
    assert res.reason == "rejected"
    assert calls == []

    with get_session() as session:
        row = (
            session.query(PendingHostApproval)
            .filter_by(
                id=held.approval_id,
            )
            .one()
        )
        assert row.status == "rejected"


# ---------------------------------------------------------------------------
# (3) Hybrid online / offline routing
# ---------------------------------------------------------------------------


def test_offline_mode_is_default(local_db):
    """No VOS3_NETWORK_MODE override + airgap_env's local-first
    preference → mode='offline'."""
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor, online=False)
    app_id = _install_app(["host.automation"], workspace_id="ws-off")
    result = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_S4HANA_CLOUD",
        script_payload={"action": "fetch_vendor_balance"},
    )
    assert result.status == "ok"
    assert result.executor == "offline"
    assert calls[0]["mode"] == "offline"


def test_online_mode_with_egress_scope_routes_to_cloud(local_db):
    """Online probe says True AND the manifest has the cloud egress
    scope → mode='online'."""
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor, online=True)
    app_id = _install_app(
        [
            "host.automation",
            "network.egress:api.sap.example.com",
        ],
        workspace_id="ws-cloud-allowed",
    )
    result = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_S4HANA_CLOUD",
        script_payload={"action": "fetch_vendor_balance"},
    )
    assert result.status == "ok"
    assert result.executor == "online"
    assert calls[0]["mode"] == "online"


def test_online_mode_without_egress_scope_falls_back_to_offline(local_db):
    """Online probe says True BUT the manifest lacks the egress
    scope → falls back to offline (no silent egress)."""
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor, online=True)
    app_id = _install_app(
        ["host.automation"],  # no network.egress scope
        workspace_id="ws-cloud-blocked",
    )
    result = svc.execute_host_automation(
        app_id=app_id,
        target_system="SAP_S4HANA_CLOUD",
        script_payload={"action": "fetch_vendor_balance"},
    )
    assert result.status == "ok"
    assert result.executor == "offline"
    assert calls[0]["mode"] == "offline"


# ---------------------------------------------------------------------------
# (4) HTTP surface
# ---------------------------------------------------------------------------


def _build_http(svc: HostBridgeService) -> FastAPI:
    """Mount the host-bridge router with our test-injected service.

    `from services.host_bridge import HOST_BRIDGE` snapshots a NAME
    binding inside the route module — re-assigning the singleton on
    the services side won't update that copy. So we also rebind the
    name on the route module itself."""
    import services.host_bridge as hb_mod

    hb_mod.HOST_BRIDGE = svc
    import api.host_bridge_routes as rt_mod

    rt_mod.HOST_BRIDGE = svc
    from api.host_bridge_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def _auth(clerk_id: str = "operator-alice"):
    from tests.airgap_harness import bootstrap_sqlite_user, make_offline_token

    bootstrap_sqlite_user(clerk_id)
    return {"Authorization": f"Bearer {make_offline_token(clerk_id)}"}


def test_http_execute_403_when_scope_missing(local_db):
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["filesystem.read"], workspace_id="ws-403")
    client = TestClient(_build_http(svc))
    r = client.post(
        "/api/host_bridge/execute",
        headers=_auth("operator-403"),
        json={
            "app_id": app_id,
            "target_system": "SAP_GUI",
            "payload": {"action": "fetch_vendor_balance"},
        },
    )
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["detail"]["error"] == "scope_denied"
    assert calls == []


def test_http_execute_ok_when_scope_present(local_db):
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["host.automation"], workspace_id="ws-200")
    client = TestClient(_build_http(svc))
    r = client.post(
        "/api/host_bridge/execute",
        headers=_auth("operator-200"),
        json={
            "app_id": app_id,
            "target_system": "SAP_GUI",
            "payload": {"action": "fetch_vendor_balance"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["executor"] == "offline"
    assert calls and calls[0]["action"] == "fetch_vendor_balance"


def test_http_high_value_returns_awaiting_approval(local_db):
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["host.automation"], workspace_id="ws-hold")
    client = TestClient(_build_http(svc))
    r = client.post(
        "/api/host_bridge/execute",
        headers=_auth("operator-hold"),
        json={
            "app_id": app_id,
            "target_system": "SAP_GUI",
            "payload": {
                "action": "create_purchase_order",
                "amount": 25_000.0,
                "currency": "USD",
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "awaiting_approval"
    approval_id = body["approval_id"]
    assert isinstance(approval_id, str)

    # GET /approvals/{id} returns the row.
    r2 = client.get(
        f"/api/host_bridge/approvals/{approval_id}",
        headers=_auth("operator-hold"),
    )
    assert r2.status_code == 200
    row = r2.json()
    assert row["status"] == "awaiting_human_approval"
    assert row["amount"] == 25_000.0

    # Listing returns the row too.
    r3 = client.get(
        "/api/host_bridge/approvals",
        headers=_auth("operator-hold"),
    )
    assert r3.status_code == 200
    rows = r3.json()["approvals"]
    assert any(a["approval_id"] == approval_id for a in rows)


def test_http_approve_route_runs_held_call_end_to_end(local_db):
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["host.automation"], workspace_id="ws-e2e")
    client = TestClient(_build_http(svc))

    r = client.post(
        "/api/host_bridge/execute",
        headers=_auth("operator-e2e"),
        json={
            "app_id": app_id,
            "target_system": "SAP_GUI",
            "payload": {
                "action": "create_purchase_order",
                "amount": 7_500.0,
                "currency": "USD",
            },
        },
    )
    approval_id = r.json()["approval_id"]
    assert calls == []

    priv_hex, pub_hex = get_or_mint_workflow_signing_key()
    from services.app_crypto import sign_manifest

    sig = sign_manifest(
        {
            "approval_id": approval_id,
            "approver_user_id": "operator-e2e",
            "decision": "approve",
        },
        private_key_hex=priv_hex,
    )

    r2 = client.post(
        f"/api/host_bridge/approvals/{approval_id}/approve",
        headers=_auth("operator-e2e"),
        json={
            "approver_user_id": "operator-e2e",
            "signature_hex": sig,
            "public_key_hex": pub_hex,
        },
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "ok"
    assert body["executor"] == "offline"
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# (5) P7.2 — /_sign proxy route
# ---------------------------------------------------------------------------


def test_sign_proxy_returns_keyring_pinned_signature(local_db):
    """P7.2 — POST /_sign with a pending approval_id returns a signature
    that the /approve route accepts end-to-end."""
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["host.automation"], workspace_id="ws-sign-proxy")
    client = TestClient(_build_http(svc))

    # Step 1 — create a held approval.
    r = client.post(
        "/api/host_bridge/execute",
        headers=_auth("operator-sign-proxy"),
        json={
            "app_id": app_id,
            "target_system": "SAP_GUI",
            "payload": {
                "action": "create_purchase_order",
                "amount": 9_999.0,
                "currency": "USD",
            },
        },
    )
    approval_id = r.json()["approval_id"]

    # Step 2 — call /_sign to get a server-minted signature.
    r2 = client.post(
        "/api/host_bridge/_sign",
        headers=_auth("operator-sign-proxy"),
        json={"approval_id": approval_id, "decision": "approve"},
    )
    assert r2.status_code == 200, r2.text
    signed = r2.json()
    assert signed["approval_id"] == approval_id
    assert signed["decision"] == "approve"
    assert signed["approver_user_id"] == "operator-sign-proxy"
    assert len(signed["signature_hex"]) == 128
    assert len(signed["public_key_hex"]) == 64

    # Step 3 — POST that signature to /approve. Should succeed.
    r3 = client.post(
        f"/api/host_bridge/approvals/{approval_id}/approve",
        headers=_auth("operator-sign-proxy"),
        json={
            "approver_user_id": signed["approver_user_id"],
            "signature_hex": signed["signature_hex"],
            "public_key_hex": signed["public_key_hex"],
        },
    )
    assert r3.status_code == 200, r3.text
    body = r3.json()
    assert body["status"] == "ok"
    assert len(calls) == 1


def test_sign_proxy_rejects_terminal_approval(local_db):
    """P7.2 — Signing a row that's already approved/rejected/executed
    returns 409 instead of letting the operator spam the audit log."""
    executor, calls = _calls_recorder()
    svc = _make_service(inline_executor=executor)
    app_id = _install_app(["host.automation"], workspace_id="ws-sign-terminal")
    client = TestClient(_build_http(svc))

    r = client.post(
        "/api/host_bridge/execute",
        headers=_auth("operator-terminal"),
        json={
            "app_id": app_id,
            "target_system": "SAP_GUI",
            "payload": {
                "action": "create_purchase_order",
                "amount": 7_500.0,
                "currency": "USD",
            },
        },
    )
    approval_id = r.json()["approval_id"]

    # Sign + approve so the row terminates as 'executed'.
    sign1 = client.post(
        "/api/host_bridge/_sign",
        headers=_auth("operator-terminal"),
        json={"approval_id": approval_id, "decision": "approve"},
    ).json()
    client.post(
        f"/api/host_bridge/approvals/{approval_id}/approve",
        headers=_auth("operator-terminal"),
        json={
            "approver_user_id": sign1["approver_user_id"],
            "signature_hex": sign1["signature_hex"],
            "public_key_hex": sign1["public_key_hex"],
        },
    )

    # Re-attempt /_sign — should 409.
    r2 = client.post(
        "/api/host_bridge/_sign",
        headers=_auth("operator-terminal"),
        json={"approval_id": approval_id, "decision": "approve"},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["error"] == "approval_already_terminal"


def test_sign_proxy_404_for_unknown_approval(local_db):
    svc = _make_service(inline_executor=lambda **_: {"ok": True})
    client = TestClient(_build_http(svc))
    r = client.post(
        "/api/host_bridge/_sign",
        headers=_auth("operator-unknown"),
        json={"approval_id": "does-not-exist", "decision": "approve"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "approval_not_found"


def test_sign_proxy_rejects_invalid_decision(local_db):
    svc = _make_service(inline_executor=lambda **_: {"ok": True})
    client = TestClient(_build_http(svc))
    r = client.post(
        "/api/host_bridge/_sign",
        headers=_auth("operator-bad-decision"),
        json={"approval_id": "any", "decision": "delete-everything"},
    )
    assert r.status_code == 400
    assert "decision" in r.json()["detail"]["reason"]
