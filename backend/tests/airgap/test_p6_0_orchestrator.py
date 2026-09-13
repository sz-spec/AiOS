"""
P6.0 — Sovereign Orchestrator E2E tests.

Coverage groups:

  1. DAG resolver — pure-function `_topological_order`: linear chain,
     diamond, fan-out, deterministic tie-break, cycle detection,
     unknown-dep rejection.

  2. submit_workflow — validation rejects bad shapes BEFORE any
     row is persisted (workspace mismatch, unknown app id, etc.).

  3. secure_handoff (Custodian) — all five checks:
       a. both apps active           → refused if either isolated
       b. shared workspaceId          → refused on mismatch
       c. source filesystem.write    → refused if missing
       d. target filesystem.read     → refused if missing
       e. path containment            → traversal auto-isolates

  4. Full multi-app E2E — 3 sandboxes, 3 steps, 2 handoffs. Verify
     execution order, file contents at each handoff, run state
     transitions, and air-gap isolation (loopback only).
"""

from __future__ import annotations

import asyncio
import base64
import textwrap

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database.sqlite_setup import (
    WorkflowRun,
    WorkflowStep,
    _reset_for_tests,
    get_session,
    init_db,
)
from services.agent_orchestrator import (
    HandoffViolation,
    ORCHESTRATOR,
    WorkflowValidationError,
    _topological_order,
)
from services.app_filesystem import FILESYSTEM_MANAGER
from services.app_sandbox import (
    SANDBOX_MANAGER,
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


def _install(scopes, *, workspace_id, name="p60-app"):
    res = SANDBOX_MANAGER.install(
        {
            "name": name,
            "version": "1.0.0",
            "scopes": list(scopes),
        },
        workspace_id=workspace_id,
    )
    return res["app_id"], res["secret"]


def _plant(app_id, body, *, name="main.py"):
    sandbox = app_storage_root(app_id)
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / name).write_text(body)


# ---------------------------------------------------------------------------
# (1) _topological_order — pure helper
# ---------------------------------------------------------------------------


def test_topo_linear_chain():
    steps = [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["b"]},
    ]
    assert _topological_order(steps) == ["a", "b", "c"]


def test_topo_diamond_is_deterministic():
    """a → {b, c} → d. Result must be deterministic across runs."""
    steps = [
        {"id": "d", "depends_on": ["b", "c"]},
        {"id": "c", "depends_on": ["a"]},
        {"id": "b", "depends_on": ["a"]},
        {"id": "a", "depends_on": []},
    ]
    order = _topological_order(steps)
    assert order[0] == "a" and order[-1] == "d"
    # Tie-break on sorted ids: b before c.
    assert order.index("b") < order.index("c")


def test_topo_rejects_cycle():
    steps = [
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
    ]
    with pytest.raises(WorkflowValidationError, match="cycle"):
        _topological_order(steps)


def test_topo_rejects_self_loop():
    with pytest.raises(WorkflowValidationError, match="cannot depend on itself"):
        _topological_order([{"id": "x", "depends_on": ["x"]}])


def test_topo_rejects_unknown_dep():
    with pytest.raises(WorkflowValidationError, match="unknown step"):
        _topological_order(
            [
                {"id": "a", "depends_on": ["ghost"]},
            ]
        )


def test_topo_rejects_duplicate_ids():
    with pytest.raises(WorkflowValidationError, match="duplicate"):
        _topological_order(
            [
                {"id": "a", "depends_on": []},
                {"id": "a", "depends_on": []},
            ]
        )


def test_topo_empty_steps_rejected():
    with pytest.raises(WorkflowValidationError, match="non-empty"):
        _topological_order([])


# ---------------------------------------------------------------------------
# (2) submit_workflow validation
# ---------------------------------------------------------------------------


