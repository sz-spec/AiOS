"""
Clerk Webhook Handler

Syncs Clerk user events to Convex:
- user.created: Create user in Convex
- user.updated: Update user in Convex
- user.deleted: Soft-delete user in Convex
- session.created: Track sign-ins
"""

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel

# W3.2d — route through repositories layer instead of direct db.convex import.
from core.repositories import (
    get_async_user_sync_repository,
    get_async_audit_log_repository,
    get_async_webhook_seen_repository,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/webhooks/clerk", tags=["webhooks"])


# ============================================
# Configuration
# ============================================

CLERK_WEBHOOK_SECRET = None  # Set from environment


def set_clerk_webhook_secret(secret: str):
    global CLERK_WEBHOOK_SECRET
    CLERK_WEBHOOK_SECRET = secret


# ============================================
# Webhook Verification
# ============================================


def verify_clerk_webhook(
    payload: bytes,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
) -> bool:
    """Verify Clerk webhook signature using Svix."""
    if not CLERK_WEBHOOK_SECRET:
        # In production, reject webhooks without a configured secret
        if os.getenv("ENVIRONMENT") == "production":
            return False
        return True  # Skip verification in development only

    signatures = svix_signature.split(" ")

    for sig in signatures:
        if sig.startswith("v1,"):
            expected_sig = sig[3:]
            signed_payload = f"{svix_id}.{svix_timestamp}.{payload.decode()}"
            # Svix secrets are base64-encoded (prefixed with "whsec_")
            secret_str = CLERK_WEBHOOK_SECRET
            if secret_str.startswith("whsec_"):
                secret_str = secret_str[6:]
            try:
                secret_bytes = base64.b64decode(secret_str)
            except Exception:
                return False
            computed_sig = base64.b64encode(
                hmac.new(secret_bytes, signed_payload.encode(), hashlib.sha256).digest()
            ).decode()
            if hmac.compare_digest(expected_sig, computed_sig):
                return True

    return False


# ============================================
# Event Models
# ============================================


class ClerkUserData(BaseModel):
    """Clerk user data from webhook."""

    id: str
    email_addresses: list[dict]
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    image_url: Optional[str] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    public_metadata: dict = {}
    private_metadata: dict = {}


class ClerkWebhookEvent(BaseModel):
    """Clerk webhook event."""

    type: str
    data: dict
    object: str = "event"


# ============================================
# Event Handlers
# ============================================


async def handle_user_created(data: dict) -> dict:
    """Handle user.created event — sync to Convex."""
    user_data = ClerkUserData(**data)

    primary_email = None
    for email in user_data.email_addresses:
        if email.get("id") == data.get("primary_email_address_id"):
            primary_email = email.get("email_address")
            break
    if not primary_email and user_data.email_addresses:
        primary_email = user_data.email_addresses[0].get("email_address")

    full_name = " ".join(filter(None, [user_data.first_name, user_data.last_name]))

    await get_async_user_sync_repository().sync_from_clerk(
        clerk_id=user_data.id,
        email=primary_email or "",
        full_name=full_name or None,
        avatar_url=user_data.image_url,
        metadata=user_data.public_metadata,
    )

    return {
        "status": "created",
        "user_id": user_data.id,
        "email": primary_email,
    }


async def handle_user_updated(data: dict) -> dict:
    """Handle user.updated event — update in Convex."""
    user_data = ClerkUserData(**data)

    primary_email = None
    for email in user_data.email_addresses:
        if email.get("id") == data.get("primary_email_address_id"):
            primary_email = email.get("email_address")
            break

    full_name = " ".join(filter(None, [user_data.first_name, user_data.last_name]))

    # syncFromClerk is an upsert — re-use it for updates too
    await get_async_user_sync_repository().sync_from_clerk(
        clerk_id=user_data.id,
        email=primary_email or "",
        full_name=full_name or None,
        avatar_url=user_data.image_url,
        metadata=user_data.public_metadata,
    )

    return {
        "status": "updated",
        "user_id": user_data.id,
    }


async def handle_user_deleted(data: dict) -> dict:
    """Handle user.deleted event — soft-delete in Convex."""
    user_id = data.get("id")

    if not user_id:
        return {"status": "skipped", "reason": "no user_id"}

    await get_async_user_sync_repository().soft_delete(clerk_id=user_id)

    return {
        "status": "deleted",
        "user_id": user_id,
    }


async def handle_session_created(data: dict) -> dict:
    """Handle session.created event — record sign-in."""
    user_id = data.get("user_id")

    if not user_id:
        return {"status": "skipped", "reason": "no user_id"}

    user_repo = get_async_user_sync_repository()
    await user_repo.record_sign_in(clerk_id=user_id)

    # Audit log
    user = await user_repo.get_by_clerk_id(clerk_id=user_id)
    if user:
        await get_async_audit_log_repository().add_entry(
            user_id=user.get("_id"),
            action="login",
            resource_type="session",
            resource_id=data.get("id"),
            metadata={
                "client_id": data.get("client_id"),
                "created_at": data.get("created_at"),
            },
        )

    return {
        "status": "tracked",
        "user_id": user_id,
    }


# ============================================
# Webhook Endpoint
# ============================================


async def _webhook_seen_already(svix_id: str) -> bool:
    """Resilience-Matrix F10 — check Convex webhook_seen for duplicate.

    Returns True iff a row keyed by ('clerk', svix_id) already exists.
    Failures during the lookup default to False (don't block delivery
    on Convex transients) — already handled inside the repo.
    """
    return await get_async_webhook_seen_repository().was_seen(
        provider="clerk", external_id=svix_id
    )


async def _webhook_record_seen(svix_id: str, event_type: str) -> None:
    """Mark this delivery as seen so a retry is short-circuited.

    24h dedup window — Clerk retries within a few hours; 24h gives
    generous coverage with bounded storage growth.
    """
    import time as _time

    await get_async_webhook_seen_repository().record(
        provider="clerk",
        external_id=svix_id,
        event_type=event_type,
        seen_at_ms=int(_time.time() * 1000),
    )


@router.post("")
async def clerk_webhook(
    request: Request,
    svix_id: str = Header(None, alias="svix-id"),
    svix_timestamp: str = Header(None, alias="svix-timestamp"),
    svix_signature: str = Header(None, alias="svix-signature"),
):
    """
    Handle Clerk webhook events.

    Supported events:
    - user.created
    - user.updated
    - user.deleted
    - session.created
    """
    payload = await request.body()

    # In production, require signature headers
    if os.getenv("ENVIRONMENT") == "production" and not (
        svix_id and svix_timestamp and svix_signature
    ):
        raise HTTPException(status_code=400, detail="Missing webhook signature headers")

    if svix_id and svix_timestamp and svix_signature:
        if not verify_clerk_webhook(payload, svix_id, svix_timestamp, svix_signature):
            raise HTTPException(status_code=400, detail="Invalid signature")

    # Resilience-Matrix F10 — webhook idempotency. After signature is
    # verified (so an attacker cannot poison the dedup cache with
    # forged externalIds), check whether this svix-id has been processed.
    if await _webhook_seen_already(svix_id):
        return {"status": "duplicate", "svix_id": svix_id}

    try:
        event_data = json.loads(payload)
        event = ClerkWebhookEvent(**event_data)
    except Exception as e:
        logger.error("Clerk webhook payload parse failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid payload")

    handlers = {
        "user.created": handle_user_created,
        "user.updated": handle_user_updated,
        "user.deleted": handle_user_deleted,
        "session.created": handle_session_created,
    }

    handler = handlers.get(event.type)

    if not handler:
        return {"status": "ignored", "event_type": event.type}

    try:
        result = await handler(event.data)
        # F10 — record only on successful processing so a transient
        # error doesn't poison the dedup cache.
        await _webhook_record_seen(svix_id, event.type)
        return {"status": "success", "event_type": event.type, **result}
    except Exception as e:
        logger.error("Webhook handler error for event %s: %s", event.type, e)
        return {"status": "error", "event_type": event.type, "error": str(e)}


# ============================================
# Health Check
# ============================================


@router.get("/health")
async def webhook_health():
    """Check webhook endpoint health."""
    return {
        "status": "healthy",
        "webhook_secret_configured": CLERK_WEBHOOK_SECRET is not None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


__all__ = ["router", "set_clerk_webhook_secret"]
