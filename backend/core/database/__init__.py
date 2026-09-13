"""
backend/core/database/ — local storage layer.

W5.1 — sqlite_setup: SQLAlchemy declarative models that mirror the
Convex schema in frontend/convex/schema.ts.

W5.2 — vector_setup: persistent ChromaDB client + offline embedder
for local RAG / semantic search.

P3.2 — sqlcipher_setup: encrypted-at-rest SQLite engine factory,
gated on `VOS_PROFILE=fortress` (or any non-empty
`VOS3_COMPLIANCE_KEY`). Kept as a sibling module so plain-SQLite
community deployments don't pay the SQLCipher driver probe cost
on import.

Activated when `VOS3_LOCALITY_PREFERENCE=local-first` is set; see
backend/core/repositories/__init__.py for the locality dispatch.
"""

from .sqlite_setup import (
    Base,
    User,
    Project,
    Build,
    ChatSession,
    ChatSessionMessage,
    AppInstallation,
    get_engine,
    get_session,
    init_db,
    resolve_db_path,
)
from .sqlcipher_setup import (
    ComplianceKeyMissing,
    SqlCipherUnavailable,
    WrongComplianceKey,
    detect_driver,
    fortress_active,
    make_encrypted_engine,
    resolve_compliance_key,
)
from .vector_setup import (
    EMBEDDING_DIM,
    Embedder,
    resolve_chroma_path,
    get_chroma_client,
    chroma_unavailable_reason,
    get_embedder,
    init_vector_db,
)

__all__ = [
    # W5.1
    "Base",
    "User",
    "Project",
    "Build",
    "ChatSession",
    "ChatSessionMessage",
    "AppInstallation",
    "get_engine",
    "get_session",
    "init_db",
    "resolve_db_path",
    # P3.2 — SQLCipher fortress profile
    "ComplianceKeyMissing",
    "SqlCipherUnavailable",
    "WrongComplianceKey",
    "detect_driver",
    "fortress_active",
    "make_encrypted_engine",
    "resolve_compliance_key",
    # W5.2
    "EMBEDDING_DIM",
    "Embedder",
    "resolve_chroma_path",
    "get_chroma_client",
    "chroma_unavailable_reason",
    "get_embedder",
    "init_vector_db",
]
