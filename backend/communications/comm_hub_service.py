"""
Communication Hub Service

Unified inbox for all customer communications:
- Email (Gmail, Outlook)
- SMS (Twilio)
- WhatsApp Business
- Live Chat
- Social Media (Facebook, Instagram)

Features:
- Thread view across channels
- AI-powered auto-responses
- Smart routing
- Contact unification
- Analytics

Based on V PRD (December 2025)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4
import asyncio
from collections import defaultdict

# ============================================
# Enums
# ============================================


class Channel(str, Enum):
    """Communication channels."""

    EMAIL = "email"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    LIVE_CHAT = "live_chat"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    VOICE = "voice"


class MessageDirection(str, Enum):
    """Message direction."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(str, Enum):
    """Message delivery status."""

    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class ConversationStatus(str, Enum):
    """Conversation status."""

    OPEN = "open"
    PENDING = "pending"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class ConversationPriority(str, Enum):
    """Conversation priority."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


# ============================================
# Data Models
# ============================================


@dataclass
class Contact:
    """Unified contact across channels."""

    id: str = field(default_factory=lambda: f"contact_{uuid4().hex[:12]}")

    # Basic info
    name: str = ""
    email: Optional[str] = None
    phone: Optional[str] = None

    # Channel identifiers
    whatsapp_id: Optional[str] = None
    facebook_id: Optional[str] = None
    instagram_id: Optional[str] = None
    twitter_id: Optional[str] = None

    # Organization
    company: Optional[str] = None
    title: Optional[str] = None

    # Metadata
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    custom_fields: dict = field(default_factory=dict)

    # Stats
    total_conversations: int = 0
    last_contact: Optional[datetime] = None

    # Ownership
    user_id: str = ""
    organization_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "whatsapp_id": self.whatsapp_id,
            "facebook_id": self.facebook_id,
            "instagram_id": self.instagram_id,
            "company": self.company,
            "title": self.title,
            "tags": self.tags,
            "notes": self.notes,
            "total_conversations": self.total_conversations,
            "last_contact": (
                self.last_contact.isoformat() if self.last_contact else None
            ),
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Message:
    """A single message in a conversation."""

    id: str = field(default_factory=lambda: f"msg_{uuid4().hex[:12]}")
    conversation_id: str = ""

    # Content
    channel: Channel = Channel.EMAIL
    direction: MessageDirection = MessageDirection.INBOUND
    content: str = ""
    content_type: str = "text"  # text, image, file, audio, video

    # Attachments
    attachments: list[dict] = field(default_factory=list)

    # Metadata
    sender_id: str = ""
    sender_name: str = ""
    recipient_id: str = ""

    # Status
    status: MessageStatus = MessageStatus.PENDING

    # Email specific
    subject: Optional[str] = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)

    # AI
    ai_generated: bool = False
    ai_suggested: bool = False
    sentiment: Optional[str] = None  # positive, neutral, negative

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "channel": self.channel.value,
            "direction": self.direction.value,
            "content": self.content,
            "content_type": self.content_type,
            "attachments": self.attachments,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "recipient_id": self.recipient_id,
            "status": self.status.value,
            "subject": self.subject,
            "ai_generated": self.ai_generated,
            "sentiment": self.sentiment,
            "created_at": self.created_at.isoformat(),
            "delivered_at": (
                self.delivered_at.isoformat() if self.delivered_at else None
            ),
            "read_at": self.read_at.isoformat() if self.read_at else None,
        }


@dataclass
class Conversation:
    """A conversation thread with a contact."""

    id: str = field(default_factory=lambda: f"conv_{uuid4().hex[:12]}")

    # Participants
    contact_id: str = ""
    contact_name: str = ""
    user_id: str = ""
    organization_id: Optional[str] = None

    # Channel info
    channel: Channel = Channel.EMAIL
    channel_conversation_id: Optional[str] = None  # External ID

    # Status
    status: ConversationStatus = ConversationStatus.OPEN
    priority: ConversationPriority = ConversationPriority.NORMAL

    # Assignment
    assigned_to: Optional[str] = None
    assigned_team: Optional[str] = None

    # Content
    subject: str = ""
    last_message: str = ""
    last_message_at: Optional[datetime] = None

    # Counts
    message_count: int = 0
    unread_count: int = 0

    # Tags & Labels
    tags: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    # AI
    ai_summary: Optional[str] = None
    sentiment: Optional[str] = None
    intent: Optional[str] = None

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "contact_id": self.contact_id,
            "contact_name": self.contact_name,
            "channel": self.channel.value,
            "status": self.status.value,
            "priority": self.priority.value,
            "assigned_to": self.assigned_to,
            "assigned_team": self.assigned_team,
            "subject": self.subject,
            "last_message": self.last_message,
            "last_message_at": (
                self.last_message_at.isoformat() if self.last_message_at else None
            ),
            "message_count": self.message_count,
            "unread_count": self.unread_count,
            "tags": self.tags,
            "labels": self.labels,
            "ai_summary": self.ai_summary,
            "sentiment": self.sentiment,
            "intent": self.intent,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


@dataclass
class QuickReply:
    """Pre-defined quick reply template."""

    id: str = field(default_factory=lambda: f"qr_{uuid4().hex[:8]}")
    name: str = ""
    content: str = ""
    channel: Optional[Channel] = None  # None = all channels
    category: str = ""
    shortcut: Optional[str] = None  # e.g., /thanks
    user_id: str = ""
    usage_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "content": self.content,
            "channel": self.channel.value if self.channel else None,
            "category": self.category,
            "shortcut": self.shortcut,
            "usage_count": self.usage_count,
        }


@dataclass
class ChannelConfig:
    """Configuration for a communication channel."""

    channel: Channel
    enabled: bool = False
    connected: bool = False

    # Credentials (encrypted in production)
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    account_id: Optional[str] = None

    # Settings
    auto_reply_enabled: bool = False
    auto_reply_message: str = ""
    working_hours_only: bool = False

    # Stats
    messages_sent: int = 0
    messages_received: int = 0

    def to_dict(self) -> dict:
        return {
            "channel": self.channel.value,
            "enabled": self.enabled,
            "connected": self.connected,
            "auto_reply_enabled": self.auto_reply_enabled,
            "auto_reply_message": self.auto_reply_message,
            "working_hours_only": self.working_hours_only,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
        }


# ============================================
# Communication Hub Service
# ============================================


class CommunicationHubService:
    """
    Unified Communication Hub

    Manages all customer communications across channels.
    """

    def __init__(self):
        self._contacts: dict[str, Contact] = {}
        self._conversations: dict[str, Conversation] = {}
        self._messages: dict[str, list[Message]] = defaultdict(list)
        self._quick_replies: dict[str, QuickReply] = {}
        self._channel_configs: dict[str, dict[Channel, ChannelConfig]] = {}

        # Initialize with sample data
        self._init_sample_data()

    def _init_sample_data(self):
        """Initialize with sample conversations."""
        user_id = "user_123"

        # Sample contacts
        contacts = [
            Contact(
                id="contact_001",
                name="Sarah Johnson",
                email="sarah@example.com",
                phone="+1234567890",
                company="Acme Corp",
                user_id=user_id,
            ),
            Contact(
                id="contact_002",
                name="Mike Chen",
                email="mike@techstartup.io",
                phone="+1987654321",
                whatsapp_id="+1987654321",
                company="TechStartup",
                user_id=user_id,
            ),
            Contact(
                id="contact_003",
                name="Emily Davis",
                email="emily.davis@enterprise.com",
                company="Enterprise Inc",
                user_id=user_id,
            ),
        ]

        for c in contacts:
            self._contacts[c.id] = c

        # Sample conversations
        convs = [
            Conversation(
                id="conv_001",
                contact_id="contact_001",
                contact_name="Sarah Johnson",
                channel=Channel.EMAIL,
                subject="Question about pricing",
                last_message="Thanks for the quick response! I'll review the proposal.",
                status=ConversationStatus.OPEN,
                priority=ConversationPriority.HIGH,
                message_count=5,
                unread_count=1,
                user_id=user_id,
                sentiment="positive",
            ),
            Conversation(
                id="conv_002",
                contact_id="contact_002",
                contact_name="Mike Chen",
                channel=Channel.WHATSAPP,
                subject="Support Request",
                last_message="The issue is still happening after the update",
                status=ConversationStatus.OPEN,
                priority=ConversationPriority.URGENT,
                message_count=12,
                unread_count=3,
                user_id=user_id,
                sentiment="negative",
                tags=["support", "bug"],
            ),
            Conversation(
                id="conv_003",
                contact_id="contact_003",
                contact_name="Emily Davis",
                channel=Channel.EMAIL,
                subject="Partnership Inquiry",
                last_message="Looking forward to our call next week.",
                status=ConversationStatus.PENDING,
                priority=ConversationPriority.NORMAL,
                message_count=3,
                unread_count=0,
                user_id=user_id,
                sentiment="positive",
                tags=["partnership", "sales"],
            ),
        ]

        for conv in convs:
            conv.last_message_at = datetime.now(timezone.utc) - timedelta(
                hours=len(convs) - int(conv.id[-1])
            )
            self._conversations[conv.id] = conv

        # Sample messages
        self._messages["conv_001"] = [
            Message(
                id="msg_001",
                conversation_id="conv_001",
                channel=Channel.EMAIL,
                direction=MessageDirection.INBOUND,
                content="Hi, I'm interested in your enterprise plan. Can you send me pricing details?",
                sender_name="Sarah Johnson",
                subject="Question about pricing",
            ),
            Message(
                id="msg_002",
                conversation_id="conv_001",
                channel=Channel.EMAIL,
                direction=MessageDirection.OUTBOUND,
                content="Hi Sarah! Thanks for reaching out. I'd be happy to help. Our enterprise plan starts at $499/month. Would you like me to prepare a custom proposal?",
                sender_name="You",
            ),
            Message(
                id="msg_003",
                conversation_id="conv_001",
                channel=Channel.EMAIL,
                direction=MessageDirection.INBOUND,
                content="Yes please, we have about 50 users.",
                sender_name="Sarah Johnson",
            ),
            Message(
                id="msg_004",
                conversation_id="conv_001",
                channel=Channel.EMAIL,
                direction=MessageDirection.OUTBOUND,
                content="Great! I've attached a proposal for 50 users with volume discount. Let me know if you have any questions!",
                sender_name="You",
                attachments=[{"name": "proposal.pdf", "size": 125000}],
            ),
            Message(
                id="msg_005",
                conversation_id="conv_001",
                channel=Channel.EMAIL,
                direction=MessageDirection.INBOUND,
                content="Thanks for the quick response! I'll review the proposal.",
                sender_name="Sarah Johnson",
                sentiment="positive",
            ),
        ]

        self._messages["conv_002"] = [
            Message(
                id="msg_010",
                conversation_id="conv_002",
                channel=Channel.WHATSAPP,
                direction=MessageDirection.INBOUND,
                content="Hey, I'm having trouble with the app",
                sender_name="Mike Chen",
            ),
            Message(
                id="msg_011",
                conversation_id="conv_002",
                channel=Channel.WHATSAPP,
                direction=MessageDirection.OUTBOUND,
                content="Hi Mike! Sorry to hear that. What seems to be the issue?",
                sender_name="You",
            ),
            Message(
                id="msg_012",
                conversation_id="conv_002",
                channel=Channel.WHATSAPP,
                direction=MessageDirection.INBOUND,
                content="It keeps crashing when I try to export data",
                sender_name="Mike Chen",
                sentiment="negative",
            ),
            Message(
                id="msg_013",
                conversation_id="conv_002",
                channel=Channel.WHATSAPP,
                direction=MessageDirection.OUTBOUND,
                content="I understand. Can you try updating to the latest version (2.5.1)?",
                sender_name="You",
            ),
            Message(
                id="msg_014",
                conversation_id="conv_002",
                channel=Channel.WHATSAPP,
                direction=MessageDirection.INBOUND,
                content="The issue is still happening after the update",
                sender_name="Mike Chen",
                sentiment="negative",
            ),
        ]

    # ==========================================
    # Conversations
    # ==========================================

    def get_conversations(
        self,
        user_id: str,
        channel: Optional[Channel] = None,
        status: Optional[ConversationStatus] = None,
        priority: Optional[ConversationPriority] = None,
        search: Optional[str] = None,
        limit: int = 50,
    ) -> list[Conversation]:
        """Get conversations with filters."""
        convs = [c for c in self._conversations.values() if c.user_id == user_id]

        if channel:
            convs = [c for c in convs if c.channel == channel]
        if status:
            convs = [c for c in convs if c.status == status]
        if priority:
            convs = [c for c in convs if c.priority == priority]
        if search:
            search_lower = search.lower()
            convs = [
                c
                for c in convs
                if search_lower in c.contact_name.lower()
                or search_lower in c.subject.lower()
                or search_lower in c.last_message.lower()
            ]

        # Sort by last message time
        convs.sort(key=lambda c: c.last_message_at or c.created_at, reverse=True)

        return convs[:limit]

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Get conversation by ID."""
        return self._conversations.get(conversation_id)

    def create_conversation(
        self,
        user_id: str,
        contact_id: str,
        channel: Channel,
        subject: str = "",
    ) -> Conversation:
        """Create a new conversation."""
        contact = self._contacts.get(contact_id)

        conv = Conversation(
            user_id=user_id,
            contact_id=contact_id,
            contact_name=contact.name if contact else "Unknown",
            channel=channel,
            subject=subject,
        )

        self._conversations[conv.id] = conv
        return conv

    def update_conversation(
        self,
        conversation_id: str,
        updates: dict,
    ) -> Optional[Conversation]:
        """Update conversation."""
        conv = self._conversations.get(conversation_id)
        if not conv:
            return None

        for key, value in updates.items():
            if hasattr(conv, key):
                if key == "status":
                    value = ConversationStatus(value)
                elif key == "priority":
                    value = ConversationPriority(value)
                setattr(conv, key, value)

        conv.updated_at = datetime.now(timezone.utc)
        return conv

    def resolve_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Mark conversation as resolved."""
        conv = self._conversations.get(conversation_id)
        if conv:
            conv.status = ConversationStatus.RESOLVED
            conv.resolved_at = datetime.now(timezone.utc)
            conv.updated_at = datetime.now(timezone.utc)
        return conv

    # ==========================================
    # Messages
    # ==========================================

    def get_messages(
        self,
        conversation_id: str,
        limit: int = 100,
    ) -> list[Message]:
        """Get messages for a conversation."""
        messages = self._messages.get(conversation_id, [])
        return sorted(messages, key=lambda m: m.created_at)[-limit:]

    async def send_message(
        self,
        conversation_id: str,
        content: str,
        user_id: str,
        attachments: list[dict] = None,
    ) -> Optional[Message]:
        """Send a message in a conversation."""
        conv = self._conversations.get(conversation_id)
        if not conv:
            return None

        message = Message(
            conversation_id=conversation_id,
            channel=conv.channel,
            direction=MessageDirection.OUTBOUND,
            content=content,
            sender_id=user_id,
            sender_name="You",
            status=MessageStatus.SENT,
            attachments=attachments or [],
        )

        self._messages[conversation_id].append(message)

        # Update conversation
        conv.last_message = content[:100]
        conv.last_message_at = message.created_at
        conv.message_count += 1
        conv.updated_at = datetime.now(timezone.utc)

        # Simulate delivery
        await asyncio.sleep(0.3)
        message.status = MessageStatus.DELIVERED
        message.delivered_at = datetime.now(timezone.utc)

        return message

    async def receive_message(
        self,
        conversation_id: str,
        content: str,
        sender_name: str,
        channel: Channel,
    ) -> Message:
        """Receive an inbound message."""
        conv = self._conversations.get(conversation_id)

        message = Message(
            conversation_id=conversation_id,
            channel=channel,
            direction=MessageDirection.INBOUND,
            content=content,
            sender_name=sender_name,
            status=MessageStatus.DELIVERED,
        )

        self._messages[conversation_id].append(message)

        if conv:
            conv.last_message = content[:100]
            conv.last_message_at = message.created_at
            conv.message_count += 1
            conv.unread_count += 1
            conv.updated_at = datetime.now(timezone.utc)

            # AI: Analyze sentiment
            message.sentiment = self._analyze_sentiment(content)
            conv.sentiment = message.sentiment

        return message

    def _analyze_sentiment(self, text: str) -> str:
        """Simple sentiment analysis."""
        positive_words = [
            "thanks",
            "great",
            "awesome",
            "love",
            "excellent",
            "happy",
            "perfect",
        ]
        negative_words = [
            "issue",
            "problem",
            "error",
            "bug",
            "crash",
            "frustrated",
            "angry",
            "broken",
        ]

        text_lower = text.lower()
        pos_count = sum(1 for w in positive_words if w in text_lower)
        neg_count = sum(1 for w in negative_words if w in text_lower)

        if pos_count > neg_count:
            return "positive"
        elif neg_count > pos_count:
            return "negative"
        return "neutral"

    def mark_read(self, conversation_id: str) -> None:
        """Mark all messages in conversation as read."""
        conv = self._conversations.get(conversation_id)
        if conv:
            conv.unread_count = 0

        for msg in self._messages.get(conversation_id, []):
            if msg.direction == MessageDirection.INBOUND and not msg.read_at:
                msg.read_at = datetime.now(timezone.utc)
                msg.status = MessageStatus.READ

    # ==========================================
    # AI Features
    # ==========================================

    async def generate_reply_suggestion(
        self,
        conversation_id: str,
    ) -> str:
        """Generate AI reply suggestion."""
        messages = self.get_messages(conversation_id, limit=5)
        if not messages:
            return "Hello! How can I help you today?"

        last_msg = messages[-1]

        # Simple rule-based suggestions (in production, use LLM)
        suggestions = {
            "pricing": "I'd be happy to discuss our pricing options with you. Our plans start at $29/month for individuals and $99/month for teams. Would you like me to send you a detailed breakdown?",
            "issue": "I'm sorry to hear you're experiencing issues. Let me help you resolve this. Could you provide more details about what's happening?",
            "thanks": "You're welcome! Is there anything else I can help you with?",
            "meeting": "I'd be happy to schedule a meeting. What times work best for you this week?",
            "help": "Of course! I'm here to help. What would you like to know more about?",
        }

        content_lower = last_msg.content.lower()
        for keyword, response in suggestions.items():
            if keyword in content_lower:
                return response

        return "Thank you for your message. I'll look into this and get back to you shortly."

    async def summarize_conversation(self, conversation_id: str) -> str:
        """Generate AI summary of conversation."""
        messages = self.get_messages(conversation_id)
        conv = self._conversations.get(conversation_id)

        if not messages:
            return "No messages in this conversation."

        # Simple summary (in production, use LLM)
        msg_count = len(messages)
        inbound = len([m for m in messages if m.direction == MessageDirection.INBOUND])

        summary = f"Conversation with {conv.contact_name if conv else 'contact'} ({msg_count} messages, {inbound} from contact). "

        if conv and conv.sentiment == "positive":
            summary += "Overall positive sentiment. "
        elif conv and conv.sentiment == "negative":
            summary += "Attention needed - negative sentiment detected. "

        summary += f'Last message: "{messages[-1].content[:50]}..."'

        if conv:
            conv.ai_summary = summary

        return summary

    # ==========================================
    # Contacts
    # ==========================================

    def get_contacts(
        self,
        user_id: str,
        search: Optional[str] = None,
        limit: int = 50,
    ) -> list[Contact]:
        """Get contacts."""
        contacts = [c for c in self._contacts.values() if c.user_id == user_id]

        if search:
            search_lower = search.lower()
            contacts = [
                c
                for c in contacts
                if search_lower in c.name.lower()
                or (c.email and search_lower in c.email.lower())
            ]

        return contacts[:limit]

    def get_contact(self, contact_id: str) -> Optional[Contact]:
        """Get contact by ID."""
        return self._contacts.get(contact_id)

    def create_contact(self, user_id: str, data: dict) -> Contact:
        """Create a new contact."""
        contact = Contact(user_id=user_id, **data)
        self._contacts[contact.id] = contact
        return contact

    def update_contact(self, contact_id: str, updates: dict) -> Optional[Contact]:
        """Update contact."""
        contact = self._contacts.get(contact_id)
        if contact:
            for key, value in updates.items():
                if hasattr(contact, key):
                    setattr(contact, key, value)
        return contact

    # ==========================================
    # Quick Replies
    # ==========================================

    def get_quick_replies(
        self,
        user_id: str,
        channel: Optional[Channel] = None,
    ) -> list[QuickReply]:
        """Get quick replies."""
        replies = [qr for qr in self._quick_replies.values() if qr.user_id == user_id]

        if channel:
            replies = [
                qr for qr in replies if qr.channel is None or qr.channel == channel
            ]

        return sorted(replies, key=lambda qr: qr.usage_count, reverse=True)

    def create_quick_reply(self, user_id: str, data: dict) -> QuickReply:
        """Create quick reply."""
        qr = QuickReply(user_id=user_id, **data)
        self._quick_replies[qr.id] = qr
        return qr

    def use_quick_reply(self, quick_reply_id: str) -> Optional[QuickReply]:
        """Increment usage count."""
        qr = self._quick_replies.get(quick_reply_id)
        if qr:
            qr.usage_count += 1
        return qr

    # ==========================================
    # Stats
    # ==========================================

    def get_inbox_stats(self, user_id: str) -> dict:
        """Get inbox statistics."""
        convs = [c for c in self._conversations.values() if c.user_id == user_id]

        return {
            "total_conversations": len(convs),
            "open": len([c for c in convs if c.status == ConversationStatus.OPEN]),
            "pending": len(
                [c for c in convs if c.status == ConversationStatus.PENDING]
            ),
            "resolved": len(
                [c for c in convs if c.status == ConversationStatus.RESOLVED]
            ),
            "unread_messages": sum(c.unread_count for c in convs),
            "by_channel": {
                ch.value: len([c for c in convs if c.channel == ch])
                for ch in Channel
                if any(c.channel == ch for c in convs)
            },
            "by_priority": {
                p.value: len([c for c in convs if c.priority == p])
                for p in ConversationPriority
            },
            "urgent_count": len(
                [
                    c
                    for c in convs
                    if c.priority == ConversationPriority.URGENT
                    and c.status == ConversationStatus.OPEN
                ]
            ),
        }


# ============================================
# Singleton
# ============================================

_comm_hub_service: Optional[CommunicationHubService] = None


def get_comm_hub_service() -> CommunicationHubService:
    """Get communication hub service singleton."""
    global _comm_hub_service
    if _comm_hub_service is None:
        _comm_hub_service = CommunicationHubService()
    return _comm_hub_service


__all__ = [
    "CommunicationHubService",
    "Conversation",
    "Message",
    "Contact",
    "QuickReply",
    "ChannelConfig",
    "Channel",
    "MessageDirection",
    "MessageStatus",
    "ConversationStatus",
    "ConversationPriority",
    "get_comm_hub_service",
]
