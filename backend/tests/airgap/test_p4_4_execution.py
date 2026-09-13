"""
P4.4 — App execution subprocess runner tests.

Coverage groups:

  1. Happy-path execution — install an app with a real `main.py`
     entrypoint, spawn via the runner, confirm stdout + exit code +
     env-var injection (VOS3_APP_ID).

  2. Capability + path gates — missing `process.execute`, isolated
     app, missing entrypoint, and traversal entrypoints all
     refuse to spawn.

  3. Subprocess hardening — wall-clock timeout kills runaway children
     (returns `timed_out=True`); large stdout doesn't deadlock the
     parent (asyncio.communicate covers both pipes).

  4. Penetration test — a malicious child that tries to reach a
     restricted scope (cloud LLM under `network.blocked`) via the
     in-process dispatcher must be 403'd at the gate. Demonstrates
     that the ephemeral X-App-Secret minted by the runner is the
     same scope the manifest restricts — the gate isn't somehow
     "softer" for subprocess-originated callbacks.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from core.database.sqlite_setup import _reset_for_tests, init_db
from services.app_sandbox import (
    APP_PROCESS_RUNNER,
    SANDBOX_MANAGER,
    _EPHEMERAL_SECRETS,
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
    _EPHEMERAL_SECRETS.clear()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()
    _EPHEMERAL_SECRETS.clear()


def _install(scopes, restrictions=()) -> tuple:
    res = SANDBOX_MANAGER.install(
        {
            "name": "exec-test",
            "version": "1.0.0",
            "scopes": list(scopes),
            "restrictions": list(restrictions),
        }
    )
    return res["app_id"], res["secret"]


def _plant_main(app_id: str, body: str, *, name: str = "main.py") -> Path:
    """Write `body` as <sandbox>/<name> and return the path."""
    sandbox = app_storage_root(app_id)
    sandbox.mkdir(parents=True, exist_ok=True)
    target = sandbox / name
    target.write_text(body)
    return target


# ---------------------------------------------------------------------------
# (1) Happy-path execution
# ---------------------------------------------------------------------------


def test_runner_executes_simple_entrypoint(local_db):
    """An installed app with a basic main.py spawns, exits 0, and
    its stdout reaches the caller."""
    app_id, _ = _install(["process.execute", "filesystem.write"])
    _plant_main(app_id, "print('hello from app')\n")

    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    assert result.returncode == 0, result.stderr
    assert "hello from app" in result.stdout
    assert result.timed_out is False
    assert result.killed_by_limit is False


def test_runner_injects_vos3_app_id_env(local_db):
    """The child must see VOS3_APP_ID set to its own id and
    VOS3_APP_SECRET set to a fresh ephemeral token."""
    app_id, _ = _install(["process.execute"])
    _plant_main(
        app_id,
        (
            "import os, json\n"
            "print(json.dumps({\n"
            "    'app_id': os.environ.get('VOS3_APP_ID'),\n"
            "    'secret_present': bool(os.environ.get('VOS3_APP_SECRET')),\n"
            "    'sandbox_flag': os.environ.get('VOS3_SANDBOX'),\n"
            "}))\n"
        ),
    )

    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout.strip())
    assert payload["app_id"] == app_id
    assert payload["secret_present"] is True
    assert payload["sandbox_flag"] == "1"


def test_runner_protected_env_keys_not_overridable(local_db):
    """`env_override` MUST NOT be able to spoof the identity keys
    (VOS3_APP_ID / VOS3_APP_SECRET / VOS3_TAURI_IPC_SECRET).
    Otherwise an operator could trick a child into impersonating
    another app via a single API call."""
    app_id, _ = _install(["process.execute"])
    _plant_main(
        app_id,
        (
            "import os\n"
            "print(os.environ.get('VOS3_APP_ID'))\n"
            "print(os.environ.get('VOS3_TAURI_IPC_SECRET'))\n"
        ),
    )

    fake_id = "attacker-app-id"
    result = asyncio.run(
        APP_PROCESS_RUNNER.run_entrypoint(
            app_id,
            env_override={
                "VOS3_APP_ID": fake_id,
                "VOS3_TAURI_IPC_SECRET": "stolen-secret",
                "MY_OWN_VAR": "ok-to-set",
            },
        )
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == app_id  # runner-injected wins
    # VOS3_TAURI_IPC_SECRET is also protected — operator can't shadow it.
    assert lines[1] != "stolen-secret"


def test_runner_propagates_args(local_db):
    app_id, _ = _install(["process.execute"])
    _plant_main(app_id, "import sys; print(' | '.join(sys.argv[1:]))\n")
    result = asyncio.run(
        APP_PROCESS_RUNNER.run_entrypoint(
            app_id,
            args=["alpha", "beta", "gamma"],
        )
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "alpha | beta | gamma"


def test_runner_cwd_is_app_sandbox(local_db):
    """Relative paths inside the child resolve under the sandbox."""
    app_id, _ = _install(["process.execute"])
    _plant_main(
        app_id,
        (
            "import os\n"
            "print(os.getcwd())\n"
            "with open('child-wrote.txt', 'w') as fh: fh.write('ok')\n"
        ),
    )
    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    assert result.returncode == 0, result.stderr
    sandbox = app_storage_root(app_id)
    # macOS resolves /var → /private/var; compare the canonical form.
    assert result.stdout.strip() == str(sandbox)
    assert (sandbox / "child-wrote.txt").read_text() == "ok"


# ---------------------------------------------------------------------------
# (2) Capability + path gates
# ---------------------------------------------------------------------------


def test_runner_refuses_without_process_execute_scope(local_db):
    """An app without `process.execute` cannot spawn — gate trips
    BEFORE we touch the filesystem."""
    from services.app_sandbox import ScopeViolation

    app_id, _ = _install(["filesystem.read"])  # no process.execute
    _plant_main(app_id, "print('should not run')\n")

    with pytest.raises(ScopeViolation):
        asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))


def test_runner_refuses_for_isolated_app(local_db):
    from services.app_sandbox import AppIsolated

    app_id, _ = _install(["process.execute"])
    _plant_main(app_id, "print('quarantine breach')\n")
    SANDBOX_MANAGER.isolate(app_id, reason="test")
    with pytest.raises(AppIsolated):
        asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))


def test_runner_refuses_missing_entrypoint(local_db):
    app_id, _ = _install(["process.execute"])
    # No main.py planted.
    with pytest.raises(FileNotFoundError):
        asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))


def test_runner_refuses_traversal_entrypoint(local_db):
    """Even if `process.execute` is granted, the entrypoint MUST be
    inside the sandbox. `../../etc/...` is rejected by the same
    containment check P4.3 uses."""
    from services.app_filesystem import PathTraversalAttempt

    app_id, _ = _install(["process.execute"])
    _plant_main(app_id, "print('decoy')\n")
    with pytest.raises(PathTraversalAttempt):
        asyncio.run(
            APP_PROCESS_RUNNER.run_entrypoint(
                app_id,
                entrypoint="../../etc/passwd",
            )
        )


def test_runner_rejects_malformed_args(local_db):
    app_id, _ = _install(["process.execute"])
    _plant_main(app_id, "print('hi')\n")
    with pytest.raises(ValueError, match="strings"):
        asyncio.run(
            APP_PROCESS_RUNNER.run_entrypoint(
                app_id,
                args=["ok", 123],  # not a string
            )
        )
    with pytest.raises(ValueError, match="NUL"):
        asyncio.run(
            APP_PROCESS_RUNNER.run_entrypoint(
                app_id,
                args=["evil\x00"],
            )
        )


# ---------------------------------------------------------------------------
# (3) Subprocess hardening — timeouts, large output
# ---------------------------------------------------------------------------


def test_runner_kills_runaway_child_on_timeout(local_db):
    """A child that sleeps past the wall-clock timeout is killed.
    The runner returns timed_out=True instead of hanging."""
    app_id, _ = _install(["process.execute"])
    _plant_main(
        app_id,
        (
            "import time\n"
            "for _ in range(100):\n"
            "    print('alive')\n"
            "    time.sleep(0.5)\n"
        ),
    )
    # Use a short timeout to keep the test fast.
    # Override per-call timeout via a fresh runner instance to avoid
    # mutating the module singleton.
    from services.app_sandbox import AppProcessRunner

    fast = AppProcessRunner(timeout_s=0.3)
    result = asyncio.run(fast.run_entrypoint(app_id))
    assert result.timed_out is True
    # Child was killed, so returncode is negative (signal) on POSIX
    # or a generic non-zero on Windows.
    assert result.returncode != 0


def test_runner_handles_large_stdout_without_deadlock(local_db):
    """Children writing more than the pipe buffer (~64 KB on Linux)
    must not deadlock — `asyncio.communicate` drains both pipes."""
    app_id, _ = _install(["process.execute"])
    # Print ~256 KB to stdout — well past any reasonable pipe buffer.
    _plant_main(app_id, ("import sys\n" "sys.stdout.write('x' * 256 * 1024)\n"))
    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    assert result.returncode == 0, result.stderr
    # Runner truncates the captured stdout to 64 KB to bound the
    # response size — confirm we didn't deadlock and DID capture
    # the head of the stream.
    assert len(result.stdout) > 0
    assert result.timed_out is False


# ---------------------------------------------------------------------------
# (4) Penetration test — child trying to reach a blocked scope
# ---------------------------------------------------------------------------


def test_child_callback_validated_by_get_app_context(local_db, network_guard):
    """The ephemeral X-App-Secret minted for a run is accepted by
    `get_app_context` exactly while the run is active. We register
    one synthetically (skipping the real spawn) and prove the
    middleware's verify path accepts it."""
    from middleware.auth import get_app_context
    from services.app_sandbox import (
        _register_ephemeral_secret,
        _unregister_ephemeral_secret,
    )

    app_id, _ = _install(["process.execute"])

    app = FastAPI()

    @app.get("/whoami")
    async def whoami(authed=Depends(get_app_context)):
        return {"app_id": authed}

    client = TestClient(app)

    # Mint an ephemeral and register it for `app_id`.
    secret = "ephemeral-secret-xyz"
    h = _register_ephemeral_secret(app_id, secret)
    try:
        r = client.get(
            "/whoami",
            headers={"X-App-Id": app_id, "X-App-Secret": secret},
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"app_id": app_id}
    finally:
        _unregister_ephemeral_secret(app_id, h)

    # After unregister, the same secret must be rejected.
    r2 = client.get(
        "/whoami",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
    )
    assert r2.status_code == 403


