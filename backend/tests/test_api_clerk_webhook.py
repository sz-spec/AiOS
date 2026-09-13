"""
Tests for Clerk Webhook API Routes
====================================

Covers: POST /api/webhooks/clerk, GET /api/webhooks/clerk/health

Tests webhook signature verification, event routing (user.created,
user.updated, user.deleted, session.created), error handling, and
the health endpoint.
"""

import hashlib
import hmac
import json
import pytest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """TestClient pointed at the FastAPI app."""
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def mock_convex():
    """Mocks the core-repository accessors the webhook module uses.

    The module was refactored (W3.2d) from a direct ``get_convex_client``
    to the repository layer (``get_async_user_sync_repository`` /
    ``…_audit_log_repository`` / ``…_webhook_seen_repository``). This fixture
    patches those accessors to return AsyncMock repos and yields a namespace
    (``.user`` / ``.audit`` / ``.seen``) for assertions. (Name kept as
    ``mock_convex`` so the 21 existing test signatures are unchanged.)
    """
    user = AsyncMock()
    user.sync_from_clerk = AsyncMock(return_value="user_id")
    user.soft_delete = AsyncMock(return_value=True)
    user.record_sign_in = AsyncMock(return_value=None)
    user.get_by_clerk_id = AsyncMock(return_value=None)
    audit = AsyncMock()
    audit.add_entry = AsyncMock(return_value="audit_id")
    seen = AsyncMock()
    seen.was_seen = AsyncMock(return_value=False)
    seen.record = AsyncMock(return_value=None)
    with patch(
        "api.clerk_webhook.get_async_user_sync_repository", return_value=user
    ), patch(
        "api.clerk_webhook.get_async_audit_log_repository", return_value=audit
    ), patch(
        "api.clerk_webhook.get_async_webhook_seen_repository", return_value=seen
    ):
        yield SimpleNamespace(user=user, audit=audit, seen=seen)


def _user_event_data(
    user_id="user_abc123",
    email="alice@example.com",
    email_id="email_1",
    primary_email_id="email_1",
    first_name="Alice",
    last_name="Smith",
):
    """Build a realistic Clerk user data dict."""
    return {
        "id": user_id,
        "email_addresses": [
            {"id": email_id, "email_address": email},
        ],
        "primary_email_address_id": primary_email_id,
        "first_name": first_name,
        "last_name": last_name,
        "image_url": "https://img.clerk.com/avatar.png",
        "created_at": 1700000000,
        "updated_at": 1700000001,
        "public_metadata": {"role": "user"},
        "private_metadata": {},
    }


def _webhook_payload(event_type, data, obj="event"):
    """Build a ClerkWebhookEvent JSON payload."""
    return json.dumps({"type": event_type, "data": data, "object": obj})


