"""
Service-Layer Convex Repositories — Sync Pragmatic Variant
===========================================================

W4.1 — pulls direct `from db.convex import get_convex_client` calls out of
the 5 sync service modules (app_registry, checkpoint_service,
collab_service, marketplace_service, revenue_share) and routes them
through a thin sync repository layer.

This file is the sync counterpart of `async_convex.py` (W3.2d, for FastAPI
async route handlers). Repos here use `_run()` thread-pool dispatch to
call the async `ConvexClient.mutation/query` from sync service methods —
the same pattern the existing sync repos in `convex.py` use.

ABC inheritance is intentionally skipped (unlike the V-Core sync repos in
`convex.py`):

  - Each service already has its own in-memory fallback dict baked in,
    so there's no need for a swappable InMemory* implementation.
  - The repos here are concrete Convex bindings; swapping to SQLite would
    add a parallel implementation and update the factory — same one-step
    swap the framing implies.

Incidental fix surfaced by this migration:

  - `apps:list` was converted to cursor-paginated in W3.2c-2. The
    `marketplace_service._get_apps()` call was passing only `{"status":
    "published"}` without `paginationOpts`, so Convex was rejecting it
    and the try/except silently returned the BUILTIN_COMPONENTS fallback.
    `ConvexMarketplaceAppsRepository.list_published()` now passes a
    bounded `paginationOpts` and consumes `result.page`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared client + run helpers (mirrors convex.py)
# ---------------------------------------------------------------------------


def _client():
    """Lazy accessor for ConvexClient. Tests can monkeypatch this."""
    from db.convex import get_convex_client

    return get_convex_client()


def _run(coro):
    """Run an async coroutine synchronously.

    Matches `convex.py::_run()` — dispatches via a dedicated thread when
    called from within an async context to avoid cross-loop httpx issues.
    """
    try:
        asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=30)
    except RuntimeError:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# App installations (apps:install)
# ---------------------------------------------------------------------------


class ConvexAppInstallationRepository:
    """Wraps `apps:install` for the AppRegistry service."""

    def install(
        self,
        *,
        app_id: str,
        organization_id: str,
        installed_by: str,
        version: str,
        granted_scopes: list[str],
        config: Optional[dict] = None,
    ) -> Any:
        return _run(
            _client().mutation(
                "apps:install",
                {
                    "appId": app_id,
                    "organizationId": organization_id,
                    "installedBy": installed_by,
                    "version": version,
                    "grantedScopes": granted_scopes,
                    "config": config,
                },
            )
        )


# ---------------------------------------------------------------------------
# Checkpoints (checkpoints:create / checkpoints:listByProject)
# ---------------------------------------------------------------------------


class ConvexCheckpointRepository:
    """Wraps checkpoint persistence for CheckpointService."""

    def create(
        self,
        *,
        project_id: str,
        description: str,
        files_snapshot: dict,
        created_by: Optional[str],
    ) -> Any:
        return _run(
            _client().mutation(
                "checkpoints:create",
                {
                    "projectId": project_id,
                    "description": description,
                    "filesSnapshot": files_snapshot,
                    "createdBy": created_by,
                },
            )
        )

    def list_by_project(self, *, project_id: str) -> list[dict]:
        result = _run(
            _client().query("checkpoints:listByProject", {"projectId": project_id})
        )
        return result or []


# ---------------------------------------------------------------------------
# Collaborators (collaborators:invite / collaborators:listByProject)
# ---------------------------------------------------------------------------


class ConvexCollaboratorRepository:
    """Wraps collaborator persistence for CollabService."""

    def invite(
        self,
        *,
        project_id: str,
        user_email: str,
        role: str,
        accepted: bool,
        invited_at_ms: int,
    ) -> Any:
        return _run(
            _client().mutation(
                "collaborators:invite",
                {
                    "projectId": project_id,
                    "userEmail": user_email,
                    "role": role,
                    "accepted": accepted,
                    "invitedAt": invited_at_ms,
                },
            )
        )

    def list_by_project(self, *, project_id: str) -> list[dict]:
        result = _run(
            _client().query("collaborators:listByProject", {"projectId": project_id})
        )
        return result or []


# ---------------------------------------------------------------------------
# Marketplace apps (apps:list — paginated since W3.2c-2)
# ---------------------------------------------------------------------------


class ConvexMarketplaceAppsRepository:
    """Wraps `apps:list` for the server-side marketplace cache.

    W3.2c-2 converted `apps:list` to cursor pagination — this repo
    consumes a single page bounded by `numItems`. The marketplace cache
    is a degraded-mode read (the live storefront uses the paginated
    frontend Convex hook), so a 200-item ceiling is sufficient.
    """

    def list_published(self, *, limit: int = 200) -> list[dict]:
        result = _run(
            _client().query(
                "apps:list",
                {
                    "status": "published",
                    "paginationOpts": {
                        "numItems": max(1, min(limit, 200)),
                        "cursor": None,
                    },
                },
            )
        )
        if not result:
            return []
        # `result` shape: { page, isDone, continueCursor }. The repo only
        # surfaces page rows; callers that need cursor-based reads can
        # build a parallel method later.
        return result.get("page", []) if isinstance(result, dict) else []


# ---------------------------------------------------------------------------
# Developer payouts (developerPayouts:create)
# ---------------------------------------------------------------------------


class ConvexDeveloperPayoutRepository:
    """Wraps `developerPayouts:create` for the RevenueShareEngine."""

    def record(
        self,
        *,
        developer_id: str,
        amount: float,
        currency: str,
        status: str,
        period_start_ms: int,
        period_end_ms: int,
    ) -> Any:
        return _run(
            _client().mutation(
                "developerPayouts:create",
                {
                    "developerId": developer_id,
                    "amount": amount,
                    "currency": currency,
                    "status": status,
                    "periodStart": period_start_ms,
                    "periodEnd": period_end_ms,
                },
            )
        )


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def get_app_installation_repository() -> ConvexAppInstallationRepository:
    return ConvexAppInstallationRepository()


def get_checkpoint_repository() -> ConvexCheckpointRepository:
    return ConvexCheckpointRepository()


def get_collaborator_repository() -> ConvexCollaboratorRepository:
    return ConvexCollaboratorRepository()


def get_marketplace_apps_repository() -> ConvexMarketplaceAppsRepository:
    return ConvexMarketplaceAppsRepository()


def get_developer_payout_repository() -> ConvexDeveloperPayoutRepository:
    return ConvexDeveloperPayoutRepository()


__all__ = [
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
]
