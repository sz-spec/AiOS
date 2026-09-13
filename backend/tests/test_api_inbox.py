"""
Tests for inbox_routes — Communication Hub API (/api/inbox/*).

The inbox router uses CommunicationHubService injected via
Depends(get_comm_hub_service).  All service methods are mocked via
app.dependency_overrides so no real network or DB calls occur.

The service returns objects whose .to_dict() method is called by the routes.
Mocks are configured so that every method (sync or async) returns a plain dict,
which the mock's to_dict() will also return, matching the real service contract.

Covers 18+ endpoints across conversations, messages, AI features,
contacts, quick replies, stats, and channels.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app

from communications.comm_hub_service import get_comm_hub_service

# ---------------------------------------------------------------------------
# Shared mock data
# ---------------------------------------------------------------------------

MOCK_CONV = {
    "id": "conv1",
    "contact_id": "contact1",
    "channel": "email",
    "subject": "Test",
    "status": "open",
    "priority": "normal",
    "assigned_to": None,
    "tags": [],
    "message_count": 0,
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00",
}
MOCK_MSG = {
    "id": "msg1",
    "conversation_id": "conv1",
    "content": "Hello",
    "sender_type": "user",
    "sender_id": "user_123",
    "attachments": [],
    "created_at": "2024-01-01T00:00:00",
}
MOCK_CONTACT = {
    "id": "contact1",
    "name": "Alice",
    "email": "alice@test.com",
    "phone": None,
    "company": None,
    "title": None,
    "tags": [],
    "notes": None,
    "created_at": "2024-01-01T00:00:00",
}
MOCK_QUICK_REPLY = {
    "id": "qr1",
    "name": "Thanks",
    "content": "Thank you!",
    "category": "general",
    "shortcut": "ty",
    "channel": None,
    "use_count": 5,
    "created_at": "2024-01-01T00:00:00",
}


def _make_obj(data: dict) -> MagicMock:
    """Wrap a dict in a MagicMock whose .to_dict() returns that dict."""
    obj = MagicMock()
    obj.to_dict.return_value = data
    return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def mock_inbox():
    svc = MagicMock()

    # Conversations — synchronous service methods return objects with to_dict()
    conv_obj = _make_obj(MOCK_CONV)
    resolved_conv_obj = _make_obj({**MOCK_CONV, "status": "resolved"})
    msg_obj = _make_obj(MOCK_MSG)
    contact_obj = _make_obj(MOCK_CONTACT)
    qr_obj = _make_obj(MOCK_QUICK_REPLY)
    used_qr_obj = _make_obj(
        {**MOCK_QUICK_REPLY, "use_count": 6, "content": "Thank you!"}
    )
    used_qr_obj.content = "Thank you!"  # route uses qr.content directly

    svc.get_conversations.return_value = [conv_obj]
    svc.get_conversation.return_value = conv_obj
    svc.create_conversation.return_value = conv_obj
    svc.update_conversation.return_value = conv_obj
    svc.resolve_conversation.return_value = resolved_conv_obj
    svc.mark_read.return_value = conv_obj

    # Messages — send_message is called with await in the route
    svc.get_messages.return_value = [msg_obj]
    svc.send_message = AsyncMock(return_value=msg_obj)

    # AI features — also async in the route
    svc.generate_reply_suggestion = AsyncMock(return_value="You're welcome!")
    svc.summarize_conversation = AsyncMock(return_value="User asked about product.")

    # Contacts
    svc.get_contacts.return_value = [contact_obj]
    svc.get_contact.return_value = contact_obj
    svc.create_contact.return_value = contact_obj
    svc.update_contact.return_value = contact_obj

    # Quick replies
    svc.get_quick_replies.return_value = [qr_obj]
    svc.create_quick_reply.return_value = qr_obj
    svc.use_quick_reply.return_value = used_qr_obj

    # Stats
    svc.get_inbox_stats.return_value = {"open_conversations": 5, "total_messages": 20}

    app.dependency_overrides[get_comm_hub_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_comm_hub_service, None)


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class TestListConversations:
    def test_returns_200_with_conversations_list(self, client, mock_inbox):
        resp = client.get("/api/inbox/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert "conversations" in data
        assert "count" in data
        assert data["count"] == 1

    def test_conversations_contain_expected_fields(self, client, mock_inbox):
        resp = client.get("/api/inbox/conversations")
        assert resp.status_code == 200
        conv = resp.json()["conversations"][0]
        assert conv["id"] == "conv1"
        assert conv["channel"] == "email"
        assert conv["status"] == "open"

    def test_list_calls_service_get_conversations(self, client, mock_inbox):
        client.get("/api/inbox/conversations")
        mock_inbox.get_conversations.assert_called_once()


class TestGetConversation:
    def test_returns_200_for_existing_conversation(self, client, mock_inbox):
        resp = client.get("/api/inbox/conversations/conv1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "conv1"

    def test_returns_404_when_conversation_not_found(self, client, mock_inbox):
        mock_inbox.get_conversation.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).get("/api/inbox/conversations/missing")
        assert resp.status_code == 404


class TestCreateConversation:
    def test_returns_201_or_200_with_created_conversation(self, client, mock_inbox):
        resp = client.post(
            "/api/inbox/conversations",
            json={"contact_id": "contact1", "channel": "email", "subject": "Hello"},
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["id"] == "conv1"
        assert data["contact_id"] == "contact1"

    def test_create_calls_service_create_conversation(self, client, mock_inbox):
        client.post(
            "/api/inbox/conversations",
            json={"contact_id": "contact1", "channel": "email", "subject": "Hi"},
        )
        mock_inbox.create_conversation.assert_called_once()


class TestUpdateConversation:
    def test_patch_returns_200_with_updated_conversation(self, client, mock_inbox):
        resp = client.patch(
            "/api/inbox/conversations/conv1", json={"status": "resolved"}
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "conv1"

    def test_patch_returns_404_when_not_found(self, client, mock_inbox):
        mock_inbox.update_conversation.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).patch("/api/inbox/conversations/missing", json={"status": "resolved"})
        assert resp.status_code == 404


class TestResolveConversation:
    def test_resolve_returns_200_with_resolved_status(self, client, mock_inbox):
        resp = client.post("/api/inbox/conversations/conv1/resolve")
        assert resp.status_code == 200
        data = resp.json()
        assert "conversation" in data
        assert data["conversation"]["status"] == "resolved"

    def test_resolve_returns_404_when_not_found(self, client, mock_inbox):
        mock_inbox.resolve_conversation.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).post("/api/inbox/conversations/missing/resolve")
        assert resp.status_code == 404


class TestMarkConversationRead:
    def test_mark_read_returns_200(self, client, mock_inbox):
        resp = client.post("/api/inbox/conversations/conv1/read")
        assert resp.status_code == 200
        assert "message" in resp.json()

    def test_mark_read_calls_service(self, client, mock_inbox):
        client.post("/api/inbox/conversations/conv1/read")
        mock_inbox.mark_read.assert_called_once_with("conv1")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestGetMessages:
    def test_returns_200_with_messages_list(self, client, mock_inbox):
        resp = client.get("/api/inbox/conversations/conv1/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert "count" in data
        assert data["count"] == 1

    def test_messages_contain_expected_fields(self, client, mock_inbox):
        resp = client.get("/api/inbox/conversations/conv1/messages")
        msg = resp.json()["messages"][0]
        assert msg["id"] == "msg1"
        assert msg["content"] == "Hello"
        assert msg["sender_type"] == "user"


class TestSendMessage:
    def test_send_returns_200_with_message(self, client, mock_inbox):
        resp = client.post(
            "/api/inbox/conversations/conv1/messages", json={"content": "Hello there"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "msg1"
        assert data["content"] == "Hello"

    def test_send_returns_404_when_conversation_missing(self, client, mock_inbox):
        mock_inbox.send_message = AsyncMock(return_value=None)
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).post("/api/inbox/conversations/missing/messages", json={"content": "Hi"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AI Features
# ---------------------------------------------------------------------------


class TestSuggestReply:
    def test_suggest_reply_returns_200_with_suggestion(self, client, mock_inbox):
        resp = client.get("/api/inbox/conversations/conv1/suggest-reply")
        assert resp.status_code == 200
        data = resp.json()
        assert "suggestion" in data
        assert isinstance(data["suggestion"], str)


class TestGetConversationSummary:
    def test_summary_returns_200_with_summary_text(self, client, mock_inbox):
        resp = client.get("/api/inbox/conversations/conv1/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert isinstance(data["summary"], str)


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


class TestListContacts:
    def test_returns_200_with_contacts_list(self, client, mock_inbox):
        resp = client.get("/api/inbox/contacts")
        assert resp.status_code == 200
        data = resp.json()
        assert "contacts" in data
        assert data["count"] == 1

    def test_contacts_contain_expected_fields(self, client, mock_inbox):
        resp = client.get("/api/inbox/contacts")
        contact = resp.json()["contacts"][0]
        assert contact["id"] == "contact1"
        assert contact["name"] == "Alice"
        assert contact["email"] == "alice@test.com"


class TestGetContact:
    def test_returns_200_for_existing_contact(self, client, mock_inbox):
        resp = client.get("/api/inbox/contacts/contact1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "contact1"

    def test_returns_404_when_contact_not_found(self, client, mock_inbox):
        mock_inbox.get_contact.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).get("/api/inbox/contacts/missing")
        assert resp.status_code == 404


class TestCreateContact:
    def test_create_returns_200_with_contact(self, client, mock_inbox):
        resp = client.post(
            "/api/inbox/contacts", json={"name": "Alice", "email": "alice@test.com"}
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["name"] == "Alice"
        assert data["email"] == "alice@test.com"

    def test_create_calls_service(self, client, mock_inbox):
        client.post("/api/inbox/contacts", json={"name": "Bob"})
        mock_inbox.create_contact.assert_called_once()


class TestUpdateContact:
    def test_patch_returns_200_with_updated_contact(self, client, mock_inbox):
        resp = client.patch("/api/inbox/contacts/contact1", json={"name": "Alice B."})
        assert resp.status_code == 200
        assert resp.json()["id"] == "contact1"

    def test_patch_returns_404_when_contact_not_found(self, client, mock_inbox):
        mock_inbox.update_contact.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).patch("/api/inbox/contacts/missing", json={"name": "Ghost"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Quick Replies
# ---------------------------------------------------------------------------


class TestListQuickReplies:
    def test_returns_200_with_quick_replies_list(self, client, mock_inbox):
        resp = client.get("/api/inbox/quick-replies")
        assert resp.status_code == 200
        data = resp.json()
        assert "quick_replies" in data
        assert data["count"] == 1

    def test_quick_replies_contain_expected_fields(self, client, mock_inbox):
        resp = client.get("/api/inbox/quick-replies")
        qr = resp.json()["quick_replies"][0]
        assert qr["id"] == "qr1"
        assert qr["name"] == "Thanks"
        assert qr["content"] == "Thank you!"


class TestCreateQuickReply:
    def test_create_returns_200_with_quick_reply(self, client, mock_inbox):
        resp = client.post(
            "/api/inbox/quick-replies",
            json={"name": "Thanks", "content": "Thank you!", "category": "general"},
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert data["name"] == "Thanks"

    def test_create_calls_service(self, client, mock_inbox):
        client.post(
            "/api/inbox/quick-replies",
            json={"name": "Bye", "content": "Goodbye!", "category": "general"},
        )
        mock_inbox.create_quick_reply.assert_called_once()


class TestUseQuickReply:
    def test_use_returns_200_with_content(self, client, mock_inbox):
        resp = client.post("/api/inbox/quick-replies/qr1/use")
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data
        assert data["content"] == "Thank you!"

    def test_use_returns_404_when_not_found(self, client, mock_inbox):
        mock_inbox.use_quick_reply.return_value = None
        resp = TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ).post("/api/inbox/quick-replies/missing/use")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Stats and Channels
# ---------------------------------------------------------------------------


class TestInboxStats:
    def test_stats_returns_200_with_stats(self, client, mock_inbox):
        resp = client.get("/api/inbox/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "open_conversations" in data
        assert data["open_conversations"] == 5

    def test_stats_calls_service_get_inbox_stats(self, client, mock_inbox):
        client.get("/api/inbox/stats")
        mock_inbox.get_inbox_stats.assert_called_once()


class TestGetChannels:
    def test_channels_returns_200_with_channels_list(self, client, mock_inbox):
        resp = client.get("/api/inbox/channels")
        assert resp.status_code == 200
        data = resp.json()
        assert "channels" in data
        assert isinstance(data["channels"], list)
        assert len(data["channels"]) > 0

    def test_channels_contain_id_and_name(self, client, mock_inbox):
        resp = client.get("/api/inbox/channels")
        channels = resp.json()["channels"]
        for ch in channels:
            assert "id" in ch
            assert "name" in ch

    def test_email_channel_is_present(self, client, mock_inbox):
        resp = client.get("/api/inbox/channels")
        ids = [ch["id"] for ch in resp.json()["channels"]]
        assert "email" in ids