def test_submit_workflow_persists_run_and_steps(local_db):
    a, _ = _install(["process.execute"], workspace_id="ws-1")
    b, _ = _install(["process.execute"], workspace_id="ws-1")
    handle = ORCHESTRATOR.submit_workflow(
        {
            "name": "smoke",
            "workspace_id": "ws-1",
            "steps": [
                {"id": "x", "app_id": a, "depends_on": [], "task": {}},
                {"id": "y", "app_id": b, "depends_on": ["x"], "task": {}},
            ],
        }
    )
    assert handle.status == "pending"
    assert handle.step_count == 2

    with get_session() as session:
        run = session.query(WorkflowRun).filter_by(id=handle.run_id).one()
        steps = session.query(WorkflowStep).filter_by(runId=handle.run_id).all()
        assert run.workspaceId == "ws-1"
        assert run.status == "pending"
        assert len(steps) == 2
        assert {s.stepId for s in steps} == {"x", "y"}


def test_submit_workflow_rejects_cross_workspace_app(local_db):
    """An app whose workspace differs from the workflow's is rejected
    at submit time — nothing is persisted."""
    a, _ = _install(["process.execute"], workspace_id="ws-1")
    b, _ = _install(["process.execute"], workspace_id="ws-OTHER")
    with pytest.raises(WorkflowValidationError, match="workspace"):
        ORCHESTRATOR.submit_workflow(
            {
                "workspace_id": "ws-1",
                "steps": [
                    {"id": "x", "app_id": a, "depends_on": []},
                    {"id": "y", "app_id": b, "depends_on": ["x"]},
                ],
            }
        )
    with get_session() as session:
        assert session.query(WorkflowRun).count() == 0


def test_submit_workflow_rejects_unknown_app(local_db):
    with pytest.raises(WorkflowValidationError, match="unknown app"):
        ORCHESTRATOR.submit_workflow(
            {
                "workspace_id": "ws-1",
                "steps": [{"id": "x", "app_id": "ghost", "depends_on": []}],
            }
        )


def test_submit_workflow_rejects_bad_handoff(local_db):
    a, _ = _install(["process.execute"], workspace_id="ws-1")
    with pytest.raises(WorkflowValidationError, match="unknown step"):
        ORCHESTRATOR.submit_workflow(
            {
                "workspace_id": "ws-1",
                "steps": [{"id": "a", "app_id": a, "depends_on": []}],
                "handoffs": [{"from_step": "a", "to_step": "ghost", "path": "x"}],
            }
        )


# ---------------------------------------------------------------------------
# (3) secure_handoff — the Custodian's five checks
# ---------------------------------------------------------------------------


def test_handoff_happy_path_copies_into_shared_inputs(local_db):
    src, _ = _install(["filesystem.write", "filesystem.read"], workspace_id="w")
    tgt, _ = _install(["filesystem.read", "filesystem.write"], workspace_id="w")
    FILESYSTEM_MANAGER.write_app_file(src, "payload.txt", "hello orchestrator")

    result = ORCHESTRATOR.secure_handoff(
        source_app_id=src,
        target_app_id=tgt,
        virtual_file_path="payload.txt",
    )
    assert result["bytes_copied"] == len(b"hello orchestrator")

    received = app_storage_root(tgt) / "shared_inputs" / "payload.txt"
    assert received.read_text() == "hello orchestrator"


def test_handoff_workspace_mismatch_blocked_and_isolates_source(local_db):
    src, _ = _install(["filesystem.write"], workspace_id="ws-A")
    tgt, _ = _install(["filesystem.read"], workspace_id="ws-B")
    FILESYSTEM_MANAGER.write_app_file(src, "x.txt", "z")
    with pytest.raises(HandoffViolation, match="workspace"):
        ORCHESTRATOR.secure_handoff(
            source_app_id=src,
            target_app_id=tgt,
            virtual_file_path="x.txt",
        )
    # SOURCE is auto-isolated.
    assert SANDBOX_MANAGER.get(src)["status"] == "isolated"
    # Target is NOT isolated.
    assert SANDBOX_MANAGER.get(tgt)["status"] == "active"


def test_handoff_source_missing_write_scope_blocked(local_db):
    """Source can read but can't write — refused (source is the
    offender for trying to push without holding write)."""
    src, _ = _install(["filesystem.read"], workspace_id="w")
    tgt, _ = _install(["filesystem.read"], workspace_id="w")
    (app_storage_root(src) / "x.txt").write_text("planted")  # bypass scope
    with pytest.raises(HandoffViolation, match="filesystem.write"):
        ORCHESTRATOR.secure_handoff(
            source_app_id=src,
            target_app_id=tgt,
            virtual_file_path="x.txt",
        )
    assert SANDBOX_MANAGER.get(src)["status"] == "isolated"


