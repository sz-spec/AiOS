"""
Semantic Cache for LLM Responses
================================
Uses embedding similarity to cache and retrieve semantically similar queries.

Features:
- 92% cosine similarity threshold for cache hits
- 24-hour TTL for cached entries
- ChromaDB for vector storage
- Integrates with SmartRouter pipeline

Configuration via environment variables:
- SEMANTIC_CACHE_ENABLED: Enable/disable cache (default: true)
- SEMANTIC_CACHE_THRESHOLD: Similarity threshold (default: 0.92)
- SEMANTIC_CACHE_TTL_HOURS: Cache TTL in hours (default: 24)
- SEMANTIC_CACHE_MAX_SIZE: Max entries before pruning (default: 10000)

Usage:
    from ai.cache import get_semantic_cache

    cache = get_semantic_cache()

    # Check cache before LLM call
    cached = await cache.get(prompt, role="coding", model="claude-opus")
    if cached:
        return cached.response

    # After LLM call, store in cache
    await cache.set(prompt, response, role="coding", model="claude-opus")
"""

import os
import hashlib
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Configuration from environment
CACHE_ENABLED = os.getenv("SEMANTIC_CACHE_ENABLED", "true").lower() == "true"
SIMILARITY_THRESHOLD = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.92"))
TTL_HOURS = int(os.getenv("SEMANTIC_CACHE_TTL_HOURS", "24"))
MAX_CACHE_SIZE = int(os.getenv("SEMANTIC_CACHE_MAX_SIZE", "10000"))

# Try to import required libraries
try:
    import chromadb
    from chromadb.config import Settings

    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logger.warning("ChromaDB not installed. Semantic cache disabled.")

try:
    from sentence_transformers import SentenceTransformer

    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False
    logger.warning("sentence-transformers not installed. Semantic cache disabled.")


@dataclass
class CacheEntry:
    """A cached LLM response."""

    query: str
    response: str
    role: str
    model: str
    created_at: str
    expires_at: str
    similarity: float = 1.0
    hit_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        """Check if entry has expired."""
        expires = datetime.fromisoformat(self.expires_at)
        return datetime.now(timezone.utc) > expires

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CacheStats:
    """Cache performance statistics."""

    hits: int = 0
    misses: int = 0
    total_queries: int = 0
    total_entries: int = 0
    avg_similarity: float = 0.0
    cost_saved_estimate: float = 0.0

    @property
    def hit_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.hits / self.total_queries

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "hit_rate": self.hit_rate,
            "hit_rate_pct": f"{self.hit_rate * 100:.1f}%",
        }


