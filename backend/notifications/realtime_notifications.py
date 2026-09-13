"""
Realtime Notifications Service

Manages live notifications for:
- Project updates
- File changes
- AI generation progress
- Deploy status
- Team activity
- System alerts
"""

import asyncio
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional
from uuid import uuid4
from collections import defaultdict

# ============================================
# Enums
# ============================================


class NotificationType(str, Enum):
    """Types of notifications."""

    # Project
    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    PROJECT_DELETED = "project_deleted"

    # Files
    FILE_CREATED = "file_created"
    FILE_UPDATED = "file_updated"
    FILE_DELETED = "file_deleted"

    # AI
    AI_GENERATION_STARTED = "ai_generation_started"
    AI_GENERATION_PROGRESS = "ai_generation_progress"
    AI_GENERATION_COMPLETED = "ai_generation_completed"
    AI_GENERATION_FAILED = "ai_generation_failed"

    # Build
    BUILD_STARTED = "build_started"
    BUILD_PROGRESS = "build_progress"
    BUILD_COMPLETED = "build_completed"
    BUILD_FAILED = "build_failed"

    # Deploy
    DEPLOY_STARTED = "deploy_started"
    DEPLOY_PROGRESS = "deploy_progress"
    DEPLOY_COMPLETED = "deploy_completed"
    DEPLOY_FAILED = "deploy_failed"

    # Team
    MEMBER_JOINED = "member_joined"
    MEMBER_LEFT = "member_left"
    COMMENT_ADDED = "comment_added"
    MENTION = "mention"

    # System
    SYSTEM_ALERT = "system_alert"
    MAINTENANCE = "maintenance"
    UPDATE_AVAILABLE = "update_available"


