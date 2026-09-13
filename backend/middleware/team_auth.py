"""
Team Authorization Middleware — Phase 3.0 Subsystem 3.4
========================================================
Enforces Owner / Editor / Viewer role-based access control
for collaborative project operations.

Role Hierarchy:
  Owner  — full access: read, write, delete, manage members, transfer ownership
  Editor — read + write: can edit code, trigger builds, comment
  Viewer — read only: can view code, preview, comment (no edits)

Usage:
    from middleware.team_auth import require_project_role, TeamRole

    @router.put("/projects/{project_id}/files/{path}")
    async def update_file(
        project_id: str,
        path: str,
        role_check: TeamRoleCheck = Depends(require_project_role(TeamRole.EDITOR)),
    ):
        # Only editors and owners can update files
        ...
"""

from __future__ import annotations

from enum import IntEnum
from dataclasses import dataclass
from typing import Optional

from fastapi import Request, HTTPException, Depends

from middleware.auth import get_current_user, AuthenticatedUser, DEV_MODE

# ─── Role Definitions ─────────────────────────────────────────────────────


class TeamRole(IntEnum):
    """
    Role hierarchy using integer values for easy comparison.
    Higher value = more permissions.
    """

    VIEWER = 10
    EDITOR = 20
    OWNER = 30


# String → TeamRole mapping (from Convex collaborators.role field)
_ROLE_MAP: dict[str, TeamRole] = {
    "viewer": TeamRole.VIEWER,
    "editor": TeamRole.EDITOR,
    "owner": TeamRole.OWNER,
    # Common aliases
    "admin": TeamRole.OWNER,
    "read": TeamRole.VIEWER,
    "write": TeamRole.EDITOR,
}


def parse_role(role_str: str) -> TeamRole:
    """Parse a role string into a TeamRole enum value."""
    normalized = role_str.strip().lower()
    role = _ROLE_MAP.get(normalized)
    if role is None:
        raise ValueError(f"Unknown role: {role_str!r}. Valid: {list(_ROLE_MAP.keys())}")
    return role


# ─── Role Check Result ────────────────────────────────────────────────────


@dataclass
class TeamRoleCheck:
    """Result of a team role authorization check."""

    user: AuthenticatedUser
    project_id: str
    role: TeamRole
    is_owner: bool

    @property
    def can_read(self) -> bool:
        return self.role >= TeamRole.VIEWER

    @property
    def can_write(self) -> bool:
        return self.role >= TeamRole.EDITOR

    @property
    def can_manage(self) -> bool:
        return self.role >= TeamRole.OWNER


# ─── Role Resolution ──────────────────────────────────────────────────────


async def _resolve_project_role(
    user: AuthenticatedUser,
    project_id: str,
) -> Optional[TeamRole]:
    """
    Determine a user's role in a project.

    Resolution order:
    1. Project owner (projects.ownerId matches user) → OWNER
    2. Collaborator record (collaborators table) → role from record
    3. Org membership (if project belongs to org and user is member) → EDITOR
    4. None (no access)
    """
    # In dev mode, grant owner access to everything
    if DEV_MODE:
        return TeamRole.OWNER

    try:
        from db.convex import get_convex_db

        db = get_convex_db()

        if db.dev_mode:
            return TeamRole.OWNER

        # Check 1: Is user the project owner?
        project = await db.query("projects:getById", {"id": project_id})
        if project:
            # Compare by Convex user ID if resolved, else by Clerk ID
            owner_id = project.get("ownerId", "")
            if user.convex_user_id and owner_id == user.convex_user_id:
                return TeamRole.OWNER
            # Fallback: check by email if available
            if user.id and owner_id == user.id:
                return TeamRole.OWNER

        # Check 2: Is user a collaborator on this project?
        collaborators = await db.query(
            "collaborators:listByProject",
            {"projectId": project_id},
        )
        if collaborators and isinstance(collaborators, list):
            for collab in collaborators:
                # Match by email (collaborators store userEmail)
                collab_email = collab.get("userEmail", "")
                if (
                    user.email and collab_email.lower() == user.email.lower()
                ) and collab.get("accepted", False):
                    role_str = collab.get("role", "viewer")
                    return parse_role(role_str)

        # Check 3: Org membership (if project belongs to an org)
        if project:
            org_id = project.get("organizationId")
            if org_id and user.org_id == org_id:
                # Org members get editor access by default
                return TeamRole.EDITOR

        return None

    except Exception:
        # If Convex is unavailable, dev mode fallback
        if DEV_MODE:
            return TeamRole.OWNER
        return None


# ─── FastAPI Dependencies ──────────────────────────────────────────────────


def require_project_role(minimum_role: TeamRole):
    """
    FastAPI dependency factory that enforces a minimum project role.

    Usage:
        @router.put("/projects/{project_id}/files")
        async def update_file(
            project_id: str,
            role_check: TeamRoleCheck = Depends(require_project_role(TeamRole.EDITOR)),
        ):
            ...
    """

    async def _check_role(
        request: Request,
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> TeamRoleCheck:
        # Extract project_id from path params
        project_id = request.path_params.get("project_id")
        if not project_id:
            # Try query params or body
            project_id = request.query_params.get("project_id")

        if not project_id:
            raise HTTPException(
                status_code=400,
                detail="Missing project_id in request path or query params",
            )

        # Resolve the user's role in this project
        role = await _resolve_project_role(user, project_id)

        if role is None:
            raise HTTPException(
                status_code=403,
                detail="You do not have access to this project",
            )

        if role < minimum_role:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Insufficient permissions. Required: {minimum_role.name}, "
                    f"your role: {role.name}"
                ),
            )

        return TeamRoleCheck(
            user=user,
            project_id=project_id,
            role=role,
            is_owner=(role >= TeamRole.OWNER),
        )

    return _check_role


# ─── Convenience Shortcuts ─────────────────────────────────────────────────

require_viewer = require_project_role(TeamRole.VIEWER)
require_editor = require_project_role(TeamRole.EDITOR)
require_owner = require_project_role(TeamRole.OWNER)


# ─── Utility Functions ────────────────────────────────────────────────────


def check_role_hierarchy(user_role: TeamRole, target_role: TeamRole) -> bool:
    """Check if user_role has sufficient permissions to manage target_role."""
    return user_role > target_role


def can_modify_collaborator(
    requester_role: TeamRole,
    target_current_role: TeamRole,
    target_new_role: Optional[TeamRole] = None,
) -> bool:
    """
    Check if a user can modify another collaborator's role.

    Rules:
    - Owner can modify anyone (except removing self as last owner)
    - Editor cannot modify anyone's role
    - Viewer cannot modify anything
    - Cannot promote someone above your own role
    """
    if requester_role < TeamRole.OWNER:
        return False

    if target_new_role and target_new_role > requester_role:
        return False

    return True


__all__ = [
    "TeamRole",
    "TeamRoleCheck",
    "parse_role",
    "require_project_role",
    "require_viewer",
    "require_editor",
    "require_owner",
    "check_role_hierarchy",
    "can_modify_collaborator",
]