def test_child_callback_denied_for_blocked_cloud_llm(local_db, network_guard):
    """Directive's penetration scenario. An app with
    `restrictions: ["network.blocked"]` runs a child that calls
    /api/chat-style cloud LLM. The same gate that protects in-process
    callers protects subprocess-originated callbacks — the child's
    X-App-Id resolves to the same manifest, so `network.outbound`
    is denied and the route raises 403."""
    from fastapi import Depends, HTTPException
    from middleware.auth import get_app_context

    app_id, _ = _install(
        scopes=["process.execute", "llm"],
        restrictions=["network.blocked"],
    )

    # In-process "cloud chat completion" stand-in: a route that
    # would normally hand off to CloudRouter.resolve(). We invoke
    # the dispatcher directly to demonstrate the gate denies even
    # when the call comes in over the validated app-secret header.
    from services.llm_dispatcher import CloudRouter, LLMRequestContext

    app = FastAPI()

    @app.post("/api/chat/cloud")
    async def cloud_chat(authed=Depends(get_app_context)):
        if not authed:
            raise HTTPException(403, "app_context_required")
        # The dispatcher's _enforce_app_scope translates
        # ScopeViolation -> HTTPException(403). We let it propagate.
        CloudRouter().resolve(LLMRequestContext(role="coding", app_id=authed))
        return {"ok": True}  # unreachable

    # Register the secret as if a child were in flight.
    secret = "ephemeral-during-run"
    from services.app_sandbox import (
        _register_ephemeral_secret,
        _unregister_ephemeral_secret,
    )

    h = _register_ephemeral_secret(app_id, secret)
    try:
        client = TestClient(app)
        r = client.post(
            "/api/chat/cloud",
            headers={"X-App-Id": app_id, "X-App-Secret": secret},
        )
        # The penetration attempt MUST be rejected with 403 and
        # specifically point at network.outbound as the blocked scope.
        assert r.status_code == 403, r.text
        detail = r.json()["detail"]
        assert detail["error"] == "scope_violation"
        assert detail["scope"] == "network.outbound"
        assert "network.blocked" in detail["reason"]
    finally:
        _unregister_ephemeral_secret(app_id, h)