def _svix_headers(payload_str, secret="test_secret"):
    """Compute valid svix headers for the given payload and secret.

    Matches the production verify_clerk_webhook() logic:
    - Strip ``whsec_`` prefix if present
    - base64-decode the secret bytes
    - HMAC-SHA256(secret_bytes, signed_payload)
    - base64-encode the digest
    """
    import base64

    svix_id = "msg_test123"
    svix_timestamp = "1700000000"
    signed_payload = f"{svix_id}.{svix_timestamp}.{payload_str}"

    secret_str = secret
    if secret_str.startswith("whsec_"):
        secret_str = secret_str[6:]
    # Secrets must be valid base64; decode to raw bytes
    secret_bytes = base64.b64decode(secret_str)
    sig = base64.b64encode(
        hmac.new(secret_bytes, signed_payload.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "svix-id": svix_id,
        "svix-timestamp": svix_timestamp,
        "svix-signature": f"v1,{sig}",
    }


# ---------------------------------------------------------------------------
# GET /api/webhooks/clerk/health
# ---------------------------------------------------------------------------


class TestWebhookHealth:
    def test_health_returns_200(self, client):
        response = client.get("/api/webhooks/clerk/health")
        assert response.status_code == 200

    def test_health_contains_status(self, client):
        data = client.get("/api/webhooks/clerk/health").json()
        assert data["status"] == "healthy"

    def test_health_contains_configured_flag(self, client):
        data = client.get("/api/webhooks/clerk/health").json()
        assert "webhook_secret_configured" in data

    def test_health_contains_timestamp(self, client):
        data = client.get("/api/webhooks/clerk/health").json()
        assert "timestamp" in data

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", "some_secret")
    def test_health_configured_true_when_secret_set(self, client):
        data = client.get("/api/webhooks/clerk/health").json()
        assert data["webhook_secret_configured"] is True

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_health_configured_false_when_no_secret(self, client):
        data = client.get("/api/webhooks/clerk/health").json()
        assert data["webhook_secret_configured"] is False


# ---------------------------------------------------------------------------
# Webhook Signature Verification
# ---------------------------------------------------------------------------


class TestWebhookVerification:
    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_no_secret_skips_verification(self, client, mock_convex):
        """When CLERK_WEBHOOK_SECRET is None (dev mode), any request passes."""
        payload = _webhook_payload("user.created", _user_event_data())
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 200

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_no_secret_no_svix_headers_still_passes(self, client, mock_convex):
        """Without svix headers and no secret configured, verification is skipped."""
        payload = _webhook_payload("user.created", _user_event_data())
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 200

    @patch(
        "api.clerk_webhook.CLERK_WEBHOOK_SECRET",
        "dGVzdF9zZWNyZXRfa2V5XzMyYnl0ZXNfcGFkISEhIQ==",
    )
    def test_valid_signature_passes(self, client, mock_convex):
        """A correctly signed request should return 200."""
        b64_secret = "dGVzdF9zZWNyZXRfa2V5XzMyYnl0ZXNfcGFkISEhIQ=="
        payload = _webhook_payload("user.created", _user_event_data())
        headers = _svix_headers(payload, b64_secret)
        headers["content-type"] = "application/json"
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk", content=payload, headers=headers
            )
        assert response.status_code == 200

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", "test_secret")
    def test_invalid_signature_returns_400(self, client):
        """A request with wrong signature should be rejected."""
        payload = _webhook_payload("user.created", _user_event_data())
        headers = {
            "svix-id": "msg_test123",
            "svix-timestamp": "1700000000",
            "svix-signature": "v1,invalid_signature_hex",
            "content-type": "application/json",
        }
        response = client.post("/api/webhooks/clerk", content=payload, headers=headers)
        assert response.status_code == 400

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", "test_secret")
    def test_missing_svix_headers_skips_verification(self, client, mock_convex):
        """Without svix headers, verification block is skipped entirely."""
        payload = _webhook_payload("user.created", _user_event_data())
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        # No svix headers → the `if svix_id and ...` block is skipped
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# user.created
# ---------------------------------------------------------------------------


class TestUserCreated:
    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_user_created_returns_success(self, client, mock_convex):
        payload = _webhook_payload("user.created", _user_event_data())
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 200
        data = response.json()
        # Handler returns {"status": "created", ...} which overwrites
        # the outer "success" via dict unpacking
        assert data["status"] == "created"
        assert data["event_type"] == "user.created"
        assert data["user_id"] == "user_abc123"

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_user_created_calls_sync_mutation(self, client, mock_convex):
        user_data = _user_event_data()
        payload = _webhook_payload("user.created", user_data)
        with nullcontext():
            client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        mock_convex.user.sync_from_clerk.assert_called_once()
        kw = mock_convex.user.sync_from_clerk.call_args.kwargs
        assert kw["clerk_id"] == "user_abc123"
        assert kw["email"] == "alice@example.com"
        assert kw["full_name"] == "Alice Smith"

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_user_created_primary_email_extraction(self, client, mock_convex):
        """Primary email is selected by matching primary_email_address_id."""
        data = _user_event_data()
        data["email_addresses"] = [
            {"id": "email_other", "email_address": "other@example.com"},
            {"id": "email_1", "email_address": "alice@example.com"},
        ]
        data["primary_email_address_id"] = "email_1"
        payload = _webhook_payload("user.created", data)
        with nullcontext():
            client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        kw = mock_convex.user.sync_from_clerk.call_args.kwargs
        assert kw["email"] == "alice@example.com"

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_user_created_missing_primary_falls_back_to_first(
        self, client, mock_convex
    ):
        """When primary_email_address_id doesn't match, fallback to first email."""
        data = _user_event_data()
        data["primary_email_address_id"] = "nonexistent_id"
        data["email_addresses"] = [
            {"id": "email_x", "email_address": "fallback@example.com"},
        ]
        payload = _webhook_payload("user.created", data)
        with nullcontext():
            resp = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        data = resp.json()
        assert data["status"] == "created"
        kw = mock_convex.user.sync_from_clerk.call_args.kwargs
        assert kw["email"] == "fallback@example.com"

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_user_created_no_emails_empty_string(self, client, mock_convex):
        """When there are no email addresses at all, email should be empty string."""
        data = _user_event_data()
        data["email_addresses"] = []
        data["primary_email_address_id"] = None
        payload = _webhook_payload("user.created", data)
        with nullcontext():
            client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        kw = mock_convex.user.sync_from_clerk.call_args.kwargs
        assert kw["email"] == ""


