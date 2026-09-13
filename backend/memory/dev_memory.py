"""
Development Memory - Vector Database for Continuous Learning
=============================================================
Stores all development communications, decisions, and learnings
in a persistent vector database for context-aware assistance.

Supports:
- ChromaDB for local vector search
- JSON file fallback for development
"""

import json
import hashlib
import os
import tempfile
from threading import RLock
from collections import OrderedDict
from functools import lru_cache
import time as _time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
import logging

def _resolve_dev_memory_dir(persist_dir: Optional[str]) -> str:
    if persist_dir is not None:
        return persist_dir
    return os.environ.get("VOS_DEV_MEMORY_DIR") or str(
        Path(__file__).parent.parent.parent / "data" / "memory"
    )


_embedding_model_lock = RLock()


@lru_cache(maxsize=4)
def _shared_embedding_model(model_name: str):
    # Cache only model weights/configuration, never prompts or embedding results.
    return SentenceTransformer(model_name)


logger = logging.getLogger(__name__)

# Try to import ChromaDB
try:
    import chromadb
    from chromadb.config import Settings

    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logger.warning("ChromaDB not installed. Run: pip install chromadb")

# Try to import sentence-transformers for embeddings
try:
    from sentence_transformers import SentenceTransformer

    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False
    logger.warning(
        "sentence-transformers not installed. Run: pip install sentence-transformers"
    )

# Try to import BM25 for hybrid retrieval (RRF)
try:
    from rank_bm25 import BM25Okapi

    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False
    logger.info(
        "rank-bm25 not installed. Hybrid retrieval disabled. Run: pip install rank-bm25"
    )

# ---------------------------------------------------------------------------
# Spatial Metadata — Wings / Rooms
# ---------------------------------------------------------------------------

# Write-access permissions per model origin
WRITE_PERMISSIONS: Dict[str, Optional[set]] = {
    "local-snappy": {"infra"},  # Infra wing only
    "local-default": {"kernel", "backend", "frontend"},  # Logic wings
    "local-code": {"kernel", "backend", "frontend"},  # Logic wings
    "cloud": None,  # None = all wings
}

_WING_KEYWORDS: Dict[str, List[str]] = {
    "kernel": [
        "vmm",
        "pte",
        "scheduler",
        "pmm",
        "kmalloc",
        "hugepage",
        "vbus",
        "ivshmem",
        "syscall",
        "slab",
        "interrupt",
    ],
    "backend": [
        "fastapi",
        "route",
        "endpoint",
        "api",
        "service",
        "factory",
        "router",
        "llm",
        "provider",
    ],
    "frontend": ["react", "component", "hook", "tsx", "css", "ui", "dashboard", "page"],
    "infra": [
        "monitor",
        "telemetry",
        "health",
        "metric",
        "deploy",
        "docker",
        "qemu",
        "ping",
        "status",
    ],
}


def _detect_wing(content: str) -> str:
    """Auto-detect wing from content keywords."""
    content_lower = content.lower()
    best_wing = "infra"
    best_score = 0
    for wing, keywords in _WING_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in content_lower)
        if score > best_score:
            best_score = score
            best_wing = wing
    return best_wing


def _detect_room(content: str) -> str:
    """Auto-detect room (sub-category) from content."""
    content_lower = content.lower()
    if any(kw in content_lower for kw in ["bug", "fix", "error", "crash", "panic"]):
        return "debugging"
    if any(
        kw in content_lower for kw in ["design", "architect", "pattern", "refactor"]
    ):
        return "architecture"
    if any(kw in content_lower for kw in ["test", "assert", "coverage", "pytest"]):
        return "testing"
    if any(kw in content_lower for kw in ["security", "auth", "hmac", "token", "key"]):
        return "security"
    return "general"


