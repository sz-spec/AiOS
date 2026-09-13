"""
Prompt History Service

Manages AI prompt history:
- Store prompts and responses
- Search and filter prompts
- Track token usage
- Analyze prompt patterns
- Export history
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4
from collections import defaultdict

# ============================================
# Enums
# ============================================


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
    CODE_EXPLAIN = "code_explain"
    CODE_FIX = "code_fix"
    CHAT = "chat"
    DESIGN = "design"
    REFACTOR = "refactor"
    TEST_GENERATION = "test_generation"
    DOCUMENTATION = "documentation"


# ============================================
# Data Models
# ============================================


@dataclass
class PromptMessage:
    """Single message in a prompt conversation."""

    role: str  # user, assistant, system
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GeneratedCode:
    """Code generated from a prompt."""

    file_path: str
    content: str
    language: str
    lines_added: int = 0
    lines_removed: int = 0


@dataclass
class PromptEntry:
    """A single prompt history entry."""

    id: str = field(default_factory=lambda: f"prompt_{uuid4().hex[:12]}")

    # User context
    user_id: str = ""
    project_id: Optional[str] = None
    session_id: Optional[str] = None

    # Prompt data
    prompt_type: PromptType = PromptType.CHAT
    status: PromptStatus = PromptStatus.PENDING

    # Messages
    messages: list[PromptMessage] = field(default_factory=list)

    # Input/Output
    input_prompt: str = ""
    output_response: str = ""
    generated_code: list[GeneratedCode] = field(default_factory=list)

    # Model info
    model: str = ""
    provider: str = ""  # openai, anthropic, etc.

    # Token usage
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    # Performance
    duration_ms: int = 0

    # Metadata
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    # User feedback
    rating: Optional[int] = None  # 1-5
    feedback: Optional[str] = None
    is_favorite: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            **asdict(self),
            "prompt_type": self.prompt_type.value,
            "status": self.status.value,
            "messages": [
                {**m.__dict__, "timestamp": m.timestamp.isoformat()}
                for m in self.messages
            ],
            "generated_code": [asdict(c) for c in self.generated_code],
            "created_at": self.created_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }


@dataclass
class PromptStats:
    """Statistics for prompt history."""

    total_prompts: int = 0
    successful_prompts: int = 0
    failed_prompts: int = 0

    total_tokens: int = 0
    avg_tokens_per_prompt: float = 0.0

    total_duration_ms: int = 0
    avg_duration_ms: float = 0.0

    by_type: dict[str, int] = field(default_factory=dict)
    by_model: dict[str, int] = field(default_factory=dict)
    by_day: dict[str, int] = field(default_factory=dict)

    most_used_tags: list[tuple[str, int]] = field(default_factory=list)


# ============================================
# Prompt History Service
# ============================================


class PromptHistoryService:
    """Service for managing prompt history."""

    def __init__(self):
        # In-memory storage (replace with persistent backend in production)
        self._prompts: dict[str, PromptEntry] = {}
        self._user_prompts: dict[str, list[str]] = defaultdict(list)
        self._project_prompts: dict[str, list[str]] = defaultdict(list)

    # ==========================================
    # CRUD Operations
    # ==========================================

    def create_prompt(
        self,
        user_id: str,
        input_prompt: str,
        prompt_type: PromptType = PromptType.CHAT,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        model: str = "",
        provider: str = "",
        metadata: Optional[dict] = None,
        tags: Optional[list[str]] = None,
    ) -> PromptEntry:
        """Create a new prompt entry."""
        entry = PromptEntry(
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
            prompt_type=prompt_type,
            input_prompt=input_prompt,
            model=model,
            provider=provider,
            metadata=metadata or {},
            tags=tags or [],
            messages=[PromptMessage(role="user", content=input_prompt)],
        )

        self._prompts[entry.id] = entry
        self._user_prompts[user_id].append(entry.id)

        if project_id:
            self._project_prompts[project_id].append(entry.id)

        return entry

    def update_prompt(
        self,
        prompt_id: str,
        status: Optional[PromptStatus] = None,
        output_response: Optional[str] = None,
        generated_code: Optional[list[GeneratedCode]] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        duration_ms: Optional[int] = None,
        error: Optional[str] = None,
    ) -> Optional[PromptEntry]:
        """Update a prompt entry."""
        entry = self._prompts.get(prompt_id)
        if not entry:
            return None

        if status:
            entry.status = status

        if output_response:
            entry.output_response = output_response
            entry.messages.append(
                PromptMessage(role="assistant", content=output_response)
            )

        if generated_code:
            entry.generated_code = generated_code

        if prompt_tokens is not None:
            entry.prompt_tokens = prompt_tokens

        if completion_tokens is not None:
            entry.completion_tokens = completion_tokens
            entry.total_tokens = entry.prompt_tokens + completion_tokens

        if duration_ms is not None:
            entry.duration_ms = duration_ms

        if error:
            entry.error = error
            entry.status = PromptStatus.FAILED

        if status == PromptStatus.COMPLETED:
            entry.completed_at = datetime.now(timezone.utc)

        return entry

    def get_prompt(self, prompt_id: str) -> Optional[PromptEntry]:
        """Get a prompt by ID."""
        return self._prompts.get(prompt_id)

    def delete_prompt(self, prompt_id: str, user_id: str) -> bool:
        """Delete a prompt."""
        entry = self._prompts.get(prompt_id)
        if not entry or entry.user_id != user_id:
            return False

        del self._prompts[prompt_id]

        if prompt_id in self._user_prompts[user_id]:
            self._user_prompts[user_id].remove(prompt_id)

        if entry.project_id and prompt_id in self._project_prompts[entry.project_id]:
            self._project_prompts[entry.project_id].remove(prompt_id)

        return True

    # ==========================================
    # Query Operations
    # ==========================================

    def list_prompts(
        self,
        user_id: str,
        project_id: Optional[str] = None,
        prompt_type: Optional[PromptType] = None,
        status: Optional[PromptStatus] = None,
        search: Optional[str] = None,
        tags: Optional[list[str]] = None,
        favorites_only: bool = False,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PromptEntry], int]:
        """List prompts with filters."""
        # Get user's prompts
        prompt_ids = self._user_prompts.get(user_id, [])

        # Filter by project if specified
        if project_id:
            project_prompt_ids = set(self._project_prompts.get(project_id, []))
            prompt_ids = [pid for pid in prompt_ids if pid in project_prompt_ids]

        # Get entries
        entries = [self._prompts[pid] for pid in prompt_ids if pid in self._prompts]

        # Apply filters
        if prompt_type:
            entries = [e for e in entries if e.prompt_type == prompt_type]

        if status:
            entries = [e for e in entries if e.status == status]

        if search:
            search_lower = search.lower()
            entries = [
                e
                for e in entries
                if search_lower in e.input_prompt.lower()
                or search_lower in e.output_response.lower()
            ]

        if tags:
            entries = [e for e in entries if any(tag in e.tags for tag in tags)]

        if favorites_only:
            entries = [e for e in entries if e.is_favorite]

        if start_date:
            entries = [e for e in entries if e.created_at >= start_date]

        if end_date:
            entries = [e for e in entries if e.created_at <= end_date]

        # Sort by created_at descending
        entries.sort(key=lambda e: e.created_at, reverse=True)

        total = len(entries)

        # Paginate
        entries = entries[offset : offset + limit]

        return entries, total

    def search_prompts(
        self,
        user_id: str,
        query: str,
        limit: int = 20,
    ) -> list[PromptEntry]:
        """Full-text search in prompts."""
        query_lower = query.lower()

        prompt_ids = self._user_prompts.get(user_id, [])
        entries = [self._prompts[pid] for pid in prompt_ids if pid in self._prompts]

        # Score and filter
        scored = []
        for entry in entries:
            score = 0

            # Check input prompt
            if query_lower in entry.input_prompt.lower():
                score += 10

            # Check output response
            if query_lower in entry.output_response.lower():
                score += 5

            # Check tags
            for tag in entry.tags:
                if query_lower in tag.lower():
                    score += 3

            # Check generated code
            for code in entry.generated_code:
                if query_lower in code.content.lower():
                    score += 2
                if query_lower in code.file_path.lower():
                    score += 1

            if score > 0:
                scored.append((score, entry))

        # Sort by score
        scored.sort(key=lambda x: x[0], reverse=True)

        return [entry for _, entry in scored[:limit]]

    # ==========================================
    # Feedback & Favorites
    # ==========================================

    def rate_prompt(
        self,
        prompt_id: str,
        user_id: str,
        rating: int,
        feedback: Optional[str] = None,
    ) -> bool:
        """Rate a prompt."""
        entry = self._prompts.get(prompt_id)
        if not entry or entry.user_id != user_id:
            return False

        entry.rating = max(1, min(5, rating))
        if feedback:
            entry.feedback = feedback

        return True

    def toggle_favorite(self, prompt_id: str, user_id: str) -> Optional[bool]:
        """Toggle favorite status."""
        entry = self._prompts.get(prompt_id)
        if not entry or entry.user_id != user_id:
            return None

        entry.is_favorite = not entry.is_favorite
        return entry.is_favorite

    def add_tags(
        self,
        prompt_id: str,
        user_id: str,
        tags: list[str],
    ) -> bool:
        """Add tags to a prompt."""
        entry = self._prompts.get(prompt_id)
        if not entry or entry.user_id != user_id:
            return False

        for tag in tags:
            if tag not in entry.tags:
                entry.tags.append(tag)

        return True

    # ==========================================
    # Statistics
    # ==========================================

    def get_stats(
        self,
        user_id: str,
        project_id: Optional[str] = None,
        days: int = 30,
    ) -> PromptStats:
        """Get prompt statistics."""
        start_date = datetime.now(timezone.utc) - timedelta(days=days)

        entries, _ = self.list_prompts(
            user_id=user_id,
            project_id=project_id,
            start_date=start_date,
            limit=10000,
        )

        stats = PromptStats()
        stats.total_prompts = len(entries)

        by_type: dict[str, int] = defaultdict(int)
        by_model: dict[str, int] = defaultdict(int)
        by_day: dict[str, int] = defaultdict(int)
        tag_counts: dict[str, int] = defaultdict(int)

        for entry in entries:
            # Status counts
            if entry.status == PromptStatus.COMPLETED:
                stats.successful_prompts += 1
            elif entry.status == PromptStatus.FAILED:
                stats.failed_prompts += 1

            # Token usage
            stats.total_tokens += entry.total_tokens
            stats.total_duration_ms += entry.duration_ms

            # By type
            by_type[entry.prompt_type.value] += 1

            # By model
            if entry.model:
                by_model[entry.model] += 1

            # By day
            day_key = entry.created_at.strftime("%Y-%m-%d")
            by_day[day_key] += 1

            # Tags
            for tag in entry.tags:
                tag_counts[tag] += 1

        # Averages
        if stats.total_prompts > 0:
            stats.avg_tokens_per_prompt = stats.total_tokens / stats.total_prompts
            stats.avg_duration_ms = stats.total_duration_ms / stats.total_prompts

        stats.by_type = dict(by_type)
        stats.by_model = dict(by_model)
        stats.by_day = dict(by_day)

        # Top tags
        stats.most_used_tags = sorted(
            tag_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:10]

        return stats

    # ==========================================
    # Export
    # ==========================================

    def export_prompts(
        self,
        user_id: str,
        project_id: Optional[str] = None,
        format: str = "json",
    ) -> dict:
        """Export prompts for a user."""
        entries, total = self.list_prompts(
            user_id=user_id,
            project_id=project_id,
            limit=10000,
        )

        if format == "json":
            return {
                "prompts": [e.to_dict() for e in entries],
                "total": total,
                "exported_at": datetime.now(timezone.utc).isoformat(),
            }

        # CSV format
        rows = []
        for entry in entries:
            rows.append(
                {
                    "id": entry.id,
                    "type": entry.prompt_type.value,
                    "status": entry.status.value,
                    "input": entry.input_prompt[:200],
                    "output": entry.output_response[:200],
                    "tokens": entry.total_tokens,
                    "duration_ms": entry.duration_ms,
                    "created_at": entry.created_at.isoformat(),
                }
            )

        return {"rows": rows, "total": total}


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
    "PromptMessage",
    "GeneratedCode",
    "PromptStatus",
    "PromptType",
    "PromptStats",
    "get_prompt_history_service",
]
