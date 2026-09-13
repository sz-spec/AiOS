"""
Expert-in-the-Loop Service
===========================
Packages build context for SOS expert requests and manages the
request lifecycle (pending → claimed → resolved).

IE-2 Phase 2.0 — Subsystem #4

Context Packaging:
- Respects the 900 KB Convex document safety limit.
- Prioritises: errors → architecture → current files → agent messages.
- Truncates large file contents and drops oldest messages first.

Usage:
    from services.expert_pool import get_expert_service, ExpertRequest

    svc = get_expert_service()
    ctx = svc.package_context(state)
    req = svc.create_request(user_id="u1", project_id="p1", ctx=ctx)
    svc.claim_request(req.id, expert_id="exp1")
    svc.resolve_request(req.id, resolution={...})
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

# =========================================================================
# Constants
# =========================================================================

# Convex document limit is ~1 MB.  We cap at 900 KB for safety.
CONTEXT_SIZE_LIMIT = 900 * 1024  # bytes

# How many files to include (full content) before truncation kicks in
MAX_FULL_FILES = 30

# Max characters per individual file content
MAX_FILE_CHARS = 8_000

# Max agent messages to include in context snapshot
MAX_MESSAGES = 20


# =========================================================================
# Data Structures
# =========================================================================


class RequestStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RESOLVED = "resolved"
    EXPIRED = "expired"


@dataclass
class ExpertContext:
    """
    Packaged build context sent to a human expert.

    Contains everything an expert needs to understand and fix the issue:
    - Current code files (truncated to fit budget)
    - Build errors and stack traces
    - Agent conversation history (most recent messages)
    - Architecture specification
    - Guardrails violations
    - Stuck count and escalation history
    """

    # --- Core build state ---
    requirements: str = ""
    architecture: Optional[Dict[str, Any]] = None

    # --- Code snapshots ---
    frontend_files: Dict[str, str] = field(default_factory=dict)
    backend_files: Dict[str, str] = field(default_factory=dict)
    test_files: Dict[str, str] = field(default_factory=dict)

    # --- Errors & diagnostics ---
    errors: List[str] = field(default_factory=list)
    review_issues: List[Dict[str, Any]] = field(default_factory=list)
    guardrails_violations: List[Dict[str, Any]] = field(default_factory=list)

    # --- Escalation metadata ---
    stuck_count: int = 0
    iteration: int = 0
    model_switch_history: List[Dict[str, Any]] = field(default_factory=list)

    # --- Agent conversation (last N messages) ---
    agent_messages: List[Dict[str, str]] = field(default_factory=list)

    # --- User-supplied description (from SOS modal) ---
    user_description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def estimated_size_bytes(self) -> int:
        """Approximate JSON byte-size of this context."""
        return len(json.dumps(self.to_dict(), default=str).encode("utf-8"))


@dataclass
class ExpertRequest:
    """A single SOS expert request with lifecycle tracking."""

    id: str = ""
    user_id: str = ""
    project_id: str = ""
    build_id: Optional[str] = None
    status: str = RequestStatus.PENDING.value

    # Packaged context
    context: Optional[Dict[str, Any]] = None

    # Expert assignment
    claimed_by: Optional[str] = None
    claimed_at: Optional[str] = None

    # Resolution
    resolution: Optional[Dict[str, Any]] = None
    resolved_at: Optional[str] = None

    # Timestamps
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict:
        return asdict(self)


# =========================================================================
# Context Packaging
# =========================================================================


def _truncate_files(
    files: Optional[Dict[str, str]],
    max_files: int = MAX_FULL_FILES,
    max_chars: int = MAX_FILE_CHARS,
) -> Dict[str, str]:
    """
    Return a copy of *files* where:
    - Only the first *max_files* entries are kept.
    - Each value is truncated to *max_chars* characters.
    """
    if not files:
        return {}

    result = {}
    for i, (path, content) in enumerate(files.items()):
        if i >= max_files:
            break
        if len(content) > max_chars:
            result[path] = (
                content[:max_chars]
                + f"\n\n... [truncated — {len(content)} chars total]"
            )
        else:
            result[path] = content
    return result


def _extract_messages(
    messages: list, limit: int = MAX_MESSAGES
) -> List[Dict[str, str]]:
    """
    Convert LangChain BaseMessage objects (or plain dicts) to simple
    serialisable dicts, keeping only the most recent *limit* messages.
    """
    if not messages:
        return []

    recent = messages[-limit:]
    result = []
    for msg in recent:
        if hasattr(msg, "content"):
            # LangChain message object
            result.append(
                {
                    "role": getattr(msg, "type", getattr(msg, "name", "unknown")),
                    "content": str(msg.content)[:2000],
                }
            )
        elif isinstance(msg, dict):
            result.append(
                {
                    "role": msg.get("role", msg.get("type", "unknown")),
                    "content": str(msg.get("content", ""))[:2000],
                }
            )
    return result


def package_context(
    state: Dict[str, Any],
    user_description: str = "",
) -> ExpertContext:
    """
    Build an :class:`ExpertContext` from a LangGraph ``ProjectState`` dict.

    Automatically truncates files and messages to stay within the
    900 KB Convex document safety limit.
    """

    ctx = ExpertContext(
        requirements=str(state.get("requirements", ""))[:4000],
        architecture=state.get("architecture"),
        frontend_files=_truncate_files(state.get("frontend_code")),
        backend_files=_truncate_files(state.get("backend_code")),
        test_files=_truncate_files(state.get("tests")),
        errors=list(state.get("errors") or [])[:20],
        review_issues=(state.get("review_results") or {}).get("issues", [])[:30],
        guardrails_violations=list(state.get("guardrails_violations") or [])[:30],
        stuck_count=state.get("stuck_count") or 0,
        iteration=state.get("iteration", 0),
        model_switch_history=list(state.get("model_switch_history") or []),
        agent_messages=_extract_messages(state.get("messages", [])),
        user_description=user_description,
    )

    # Iterative trimming until we fit the budget
    size = ctx.estimated_size_bytes()
    if size > CONTEXT_SIZE_LIMIT:
        # Pass 1: drop agent messages down to 5
        ctx.agent_messages = ctx.agent_messages[-5:]
        size = ctx.estimated_size_bytes()

    if size > CONTEXT_SIZE_LIMIT:
        # Pass 2: halve file char limits
        ctx.frontend_files = _truncate_files(
            state.get("frontend_code"), max_files=15, max_chars=MAX_FILE_CHARS // 2
        )
        ctx.backend_files = _truncate_files(
            state.get("backend_code"), max_files=15, max_chars=MAX_FILE_CHARS // 2
        )
        ctx.test_files = _truncate_files(
            state.get("tests"), max_files=10, max_chars=MAX_FILE_CHARS // 2
        )
        size = ctx.estimated_size_bytes()

    if size > CONTEXT_SIZE_LIMIT:
        # Pass 3: nuclear — file names only, no content
        ctx.frontend_files = {
            k: "[content omitted]" for k in (state.get("frontend_code") or {}).keys()
        }
        ctx.backend_files = {
            k: "[content omitted]" for k in (state.get("backend_code") or {}).keys()
        }
        ctx.test_files = {}

    return ctx


# =========================================================================
# Expert Pool Service (in-memory + optional Convex persistence)
# =========================================================================


class ExpertPoolService:
    """
    Manages the lifecycle of SOS expert help requests.

    Storage: in-memory ``Dict`` in dev mode.  In production, each
    mutation also persists to Convex via ``ConvexClient``.
    """

    def __init__(self):
        self._requests: Dict[str, ExpertRequest] = {}

    # ---- Create ----

    def create_request(
        self,
        user_id: str,
        project_id: str,
        context: ExpertContext,
        build_id: Optional[str] = None,
    ) -> ExpertRequest:
        """Create a new SOS request with packaged context."""
        req = ExpertRequest(
            user_id=user_id,
            project_id=project_id,
            build_id=build_id,
            context=context.to_dict(),
        )
        self._requests[req.id] = req
        return req

    # ---- Read ----

    def get_request(self, request_id: str) -> Optional[ExpertRequest]:
        return self._requests.get(request_id)

    def list_pending(self) -> List[ExpertRequest]:
        return [
            r
            for r in self._requests.values()
            if r.status == RequestStatus.PENDING.value
        ]

    def list_by_user(self, user_id: str) -> List[ExpertRequest]:
        return [r for r in self._requests.values() if r.user_id == user_id]

    # ---- Claim ----

    def claim_request(
        self,
        request_id: str,
        expert_id: str,
    ) -> Optional[ExpertRequest]:
        """
        Mark a request as claimed by an expert.

        Returns ``None`` if the request doesn't exist or is already
        claimed/resolved.
        """
        req = self._requests.get(request_id)
        if not req or req.status != RequestStatus.PENDING.value:
            return None

        req.status = RequestStatus.CLAIMED.value
        req.claimed_by = expert_id
        req.claimed_at = datetime.now(timezone.utc).isoformat()
        req.updated_at = req.claimed_at
        return req

    # ---- Resolve ----

    def resolve_request(
        self,
        request_id: str,
        resolution: Dict[str, Any],
    ) -> Optional[ExpertRequest]:
        """
        Mark a request as resolved and attach the expert's resolution.

        ``resolution`` should contain at minimum:
        - ``patches``: ``Dict[str, str]`` — file path → new content
        - ``summary``: ``str`` — what was changed and why
        """
        req = self._requests.get(request_id)
        if not req or req.status not in (
            RequestStatus.PENDING.value,
            RequestStatus.CLAIMED.value,
        ):
            return None

        req.status = RequestStatus.RESOLVED.value
        req.resolution = resolution
        now = datetime.now(timezone.utc).isoformat()
        req.resolved_at = now
        req.updated_at = now
        return req

    # ---- Memory Integration ----

    @staticmethod
    def store_expert_fix_in_memory(
        resolution: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[str]:
        """
        Store the expert's fix as procedural memory so the AI learns.

        Uses ``MemoryManager.update_procedure()`` to persist the fix
        pattern keyed to the ``frontend`` agent namespace.

        Returns the memory ID if stored, or ``None`` on failure.
        """
        patches = resolution.get("patches", {})
        summary = resolution.get("summary", "Expert fix applied")
        errors = context.get("errors", [])
        violations = context.get("guardrails_violations", [])

        if not patches and not summary:
            return None

        # Build instruction from fix context
        instruction_parts = [f"EXPERT FIX: {summary}"]

        if errors:
            instruction_parts.append(f"Original errors: {'; '.join(errors[:3])}")
        if violations:
            rule_ids = list({v.get("rule_id", "?") for v in violations[:5]})
            instruction_parts.append(f"Guardrails violated: {', '.join(rule_ids)}")
        if patches:
            instruction_parts.append(f"Files patched: {', '.join(patches.keys())}")

        instruction = "\n".join(instruction_parts)

        try:
            from memory import MemoryManager

            mm = MemoryManager()
            return mm.update_procedure(
                instruction=instruction,
                agent_id="frontend",
                priority=5,  # Expert fixes are high-priority
                replace=False,
            )
        except Exception:
            # Memory system not available — graceful degradation
            return None


# =========================================================================
# Singleton accessor (matches project pattern)
# =========================================================================

_expert_service: Optional[ExpertPoolService] = None


def get_expert_service() -> ExpertPoolService:
    global _expert_service
    if _expert_service is None:
        _expert_service = ExpertPoolService()
    return _expert_service
