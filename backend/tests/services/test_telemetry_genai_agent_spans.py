"""
backend/tests/services/test_telemetry_genai_agent_spans.py

Sprint 16 / Item G2 — OTel GenAI agent task/action span tests.

Covers:
- Attribute-name constants exactly match the OTel semconv namespace
  prefixes (gen_ai.agent.task.*, gen_ai.agent.action.*, etc.).
- TaskKind / ActionKind / TaskStatus enum values are stable strings.
- build_task_attrs / build_action_attrs produce dicts with every
  declared attribute present.
- emit_task_span / emit_action_span validate inputs (non-empty
  task_id, ActionKind type, etc.) and return populated dataclasses.
- _coerce_status accepts enum + string (with .lower()); rejects
  invalid string / wrong type.
- emit_action_span auto-generates a UUID when action_id is omitted.
- AgentSpanBuilder fluent API:
    - with_team requires team_id; role optional.
    - with_artifact requires ref + kind; sha256 optional.
    - with_kvcache_memory requires non-negative slot + non-empty hash.
- AgentSpanBuilder.build() returns a COPY (mutating it doesn't affect
  subsequent build() calls).
- Builder + emit_task_span composition produces the full attribute
  superset on the emitted span.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AS_PATH = _REPO_ROOT / "backend" / "services" / "telemetry_genai_agent_spans.py"
_spec = importlib.util.spec_from_file_location("vos3_agent_spans_under_test", _AS_PATH)
asp = importlib.util.module_from_spec(_spec)
sys.modules["vos3_agent_spans_under_test"] = asp
_spec.loader.exec_module(asp)


# ---------------------------------------------------------------------------
# Constants — OTel namespace check
# ---------------------------------------------------------------------------


def test_task_attribute_constants_in_namespace():
    assert asp.ATTR_GEN_AI_AGENT_TASK_ID == "gen_ai.agent.task.id"
    assert asp.ATTR_GEN_AI_AGENT_TASK_KIND == "gen_ai.agent.task.kind"
    assert asp.ATTR_GEN_AI_AGENT_TASK_DESCRIPTION == "gen_ai.agent.task.description"
    assert asp.ATTR_GEN_AI_AGENT_TASK_PARENT_ID == "gen_ai.agent.task.parent_id"
    assert asp.ATTR_GEN_AI_AGENT_TASK_STATUS == "gen_ai.agent.task.status"


def test_action_attribute_constants_in_namespace():
    assert asp.ATTR_GEN_AI_AGENT_ACTION_ID == "gen_ai.agent.action.id"
    assert asp.ATTR_GEN_AI_AGENT_ACTION_KIND == "gen_ai.agent.action.kind"
    assert asp.ATTR_GEN_AI_AGENT_ACTION_TARGET == "gen_ai.agent.action.target"
    assert asp.ATTR_GEN_AI_AGENT_ACTION_TASK_ID == "gen_ai.agent.action.task_id"


def test_team_artifact_memory_constants_in_namespace():
    assert asp.ATTR_GEN_AI_AGENT_TEAM_ID == "gen_ai.agent.team.id"
    assert asp.ATTR_GEN_AI_AGENT_TEAM_ROLE == "gen_ai.agent.team.role"
    assert asp.ATTR_GEN_AI_AGENT_ARTIFACT_REF == "gen_ai.agent.artifact.ref"
    assert asp.ATTR_GEN_AI_AGENT_ARTIFACT_KIND == "gen_ai.agent.artifact.kind"
    assert asp.ATTR_GEN_AI_AGENT_ARTIFACT_SHA256 == "gen_ai.agent.artifact.sha256"
    assert (
        asp.ATTR_GEN_AI_AGENT_MEMORY_KVCACHE_BLOB_SHA256
        == "gen_ai.agent.memory.kvcache_blob_sha256"
    )
    assert asp.ATTR_GEN_AI_AGENT_MEMORY_SLOT_ID == "gen_ai.agent.memory.slot_id"


# ---------------------------------------------------------------------------
# Enum vocabulary
# ---------------------------------------------------------------------------


def test_task_kind_values():
    assert asp.TaskKind.PLAN.value == "plan"
    assert asp.TaskKind.RESEARCH.value == "research"
    assert asp.TaskKind.EXECUTE.value == "execute"
    assert asp.TaskKind.SUMMARIZE.value == "summarize"


def test_action_kind_values():
    assert asp.ActionKind.TOOL_CALL.value == "tool_call"
    assert asp.ActionKind.LLM_QUERY.value == "llm_query"
    assert asp.ActionKind.API_REQUEST.value == "api_request"
    assert asp.ActionKind.VECTOR_DB_QUERY.value == "vector_db_query"
    assert asp.ActionKind.MEMORY_READ.value == "memory_read"
    assert asp.ActionKind.MEMORY_WRITE.value == "memory_write"


def test_task_status_values():
    assert asp.TaskStatus.PENDING.value == "pending"
    assert asp.TaskStatus.IN_PROGRESS.value == "in_progress"
    assert asp.TaskStatus.COMPLETED.value == "completed"
    assert asp.TaskStatus.FAILED.value == "failed"
    assert asp.TaskStatus.CANCELLED.value == "cancelled"


# ---------------------------------------------------------------------------
# build_task_attrs / build_action_attrs
# ---------------------------------------------------------------------------


def test_build_task_attrs_minimal():
    data = asp.TaskSpanData(
        task_id="t1",
        kind=asp.TaskKind.PLAN,
        agent_id="agent-A",
    )
    attrs = asp.build_task_attrs(data)
    assert attrs[asp.ATTR_GEN_AI_AGENT_TASK_ID] == "t1"
    assert attrs[asp.ATTR_GEN_AI_AGENT_TASK_KIND] == "plan"
    assert attrs[asp.ATTR_GEN_AI_AGENT_ID] == "agent-A"
    assert attrs[asp.ATTR_GEN_AI_AGENT_TASK_STATUS] == "in_progress"


def test_build_task_attrs_with_optional_fields():
    data = asp.TaskSpanData(
        task_id="t2",
        kind=asp.TaskKind.EXECUTE,
        agent_id="agent-B",
        parent_task_id="t1",
        description="hello",
        status=asp.TaskStatus.COMPLETED,
        extra_attrs={"custom.x": "y"},
    )
    attrs = asp.build_task_attrs(data)
    assert attrs[asp.ATTR_GEN_AI_AGENT_TASK_PARENT_ID] == "t1"
    assert attrs[asp.ATTR_GEN_AI_AGENT_TASK_DESCRIPTION] == "hello"
    assert attrs[asp.ATTR_GEN_AI_AGENT_TASK_STATUS] == "completed"
    assert attrs["custom.x"] == "y"


def test_build_action_attrs_minimal():
    data = asp.ActionSpanData(
        action_id="a1",
        kind=asp.ActionKind.TOOL_CALL,
        task_id="t1",
        agent_id="agent-A",
    )
    attrs = asp.build_action_attrs(data)
    assert attrs[asp.ATTR_GEN_AI_AGENT_ACTION_ID] == "a1"
    assert attrs[asp.ATTR_GEN_AI_AGENT_ACTION_KIND] == "tool_call"
    assert attrs[asp.ATTR_GEN_AI_AGENT_ACTION_TASK_ID] == "t1"


def test_build_action_attrs_with_target():
    data = asp.ActionSpanData(
        action_id="a2",
        kind=asp.ActionKind.API_REQUEST,
        task_id="t1",
        agent_id="agent-A",
        target="https://api.example.com",
    )
    attrs = asp.build_action_attrs(data)
    assert attrs[asp.ATTR_GEN_AI_AGENT_ACTION_TARGET] == "https://api.example.com"


# ---------------------------------------------------------------------------
# emit_task_span / emit_action_span
# ---------------------------------------------------------------------------


def test_emit_task_span_validation():
    with pytest.raises(ValueError):
        asp.emit_task_span(task_id="", kind=asp.TaskKind.PLAN, agent_id="A")
    with pytest.raises(TypeError):
        asp.emit_task_span(task_id="t", kind="plan", agent_id="A")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        asp.emit_task_span(task_id="t", kind=asp.TaskKind.PLAN, agent_id="")


def test_emit_task_span_accepts_status_string():
    data = asp.emit_task_span(
        task_id="t", kind=asp.TaskKind.PLAN, agent_id="A", status="completed"
    )
    assert data.status == asp.TaskStatus.COMPLETED


def test_emit_task_span_rejects_invalid_status_string():
    with pytest.raises(ValueError):
        asp.emit_task_span(
            task_id="t",
            kind=asp.TaskKind.PLAN,
            agent_id="A",
            status="not-a-real-status",
        )


def test_emit_task_span_rejects_wrong_status_type():
    with pytest.raises(TypeError):
        asp.emit_task_span(
            task_id="t", kind=asp.TaskKind.PLAN, agent_id="A", status=42
        )  # type: ignore[arg-type]


def test_emit_action_span_auto_generates_id():
    data = asp.emit_action_span(
        kind=asp.ActionKind.TOOL_CALL, task_id="t1", agent_id="A"
    )
    assert data.action_id
    # Valid UUID.
    uuid.UUID(data.action_id)


def test_emit_action_span_validation():
    with pytest.raises(TypeError):
        asp.emit_action_span(kind="tool_call", task_id="t", agent_id="A")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        asp.emit_action_span(kind=asp.ActionKind.TOOL_CALL, task_id="", agent_id="A")
    with pytest.raises(ValueError):
        asp.emit_action_span(kind=asp.ActionKind.TOOL_CALL, task_id="t", agent_id="")


# ---------------------------------------------------------------------------
# AgentSpanBuilder
# ---------------------------------------------------------------------------


def test_builder_with_team():
    b = asp.AgentSpanBuilder().with_team("team-X", role="planner")
    attrs = b.build()
    assert attrs[asp.ATTR_GEN_AI_AGENT_TEAM_ID] == "team-X"
    assert attrs[asp.ATTR_GEN_AI_AGENT_TEAM_ROLE] == "planner"


def test_builder_with_team_rejects_empty_id():
    with pytest.raises(ValueError):
        asp.AgentSpanBuilder().with_team("")


def test_builder_with_artifact_minimal():
    b = asp.AgentSpanBuilder().with_artifact("ref/doc-1", kind="document")
    attrs = b.build()
    assert attrs[asp.ATTR_GEN_AI_AGENT_ARTIFACT_REF] == "ref/doc-1"
    assert attrs[asp.ATTR_GEN_AI_AGENT_ARTIFACT_KIND] == "document"
    assert asp.ATTR_GEN_AI_AGENT_ARTIFACT_SHA256 not in attrs


def test_builder_with_artifact_with_sha256():
    b = asp.AgentSpanBuilder().with_artifact(
        "ref/doc-2", kind="document", sha256="abc123" * 10
    )
    attrs = b.build()
    assert attrs[asp.ATTR_GEN_AI_AGENT_ARTIFACT_SHA256] == "abc123" * 10


def test_builder_with_kvcache_memory():
    b = asp.AgentSpanBuilder().with_kvcache_memory(slot_id=3, blob_sha256="hash-xyz")
    attrs = b.build()
    assert attrs[asp.ATTR_GEN_AI_AGENT_MEMORY_SLOT_ID] == 3
    assert attrs[asp.ATTR_GEN_AI_AGENT_MEMORY_KVCACHE_BLOB_SHA256] == "hash-xyz"


def test_builder_with_kvcache_memory_rejects_invalid():
    with pytest.raises(ValueError):
        asp.AgentSpanBuilder().with_kvcache_memory(slot_id=-1, blob_sha256="h")
    with pytest.raises(ValueError):
        asp.AgentSpanBuilder().with_kvcache_memory(slot_id=0, blob_sha256="")


def test_builder_build_returns_copy():
    b = asp.AgentSpanBuilder().with_team("team-X")
    out1 = b.build()
    out1["custom.x"] = "leak"
    out2 = b.build()
    assert "custom.x" not in out2


def test_builder_chains():
    """Single chain produces composite attrs across team + artifact + memory."""
    attrs = (
        asp.AgentSpanBuilder()
        .with_team("team-X", role="planner")
        .with_artifact("ref/doc-1", kind="document", sha256="hash")
        .with_kvcache_memory(slot_id=2, blob_sha256="kvc-hash")
        .build()
    )
    assert attrs[asp.ATTR_GEN_AI_AGENT_TEAM_ID] == "team-X"
    assert attrs[asp.ATTR_GEN_AI_AGENT_ARTIFACT_REF] == "ref/doc-1"
    assert attrs[asp.ATTR_GEN_AI_AGENT_MEMORY_SLOT_ID] == 2


def test_emit_with_builder_overlay():
    """Builder attrs flow through emit_task_span's `attrs` argument."""
    overlay = asp.AgentSpanBuilder().with_team("team-X").build()
    data = asp.emit_task_span(
        task_id="t", kind=asp.TaskKind.PLAN, agent_id="A", attrs=overlay
    )
    full = asp.build_task_attrs(data)
    assert full[asp.ATTR_GEN_AI_AGENT_TEAM_ID] == "team-X"
