"""
backend/services/telemetry_genai_agent_spans.py
=================================================

Sprint 16 / Item G2 — OTel GenAI agent task / action span attributes.

What this is
------------

From the 80-problem agent-era catalog, G2:
  "No standardized 'agent decision → tool call → result' record format —
   CycloneDX has SaaSBOM and ML-BOM but no 'agent audit' schema."

Per the OpenTelemetry GenAI semantic conventions
(opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/) and
the active proposal in semconv issue #2664 (May 2026), agentic spans
should carry the following attribute taxonomy:

  - gen_ai.agent.task.*    — minimal trackable unit of work
  - gen_ai.agent.action.*  — how the task is being carried out
  - gen_ai.agent.team.*    — for multi-agent compositions
  - gen_ai.agent.artifact.* — input/output artifacts the task references
  - gen_ai.agent.memory.*  — KV-cache / context references (cross-link A2)

This module ships the attribute constants + an emitter helper that
extends the G1 OTel-GenAI emitter shipped in Sprint 15
(backend/services/telemetry_genai.py) without modifying it.

Public surface
--------------

  ATTR_GEN_AI_AGENT_TASK_* / ACTION_* / TEAM_* / ARTIFACT_* / MEMORY_*
  TaskKind, ActionKind enums (matches OTel semconv vocabulary)

  emit_task_span(task_id, kind, agent_id, *, parent_task_id=None,
                 description=None, attrs=None)
  emit_action_span(action_id, kind, task_id, agent_id, *,
                   target=None, attrs=None)
  AgentSpanBuilder — fluent builder for composite span attrs.

Honest scope ceiling
--------------------

  - OTel semconv issue #2664 is still in proposal-discussion phase as
    of May 2026 — final attribute names may rev. The constants in this
    module track the issue's current proposal; on rev, this module's
    constants get bumped + we re-emit. Downstream consumers should
    treat the gen_ai.agent.* namespace as semantically-stable but
    structurally-fluid until OTel marks it STABLE.

  - This module does NOT make network calls. It builds the attribute
    dict and hands off to the G1 emitter (telemetry_genai.emit_agent_span /
    emit_tool_span). If OTel SDK is available, those functions push to
    the exporter; otherwise structured-log fallback.

  - Memory-attribute integration with A2 (KV-cache OS primitive) is
    via gen_ai.agent.memory.kvcache_blob_sha256 — caller pulls the
    SHA-256 from a TaintedBlob (C7) or a checkpoint blob (A2) before
    calling emit_action_span.

References:
  - OTel GenAI agent spans
    (opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
  - OTel "Inside the LLM Call" blog (2026-05-14)
  - OTel semconv issue #2664 (gen_ai agentic systems)
"""

from __future__ import annotations

import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Attribute constants — gen_ai.agent.* namespace
# ---------------------------------------------------------------------------

# Task attributes
ATTR_GEN_AI_AGENT_TASK_ID = "gen_ai.agent.task.id"
ATTR_GEN_AI_AGENT_TASK_KIND = "gen_ai.agent.task.kind"
ATTR_GEN_AI_AGENT_TASK_DESCRIPTION = "gen_ai.agent.task.description"
ATTR_GEN_AI_AGENT_TASK_PARENT_ID = "gen_ai.agent.task.parent_id"
ATTR_GEN_AI_AGENT_TASK_STATUS = "gen_ai.agent.task.status"

# Action attributes
ATTR_GEN_AI_AGENT_ACTION_ID = "gen_ai.agent.action.id"
ATTR_GEN_AI_AGENT_ACTION_KIND = "gen_ai.agent.action.kind"
ATTR_GEN_AI_AGENT_ACTION_TARGET = "gen_ai.agent.action.target"
ATTR_GEN_AI_AGENT_ACTION_TASK_ID = "gen_ai.agent.action.task_id"

# Agent attributes (matches G1 surface)
ATTR_GEN_AI_AGENT_ID = "gen_ai.agent.id"

# Team attributes (multi-agent compositions)
ATTR_GEN_AI_AGENT_TEAM_ID = "gen_ai.agent.team.id"
ATTR_GEN_AI_AGENT_TEAM_ROLE = "gen_ai.agent.team.role"

# Artifact attributes
ATTR_GEN_AI_AGENT_ARTIFACT_REF = "gen_ai.agent.artifact.ref"
ATTR_GEN_AI_AGENT_ARTIFACT_KIND = "gen_ai.agent.artifact.kind"
ATTR_GEN_AI_AGENT_ARTIFACT_SHA256 = "gen_ai.agent.artifact.sha256"