class NotificationPriority(str, Enum):
    """Notification priority levels."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class NotificationChannel(str, Enum):
    """Delivery channels."""

    IN_APP = "in_app"
    PUSH = "push"
    EMAIL = "email"
    WEBHOOK = "webhook"


# ============================================
# Data Models
# ============================================


@dataclass
class Notification:
    """A single notification."""

    id: str = field(default_factory=lambda: f"notif_{uuid4().hex[:12]}")

    # Type and priority
    type: NotificationType = NotificationType.SYSTEM_ALERT
    priority: NotificationPriority = NotificationPriority.NORMAL

    # Target
    user_id: Optional[str] = None
    project_id: Optional[str] = None
    organization_id: Optional[str] = None

    # Content
    title: str = ""
    message: str = ""
    data: dict = field(default_factory=dict)

    # Actions
    action_url: Optional[str] = None
    action_label: Optional[str] = None

    # State
    read: bool = False
    dismissed: bool = False

    # Channels
    channels: list[NotificationChannel] = field(
        default_factory=lambda: [NotificationChannel.IN_APP]
    )

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    read_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            **asdict(self),
            "type": self.type.value,
            "priority": self.priority.value,
            "channels": [c.value for c in self.channels],
            "created_at": self.created_at.isoformat(),
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class NotificationPreferences:
    """User notification preferences."""

    user_id: str

    # Channel preferences
    in_app_enabled: bool = True
    push_enabled: bool = True
    email_enabled: bool = True

    # Type preferences (disabled types)
    disabled_types: list[str] = field(default_factory=list)

    # Quiet hours
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "22:00"  # HH:MM
    quiet_hours_end: str = "08:00"

    # Email digest
    email_digest: bool = False  # Send digest instead of individual emails
    digest_frequency: str = "daily"  # daily, weekly


@dataclass
class RealtimeEvent:
    """A realtime event broadcast to subscribers."""

    id: str = field(default_factory=lambda: f"evt_{uuid4().hex[:8]}")

    event_type: str = ""
    channel: str = ""  # user:{id}, project:{id}, org:{id}, global

    data: dict = field(default_factory=dict)

    sender_id: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "timestamp": self.timestamp.isoformat(),
        }


# ============================================
# Realtime Service
# ============================================


class RealtimeNotificationService:
    """Service for realtime notifications."""

    def __init__(self):
        # Storage
        self._notifications: dict[str, Notification] = {}
        self._user_notifications: dict[str, list[str]] = defaultdict(list)
        self._preferences: dict[str, NotificationPreferences] = {}

        # Subscriptions: channel -> list of (user_id, callback)
        self._subscriptions: dict[str, list[tuple[str, Callable]]] = defaultdict(list)

        # Event queue for broadcasting
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._broadcast_task: Optional[asyncio.Task] = None

    # ==========================================
    # Notification CRUD
    # ==========================================

    def create_notification(
        self,
        user_id: str,
        type: NotificationType,
        title: str,
        message: str,
        project_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        data: Optional[dict] = None,
        action_url: Optional[str] = None,
        action_label: Optional[str] = None,
        channels: Optional[list[NotificationChannel]] = None,
    ) -> Notification:
        """Create and store a notification."""
        # Check preferences
        prefs = self._preferences.get(user_id)
        if prefs:
            if type.value in prefs.disabled_types:
                return None  # User disabled this type

        notification = Notification(
            user_id=user_id,
            project_id=project_id,
            organization_id=organization_id,
            type=type,
            priority=priority,
            title=title,
            message=message,
            data=data or {},
            action_url=action_url,
            action_label=action_label,
            channels=channels or [NotificationChannel.IN_APP],
        )

        # Store
        self._notifications[notification.id] = notification
        self._user_notifications[user_id].append(notification.id)

        # Broadcast to user's channel
        self._broadcast_to_channel(
            channel=f"user:{user_id}",
            event_type="notification",
            data=notification.to_dict(),
        )

        return notification

    def get_notification(self, notification_id: str) -> Optional[Notification]:
        """Get a notification by ID."""
        return self._notifications.get(notification_id)

    def list_notifications(
        self,
        user_id: str,
        unread_only: bool = False,
        type_filter: Optional[NotificationType] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Notification], int]:
        """List notifications for a user."""
        notif_ids = self._user_notifications.get(user_id, [])
        notifications = [
            self._notifications[nid]
            for nid in notif_ids
            if nid in self._notifications and not self._notifications[nid].dismissed
        ]

        # Filter
        if unread_only:
            notifications = [n for n in notifications if not n.read]

        if type_filter:
            notifications = [n for n in notifications if n.type == type_filter]

        # Sort by created_at desc
        notifications.sort(key=lambda n: n.created_at, reverse=True)

        total = len(notifications)
        notifications = notifications[offset : offset + limit]

        return notifications, total

    def mark_as_read(
        self,
        notification_id: str,
        user_id: str,
    ) -> bool:
        """Mark a notification as read."""
        notification = self._notifications.get(notification_id)
        if not notification or notification.user_id != user_id:
            return False

        notification.read = True
        notification.read_at = datetime.now(timezone.utc)
        return True

    def mark_all_as_read(self, user_id: str) -> int:
        """Mark all notifications as read."""
        count = 0
        notif_ids = self._user_notifications.get(user_id, [])

        for nid in notif_ids:
            notification = self._notifications.get(nid)
            if notification and not notification.read:
                notification.read = True
                notification.read_at = datetime.now(timezone.utc)
                count += 1

        return count

    def dismiss_notification(
        self,
        notification_id: str,
        user_id: str,
    ) -> bool:
        """Dismiss (hide) a notification."""
        notification = self._notifications.get(notification_id)
        if not notification or notification.user_id != user_id:
            return False

        notification.dismissed = True
        return True

    def get_unread_count(self, user_id: str) -> int:
        """Get count of unread notifications."""
        notif_ids = self._user_notifications.get(user_id, [])
        return sum(
            1
            for nid in notif_ids
            if nid in self._notifications
            and not self._notifications[nid].read
            and not self._notifications[nid].dismissed
        )

    # ==========================================
    # Preferences
    # ==========================================

    def get_preferences(self, user_id: str) -> NotificationPreferences:
        """Get user's notification preferences."""
        if user_id not in self._preferences:
            self._preferences[user_id] = NotificationPreferences(user_id=user_id)
        return self._preferences[user_id]

    def update_preferences(
        self,
        user_id: str,
        **kwargs,
    ) -> NotificationPreferences:
        """Update notification preferences."""
        prefs = self.get_preferences(user_id)

        for key, value in kwargs.items():
            if hasattr(prefs, key):
                setattr(prefs, key, value)

        return prefs

    # ==========================================
    # Realtime Broadcasting
    # ==========================================

    def subscribe(
        self,
        channel: str,
        user_id: str,
        callback: Callable[[RealtimeEvent], None],
    ) -> str:
        """Subscribe to a channel."""
        subscription_id = f"sub_{uuid4().hex[:8]}"
        self._subscriptions[channel].append((user_id, callback, subscription_id))
        return subscription_id

    def unsubscribe(self, channel: str, subscription_id: str) -> bool:
        """Unsubscribe from a channel."""
        subs = self._subscriptions.get(channel, [])
        self._subscriptions[channel] = [
            (uid, cb, sid) for uid, cb, sid in subs if sid != subscription_id
        ]
        return True

    def _broadcast_to_channel(
        self,
        channel: str,
        event_type: str,
        data: dict,
        sender_id: Optional[str] = None,
    ):
        """Broadcast an event to a channel."""
        event = RealtimeEvent(
            event_type=event_type,
            channel=channel,
            data=data,
            sender_id=sender_id,
        )

        subscribers = self._subscriptions.get(channel, [])
        for user_id, callback, sub_id in subscribers:
            try:
                callback(event)
            except Exception as e:
                print(f"Error broadcasting to {user_id}: {e}")

    def broadcast_project_update(
        self,
        project_id: str,
        event_type: str,
        data: dict,
        sender_id: Optional[str] = None,
        notify_users: Optional[list[str]] = None,
    ):
        """Broadcast a project update."""
        # Broadcast to project channel
        self._broadcast_to_channel(
            channel=f"project:{project_id}",
            event_type=event_type,
            data={"project_id": project_id, **data},
            sender_id=sender_id,
        )

        # Create notifications for users
        if notify_users:
            for user_id in notify_users:
                if user_id != sender_id:  # Don't notify sender
                    self.create_notification(
                        user_id=user_id,
                        type=NotificationType.PROJECT_UPDATED,
                        title="Project Updated",
                        message=data.get("message", "Project was updated"),
                        project_id=project_id,
                        data=data,
                    )

    def broadcast_ai_progress(
        self,
        user_id: str,
        project_id: Optional[str],
        status: str,
        progress: float,
        message: str,
        data: Optional[dict] = None,
    ):
        """Broadcast AI generation progress."""
        self._broadcast_to_channel(
            channel=f"user:{user_id}",
            event_type="ai_progress",
            data={
                "status": status,
                "progress": progress,
                "message": message,
                "project_id": project_id,
                **(data or {}),
            },
        )

    def broadcast_deploy_status(
        self,
        project_id: str,
        status: str,
        progress: float,
        message: str,
        url: Optional[str] = None,
        error: Optional[str] = None,
    ):
        """Broadcast deployment status."""
        self._broadcast_to_channel(
            channel=f"project:{project_id}",
            event_type="deploy_status",
            data={
                "status": status,
                "progress": progress,
                "message": message,
                "url": url,
                "error": error,
            },
        )

    # ==========================================
    # Convenience Methods
    # ==========================================

    def notify_project_created(
        self,
        user_id: str,
        project_id: str,
        project_name: str,
    ):
        """Notify about project creation."""
        return self.create_notification(
            user_id=user_id,
            type=NotificationType.PROJECT_CREATED,
            title="Project Created",
            message=f"Your project '{project_name}' has been created",
            project_id=project_id,
            action_url=f"/projects/{project_id}",
            action_label="Open Project",
        )

    def notify_ai_completed(
        self,
        user_id: str,
        project_id: Optional[str],
        files_generated: int,
        tokens_used: int,
    ):
        """Notify about AI generation completion."""
        return self.create_notification(
            user_id=user_id,
            type=NotificationType.AI_GENERATION_COMPLETED,
            title="Code Generated",
            message=f"Generated {files_generated} file(s) using {tokens_used:,} tokens",
            project_id=project_id,
            priority=NotificationPriority.NORMAL,
            data={"files_generated": files_generated, "tokens_used": tokens_used},
        )

    def notify_deploy_success(
        self,
        user_id: str,
        project_id: str,
        deploy_url: str,
    ):
        """Notify about successful deployment."""
        return self.create_notification(
            user_id=user_id,
            type=NotificationType.DEPLOY_COMPLETED,
            title="Deployment Successful",
            message="Your project is now live!",
            project_id=project_id,
            priority=NotificationPriority.HIGH,
            action_url=deploy_url,
            action_label="View Site",
            data={"deploy_url": deploy_url},
        )

    def notify_deploy_failed(
        self,
        user_id: str,
        project_id: str,
        error: str,
    ):
        """Notify about failed deployment."""
        return self.create_notification(
            user_id=user_id,
            type=NotificationType.DEPLOY_FAILED,
            title="Deployment Failed",
            message=f"Deployment failed: {error[:100]}",
            project_id=project_id,
            priority=NotificationPriority.HIGH,
            data={"error": error},
        )

    def notify_mention(
        self,
        user_id: str,
        mentioned_by: str,
        project_id: str,
        comment: str,
    ):
        """Notify about being mentioned."""
        return self.create_notification(
            user_id=user_id,
            type=NotificationType.MENTION,
            title=f"{mentioned_by} mentioned you",
            message=comment[:100],
            project_id=project_id,
            priority=NotificationPriority.HIGH,
            action_url=f"/projects/{project_id}#comments",
            action_label="View Comment",
        )


# ============================================
# Singleton
# ============================================

_realtime_service: Optional[RealtimeNotificationService] = None


def get_realtime_service() -> RealtimeNotificationService:
    """Get realtime service singleton."""
    global _realtime_service
    if _realtime_service is None:
        _realtime_service = RealtimeNotificationService()
    return _realtime_service


__all__ = [
    "RealtimeNotificationService",
    "Notification",
    "NotificationType",
    "NotificationPriority",
    "NotificationChannel",
    "NotificationPreferences",
    "RealtimeEvent",
    "get_realtime_service",
]
