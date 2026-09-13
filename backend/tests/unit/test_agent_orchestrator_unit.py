"""
Stage 1 · Atomic unit isolation for services/agent_orchestrator.py.

Covers:
  _topological_order pure function on diamond/fanout/linear shapes
  _workflow_canonical_bytes determinism
  _sign_workflow_row + _verify_workflow_row roundtrip
  WorkflowTampered raised on bit-flip

Note: _topological_order returns a list of step-id strings (not dicts),
and raises on empty input.
"""

from __future__ import annotations

import pytest

from core.database.sqlite_setup import _reset_for_tests, init_db


@pytest.fixture
def orch_env(unit_env, tmp_path, monkeypatch):
    monkeypatch.setenv("VOS3_APP_DATA_DIR", str(tmp_path / "vos"))
    _reset_for_tests()
    init_db()
    from services.app_sandbox import _reset_gate_for_tests

    _reset_gate_for_tests()
    yield


# ---------------------------------------------------------------------------
# _topological_order pure function
# ---------------------------------------------------------------------------


def test_topo_linear_chain():
    from services.agent_orchestrator import _topological_order

    steps = [
        {"id": "a", "depends_on": []},
        {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["b"]},
    ]
    assert _topological_order(steps) == ["a", "b", "c"]


def test_topo_diamond_deterministic_tie_break():
    """`b` and `c` both depend on `a`; both feed `d`. Deterministic
    tie-break is by step id alphabetically."""
    from services.agent_orchestrator import _topological_order

    steps = [
        {"id": "d", "depends_on": ["b", "c"]},
        {"id": "a", "depends_on": []},
        {"id": "c", "depends_on": ["a"]},
        {"id": "b", "depends_on": ["a"]},
    ]
    # a first, then sorted([b, c]), then d
    assert _topological_order(steps) == ["a", "b", "c", "d"]


def test_topo_fanout():
    from services.agent_orchestrator import _topological_order

    steps = [
        {"id": "root", "depends_on": []},
        {"id": "z", "depends_on": ["root"]},
        {"id": "y", "depends_on": ["root"]},
        {"id": "x", "depends_on": ["root"]},
    ]
    order = _topological_order(steps)
    assert order[0] == "root"
    # The three children sort alphabetically.
    assert order[1:] == ["x", "y", "z"]


def test_topo_detects_cycle():
    from services.agent_orchestrator import _topological_order, WorkflowValidationError

    steps = [
        {"id": "a", "depends_on": ["b"]},
        {"id": "b", "depends_on": ["a"]},
    ]
    with pytest.raises(WorkflowValidationError):
        _topological_order(steps)


def test_topo_detects_self_cycle():
    from services.agent_orchestrator import _topological_order, WorkflowValidationError

    steps = [{"id": "a", "depends_on": ["a"]}]
    with pytest.raises(WorkflowValidationError):
        _topological_order(steps)


def test_topo_rejects_unknown_dep():
    from services.agent_orchestrator import _topological_order, WorkflowValidationError

    steps = [{"id": "a", "depends_on": ["ghost"]}]
    with pytest.raises(WorkflowValidationError):
        _topological_order(steps)


def test_topo_empty_list_raises():
    """Empty manifest is illegal — workflow with no steps is meaningless."""
    from services.agent_orchestrator import _topological_order, WorkflowValidationError

    with pytest.raises(WorkflowValidationError):
        _topological_order([])


def test_topo_single_step_no_deps():
    from services.agent_orchestrator import _topological_order

    assert _topological_order([{"id": "solo", "depends_on": []}]) == ["solo"]


def test_topo_rejects_duplicate_ids():
    from services.agent_orchestrator import _topological_order, WorkflowValidationError

    steps = [
        {"id": "a", "depends_on": []},
        {"id": "a", "depends_on": []},
    ]
    with pytest.raises(WorkflowValidationError):
        _topological_order(steps)


def test_topo_rejects_missing_id():
    from services.agent_orchestrator import _topological_order, WorkflowValidationError

    steps = [{"depends_on": []}]
    with pytest.raises(WorkflowValidationError):
        _topological_order(steps)


# ---------------------------------------------------------------------------
# _workflow_canonical_bytes
# ---------------------------------------------------------------------------


def test_canonical_bytes_deterministic():
    from services.agent_orchestrator import _workflow_canonical_bytes

    a = _workflow_canonical_bytes(
        run_id="r",
        workspace_id="w",
        manifest_json="{}",
        created_at_ms=1000,
    )
    b = _workflow_canonical_bytes(
        run_id="r",
        workspace_id="w",
        manifest_json="{}",
        created_at_ms=1000,
    )
    assert a == b