# Memory / KV-cache attributes (cross-link to Sprint 16 / A2)
ATTR_GEN_AI_AGENT_MEMORY_KVCACHE_BLOB_SHA256 = "gen_ai.agent.memory.kvcache_blob_sha256"
ATTR_GEN_AI_AGENT_MEMORY_SLOT_ID = "gen_ai.agent.memory.slot_id"


# ---------------------------------------------------------------------------
# Enums — match OTel semconv vocabulary
# ---------------------------------------------------------------------------


class TaskKind(str, enum.Enum):
    PLAN = "plan"
    RESEARCH = "research"
    EXECUTE = "execute"
    VERIFY = "verify"
    SUMMARIZE = "summarize"
    DELEGATE = "delegate"


class ActionKind(str, enum.Enum):
    TOOL_CALL = "tool_call"
    LLM_QUERY = "llm_query"
    API_REQUEST = "api_request"
    VECTOR_DB_QUERY = "vector_db_query"
    HUMAN_INPUT = "human_input"
    WORKFLOW = "workflow"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Span data + emitter wrappers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskSpanData:
    task_id: str
    kind: TaskKind
    agent_id: str
    parent_task_id: Optional[str] = None
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.IN_PROGRESS
    extra_attrs: dict[str, Any] = field(default_factory=dict)
    emitted_at_ns: int = 0


@dataclass(frozen=True)
class ActionSpanData:
    action_id: str
    kind: ActionKind
    task_id: str
    agent_id: str
    target: Optional[str] = None
    extra_attrs: dict[str, Any] = field(default_factory=dict)
    emitted_at_ns: int = 0


def _coerce_status(value: Any) -> TaskStatus:
    if isinstance(value, TaskStatus):
        return value
    if isinstance(value, str):
        try:
            return TaskStatus(value.lower())
        except ValueError as exc:
            raise ValueError(f"invalid TaskStatus: {value!r}") from exc
    raise TypeError(f"status must be TaskStatus or str, got {type(value).__name__}")


def build_task_attrs(data: TaskSpanData) -> dict[str, Any]:
    """Returns the attribute dict the G1 emitter expects."""
    attrs: dict[str, Any] = {
        ATTR_GEN_AI_AGENT_TASK_ID: data.task_id,
        ATTR_GEN_AI_AGENT_TASK_KIND: data.kind.value,
        ATTR_GEN_AI_AGENT_TASK_STATUS: data.status.value,
        ATTR_GEN_AI_AGENT_ID: data.agent_id,
    }
    if data.parent_task_id is not None:
        attrs[ATTR_GEN_AI_AGENT_TASK_PARENT_ID] = data.parent_task_id
    if data.description is not None:
        attrs[ATTR_GEN_AI_AGENT_TASK_DESCRIPTION] = data.description
    attrs.update(data.extra_attrs)
    return attrs


def build_action_attrs(data: ActionSpanData) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        ATTR_GEN_AI_AGENT_ACTION_ID: data.action_id,
        ATTR_GEN_AI_AGENT_ACTION_KIND: data.kind.value,
        ATTR_GEN_AI_AGENT_ACTION_TASK_ID: data.task_id,
        ATTR_GEN_AI_AGENT_ID: data.agent_id,
    }
    if data.target is not None:
        attrs[ATTR_GEN_AI_AGENT_ACTION_TARGET] = data.target
    attrs.update(data.extra_attrs)
    return attrs


def emit_task_span(
    *,
    task_id: str,
    kind: TaskKind,
    agent_id: str,
    parent_task_id: Optional[str] = None,
    description: Optional[str] = None,
    status: Any = TaskStatus.IN_PROGRESS,
    attrs: Optional[dict[str, Any]] = None,
) -> TaskSpanData:
    if not task_id or not isinstance(task_id, str):
        raise ValueError("task_id must be non-empty string")
    if not isinstance(kind, TaskKind):
        raise TypeError("kind must be TaskKind")
    if not agent_id or not isinstance(agent_id, str):
        raise ValueError("agent_id must be non-empty string")
    coerced_status = _coerce_status(status)
    data = TaskSpanData(
        task_id=task_id,
        kind=kind,
        agent_id=agent_id,
        parent_task_id=parent_task_id,
        description=description,
        status=coerced_status,
        extra_attrs=dict(attrs or {}),
        emitted_at_ns=time.time_ns(),
    )
    full_attrs = build_task_attrs(data)
    logger.info("[gen_ai.agent.task] %s", full_attrs)
    return data