class SemanticCache:
    """
    Semantic cache for LLM responses using embedding similarity.

    Uses ChromaDB for vector storage and sentence-transformers for embeddings.
    Cache hits require >= 92% cosine similarity by default.
    """

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        collection_name: str = "llm_semantic_cache",
        embedding_model: str = "all-MiniLM-L6-v2",
        similarity_threshold: float = SIMILARITY_THRESHOLD,
        ttl_hours: int = TTL_HOURS,
        max_size: int = MAX_CACHE_SIZE,
    ):
        """
        Initialize the semantic cache.

        Args:
            persist_dir: Directory for ChromaDB persistence
            collection_name: ChromaDB collection name
            embedding_model: Sentence transformer model for embeddings
            similarity_threshold: Minimum similarity for cache hit (0.0-1.0)
            ttl_hours: Time-to-live for cache entries in hours
            max_size: Maximum cache entries before pruning
        """
        self.persist_dir = persist_dir or str(
            Path(__file__).parent.parent.parent / "data" / "semantic_cache"
        )
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model
        self.similarity_threshold = similarity_threshold
        self.ttl_hours = ttl_hours
        self.max_size = max_size

        # Statistics
        self._stats = CacheStats()

        # Initialize components
        self._initialized = False
        self._chroma_client = None
        self._collection = None
        self._embedding_model = None
        self._lock = asyncio.Lock()

        # Check if cache is available
        self.available = CACHE_ENABLED and CHROMADB_AVAILABLE and EMBEDDINGS_AVAILABLE

        if self.available:
            self._initialize()

    def _initialize(self):
        """Initialize ChromaDB and embedding model."""
        if self._initialized:
            return

        try:
            # Create persist directory
            Path(self.persist_dir).mkdir(parents=True, exist_ok=True)

            # Initialize ChromaDB with persistence
            self._chroma_client = chromadb.Client(
                Settings(
                    chroma_db_impl="duckdb+parquet",
                    persist_directory=self.persist_dir,
                    anonymized_telemetry=False,
                )
            )

            # Get or create collection
            self._collection = self._chroma_client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},  # Cosine similarity
            )

            # Load embedding model
            self._embedding_model = SentenceTransformer(self.embedding_model_name)

            self._initialized = True
            self._stats.total_entries = self._collection.count()

            logger.info(
                f"Semantic cache initialized: {self._stats.total_entries} entries, "
                f"threshold={self.similarity_threshold}, ttl={self.ttl_hours}h"
            )

        except Exception as e:
            logger.error(f"Failed to initialize semantic cache: {e}")
            self.available = False

    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding vector for text."""
        if not self._embedding_model:
            return []
        return self._embedding_model.encode(text).tolist()

    def _generate_id(self, query: str, role: str, model: str) -> str:
        """Generate unique ID for cache entry."""
        content = f"{query}:{role}:{model}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    async def get(
        self, query: str, role: str = "general", model: str = "any", **kwargs
    ) -> Optional[CacheEntry]:
        """
        Get cached response for a semantically similar query.

        Args:
            query: The query/prompt to search for
            role: Task role (coding, architect, reviewer, etc.)
            model: Model name for filtering

        Returns:
            CacheEntry if found with similarity >= threshold, else None
        """
        if not self.available or not self._initialized:
            return None

        self._stats.total_queries += 1

        try:
            # Get embedding for query
            embedding = self._get_embedding(query)
            if not embedding:
                self._stats.misses += 1
                return None

            # Build filter for role and model
            where_filter = {"$and": [{"role": role}]}
            if model != "any":
                where_filter["$and"].append({"model": model})

            # Query ChromaDB for similar entries
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=1,
                where=where_filter if len(where_filter["$and"]) > 0 else None,
                include=["metadatas", "documents", "distances"],
            )

            if not results["ids"] or not results["ids"][0]:
                self._stats.misses += 1
                return None

            # Check similarity (ChromaDB returns distance, convert to similarity)
            # For cosine distance: similarity = 1 - distance
            distance = results["distances"][0][0]
            similarity = 1 - distance

            if similarity < self.similarity_threshold:
                logger.debug(
                    f"Cache miss: similarity {similarity:.3f} < threshold {self.similarity_threshold}"
                )
                self._stats.misses += 1
                return None

            # Get metadata
            metadata = results["metadatas"][0][0]

            # Check expiration
            expires_at = datetime.fromisoformat(metadata["expires_at"])
            if datetime.now(timezone.utc) > expires_at:
                # Entry expired, delete it
                self._collection.delete(ids=[results["ids"][0][0]])
                self._stats.misses += 1
                return None

            # Cache hit!
            self._stats.hits += 1

            # Update average similarity
            total_sims = (
                self._stats.avg_similarity * (self._stats.hits - 1) + similarity
            )
            self._stats.avg_similarity = total_sims / self._stats.hits

            # Estimate cost saved (rough: $0.01 per 1K tokens, avg 500 tokens)
            self._stats.cost_saved_estimate += 0.005

            logger.debug(f"Cache hit: similarity={similarity:.3f}, role={role}")

            return CacheEntry(
                query=metadata.get("original_query", query),
                response=results["documents"][0][0],
                role=metadata["role"],
                model=metadata["model"],
                created_at=metadata["created_at"],
                expires_at=metadata["expires_at"],
                similarity=similarity,
                hit_count=metadata.get("hit_count", 0) + 1,
                metadata=metadata,
            )

        except Exception as e:
            logger.error(f"Cache get error: {e}")
            self._stats.misses += 1
            return None

    async def set(
        self,
        query: str,
        response: str,
        role: str = "general",
        model: str = "unknown",
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """
        Cache an LLM response.

        Args:
            query: The query/prompt
            response: The LLM response
            role: Task role
            model: Model that generated the response
            metadata: Additional metadata

        Returns:
            True if cached successfully
        """
        if not self.available or not self._initialized:
            return False

        try:
            # Check cache size and prune if needed
            if self._collection.count() >= self.max_size:
                await self._prune_expired()

            # Get embedding
            embedding = self._get_embedding(query)
            if not embedding:
                return False

            # Generate ID
            entry_id = self._generate_id(query, role, model)

            # Calculate expiration
            created_at = datetime.now(timezone.utc)
            expires_at = created_at + timedelta(hours=self.ttl_hours)

            # Build metadata
            entry_metadata = {
                "original_query": query[:500],  # Truncate for storage
                "role": role,
                "model": model,
                "created_at": created_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "hit_count": 0,
                "response_length": len(response),
            }
            if metadata:
                entry_metadata.update(
                    {
                        k: str(v)[:200]
                        for k, v in metadata.items()
                        if k not in entry_metadata
                    }
                )

            # Upsert to ChromaDB
            self._collection.upsert(
                ids=[entry_id],
                embeddings=[embedding],
                documents=[response],
                metadatas=[entry_metadata],
            )

            self._stats.total_entries = self._collection.count()
            logger.debug(f"Cached response: role={role}, model={model}, id={entry_id}")

            return True

        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False

    async def _prune_expired(self):
        """Remove expired entries from cache."""
        if not self._collection:
            return

        try:
            # Get all entries
            all_entries = self._collection.get(include=["metadatas"])
            if not all_entries["ids"]:
                return

            # Find expired entries
            now = datetime.now(timezone.utc)
            expired_ids = []

            for i, entry_id in enumerate(all_entries["ids"]):
                metadata = all_entries["metadatas"][i]
                expires_at = datetime.fromisoformat(
                    metadata.get("expires_at", now.isoformat())
                )
                if now > expires_at:
                    expired_ids.append(entry_id)

            # Delete expired
            if expired_ids:
                self._collection.delete(ids=expired_ids)
                logger.info(f"Pruned {len(expired_ids)} expired cache entries")

            self._stats.total_entries = self._collection.count()

        except Exception as e:
            logger.error(f"Cache prune error: {e}")

    async def clear(self):
        """Clear all cache entries."""
        if not self._collection:
            return

        try:
            # Delete collection and recreate
            self._chroma_client.delete_collection(self.collection_name)
            self._collection = self._chroma_client.create_collection(
                name=self.collection_name, metadata={"hnsw:space": "cosine"}
            )
            self._stats = CacheStats()
            logger.info("Semantic cache cleared")

        except Exception as e:
            logger.error(f"Cache clear error: {e}")

    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        if self._collection:
            self._stats.total_entries = self._collection.count()
        return self._stats

    async def warmup(self, entries: List[Dict[str, Any]]):
        """
        Pre-populate cache with common queries.

        Args:
            entries: List of {query, response, role, model} dicts
        """
        for entry in entries:
            await self.set(
                query=entry["query"],
                response=entry["response"],
                role=entry.get("role", "general"),
                model=entry.get("model", "unknown"),
            )
        logger.info(f"Warmed up cache with {len(entries)} entries")


# =============================================================================
# Singleton Instance
# =============================================================================

_semantic_cache: Optional[SemanticCache] = None


def get_semantic_cache() -> SemanticCache:
    """
    Get the singleton semantic cache instance.

    Returns:
        SemanticCache instance
    """
    global _semantic_cache

    if _semantic_cache is None:
        _semantic_cache = SemanticCache()

    return _semantic_cache


def reset_semantic_cache():
    """Reset the singleton cache instance (for testing)."""
    global _semantic_cache
    _semantic_cache = None


# =============================================================================
# SmartRouter Integration Helper
# =============================================================================


async def check_cache_before_llm(
    prompt: str, role: str = "general", model: str = "any"
) -> Tuple[Optional[str], bool]:
    """
    Check semantic cache before making LLM call.

    Integration helper for SmartRouter pipeline.

    Args:
        prompt: The prompt to check
        role: Task role for filtering
        model: Model name for filtering

    Returns:
        Tuple of (response or None, was_cache_hit)
    """
    cache = get_semantic_cache()

    if not cache.available:
        return None, False

    entry = await cache.get(prompt, role=role, model=model)

    if entry:
        return entry.response, True

    return None, False


async def cache_llm_response(
    prompt: str,
    response: str,
    role: str = "general",
    model: str = "unknown",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Cache an LLM response after generation.

    Integration helper for SmartRouter pipeline.

    Args:
        prompt: The original prompt
        response: The LLM response
        role: Task role
        model: Model that generated response
        metadata: Optional metadata

    Returns:
        True if cached successfully
    """
    cache = get_semantic_cache()

    if not cache.available:
        return False

    return await cache.set(
        query=prompt, response=response, role=role, model=model, metadata=metadata
    )


__all__ = [
    "SemanticCache",
    "get_semantic_cache",
    "reset_semantic_cache",
    "CacheEntry",
    "CacheStats",
    "check_cache_before_llm",
    "cache_llm_response",
    "CACHE_ENABLED",
    "SIMILARITY_THRESHOLD",
    "TTL_HOURS",
]
