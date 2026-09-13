"""
Project Memory Service
======================
Persistent memory service for cross-session context recall.

Provides semantic search over:
- ADRs (Architectural Decision Records)
- Project Specifications
- Session Summaries
- Decisions and Context

Uses HybridRetriever from efficiency package for 90%+ semantic accuracy.
Integrates with Repository Pattern for persistent/in-memory storage.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Literal
from dataclasses import dataclass, field

from .repositories import (
    ProjectMemoryRepository,
    get_project_memory_repository,
)

# Import HybridRetriever from efficiency package
try:
    from src.efficiency.retrieval import HybridRetriever
    from src.efficiency.llm import create_efficient_embeddings

    RETRIEVER_AVAILABLE = True
except ImportError:
    RETRIEVER_AVAILABLE = False

logger = logging.getLogger(__name__)


# Memory type definitions
MemoryType = Literal["adr", "project_spec", "session_summary", "decision", "context"]


@dataclass
class MemoryEntry:
    """A single memory entry with metadata."""

    id: str
    memory_type: MemoryType
    project_id: str
    content: str
    title: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "memory_type": self.memory_type,
            "project_id": self.project_id,
            "content": self.content,
            "title": self.title,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        return cls(
            id=data.get("id", ""),
            memory_type=data.get("memory_type", "context"),
            project_id=data.get("project_id", ""),
            content=data.get("content", ""),
            title=data.get("title"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


@dataclass
class RecallResult:
    """Result from semantic recall with relevance score."""

    entry: MemoryEntry
    score: float
    match_type: str  # "semantic", "bm25", or "hybrid"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry": self.entry.to_dict(),
            "score": self.score,
            "match_type": self.match_type,
        }


class ProjectMemoryService:
    """
    Service for managing project memory with semantic search.

    Features:
    - Store ADRs, specs, summaries, and decisions
    - Semantic recall with 90%+ accuracy target
    - Hybrid search (BM25 + embeddings)
    - Cross-session persistence via repository pattern

    Usage:
        service = ProjectMemoryService(project_id="my-project")

        # Store an ADR
        service.store_adr(
            title="Use Repository Pattern",
            content="We decided to use repository pattern for...",
            tags=["architecture", "persistence"]
        )

        # Recall relevant context
        results = service.recall("How do we handle persistence?", k=5)
    """

    def __init__(
        self,
        project_id: str,
        repository: Optional[ProjectMemoryRepository] = None,
        use_semantic_search: bool = True,
    ):
        """
        Initialize project memory service.

        Args:
            project_id: Unique identifier for the project
            repository: Optional custom repository (defaults to env-based)
            use_semantic_search: Whether to enable HybridRetriever (default: True)
        """
        self.project_id = project_id
        self._repo = repository or get_project_memory_repository()
        self._use_semantic = use_semantic_search and RETRIEVER_AVAILABLE
        self._retriever: Optional[HybridRetriever] = None
        self._retriever_dirty = True  # Flag to rebuild retriever when data changes

        if self._use_semantic:
            self._embeddings = create_efficient_embeddings()
        else:
            self._embeddings = None

        logger.info(
            f"ProjectMemoryService initialized for project={project_id}, "
            f"semantic_search={'enabled' if self._use_semantic else 'disabled'}"
        )

    def _rebuild_retriever(self) -> None:
        """Rebuild the HybridRetriever with current memory contents."""
        if not self._use_semantic:
            return

        # Get all memories for this project
        memories = self._repo.list_by_project(self.project_id, limit=1000)

        if not memories:
            self._retriever = None
            self._retriever_dirty = False
            return

        # Extract documents for indexing
        documents = []
        self._doc_to_memory_map = {}

        for mem in memories:
            # Create searchable text combining title, content, and tags
            title = mem.get("title", "")
            content = mem.get("content", "")
            tags = " ".join(mem.get("tags", []))
            doc_text = f"{title}\n{content}\n{tags}".strip()

            documents.append(doc_text)
            self._doc_to_memory_map[len(documents) - 1] = mem

        try:
            self._retriever = HybridRetriever(
                documents=documents, embeddings=self._embeddings, use_cache=True
            )
            self._retriever_dirty = False
            logger.debug(f"Rebuilt retriever with {len(documents)} documents")
        except Exception as e:
            logger.warning(f"Failed to build HybridRetriever: {e}")
            self._retriever = None

    # ==========================================================================
    # Storage Methods
    # ==========================================================================

    def store(
        self,
        memory_type: MemoryType,
        content: str,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryEntry:
        """
        Store a memory entry.

        Args:
            memory_type: Type of memory (adr, project_spec, session_summary, etc.)
            content: The main content to store
            title: Optional human-readable title
            tags: Optional tags for filtering
            metadata: Optional additional structured data

        Returns:
            The created MemoryEntry
        """
        memory_data = {
            "memory_type": memory_type,
            "project_id": self.project_id,
            "content": content,
            "title": title,
            "tags": tags or [],
            "metadata": metadata or {},
        }

        result = self._repo.create(memory_data)
        self._retriever_dirty = True

        logger.info(f"Stored {memory_type}: {title or result.get('id', 'unknown')}")
        return MemoryEntry.from_dict(result)

    def store_adr(
        self,
        title: str,
        content: str,
        decision: Optional[str] = None,
        context: Optional[str] = None,
        consequences: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> MemoryEntry:
        """
        Store an Architectural Decision Record (ADR).

        Args:
            title: ADR title (e.g., "Use Repository Pattern")
            content: Full ADR content
            decision: The decision made (optional, extracted if not provided)
            context: Context/background for the decision
            consequences: Known consequences of the decision
            tags: Tags for filtering

        Returns:
            The created MemoryEntry
        """
        metadata = {
            "decision": decision,
            "context": context,
            "consequences": consequences,
        }
        # Remove None values
        metadata = {k: v for k, v in metadata.items() if v is not None}

        return self.store(
            memory_type="adr",
            content=content,
            title=title,
            tags=tags or ["architecture", "decision"],
            metadata=metadata,
        )

    def store_project_spec(
        self,
        title: str,
        content: str,
        version: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> MemoryEntry:
        """
        Store a Project Specification snapshot.

        Args:
            title: Spec title or version name
            content: Full specification content
            version: Version identifier
            tags: Tags for filtering

        Returns:
            The created MemoryEntry
        """
        metadata = {"version": version} if version else {}

        return self.store(
            memory_type="project_spec",
            content=content,
            title=title,
            tags=tags or ["specification", "requirements"],
            metadata=metadata,
        )

    def store_session_summary(
        self,
        content: str,
        session_id: Optional[str] = None,
        key_decisions: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ) -> MemoryEntry:
        """
        Store a Session Summary for cross-session continuity.

        Args:
            content: Summary of the session
            session_id: Optional session identifier
            key_decisions: List of key decisions made in the session
            tags: Tags for filtering

        Returns:
            The created MemoryEntry
        """
        metadata = {
            "session_id": session_id,
            "key_decisions": key_decisions or [],
        }
        metadata = {k: v for k, v in metadata.items() if v}

        return self.store(
            memory_type="session_summary",
            content=content,
            title=f"Session Summary - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
            tags=tags or ["session", "summary"],
            metadata=metadata,
        )

    def store_decision(
        self,
        content: str,
        title: Optional[str] = None,
        rationale: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> MemoryEntry:
        """
        Store a standalone decision.

        Args:
            content: The decision content
            title: Decision title
            rationale: Why this decision was made
            tags: Tags for filtering

        Returns:
            The created MemoryEntry
        """
        metadata = {"rationale": rationale} if rationale else {}

        return self.store(
            memory_type="decision",
            content=content,
            title=title,
            tags=tags or ["decision"],
            metadata=metadata,
        )

    def store_context(
        self,
        content: str,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> MemoryEntry:
        """
        Store general context/background information.

        Args:
            content: The context content
            title: Optional title
            tags: Tags for filtering

        Returns:
            The created MemoryEntry
        """
        return self.store(
            memory_type="context",
            content=content,
            title=title,
            tags=tags or ["context"],
        )

    # ==========================================================================
    # Recall Methods (Semantic Search)
    # ==========================================================================

    def recall(
        self,
        query: str,
        k: int = 5,
        memory_type: Optional[MemoryType] = None,
        alpha: float = 0.5,
    ) -> List[RecallResult]:
        """
        Semantic recall of relevant memories.

        Uses HybridRetriever for 90%+ accuracy combining:
        - BM25 keyword matching
        - Semantic embedding similarity

        Args:
            query: Natural language query
            k: Number of results to return
            memory_type: Optional filter by memory type
            alpha: Semantic weight (0=BM25 only, 1=semantic only, 0.5=hybrid)

        Returns:
            List of RecallResult with relevance scores
        """
        # Rebuild retriever if needed
        if self._retriever_dirty:
            self._rebuild_retriever()

        # If semantic search is available, use it
        if self._retriever is not None:
            results = self._retriever.search(query, k=k * 2, alpha=alpha)

            recall_results = []
            for result in results[:k]:
                idx = result.get("index", 0)
                if idx in self._doc_to_memory_map:
                    memory_dict = self._doc_to_memory_map[idx]

                    # Apply type filter if specified
                    if memory_type and memory_dict.get("memory_type") != memory_type:
                        continue

                    recall_results.append(
                        RecallResult(
                            entry=MemoryEntry.from_dict(memory_dict),
                            score=result.get("final_score", result.get("score", 0)),
                            match_type=result.get("type", "hybrid"),
                        )
                    )

            return recall_results[:k]

        # Fallback to basic keyword search via repository
        return self._keyword_recall(query, k, memory_type)

    def _keyword_recall(
        self,
        query: str,
        k: int,
        memory_type: Optional[MemoryType] = None,
    ) -> List[RecallResult]:
        """Fallback keyword-based recall when semantic search unavailable."""
        memories = self._repo.list_by_project(self.project_id, memory_type, limit=100)

        query_terms = set(query.lower().split())
        scored = []

        for mem in memories:
            content = mem.get("content", "").lower()
            title = (mem.get("title") or "").lower()
            tags = " ".join(mem.get("tags", [])).lower()
            text = f"{title} {content} {tags}"

            text_terms = set(text.split())
            overlap = len(query_terms & text_terms)

            if overlap > 0:
                score = overlap / len(query_terms)
                scored.append((mem, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        return [
            RecallResult(
                entry=MemoryEntry.from_dict(mem),
                score=score,
                match_type="bm25",
            )
            for mem, score in scored[:k]
        ]

    def recall_recent(
        self,
        k: int = 10,
        memory_type: Optional[MemoryType] = None,
    ) -> List[MemoryEntry]:
        """
        Get most recent memories (no semantic search).

        Args:
            k: Number of recent entries
            memory_type: Optional filter by type

        Returns:
            List of recent MemoryEntry objects
        """
        memories = self._repo.get_recent(self.project_id, k, memory_type)
        return [MemoryEntry.from_dict(m) for m in memories]

    def recall_by_tags(
        self,
        tags: List[str],
        k: int = 10,
    ) -> List[MemoryEntry]:
        """
        Recall memories by tag.

        Args:
            tags: Tags to search for (any match)
            k: Max results

        Returns:
            List of matching MemoryEntry objects
        """
        memories = self._repo.search_by_tags(tags, self.project_id, k)
        return [MemoryEntry.from_dict(m) for m in memories]

    # ==========================================================================
    # Agent Integration Methods
    # ==========================================================================

    def get_context_for_agent(
        self,
        task_description: str,
        max_tokens: int = 2000,
        include_types: Optional[List[MemoryType]] = None,
    ) -> str:
        """
        Get formatted context string for an agent prompt.

        Args:
            task_description: Description of the agent's current task
            max_tokens: Approximate max tokens (chars/4) for context
            include_types: Memory types to include (default: all)

        Returns:
            Formatted context string for agent prompt injection
        """
        # Recall relevant memories
        results = self.recall(task_description, k=10)

        if include_types:
            results = [r for r in results if r.entry.memory_type in include_types]

        if not results:
            return ""

        # Build formatted context
        lines = ["## Relevant Project Memory\n"]
        char_count = len(lines[0])
        max_chars = max_tokens * 4

        for result in results:
            entry = result.entry
            header = f"### [{entry.memory_type.upper()}] {entry.title or 'Untitled'}\n"
            content_preview = (
                entry.content[:500] + "..."
                if len(entry.content) > 500
                else entry.content
            )
            block = f"{header}{content_preview}\n\n"

            if char_count + len(block) > max_chars:
                break

            lines.append(block)
            char_count += len(block)

        return "".join(lines)

    def summarize_session(
        self,
        session_events: List[Dict[str, Any]],
        auto_store: bool = True,
    ) -> str:
        """
        Generate and optionally store a session summary.

        Args:
            session_events: List of events/actions from the session
            auto_store: Whether to automatically store the summary

        Returns:
            The generated summary text
        """
        # Simple summary generation (could be enhanced with LLM)
        summary_parts = []

        decisions = [e for e in session_events if e.get("type") == "decision"]
        if decisions:
            summary_parts.append(f"Decisions made: {len(decisions)}")
            for d in decisions[:3]:
                summary_parts.append(f"  - {d.get('content', 'Unknown')[:100]}")

        files_modified = [e for e in session_events if e.get("type") == "file_change"]
        if files_modified:
            summary_parts.append(f"Files modified: {len(files_modified)}")

        summary = (
            "\n".join(summary_parts)
            if summary_parts
            else "Session with no recorded events."
        )

        if auto_store:
            key_decisions = [d.get("content", "")[:100] for d in decisions[:5]]
            self.store_session_summary(
                content=summary,
                key_decisions=key_decisions,
            )

        return summary

    # ==========================================================================
    # Management Methods
    # ==========================================================================

    def get(self, memory_id: str) -> Optional[MemoryEntry]:
        """Get a specific memory by ID."""
        result = self._repo.get_by_id(memory_id)
        return MemoryEntry.from_dict(result) if result else None

    def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[MemoryEntry]:
        """Update an existing memory entry."""
        updates = {}
        if content is not None:
            updates["content"] = content
        if title is not None:
            updates["title"] = title
        if tags is not None:
            updates["tags"] = tags
        if metadata is not None:
            updates["metadata"] = metadata

        if not updates:
            return self.get(memory_id)

        result = self._repo.update(memory_id, updates)
        if result:
            self._retriever_dirty = True
            return MemoryEntry.from_dict(result)
        return None

    def delete(self, memory_id: str) -> bool:
        """Delete a memory entry."""
        success = self._repo.delete(memory_id)
        if success:
            self._retriever_dirty = True
        return success

    def list_all(
        self,
        memory_type: Optional[MemoryType] = None,
        limit: int = 100,
    ) -> List[MemoryEntry]:
        """List all memories for the project."""
        memories = self._repo.list_by_project(self.project_id, memory_type, limit)
        return [MemoryEntry.from_dict(m) for m in memories]

    def clear_project_memory(self) -> int:
        """
        Clear all memories for the project. Use with caution!

        Returns:
            Number of entries deleted
        """
        memories = self._repo.list_by_project(self.project_id, limit=10000)
        count = 0
        for mem in memories:
            mem_id = mem.get("id") or mem.get("_id")
            if mem_id and self._repo.delete(mem_id):
                count += 1

        self._retriever_dirty = True
        logger.warning(f"Cleared {count} memories for project {self.project_id}")
        return count


# =============================================================================
# Factory Function
# =============================================================================

_memory_services: Dict[str, ProjectMemoryService] = {}


def get_project_memory_service(
    project_id: str,
    repository: Optional[ProjectMemoryRepository] = None,
) -> ProjectMemoryService:
    """
    Get or create a ProjectMemoryService for a project.

    Args:
        project_id: Unique project identifier
        repository: Optional custom repository

    Returns:
        ProjectMemoryService instance (cached per project_id)
    """
    if project_id not in _memory_services:
        _memory_services[project_id] = ProjectMemoryService(
            project_id=project_id,
            repository=repository,
        )
    return _memory_services[project_id]


__all__ = [
    "ProjectMemoryService",
    "MemoryEntry",
    "RecallResult",
    "MemoryType",
    "get_project_memory_service",
    "RETRIEVER_AVAILABLE",
]
