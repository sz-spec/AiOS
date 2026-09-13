"""
Prompt History Service — Convex-backed persistence.

Manages AI prompt history with Convex as the storage backend.
Maintains backward-compatible API with PromptEntry dataclass.

On restart, all prompt history is recovered from Convex.
Local cache provides fast reads within the same process lifetime.
"""

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4
from collections import defaultdict

logger = logging.getLogger(__name__)


class PromptStatus(str, Enum):
    """Prompt execution status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PromptType(str, Enum):
    """Types of prompts."""

    CODE_GENERATION = "code_generation"
    CODE_EDIT = "code_edit"
    CODE_REVIEW = "code_review"
    CHAT = "chat"
    DOCUMENTATION = "documentation"
    DEBUGGING = "debugging"
    REFACTORING = "refactoring"
    TESTING = "testing"


@dataclass
class PromptEntry:
    """A single prompt entry."""

    id: str = field(default_factory=lambda: f"prompt_{uuid4().hex[:12]}")

    # User & Project
    user_id: str = ""
    project_id: Optional[str] = None
    session_id: Optional[str] = None

    # Prompt content
    prompt: str = ""
    system_prompt: Optional[str] = None
    context: dict = field(default_factory=dict)

    # Response
    response: Optional[str] = None
    generated_code: Optional[str] = None
    generated_files: list[dict] = field(default_factory=list)

    # Metadata
    prompt_type: PromptType = PromptType.CHAT
    status: PromptStatus = PromptStatus.PENDING
    model: str = ""

    # Tokens
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # Timing
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: int = 0

    # Error info
    error: Optional[str] = None

    # Tags for organization
    tags: list[str] = field(default_factory=list)
    is_favorite: bool = False

    # Internal: Convex document ID (for update/delete operations)
    _doc_id: Optional[str] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            **{k: v for k, v in asdict(self).items() if not k.startswith("_")},
            "prompt_type": self.prompt_type.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PromptEntry":
        """Create from dictionary."""
        if "prompt_type" in data and isinstance(data["prompt_type"], str):
            data["prompt_type"] = PromptType(data["prompt_type"])
        if "status" in data and isinstance(data["status"], str):
            data["status"] = PromptStatus(data["status"])
        for field_name in ["created_at", "started_at", "completed_at"]:
            if field_name in data and isinstance(data[field_name], str):
                data[field_name] = datetime.fromisoformat(data[field_name])
        return cls(
            **{
                k: v
                for k, v in data.items()
                if k in cls.__dataclass_fields__ and not k.startswith("_")
            }
        )

    @classmethod
    def from_convex_doc(cls, doc: dict) -> "PromptEntry":
        """Create from a Convex document."""
        created_ts = doc.get("createdAt", 0)
        created_dt = (
            datetime.fromtimestamp(created_ts / 1000, tz=timezone.utc)
            if created_ts
            else datetime.now(timezone.utc)
        )
        entry = cls(
            id=doc.get("promptId", ""),
            user_id=doc.get("userId", ""),
            project_id=doc.get("projectId"),
            session_id=doc.get("sessionId"),
            prompt=doc.get("prompt", ""),
            system_prompt=doc.get("systemPrompt"),
            context=doc.get("context") or {},
            response=doc.get("response"),
            generated_code=doc.get("generatedCode"),
            generated_files=doc.get("generatedFiles") or [],
            prompt_type=PromptType(doc.get("promptType", "chat")),
            status=PromptStatus(doc.get("status", "pending")),
            model=doc.get("model", ""),
            prompt_tokens=doc.get("promptTokens", 0),
            completion_tokens=doc.get("completionTokens", 0),
            total_tokens=doc.get("totalTokens", 0),
            duration_ms=doc.get("durationMs", 0),
            error=doc.get("error"),
            tags=doc.get("tags", []),
            is_favorite=doc.get("isFavorite", False),
            created_at=created_dt,
        )
        entry._doc_id = doc.get("_id")
        return entry


@dataclass
class PromptStats:
    """Statistics for prompts."""

    total_prompts: int = 0
    total_tokens: int = 0
    avg_tokens_per_prompt: float = 0.0
    avg_duration_ms: float = 0.0
    success_rate: float = 0.0

    by_type: dict = field(default_factory=dict)
    by_model: dict = field(default_factory=dict)
    by_day: dict = field(default_factory=dict)

    most_used_tags: list[tuple[str, int]] = field(default_factory=list)
    recent_prompts: list[PromptEntry] = field(default_factory=list)


def _get_repo():
    """Lazy-init Convex prompt history repository."""
    from core.repositories.convex import ConvexPromptHistoryRepository

    return ConvexPromptHistoryRepository()


class PromptHistoryService:
    """Service for managing prompt history with Convex persistence."""

    def __init__(self):
        # Local cache: prompt_id -> PromptEntry
        self._cache: dict[str, PromptEntry] = {}
        # Index caches for fast lookups
        self._user_index: dict[str, list[str]] = defaultdict(list)
        self._project_index: dict[str, list[str]] = defaultdict(list)

    def _cache_entry(self, entry: PromptEntry) -> None:
        """Add an entry to all local caches."""
        self._cache[entry.id] = entry
        if entry.id not in self._user_index[entry.user_id]:
            self._user_index[entry.user_id].append(entry.id)
        if entry.project_id and entry.id not in self._project_index[entry.project_id]:
            self._project_index[entry.project_id].append(entry.id)

    def _hydrate_user(self, user_id: str) -> None:
        """Load all prompts for a user from Convex into cache (once)."""
        if user_id in self._user_index and self._user_index[user_id]:
            return  # Already hydrated
        try:
            docs = _get_repo().list_by_user(user_id)
            for doc in docs:
                entry = PromptEntry.from_convex_doc(doc)
                self._cache_entry(entry)
        except Exception as e:
            logger.warning("Convex prompt hydration failed for user %s: %s", user_id, e)

    # ==========================================
    # CRUD Operations
    # ==========================================

    def create_prompt(
        self,
        user_id: str,
        prompt: str,
        prompt_type: PromptType = PromptType.CHAT,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        context: Optional[dict] = None,
        system_prompt: Optional[str] = None,
        model: str = "",
        tags: Optional[list[str]] = None,
    ) -> PromptEntry:
        """Create a new prompt entry, persisted to Convex."""
        entry = PromptEntry(
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
            prompt=prompt,
            system_prompt=system_prompt,
            context=context or {},
            prompt_type=prompt_type,
            model=model,
            tags=tags or [],
        )
        # Write to Convex
        try:
            doc_id = _get_repo().create(
                {
                    "id": entry.id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "session_id": session_id,
                    "prompt": prompt,
                    "system_prompt": system_prompt,
                    "context": context,
                    "prompt_type": prompt_type.value,
                    "status": entry.status.value,
                    "model": model,
                    "tags": tags or [],
                }
            )
            entry._doc_id = doc_id
        except Exception as e:
            logger.warning("Convex prompt create failed: %s", e)

        self._cache_entry(entry)
        return entry

    def update_prompt(
        self,
        prompt_id: str,
        response: Optional[str] = None,
        generated_code: Optional[str] = None,
        generated_files: Optional[list[dict]] = None,
        status: Optional[PromptStatus] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        error: Optional[str] = None,
    ) -> Optional[PromptEntry]:
        """Update a prompt entry."""
        entry = self._cache.get(prompt_id)
        if not entry:
            return None

        updates: dict = {}
        if response is not None:
            entry.response = response
            updates["response"] = response
        if generated_code is not None:
            entry.generated_code = generated_code
            updates["generatedCode"] = generated_code
        if generated_files is not None:
            entry.generated_files = generated_files
            updates["generatedFiles"] = generated_files
        if status is not None:
            entry.status = status
            updates["status"] = status.value
            if status == PromptStatus.PROCESSING and not entry.started_at:
                entry.started_at = datetime.now(timezone.utc)
            elif status in [PromptStatus.COMPLETED, PromptStatus.FAILED]:
                entry.completed_at = datetime.now(timezone.utc)
                if entry.started_at:
                    entry.duration_ms = int(
                        (entry.completed_at - entry.started_at).total_seconds() * 1000
                    )
                    updates["durationMs"] = entry.duration_ms
        if prompt_tokens is not None:
            entry.prompt_tokens = prompt_tokens
            updates["promptTokens"] = prompt_tokens
        if completion_tokens is not None:
            entry.completion_tokens = completion_tokens
            entry.total_tokens = entry.prompt_tokens + completion_tokens
            updates["completionTokens"] = completion_tokens
            updates["totalTokens"] = entry.total_tokens
        if error is not None:
            entry.error = error
            updates["error"] = error

        # Write-through to Convex
        if updates and entry._doc_id:
            try:
                _get_repo().update(entry._doc_id, updates)
            except Exception as e:
                logger.warning("Convex prompt update failed for %s: %s", prompt_id, e)

        return entry

    def get_prompt(self, prompt_id: str) -> Optional[PromptEntry]:
        """Get a prompt by ID."""
        return self._cache.get(prompt_id)

    def delete_prompt(self, prompt_id: str, user_id: str) -> bool:
        """Delete a prompt."""
        entry = self._cache.get(prompt_id)
        if not entry or entry.user_id != user_id:
            return False

        # Delete from Convex
        if entry._doc_id:
            try:
                _get_repo().delete(entry._doc_id)
            except Exception as e:
                logger.warning("Convex prompt delete failed for %s: %s", prompt_id, e)

        del self._cache[prompt_id]
        if prompt_id in self._user_index.get(user_id, []):
            self._user_index[user_id].remove(prompt_id)
        if entry.project_id and prompt_id in self._project_index.get(
            entry.project_id, []
        ):
            self._project_index[entry.project_id].remove(prompt_id)

        return True

    def toggle_favorite(self, prompt_id: str, user_id: str) -> Optional[bool]:
        """Toggle favorite status."""
        entry = self._cache.get(prompt_id)
        if not entry or entry.user_id != user_id:
            return None
        entry.is_favorite = not entry.is_favorite
        if entry._doc_id:
            try:
                _get_repo().update(entry._doc_id, {"isFavorite": entry.is_favorite})
            except Exception as e:
                logger.warning("Convex prompt toggle_favorite failed: %s", e)
        return entry.is_favorite

    def add_tags(
        self, prompt_id: str, user_id: str, tags: list[str]
    ) -> Optional[list[str]]:
        """Add tags to a prompt."""
        entry = self._cache.get(prompt_id)
        if not entry or entry.user_id != user_id:
            return None
        for tag in tags:
            if tag not in entry.tags:
                entry.tags.append(tag)
        if entry._doc_id:
            try:
                _get_repo().update(entry._doc_id, {"tags": entry.tags})
            except Exception as e:
                logger.warning("Convex prompt add_tags failed: %s", e)
        return entry.tags

    # ==========================================
    # Search & Filter
    # ==========================================

    def search_prompts(
        self,
        user_id: str,
        query: Optional[str] = None,
        project_id: Optional[str] = None,
        prompt_type: Optional[PromptType] = None,
        status: Optional[PromptStatus] = None,
        tags: Optional[list[str]] = None,
        favorites_only: bool = False,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PromptEntry], int]:
        """Search prompts with filters. Hydrates from Convex on first call."""
        self._hydrate_user(user_id)

        prompt_ids = self._user_index.get(user_id, [])
        prompts = [self._cache[pid] for pid in prompt_ids if pid in self._cache]

        if project_id:
            prompts = [p for p in prompts if p.project_id == project_id]
        if prompt_type:
            prompts = [p for p in prompts if p.prompt_type == prompt_type]
        if status:
            prompts = [p for p in prompts if p.status == status]
        if favorites_only:
            prompts = [p for p in prompts if p.is_favorite]
        if tags:
            prompts = [p for p in prompts if any(t in p.tags for t in tags)]
        if start_date:
            prompts = [p for p in prompts if p.created_at >= start_date]
        if end_date:
            prompts = [p for p in prompts if p.created_at <= end_date]
        if query:
            query_lower = query.lower()
            prompts = [
                p
                for p in prompts
                if query_lower in p.prompt.lower()
                or (p.response and query_lower in p.response.lower())
                or (p.generated_code and query_lower in p.generated_code.lower())
            ]

        prompts.sort(key=lambda p: p.created_at, reverse=True)
        total = len(prompts)
        prompts = prompts[offset : offset + limit]
        return prompts, total

    def get_recent_prompts(self, user_id: str, limit: int = 10) -> list[PromptEntry]:
        """Get most recent prompts."""
        prompts, _ = self.search_prompts(user_id, limit=limit)
        return prompts

    def get_similar_prompts(
        self, user_id: str, prompt: str, limit: int = 5
    ) -> list[PromptEntry]:
        """Find similar prompts (simple keyword matching)."""
        words = set(prompt.lower().split())
        prompts, _ = self.search_prompts(user_id, limit=100)
        scored = []
        for p in prompts:
            p_words = set(p.prompt.lower().split())
            score = len(words & p_words) / max(len(words | p_words), 1)
            if score > 0.1:
                scored.append((score, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:limit]]

    # ==========================================
    # Statistics
    # ==========================================

    def get_stats(self, user_id: str, days: int = 30) -> PromptStats:
        """Get prompt statistics."""
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        prompts, total = self.search_prompts(user_id, start_date=start_date, limit=1000)
        if not prompts:
            return PromptStats()

        completed = [p for p in prompts if p.status == PromptStatus.COMPLETED]
        total_tokens = sum(p.total_tokens for p in prompts)
        total_duration = sum(p.duration_ms for p in completed)

        by_type: dict[str, int] = defaultdict(int)
        for p in prompts:
            by_type[p.prompt_type.value] += 1

        by_model: dict[str, int] = defaultdict(int)
        for p in prompts:
            if p.model:
                by_model[p.model] += 1

        by_day: dict[str, int] = defaultdict(int)
        for p in prompts:
            day = p.created_at.strftime("%Y-%m-%d")
            by_day[day] += 1

        tag_counts: dict[str, int] = defaultdict(int)
        for p in prompts:
            for tag in p.tags:
                tag_counts[tag] += 1
        most_used_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[
            :10
        ]

        return PromptStats(
            total_prompts=total,
            total_tokens=total_tokens,
            avg_tokens_per_prompt=total_tokens / total if total > 0 else 0,
            avg_duration_ms=total_duration / len(completed) if completed else 0,
            success_rate=len(completed) / total * 100 if total > 0 else 0,
            by_type=dict(by_type),
            by_model=dict(by_model),
            by_day=dict(by_day),
            most_used_tags=most_used_tags,
            recent_prompts=prompts[:5],
        )

    # ==========================================
    # Templates
    # ==========================================

    def save_as_template(
        self, prompt_id: str, user_id: str, template_name: str
    ) -> Optional[dict]:
        """Save a prompt as a reusable template."""
        entry = self._cache.get(prompt_id)
        if not entry or entry.user_id != user_id:
            return None
        return {
            "id": f"template_{uuid4().hex[:8]}",
            "name": template_name,
            "prompt": entry.prompt,
            "system_prompt": entry.system_prompt,
            "prompt_type": entry.prompt_type.value,
            "tags": entry.tags,
            "created_from": prompt_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


# ============================================
# Singleton
# ============================================

_prompt_history_service: Optional[PromptHistoryService] = None


def get_prompt_history_service() -> PromptHistoryService:
    """Get prompt history service singleton."""
    global _prompt_history_service
    if _prompt_history_service is None:
        _prompt_history_service = PromptHistoryService()
    return _prompt_history_service


__all__ = [
    "PromptHistoryService",
    "PromptEntry",
    "PromptStats",
    "PromptType",
    "PromptStatus",
    "get_prompt_history_service",
]