def test_canonical_bytes_changes_on_each_field():
    from services.agent_orchestrator import _workflow_canonical_bytes

    base = _workflow_canonical_bytes(
        run_id="r",
        workspace_id="w",
        manifest_json="{}",
        created_at_ms=1,
    )
    assert base != _workflow_canonical_bytes(
        run_id="X",
        workspace_id="w",
        manifest_json="{}",
        created_at_ms=1,
    )
    assert base != _workflow_canonical_bytes(
        run_id="r",
        workspace_id="X",
        manifest_json="{}",
        created_at_ms=1,
    )
    assert base != _workflow_canonical_bytes(
        run_id="r",
        workspace_id="w",
        manifest_json='{"x":1}',
        created_at_ms=1,
    )
    assert base != _workflow_canonical_bytes(
        run_id="r",
        workspace_id="w",
        manifest_json="{}",
        created_at_ms=2,
    )


def test_canonical_bytes_keys_sorted():
    from services.agent_orchestrator import _workflow_canonical_bytes

    out = _workflow_canonical_bytes(
        run_id="r",
        workspace_id="w",
        manifest_json="{}",
        created_at_ms=1,
    ).decode("utf-8")
    # Sorted JSON keys: created_at_ms < manifest_json < run_id < workspace_id
    keys_in_order = ["created_at_ms", "manifest_json", "run_id", "workspace_id"]
    idxs = [out.index(f'"{k}":') for k in keys_in_order]
    assert idxs == sorted(idxs)


# ---------------------------------------------------------------------------
# Sign + verify round trip
# ---------------------------------------------------------------------------


def test_sign_then_verify_workflow_row(orch_env):
    """A signed row roundtrips through verify cleanly."""
    from services.agent_orchestrator import (
        _sign_workflow_row,
        _verify_workflow_row,
    )

    sig, pub = _sign_workflow_row(
        run_id="r1",
        workspace_id="ws",
        manifest_json='{"k":1}',
        created_at_ms=1000,
    )
    if sig is None:
        pytest.skip("keyring stack wedged — signing path unavailable in this env")

    class _FakeRow:
        id = "r1"
        workspaceId = "ws"
        manifest_json = '{"k":1}'
        createdAt = 1000
        signature = sig
        signing_public_key = pub

    assert _verify_workflow_row(_FakeRow()) is True


def test_verify_workflow_row_unsigned_returns_true(orch_env):
    """Legacy NULL signature/public_key — verifier passes (backward
    compatibility for pre-P6.1 rows)."""
    from services.agent_orchestrator import _verify_workflow_row

    class _FakeRow:
        id = "r1"
        workspaceId = "ws"
        manifest_json = "{}"
        createdAt = 1000
        signature = None
        signing_public_key = None

    assert _verify_workflow_row(_FakeRow()) is True


def test_verify_workflow_row_tampered_manifest_returns_false(orch_env):
    from services.agent_orchestrator import (
        _sign_workflow_row,
        _verify_workflow_row,
    )

    sig, pub = _sign_workflow_row(
        run_id="r2",
        workspace_id="ws",
        manifest_json='{"k":1}',
        created_at_ms=1000,
    )
    if sig is None:
        pytest.skip("keyring stack wedged — signing path unavailable in this env")

    class _FakeRow:
        id = "r2"
        workspaceId = "ws"
        manifest_json = '{"k":2}'  # tampered
        createdAt = 1000
        signature = sig
        signing_public_key = pub

    assert _verify_workflow_row(_FakeRow()) is False


def test_verify_workflow_row_tampered_workspace_returns_false(orch_env):
    from services.agent_orchestrator import (
        _sign_workflow_row,
        _verify_workflow_row,
    )

    sig, pub = _sign_workflow_row(
        run_id="r3",
        workspace_id="ws-alpha",
        manifest_json="{}",
        created_at_ms=1000,
    )
    if sig is None:
        pytest.skip("keyring stack wedged — signing path unavailable in this env")

    class _FakeRow:
        id = "r3"
        workspaceId = "ws-OMEGA"  # tampered
        manifest_json = "{}"
        createdAt = 1000
        signature = sig
        signing_public_key = pub

    assert _verify_workflow_row(_FakeRow()) is False


def test_verify_workflow_row_swapped_signature_returns_false(orch_env):
    """Sig from run A used against row B → verify fails."""
    from services.agent_orchestrator import (
        _sign_workflow_row,
        _verify_workflow_row,
    )

    sig_a, pub = _sign_workflow_row(
        run_id="A",
        workspace_id="w",
        manifest_json="{}",
        created_at_ms=1,
    )
    if sig_a is None:
        pytest.skip("keyring stack wedged — signing path unavailable in this env")

    class _FakeRowB:
        id = "B"
        workspaceId = "w"
        manifest_json = "{}"
        createdAt = 1
        signature = sig_a  # wrong sig
        signing_public_key = pub

    assert _verify_workflow_row(_FakeRowB()) is False


# ---------------------------------------------------------------------------
# WorkflowTampered exception shape
# ---------------------------------------------------------------------------


def test_workflow_tampered_carries_run_id():
    from services.agent_orchestrator import WorkflowTampered

    exc = WorkflowTampered("run-xyz", reason="custom")
    assert exc.run_id == "run-xyz"
    assert exc.reason == "custom"
    assert "run-xyz" in str(exc)


def test_workflow_tampered_default_reason():
    from services.agent_orchestrator import WorkflowTampered

    exc = WorkflowTampered("run-abc")
    assert exc.reason == "signature mismatch"
