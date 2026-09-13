"""
P5.4 — Resource Guard + Auto-Isolation pen-tests.

A rogue app that overshoots its CPU or memory budget MUST be
auto-isolated by the time `run_entrypoint` returns. The directive's
two worked scenarios:

  - **CPU bomb**: `while True: pass` — the wall-clock timeout fires
    and the runner kills it. `_classify_resource_violation` marks
    cpu_limit_violated=True (because timed_out=True), the manager
    flips status='isolated', and a `resource_limit_exceeded` row
    lands in `securityAuditLog`.

  - **Memory bomb**: an explicit `bytearray(N)` allocation that
    triggers MemoryError. We rely on Python surfacing the error
    on stderr (regardless of whether the OS ulimits applied) —
    the classifier picks it up either via stderr-scan OR via the
    OOM-killer signal path.

The pure classifier function is also unit-tested without spawning
a real process — keeps the failure modes documented even when the
host's RLIMIT enforcement is flaky (macOS asyncio preexec_fn
fallback path).
"""

from __future__ import annotations

import asyncio
import json
import signal

import pytest

from core.database.sqlite_setup import (
    SecurityAuditLog,
    _reset_for_tests,
    get_session,
    init_db,
)
from services.app_sandbox import (
    APP_PROCESS_RUNNER,
    SANDBOX_MANAGER,
    AppProcessRunner,
    ExecutionResult,
    _classify_resource_violation,
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
            "name": "p54-test",
            "version": "1.0.0",
            "scopes": list(scopes),
            "restrictions": list(restrictions),
        }
    )
    return res["app_id"], res["secret"]


def _plant(app_id: str, body: str, *, name: str = "main.py") -> None:
    sandbox = app_storage_root(app_id)
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / name).write_text(body)