def test_runner_clears_ephemeral_secret_after_exit(local_db):
    """The runner MUST tear down the ephemeral secret when the child
    exits — otherwise a stale secret could be replayed."""
    app_id, _ = _install(["process.execute"])
    _plant_main(app_id, "print('done')\n")

    asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    # No leftover entry for this app.
    assert app_id not in _EPHEMERAL_SECRETS


# ---------------------------------------------------------------------------
# (5) HTTP endpoint — POST /api/apps/{id}/execute
# ---------------------------------------------------------------------------


def _build_http_app() -> FastAPI:
    from api.app_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def test_http_execute_roundtrip(local_db, network_guard):
    app_id, secret = _install(["process.execute"])
    _plant_main(app_id, "print('via http')\n")
    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{app_id}/execute",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["returncode"] == 0
    assert "via http" in body["stdout"]


def test_http_execute_403_without_scope(local_db, network_guard):
    app_id, secret = _install(["filesystem.read"])  # missing process.execute
    _plant_main(app_id, "print('no')\n")
    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{app_id}/execute",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["scope"] == "process.execute"


def test_http_execute_404_for_missing_entrypoint(local_db, network_guard):
    app_id, secret = _install(["process.execute"])
    # no main.py planted
    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{app_id}/execute",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "entrypoint_missing"


def test_http_execute_traversal_entrypoint_blocked(local_db, network_guard):
    app_id, secret = _install(["process.execute"])
    _plant_main(app_id, "print('inside')\n")
    client = TestClient(_build_http_app())
    r = client.post(
        f"/api/apps/{app_id}/execute",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"entrypoint": "../../etc/passwd"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "path_traversal_attempt"
