"""
PersistentService — shared bootstrap for V-Core services.
"""

import os
import logging

logger = logging.getLogger(__name__)

# Check if Convex repository layer is available (lazy — only checks importability)
try:
    from core.repositories.convex import (
        ConvexUserRepository,
        ConvexOrganizationRepository,
        ConvexEntityRepository,
        ConvexRecordRepository,
        ConvexWorkflowRepository,
        ConvexWorkflowExecutionRepository,
        ConvexApprovalRepository,
        ConvexAlertRepository,
        ConvexAuditLogRepository,
        ConvexApiKeyRepository,
    )

    REPOSITORIES_AVAILABLE = True
except ImportError:
    REPOSITORIES_AVAILABLE = False

# Max in-memory entries for unbounded collections (TTL cap)
IN_MEMORY_CAP = 1000


class PersistentService:
    """Base class providing shared persistence bootstrap for V-Core services."""

    def _bootstrap_persistence(self, use_persistence: bool = None) -> bool:
        """Resolve persistence mode and return True if using persistent storage."""
        if use_persistence is None:
            use_persistence = (
                os.getenv("VOS3_STORAGE_BACKEND", "convex").lower() != "memory"
            )

        if use_persistence and REPOSITORIES_AVAILABLE:
            return True

        if use_persistence and not REPOSITORIES_AVAILABLE:
            logger.warning(
                "%s: Convex repositories not available, falling back to in-memory",
                self.__class__.__name__,
            )
        return False

    @staticmethod
    def _trim_list(collection: list, cap: int = IN_MEMORY_CAP) -> None:
        """Trim a list to the most recent `cap` entries (in-place)."""
        if len(collection) > cap:
            del collection[:-cap]