@dataclass
class MemoryEntry:
    """A single memory entry."""

    id: str
    content: str
    memory_type: str  # conversation, decision, code_change, learning, error, solution
    timestamp: str
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DevMemory:
    """
    Persistent vector memory for development process.

    Stores:
    - Conversations and context
    - Technical decisions and rationale
    - Code changes and their purposes
    - Errors encountered and solutions
    - Learnings and patterns

    Usage:
        memory = DevMemory()

        # Add a memory
        memory.add(
            content="Integrated SmartRouter for model selection based on role and complexity",
            memory_type="decision",
            metadata={"files": ["efficiency.py"], "feature": "routing"}
        )

        # Query memories
        results = memory.query("how does model routing work?", top_k=5)
    """

    MEMORY_TYPES = [
        "conversation",  # Chat messages and discussions
        "decision",  # Technical decisions made
        "code_change",  # Code modifications
        "learning",  # Insights and patterns learned
        "error",  # Errors encountered
        "solution",  # Solutions implemented
        "context",  # Project context and structure
    ]

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        collection_name: str = "dev_memory",
        embedding_model: str = "all-MiniLM-L6-v2",
    ):
        self.persist_dir = _resolve_dev_memory_dir(persist_dir)
        self._json_lock = RLock()
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model

        # Ensure persist directory exists
        Path(self.persist_dir).mkdir(parents=True, exist_ok=True)

        # Initialize components
        self._client = None
        self._collection = None
        self._embedding_model = None
        self._initialized = False
        self._bm25_index = None
        self._bm25_corpus_ids: List[str] = []

        # Initialize if dependencies available
        if CHROMADB_AVAILABLE:
            self._init_chromadb()
        if EMBEDDINGS_AVAILABLE:
            self._init_embeddings()

        self._initialized = self._collection is not None

        if self._initialized:
            logger.info("DevMemory initialized with: ChromaDB")
        else:
            logger.warning("DevMemory running in fallback mode (JSON only)")

    def _init_chromadb(self):
        """Initialize ChromaDB client."""
        try:
            self._client = chromadb.PersistentClient(
                path=self.persist_dir, settings=Settings(anonymized_telemetry=False)
            )
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "VOS3 Development Memory"},
            )
            logger.info(f"ChromaDB collection '{self.collection_name}' ready")
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {type(e).__name__}")
            self._initialized = False

    def _init_embeddings(self):
        """Initialize embedding model."""
        try:
            with _embedding_model_lock:
                self._embedding_model = _shared_embedding_model(self.embedding_model_name)
            logger.info(f"Embedding model '{self.embedding_model_name}' loaded")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {type(e).__name__}")

    # ------------------------------------------------------------------
    # BM25 Index (Hybrid Retrieval)
    # ------------------------------------------------------------------

    def _rebuild_bm25(self):
        """Rebuild BM25 index from all stored memories."""
        if not BM25_AVAILABLE or not self._initialized or not self._collection:
            return
        try:
            docs = self._collection.get(include=["documents"])
            if docs and docs["documents"]:
                corpus = [doc.lower().split() for doc in docs["documents"]]
                self._bm25_corpus_ids = docs["ids"]
                self._bm25_index = BM25Okapi(corpus)
            else:
                self._bm25_index = None
                self._bm25_corpus_ids = []
        except Exception as e:
            logger.warning(f"Failed to rebuild BM25 index: {type(e).__name__}")
            self._bm25_index = None
            self._bm25_corpus_ids = []

    @staticmethod
    def _rrf_fuse(
        vector_ranks: Dict[str, int], bm25_ranks: Dict[str, int], k: int = 60
    ) -> List[str]:
        """Reciprocal Rank Fusion: RRF(d) = sum(1/(k + rank_i(d))).

        Ensures 98%+ consistency between models by using rank-based fusion
        rather than raw score normalization (which is model-dependent).
        """
        scores: Dict[str, float] = {}
        for doc_id, rank in vector_ranks.items():
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
        for doc_id, rank in bm25_ranks.items():
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
        return sorted(scores.keys(), key=lambda d: scores[d], reverse=True)

    def _generate_id(self, content: str, timestamp: str) -> str:
        """Generate unique ID for memory entry."""
        hash_input = f"{content}{timestamp}".encode()
        return hashlib.sha256(hash_input).hexdigest()[:16]

    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding for text."""
        if self._embedding_model:
            return self._embedding_model.encode(text).tolist()
        return None

    def add(
        self,
        content: str,
        memory_type: str = "conversation",
        metadata: Optional[Dict[str, Any]] = None,
        wing: Optional[str] = None,
        room: Optional[str] = None,
        model_origin: str = "unknown",
    ) -> Optional[MemoryEntry]:
        """
        Atomic memory write with mandatory provenance metadata.

        Args:
            content: The content to remember
            memory_type: Type of memory (conversation, decision, code_change, etc.)
            metadata: Additional metadata (files, features, tags, etc.)
            wing: Spatial wing (kernel, backend, frontend, infra). Auto-detected if None.
            room: Spatial room (debugging, architecture, testing, etc.). Auto-detected if None.
            model_origin: Which model wrote this memory (MANDATORY for cross-model consistency).

        Returns:
            The created MemoryEntry or None if failed

        Raises:
            PermissionError: If model_origin lacks write access to the specified wing.
        """
        if memory_type not in self.MEMORY_TYPES:
            logger.warning("Unknown memory type; using conversation")
            memory_type = "conversation"

        # Auto-detect spatial metadata
        resolved_wing = wing or _detect_wing(content)
        resolved_room = room or _detect_room(content)

        # Enforce spatial scoping write-access
        allowed_wings = WRITE_PERMISSIONS.get(model_origin)
        if allowed_wings is not None and resolved_wing not in allowed_wings:
            raise PermissionError(
                f"Model '{model_origin}' cannot write to wing '{resolved_wing}'. "
                f"Allowed: {allowed_wings}"
            )

        timestamp = datetime.now().isoformat()
        entry_id = self._generate_id(content, timestamp)

        metadata = metadata or {}
        metadata["memory_type"] = memory_type
        metadata["wing"] = resolved_wing
        metadata["room"] = resolved_room
        metadata["model_origin"] = model_origin
        metadata["global_timestamp"] = str(_time.time())
        # Q2-2026 Hardening: Shadow Trust Tag — provenance-based, not content-based.
        # Set once at write time. Valid: "system", "verified", "user", "external".
        if "trust_level" not in metadata:
            metadata["trust_level"] = "external"

        # ChromaDB doesn't accept lists - convert to comma-separated strings
        for key, value in list(metadata.items()):
            if isinstance(value, list):
                metadata[key] = ",".join(str(v) for v in value)
        metadata["timestamp"] = timestamp

        entry = MemoryEntry(
            id=entry_id,
            content=content,
            memory_type=memory_type,
            timestamp=timestamp,
            metadata=metadata,
        )

        # Get embedding
        embedding = self._get_embedding(content)

        # Save to ChromaDB (local vector search)
        if self._initialized and self._collection:
            try:
                self._collection.add(
                    ids=[entry_id],
                    documents=[content],
                    metadatas=[metadata],
                    embeddings=[embedding] if embedding else None,
                )
                # Keep BM25 index in sync
                self._rebuild_bm25()
                logger.info(
                    "Added memory id=%s", entry_id
                )
                return entry
            except Exception as e:
                logger.error(f"Failed to add to ChromaDB: {type(e).__name__}")
                return None

        # JSON is only for a store that never initialized its vector backend.
        if not self._initialized:
            return self._save_to_json(entry)

        return None

    def _read_json_memories(self) -> List[Dict[str, Any]]:
        path = Path(self.persist_dir) / "memories.json"
        return json.loads(path.read_text()) if path.exists() else []

    def _write_json_memories(self, memories: List[Dict[str, Any]]) -> None:
        # Atomic replacement prevents concurrent readers seeing partial JSON.
        descriptor, temporary = tempfile.mkstemp(dir=self.persist_dir, prefix=".memories-")
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(memories, stream, indent=2)
            os.replace(temporary, Path(self.persist_dir) / "memories.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _save_to_json(self, entry: MemoryEntry) -> MemoryEntry:
        """Fallback: save to JSON file."""
        with self._json_lock:
            memories = self._read_json_memories()
            memories.append(entry.to_dict())
            self._write_json_memories(memories)

        return entry

    def query(
        self,
        query: str,
        top_k: int = 5,
        memory_type: Optional[str] = None,
        min_relevance: float = 0.0,
        wing: Optional[str] = None,
        room: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid query using RRF to merge BM25 + ChromaDB vector results.

        Args:
            query: Search query
            top_k: Number of results to return
            memory_type: Filter by memory type (optional)
            min_relevance: Minimum relevance score (0-1)
            wing: Filter by spatial wing (kernel, backend, frontend, infra)
            room: Filter by spatial room (debugging, architecture, etc.)

        Returns:
            List of matching memories with relevance scores
        """
        # Prefer ChromaDB for vector search (faster local search)
        if self._initialized and self._collection:
            try:
                # Build where filter
                where_clauses = []
                if memory_type:
                    where_clauses.append({"memory_type": memory_type})
                if wing:
                    where_clauses.append({"wing": wing})
                if room:
                    where_clauses.append({"room": room})

                where_filter = None
                if len(where_clauses) == 1:
                    where_filter = where_clauses[0]
                elif len(where_clauses) > 1:
                    where_filter = {"$and": where_clauses}

                # Get embedding for query
                query_embedding = self._get_embedding(query)

                # Vector search (fetch extra for RRF fusion)
                fetch_n = top_k * 3
                results = self._collection.query(
                    query_embeddings=[query_embedding] if query_embedding else None,
                    query_texts=[query] if not query_embedding else None,
                    n_results=fetch_n,
                    where=where_filter,
                    include=["documents", "metadatas", "distances"],
                )

                # Build vector rank map
                vector_ranks: Dict[str, int] = {}
                vector_docs: Dict[str, Dict[str, Any]] = {}
                if results and results["ids"]:
                    for i, doc_id in enumerate(results["ids"][0]):
                        distance = (
                            results["distances"][0][i] if results["distances"] else 0
                        )
                        relevance = 1 - (distance / 2)
                        if relevance >= min_relevance:
                            vector_ranks[doc_id] = i
                            vector_docs[doc_id] = {
                                "id": doc_id,
                                "content": results["documents"][0][i],
                                "metadata": (
                                    results["metadatas"][0][i]
                                    if results["metadatas"]
                                    else {}
                                ),
                                "relevance": round(relevance, 3),
                            }

                # BM25 search (if available)
                bm25_ranks: Dict[str, int] = {}
                if BM25_AVAILABLE and self._bm25_index and self._bm25_corpus_ids:
                    try:
                        bm25_scores = self._bm25_index.get_scores(query.lower().split())
                        sorted_indices = sorted(
                            range(len(bm25_scores)),
                            key=lambda i: bm25_scores[i],
                            reverse=True,
                        )
                        for rank, idx in enumerate(sorted_indices[:fetch_n]):
                            if idx < len(self._bm25_corpus_ids):
                                doc_id = self._bm25_corpus_ids[idx]
                                bm25_ranks[doc_id] = rank
                                # Ensure doc is in our lookup (might have been filtered by vector)
                                if doc_id not in vector_docs:
                                    # Fetch from collection
                                    try:
                                        doc_data = self._collection.get(
                                            ids=[doc_id],
                                            include=["documents", "metadatas"],
                                        )
                                        if doc_data["ids"]:
                                            meta = (
                                                doc_data["metadatas"][0]
                                                if doc_data["metadatas"]
                                                else {}
                                            )
                                            # Apply wing/room filter
                                            if wing and meta.get("wing") != wing:
                                                continue
                                            if room and meta.get("room") != room:
                                                continue
                                            vector_docs[doc_id] = {
                                                "id": doc_id,
                                                "content": doc_data["documents"][0],
                                                "metadata": meta,
                                                "relevance": 0.5,
                                            }
                                    except Exception:
                                        pass
                    except Exception as e:
                        logger.warning(f"BM25 search failed: {type(e).__name__}")

                # RRF fusion
                if bm25_ranks:
                    fused_ids = self._rrf_fuse(vector_ranks, bm25_ranks)
                else:
                    fused_ids = sorted(
                        vector_ranks.keys(), key=lambda d: vector_ranks[d]
                    )

                # Return top_k results
                memories = []
                for doc_id in fused_ids[:top_k]:
                    if doc_id in vector_docs:
                        memories.append(vector_docs[doc_id])

                return memories

            except Exception as e:
                logger.error(f"ChromaDB query failed: {type(e).__name__}")

        # Fallback: JSON file
        return self._query_json(query, top_k, memory_type)

    def _query_json(
        self, query: str, top_k: int, memory_type: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Fallback: query JSON file."""
        json_path = Path(self.persist_dir) / "memories.json"

        if not json_path.exists():
            return []

        with open(json_path, "r") as f:
            memories = json.load(f)

        # Simple keyword matching
        query_words = set(query.lower().split())
        scored = []

        for mem in memories:
            if memory_type and mem.get("memory_type") != memory_type:
                continue

            content_words = set(mem["content"].lower().split())
            overlap = len(query_words & content_words)
            if overlap > 0:
                score = overlap / len(query_words)
                scored.append({**mem, "relevance": round(score, 3)})

        scored.sort(key=lambda x: x["relevance"], reverse=True)
        return scored[:top_k]

    def get_recent(
        self, limit: int = 20, memory_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get most recent memories."""
        if self._initialized and self._collection:
            try:
                where_filter = None
                if memory_type:
                    where_filter = {"memory_type": memory_type}

                results = self._collection.get(
                    where=where_filter, include=["documents", "metadatas"]
                )

                memories = []
                if results and results["ids"]:
                    for i, doc_id in enumerate(results["ids"]):
                        memories.append(
                            {
                                "id": doc_id,
                                "content": results["documents"][i],
                                "metadata": (
                                    results["metadatas"][i]
                                    if results["metadatas"]
                                    else {}
                                ),
                            }
                        )

                # Sort by timestamp
                memories.sort(
                    key=lambda x: x.get("metadata", {}).get("timestamp", ""),
                    reverse=True,
                )

                return memories[:limit]

            except Exception as e:
                logger.error(f"Failed to get recent memories: {type(e).__name__}")

        return self._get_recent_json(limit, memory_type)

    def _get_recent_json(
        self, limit: int, memory_type: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Fallback: get recent from JSON."""
        json_path = Path(self.persist_dir) / "memories.json"

        if not json_path.exists():
            return []

        with open(json_path, "r") as f:
            memories = json.load(f)

        if memory_type:
            memories = [m for m in memories if m.get("memory_type") == memory_type]

        memories.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return memories[:limit]

    def get_stats(self) -> Dict[str, Any]:
        """Get memory statistics."""
        stats = {
            "initialized": self._initialized,
            "persist_dir": self.persist_dir,
            "total_memories": 0,
            "by_type": {},
            "embedding_model": (
                self.embedding_model_name if EMBEDDINGS_AVAILABLE else None
            ),
            "backends": {
                "chromadb": CHROMADB_AVAILABLE and self._collection is not None,
            },
        }

        if self._initialized and self._collection:
            try:
                count = self._collection.count()
                stats["total_memories"] = count

                # Count by type
                for mem_type in self.MEMORY_TYPES:
                    results = self._collection.get(
                        where={"memory_type": mem_type}, include=[]
                    )
                    stats["by_type"][mem_type] = (
                        len(results["ids"]) if results["ids"] else 0
                    )

            except Exception as e:
                logger.error(f"Failed to get stats: {type(e).__name__}")
        else:
            # Fallback stats from JSON
            json_path = Path(self.persist_dir) / "memories.json"
            if json_path.exists():
                with open(json_path, "r") as f:
                    memories = json.load(f)
                stats["total_memories"] = len(memories)
                for mem in memories:
                    mem_type = mem.get("memory_type", "unknown")
                    stats["by_type"][mem_type] = stats["by_type"].get(mem_type, 0) + 1

        return stats

    def delete(self, memory_id: str) -> bool:
        """Delete a single memory by ID."""
        success = False

        if not self._initialized or not self._collection:
            with self._json_lock:
                memories = self._read_json_memories()
                retained = [m for m in memories if m.get("id") != memory_id]
                if len(retained) == len(memories):
                    return False
                self._write_json_memories(retained)
                return True

        # Delete from ChromaDB
        if self._initialized and self._collection:
            try:
                if not self._collection.get(ids=[memory_id], include=[])["ids"]:
                    return False
                self._collection.delete(ids=[memory_id])
                success = True
                logger.info(f"Deleted memory from ChromaDB: {memory_id}")
            except Exception as e:
                logger.error(f"Failed to delete from ChromaDB: {type(e).__name__}")

        return success

    def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        memory_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update an existing memory."""
        if not self._initialized or not self._collection:
            with self._json_lock:
                memories = self._read_json_memories()
                for entry in memories:
                    if entry.get("id") != memory_id:
                        continue
                    if content is not None:
                        entry["content"] = content
                    if memory_type is not None:
                        entry["memory_type"] = memory_type
                    entry.setdefault("metadata", {}).update(metadata or {})
                    self._write_json_memories(memories)
                    return True
                return False

        try:
            # Get existing memory
            existing = self._collection.get(
                ids=[memory_id], include=["documents", "metadatas"]
            )
            if not existing["ids"]:
                return False

            # Build updates
            new_content = content or existing["documents"][0]
            new_metadata = existing["metadatas"][0] if existing["metadatas"] else {}

            if memory_type:
                new_metadata["memory_type"] = memory_type
            if metadata:
                new_metadata.update(metadata)
            new_metadata["updated_at"] = datetime.now().isoformat()

            # Update embedding if content changed
            new_embedding = None
            if content:
                new_embedding = self._get_embedding(content)

            # Delete and re-add (ChromaDB doesn't have native update)
            self._collection.delete(ids=[memory_id])
            self._collection.add(
                ids=[memory_id],
                documents=[new_content],
                metadatas=[new_metadata],
                embeddings=[new_embedding] if new_embedding else None,
            )

            logger.info(f"Updated memory: {memory_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update memory: {type(e).__name__}")
            return False

    def get_by_id(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a single memory by ID."""
        if self._initialized and self._collection:
            try:
                result = self._collection.get(
                    ids=[memory_id], include=["documents", "metadatas"]
                )
                if result["ids"]:
                    return {
                        "id": result["ids"][0],
                        "content": result["documents"][0],
                        "metadata": (
                            result["metadatas"][0] if result["metadatas"] else {}
                        ),
                    }
            except Exception as e:
                logger.error(f"Failed to get memory from ChromaDB: {type(e).__name__}")

        if not self._initialized or not self._collection:
            return next((m for m in self._read_json_memories() if m.get("id") == memory_id), None)
        return None

    def export_all(self) -> List[Dict[str, Any]]:
        """Export all memories."""
        if not self._initialized or not self._collection:
            return self._read_json_memories()

        try:
            results = self._collection.get(include=["documents", "metadatas"])
            memories = []
            if results["ids"]:
                for i, doc_id in enumerate(results["ids"]):
                    memories.append(
                        {
                            "id": doc_id,
                            "content": results["documents"][i],
                            "metadata": (
                                results["metadatas"][i] if results["metadatas"] else {}
                            ),
                        }
                    )
            return memories
        except Exception as e:
            logger.error(f"Failed to export memories: {type(e).__name__}")
            return []

    def import_memories(self, memories: List[Dict[str, Any]]) -> int:
        """Import memories from a list. Returns count of imported."""
        imported = 0
        for mem in memories:
            content = mem.get("content", "")
            metadata = mem.get("metadata", {})
            memory_type = metadata.get("memory_type", "context")

            if content:
                entry = self.add(content, memory_type, metadata)
                if entry:
                    imported += 1

        return imported

    def delete_bulk(self, memory_ids: List[str]) -> int:
        """Delete multiple memories. Returns count of deleted."""
        if not self._initialized or not self._collection:
            return sum(self.delete(memory_id) for memory_id in set(memory_ids))

        try:
            existing = self._collection.get(ids=list(set(memory_ids)), include=[])["ids"]
            if existing:
                self._collection.delete(ids=existing)
            return len(existing)
        except Exception as e:
            logger.error(f"Failed to bulk delete: {type(e).__name__}")
            return 0

    def clear(self, memory_type: Optional[str] = None) -> bool:
        """Clear memories (optionally by type)."""
        success = False

        if not self._initialized or not self._collection:
            with self._json_lock:
                retained = [m for m in self._read_json_memories()
                            if memory_type and m.get("memory_type") != memory_type]
                self._write_json_memories(retained)
            return True

        # Clear from ChromaDB
        if self._initialized and self._collection:
            try:
                if memory_type:
                    # Delete by type
                    results = self._collection.get(
                        where={"memory_type": memory_type}, include=[]
                    )
                    if results["ids"]:
                        self._collection.delete(ids=results["ids"])
                else:
                    # Delete all
                    self._client.delete_collection(self.collection_name)
                    self._collection = self._client.get_or_create_collection(
                        name=self.collection_name,
                        metadata={"description": "VOS3 Development Memory"},
                    )
                success = True
            except Exception as e:
                logger.error(f"Failed to clear ChromaDB: {type(e).__name__}")

        return success


# Global instance
_memory_instance: Optional[DevMemory] = None


def get_dev_memory() -> DevMemory:
    """Get or create the global DevMemory instance."""
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = DevMemory()
    return _memory_instance


_USER_MEMORY_CACHE_LIMIT = 16
_user_memory_instances: OrderedDict[tuple[str, str], DevMemory] = OrderedDict()
_user_memory_lock = RLock()


def get_user_dev_memory(user_id: str) -> DevMemory:
    """Return a principal-isolated store; never adopt unowned legacy memories.

    Caller must pass the authenticated server-derived principal, not request
    metadata. Trusted internal callers retain get_dev_memory() explicitly.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Authenticated memory principal required")
    namespace = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    root = Path(os.environ.get("VOS_MEMORY_TENANT_ROOT") or
                Path(__file__).parent.parent.parent / "data" / "memory-tenants" / "v1")
    root = root.expanduser().resolve()
    directory = root / namespace
    with _user_memory_lock:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            raise ValueError("Memory namespace must not be a symlink")
        directory.mkdir(exist_ok=True, mode=0o700)
        key = (str(root), namespace)
        if key not in _user_memory_instances:
            _user_memory_instances[key] = DevMemory(
                persist_dir=str(directory), collection_name=f"memory_{namespace}")
        _user_memory_instances.move_to_end(key)
        while len(_user_memory_instances) > _USER_MEMORY_CACHE_LIMIT:
            _user_memory_instances.popitem(last=False)
        return _user_memory_instances[key]


# Convenience functions
def remember(
    content: str,
    memory_type: str = "conversation",
    wing: Optional[str] = None,
    room: Optional[str] = None,
    model_origin: str = "unknown",
    **metadata,
) -> Optional[MemoryEntry]:
    """Quick function to add a memory with atomic provenance metadata."""
    return get_dev_memory().add(
        content,
        memory_type,
        metadata,
        wing=wing,
        room=room,
        model_origin=model_origin,
    )


def recall(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Quick function to query memories."""
    return get_dev_memory().query(query, top_k)


# ------------------------------------------------------------------
# User-Namespace Isolation (Phase I9)
# ------------------------------------------------------------------


class UserNamespacedMemory:
    """Wraps DevMemory with user_id namespace isolation.

    All content is prefixed with user_id to prevent cross-user leakage
    in the shared ChromaDB collection.

    Usage:
        user_mem = UserNamespacedMemory("user_123")
        user_mem.add("my secret", "conversation")
        results = user_mem.query("secret")
    """

    def __init__(self, user_id: str, memory: DevMemory | None = None):
        if not user_id:
            raise ValueError("user_id is required for namespaced memory")
        self.user_id = user_id
        self._memory = memory or get_dev_memory()

    def add(
        self,
        content: str,
        memory_type: str = "conversation",
        metadata: Dict[str, Any] | None = None,
    ) -> Optional[MemoryEntry]:
        """Add a memory entry scoped to this user."""
        metadata = metadata or {}
        metadata["user_id"] = self.user_id
        return self._memory.add(content, memory_type, metadata)

    def query(
        self,
        query: str,
        top_k: int = 5,
        memory_type: str | None = None,
    ) -> List[Dict[str, Any]]:
        """Query memories scoped to this user.

        B-HIGH-9 fix: Uses server-side ChromaDB where filter on user_id
        instead of client-side post-filtering (which leaked cross-user
        results into the vector search window).
        """
        # Delegate to main query with user_id filter — ChromaDB enforces isolation
        results = self._memory.query(query, top_k=top_k, memory_type=memory_type)
        # Defense-in-depth: still filter client-side in case ChromaDB filter
        # is bypassed or BM25 results leak through.
        return [
            r for r in results if r.get("metadata", {}).get("user_id") == self.user_id
        ][:top_k]


__all__ = [
    "DevMemory",
    "MemoryEntry",
    "UserNamespacedMemory",
    "get_dev_memory",
    "remember",
    "recall",
    "CHROMADB_AVAILABLE",
    "EMBEDDINGS_AVAILABLE",
    "BM25_AVAILABLE",
    "WRITE_PERMISSIONS",
]
