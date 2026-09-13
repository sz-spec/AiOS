"""
VOS3 Repository Pattern
=======================
Abstracts data persistence to support both in-memory (dev/test) and
Convex (production) backends with identical interfaces.

Usage:
    # Default: in-memory (backward compatible)
    from core.repositories import get_user_repository
    repo = get_user_repository()

    # Production: Convex
    from core.repositories import get_user_repository
    repo = get_user_repository(backend="convex")
"""

import os
from typing import Literal

from .base import (
    UserRepository,
    OrganizationRepository,
    MembershipRepository,
    ApiKeyRepository,
    AuditLogRepository,
    EntityRepository,
    RecordRepository,
    ApprovalRepository,
    AlertRepository,
    WorkflowRepository,
    WorkflowExecutionRepository,
    ProjectMemoryRepository,
)
from .memory import (
    InMemoryUserRepository,
    InMemoryOrganizationRepository,
    InMemoryMembershipRepository,
    InMemoryApiKeyRepository,
    InMemoryAuditLogRepository,
    InMemoryEntityRepository,
    InMemoryRecordRepository,
    InMemoryApprovalRepository,
    InMemoryAlertRepository,
    InMemoryWorkflowRepository,
    InMemoryWorkflowExecutionRepository,
    InMemoryProjectMemoryRepository,
)

# W3.2d — async-native repositories for FastAPI route handlers.
# Surfaced as factory functions to keep the public API stable when
# implementation classes evolve.
from .async_convex import (
    AsyncUserSyncRepository,
    AsyncAuditLogRepository,
    AsyncWebhookSeenRepository,
    AsyncDeveloperRepository,
    AsyncAppVersionRepository,
    AsyncAppPermissionsRepository,
    AsyncChatSessionRepository,
    AsyncDeploymentRepository,
    get_async_user_sync_repository as _convex_get_user_sync,
    get_async_audit_log_repository,
    get_async_webhook_seen_repository,
    get_async_developer_repository,
    get_async_app_version_repository,
    get_async_app_permissions_repository,
    get_async_chat_session_repository as _convex_get_chat_session,
    get_async_deployment_repository,
)

# W4.1 — sync pragmatic repositories for service-layer modules.
from .service_repos import (
    ConvexAppInstallationRepository,
    ConvexCheckpointRepository,
    ConvexCollaboratorRepository,
    ConvexMarketplaceAppsRepository,
    ConvexDeveloperPayoutRepository,
    get_app_installation_repository as _convex_get_app_installation,
    get_checkpoint_repository,
    get_collaborator_repository,
    get_marketplace_apps_repository,
    get_developer_payout_repository,
)

# W5.1 — local-first SQLite implementations (community profile).
# W5.2 — local Chroma RAG repository.
from .sqlite import (
    SQLiteAppInstallationRepository,
    SQLiteUserSyncRepository,
    SQLiteChatSessionRepository,
    LocalChromaMemoryRepository,
    get_sqlite_app_installation_repository,
    get_sqlite_user_sync_repository,
    get_sqlite_chat_session_repository,
    get_local_chroma_memory_repository,
)

# ---------------------------------------------------------------------------
# W5.1 — Locality dispatch
#
# The three factory functions below pick a backend based on
# `VOS3_LOCALITY_PREFERENCE`:
#
#   local-first  → SQLite (offline community profile)
#   anything else (or unset) → Convex (cloud/enterprise profile)
#
# This is the same env var read by backend/src/efficiency/router.py for
# the LLM-routing layer, so a single env flip switches BOTH the LLM
# locality and the storage locality. The factories preserve the exact
# method contracts established in W3.2d/W4.1, so call sites do not
# need to branch on the backend.
# ---------------------------------------------------------------------------


def _is_local_first() -> bool:
    return os.getenv("VOS3_LOCALITY_PREFERENCE", "").strip().lower() == "local-first"


def get_app_installation_repository():
    """W4.1 + W5.1 — dispatches to SQLite under local-first profile."""
    if _is_local_first():
        return get_sqlite_app_installation_repository()
    return _convex_get_app_installation()


def get_async_user_sync_repository():
    """W3.2d + W5.1 — dispatches to SQLite under local-first profile."""
    if _is_local_first():
        return get_sqlite_user_sync_repository()
    return _convex_get_user_sync()


def get_async_chat_session_repository():
    """W3.2d + W5.1 — dispatches to SQLite under local-first profile."""
    if _is_local_first():
        return get_sqlite_chat_session_repository()
    return _convex_get_chat_session()


def get_memory_repository():
    """W5.2 — semantic memory (RAG) repository.

    Under `VOS3_LOCALITY_PREFERENCE=local-first` returns the
    LocalChromaMemoryRepository (persistent on-disk Chroma + offline
    embedder). Under the cloud profile this raises explicitly — the
    Convex side does not yet host a vector store, so the call site
    must either run local-first or skip the semantic-recall path.

    This is intentionally a NEW factory (distinct from the
    structural `get_project_memory_repository` ABC dispatch in this
    same module) — the two layers handle different concerns:

      - get_project_memory_repository  → ADRs, tagged metadata,
                                          structured CRUD (ABC)
      - get_memory_repository           → semantic similarity search,
                                          embedding-backed recall (W5.2)
    """
    if _is_local_first():
        return get_local_chroma_memory_repository()
    raise NotImplementedError(
        "Semantic memory repository is local-first only at this stage. "
        "Set VOS3_LOCALITY_PREFERENCE=local-first to enable the local "
        "Chroma-backed RAG store. A cloud-hosted vector store is not "
        "wired yet."
    )


# Type alias for backend selection
BackendType = Literal["memory", "convex"]


