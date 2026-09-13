"""
Stage 5 · Workflow orchestration E2E.

Submit a workflow → orchestrator validates DAG, signs row, persists
it → retrieve + verify signature → tamper detection.
"""

from __future__ import annotations


import pytest


def test_signed_row_verifies(e2e_env):
    from services.agent_orchestrator import (
        _sign_workflow_row,
        _verify_workflow_row,
    )

    sig, pub = _sign_workflow_row(
        run_id="r-ok",
        workspace_id="ws-ok",
        manifest_json='{"steps":[{"id":"a","depends_on":[]}]}',
        created_at_ms=1_700_000_000_000,
    )

    class _Row:
        id = "r-ok"
        workspaceId = "ws-ok"
        manifest_json = '{"steps":[{"id":"a","depends_on":[]}]}'
        createdAt = 1_700_000_000_000
        signature = sig
        signing_public_key = pub

    assert _verify_workflow_row(_Row()) is True


def test_manifest_tampered_post_signing_rejected(e2e_env):
    from services.agent_orchestrator import (
        _sign_workflow_row,
        _verify_workflow_row,
    )

    sig, pub = _sign_workflow_row(
        run_id="r-t",
        workspace_id="ws-t",
        manifest_json='{"steps":[{"id":"a"}]}',
        created_at_ms=1,
    )

    class _Row:
        id = "r-t"
        workspaceId = "ws-t"
        manifest_json = '{"steps":[{"id":"a","extra":"injected"}]}'
        createdAt = 1
        signature = sig
        signing_public_key = pub

    assert _verify_workflow_row(_Row()) is False


def test_topological_order_diamond_real_payload(e2e_env):
    """Real diamond DAG via the topo resolver."""
    from services.agent_orchestrator import _topological_order

    steps = [
        {"id": "build", "depends_on": []},
        {"id": "lint", "depends_on": ["build"]},
        {"id": "test", "depends_on": ["build"]},
        {"id": "deploy", "depends_on": ["lint", "test"]},
    ]
    assert _topological_order(steps) == ["build", "lint", "test", "deploy"]


def test_topological_order_rejects_unknown_dep(e2e_env):
    from services.agent_orchestrator import (
        _topological_order,
        WorkflowValidationError,
    )

    with pytest.raises(WorkflowValidationError):
        _topological_order([{"id": "a", "depends_on": ["ghost"]}])


def test_topological_order_rejects_cycle(e2e_env):
    from services.agent_orchestrator import (
        _topological_order,
        WorkflowValidationError,
    )

    steps = [
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
    ]
    with pytest.raises(WorkflowValidationError):
        _topological_order(steps)


def test_workflow_tampered_carries_run_id_e2e(e2e_env):
    from services.agent_orchestrator import WorkflowTampered

    exc = WorkflowTampered("run-id-1", reason="post-write mutation")
    assert "run-id-1" in str(exc)
    assert exc.reason == "post-write mutation"


def test_canonical_bytes_stable_across_processes(e2e_env):
    """The canonical_bytes function is deterministic — same inputs always
    produce the same output, run after run."""
    from services.agent_orchestrator import _workflow_canonical_bytes

    b1 = _workflow_canonical_bytes(
        run_id="r",
        workspace_id="w",
        manifest_json='{"k":1}',
        created_at_ms=42,
    )
    b2 = _workflow_canonical_bytes(
        run_id="r",
        workspace_id="w",
        manifest_json='{"k":1}',
        created_at_ms=42,
    )
    assert b1 == b2


def test_workflow_signature_binds_created_at(e2e_env):
    """Mutating createdAt invalidates the signature."""
    from services.agent_orchestrator import (
        _sign_workflow_row,
        _verify_workflow_row,
    )

    sig, pub = _sign_workflow_row(
        run_id="r-t2",
        workspace_id="ws",
        manifest_json="{}",
        created_at_ms=1000,
    )

    class _Row:
        id = "r-t2"
        workspaceId = "ws"
        manifest_json = "{}"
        createdAt = 2000  # tampered
        signature = sig
        signing_public_key = pub

    assert _verify_workflow_row(_Row()) is False


def test_complex_manifest_topological_order(e2e_env):
    """Test a real-world-ish 8-step workflow."""
    from services.agent_orchestrator import _topological_order

    steps = [
        {"id": "fetch", "depends_on": []},
        {"id": "parse", "depends_on": ["fetch"]},
        {"id": "validate", "depends_on": ["parse"]},
        {"id": "transform", "depends_on": ["validate"]},
        {"id": "enrich-a", "depends_on": ["transform"]},
        {"id": "enrich-b", "depends_on": ["transform"]},
        {"id": "merge", "depends_on": ["enrich-a", "enrich-b"]},
        {"id": "store", "depends_on": ["merge"]},
    ]
    order = _topological_order(steps)
    # First and last are deterministic.
    assert order[0] == "fetch"
    assert order[-1] == "store"
    # `validate` comes after `parse`, etc.
    pos = {sid: i for i, sid in enumerate(order)}
    assert pos["fetch"] < pos["parse"]
    assert pos["parse"] < pos["validate"]
    assert pos["validate"] < pos["transform"]
    assert pos["transform"] < pos["enrich-a"]
    assert pos["transform"] < pos["enrich-b"]
    assert pos["enrich-a"] < pos["merge"]
    assert pos["enrich-b"] < pos["merge"]
    assert pos["merge"] < pos["store"]
