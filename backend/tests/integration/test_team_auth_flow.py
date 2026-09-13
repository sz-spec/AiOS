"""
Team Authorization Flow Tests
================================
Tests TeamRole hierarchy, require_project_role() dependency,
_resolve_project_role(), and collaborator modification rules.

Run:
    pytest tests/integration/test_team_auth_flow.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(
    user_id="user_test",
    email="test@example.com",
    org_id=None,
    convex_user_id=None,
    permissions=None,
):
    """Create an AuthenticatedUser for testing."""
    from middleware.auth import AuthenticatedUser

    return AuthenticatedUser(
        id=user_id,
        email=email,
        org_id=org_id,
        convex_user_id=convex_user_id,
        permissions=permissions or [],
    )


def _build_role_app(min_role):
    """Build a minimal FastAPI app with a single endpoint protected by
    require_project_role(min_role)."""
    from middleware.team_auth import require_project_role, TeamRoleCheck

    app = FastAPI()

    @app.get("/projects/{project_id}/data")
    async def get_data(
        project_id: str,
        role_check: TeamRoleCheck = Depends(require_project_role(min_role)),
    ):
        return {
            "project_id": role_check.project_id,
            "role": role_check.role.name,
            "is_owner": role_check.is_owner,
        }

    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTeamAuthFlow:
    """Integration tests for team role-based authorization."""

    # 1. TeamRole ordering --------------------------------------------------
    def test_team_role_ordering(self):
        """VIEWER < EDITOR < OWNER."""
        from middleware.team_auth import TeamRole

        assert TeamRole.VIEWER < TeamRole.EDITOR
        assert TeamRole.EDITOR < TeamRole.OWNER
        assert TeamRole.VIEWER < TeamRole.OWNER
        assert TeamRole.VIEWER == 10
        assert TeamRole.EDITOR == 20
        assert TeamRole.OWNER == 30

    # 2. _ROLE_MAP maps all expected strings ---------------------------------
    def test_role_map_completeness(self):
        """_ROLE_MAP includes viewer, editor, owner, admin, read, write."""
        from middleware.team_auth import _ROLE_MAP, TeamRole

        assert _ROLE_MAP["viewer"] == TeamRole.VIEWER
        assert _ROLE_MAP["editor"] == TeamRole.EDITOR
        assert _ROLE_MAP["owner"] == TeamRole.OWNER
        assert _ROLE_MAP["admin"] == TeamRole.OWNER
        assert _ROLE_MAP["read"] == TeamRole.VIEWER
        assert _ROLE_MAP["write"] == TeamRole.EDITOR

    # 3. require_project_role(EDITOR) blocks VIEWER -------------------------
    def test_editor_required_blocks_viewer(self):
        """A VIEWER is blocked from an EDITOR-required endpoint (403)."""
        from middleware.team_auth import TeamRole
        from middleware.auth import get_current_user

        app = _build_role_app(TeamRole.EDITOR)
        user = _make_user()

        app.dependency_overrides[get_current_user] = lambda: user

        with patch(
            "middleware.team_auth._resolve_project_role", new_callable=AsyncMock
        ) as mock_resolve:
            mock_resolve.return_value = TeamRole.VIEWER
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/projects/proj_1/data")

        assert resp.status_code == 403
        assert "Insufficient permissions" in resp.json().get("detail", "")

    # 4. require_project_role(EDITOR) allows EDITOR -------------------------
    def test_editor_required_allows_editor(self):
        """An EDITOR passes an EDITOR-required check."""
        from middleware.team_auth import TeamRole
        from middleware.auth import get_current_user

        app = _build_role_app(TeamRole.EDITOR)
        user = _make_user()
        app.dependency_overrides[get_current_user] = lambda: user

        with patch(
            "middleware.team_auth._resolve_project_role", new_callable=AsyncMock
        ) as mock_resolve:
            mock_resolve.return_value = TeamRole.EDITOR
            client = TestClient(app)
            resp = client.get("/projects/proj_1/data")

        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "EDITOR"
        assert body["is_owner"] is False

    # 5. require_project_role(EDITOR) allows OWNER --------------------------
    def test_editor_required_allows_owner(self):
        """An OWNER passes an EDITOR-required check."""
        from middleware.team_auth import TeamRole
        from middleware.auth import get_current_user

        app = _build_role_app(TeamRole.EDITOR)
        user = _make_user()
        app.dependency_overrides[get_current_user] = lambda: user

        with patch(
            "middleware.team_auth._resolve_project_role", new_callable=AsyncMock
        ) as mock_resolve:
            mock_resolve.return_value = TeamRole.OWNER
            client = TestClient(app)
            resp = client.get("/projects/proj_1/data")

        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "OWNER"
        assert body["is_owner"] is True

    # 6. require_project_role(OWNER) blocks EDITOR --------------------------
    def test_owner_required_blocks_editor(self):
        """An EDITOR is blocked from an OWNER-required endpoint (403)."""
        from middleware.team_auth import TeamRole
        from middleware.auth import get_current_user

        app = _build_role_app(TeamRole.OWNER)
        user = _make_user()
        app.dependency_overrides[get_current_user] = lambda: user

        with patch(
            "middleware.team_auth._resolve_project_role", new_callable=AsyncMock
        ) as mock_resolve:
            mock_resolve.return_value = TeamRole.EDITOR
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/projects/proj_1/data")

        assert resp.status_code == 403

    # 7. Cannot promote collaborator above own role -------------------------
    def test_cannot_promote_above_own_role(self):
        """can_modify_collaborator returns False when promoting above requester role."""
        from middleware.team_auth import can_modify_collaborator, TeamRole

        # Owner promoting to OWNER is fine (equal)
        assert (
            can_modify_collaborator(TeamRole.OWNER, TeamRole.VIEWER, TeamRole.EDITOR)
            is True
        )

        # Owner cannot promote to a role above OWNER (impossible in enum, but test logic)
        # EDITOR trying to promote anyone → False (not OWNER)
        assert (
            can_modify_collaborator(TeamRole.EDITOR, TeamRole.VIEWER, TeamRole.EDITOR)
            is False
        )

    # 8. Owner can modify any collaborator ----------------------------------
    def test_owner_can_modify_any(self):
        """Owner can modify VIEWER and EDITOR collaborators."""
        from middleware.team_auth import can_modify_collaborator, TeamRole

        assert can_modify_collaborator(TeamRole.OWNER, TeamRole.VIEWER) is True
        assert can_modify_collaborator(TeamRole.OWNER, TeamRole.EDITOR) is True
        assert (
            can_modify_collaborator(TeamRole.OWNER, TeamRole.VIEWER, TeamRole.EDITOR)
            is True
        )

    # 9. VIEWER can_modify_collaborator → False for all targets --------------
    def test_viewer_cannot_modify_anyone(self):
        """VIEWER cannot modify any collaborator."""
        from middleware.team_auth import can_modify_collaborator, TeamRole

        assert can_modify_collaborator(TeamRole.VIEWER, TeamRole.VIEWER) is False
        assert can_modify_collaborator(TeamRole.VIEWER, TeamRole.EDITOR) is False
        assert can_modify_collaborator(TeamRole.VIEWER, TeamRole.OWNER) is False

    # 10. Missing project_id in path → 400 ----------------------------------
    def test_missing_project_id_returns_400(self):
        """Request without project_id in path params returns 400."""
        from middleware.team_auth import require_project_role, TeamRole, TeamRoleCheck
        from middleware.auth import get_current_user

        app = FastAPI()

        @app.get("/no-project-id")
        async def no_pid(
            role_check: TeamRoleCheck = Depends(require_project_role(TeamRole.VIEWER)),
        ):
            return {"ok": True}

        user = _make_user()
        app.dependency_overrides[get_current_user] = lambda: user

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/no-project-id")

        assert resp.status_code == 400
        assert "project_id" in resp.json().get("detail", "").lower()

    # 11. Non-existent project → 403 (no access) ----------------------------
    def test_nonexistent_project_returns_403(self):
        """When _resolve_project_role returns None, endpoint returns 403."""
        from middleware.team_auth import TeamRole
        from middleware.auth import get_current_user

        app = _build_role_app(TeamRole.VIEWER)
        user = _make_user()
        app.dependency_overrides[get_current_user] = lambda: user

        with patch(
            "middleware.team_auth._resolve_project_role", new_callable=AsyncMock
        ) as mock_resolve:
            mock_resolve.return_value = None  # No access
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/projects/nonexistent_proj/data")

        assert resp.status_code == 403
        assert "do not have access" in resp.json().get("detail", "").lower()

    # 12. Dev mode grants OWNER access ---------------------------------------
    @pytest.mark.asyncio
    async def test_dev_mode_grants_owner(self):
        """In DEV_MODE, _resolve_project_role returns OWNER."""
        from middleware.team_auth import _resolve_project_role, TeamRole

        user = _make_user()

        with patch("middleware.team_auth.DEV_MODE", True):
            role = await _resolve_project_role(user, "any_project")

        assert role == TeamRole.OWNER

    # 13. Org membership fallback: same org gets EDITOR ----------------------
    @pytest.mark.asyncio
    async def test_org_membership_fallback(self):
        """User in same org as project gets EDITOR via org membership fallback."""
        from middleware.team_auth import _resolve_project_role, TeamRole

        user = _make_user(org_id="org_shared")

        mock_db = MagicMock()
        mock_db.dev_mode = False
        mock_db.query = AsyncMock(
            side_effect=[
                # First call: projects:getById — project belongs to org_shared
                {"ownerId": "other_owner", "organizationId": "org_shared"},
                # Second call: collaborators:listByProject — no collaborator match
                [],
            ]
        )

        with patch("middleware.team_auth.DEV_MODE", False), patch(
            "db.convex.get_convex_db", return_value=mock_db
        ):
            role = await _resolve_project_role(user, "proj_org")

        assert role == TeamRole.EDITOR

    # 14. Role string aliases: admin=OWNER, read=VIEWER, write=EDITOR -------
    def test_parse_role_aliases(self):
        """parse_role handles standard names and aliases."""
        from middleware.team_auth import parse_role, TeamRole

        assert parse_role("admin") == TeamRole.OWNER
        assert parse_role("read") == TeamRole.VIEWER
        assert parse_role("write") == TeamRole.EDITOR
        assert parse_role("VIEWER") == TeamRole.VIEWER  # case-insensitive
        assert parse_role("  Editor  ") == TeamRole.EDITOR  # whitespace stripped

    # 15. check_role_hierarchy: same role → False (not strictly greater) -----
    def test_check_role_hierarchy_same_role(self):
        """check_role_hierarchy with same role returns False (strict >)."""
        from middleware.team_auth import check_role_hierarchy, TeamRole

        assert check_role_hierarchy(TeamRole.VIEWER, TeamRole.VIEWER) is False
        assert check_role_hierarchy(TeamRole.EDITOR, TeamRole.EDITOR) is False
        assert check_role_hierarchy(TeamRole.OWNER, TeamRole.OWNER) is False

        # Higher manages lower
        assert check_role_hierarchy(TeamRole.OWNER, TeamRole.EDITOR) is True
        assert check_role_hierarchy(TeamRole.OWNER, TeamRole.VIEWER) is True
        assert check_role_hierarchy(TeamRole.EDITOR, TeamRole.VIEWER) is True

        # Lower cannot manage higher
        assert check_role_hierarchy(TeamRole.VIEWER, TeamRole.EDITOR) is False
        assert check_role_hierarchy(TeamRole.EDITOR, TeamRole.OWNER) is False
