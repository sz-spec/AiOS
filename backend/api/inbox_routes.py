"""
Communication Hub API Routes

REST API for unified inbox:
- Conversations
- Messages
- Contacts
- Quick Replies
- AI features
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser

from communications.comm_hub_service import (
    CommunicationHubService,
    Channel,
    ConversationStatus,
    ConversationPriority,
    get_comm_hub_service,
)

router = APIRouter(prefix="/inbox", tags=["inbox"])


# ============================================
# Request Models
# ============================================


class CreateConversationRequest(BaseModel):
    contact_id: str
    channel: str
    subject: str = ""


class SendMessageRequest(BaseModel):
    content: str
    attachments: list[dict] = []


class UpdateConversationRequest(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    tags: Optional[list[str]] = None


class CreateContactRequest(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    tags: list[str] = []
    notes: str = ""


class CreateQuickReplyRequest(BaseModel):
    name: str
    content: str
    category: str = ""
    shortcut: Optional[str] = None
    channel: Optional[str] = None


# ============================================
# Conversations
# ============================================


@router.get("/conversations")
async def get_conversations(
    channel: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Get conversations with filters."""
    ch = Channel(channel) if channel else None
    st = ConversationStatus(status) if status else None
    pr = ConversationPriority(priority) if priority else None

    convs = service.get_conversations(
        user_id=user.id,
        channel=ch,
        status=st,
        priority=pr,
        search=search,
        limit=limit,
    )

    return {
        "count": len(convs),
        "conversations": [c.to_dict() for c in convs],
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Get conversation by ID."""
    conv = service.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv.to_dict()


@router.post("/conversations")
async def create_conversation(
    request: CreateConversationRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Create a new conversation."""
    conv = service.create_conversation(
        user_id=user.id,
        contact_id=request.contact_id,
        channel=Channel(request.channel),
        subject=request.subject,
    )
    return conv.to_dict()


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Update conversation."""
    updates = {k: v for k, v in request.dict().items() if v is not None}
    conv = service.update_conversation(conversation_id, updates)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv.to_dict()


@router.post("/conversations/{conversation_id}/resolve")
async def resolve_conversation(
    conversation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Mark conversation as resolved."""
    conv = service.resolve_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"message": "Conversation resolved", "conversation": conv.to_dict()}


@router.post("/conversations/{conversation_id}/read")
async def mark_conversation_read(
    conversation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Mark conversation as read."""
    service.mark_read(conversation_id)
    return {"message": "Marked as read"}


# ============================================
# Messages
# ============================================


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    limit: int = 100,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Get messages for a conversation."""
    messages = service.get_messages(conversation_id, limit=limit)
    return {
        "count": len(messages),
        "messages": [m.to_dict() for m in messages],
    }


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Send a message."""
    message = await service.send_message(
        conversation_id=conversation_id,
        content=request.content,
        user_id=user.id,
        attachments=request.attachments,
    )

    if not message:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return message.to_dict()


# ============================================
# AI Features
# ============================================


@router.get("/conversations/{conversation_id}/suggest-reply")
async def suggest_reply(
    conversation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Get AI-suggested reply."""
    suggestion = await service.generate_reply_suggestion(conversation_id)
    return {"suggestion": suggestion}


@router.get("/conversations/{conversation_id}/summary")
async def get_conversation_summary(
    conversation_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Get AI summary of conversation."""
    summary = await service.summarize_conversation(conversation_id)
    return {"summary": summary}


# ============================================
# Contacts
# ============================================


@router.get("/contacts")
async def get_contacts(
    search: Optional[str] = None,
    limit: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Get contacts."""
    contacts = service.get_contacts(user.id, search=search, limit=limit)
    return {
        "count": len(contacts),
        "contacts": [c.to_dict() for c in contacts],
    }


@router.get("/contacts/{contact_id}")
async def get_contact(
    contact_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Get contact by ID."""
    contact = service.get_contact(contact_id)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact.to_dict()


@router.post("/contacts")
async def create_contact(
    request: CreateContactRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Create a new contact."""
    contact = service.create_contact(user.id, request.dict())
    return contact.to_dict()


@router.patch("/contacts/{contact_id}")
async def update_contact(
    contact_id: str,
    request: CreateContactRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Update contact."""
    updates = {k: v for k, v in request.dict().items() if v is not None}
    contact = service.update_contact(contact_id, updates)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact.to_dict()


# ============================================
# Quick Replies
# ============================================


@router.get("/quick-replies")
async def get_quick_replies(
    channel: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Get quick replies."""
    ch = Channel(channel) if channel else None
    replies = service.get_quick_replies(user.id, channel=ch)
    return {
        "count": len(replies),
        "quick_replies": [qr.to_dict() for qr in replies],
    }


@router.post("/quick-replies")
async def create_quick_reply(
    request: CreateQuickReplyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Create quick reply."""
    data = request.dict()
    if data.get("channel"):
        data["channel"] = Channel(data["channel"])
    qr = service.create_quick_reply(user.id, data)
    return qr.to_dict()


@router.post("/quick-replies/{quick_reply_id}/use")
async def use_quick_reply(
    quick_reply_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Mark quick reply as used."""
    qr = service.use_quick_reply(quick_reply_id)
    if not qr:
        raise HTTPException(status_code=404, detail="Quick reply not found")
    return {"content": qr.content}


# ============================================
# Stats
# ============================================


@router.get("/stats")
async def get_inbox_stats(
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommunicationHubService = Depends(get_comm_hub_service),
):
    """Get inbox statistics."""
    return service.get_inbox_stats(user.id)


# ============================================
# Channels
# ============================================


@router.get("/channels")
async def get_channels(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get available channels."""
    return {
        "channels": [
            {"id": "email", "name": "Email", "icon": "📧"},
            {"id": "sms", "name": "SMS", "icon": "💬"},
            {"id": "whatsapp", "name": "WhatsApp", "icon": "💚"},
            {"id": "live_chat", "name": "Live Chat", "icon": "🗨️"},
            {"id": "facebook", "name": "Facebook", "icon": "👤"},
            {"id": "instagram", "name": "Instagram", "icon": "📷"},
            {"id": "twitter", "name": "Twitter", "icon": "🐦"},
        ]
    }


__all__ = ["router"]
