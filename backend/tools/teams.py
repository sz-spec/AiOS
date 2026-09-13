"""
Team Collaboration Service
===========================
Organizations, teams, roles, and project sharing.

Features:
- Organizations/Teams
- Role-based permissions
- Project sharing
- Team invitations
- Activity feed
- Real-time collaboration
"""

import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
import secrets
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# Enums & Types
# =============================================================================


class TeamRole(str, Enum):
    """Team member roles."""

    OWNER = "owner"  # Full control, can delete team
    ADMIN = "admin"  # Can manage members, settings
    MEMBER = "member"  # Can create/edit projects
    VIEWER = "viewer"  # Read-only access


class ProjectPermission(str, Enum):
    """Project-level permissions."""

    OWNER = "owner"  # Full control
    EDITOR = "editor"  # Can edit
    COMMENTER = "commenter"  # Can comment only
    VIEWER = "viewer"  # Read-only


class InviteStatus(str, Enum):
    """Invitation status."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"


class ActivityType(str, Enum):
    """Activity types for feed."""

    MEMBER_JOINED = "member_joined"
    MEMBER_LEFT = "member_left"
    MEMBER_ROLE_CHANGED = "member_role_changed"
    PROJECT_CREATED = "project_created"
    PROJECT_SHARED = "project_shared"
    PROJECT_UPDATED = "project_updated"
    COMMENT_ADDED = "comment_added"
    INVITE_SENT = "invite_sent"


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class TeamMember:
    """Team member."""

    user_id: str
    team_id: str
    role: TeamRole
    joined_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    invited_by: Optional[str] = None

    # User info (populated from user table)
    email: Optional[str] = None
    name: Optional[str] = None
    avatar_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "role": self.role.value,
            "joined_at": self.joined_at.isoformat(),
        }


@dataclass
class Team:
    """Team/Organization."""

    id: str
    name: str
    slug: str
    owner_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Settings
    logo_url: Optional[str] = None
    description: Optional[str] = None
    website: Optional[str] = None

    # Limits (based on plan)
    max_members: int = 5
    max_projects: int = 10

    # Counts (calculated)
    member_count: int = 0
    project_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class TeamInvite:
    """Team invitation."""

    id: str
    team_id: str
    email: str
    role: TeamRole
    invited_by: str
    status: InviteStatus = InviteStatus.PENDING
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=7)
    )
    accepted_at: Optional[datetime] = None

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "role": self.role.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
        }


@dataclass
class ProjectShare:
    """Project sharing with team or individual."""

    id: str
    project_id: str
    shared_by: str
    permission: ProjectPermission
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Share with team OR user (one must be set)
    team_id: Optional[str] = None
    user_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "permission": self.permission.value,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Activity:
    """Activity feed item."""

    id: str
    team_id: str
    user_id: str
    activity_type: ActivityType
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Context
    project_id: Optional[str] = None
    target_user_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # User info (populated)
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "activity_type": self.activity_type.value,
            "created_at": self.created_at.isoformat(),
        }


# =============================================================================
# Permission Checks
# =============================================================================


class PermissionChecker:
    """Check user permissions for teams and projects."""

    ROLE_HIERARCHY = {
        TeamRole.OWNER: 4,
        TeamRole.ADMIN: 3,
        TeamRole.MEMBER: 2,
        TeamRole.VIEWER: 1,
    }

    PROJECT_HIERARCHY = {
        ProjectPermission.OWNER: 4,
        ProjectPermission.EDITOR: 3,
        ProjectPermission.COMMENTER: 2,
        ProjectPermission.VIEWER: 1,
    }

    @classmethod
    def can_manage_team(cls, role: TeamRole) -> bool:
        """Can manage team settings and members."""
        return cls.ROLE_HIERARCHY[role] >= cls.ROLE_HIERARCHY[TeamRole.ADMIN]

    @classmethod
    def can_invite_members(cls, role: TeamRole) -> bool:
        """Can invite new members."""
        return cls.ROLE_HIERARCHY[role] >= cls.ROLE_HIERARCHY[TeamRole.ADMIN]

    @classmethod
    def can_remove_member(cls, remover_role: TeamRole, target_role: TeamRole) -> bool:
        """Can remove a member (must be higher role)."""
        return cls.ROLE_HIERARCHY[remover_role] > cls.ROLE_HIERARCHY[target_role]

    @classmethod
    def can_change_role(cls, changer_role: TeamRole, new_role: TeamRole) -> bool:
        """Can change member role (can't assign higher than own)."""
        return cls.ROLE_HIERARCHY[changer_role] >= cls.ROLE_HIERARCHY[new_role]

    @classmethod
    def can_create_projects(cls, role: TeamRole) -> bool:
        """Can create new projects."""
        return cls.ROLE_HIERARCHY[role] >= cls.ROLE_HIERARCHY[TeamRole.MEMBER]

    @classmethod
    def can_edit_project(cls, permission: ProjectPermission) -> bool:
        """Can edit project."""
        return (
            cls.PROJECT_HIERARCHY[permission]
            >= cls.PROJECT_HIERARCHY[ProjectPermission.EDITOR]
        )

    @classmethod
    def can_comment(cls, permission: ProjectPermission) -> bool:
        """Can add comments."""
        return (
            cls.PROJECT_HIERARCHY[permission]
            >= cls.PROJECT_HIERARCHY[ProjectPermission.COMMENTER]
        )

    @classmethod
    def can_share_project(cls, permission: ProjectPermission) -> bool:
        """Can share project with others."""
        return (
            cls.PROJECT_HIERARCHY[permission]
            >= cls.PROJECT_HIERARCHY[ProjectPermission.EDITOR]
        )

    @classmethod
    def can_delete_project(cls, permission: ProjectPermission) -> bool:
        """Can delete project."""
        return permission == ProjectPermission.OWNER


# =============================================================================
# Team Service
# =============================================================================


class TeamService:
    """
    Team collaboration service.

    Usage:
        service = TeamService()

        # Create team
        team = await service.create_team("My Team", user_id)

        # Invite member
        invite = await service.invite_member(team.id, "user@example.com", TeamRole.MEMBER, user_id)

        # Share project
        await service.share_project(project_id, team_id=team.id, permission=ProjectPermission.EDITOR)
    """

    def __init__(self, email_service=None, analytics_service=None):
        self.email = email_service
        self.analytics = analytics_service

        # In-memory storage
        self._teams: Dict[str, Team] = {}
        self._members: Dict[str, List[TeamMember]] = {}  # team_id -> members
        self._invites: Dict[str, TeamInvite] = {}
        self._shares: Dict[str, List[ProjectShare]] = {}  # project_id -> shares
        self._activities: Dict[str, List[Activity]] = {}  # team_id -> activities

    # -------------------------------------------------------------------------
    # Team CRUD
    # -------------------------------------------------------------------------

    async def create_team(
        self,
        name: str,
        owner_id: str,
        description: Optional[str] = None,
        logo_url: Optional[str] = None,
    ) -> Team:
        """Create a new team."""
        team_id = str(uuid4())
        slug = self._generate_slug(name)

        team = Team(
            id=team_id,
            name=name,
            slug=slug,
            owner_id=owner_id,
            description=description,
            logo_url=logo_url,
            member_count=1,
        )

        # Store team
        self._teams[team_id] = team

        # Add owner as member
        await self._add_member(team_id, owner_id, TeamRole.OWNER)

        # Track analytics
        if self.analytics:
            await self.analytics.track(
                "team_created", owner_id, {"team_id": team_id, "team_name": name}
            )

        logger.info(f"Team created: {name} (ID: {team_id})")
        return team

    async def get_team(self, team_id: str) -> Optional[Team]:
        """Get team by ID."""
        return self._teams.get(team_id)

    async def get_team_by_slug(self, slug: str) -> Optional[Team]:
        """Get team by slug."""
        for team in self._teams.values():
            if team.slug == slug:
                return team
        return None

    async def update_team(
        self, team_id: str, user_id: str, **updates
    ) -> Optional[Team]:
        """Update team settings."""
        # Check permission
        member = await self.get_member(team_id, user_id)
        if not member or not PermissionChecker.can_manage_team(member.role):
            raise PermissionError("Not authorized to update team")

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        team = self._teams.get(team_id)
        if team:
            for key, value in updates.items():
                if hasattr(team, key):
                    setattr(team, key, value)
        return team

    async def delete_team(self, team_id: str, user_id: str) -> bool:
        """Delete team (owner only)."""
        team = await self.get_team(team_id)
        if not team or team.owner_id != user_id:
            raise PermissionError("Only owner can delete team")

        del self._teams[team_id]

        logger.info(f"Team deleted: {team_id}")
        return True

    async def get_user_teams(self, user_id: str) -> List[Team]:
        """Get all teams a user belongs to."""
        teams = []
        for team_id, members in self._members.items():
            if any(m.user_id == user_id for m in members):
                team = self._teams.get(team_id)
                if team:
                    teams.append(team)
        return teams

    # -------------------------------------------------------------------------
    # Members
    # -------------------------------------------------------------------------

    async def _add_member(
        self,
        team_id: str,
        user_id: str,
        role: TeamRole,
        invited_by: Optional[str] = None,
    ) -> TeamMember:
        """Internal: Add member to team."""
        member = TeamMember(
            user_id=user_id,
            team_id=team_id,
            role=role,
            invited_by=invited_by,
        )

        if team_id not in self._members:
            self._members[team_id] = []
        self._members[team_id].append(member)

        # Log activity
        await self._log_activity(
            team_id, user_id, ActivityType.MEMBER_JOINED, metadata={"role": role.value}
        )

        return member

    async def get_member(self, team_id: str, user_id: str) -> Optional[TeamMember]:
        """Get team member."""
        members = self._members.get(team_id, [])
        for member in members:
            if member.user_id == user_id:
                return member
        return None

    async def get_team_members(self, team_id: str) -> List[TeamMember]:
        """Get all team members."""
        return self._members.get(team_id, [])

    async def update_member_role(
        self, team_id: str, target_user_id: str, new_role: TeamRole, changer_id: str
    ) -> TeamMember:
        """Update member role."""
        changer = await self.get_member(team_id, changer_id)
        target = await self.get_member(team_id, target_user_id)

        if not changer or not target:
            raise ValueError("Member not found")

        if not PermissionChecker.can_change_role(changer.role, new_role):
            raise PermissionError("Cannot assign role higher than your own")

        if not PermissionChecker.can_remove_member(changer.role, target.role):
            raise PermissionError("Cannot modify member with higher role")

        target.role = new_role

        # Log activity
        await self._log_activity(
            team_id,
            changer_id,
            ActivityType.MEMBER_ROLE_CHANGED,
            target_user_id=target_user_id,
            metadata={"new_role": new_role.value},
        )

        return target

    async def remove_member(
        self, team_id: str, target_user_id: str, remover_id: str
    ) -> bool:
        """Remove member from team."""
        remover = await self.get_member(team_id, remover_id)
        target = await self.get_member(team_id, target_user_id)

        if not remover or not target:
            raise ValueError("Member not found")

        # Can remove self or lower role
        if target_user_id != remover_id:
            if not PermissionChecker.can_remove_member(remover.role, target.role):
                raise PermissionError("Cannot remove member with higher role")

        # Can't remove owner
        team = await self.get_team(team_id)
        if team and team.owner_id == target_user_id:
            raise PermissionError("Cannot remove team owner")

        members = self._members.get(team_id, [])
        self._members[team_id] = [m for m in members if m.user_id != target_user_id]

        # Log activity
        await self._log_activity(
            team_id, remover_id, ActivityType.MEMBER_LEFT, target_user_id=target_user_id
        )

        return True

    # -------------------------------------------------------------------------
    # Invitations
    # -------------------------------------------------------------------------

    async def invite_member(
        self, team_id: str, email: str, role: TeamRole, inviter_id: str
    ) -> TeamInvite:
        """Invite someone to join the team."""
        inviter = await self.get_member(team_id, inviter_id)
        if not inviter or not PermissionChecker.can_invite_members(inviter.role):
            raise PermissionError("Not authorized to invite members")

        # Check limits
        team = await self.get_team(team_id)
        if team and team.member_count >= team.max_members:
            raise ValueError(f"Team limit reached ({team.max_members} members)")

        invite = TeamInvite(
            id=str(uuid4()),
            team_id=team_id,
            email=email,
            role=role,
            invited_by=inviter_id,
        )

        self._invites[invite.id] = invite

        # Send invitation email
        if self.email and team:
            invite_url = (
                f"{os.getenv('APP_URL', 'http://localhost:3000')}/invite/{invite.token}"
            )
            await self.email.send_team_invitation(
                to=email,
                inviter_name=inviter.name or "A team member",
                team_name=team.name,
                invite_url=invite_url,
            )

        # Log activity
        await self._log_activity(
            team_id,
            inviter_id,
            ActivityType.INVITE_SENT,
            metadata={"email": email, "role": role.value},
        )

        logger.info(f"Invite sent to {email} for team {team_id}")
        return invite

    async def accept_invite(self, token: str, user_id: str) -> TeamMember:
        """Accept a team invitation."""
        invite = await self._get_invite_by_token(token)
        if not invite:
            raise ValueError("Invalid invitation")

        if invite.is_expired():
            raise ValueError("Invitation has expired")

        if invite.status != InviteStatus.PENDING:
            raise ValueError("Invitation already used")

        # Update invite status
        invite.status = InviteStatus.ACCEPTED
        invite.accepted_at = datetime.now(timezone.utc)

        # Add member
        member = await self._add_member(
            invite.team_id, user_id, invite.role, invited_by=invite.invited_by
        )

        return member

    async def decline_invite(self, token: str) -> bool:
        """Decline a team invitation."""
        invite = await self._get_invite_by_token(token)
        if not invite:
            return False

        invite.status = InviteStatus.DECLINED

        return True

    async def get_pending_invites(self, team_id: str) -> List[TeamInvite]:
        """Get pending invitations for a team."""
        return [
            inv
            for inv in self._invites.values()
            if inv.team_id == team_id and inv.status == InviteStatus.PENDING
        ]

    async def _get_invite_by_token(self, token: str) -> Optional[TeamInvite]:
        """Get invite by token."""
        for invite in self._invites.values():
            if invite.token == token:
                return invite
        return None

    # -------------------------------------------------------------------------
    # Project Sharing
    # -------------------------------------------------------------------------

    async def share_project(
        self,
        project_id: str,
        sharer_id: str,
        permission: ProjectPermission,
        team_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> ProjectShare:
        """Share project with team or user."""
        if not team_id and not user_id:
            raise ValueError("Must specify team_id or user_id")

        share = ProjectShare(
            id=str(uuid4()),
            project_id=project_id,
            shared_by=sharer_id,
            permission=permission,
            team_id=team_id,
            user_id=user_id,
        )

        if project_id not in self._shares:
            self._shares[project_id] = []
        self._shares[project_id].append(share)

        # Log activity
        if team_id:
            await self._log_activity(
                team_id,
                sharer_id,
                ActivityType.PROJECT_SHARED,
                project_id=project_id,
                metadata={"permission": permission.value},
            )

        # Track analytics
        if self.analytics:
            await self.analytics.track(
                "project_shared",
                sharer_id,
                {
                    "project_id": project_id,
                    "team_id": team_id,
                    "user_id": user_id,
                    "permission": permission.value,
                },
            )

        return share

    async def get_project_shares(self, project_id: str) -> List[ProjectShare]:
        """Get all shares for a project."""
        return self._shares.get(project_id, [])

    async def get_user_project_permission(
        self, project_id: str, user_id: str
    ) -> Optional[ProjectPermission]:
        """Get user's permission for a project."""
        shares = await self.get_project_shares(project_id)

        highest_permission = None

        for share in shares:
            # Direct user share
            if share.user_id == user_id:
                if (
                    highest_permission is None
                    or PermissionChecker.PROJECT_HIERARCHY[share.permission]
                    > PermissionChecker.PROJECT_HIERARCHY[highest_permission]
                ):
                    highest_permission = share.permission

            # Team share
            if share.team_id:
                member = await self.get_member(share.team_id, user_id)
                if member:
                    if (
                        highest_permission is None
                        or PermissionChecker.PROJECT_HIERARCHY[share.permission]
                        > PermissionChecker.PROJECT_HIERARCHY[highest_permission]
                    ):
                        highest_permission = share.permission

        return highest_permission

    async def remove_share(self, share_id: str, user_id: str) -> bool:
        """Remove a project share."""
        for project_id, shares in self._shares.items():
            self._shares[project_id] = [s for s in shares if s.id != share_id]
        return True

    # -------------------------------------------------------------------------
    # Activity Feed
    # -------------------------------------------------------------------------

    async def _log_activity(
        self,
        team_id: str,
        user_id: str,
        activity_type: ActivityType,
        project_id: Optional[str] = None,
        target_user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Log activity to feed."""
        activity = Activity(
            id=str(uuid4()),
            team_id=team_id,
            user_id=user_id,
            activity_type=activity_type,
            project_id=project_id,
            target_user_id=target_user_id,
            metadata=metadata or {},
        )

        if team_id not in self._activities:
            self._activities[team_id] = []
        self._activities[team_id].append(activity)

    async def get_activity_feed(
        self, team_id: str, limit: int = 50, offset: int = 0
    ) -> List[Activity]:
        """Get team activity feed."""
        activities = self._activities.get(team_id, [])
        activities.sort(key=lambda a: a.created_at, reverse=True)
        return activities[offset : offset + limit]

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _generate_slug(self, name: str) -> str:
        """Generate URL-safe slug from name."""
        import re

        slug = name.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        # Add random suffix for uniqueness
        suffix = secrets.token_hex(3)
        return f"{slug}-{suffix}"

    def _dict_to_team(self, data: Dict) -> Team:
        """Convert dict to Team."""
        data = dict(data)
        data.pop("users", None)
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(
                data["created_at"].replace("Z", "+00:00")
            )
        if isinstance(data.get("updated_at"), str):
            data["updated_at"] = datetime.fromisoformat(
                data["updated_at"].replace("Z", "+00:00")
            )
        return Team(**data)

    def _dict_to_member(self, data: Dict) -> TeamMember:
        """Convert dict to TeamMember."""
        data = dict(data)
        user_data = data.pop("users", None)
        if isinstance(data.get("role"), str):
            data["role"] = TeamRole(data["role"])
        if isinstance(data.get("joined_at"), str):
            data["joined_at"] = datetime.fromisoformat(
                data["joined_at"].replace("Z", "+00:00")
            )
        member = TeamMember(**data)
        if user_data:
            member.email = user_data.get("email")
            member.name = user_data.get("name")
            member.avatar_url = user_data.get("avatar_url")
        return member

    def _dict_to_invite(self, data: Dict) -> TeamInvite:
        """Convert dict to TeamInvite."""
        data = dict(data)
        if isinstance(data.get("role"), str):
            data["role"] = TeamRole(data["role"])
        if isinstance(data.get("status"), str):
            data["status"] = InviteStatus(data["status"])
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(
                data["created_at"].replace("Z", "+00:00")
            )
        if isinstance(data.get("expires_at"), str):
            data["expires_at"] = datetime.fromisoformat(
                data["expires_at"].replace("Z", "+00:00")
            )
        if data.get("accepted_at") and isinstance(data["accepted_at"], str):
            data["accepted_at"] = datetime.fromisoformat(
                data["accepted_at"].replace("Z", "+00:00")
            )
        return TeamInvite(**data)

    def _dict_to_share(self, data: Dict) -> ProjectShare:
        """Convert dict to ProjectShare."""
        data = dict(data)
        if isinstance(data.get("permission"), str):
            data["permission"] = ProjectPermission(data["permission"])
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(
                data["created_at"].replace("Z", "+00:00")
            )
        return ProjectShare(**data)

    def _dict_to_activity(self, data: Dict) -> Activity:
        """Convert dict to Activity."""
        data = dict(data)
        user_data = data.pop("users", None)
        if isinstance(data.get("activity_type"), str):
            data["activity_type"] = ActivityType(data["activity_type"])
        if isinstance(data.get("created_at"), str):
            data["created_at"] = datetime.fromisoformat(
                data["created_at"].replace("Z", "+00:00")
            )
        activity = Activity(**data)
        if user_data:
            activity.user_name = user_data.get("name")
            activity.user_avatar = user_data.get("avatar_url")
        return activity


# =============================================================================
# Singleton
# =============================================================================

_team_service: Optional[TeamService] = None


def get_team_service() -> TeamService:
    """Get team service singleton."""
    global _team_service
    if _team_service is None:
        _team_service = TeamService()
    return _team_service


def init_team_service(email=None, analytics=None) -> TeamService:
    """Initialize team service with dependencies."""
    global _team_service
    _team_service = TeamService(email, analytics)
    return _team_service
