"""
Push Notification Service

Server-side push notification handling:
- VAPID key generation
- Subscription management
- Push message sending
- Notification templates

Dependencies:
- pywebpush
- cryptography
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import base64


class NotificationType(str, Enum):
    """Types of push notifications."""

    PROJECT_BUILD = "project_build"
    COMMENT_MENTION = "comment_mention"
    TEAM_INVITE = "team_invite"
    CHAT_RESPONSE = "chat_response"
    SYSTEM_UPDATE = "system_update"
    REVIEW_COMPLETE = "review_complete"


@dataclass
class PushSubscription:
    """Web Push subscription data."""

    endpoint: str
    p256dh: str
    auth: str
    user_id: str
    created_at: datetime

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "keys": {
                "p256dh": self.p256dh,
                "auth": self.auth,
            },
        }


@dataclass
class NotificationPayload:
    """Push notification payload."""

    title: str
    body: str
    icon: str = "/icons/icon-192.png"
    badge: str = "/icons/badge-72.png"
    tag: Optional[str] = None
    data: Optional[dict] = None
    actions: Optional[list] = None
    vibrate: list = None
    renotify: bool = False
    require_interaction: bool = False
    silent: bool = False
    timestamp: Optional[int] = None

    def __post_init__(self):
        if self.vibrate is None:
            self.vibrate = [100, 50, 100]
        if self.timestamp is None:
            self.timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

    def to_json(self) -> str:
        payload = {
            "title": self.title,
            "body": self.body,
            "icon": self.icon,
            "badge": self.badge,
            "vibrate": self.vibrate,
            "timestamp": self.timestamp,
            "renotify": self.renotify,
            "requireInteraction": self.require_interaction,
            "silent": self.silent,
        }

        if self.tag:
            payload["tag"] = self.tag
        if self.data:
            payload["data"] = self.data
        if self.actions:
            payload["actions"] = self.actions

        return json.dumps(payload)


class PushNotificationService:
    """
    Web Push notification service.

    Features:
    - VAPID authentication
    - Subscription management
    - Notification templates
    - Batch sending
    """

    def __init__(
        self,
        vapid_private_key: Optional[str] = None,
        vapid_public_key: Optional[str] = None,
        vapid_claims_email: str = "mailto:admin@aiappbuilder.dev",
    ):
        self.vapid_private_key = vapid_private_key or os.getenv("VAPID_PRIVATE_KEY")
        self.vapid_public_key = vapid_public_key or os.getenv("VAPID_PUBLIC_KEY")
        self.vapid_claims_email = vapid_claims_email

        # In-memory subscription storage
        self._subscriptions: list[dict] = []

        # Generate VAPID keys if not provided
        if not self.vapid_private_key or not self.vapid_public_key:
            self._generate_vapid_keys()

    def _generate_vapid_keys(self):
        """Generate new VAPID key pair."""
        private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        public_key = private_key.public_key()

        # Serialize private key
        private_bytes = private_key.private_numbers().private_value.to_bytes(32, "big")
        self.vapid_private_key = (
            base64.urlsafe_b64encode(private_bytes).decode("utf-8").rstrip("=")
        )

        # Serialize public key
        public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        self.vapid_public_key = (
            base64.urlsafe_b64encode(public_bytes).decode("utf-8").rstrip("=")
        )

        print("Generated VAPID keys:")
        print(f"Public: {self.vapid_public_key}")
        print(f"Private: {self.vapid_private_key}")

    def get_vapid_public_key(self) -> str:
        """Get VAPID public key for client subscription."""
        return self.vapid_public_key

    async def save_subscription(
        self,
        user_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
    ) -> bool:
        """Save push subscription."""
        # Remove existing subscription with same endpoint
        self._subscriptions = [
            s for s in self._subscriptions if s["endpoint"] != endpoint
        ]
        self._subscriptions.append(
            {
                "user_id": user_id,
                "endpoint": endpoint,
                "p256dh": p256dh,
                "auth": auth,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return True

    async def remove_subscription(self, user_id: str, endpoint: str) -> bool:
        """Remove push subscription."""
        before = len(self._subscriptions)
        self._subscriptions = [
            s
            for s in self._subscriptions
            if not (s["user_id"] == user_id and s["endpoint"] == endpoint)
        ]
        return len(self._subscriptions) < before

    async def get_user_subscriptions(self, user_id: str) -> list[PushSubscription]:
        """Get all push subscriptions for a user."""
        return [
            PushSubscription(
                endpoint=sub["endpoint"],
                p256dh=sub["p256dh"],
                auth=sub["auth"],
                user_id=sub["user_id"],
                created_at=datetime.fromisoformat(
                    sub["created_at"].replace("Z", "+00:00")
                ),
            )
            for sub in self._subscriptions
            if sub["user_id"] == user_id
        ]

    async def send_notification(
        self,
        subscription: PushSubscription,
        payload: NotificationPayload,
    ) -> bool:
        """Send push notification to a single subscription."""
        try:
            # Import pywebpush here to avoid startup errors if not installed
            from pywebpush import webpush

            webpush(
                subscription_info=subscription.to_dict(),
                data=payload.to_json(),
                vapid_private_key=self.vapid_private_key,
                vapid_claims={"sub": self.vapid_claims_email},
            )
            return True

        except Exception as e:
            if "410" in str(e) or "404" in str(e):
                # Subscription expired, remove it
                await self.remove_subscription(
                    subscription.user_id, subscription.endpoint
                )
            print(f"Push notification failed: {e}")
            return False

    async def send_to_user(
        self,
        user_id: str,
        payload: NotificationPayload,
    ) -> int:
        """Send notification to all user's devices."""
        subscriptions = await self.get_user_subscriptions(user_id)

        sent_count = 0
        for subscription in subscriptions:
            if await self.send_notification(subscription, payload):
                sent_count += 1

        return sent_count

    async def send_to_users(
        self,
        user_ids: list[str],
        payload: NotificationPayload,
    ) -> dict[str, int]:
        """Send notification to multiple users."""
        results = {}
        for user_id in user_ids:
            results[user_id] = await self.send_to_user(user_id, payload)
        return results

    # ==========================================
    # Notification Templates
    # ==========================================

    def create_project_build_notification(
        self,
        project_name: str,
        status: str,  # "success" | "failed"
        project_id: str,
    ) -> NotificationPayload:
        """Create project build notification."""
        if status == "success":
            return NotificationPayload(
                title="Build Complete! 🎉",
                body=f"Your project '{project_name}' built successfully.",
                tag=f"build-{project_id}",
                data={
                    "type": NotificationType.PROJECT_BUILD.value,
                    "project_id": project_id,
                    "url": f"/editor/{project_id}",
                },
                actions=[
                    {"action": "open", "title": "Open Project"},
                    {"action": "dismiss", "title": "Dismiss"},
                ],
            )
        else:
            return NotificationPayload(
                title="Build Failed ❌",
                body=f"There was an error building '{project_name}'.",
                tag=f"build-{project_id}",
                data={
                    "type": NotificationType.PROJECT_BUILD.value,
                    "project_id": project_id,
                    "url": f"/editor/{project_id}",
                },
                actions=[
                    {"action": "open", "title": "View Error"},
                    {"action": "dismiss", "title": "Dismiss"},
                ],
                require_interaction=True,
            )

    def create_mention_notification(
        self,
        from_user: str,
        comment_preview: str,
        project_id: str,
        comment_id: str,
    ) -> NotificationPayload:
        """Create @mention notification."""
        return NotificationPayload(
            title=f"💬 {from_user} mentioned you",
            body=comment_preview[:100],
            tag=f"mention-{comment_id}",
            data={
                "type": NotificationType.COMMENT_MENTION.value,
                "project_id": project_id,
                "comment_id": comment_id,
                "url": f"/editor/{project_id}?comment={comment_id}",
            },
            actions=[
                {"action": "reply", "title": "Reply"},
                {"action": "open", "title": "View"},
            ],
        )

    def create_team_invite_notification(
        self,
        team_name: str,
        from_user: str,
        invite_id: str,
    ) -> NotificationPayload:
        """Create team invitation notification."""
        return NotificationPayload(
            title="Team Invitation 👥",
            body=f"{from_user} invited you to join '{team_name}'",
            tag=f"invite-{invite_id}",
            data={
                "type": NotificationType.TEAM_INVITE.value,
                "invite_id": invite_id,
                "url": f"/teams/invites/{invite_id}",
            },
            actions=[
                {"action": "accept", "title": "Accept"},
                {"action": "decline", "title": "Decline"},
            ],
            require_interaction=True,
        )

    def create_chat_response_notification(
        self,
        response_preview: str,
        project_id: str,
        session_id: str,
    ) -> NotificationPayload:
        """Create AI chat response notification."""
        return NotificationPayload(
            title="AI Response Ready 🤖",
            body=response_preview[:100],
            tag=f"chat-{session_id}",
            data={
                "type": NotificationType.CHAT_RESPONSE.value,
                "project_id": project_id,
                "session_id": session_id,
                "url": f"/editor/{project_id}",
            },
        )

    def create_review_complete_notification(
        self,
        project_name: str,
        score: int,
        issues_count: int,
        review_id: str,
    ) -> NotificationPayload:
        """Create code review complete notification."""
        emoji = "✅" if score >= 80 else "⚠️" if score >= 60 else "❌"
        return NotificationPayload(
            title=f"Code Review Complete {emoji}",
            body=f"'{project_name}' scored {score}/100 with {issues_count} issues found.",
            tag=f"review-{review_id}",
            data={
                "type": NotificationType.REVIEW_COMPLETE.value,
                "review_id": review_id,
                "score": score,
            },
            actions=[
                {"action": "open", "title": "View Report"},
                {"action": "dismiss", "title": "Dismiss"},
            ],
        )


# ============================================
# Database Schema for Push Subscriptions
# ============================================

PUSH_SUBSCRIPTIONS_SCHEMA = """
-- Push Subscriptions Table
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT unique_user_endpoint UNIQUE (user_id, endpoint)
);

-- Index for user lookups
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user 
ON push_subscriptions(user_id);

-- RLS Policies
ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage their subscriptions"
ON push_subscriptions
FOR ALL
USING (user_id = auth.uid()::text);

CREATE POLICY "Service can manage all subscriptions"
ON push_subscriptions
FOR ALL
USING (auth.role() = 'service_role');
"""


# Export
__all__ = [
    "PushNotificationService",
    "NotificationPayload",
    "NotificationType",
    "PushSubscription",
    "PUSH_SUBSCRIPTIONS_SCHEMA",
]