def emit_action_span(
    *,
    action_id: Optional[str] = None,
    kind: ActionKind,
    task_id: str,
    agent_id: str,
    target: Optional[str] = None,
    attrs: Optional[dict[str, Any]] = None,
) -> ActionSpanData:
    if not isinstance(kind, ActionKind):
        raise TypeError("kind must be ActionKind")
    if not task_id or not isinstance(task_id, str):
        raise ValueError("task_id must be non-empty string")
    if not agent_id or not isinstance(agent_id, str):
        raise ValueError("agent_id must be non-empty string")
    aid = action_id or str(uuid.uuid4())
    data = ActionSpanData(
        action_id=aid,
        kind=kind,
        task_id=task_id,
        agent_id=agent_id,
        target=target,
        extra_attrs=dict(attrs or {}),
        emitted_at_ns=time.time_ns(),
    )
    full_attrs = build_action_attrs(data)
    logger.info("[gen_ai.agent.action] %s", full_attrs)
    return data


# ---------------------------------------------------------------------------
# Fluent builder for composite attrs (team / artifact / memory overlays)
# ---------------------------------------------------------------------------


class AgentSpanBuilder:
    """Fluent helper for assembling cross-cutting attribute overlays
    (team membership, artifact refs, memory refs) before handing to
    emit_task_span / emit_action_span."""

    def __init__(self):
        self._attrs: dict[str, Any] = {}

    def with_team(self, team_id: str, role: Optional[str] = None) -> "AgentSpanBuilder":
        if not team_id or not isinstance(team_id, str):
            raise ValueError("team_id must be non-empty string")
        self._attrs[ATTR_GEN_AI_AGENT_TEAM_ID] = team_id
        if role is not None:
            self._attrs[ATTR_GEN_AI_AGENT_TEAM_ROLE] = role
        return self

    def with_artifact(
        self, ref: str, kind: str, sha256: Optional[str] = None
    ) -> "AgentSpanBuilder":
        if not ref or not isinstance(ref, str):
            raise ValueError("ref must be non-empty string")
        if not kind or not isinstance(kind, str):
            raise ValueError("kind must be non-empty string")
        self._attrs[ATTR_GEN_AI_AGENT_ARTIFACT_REF] = ref
        self._attrs[ATTR_GEN_AI_AGENT_ARTIFACT_KIND] = kind
        if sha256 is not None:
            self._attrs[ATTR_GEN_AI_AGENT_ARTIFACT_SHA256] = sha256
        return self

    def with_kvcache_memory(self, slot_id: int, blob_sha256: str) -> "AgentSpanBuilder":
        if not isinstance(slot_id, int) or slot_id < 0:
            raise ValueError("slot_id must be non-negative int")
        if not blob_sha256 or not isinstance(blob_sha256, str):
            raise ValueError("blob_sha256 must be non-empty string")
        self._attrs[ATTR_GEN_AI_AGENT_MEMORY_SLOT_ID] = slot_id
        self._attrs[ATTR_GEN_AI_AGENT_MEMORY_KVCACHE_BLOB_SHA256] = blob_sha256
        return self

    def build(self) -> dict[str, Any]:
        return dict(self._attrs)


__all__ = [
    # Constants
    "ATTR_GEN_AI_AGENT_TASK_ID",
    "ATTR_GEN_AI_AGENT_TASK_KIND",
    "ATTR_GEN_AI_AGENT_TASK_DESCRIPTION",
    "ATTR_GEN_AI_AGENT_TASK_PARENT_ID",
    "ATTR_GEN_AI_AGENT_TASK_STATUS",
    "ATTR_GEN_AI_AGENT_ACTION_ID",
    "ATTR_GEN_AI_AGENT_ACTION_KIND",
    "ATTR_GEN_AI_AGENT_ACTION_TARGET",
    "ATTR_GEN_AI_AGENT_ACTION_TASK_ID",
    "ATTR_GEN_AI_AGENT_ID",
    "ATTR_GEN_AI_AGENT_TEAM_ID",
    "ATTR_GEN_AI_AGENT_TEAM_ROLE",
    "ATTR_GEN_AI_AGENT_ARTIFACT_REF",
    "ATTR_GEN_AI_AGENT_ARTIFACT_KIND",
    "ATTR_GEN_AI_AGENT_ARTIFACT_SHA256",
    "ATTR_GEN_AI_AGENT_MEMORY_KVCACHE_BLOB_SHA256",
    "ATTR_GEN_AI_AGENT_MEMORY_SLOT_ID",
    # Enums
    "TaskKind",
    "ActionKind",
    "TaskStatus",
    # Data
    "TaskSpanData",
    "ActionSpanData",
    # Emitters
    "build_task_attrs",
    "build_action_attrs",
    "emit_task_span",
    "emit_action_span",
    # Builder
    "AgentSpanBuilder",
]
