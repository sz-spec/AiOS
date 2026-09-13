"""
Team API Routes
================
REST API endpoints for team collaboration.
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional, List
from pydantic import BaseModel, EmailStr
from datetime import datetime

from api.deps import get_current_user, AuthenticatedUser

logger = logging.getLogger(__name__)
from tools.teams import (
    get_team_service,
    TeamRole,
    ProjectPermission,
    PermissionChecker,
)

router = APIRouter(prefix="/api/teams", tags=["Teams"])


# =============================================================================
# Request/Response Models
# =============================================================================


class CreateTeamRequest(BaseModel):
    name: str
    description: Optional[str] = None
    logo_url: Optional[str] = None


class UpdateTeamRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    website: Optional[str] = None


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = "member"


class UpdateRoleRequest(BaseModel):
    role: str


class ShareProjectRequest(BaseModel):
    project_id: str
    permission: str = "viewer"
    team_id: Optional[str] = None
    user_id: Optional[str] = None


class TeamResponse(BaseModel):
    id: str
    name: str
    slug: str
    owner_id: str
    description: Optional[str]
    logo_url: Optional[str]
    website: Optional[str]
    max_members: int
    max_projects: int
    member_count: int
    project_count: int
    created_at: datetime


class MemberResponse(BaseModel):
    user_id: str
    team_id: str
    role: str
    joined_at: datetime
    email: Optional[str]
    name: Optional[str]
    avatar_url: Optional[str]


class InviteResponse(BaseModel):
    id: str
    team_id: str
    email: str
    role: str
    status: str
    created_at: datetime
    expires_at: datetime


class ActivityResponse(BaseModel):
    id: str
    team_id: str
    user_id: str
    activity_type: str
    created_at: datetime
    project_id: Optional[str]
    target_user_id: Optional[str]
    metadata: dict
    user_name: Optional[str]
    user_avatar: Optional[str]


# =============================================================================
# Team Endpoints
# =============================================================================


@router.post("", response_model=TeamResponse)
async def create_team(
    data: CreateTeamRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create a new team."""
    user_id = user.id
    service = get_team_service()

    try:
        team = await service.create_team(
            name=data.name,
            owner_id=user_id,
            description=data.description,
            logo_url=data.logo_url,
        )
        return TeamResponse(**team.to_dict())
    except Exception as e:
        logger.error("Team creation failed: %s", e)
        raise HTTPException(status_code=400, detail="Team creation failed")


@router.get("", response_model=List[TeamResponse])
async def get_user_teams(user: AuthenticatedUser = Depends(get_current_user)):
    """Get all teams for current user."""
    user_id = user.id
    service = get_team_service()

    teams = await service.get_user_teams(user_id)
    return [TeamResponse(**t.to_dict()) for t in teams]


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(team_id: str, user: AuthenticatedUser = Depends(get_current_user)):
    """Get team details."""
    user_id = user.id
    service = get_team_service()

    # Check membership
    member = await service.get_member(team_id, user_id)
    if not member:
        raise HTTPException(status_code=403, detail="Not a team member")

    team = await service.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    return TeamResponse(**team.to_dict())


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: str,
    data: UpdateTeamRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update team settings."""
    user_id = user.id
    service = get_team_service()

    try:
        updates = data.dict(exclude_none=True)
        team = await service.update_team(team_id, user_id, **updates)
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        return TeamResponse(**team.to_dict())
    except PermissionError as e:
        logger.error("Team update permission denied: %s", e)
        raise HTTPException(status_code=403, detail="Permission denied")


@router.delete("/{team_id}")
async def delete_team(
    team_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Delete a team (owner only)."""
    user_id = user.id
    service = get_team_service()

    try:
        await service.delete_team(team_id, user_id)
        return {"status": "deleted"}
    except PermissionError as e:
        logger.error("Team deletion permission denied: %s", e)
        raise HTTPException(status_code=403, detail="Permission denied")


# =============================================================================
# Member Endpoints
# =============================================================================


