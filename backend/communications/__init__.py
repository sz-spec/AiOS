"""
Communications Hub Module

Unified inbox for all customer communications:
- Email
- SMS
- WhatsApp
- Live Chat
- Social Media
"""

from communications.comm_hub_service import (
    CommunicationHubService,
    Conversation,
    Message,
    Contact,
    QuickReply,
    ChannelConfig,
    Channel,
    MessageDirection,
    MessageStatus,
    ConversationStatus,
    ConversationPriority,
    get_comm_hub_service,
)

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
