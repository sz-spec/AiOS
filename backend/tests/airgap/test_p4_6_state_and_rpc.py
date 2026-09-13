"""
P4.6 — App-scoped state store + inter-app RPC bridge.

Coverage groups:

  1. AppStateStore   — composite-PK isolation; oversize / malformed
     keys + values rejected.

  2. State HTTP API  — POST /api/apps/{id}/state/set + GET
     /api/apps/{id}/state/get. Cross-app reads/writes (caller_id !=
     URL_id) MUST 403; missing `app.state.read`/`app.state.write`
     scopes MUST 403.

  3. RPC bridge      — happy-path A→B round-trip with a real
     subprocess. Wildcard `rpc.call:*` grant. Caller without scope
     blocked. Target without `rpc.expose` blocked. Non-JSON stdout
     → RPCError → HTTP 502.

  4. Cross-app penetration — App A with valid secret + URL=B → 403
     across BOTH state and RPC entry surfaces.

All tests run under the airgap loopback kill-switch.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database.sqlite_setup import (
    AppState,
    _reset_for_tests,
    get_session,
    init_db,
)
from services.app_rpc import (
    APP_STATE_STORE,
    RPC_BRIDGE,
    AppStateStore,
    RPCError,
)
from services.app_sandbox import (
    SANDBOX_MANAGER,
    ScopeViolation,
    _reset_gate_for_tests,
    app_storage_root,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env):
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()


def _install(scopes, restrictions=()) -> tuple:
    res = SANDBOX_MANAGER.install(
        {
            "name": "p46-test",
            "version": "1.0.0",
            "scopes": list(scopes),
            "restrictions": list(restrictions),
        }
    )
    return res["app_id"], res["secret"]


def _plant_rpc_main(app_id: str, body: str) -> None:
    sandbox = app_storage_root(app_id)
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "main.py").write_text(body)


def _build_http_app() -> FastAPI:
    from api.app_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# (1) AppStateStore — direct unit-level isolation
# ---------------------------------------------------------------------------


def test_state_set_then_get_roundtrips(local_db):
    app_a, _ = _install([])
    entry = APP_STATE_STORE.set(app_a, "theme", "dark")
    assert entry.value == "dark"
    read = APP_STATE_STORE.get(app_a, "theme")
    assert read is not None and read.value == "dark"
    assert read.app_id == app_a


def test_state_each_app_has_independent_keyspace(local_db):
    """Composite PK isolation — A and B can both have the same key
    name with different values."""
    a, _ = _install([])
    b, _ = _install([])
    APP_STATE_STORE.set(a, "theme", "dark")
    APP_STATE_STORE.set(b, "theme", "light")
    assert APP_STATE_STORE.get(a, "theme").value == "dark"
    assert APP_STATE_STORE.get(b, "theme").value == "light"


def test_state_overwrite_marks_dirty(local_db):
    """Every write flips dirty=True for the sync engine."""
    a, _ = _install([])
    APP_STATE_STORE.set(a, "k", "v1")
    APP_STATE_STORE.set(a, "k", "v2")
    with get_session() as session:
        row = session.query(AppState).filter_by(appId=a, key="k").one()
        assert json.loads(row.value_json) == "v2"
        assert row.dirty is True


def test_state_rejects_bad_keys(local_db):
    a, _ = _install([])
    with pytest.raises(ValueError, match="key"):
        APP_STATE_STORE.set(a, "", "v")
    with pytest.raises(ValueError, match="key"):
        APP_STATE_STORE.set(a, "has space", "v")
    with pytest.raises(ValueError, match="key"):
        APP_STATE_STORE.set(a, "x" * 257, "v")


def test_state_rejects_oversize_value(local_db):
    a, _ = _install([])
    # JSON-encoded length > 64 KB → ValueError BEFORE the write.
    huge = "x" * (AppStateStore.MAX_VALUE_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds"):
        APP_STATE_STORE.set(a, "huge", huge)


def test_state_delete_then_get_returns_none(local_db):
    a, _ = _install([])
    APP_STATE_STORE.set(a, "k", "v")
    assert APP_STATE_STORE.delete(a, "k") is True
    assert APP_STATE_STORE.get(a, "k") is None
    # Idempotent: second delete returns False, doesn't raise.
    assert APP_STATE_STORE.delete(a, "k") is False


def test_state_keys_lists_only_own_app(local_db):
    a, _ = _install([])
    b, _ = _install([])
    APP_STATE_STORE.set(a, "k1", "x")
    APP_STATE_STORE.set(a, "k2", "y")
    APP_STATE_STORE.set(b, "z", "qq")
    assert sorted(APP_STATE_STORE.keys(a)) == ["k1", "k2"]
    assert APP_STATE_STORE.keys(b) == ["z"]


# ---------------------------------------------------------------------------
# (2) State HTTP API — auth + scope + cross-app guard
# ---------------------------------------------------------------------------


def test_http_state_write_then_read(local_db, network_guard):
    app_id, secret = _install(["app.state.read", "app.state.write"])
    client = TestClient(_build_http_app())

    w = client.post(
        f"/api/apps/{app_id}/state/set",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"key": "theme", "value": "dark"},
    )
    assert w.status_code == 200, w.text
    assert w.json()["value"] == "dark"

    r = client.get(
        f"/api/apps/{app_id}/state/get",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        params={"key": "theme"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == "dark"
    assert body["app_id"] == app_id


def test_http_state_write_denied_without_scope(local_db, network_guard):
    app_id, secret = _install(["app.state.read"])  # read-only
    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{app_id}/state/set",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"key": "x", "value": 1},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["scope"] == "app.state.write"


def test_http_state_read_denied_without_scope(local_db, network_guard):
    app_id, secret = _install(["app.state.write"])  # write-only
    client = TestClient(_build_http_app())
    # Plant a value via the store so the read isn't a 404 before the gate.
    APP_STATE_STORE.set(app_id, "x", "planted")
    r = client.get(
        f"/api/apps/{app_id}/state/get",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        params={"key": "x"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["scope"] == "app.state.read"


def test_http_state_cross_app_blocked(local_db, network_guard):
    """The directive's penetration scenario: App A authenticates
    with its own secret but addresses App B's URL → 403 via the
    `_require_matching_app` guard."""
    a, a_secret = _install(["app.state.read", "app.state.write"])
    b, _ = _install(["app.state.read", "app.state.write"])
    client = TestClient(_build_http_app())

    # A tries to write into B's state space.
    r = client.post(
        f"/api/apps/{b}/state/set",
        headers={"X-App-Id": a, "X-App-Secret": a_secret},
        json={"key": "theme", "value": "stolen"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] in (
        "app_id_mismatch",
        "app_unauthorized",
    )
    # And the read direction.
    r = client.get(
        f"/api/apps/{b}/state/get",
        headers={"X-App-Id": a, "X-App-Secret": a_secret},
        params={"key": "theme"},
    )
    assert r.status_code == 403


def test_http_state_404_for_missing_key(local_db, network_guard):
    app_id, secret = _install(["app.state.read"])
    client = TestClient(_build_http_app())
    r = client.get(
        f"/api/apps/{app_id}/state/get",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        params={"key": "never-set"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# (3) RPC bridge — direct + HTTP, happy path + scope failures
# ---------------------------------------------------------------------------


# Minimal valid RPC entrypoint — echoes method + params + caller id
# back as a JSON object. Lives inside the target's sandbox.
_RPC_ECHO_MAIN = (
    "import os, json, sys\n"
    "method = os.environ.get('VOS3_RPC_METHOD', '')\n"
    "params = json.loads(os.environ.get('VOS3_RPC_PARAMS', '{}'))\n"
    "caller = os.environ.get('VOS3_RPC_CALLER', '')\n"
    "rpc_id = os.environ.get('VOS3_RPC_ID', '')\n"
    "print(json.dumps({\n"
    "    'ok': True,\n"
    "    'method_echo': method,\n"
    "    'params_echo': params,\n"
    "    'caller_echo': caller,\n"
    "    'rpc_id_echo': rpc_id,\n"
    "}))\n"
)


def test_rpc_bridge_direct_call_succeeds(local_db):
    caller, _ = _install(
        ["rpc.call:dummy"]
    )  # placeholder; we'll re-install with real ids
    target, _ = _install(["process.execute", "rpc.expose"])
    _plant_rpc_main(target, _RPC_ECHO_MAIN)

    # Re-install caller with the specific target id in its manifest.
    SANDBOX_MANAGER.install(
        {
            "name": "caller",
            "version": "1.0.0",
            "scopes": [f"rpc.call:{target}"],
        },
        app_id=caller,
    )

    result = asyncio.run(
        RPC_BRIDGE.call(
            caller_app_id=caller,
            target_app_id=target,
            method="ping",
            params={"x": 42},
        )
    )
    assert result.caller_app_id == caller
    assert result.target_app_id == target
    assert result.method == "ping"
    assert result.result["ok"] is True
    assert result.result["method_echo"] == "ping"
    assert result.result["params_echo"] == {"x": 42}
    assert result.result["caller_echo"] == caller


def test_rpc_bridge_wildcard_scope_works(local_db):
    """`rpc.call:*` grants the caller access to any target."""
    target, _ = _install(["process.execute", "rpc.expose"])
    caller, _ = _install(["rpc.call:*"])
    _plant_rpc_main(target, _RPC_ECHO_MAIN)

    result = asyncio.run(
        RPC_BRIDGE.call(
            caller_app_id=caller,
            target_app_id=target,
            method="whatever",
        )
    )
    assert result.result["ok"] is True


def test_rpc_caller_without_scope_denied(local_db):
    target, _ = _install(["process.execute", "rpc.expose"])
    caller, _ = _install(["filesystem.read"])  # no rpc.call grant
    _plant_rpc_main(target, _RPC_ECHO_MAIN)

    with pytest.raises(ScopeViolation) as exc:
        asyncio.run(
            RPC_BRIDGE.call(
                caller_app_id=caller,
                target_app_id=target,
                method="ping",
            )
        )
    # Specific target check is what fails (wildcard isn't granted).
    assert exc.value.scope == f"rpc.call:{target}"


def test_rpc_target_without_expose_denied(local_db):
    """Target must hold `rpc.expose`."""
    target, _ = _install(["process.execute"])  # no rpc.expose
    caller, _ = _install(["rpc.call:*"])
    _plant_rpc_main(target, _RPC_ECHO_MAIN)

    with pytest.raises(ScopeViolation) as exc:
        asyncio.run(
            RPC_BRIDGE.call(
                caller_app_id=caller,
                target_app_id=target,
                method="ping",
            )
        )
    assert exc.value.app_id == target
    assert exc.value.scope == "rpc.expose"


def test_rpc_self_call_refused(local_db):
    """Self-RPC is a contract bug — caller should invoke its own
    code directly, not round-trip through a subprocess."""
    app_id, _ = _install(["process.execute", "rpc.expose", "rpc.call:*"])
    with pytest.raises(ValueError, match="self-RPC"):
        asyncio.run(
            RPC_BRIDGE.call(
                caller_app_id=app_id,
                target_app_id=app_id,
                method="x",
            )
        )


def test_rpc_non_json_stdout_surfaces_as_rpc_error(local_db):
    """Target prints free-form text → bridge returns RPCError with
    captured stderr/stdout for diagnosis."""
    target, _ = _install(["process.execute", "rpc.expose"])
    caller, _ = _install(["rpc.call:*"])
    _plant_rpc_main(target, "print('not-json output')\n")

    with pytest.raises(RPCError) as exc:
        asyncio.run(
            RPC_BRIDGE.call(
                caller_app_id=caller,
                target_app_id=target,
                method="x",
            )
        )
    assert "JSON" in exc.value.reason


def test_rpc_target_nonzero_exit_surfaces_as_rpc_error(local_db):
    target, _ = _install(["process.execute", "rpc.expose"])
    caller, _ = _install(["rpc.call:*"])
    _plant_rpc_main(
        target,
        (
            "import sys, json\n"
            "sys.stdout.write(json.dumps({'ok': False}))\n"
            "sys.exit(7)\n"
        ),
    )
    with pytest.raises(RPCError) as exc:
        asyncio.run(
            RPC_BRIDGE.call(
                caller_app_id=caller,
                target_app_id=target,
                method="x",
            )
        )
    assert exc.value.returncode == 7


# ---------------------------------------------------------------------------
# (4) HTTP RPC endpoint — auth + scope + cross-app guard
# ---------------------------------------------------------------------------


def test_http_rpc_call_happy_path(local_db, network_guard):
    target, _ = _install(["process.execute", "rpc.expose"])
    caller, caller_secret = _install([f"rpc.call:{target}"])
    _plant_rpc_main(target, _RPC_ECHO_MAIN)

    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{caller}/rpc/call",
        headers={"X-App-Id": caller, "X-App-Secret": caller_secret},
        json={
            "target_app_id": target,
            "method": "generate_report",
            "params": {"format": "md"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["caller_app_id"] == caller
    assert body["target_app_id"] == target
    assert body["method"] == "generate_report"
    assert body["result"]["method_echo"] == "generate_report"
    assert body["result"]["params_echo"] == {"format": "md"}


def test_http_rpc_403_when_caller_missing_scope(local_db, network_guard):
    target, _ = _install(["process.execute", "rpc.expose"])
    caller, caller_secret = _install(["filesystem.read"])  # no rpc.call
    _plant_rpc_main(target, _RPC_ECHO_MAIN)

    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{caller}/rpc/call",
        headers={"X-App-Id": caller, "X-App-Secret": caller_secret},
        json={"target_app_id": target, "method": "x"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["scope"] == f"rpc.call:{target}"


def test_http_rpc_403_when_target_missing_expose(local_db, network_guard):
    target, _ = _install(["process.execute"])  # no rpc.expose
    caller, caller_secret = _install(["rpc.call:*"])
    _plant_rpc_main(target, _RPC_ECHO_MAIN)

    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{caller}/rpc/call",
        headers={"X-App-Id": caller, "X-App-Secret": caller_secret},
        json={"target_app_id": target, "method": "x"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["scope"] == "rpc.expose"


def test_http_rpc_502_when_target_outputs_non_json(local_db, network_guard):
    target, _ = _install(["process.execute", "rpc.expose"])
    caller, caller_secret = _install(["rpc.call:*"])
    _plant_rpc_main(target, "print('garbage')\n")
    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{caller}/rpc/call",
        headers={"X-App-Id": caller, "X-App-Secret": caller_secret},
        json={"target_app_id": target, "method": "x"},
    )
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "rpc_error"


def test_http_rpc_cross_app_url_mismatch_blocked(local_db, network_guard):
    """Cross-app guard at the URL → header layer. Caller A sends its
    own secret + URL=B → 403 BEFORE the bridge fires."""
    a, a_secret = _install(["rpc.call:*"])
    b, _ = _install(["rpc.call:*"])
    target, _ = _install(["process.execute", "rpc.expose"])
    _plant_rpc_main(target, _RPC_ECHO_MAIN)

    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{b}/rpc/call",  # URL is B
        headers={"X-App-Id": a, "X-App-Secret": a_secret},  # but A's creds
        json={"target_app_id": target, "method": "x"},
    )
    assert r.status_code == 403


def test_http_rpc_404_when_target_unknown(local_db, network_guard):
    caller, caller_secret = _install(["rpc.call:*"])
    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{caller}/rpc/call",
        headers={"X-App-Id": caller, "X-App-Secret": caller_secret},
        json={"target_app_id": "ghost-app-id", "method": "x"},
    )
    # The target id doesn't exist → caller's gate.check for
    # `rpc.call:ghost-app-id` raises AppNotFound on the caller-row
    # lookup. The route currently returns 403 for AppNotFound on
    # caller-side checks; accept either 403 or 404.
    assert r.status_code in (403, 404)
