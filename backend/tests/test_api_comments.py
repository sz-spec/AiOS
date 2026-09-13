"""
Tests for Comments API routes (/api/comments/*).

Covers all 11 REST endpoints + WebSocket in api/comment_routes.py
using a mocked CommentsService.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
from api.comment_routes import router as _comments_router, get_comments_service
from api.comments import (
    Comment,
    CommentType,
    CommentStatus,
)

# Mount the comments router (not mounted in main.py for production yet)
_COMMENTS_PREFIX = "/api/comments"
_mounted_comments = False
for _route in app.routes:
    if hasattr(_route, "path") and _route.path.startswith(_COMMENTS_PREFIX):
        _mounted_comments = True
        break
if not _mounted_comments:
    app.include_router(_comments_router, prefix="/api", tags=["Comments-Test"])


# =============================================================================
# Fixtures
# =============================================================================


def _make_comment(
    comment_id="comment_test123",
    project_id="proj1",
    status=CommentStatus.OPEN,
    suggestion_code=None,
):
    """Build a real Comment dataclass instance."""
    return Comment(
        id=comment_id,
        project_id=project_id,
        user_id="dev_seed_user",
        user_name="Demo User",
        user_avatar=None,
        content="Test comment content",
        location=None,
        comment_type=CommentType.COMMENT,
        status=status,
        parent_id=None,
        mentioned_users=[],
        reactions=[],
        suggestion_code=suggestion_code,
        created_at=datetime(2024, 1, 1),
        updated_at=datetime(2024, 1, 1),
    )


@pytest.fixture
def mock_comment():
    return _make_comment()


@pytest.fixture(autouse=True)
def mock_comments_svc(mock_comment):
    """Inject a mock CommentsService for every test in this module."""
    svc = MagicMock()

    svc.create_comment = AsyncMock(return_value=mock_comment)
    svc.get_comment = AsyncMock(return_value=mock_comment)
    svc.get_comments = AsyncMock(return_value=[mock_comment])
    svc.get_comments_by_line = AsyncMock(return_value={1: [mock_comment]})
    svc.get_unresolved_count = AsyncMock(return_value=3)
    svc.get_thread = AsyncMock(return_value=[mock_comment])
    svc.update_comment = AsyncMock(return_value=mock_comment)
    svc.delete_comment = AsyncMock(return_value=True)
    svc.resolve_comment = AsyncMock(return_value=mock_comment)
    svc.add_reaction = AsyncMock(return_value=True)

    app.dependency_overrides[get_comments_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_comments_service, None)


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


# =============================================================================
# Create comment
# =============================================================================


def test_create_comment_success(client, mock_comments_svc):
    resp = client.post(
        "/api/comments/projects/proj1",
        json={
            "content": "This is a test comment",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    mock_comments_svc.create_comment.assert_awaited_once()


def test_create_comment_missing_content(client):
    resp = client.post("/api/comments/projects/proj1", json={})
    assert resp.status_code == 422


def test_create_comment_with_location(client, mock_comments_svc):
    resp = client.post(
        "/api/comments/projects/proj1",
        json={
            "content": "Check this line",
            "file_path": "src/main.py",
            "line_start": 42,
            "line_end": 45,
        },
    )
    assert resp.status_code == 200


# =============================================================================
# List comments
# =============================================================================


def test_get_comments(client, mock_comments_svc):
    resp = client.get("/api/comments/projects/proj1")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    mock_comments_svc.get_comments.assert_awaited()


def test_get_comments_with_file_path(client, mock_comments_svc):
    resp = client.get("/api/comments/projects/proj1?file_path=src/main.py")
    assert resp.status_code == 200
    # The route calls get_comments twice (once for the list, once per comment for reply count).
    # Check the FIRST call which carries the user's filter params.
    first_call_kwargs = mock_comments_svc.get_comments.call_args_list[0].kwargs
    assert first_call_kwargs.get("file_path") == "src/main.py"


def test_get_comments_with_status_filter(client, mock_comments_svc):
    resp = client.get("/api/comments/projects/proj1?status=open")
    assert resp.status_code == 200


# =============================================================================
# File & unresolved views
# =============================================================================


def test_get_file_comments(client, mock_comments_svc):
    resp = client.get("/api/comments/projects/proj1/file?file_path=src/main.py")
    assert resp.status_code == 200
    data = resp.json()
    # Returns dict keyed by line number (serialized as string keys)
    assert isinstance(data, dict)
    mock_comments_svc.get_comments_by_line.assert_awaited_once_with(
        "proj1", "src/main.py"
    )


def test_get_file_comments_missing_file_path(client):
    resp = client.get("/api/comments/projects/proj1/file")
    assert resp.status_code == 422


def test_get_unresolved_count(client, mock_comments_svc):
    resp = client.get("/api/comments/projects/proj1/unresolved")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert data["count"] == 3
    mock_comments_svc.get_unresolved_count.assert_awaited_once()


# =============================================================================
# Single comment operations
# =============================================================================


def test_get_comment_found(client, mock_comments_svc):
    resp = client.get("/api/comments/comment_test123")
    assert resp.status_code == 200
    assert resp.json()["id"] == "comment_test123"


def test_get_comment_not_found(client, mock_comments_svc):
    mock_comments_svc.get_comment.return_value = None
    resp = client.get("/api/comments/nonexistent")
    assert resp.status_code == 404


def test_get_thread(client, mock_comments_svc):
    resp = client.get("/api/comments/comment_test123/thread")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    mock_comments_svc.get_thread.assert_awaited_once_with("comment_test123")


def test_update_comment_success(client, mock_comments_svc):
    resp = client.patch(
        "/api/comments/comment_test123", json={"content": "Updated content"}
    )
    assert resp.status_code == 200
    mock_comments_svc.update_comment.assert_awaited_once()


def test_update_comment_permission_denied(client, mock_comments_svc):
    mock_comments_svc.update_comment.side_effect = PermissionError("Not the author")
    resp = client.patch("/api/comments/comment_test123", json={"content": "Hacked"})
    assert resp.status_code == 403


def test_delete_comment_success(client, mock_comments_svc):
    resp = client.delete("/api/comments/comment_test123")
    assert resp.status_code == 200
    mock_comments_svc.delete_comment.assert_awaited_once()


def test_delete_comment_permission_denied(client, mock_comments_svc):
    mock_comments_svc.get_comment.return_value = _make_comment()
    mock_comments_svc.delete_comment.side_effect = PermissionError("Not the author")
    resp = client.delete("/api/comments/comment_test123")
    assert resp.status_code == 403


# =============================================================================
# Resolve & react
# =============================================================================


def test_resolve_comment(client, mock_comments_svc):
    resolved_comment = _make_comment(status=CommentStatus.RESOLVED)
    mock_comments_svc.resolve_comment.return_value = resolved_comment
    resp = client.post("/api/comments/comment_test123/resolve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"
    mock_comments_svc.resolve_comment.assert_awaited_once()


def test_resolve_comment_with_suggestion(client, mock_comments_svc):
    """
    When apply_suggestion=true the route includes apply_suggestion in the response
    (now that CommentResponse has the field defined).
    """
    from api.comments import CommentLocation

    comment_with_suggestion = _make_comment(
        status=CommentStatus.RESOLVED,
        suggestion_code="return True",
    )
    comment_with_suggestion.location = CommentLocation(
        file_path="src/main.py",
        line_start=10,
        line_end=10,
    )
    mock_comments_svc.resolve_comment.return_value = comment_with_suggestion

    resp = client.post("/api/comments/comment_test123/resolve?apply_suggestion=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"
    assert data["suggestion_code"] == "return True"
    assert "apply_suggestion" in data
    mock_comments_svc.resolve_comment.assert_awaited_once()


def test_toggle_reaction(client, mock_comments_svc):
    resp = client.post("/api/comments/comment_test123/reactions", json={"emoji": "👍"})
    assert resp.status_code == 200
    data = resp.json()
    assert "added" in data
    assert data["added"] is True
    mock_comments_svc.add_reaction.assert_awaited_once()


# =============================================================================
# WebSocket
# =============================================================================


def test_websocket_connect_and_subscribe():
    """WebSocket connects and responds to subscribe message."""
    with TestClient(app, base_url="http://localhost") as c:
        with c.websocket_connect("/api/comments/ws/proj1") as ws:
            ws.send_json({"type": "subscribe"})
            resp = ws.receive_json()
            assert resp["type"] == "subscribed"
            assert resp["project_id"] == "proj1"


def test_websocket_ping_pong():
    """WebSocket responds to ping with pong."""
    with TestClient(app, base_url="http://localhost") as c:
        with c.websocket_connect("/api/comments/ws/proj1") as ws:
            ws.send_json({"type": "ping"})
            resp = ws.receive_json()
            assert resp["type"] == "pong"


def test_websocket_disconnect_no_crash():
    """Disconnecting cleans up the connection without raising."""
    with TestClient(app, base_url="http://localhost") as c:
        with c.websocket_connect("/api/comments/ws/proj2") as ws:
            ws.send_json({"type": "ping"})
            ws.receive_json()
        # After context exits, connection is cleaned up — no exception raised


# =============================================================================
# Coverage: broadcast_comment cleanup
# =============================================================================


def test_broadcast_cleanup_empty_project():
    """After the last client disconnects, the project key is removed from _ws_connections."""
    from api.comment_routes import _ws_connections

    with TestClient(app, base_url="http://localhost") as c:
        with c.websocket_connect("/api/comments/ws/proj_cleanup") as ws:
            ws.send_json({"type": "subscribe"})
            ws.receive_json()
        # Connection closed — the project entry should be gone
        assert "proj_cleanup" not in _ws_connections
