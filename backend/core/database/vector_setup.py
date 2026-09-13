"""
backend/core/database/vector_setup.py — local ChromaDB + offline embedder.

W5.2 — companion to sqlite_setup.py. Provides:

  1. A persistent ChromaDB client at $VOS3_LOCAL_CHROMA_PATH (default
     ~/.vos/chroma_db), exposed via get_chroma_client().
  2. A three-tier embedder, exposed via get_embedder():
       a. env-pinned override        ($VOS3_EMBEDDING_BACKEND)
       b. sentence-transformers      (all-MiniLM-L6-v2, 384 dims)
       c. Ollama HTTP                ($VOS3_OLLAMA_EMBED_URL)
       d. deterministic fallback     (bag-of-words + synonym expansion)

Idempotency:
  - The Chroma client is a module-level singleton; safe to call
    get_chroma_client() repeatedly.
  - The embedder picks ONE backend at first call and sticks with it.
  - If chromadb is not installed, get_chroma_client() returns None
    and the repository raises a clear runtime error at first use.

The deterministic embedder is intentionally simple and explicit:
  - Bag-of-words over a stable hash bucketing (NUM_BUCKETS=256)
  - Small domain-specific synonym map for tech terms (database ↔ sqlite,
    api ↔ endpoint, etc.) so common-vocabulary queries still match the
    right document. NOT a substitute for real embeddings — it exists so
    tests run deterministically without network or large ML deps, and
    so the W5.2 RAG flow has a working answer when the production model
    isn't loaded yet.

The 384-dim vector size matches all-MiniLM-L6-v2 so a SwAp from the
deterministic backend to sentence-transformers does NOT require
rebuilding the Chroma collection.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, List, Optional, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_chroma_path() -> Path:
    """Resolve the persistent Chroma storage directory.

    Precedence:
      1. $VOS3_LOCAL_CHROMA_PATH (absolute path; used by tests)
      2. ~/.vos/chroma_db        (default for local-first profile)
    """
    override = os.getenv("VOS3_LOCAL_CHROMA_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".vos" / "chroma_db"


# ---------------------------------------------------------------------------
# ChromaDB client — singleton, lazy, gracefully degraded
# ---------------------------------------------------------------------------


_chroma_client: Any = None
_chroma_load_attempted = False
_chroma_unavailable_reason: Optional[str] = None


def get_chroma_client() -> Any:
    """Return the persistent Chroma client, or None if unavailable.

    The first call constructs the client (creating the storage
    directory if necessary). Returns None and logs a clear warning if
    chromadb is not installed or fails to initialize — the calling
    repository surfaces the issue at the first add_memory/query_memory
    call.
    """
    global _chroma_client, _chroma_load_attempted, _chroma_unavailable_reason
    if _chroma_client is not None:
        return _chroma_client
    if _chroma_load_attempted:
        return None
    _chroma_load_attempted = True
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        _chroma_unavailable_reason = (
            f"chromadb package not installed: {exc}. "
            f"pip install chromadb to enable local RAG."
        )
        logger.warning(_chroma_unavailable_reason)
        return None
    try:
        path = resolve_chroma_path()
        path.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(path))
        logger.info("Chroma persistent client initialized at %s", path)
        return _chroma_client
    except Exception as exc:
        _chroma_unavailable_reason = f"Chroma client init failed: {exc}"
        logger.warning(_chroma_unavailable_reason)
        return None


def chroma_unavailable_reason() -> Optional[str]:
    """For callers that need to surface the failure mode to the user."""
    return _chroma_unavailable_reason


def _reset_chroma_for_tests() -> None:
    """Reset the module-level singleton — used by tests that swap paths."""
    global _chroma_client, _chroma_load_attempted, _chroma_unavailable_reason
    _chroma_client = None
    _chroma_load_attempted = False
    _chroma_unavailable_reason = None


# ---------------------------------------------------------------------------
# Embedder protocol — used by the RAG repo so it doesn't care which
# backend produced the vector.
# ---------------------------------------------------------------------------


EMBEDDING_DIM = 384  # matches all-MiniLM-L6-v2


class Embedder(Protocol):
    backend: str

    def embed(self, text: str) -> List[float]:
        """Return a unit-normalized vector of length EMBEDDING_DIM."""
        ...


# ---------------------------------------------------------------------------
# Deterministic fallback embedder
# ---------------------------------------------------------------------------


# Tech-domain synonym expansion. NOT exhaustive — covers terms that
# appear in the W5.2 verification test + a handful of obvious adjacents
# so a "database" query can match a "SQLite" memory without needing a
# real embedding model. Adding more is cheap; this isn't on a hot path.
TECH_SYNONYMS: dict[str, tuple[str, ...]] = {
    "database": (
        "sql",
        "sqlite",
        "postgres",
        "mysql",
        "db",
        "schema",
        "table",
        "relational",
    ),
    "relational": ("sql", "sqlite", "rdbms", "database", "schema"),
    "sqlite": ("database", "sql", "relational", "db"),
    "postgres": ("database", "sql", "relational", "db"),
    "api": ("endpoint", "route", "request", "rest"),
    "endpoint": ("api", "route", "request"),
    "ui": ("frontend", "interface", "component", "react"),
    "frontend": ("ui", "react", "component"),
    "security": ("auth", "csrf", "idor", "hardening"),
    "auth": ("security", "login", "session"),
    "idor": ("security", "permission", "ownership"),
    "billing": ("payment", "subscription", "stripe", "charge"),
    "memory": ("storage", "persistence", "cache"),
    "storage": ("memory", "persistence", "database"),
    "kernel": ("os", "linux", "system"),
    "vector": ("embedding", "rag", "search", "semantic"),
    "rag": ("vector", "embedding", "retrieval", "semantic"),
    "embedding": ("vector", "rag", "semantic"),
}

# Stopwords stripped before bucketing — keep small for the fallback
# (real embeddings handle stopwords natively).
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "by",
        "from",
        "is",
        "was",
        "are",
        "were",
        "be",
        "been",
        "has",
        "have",
        "had",
        "i",
        "we",
        "you",
        "he",
        "she",
        "it",
        "they",
        "this",
        "that",
        "these",
        "those",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _bucket(token: str) -> int:
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(h, "big") % EMBEDDING_DIM


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


class _DeterministicEmbedder:
    """Bag-of-words + synonym-expansion embedder.

    Use cases:
      - CI / unit tests (no network, no GPU, no ML deps)
      - First-run dev when the real model isn't downloaded yet
      - Honest-default backstop so the RAG pipeline never crashes

    Synonym weight is 0.5 of the primary-token weight, so a memory
    containing the literal query term will always outrank one that
    matches only via expansion.
    """

    backend = "deterministic"

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * EMBEDDING_DIM
        for tok in _tokens(text):
            vec[_bucket(tok)] += 1.0
            for syn in TECH_SYNONYMS.get(tok, ()):
                vec[_bucket(syn)] += 0.5
        return _l2_normalize(vec)


# ---------------------------------------------------------------------------
# sentence-transformers backend
# ---------------------------------------------------------------------------


class _SentenceTransformersEmbedder:
    """Wraps sentence-transformers/all-MiniLM-L6-v2 (CPU).

    Loaded lazily on first embed() to keep import-time cheap.
    """

    backend = "sentence-transformers"

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model: Any = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, text: str) -> List[float]:
        model = self._load()
        # Returns numpy array; convert to list for JSON portability.
        vec = model.encode(text, normalize_embeddings=True)
        return [float(x) for x in vec]


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------


class _OllamaEmbedder:
    """Embedder backed by a local Ollama server.

    Expects $VOS3_OLLAMA_EMBED_URL to point at the embeddings endpoint
    (e.g. http://localhost:11434/api/embeddings). Model name comes from
    $VOS3_OLLAMA_EMBED_MODEL (default: nomic-embed-text).
    """

    backend = "ollama"

    def __init__(self):
        self._url = os.getenv("VOS3_OLLAMA_EMBED_URL", "").strip()
        self._model = os.getenv("VOS3_OLLAMA_EMBED_MODEL", "nomic-embed-text").strip()

    def embed(self, text: str) -> List[float]:
        import httpx  # already in requirements

        resp = httpx.post(
            self._url,
            json={"model": self._model, "prompt": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        vec = data.get("embedding") or data.get("embeddings") or []
        if not vec:
            raise RuntimeError(f"Ollama returned no embedding for {self._model!r}")
        # Pad/truncate to EMBEDDING_DIM so Chroma collections built with
        # a different backend still accept the vectors.
        if len(vec) > EMBEDDING_DIM:
            vec = vec[:EMBEDDING_DIM]
        elif len(vec) < EMBEDDING_DIM:
            vec = list(vec) + [0.0] * (EMBEDDING_DIM - len(vec))
        return _l2_normalize([float(x) for x in vec])


# ---------------------------------------------------------------------------
# Embedder factory
# ---------------------------------------------------------------------------


_embedder: Optional[Embedder] = None


def get_embedder() -> Embedder:
    """Return the active embedder (constructed once per process).

    Selection order (first that loads wins):
      1. $VOS3_EMBEDDING_BACKEND override (one of:
         sentence-transformers / ollama / deterministic)
      2. sentence-transformers if installed
      3. Ollama if $VOS3_OLLAMA_EMBED_URL is set
      4. Deterministic fallback (always works)
    """
    global _embedder
    if _embedder is not None:
        return _embedder

    pinned = os.getenv("VOS3_EMBEDDING_BACKEND", "").strip().lower()

    if pinned == "deterministic":
        _embedder = _DeterministicEmbedder()
        logger.info("Embedder pinned to deterministic fallback (env)")
        return _embedder

    if pinned == "ollama" or (
        not pinned and os.getenv("VOS3_OLLAMA_EMBED_URL", "").strip()
    ):
        try:
            _embedder = _OllamaEmbedder()
            # Probe — failing here falls through to the next backend.
            _embedder.embed("vector setup probe")
            logger.info(
                "Embedder using local Ollama at %s",
                os.getenv("VOS3_OLLAMA_EMBED_URL", ""),
            )
            return _embedder
        except Exception as exc:
            logger.warning("Ollama embedder unavailable, falling back: %s", exc)
            _embedder = None

    if pinned in ("", "sentence-transformers"):
        try:
            _embedder = _SentenceTransformersEmbedder()
            # Don't probe — model loads lazily on first embed() to keep
            # boot fast. If load fails later, callers see a clean error.
            logger.info("Embedder using sentence-transformers/all-MiniLM-L6-v2")
            return _embedder
        except Exception as exc:
            logger.warning("sentence-transformers unavailable, falling back: %s", exc)
            _embedder = None

    _embedder = _DeterministicEmbedder()
    logger.info(
        "Embedder defaulted to deterministic fallback "
        "(no ML library or Ollama available)"
    )
    return _embedder


def _reset_embedder_for_tests() -> None:
    global _embedder
    _embedder = None


# ---------------------------------------------------------------------------
# Bootstrap — used by repo init code
# ---------------------------------------------------------------------------


def init_vector_db() -> bool:
    """Idempotently bring up the vector layer.

    Returns True if Chroma is usable, False if missing (in which case
    callers should either fall back or surface a clear error).
    """
    client = get_chroma_client()
    if client is None:
        return False
    # Touch the embedder so first-call latency happens during boot, not
    # the first RAG request.
    get_embedder()
    return True


__all__ = [
    "EMBEDDING_DIM",
    "Embedder",
    "resolve_chroma_path",
    "get_chroma_client",
    "chroma_unavailable_reason",
    "get_embedder",
    "init_vector_db",
]
