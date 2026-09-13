"""
API tests for team_routes — team collaboration and sharing.

Router:  api/team_routes.py
Service: TeamService (tools.teams), accessed via get_team_service() directly
         (not via Depends) inside each handler — patched with monkeypatch.
Auth:    X-User-ID header required; 401 without it.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient
import sys
import os
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from tools.teams import ProjectPermission
import middleware.auth as _auth_module


@contextmanager
def _disable_dev_mode():
    """Temporarily disable DEV_MODE and remove any get_current_user override."""
    original = _auth_module.DEV_MODE
    _auth_module.DEV_MODE = False
    override = app.dependency_overrides.pop(_auth_module.get_current_user, None)
    try:
        yield
    finally:
        _auth_module.DEV_MODE = original
        if override is not None:
            app.dependency_overrides[_auth_module.get_current_user] = override


# =============================================================================
# Shared mock data
# =============================================================================

MOCK_TEAM = {
    "id": "team1",
    "name": "Test Team",
    "slug": "test-team",
    "owner_id": "user_123",
    "description": "Test",
    "logo_url": None,
    "website": None,
    "max_members": 5,
    "max_projects": 10,
    "member_count": 1,
    "project_count": 0,
    "created_at": "2024-01-01T00:00:00",
}

MOCK_MEMBER = {
    "user_id": "user_123",
    "team_id": "team1",
    "role": "owner",
    "joined_at": "2024-01-01T00:00:00",
    "email": "test@test.com",
    "name": "Test User",
    "avatar_url": None,
}

MOCK_INVITE = {
    "id": "inv1",
    "team_id": "team1",
    "email": "new@test.com",
    "role": "member",
    "status": "pending",
    "created_at": "2024-01-01T00:00:00",
    "expires_at": "2024-01-08T00:00:00",
    "token": "tok123",
}

AUTH_HEADERS = {"X-User-ID": "user_123"}


def _make_obj(data: dict) -> MagicMock:
    """Return a MagicMock whose .to_dict() returns *data*."""
    obj = MagicMock()
    obj.to_dict.return_value = data
    # Expose top-level attributes so code like `member.role` also works
    for k, v in data.items():
        setattr(obj, k, v)
    return obj


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def mock_team_svc(monkeypatch):
    svc = MagicMock()

    team_obj = _make_obj(MOCK_TEAM)
    member_obj = _make_obj(MOCK_MEMBER)
    invite_obj = _make_obj(MOCK_INVITE)
    share_obj = _make_obj(
        {
            "id": "share1",
            "team_id": "team1",
            "project_id": "proj1",
            "permission": "view",
        }
    )
    activity_obj = _make_obj(
        {
            "id": "act1",
            "team_id": "team1",
            "user_id": "user_123",
            "activity_type": "member_joined",
            "created_at": "2024-01-01T00:00:00",
            "project_id": None,
            "target_user_id": None,
            "metadata": {},
            "user_name": "Test User",
            "user_avatar": None,
        }
    )

    svc.create_team = AsyncMock(return_value=team_obj)
    svc.get_user_teams = AsyncMock(return_value=[team_obj])
    svc.get_team = AsyncMock(return_value=team_obj)
    svc.update_team = AsyncMock(return_value=team_obj)
    svc.delete_team = AsyncMock(return_value=True)

    # Membership helpers used inside route handlers
    svc.get_member = AsyncMock(return_value=member_obj)
    svc.get_team_members = AsyncMock(return_value=[member_obj])
    svc.update_member_role = AsyncMock(return_value=member_obj)
    svc.remove_member = AsyncMock(return_value=True)

    # Invitations
    svc.invite_member = AsyncMock(return_value=invite_obj)
    svc.get_pending_invites = AsyncMock(return_value=[invite_obj])
    svc.accept_invite = AsyncMock(return_value=member_obj)
    svc.decline_invite = AsyncMock(return_value=True)

    # Project sharing
    svc.share_project = AsyncMock(return_value=share_obj)
    svc.get_project_shares = AsyncMock(return_value=[share_obj])
    svc.get_user_project_permission = AsyncMock(return_value=ProjectPermission.EDITOR)
    svc.remove_share = AsyncMock(return_value=True)

    # Activity
    svc.get_activity_feed = AsyncMock(return_value=[activity_obj])

    monkeypatch.setattr("api.team_routes.get_team_service", lambda: svc)
    return svc


# =============================================================================
# Team CRUD tests
# =============================================================================


class TestCreateTeam:
    def test_create_team_success(self, client):
        resp = client.post(
            "/api/teams",
            json={"name": "My Team"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "team1"
        assert body["name"] == "Test Team"

    def test_create_team_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.post("/api/teams", json={"name": "My Team"})
        assert resp.status_code == 401

    def test_create_team_with_description(self, client):
        resp = client.post(
            "/api/teams",
            json={"name": "My Team", "description": "A great team"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200

    def test_create_team_missing_name(self, client):
        resp = client.post("/api/teams", json={}, headers=AUTH_HEADERS)
        assert resp.status_code == 422


class TestGetTeams:
    def test_list_user_teams(self, client):
        resp = client.get("/api/teams", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        teams = resp.json()
        assert isinstance(teams, list)
        assert teams[0]["id"] == "team1"

    def test_list_teams_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.get("/api/teams")
        assert resp.status_code == 401

    def test_get_team_by_id(self, client):
        resp = client.get("/api/teams/team1", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["id"] == "team1"

    def test_get_team_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.get("/api/teams/team1")
        assert resp.status_code == 401

    def test_get_team_not_member(self, client, mock_team_svc):
        mock_team_svc.get_member = AsyncMock(return_value=None)
        resp = client.get("/api/teams/team1", headers=AUTH_HEADERS)
        assert resp.status_code == 403


class TestUpdateDeleteTeam:
    def test_update_team_success(self, client):
        resp = client.patch(
            "/api/teams/team1",
            json={"name": "New Name"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "team1"

    def test_update_team_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.patch("/api/teams/team1", json={"name": "X"})
        assert resp.status_code == 401

    def test_delete_team_success(self, client):
        resp = client.delete("/api/teams/team1", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_team_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.delete("/api/teams/team1")
        assert resp.status_code == 401


# =============================================================================
# Member tests
# =============================================================================


class TestMembers:
    def test_list_members(self, client):
        resp = client.get("/api/teams/team1/members", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        members = resp.json()
        assert isinstance(members, list)
        assert members[0]["user_id"] == "user_123"

    def test_list_members_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.get("/api/teams/team1/members")
        assert resp.status_code == 401

    def test_list_members_not_member(self, client, mock_team_svc):
        mock_team_svc.get_member = AsyncMock(return_value=None)
        resp = client.get("/api/teams/team1/members", headers=AUTH_HEADERS)
        assert resp.status_code == 403

    def test_update_member_role(self, client):
        resp = client.patch(
            "/api/teams/team1/members/user_456",
            json={"role": "admin"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "user_123"

    def test_remove_member(self, client):
        resp = client.delete(
            "/api/teams/team1/members/user_456",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"


# =============================================================================
# Invitation tests
# =============================================================================


class TestInvitations:
    def test_invite_member(self, client):
        resp = client.post(
            "/api/teams/team1/invites",
            json={"email": "new@test.com"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "inv1"
        assert body["email"] == "new@test.com"

    def test_invite_member_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.post(
                "/api/teams/team1/invites",
                json={"email": "new@test.com"},
            )
        assert resp.status_code == 401

    def test_invite_member_invalid_email(self, client):
        resp = client.post(
            "/api/teams/team1/invites",
            json={"email": "not-an-email"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 422

    def test_list_invites(self, client):
        resp = client.get("/api/teams/team1/invites", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        invites = resp.json()
        assert isinstance(invites, list)
        assert invites[0]["id"] == "inv1"
        assert invites[0]["email"] == "new@test.com"

    def test_list_invites_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.get("/api/teams/team1/invites")
        assert resp.status_code == 401

    def test_accept_invite(self, client):
        resp = client.post(
            "/api/teams/invites/tok123/accept",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "user_123"

    def test_decline_invite(self, client):
        resp = client.post("/api/teams/invites/tok123/decline")
        assert resp.status_code == 200
        assert resp.json()["status"] == "declined"

    def test_decline_invite_not_found(self, client, mock_team_svc):
        mock_team_svc.decline_invite = AsyncMock(return_value=False)
        resp = client.post("/api/teams/invites/bad-token/decline")
        assert resp.status_code == 404


# =============================================================================
# Project sharing tests
# =============================================================================


class TestProjectSharing:
    def test_share_project(self, client):
        resp = client.post(
            "/api/teams/team1/shares",
            json={"project_id": "proj1", "permission": "viewer"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == "share1"

    def test_share_project_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.post(
                "/api/teams/team1/shares",
                json={"project_id": "proj1", "permission": "view"},
            )
        assert resp.status_code == 401

    def test_get_project_permission(self, client):
        resp = client.get(
            "/api/teams/projects/proj1/permission",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "permission" in body
        assert "can_edit" in body

    def test_get_project_permission_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.get("/api/teams/projects/proj1/permission")
        assert resp.status_code == 401

    def test_remove_share(self, client):
        resp = client.delete(
            "/api/teams/shares/share1",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"

    def test_remove_share_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.delete("/api/teams/shares/share1")
        assert resp.status_code == 401


# =============================================================================
# Activity feed tests
# =============================================================================


class TestActivityFeed:
    def test_get_activity(self, client):
        resp = client.get("/api/teams/team1/activity", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        activities = resp.json()
        assert isinstance(activities, list)

    def test_get_activity_no_auth(self, client):
        with _disable_dev_mode():
            resp = client.get("/api/teams/team1/activity")
        assert resp.status_code == 401

    def test_get_activity_not_member(self, client, mock_team_svc):
        mock_team_svc.get_member = AsyncMock(return_value=None)
        resp = client.get("/api/teams/team1/activity", headers=AUTH_HEADERS)
        assert resp.status_code == 403

    def test_get_activity_with_limit(self, client):
        resp = client.get(
            "/api/teams/team1/activity?limit=10",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200

    def test_get_activity_with_offset(self, client):
        resp = client.get(
            "/api/teams/team1/activity?limit=5&offset=2",
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 200


# =============================================================================
# Parametrized: endpoints that require auth header
# =============================================================================


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/teams", None),
        ("POST", "/api/teams", {"name": "X"}),
        ("GET", "/api/teams/team1", None),
        ("PATCH", "/api/teams/team1", {"name": "Y"}),
        ("DELETE", "/api/teams/team1", None),
        ("GET", "/api/teams/team1/members", None),
        ("GET", "/api/teams/team1/invites", None),
        ("POST", "/api/teams/team1/invites", {"email": "x@x.com"}),
        ("POST", "/api/teams/invites/tok/accept", None),
        ("POST", "/api/teams/team1/shares", {"project_id": "p1", "permission": "view"}),
        ("DELETE", "/api/teams/shares/s1", None),
        ("GET", "/api/teams/team1/activity", None),
    ],
)
def test_requires_auth_header(client, method, path, body):
    """Every protected endpoint must return 401 when X-User-ID is absent."""
    fn = getattr(client, method.lower())
    kwargs = {}
    if body is not None:
        kwargs["json"] = body
    with _disable_dev_mode():
        resp = fn(path, **kwargs)
    assert (
        resp.status_code == 401
    ), f"{method} {path} should be 401 without auth, got {resp.status_code}"