@router.get("/{team_id}/members", response_model=List[MemberResponse])
async def get_team_members(
    team_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get all team members."""
    user_id = user.id
    service = get_team_service()

    # Check membership
    member = await service.get_member(team_id, user_id)
    if not member:
        raise HTTPException(status_code=403, detail="Not a team member")

    members = await service.get_team_members(team_id)
    return [MemberResponse(**m.to_dict()) for m in members]


@router.patch("/{team_id}/members/{target_user_id}", response_model=MemberResponse)
async def update_member_role(
    team_id: str,
    target_user_id: str,
    data: UpdateRoleRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update a member's role."""
    user_id = user.id
    service = get_team_service()

    try:
        role = TeamRole(data.role)
        member = await service.update_member_role(
            team_id, target_user_id, role, user_id
        )
        return MemberResponse(**member.to_dict())
    except ValueError as e:
        logger.error("Member role update validation failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid role")
    except PermissionError as e:
        logger.error("Member role update permission denied: %s", e)
        raise HTTPException(status_code=403, detail="Permission denied")


@router.delete("/{team_id}/members/{target_user_id}")
async def remove_member(
    team_id: str,
    target_user_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Remove a member from the team."""
    user_id = user.id
    service = get_team_service()

    try:
        await service.remove_member(team_id, target_user_id, user_id)
        return {"status": "removed"}
    except ValueError as e:
        logger.error("Member removal validation failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid request")
    except PermissionError as e:
        logger.error("Member removal permission denied: %s", e)
        raise HTTPException(status_code=403, detail="Permission denied")


# =============================================================================
# Invitation Endpoints
# =============================================================================


@router.post("/{team_id}/invites", response_model=InviteResponse)
async def invite_member(
    team_id: str,
    data: InviteMemberRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Invite someone to join the team."""
    user_id = user.id
    service = get_team_service()

    try:
        role = TeamRole(data.role)
        invite = await service.invite_member(team_id, data.email, role, user_id)
        return InviteResponse(**invite.to_dict())
    except ValueError as e:
        logger.error("Member invitation validation failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid invitation request")
    except PermissionError as e:
        logger.error("Member invitation permission denied: %s", e)
        raise HTTPException(status_code=403, detail="Permission denied")


@router.get("/{team_id}/invites", response_model=List[InviteResponse])
async def get_pending_invites(
    team_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get pending invitations."""
    user_id = user.id
    service = get_team_service()

    # Check admin permission
    member = await service.get_member(team_id, user_id)
    if not member or not PermissionChecker.can_invite_members(member.role):
        raise HTTPException(status_code=403, detail="Not authorized")

    invites = await service.get_pending_invites(team_id)
    return [InviteResponse(**i.to_dict()) for i in invites]


@router.post("/invites/{token}/accept", response_model=MemberResponse)
async def accept_invite(
    token: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Accept a team invitation."""
    user_id = user.id
    service = get_team_service()

    try:
        member = await service.accept_invite(token, user_id)
        return MemberResponse(**member.to_dict())
    except ValueError as e:
        logger.error("Invite acceptance failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid or expired invitation")


@router.post("/invites/{token}/decline")
async def decline_invite(
    token: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Decline a team invitation."""
    service = get_team_service()

    success = await service.decline_invite(token)
    if not success:
        raise HTTPException(status_code=404, detail="Invitation not found")

    return {"status": "declined"}


# =============================================================================
# Project Sharing Endpoints
# =============================================================================


@router.post("/{team_id}/shares")
async def share_project_with_team(
    team_id: str,
    data: ShareProjectRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Share a project with the team."""
    user_id = user.id
    service = get_team_service()

    try:
        permission = ProjectPermission(data.permission)
        share = await service.share_project(
            project_id=data.project_id,
            sharer_id=user_id,
            permission=permission,
            team_id=team_id,
        )
        return share.to_dict()
    except ValueError as e:
        logger.error("Project sharing validation failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid sharing request")


@router.get("/projects/{project_id}/shares")
async def get_project_shares(
    project_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get all shares for a project."""
    service = get_team_service()

    shares = await service.get_project_shares(project_id)
    return [s.to_dict() for s in shares]


@router.get("/projects/{project_id}/permission")
async def get_project_permission(
    project_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get current user's permission for a project."""
    user_id = user.id
    service = get_team_service()

    permission = await service.get_user_project_permission(project_id, user_id)
    return {
        "project_id": project_id,
        "permission": permission.value if permission else None,
        "can_edit": (
            PermissionChecker.can_edit_project(permission) if permission else False
        ),
        "can_comment": (
            PermissionChecker.can_comment(permission) if permission else False
        ),
        "can_share": (
            PermissionChecker.can_share_project(permission) if permission else False
        ),
    }


@router.delete("/shares/{share_id}")
async def remove_share(
    share_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Remove a project share."""
    user_id = user.id
    service = get_team_service()

    await service.remove_share(share_id, user_id)
    return {"status": "removed"}


# =============================================================================
# Activity Feed Endpoints
# =============================================================================


@router.get("/{team_id}/activity", response_model=List[ActivityResponse])
async def get_activity_feed(
    team_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Get team activity feed."""
    user_id = user.id
    service = get_team_service()

    # Check membership
    member = await service.get_member(team_id, user_id)
    if not member:
        raise HTTPException(status_code=403, detail="Not a team member")

    activities = await service.get_activity_feed(team_id, limit, offset)
    return [ActivityResponse(**a.to_dict()) for a in activities]