def test_handoff_target_missing_read_scope_blocked_and_isolates_target(local_db):
    src, _ = _install(["filesystem.write"], workspace_id="w")
    tgt, _ = _install(["filesystem.write"], workspace_id="w")  # no read
    FILESYSTEM_MANAGER.write_app_file(src, "x.txt", "z")
    with pytest.raises(HandoffViolation, match="filesystem.read"):
        ORCHESTRATOR.secure_handoff(
            source_app_id=src,
            target_app_id=tgt,
            virtual_file_path="x.txt",
        )
    # TARGET is the offender — it should declare it accepts files.
    assert SANDBOX_MANAGER.get(tgt)["status"] == "isolated"


def test_handoff_inactive_source_blocked(local_db):
    src, _ = _install(["filesystem.write"], workspace_id="w")
    tgt, _ = _install(["filesystem.read"], workspace_id="w")
    SANDBOX_MANAGER.isolate(src, reason="test-pre")
    with pytest.raises(HandoffViolation, match="not active"):
        ORCHESTRATOR.secure_handoff(
            source_app_id=src,
            target_app_id=tgt,
            virtual_file_path="anything.txt",
        )


def test_handoff_traversal_path_blocked(local_db):
    """Path containment is the same `_resolve_inside_sandbox` used
    everywhere — it auto-isolates the source on its own."""
    from services.app_filesystem import PathTraversalAttempt

    src, _ = _install(["filesystem.write"], workspace_id="w")
    tgt, _ = _install(["filesystem.read"], workspace_id="w")
    with pytest.raises(PathTraversalAttempt):
        ORCHESTRATOR.secure_handoff(
            source_app_id=src,
            target_app_id=tgt,
            virtual_file_path="../../etc/passwd",
        )
    assert SANDBOX_MANAGER.get(src)["status"] == "isolated"


def test_handoff_missing_source_file_blocked(local_db):
    src, _ = _install(["filesystem.write"], workspace_id="w")
    tgt, _ = _install(["filesystem.read"], workspace_id="w")
    with pytest.raises(HandoffViolation, match="missing"):
        ORCHESTRATOR.secure_handoff(
            source_app_id=src,
            target_app_id=tgt,
            virtual_file_path="nothing.txt",
        )


# ---------------------------------------------------------------------------
# (4) Full multi-app E2E — directive's Test 4
# ---------------------------------------------------------------------------


# Mock parser — writes a fixed-output file when its task says so.
_PARSER_MAIN = textwrap.dedent("""
    import os, json, sys, pathlib
    task = json.loads(os.environ.get("VOS3_TASK_JSON", "{}"))
    output_file = task["output_file"]
    content = task["content"]
    p = pathlib.Path(output_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    print(json.dumps({
        "ok": True,
        "wrote": output_file,
        "bytes": len(content),
    }))
""")

# Mock AI agent — reads an input file from shared_inputs/ and writes
# a mock-translated version. Translation is `.upper() + suffix`.
_TRANSLATOR_MAIN = textwrap.dedent("""
    import os, json, sys, pathlib
    task = json.loads(os.environ.get("VOS3_TASK_JSON", "{}"))
    src = pathlib.Path(task["input_file"])
    dst = pathlib.Path(task["output_file"])
    text = src.read_text()
    translated = text.upper() + " [TRANSLATED]"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(translated)
    print(json.dumps({
        "ok": True,
        "source": str(src),
        "wrote": str(dst),
        "translation": translated,
    }))
""")

# Mock compressor — reads input, "compresses" it via base64 + marker.
_COMPRESSOR_MAIN = textwrap.dedent("""
    import os, json, sys, base64, pathlib
    task = json.loads(os.environ.get("VOS3_TASK_JSON", "{}"))
    src = pathlib.Path(task["input_file"])
    dst = pathlib.Path(task["output_file"])
    raw = src.read_bytes()
    compressed = b"ZMOCK1:" + base64.b64encode(raw)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(compressed)
    print(json.dumps({
        "ok": True,
        "source_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "wrote": str(dst),
    }))
""")


