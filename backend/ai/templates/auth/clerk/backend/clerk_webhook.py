"""
Clerk Webhook - Handles Clerk webhook events.
Syncs user data from Clerk to the application database.
"""

import os
import logging
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])

CLERK_WEBHOOK_SECRET = os.getenv("CLERK_WEBHOOK_SECRET", "")


@router.post("/clerk")
async def clerk_webhook(request: Request):
    """Process Clerk webhook events (user.created, user.updated, etc.)."""
    body = await request.json()
    event_type = body.get("type", "")

    if event_type == "user.created":
        user_data = body.get("data", {})
        logger.info("New user created: %s", user_data.get("id"))
        # Sync user to local database
        return {"status": "ok"}

    if event_type == "user.updated":
        user_data = body.get("data", {})
        logger.info("User updated: %s", user_data.get("id"))
        return {"status": "ok"}

    if event_type == "user.deleted":
        user_data = body.get("data", {})
        logger.info("User deleted: %s", user_data.get("id"))
        return {"status": "ok"}

    return {"status": "ignored", "event": event_type}