# ---------------------------------------------------------------------------
# user.updated
# ---------------------------------------------------------------------------


class TestUserUpdated:
    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_user_updated_returns_success(self, client, mock_convex):
        payload = _webhook_payload("user.updated", _user_event_data())
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"
        assert data["event_type"] == "user.updated"
        assert data["user_id"] == "user_abc123"

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_user_updated_calls_sync_mutation(self, client, mock_convex):
        user_data = _user_event_data(first_name="Bob", last_name="Jones")
        payload = _webhook_payload("user.updated", user_data)
        with nullcontext():
            client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        mock_convex.user.sync_from_clerk.assert_called_once()
        kw = mock_convex.user.sync_from_clerk.call_args.kwargs
        assert kw["full_name"] == "Bob Jones"


# ---------------------------------------------------------------------------
# user.deleted
# ---------------------------------------------------------------------------


class TestUserDeleted:
    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_user_deleted_returns_success(self, client, mock_convex):
        payload = _webhook_payload("user.deleted", {"id": "user_del_123"})
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["event_type"] == "user.deleted"
        assert data["user_id"] == "user_del_123"

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_user_deleted_calls_soft_delete_mutation(self, client, mock_convex):
        payload = _webhook_payload("user.deleted", {"id": "user_del_456"})
        with nullcontext():
            client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        mock_convex.user.soft_delete.assert_called_once()
        assert (
            mock_convex.user.soft_delete.call_args.kwargs["clerk_id"] == "user_del_456"
        )

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_user_deleted_missing_id_returns_skipped(self, client, mock_convex):
        """When data has no 'id' field, handler returns skipped status."""
        payload = _webhook_payload("user.deleted", {})
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 200
        data = response.json()
        # The inner handler returns {"status": "skipped"} which overwrites
        # the outer "success" via dict unpacking
        assert data["status"] == "skipped"
        assert data["reason"] == "no user_id"


# ---------------------------------------------------------------------------
# session.created
# ---------------------------------------------------------------------------