def test_full_e2e_legacy_translator_compressor_workflow(local_db, network_guard):
    """Directive's Test 4 — three sandboxed apps cooperate through
    the orchestrator with secure handoffs between each step. End-
    to-end execution must be 100% offline."""
    ws = "ws-pipeline-e2e"
    parser, _ = _install(
        ["process.execute", "filesystem.write", "filesystem.read"],
        workspace_id=ws,
        name="Legacy_Parser",
    )
    translator, _ = _install(
        ["process.execute", "filesystem.write", "filesystem.read"],
        workspace_id=ws,
        name="Translator_Agent",
    )
    compressor, _ = _install(
        ["process.execute", "filesystem.write", "filesystem.read"],
        workspace_id=ws,
        name="Legacy_Compressor",
    )
    _plant(parser, _PARSER_MAIN)
    _plant(translator, _TRANSLATOR_MAIN)
    _plant(compressor, _COMPRESSOR_MAIN)

    manifest = {
        "name": "translate-and-compress",
        "workspace_id": ws,
        "steps": [
            {
                "id": "parse",
                "app_id": parser,
                "depends_on": [],
                "task": {
                    "content": "Hello, vOS!",
                    "output_file": "raw.txt",
                },
            },
            {
                "id": "translate",
                "app_id": translator,
                "depends_on": ["parse"],
                "task": {
                    "input_file": "shared_inputs/raw.txt",
                    "output_file": "translated.txt",
                },
            },
            {
                "id": "compress",
                "app_id": compressor,
                "depends_on": ["translate"],
                "task": {
                    "input_file": "shared_inputs/translated.txt",
                    "output_file": "translated.zmock",
                },
            },
        ],
        "handoffs": [
            {"from_step": "parse", "to_step": "translate", "path": "raw.txt"},
            {"from_step": "translate", "to_step": "compress", "path": "translated.txt"},
        ],
    }

    handle = ORCHESTRATOR.submit_workflow(manifest)
    final = asyncio.run(ORCHESTRATOR.execute_workflow(handle.run_id))

    assert final["status"] == "completed", final.get("error")

    # Execution order matches the DAG.
    steps_by_id = {s["step_id"]: s for s in final["steps"]}
    assert steps_by_id["parse"]["status"] == "completed"
    assert steps_by_id["translate"]["status"] == "completed"
    assert steps_by_id["compress"]["status"] == "completed"

    # Step outputs round-tripped.
    assert steps_by_id["parse"]["output"]["bytes"] == len("Hello, vOS!")
    assert steps_by_id["translate"]["output"]["translation"] == (
        "HELLO, VOS! [TRANSLATED]"
    )
    assert steps_by_id["compress"]["output"]["wrote"].endswith("translated.zmock")

    # Files landed in the right sandboxes — verifies handoffs.
    parser_raw = app_storage_root(parser) / "raw.txt"
    translator_inbox = app_storage_root(translator) / "shared_inputs" / "raw.txt"
    translator_out = app_storage_root(translator) / "translated.txt"
    compressor_inbox = app_storage_root(compressor) / "shared_inputs" / "translated.txt"
    compressor_out = app_storage_root(compressor) / "translated.zmock"

    assert parser_raw.read_text() == "Hello, vOS!"
    assert translator_inbox.read_text() == "Hello, vOS!"  # handoff 1
    assert translator_out.read_text() == "HELLO, VOS! [TRANSLATED]"
    assert compressor_inbox.read_text() == "HELLO, VOS! [TRANSLATED]"  # handoff 2
    compressed = compressor_out.read_bytes()
    assert compressed.startswith(b"ZMOCK1:")
    assert base64.b64decode(compressed[len(b"ZMOCK1:") :]) == (
        b"HELLO, VOS! [TRANSLATED]"
    )

    # The whole run executed under the loopback-only kill-switch.
    assert network_guard.violations == []