def _get_backend() -> BackendType:
    """Determine backend from environment."""
    backend = os.getenv("VOS3_STORAGE_BACKEND", "convex").lower()
    if backend not in ("memory", "convex"):
        return "convex"
    return backend


def get_user_repository(backend: BackendType = None) -> UserRepository:
    """Factory function for user repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexUserRepository

        return ConvexUserRepository()
    return InMemoryUserRepository()


def get_organization_repository(backend: BackendType = None) -> OrganizationRepository:
    """Factory function for organization repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexOrganizationRepository

        return ConvexOrganizationRepository()
    return InMemoryOrganizationRepository()


def get_membership_repository(backend: BackendType = None) -> MembershipRepository:
    """Factory function for membership repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexMembershipRepository

        return ConvexMembershipRepository()
    return InMemoryMembershipRepository()


def get_api_key_repository(backend: BackendType = None) -> ApiKeyRepository:
    """Factory function for API key repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexApiKeyRepository

        return ConvexApiKeyRepository()
    return InMemoryApiKeyRepository()


def get_audit_log_repository(backend: BackendType = None) -> AuditLogRepository:
    """Factory function for audit log repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexAuditLogRepository

        return ConvexAuditLogRepository()
    return InMemoryAuditLogRepository()


def get_entity_repository(backend: BackendType = None) -> EntityRepository:
    """Factory function for entity repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexEntityRepository

        return ConvexEntityRepository()
    return InMemoryEntityRepository()


def get_record_repository(backend: BackendType = None) -> RecordRepository:
    """Factory function for record repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexRecordRepository

        return ConvexRecordRepository()
    return InMemoryRecordRepository()


def get_approval_repository(backend: BackendType = None) -> ApprovalRepository:
    """Factory function for approval repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexApprovalRepository

        return ConvexApprovalRepository()
    return InMemoryApprovalRepository()


def get_alert_repository(backend: BackendType = None) -> AlertRepository:
    """Factory function for alert repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexAlertRepository

        return ConvexAlertRepository()
    return InMemoryAlertRepository()


def get_workflow_repository(backend: BackendType = None) -> WorkflowRepository:
    """Factory function for workflow repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexWorkflowRepository

        return ConvexWorkflowRepository()
    return InMemoryWorkflowRepository()


def get_workflow_execution_repository(
    backend: BackendType = None,
) -> WorkflowExecutionRepository:
    """Factory function for workflow execution repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexWorkflowExecutionRepository

        return ConvexWorkflowExecutionRepository()
    return InMemoryWorkflowExecutionRepository()


def get_project_memory_repository(
    backend: BackendType = None,
) -> ProjectMemoryRepository:
    """Factory function for project memory repository."""
    backend = backend or _get_backend()
    if backend == "convex":
        from .convex import ConvexProjectMemoryRepository

        return ConvexProjectMemoryRepository()
    return InMemoryProjectMemoryRepository()


__all__ = [
    # Base interfaces
    "UserRepository",
    "OrganizationRepository",
    "MembershipRepository",
    "ApiKeyRepository",
    "AuditLogRepository",
    "EntityRepository",
    "RecordRepository",
    "ApprovalRepository",
    "AlertRepository",
    "WorkflowRepository",
    "WorkflowExecutionRepository",
    "ProjectMemoryRepository",
    # In-memory implementations
    "InMemoryUserRepository",
    "InMemoryOrganizationRepository",
    "InMemoryMembershipRepository",
    "InMemoryApiKeyRepository",
    "InMemoryAuditLogRepository",
    "InMemoryEntityRepository",
    "InMemoryRecordRepository",
    "InMemoryApprovalRepository",
    "InMemoryAlertRepository",
    "InMemoryWorkflowRepository",
    "InMemoryWorkflowExecutionRepository",
    "InMemoryProjectMemoryRepository",
    # Factory functions
    "get_user_repository",
    "get_organization_repository",
    "get_membership_repository",
    "get_api_key_repository",
    "get_audit_log_repository",
    "get_entity_repository",
    "get_record_repository",
    "get_approval_repository",
    "get_alert_repository",
    "get_workflow_repository",
    "get_workflow_execution_repository",
    "get_project_memory_repository",
    # W3.2d — Async repositories (route-layer)
    "AsyncUserSyncRepository",
    "AsyncAuditLogRepository",
    "AsyncWebhookSeenRepository",
    "AsyncDeveloperRepository",
    "AsyncAppVersionRepository",
    "AsyncAppPermissionsRepository",
    "AsyncChatSessionRepository",
    "AsyncDeploymentRepository",
    "get_async_user_sync_repository",
    "get_async_audit_log_repository",
    "get_async_webhook_seen_repository",
    "get_async_developer_repository",
    "get_async_app_version_repository",
    "get_async_app_permissions_repository",
    "get_async_chat_session_repository",
    "get_async_deployment_repository",
    # W4.1 — Sync service-layer repositories
    "ConvexAppInstallationRepository",
    "ConvexCheckpointRepository",
    "ConvexCollaboratorRepository",
    "ConvexMarketplaceAppsRepository",
    "ConvexDeveloperPayoutRepository",
    "get_app_installation_repository",
    "get_checkpoint_repository",
    "get_collaborator_repository",
    "get_marketplace_apps_repository",
    "get_developer_payout_repository",
    # W5.1 — SQLite local-first repositories
    "SQLiteAppInstallationRepository",
    "SQLiteUserSyncRepository",
    "SQLiteChatSessionRepository",
    "get_sqlite_app_installation_repository",
    "get_sqlite_user_sync_repository",
    "get_sqlite_chat_session_repository",
    # W5.2 — Local Chroma RAG repository
    "LocalChromaMemoryRepository",
    "get_local_chroma_memory_repository",
    "get_memory_repository",
    # Types
    "BackendType",
]