def _audit_rows(app_id: str, kind: str) -> list:
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind=kind,
                appId=app_id,
            )
            .all()
        )
        return [
            {
                "id": r.id,
                "kind": r.kind,
                "reason": r.reason,
                "details": json.loads(r.details_json or "{}"),
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# (1) Pure classifier — every signal/exit pattern documented
# ---------------------------------------------------------------------------


def _result(*, rc, timed_out=False, stderr=""):
    """Build an ExecutionResult stub for the classifier."""
    return ExecutionResult(
        app_id="x",
        returncode=rc,
        stdout="",
        stderr=stderr,
        duration_ms=0.0,
        timed_out=timed_out,
        killed_by_limit=(rc < 0),
    )


def test_classifier_timeout_counts_as_cpu():
    """SIGKILL + timed_out=True is OUR wall-clock kill — attribute
    purely to CPU. Without the `if timed_out` short-circuit the
    SIGKILL branch would flag it as memory too, which would be a
    false positive (the kernel didn't OOM-kill it; we did)."""
    c = _classify_resource_violation(_result(rc=-9, timed_out=True))
    assert c["violation"] is True
    assert c["cpu_limit_violated"] is True
    assert c["memory_limit_violated"] is False


def test_classifier_sigxcpu_is_cpu():
    sigxcpu = -getattr(signal, "SIGXCPU", -24)
    c = _classify_resource_violation(_result(rc=sigxcpu))
    assert c["cpu_limit_violated"] is True
    assert c["memory_limit_violated"] is False


def test_classifier_sigkill_without_timeout_is_memory():
    """A SIGKILL with NO wall-clock-timeout flag is most likely the
    OOM-killer / RLIMIT_AS surfacing."""
    c = _classify_resource_violation(_result(rc=-9, timed_out=False))
    assert c["memory_limit_violated"] is True
    assert c["cpu_limit_violated"] is False


def test_classifier_memoryerror_in_stderr_is_memory():
    c = _classify_resource_violation(
        _result(rc=1, stderr="Traceback…\nMemoryError\n"),
    )
    assert c["memory_limit_violated"] is True
    assert c["violation"] is True


def test_classifier_clean_exit_is_not_a_violation():
    c = _classify_resource_violation(_result(rc=0, stderr="ok\n"))
    assert c["violation"] is False
    assert c["cpu_limit_violated"] is False
    assert c["memory_limit_violated"] is False


def test_classifier_sigsegv_is_memory():
    sigsegv = -getattr(signal, "SIGSEGV", -11)
    c = _classify_resource_violation(_result(rc=sigsegv))
    assert c["memory_limit_violated"] is True


# ---------------------------------------------------------------------------
# (2) CPU bomb — directive's Test 1
# ---------------------------------------------------------------------------


def test_cpu_bomb_auto_isolates_and_audits(local_db):
    """Directive's Test 1 — `while True: pass` runs past the
    wall-clock timeout. The runner kills it, classifies as CPU
    violation, and flips status='isolated'."""
    app_id, _ = _install(["process.execute"])
    _plant(app_id, ("while True:\n" "    pass\n"))

    # Use a fresh runner with a tight timeout so the test is fast.
    runner = AppProcessRunner(timeout_s=0.4)
    result = asyncio.run(runner.run_entrypoint(app_id))

    assert result.timed_out is True
    assert result.returncode != 0

    # Status flipped to isolated.
    record = SANDBOX_MANAGER.get(app_id)
    assert record["status"] == "isolated", record

    # An audit row was emitted with the right kind + details.
    rows = _audit_rows(app_id, "resource_limit_exceeded")
    assert len(rows) >= 1
    row = rows[-1]
    assert row["details"]["cpu_limit_violated"] is True
    assert row["details"]["exit_code"] == result.returncode
    assert row["details"]["timed_out"] is True


def test_cpu_bomb_blocks_subsequent_gate_checks(local_db):
    """After auto-isolation, the very next gate check refuses with
    AppIsolated (translated to HTTP 403 at the route layer)."""
    from services.app_sandbox import AppIsolated, PERMISSION_GATE

    app_id, _ = _install(["process.execute", "llm.local"])
    _plant(app_id, "while True: pass\n")

    runner = AppProcessRunner(timeout_s=0.4)
    asyncio.run(runner.run_entrypoint(app_id))

    PERMISSION_GATE._clear(app_id)  # force re-hydrate
    with pytest.raises(AppIsolated):
        PERMISSION_GATE.check(app_id, "llm.local")


# ---------------------------------------------------------------------------
# (3) Memory bomb — directive's Test 2
# ---------------------------------------------------------------------------


def test_memory_bomb_auto_isolates_and_audits(local_db):
    """Directive's Test 2 — a child that surfaces MemoryError on
    stderr must be auto-isolated.

    We raise MemoryError EXPLICITLY rather than relying on a real
    OOM. Reasons:
      * macOS overcommit + sparse pages: `bytearray(N)` of N=4GB
        succeeds without backing physical memory; only a full
        page-write would force a real OOM, which is risky for
        the test runner.
      * asyncio's preexec_fn fallback on some macOS configurations
        silently drops RLIMIT_AS, so the kernel never SIGKILLs.
    The classifier scans stderr for "MemoryError" — the path under
    test is identical whether the error came from the OOM-killer's
    SIGKILL or from Python catching ENOMEM on malloc. The pure
    classifier unit-test above already covers the SIGKILL path."""
    app_id, _ = _install(["process.execute"])
    _plant(
        app_id,
        (
            "import sys\n"
            "# Synthetic OOM — surfaces 'MemoryError' on stderr identical\n"
            "# to a real one. The auto-isolate path keys off this string.\n"
            "raise MemoryError('synthetic OOM for P5.4 pen-test')\n"
        ),
    )

    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    assert result.returncode != 0
    assert "MemoryError" in result.stderr

    record = SANDBOX_MANAGER.get(app_id)
    assert record["status"] == "isolated", record

    rows = _audit_rows(app_id, "resource_limit_exceeded")
    assert rows, "expected a resource_limit_exceeded audit row"
    row = rows[-1]
    assert row["details"]["memory_limit_violated"] is True
    assert row["details"]["cpu_limit_violated"] is False


# ---------------------------------------------------------------------------
# (4) Non-bomb baseline — clean exits MUST NOT auto-isolate
# ---------------------------------------------------------------------------


def test_clean_exit_does_not_isolate(local_db):
    """A well-behaved app's normal `sys.exit(0)` must NOT trip the
    Resource Guard — false positives would brick legitimate apps."""
    app_id, _ = _install(["process.execute"])
    _plant(app_id, "print('hello'); import sys; sys.exit(0)\n")
    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    assert result.returncode == 0

    record = SANDBOX_MANAGER.get(app_id)
    assert record["status"] == "active"
    assert _audit_rows(app_id, "resource_limit_exceeded") == []


def test_nonzero_exit_without_signal_does_not_isolate(local_db):
    """An app that exits 1 with a normal Python error (no
    MemoryError) is NOT a resource violation."""
    app_id, _ = _install(["process.execute"])
    _plant(
        app_id,
        ("import sys\n" "sys.stderr.write('oops: a regular bug\\n')\n" "sys.exit(1)\n"),
    )
    result = asyncio.run(APP_PROCESS_RUNNER.run_entrypoint(app_id))
    assert result.returncode == 1

    record = SANDBOX_MANAGER.get(app_id)
    # Not auto-isolated — operator decides what to do with a buggy
    # app that exits 1.
    assert record["status"] == "active"
    assert _audit_rows(app_id, "resource_limit_exceeded") == []


# ---------------------------------------------------------------------------
# (5) Audit log endpoint surfaces the events
# ---------------------------------------------------------------------------


def test_audit_endpoint_surfaces_resource_limit_kind(local_db, network_guard):
    """GET /api/system/audit-logs?kind=resource_limit_exceeded
    must round-trip the rows the runner inserted."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.system_routes import router as system_router

    app_id, _ = _install(["process.execute"])
    _plant(app_id, "while True: pass\n")
    runner = AppProcessRunner(timeout_s=0.4)
    asyncio.run(runner.run_entrypoint(app_id))

    fastapi_app = FastAPI()
    fastapi_app.include_router(system_router)
    client = TestClient(fastapi_app)
    body = client.get(
        "/api/system/audit-logs",
        params={"kind": "resource_limit_exceeded"},
    ).json()
    assert body["count"] >= 1
    assert any(
        r["app_id"] == app_id and r["details"]["cpu_limit_violated"] is True
        for r in body["rows"]
    )


def test_apps_endpoint_reflects_isolated_status_immediately(local_db, network_guard):
    """The directive's Task 2 — GET /api/apps must show the
    auto-isolated status without a server reboot. The endpoint
    reads SQLite directly so this is a sanity test of the wiring."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.app_routes import router

    app_id, _ = _install(["process.execute"])
    _plant(app_id, "while True: pass\n")
    runner = AppProcessRunner(timeout_s=0.4)
    asyncio.run(runner.run_entrypoint(app_id))

    fastapi_app = FastAPI()
    fastapi_app.include_router(router)
    client = TestClient(fastapi_app)

    # Build a bearer token via the airgap harness shape.
    from tests.airgap_harness import bootstrap_sqlite_user, make_offline_token

    clerk_id = "user_p54_apps_endpoint"
    bootstrap_sqlite_user(clerk_id, "alice@air.gap")
    token = make_offline_token(clerk_id)

    r = client.get(
        "/api/apps",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    rec = next((a for a in body["apps"] if a["id"] == app_id), None)
    assert rec is not None
    assert rec["status"] == "isolated"