class TestSessionCreated:
    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_session_created_returns_success(self, client, mock_convex):
        session_data = {
            "id": "sess_abc",
            "user_id": "user_sess_123",
            "client_id": "client_xyz",
            "created_at": 1700000000,
        }
        payload = _webhook_payload("session.created", session_data)
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "tracked"
        assert data["event_type"] == "session.created"
        assert data["user_id"] == "user_sess_123"

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_session_created_calls_record_sign_in(self, client, mock_convex):
        session_data = {
            "id": "sess_abc",
            "user_id": "user_sess_123",
            "client_id": "client_xyz",
            "created_at": 1700000000,
        }
        payload = _webhook_payload("session.created", session_data)
        with nullcontext():
            client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        # Sign-in is recorded via the user-sync repository.
        mock_convex.user.record_sign_in.assert_called_once()
        assert (
            mock_convex.user.record_sign_in.call_args.kwargs["clerk_id"]
            == "user_sess_123"
        )

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_session_created_with_existing_user_adds_audit(self, client, mock_convex):
        """When query returns a user, an audit entry mutation is added."""
        mock_convex.user.get_by_clerk_id.return_value = {
            "_id": "convex_user_id",
            "name": "Alice",
        }
        session_data = {
            "id": "sess_audit",
            "user_id": "user_audit_123",
            "client_id": "client_xyz",
            "created_at": 1700000000,
        }
        payload = _webhook_payload("session.created", session_data)
        with nullcontext():
            client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        # Should have looked up the user via the repository.
        mock_convex.user.get_by_clerk_id.assert_called_once_with(
            clerk_id="user_audit_123"
        )
        # Should have written one audit entry for the found user.
        mock_convex.audit.add_entry.assert_called_once()
        audit_kw = mock_convex.audit.add_entry.call_args.kwargs
        assert audit_kw["user_id"] == "convex_user_id"
        assert audit_kw["action"] == "login"
        assert audit_kw["resource_type"] == "session"
        assert audit_kw["resource_id"] == "sess_audit"

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_session_created_no_user_found_skips_audit(self, client, mock_convex):
        """When query returns None, no audit entry is created."""
        mock_convex.user.get_by_clerk_id.return_value = None
        session_data = {
            "id": "sess_no_user",
            "user_id": "user_unknown",
            "client_id": "client_xyz",
            "created_at": 1700000000,
        }
        payload = _webhook_payload("session.created", session_data)
        with nullcontext():
            client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        # record_sign_in should be called, but no audit entry (user not found)
        mock_convex.user.record_sign_in.assert_called_once()
        mock_convex.audit.add_entry.assert_not_called()

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_session_created_missing_user_id_returns_skipped(self, client, mock_convex):
        """When session data has no user_id, handler returns skipped."""
        session_data = {"id": "sess_no_uid", "client_id": "client_xyz"}
        payload = _webhook_payload("session.created", session_data)
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        assert data["reason"] == "no user_id"


# ---------------------------------------------------------------------------
# Webhook Endpoint — General Behavior
# ---------------------------------------------------------------------------


class TestWebhookEndpoint:
    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_unknown_event_type_returns_ignored(self, client):
        payload = _webhook_payload("organization.created", {"id": "org_123"})
        response = client.post(
            "/api/webhooks/clerk",
            content=payload,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"
        assert data["event_type"] == "organization.created"

    def test_invalid_json_payload_returns_400(self, client):
        response = client.post(
            "/api/webhooks/clerk",
            content=b"this is not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_missing_type_field_returns_400(self, client):
        """A JSON body without the required 'type' field should fail."""
        payload = json.dumps({"data": {}, "object": "event"})
        response = client.post(
            "/api/webhooks/clerk",
            content=payload,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_missing_data_field_returns_400(self, client):
        """A JSON body without the required 'data' field should fail."""
        payload = json.dumps({"type": "user.created", "object": "event"})
        response = client.post(
            "/api/webhooks/clerk",
            content=payload,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_convex_mutation_error_returns_error_status(self, client, mock_convex):
        """When Convex raises, the endpoint returns status=error (not HTTP 500)."""
        mock_convex.user.soft_delete.side_effect = RuntimeError("Convex unavailable")
        payload = _webhook_payload("user.deleted", {"id": "user_err_123"})
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "Convex unavailable" in data["error"]

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_convex_query_error_in_session_returns_error_status(
        self, client, mock_convex
    ):
        """When Convex query raises during session.created, endpoint returns error."""
        mock_convex.user.get_by_clerk_id.side_effect = RuntimeError("Query failed")
        session_data = {
            "id": "sess_err",
            "user_id": "user_err",
            "client_id": "client_err",
        }
        payload = _webhook_payload("session.created", session_data)
        with nullcontext():
            response = client.post(
                "/api/webhooks/clerk",
                content=payload,
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"

    @patch("api.clerk_webhook.CLERK_WEBHOOK_SECRET", None)
    def test_empty_body_returns_400(self, client):
        response = client.post(
            "/api/webhooks/clerk",
            content=b"",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