def test_e2e_cross_workspace_handoff_blocks_workflow(local_db, network_guard):
    """A workflow with apps in DIFFERENT workspaces is rejected at
    submit. Demonstrates the Custodian's hardest invariant."""
    parser, _ = _install(["process.execute", "filesystem.write"], workspace_id="ws-A")
    translator, _ = _install(
        ["process.execute", "filesystem.read"],
        workspace_id="ws-B",
    )
    _plant(parser, _PARSER_MAIN)
    _plant(translator, _TRANSLATOR_MAIN)

    with pytest.raises(WorkflowValidationError, match="workspace"):
        ORCHESTRATOR.submit_workflow(
            {
                "workspace_id": "ws-A",
                "steps": [
                    {
                        "id": "parse",
                        "app_id": parser,
                        "depends_on": [],
                        "task": {"content": "x", "output_file": "raw.txt"},
                    },
                    {
                        "id": "translate",
                        "app_id": translator,
                        "depends_on": ["parse"],
                        "task": {
                            "input_file": "shared_inputs/raw.txt",
                            "output_file": "out.txt",
                        },
                    },
                ],
                "handoffs": [
                    {"from_step": "parse", "to_step": "translate", "path": "raw.txt"},
                ],
            }
        )


def test_e2e_run_marked_failed_when_step_subprocess_errors(local_db, network_guard):
    """If any step's subprocess returns non-zero, the run is marked
    failed and downstream steps never execute."""
    ws = "ws-fail"
    a, _ = _install(["process.execute", "filesystem.write"], workspace_id=ws)
    b, _ = _install(["process.execute", "filesystem.read"], workspace_id=ws)
    _plant(a, "import sys; sys.stderr.write('intentional failure\\n'); sys.exit(2)\n")
    _plant(b, "import json; print(json.dumps({'ok': True}))\n")

    handle = ORCHESTRATOR.submit_workflow(
        {
            "workspace_id": ws,
            "steps": [
                {"id": "a", "app_id": a, "depends_on": [], "task": {}},
                {"id": "b", "app_id": b, "depends_on": ["a"], "task": {}},
            ],
        }
    )
    final = asyncio.run(ORCHESTRATOR.execute_workflow(handle.run_id))
    assert final["status"] == "failed"
    steps = {s["step_id"]: s for s in final["steps"]}
    assert steps["a"]["status"] == "failed"
    # Downstream step never started.
    assert steps["b"]["status"] == "pending"


# ---------------------------------------------------------------------------
# (5) HTTP routes — submit + status
# ---------------------------------------------------------------------------


def _build_http() -> FastAPI:
    from api.orchestrator_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def test_http_submit_workflow_validates_body(
    local_db,
    bootstrap_user,
    offline_token,
    network_guard,
):
    clerk_id = "user_p60_alice"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)
    client = TestClient(_build_http())

    # Missing body.manifest.
    r = client.post(
        "/api/orchestrator/workflows",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert r.status_code == 400

    # Invalid manifest (no workspace_id).
    r = client.post(
        "/api/orchestrator/workflows",
        headers={"Authorization": f"Bearer {token}"},
        json={"manifest": {"steps": []}},
    )
    assert r.status_code == 400


def test_http_submit_workflow_persists_and_status_route_returns_it(
    local_db,
    bootstrap_user,
    offline_token,
    network_guard,
):
    clerk_id = "user_p60_bob"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)
    ws = "ws-http"
    a, _ = _install(["process.execute"], workspace_id=ws)
    client = TestClient(_build_http())

    r = client.post(
        "/api/orchestrator/workflows",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "manifest": {
                "workspace_id": ws,
                "steps": [{"id": "x", "app_id": a, "depends_on": [], "task": {}}],
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    run_id = body["run_id"]
    assert body["status"] == "pending"

    r2 = client.get(
        f"/api/orchestrator/workflows/{run_id}/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "pending"


def test_http_status_unknown_run_returns_404(
    local_db,
    bootstrap_user,
    offline_token,
    network_guard,
):
    clerk_id = "user_p60_carol"
    bootstrap_user(clerk_id)
    token = offline_token(clerk_id)
    client = TestClient(_build_http())
    r = client.get(
        "/api/orchestrator/workflows/ghost-run-id/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 404
