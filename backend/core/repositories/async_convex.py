"""
Async Convex Repositories — Route-Layer Data Access
====================================================

W3.2d — pulls direct `from db.convex import get_convex_client` calls out of
FastAPI route handlers and routes them through a thin async repository layer.

This file is a sibling to `convex.py` (which holds the **sync** repository
implementations used by the V-Core core/* services via `_run()` thread
dispatch). These repositories are **async-native** because:

  1. The callers (FastAPI route handlers) are already `async def`.
  2. `ConvexClient.query/mutation/action` are async coroutines.
  3. Avoiding the sync→async thread-pool hop removes a class of cross-loop
     httpx issues and keeps the request hot path on a single event loop.

The "SQLite-swap enabler" framing of W3.2d is preserved: each repo class
exposes a stable async interface; swapping `ConvexClient` for a different
backend (SQLite, Supabase, Neon) is a matter of adding a parallel
implementation and updating the factory in `__init__.py`.

Memory/in-memory fallbacks are intentionally NOT provided here — the
calling routes already degrade gracefully via try/except (returning
sentinel "dev mode" responses). If a future test needs deterministic
fixtures, add Fake* classes alongside.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared client accessor — lazy, so import-time of this module doesn't
# require CONVEX_URL to be set.
# ---------------------------------------------------------------------------


def _client():
    """Lazy import + singleton accessor for the underlying ConvexClient.

    Kept module-private so callers always go through a repo method,
    never directly. Tests can monkeypatch this function to inject fakes.
    """
    from db.convex import get_convex_client

    return get_convex_client()


# ---------------------------------------------------------------------------
# User sync (Clerk webhook → Convex users table)
# ---------------------------------------------------------------------------


class AsyncUserSyncRepository:
    """Convex-backed sync operations used by the Clerk webhook.

    Wraps `users:syncFromClerk`, `users:softDelete`, `users:recordSignIn`,
    `users:getByClerkId`. The sync `ConvexUserRepository` in `convex.py`
    covers the same surface but is sync-only; this async variant exists so
    the webhook handler can stay on the request event loop.
    """

    async def sync_from_clerk(
        self,
        *,
        clerk_id: str,
        email: str,
        full_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Any:
        return await _client().mutation(
            "users:syncFromClerk",
            {
                "clerkId": clerk_id,
                "email": email,
                "fullName": full_name,
                "avatarUrl": avatar_url,
                "metadata": metadata,
            },
        )

    async def soft_delete(self, *, clerk_id: str) -> Any:
        return await _client().mutation("users:softDelete", {"clerkId": clerk_id})

    async def record_sign_in(self, *, clerk_id: str) -> Any:
        return await _client().mutation("users:recordSignIn", {"clerkId": clerk_id})

    async def get_by_clerk_id(self, *, clerk_id: str) -> Optional[dict]:
        return await _client().query("users:getByClerkId", {"clerkId": clerk_id})


# ---------------------------------------------------------------------------
# Audit log (used by Clerk webhook for session.created events)
# ---------------------------------------------------------------------------


class AsyncAuditLogRepository:
    """Async wrapper for `vcore:addAuditEntry`.

    The sync `ConvexAuditLogRepository` exists for V-Core service callers;
    this variant is for async route/webhook contexts.
    """

    async def add_entry(
        self,
        *,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        ip_address: Optional[str] = None,
    ) -> Any:
        payload: dict = {
            "action": action,
            "resourceType": resource_type,
        }
        if user_id is not None:
            payload["userId"] = user_id
        if organization_id is not None:
            payload["organizationId"] = organization_id
        if resource_id is not None:
            payload["resourceId"] = resource_id
        if metadata is not None:
            payload["metadata"] = metadata
        if ip_address is not None:
            payload["ipAddress"] = ip_address
        return await _client().mutation("vcore:addAuditEntry", payload)


# ---------------------------------------------------------------------------
# Webhook idempotency (Resilience-Matrix F10)
# ---------------------------------------------------------------------------


class AsyncWebhookSeenRepository:
    """Provider-keyed dedup store for inbound webhook deliveries.

    See `frontend/convex/webhook_seen.ts` for the backing schema and the
    24h `expiresAt` window contract. Failures during lookup default to
    False — we don't want a Convex transient to block a legitimate
    Clerk delivery.
    """

    async def was_seen(self, *, provider: str, external_id: str) -> bool:
        if not external_id:
            return False
        try:
            existing = await _client().query(
                "webhook_seen:byProviderExternal",
                {"provider": provider, "externalId": external_id},
            )
            return existing is not None
        except Exception as exc:
            logger.warning("webhook_seen lookup failed (continuing): %s", exc)
            return False

    async def record(
        self,
        *,
        provider: str,
        external_id: str,
        event_type: str,
        seen_at_ms: int,
        ttl_ms: int = 86_400_000,
    ) -> None:
        if not external_id:
            return
        try:
            await _client().mutation(
                "webhook_seen:record",
                {
                    "provider": provider,
                    "externalId": external_id,
                    "eventType": event_type,
                    "seenAt": seen_at_ms,
                    "expiresAt": seen_at_ms + ttl_ms,
                },
            )
        except Exception as exc:
            logger.warning("webhook_seen record failed (continuing): %s", exc)


# ---------------------------------------------------------------------------
# Developer profile + app listing
# ---------------------------------------------------------------------------


class AsyncDeveloperRepository:
    """Wraps `developers:register`, `developers:getProfile`,
    `developers:getApps`.
    """

    async def register(
        self,
        *,
        user_id: str,
        display_name: str,
        email: str,
        website: Optional[str] = None,
        bio: Optional[str] = None,
    ) -> Any:
        return await _client().mutation(
            "developers:register",
            {
                "userId": user_id,
                "displayName": display_name,
                "email": email,
                "website": website,
                "bio": bio,
            },
        )

    async def get_profile(self, *, user_id: str) -> Optional[dict]:
        return await _client().query("developers:getProfile", {"userId": user_id})

    async def list_apps(self, *, developer_id: str) -> list:
        result = await _client().query(
            "developers:getApps", {"developerId": developer_id}
        )
        return result or []


# ---------------------------------------------------------------------------
# App versions (submission flow)
# ---------------------------------------------------------------------------


class AsyncAppVersionRepository:
    """Wraps `appVersions:create`, `appVersions:listByApp`."""

    async def create(
        self,
        *,
        app_id: str,
        version: str,
        changelog: str,
        manifest: dict,
        status: str = "review",
    ) -> Any:
        return await _client().mutation(
            "appVersions:create",
            {
                "appId": app_id,
                "version": version,
                "changelog": changelog,
                "manifest": manifest,
                "status": status,
            },
        )

    async def list_by_app(self, *, app_id: str) -> list:
        result = await _client().query("appVersions:listByApp", {"appId": app_id})
        return result or []


# ---------------------------------------------------------------------------
# App permissions (OAuth consent grants)
# ---------------------------------------------------------------------------


class AsyncAppPermissionsRepository:
    """Wraps `appPermissions:grant`."""

    async def grant(
        self,
        *,
        app_id: str,
        organization_id: str,
        user_id: str,
        scopes: list[str],
        granted_at_ms: int,
    ) -> Any:
        return await _client().mutation(
            "appPermissions:grant",
            {
                "appId": app_id,
                "organizationId": organization_id,
                "userId": user_id,
                "scopes": scopes,
                "grantedAt": granted_at_ms,
            },
        )


# ---------------------------------------------------------------------------
# Chat session history
# ---------------------------------------------------------------------------


class AsyncChatSessionRepository:
    """Wraps `chatSessions:load`, `chatSessions:remove`."""

    async def load(self, *, session_id: str) -> Optional[dict]:
        return await _client().query("chatSessions:load", {"sessionId": session_id})

    async def remove(self, *, session_id: str) -> Any:
        return await _client().mutation(
            "chatSessions:remove", {"sessionId": session_id}
        )


# ---------------------------------------------------------------------------
# Deployment persistence (DeployService)
# ---------------------------------------------------------------------------


class AsyncDeploymentRepository:
    """Wraps `deployments:create` for the async DeployService.

    Lives in the async pool because DeployService methods are `async def`
    (deploy_to_vercel uses httpx.AsyncClient) — calling a sync repo
    through `_run()` would introduce an unnecessary thread hop on the
    already-on-loop request path.
    """

    async def create(
        self,
        *,
        project_id: str,
        provider: str,
        url: str,
        subdomain: Optional[str] = None,
        status: str = "live",
        config: Optional[dict] = None,
    ) -> Any:
        return await _client().mutation(
            "deployments:create",
            {
                "projectId": project_id,
                "provider": provider,
                "url": url,
                "subdomain": subdomain,
                "status": status,
                "config": config,
            },
        )


# ---------------------------------------------------------------------------
# Factory functions (single source of indirection for route layer)
# ---------------------------------------------------------------------------


def get_async_user_sync_repository() -> AsyncUserSyncRepository:
    return AsyncUserSyncRepository()


def get_async_audit_log_repository() -> AsyncAuditLogRepository:
    return AsyncAuditLogRepository()


def get_async_webhook_seen_repository() -> AsyncWebhookSeenRepository:
    return AsyncWebhookSeenRepository()


def get_async_developer_repository() -> AsyncDeveloperRepository:
    return AsyncDeveloperRepository()


def get_async_app_version_repository() -> AsyncAppVersionRepository:
    return AsyncAppVersionRepository()


def get_async_app_permissions_repository() -> AsyncAppPermissionsRepository:
    return AsyncAppPermissionsRepository()


def get_async_chat_session_repository() -> AsyncChatSessionRepository:
    return AsyncChatSessionRepository()


def get_async_deployment_repository() -> AsyncDeploymentRepository:
    return AsyncDeploymentRepository()


__all__ = [
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
]
