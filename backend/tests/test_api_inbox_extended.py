"""
Extended tests for inbox_routes -- Communication Hub API (/api/inbox/*).

Covers edge cases and low-coverage areas NOT in test_api_inbox.py:
1. Conversation query-param filters (channel, status, priority, search, limit)
2. Search edge cases (empty results, special characters)
3. Create conversation validation (missing required fields, invalid channel)
4. Partial PATCH updates (status-only, priority-only, tags-only, mixed, empty)
5. Message sending edge cases (empty content, large content, attachments, missing field)
6. Contact validation (missing name, duplicate email, all optional fields, search passthrough)
7. Quick reply channel filtering
8. Stats response structure verification
9. Channels endpoint structure and completeness

Uses a locally-created FastAPI app with the inbox router mounted directly,
bypassing the production middleware stack (api_key, auth) that requires
env vars only available in deployment.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.inbox_routes import router as inbox_router
from communications.comm_hub_service import get_comm_hub_service
from api.deps import get_current_user, AuthenticatedUser

# ---------------------------------------------------------------------------
# Build a minimal app with just the inbox router -- no production middleware.
# ---------------------------------------------------------------------------

_test_app = FastAPI()
_test_app.include_router(inbox_router, prefix="/api")

_DEV_USER = AuthenticatedUser(
    id="user_test",
    email="test@test.com",
    org_id="org_test",
    permissions=["read", "write"],
)

# ---------------------------------------------------------------------------
# Helpers & shared mock data
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
    if "content" in data:
        obj.content = data["content"]
    return obj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(_test_app, base_url="http://localhost")


@pytest.fixture
def err_client():
    """Client that does NOT raise server exceptions -- used for 4xx/5xx tests."""
    return TestClient(
        _test_app, base_url="http://localhost", raise_server_exceptions=False
    )


@pytest.fixture
def mock_svc():
    """Fully-wired mock CommunicationHubService injected via dependency override."""
    svc = MagicMock()

    conv_obj = _make_obj(MOCK_CONV)
    msg_obj = _make_obj(MOCK_MSG)
    contact_obj = _make_obj(MOCK_CONTACT)
    qr_obj = _make_obj(MOCK_QUICK_REPLY)

    used_qr_obj = _make_obj({**MOCK_QUICK_REPLY, "use_count": 6})
    used_qr_obj.content = "Thank you!"

    # Conversations
    svc.get_conversations.return_value = [conv_obj]
    svc.get_conversation.return_value = conv_obj
    svc.create_conversation.return_value = conv_obj
    svc.update_conversation.return_value = conv_obj
    svc.resolve_conversation.return_value = conv_obj
    svc.mark_read.return_value = None

    # Messages
    svc.get_messages.return_value = [msg_obj]
    svc.send_message = AsyncMock(return_value=msg_obj)

    # AI
    svc.generate_reply_suggestion = AsyncMock(return_value="Suggestion")
    svc.summarize_conversation = AsyncMock(return_value="Summary")

    # Contacts
    svc.get_contacts.return_value = [contact_obj]
    svc.get_contact.return_value = contact_obj
    svc.create_contact.return_value = contact_obj
    svc.update_contact.return_value = contact_obj

    # Quick replies
    svc.get_quick_replies.return_value = [qr_obj]
    svc.create_quick_reply.return_value = qr_obj
    svc.use_quick_reply.return_value = used_qr_obj

    # Stats -- mirrors the real service keys from get_inbox_stats()
    svc.get_inbox_stats.return_value = {
        "total_conversations": 10,
        "open": 5,
        "pending": 2,
        "resolved": 3,
        "unread_messages": 7,
        "by_channel": {"email": 4, "sms": 3, "whatsapp": 3},
        "by_priority": {"low": 1, "normal": 5, "high": 3, "urgent": 1},
        "urgent_count": 1,
    }

    # Override both service and auth dependencies on the test app
    _test_app.dependency_overrides[get_comm_hub_service] = lambda: svc
    _test_app.dependency_overrides[get_current_user] = lambda: _DEV_USER
    yield svc
    _test_app.dependency_overrides.pop(get_comm_hub_service, None)
    _test_app.dependency_overrides.pop(get_current_user, None)


# =========================================================================
# 1. Conversation Filters
# =========================================================================


class TestConversationFilters:
    """GET /api/inbox/conversations with query params."""

    def test_filter_by_channel_email(self, client, mock_svc):
        resp = client.get("/api/inbox/conversations?channel=email")
        assert resp.status_code == 200
        kw = mock_svc.get_conversations.call_args
        assert kw.kwargs.get("channel") is not None

    def test_filter_by_status_open(self, client, mock_svc):
        resp = client.get("/api/inbox/conversations?status=open")
        assert resp.status_code == 200
        kw = mock_svc.get_conversations.call_args
        assert kw.kwargs.get("status") is not None

    def test_filter_by_priority_high(self, client, mock_svc):
        resp = client.get("/api/inbox/conversations?priority=high")
        assert resp.status_code == 200
        kw = mock_svc.get_conversations.call_args
        assert kw.kwargs.get("priority") is not None

    def test_filter_by_search_keyword(self, client, mock_svc):
        resp = client.get("/api/inbox/conversations?search=pricing")
        assert resp.status_code == 200
        kw = mock_svc.get_conversations.call_args
        assert kw.kwargs.get("search") == "pricing"

    def test_filter_by_limit(self, client, mock_svc):
        resp = client.get("/api/inbox/conversations?limit=5")
        assert resp.status_code == 200
        kw = mock_svc.get_conversations.call_args
        assert kw.kwargs.get("limit") == 5


# =========================================================================
# 2. Search Edge Cases
# =========================================================================


class TestSearchEdgeCases:
    """Empty results and special-character search queries."""

    def test_empty_search_results(self, client, mock_svc):
        mock_svc.get_conversations.return_value = []
        resp = client.get("/api/inbox/conversations?search=zzzznotfound")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["conversations"] == []

    def test_special_chars_in_search(self, client, mock_svc):
        mock_svc.get_conversations.return_value = []
        resp = client.get("/api/inbox/conversations?search=%40%23%24")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# =========================================================================
# 3. Create Conversation Validation
# =========================================================================


class TestCreateConversationValidation:
    """POST /api/inbox/conversations -- missing/invalid fields."""

    def test_missing_contact_id_returns_422(self, err_client, mock_svc):
        resp = err_client.post(
            "/api/inbox/conversations",
            json={"channel": "email", "subject": "Hi"},
        )
        assert resp.status_code == 422

    def test_missing_channel_returns_422(self, err_client, mock_svc):
        resp = err_client.post(
            "/api/inbox/conversations",
            json={"contact_id": "c1", "subject": "Hi"},
        )
        assert resp.status_code == 422

    def test_invalid_channel_value_returns_error(self, err_client, mock_svc):
        """Channel('invalid_xyz') raises ValueError -> 500 from route."""
        resp = err_client.post(
            "/api/inbox/conversations",
            json={"contact_id": "c1", "channel": "invalid_xyz", "subject": "Hi"},
        )
        assert resp.status_code in (422, 500)


# =========================================================================
# 4. Update Conversation -- Partial Fields
# =========================================================================


class TestUpdateConversationPartial:
    """PATCH /api/inbox/conversations/{id} with various partial payloads."""

    def test_patch_status_only(self, client, mock_svc):
        resp = client.patch(
            "/api/inbox/conversations/conv1",
            json={"status": "resolved"},
        )
        assert resp.status_code == 200
        updates = mock_svc.update_conversation.call_args[0][1]
        assert "status" in updates
        assert "priority" not in updates

    def test_patch_priority_only(self, client, mock_svc):
        resp = client.patch(
            "/api/inbox/conversations/conv1",
            json={"priority": "urgent"},
        )
        assert resp.status_code == 200
        updates = mock_svc.update_conversation.call_args[0][1]
        assert "priority" in updates
        assert "status" not in updates

    def test_patch_tags_only(self, client, mock_svc):
        resp = client.patch(
            "/api/inbox/conversations/conv1",
            json={"tags": ["vip", "support"]},
        )
        assert resp.status_code == 200
        updates = mock_svc.update_conversation.call_args[0][1]
        assert updates["tags"] == ["vip", "support"]

    def test_patch_mixed_fields(self, client, mock_svc):
        resp = client.patch(
            "/api/inbox/conversations/conv1",
            json={"status": "pending", "priority": "low", "assigned_to": "agent_7"},
        )
        assert resp.status_code == 200
        updates = mock_svc.update_conversation.call_args[0][1]
        assert "status" in updates
        assert "priority" in updates
        assert "assigned_to" in updates

    def test_patch_empty_body_sends_no_updates(self, client, mock_svc):
        resp = client.patch(
            "/api/inbox/conversations/conv1",
            json={},
        )
        assert resp.status_code == 200
        updates = mock_svc.update_conversation.call_args[0][1]
        assert updates == {}


# =========================================================================
# 5. Message Sending Edge Cases
# =========================================================================


class TestMessageSendingEdgeCases:
    """POST /api/inbox/conversations/{id}/messages -- edge cases."""

    def test_send_empty_content(self, client, mock_svc):
        resp = client.post(
            "/api/inbox/conversations/conv1/messages",
            json={"content": ""},
        )
        assert resp.status_code == 200
        mock_svc.send_message.assert_awaited_once()

    def test_send_large_content(self, client, mock_svc):
        large = "A" * 10_000
        resp = client.post(
            "/api/inbox/conversations/conv1/messages",
            json={"content": large},
        )
        assert resp.status_code == 200
        kw = mock_svc.send_message.call_args
        assert kw.kwargs.get("content") == large

    def test_send_with_attachments(self, client, mock_svc):
        attachments = [
            {"name": "doc.pdf", "size": 1024, "type": "application/pdf"},
            {"name": "img.png", "size": 2048, "type": "image/png"},
        ]
        resp = client.post(
            "/api/inbox/conversations/conv1/messages",
            json={"content": "See attached", "attachments": attachments},
        )
        assert resp.status_code == 200
        kw = mock_svc.send_message.call_args
        assert len(kw.kwargs.get("attachments")) == 2

    def test_send_missing_content_returns_422(self, err_client, mock_svc):
        resp = err_client.post(
            "/api/inbox/conversations/conv1/messages",
            json={},
        )
        assert resp.status_code == 422


# =========================================================================
# 6. Contact Validation
# =========================================================================


class TestContactValidation:
    """POST /api/inbox/contacts -- validation edge cases."""

    def test_missing_name_returns_422(self, err_client, mock_svc):
        resp = err_client.post(
            "/api/inbox/contacts",
            json={"email": "bob@example.com"},
        )
        assert resp.status_code == 422

    def test_create_with_all_optional_fields(self, client, mock_svc):
        resp = client.post(
            "/api/inbox/contacts",
            json={
                "name": "Bob",
                "email": "bob@example.com",
                "phone": "+1555000111",
                "company": "Acme",
                "title": "CTO",
                "tags": ["vip"],
                "notes": "Important client",
            },
        )
        assert resp.status_code in (200, 201)
        mock_svc.create_contact.assert_called_once()

    def test_duplicate_email_handled_by_service(self, client, mock_svc):
        """Route does not enforce email uniqueness; the service layer decides."""
        resp = client.post(
            "/api/inbox/contacts",
            json={"name": "Dup", "email": "alice@test.com"},
        )
        assert resp.status_code in (200, 201)

    def test_contact_search_passed_to_service(self, client, mock_svc):
        client.get("/api/inbox/contacts?search=Alice")
        kw = mock_svc.get_contacts.call_args
        # search is passed as a keyword argument
        assert kw.kwargs.get("search") == "Alice"


# =========================================================================
# 7. Quick Reply Channel Filter
# =========================================================================


class TestQuickReplyChannelFilter:
    """GET /api/inbox/quick-replies?channel=email."""

    def test_channel_filter_passed_to_service(self, client, mock_svc):
        resp = client.get("/api/inbox/quick-replies?channel=email")
        assert resp.status_code == 200
        kw = mock_svc.get_quick_replies.call_args
        assert kw.kwargs.get("channel") is not None

    def test_no_channel_filter_passes_none(self, client, mock_svc):
        resp = client.get("/api/inbox/quick-replies")
        assert resp.status_code == 200
        kw = mock_svc.get_quick_replies.call_args
        assert kw.kwargs.get("channel") is None


# =========================================================================
# 8. Stats Structure
# =========================================================================


class TestStatsStructure:
    """GET /api/inbox/stats -- verify all expected metric keys."""

    def test_stats_contains_all_expected_keys(self, client, mock_svc):
        resp = client.get("/api/inbox/stats")
        assert resp.status_code == 200
        data = resp.json()
        expected_keys = {
            "total_conversations",
            "open",
            "pending",
            "resolved",
            "unread_messages",
            "by_channel",
            "by_priority",
            "urgent_count",
        }
        assert expected_keys.issubset(set(data.keys()))

    def test_stats_by_priority_has_all_levels(self, client, mock_svc):
        resp = client.get("/api/inbox/stats")
        by_priority = resp.json()["by_priority"]
        for level in ("low", "normal", "high", "urgent"):
            assert level in by_priority


# =========================================================================
# 9. Channels Endpoint
# =========================================================================


class TestChannelsEndpoint:
    """GET /api/inbox/channels -- structure and completeness."""

    def test_channels_returns_seven_entries(self, client, mock_svc):
        resp = client.get("/api/inbox/channels")
        assert resp.status_code == 200
        channels = resp.json()["channels"]
        assert len(channels) == 7

    def test_each_channel_has_id_name_icon(self, client, mock_svc):
        resp = client.get("/api/inbox/channels")
        for ch in resp.json()["channels"]:
            assert "id" in ch
            assert "name" in ch
            assert "icon" in ch

    def test_all_expected_channel_ids_present(self, client, mock_svc):
        resp = client.get("/api/inbox/channels")
        ids = {ch["id"] for ch in resp.json()["channels"]}
        expected = {
            "email",
            "sms",
            "whatsapp",
            "live_chat",
            "facebook",
            "instagram",
            "twitter",
        }
        assert ids == expected
