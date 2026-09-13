"""
V Core - Control Plane

Users, Roles, Permissions, Organizations, Admin Console
Based on V PRD Section 4.1

Features:
- Multi-tenant organizations
- Role-based access control (RBAC)
- Field-level permissions
- SSO/SAML/OIDC support
- Audit trail
- Admin console
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4
import hashlib
import secrets

import bcrypt

from core.base_service import PersistentService

# Optional repository imports for persistence layer
try:
    from .repositories import (
        get_user_repository,
        get_organization_repository,
        get_membership_repository,
        get_api_key_repository,
        get_audit_log_repository,
        UserRepository,
        OrganizationRepository,
        MembershipRepository,
        ApiKeyRepository,
        AuditLogRepository,
    )
except ImportError:
    UserRepository = None
    OrganizationRepository = None
    MembershipRepository = None
    ApiKeyRepository = None
    AuditLogRepository = None


def hash_password(password: str) -> str:
    """Hash password using bcrypt with automatic salt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash. Also supports legacy SHA256 for migration."""
    # Check if it's a bcrypt hash (starts with $2)
    if hashed.startswith("$2"):
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    # Legacy SHA256 fallback for existing users
    return hashlib.sha256(password.encode()).hexdigest() == hashed


# ============================================
# Enums
# ============================================


class UserStatus(str, Enum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class OrganizationPlan(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class Permission(str, Enum):
    # Organization
    ORG_VIEW = "org:view"
    ORG_EDIT = "org:edit"
    ORG_DELETE = "org:delete"
    ORG_BILLING = "org:billing"

    # Users
    USERS_VIEW = "users:view"
    USERS_INVITE = "users:invite"
    USERS_EDIT = "users:edit"
    USERS_DELETE = "users:delete"

    # Roles
    ROLES_VIEW = "roles:view"
    ROLES_MANAGE = "roles:manage"

    # Data
    DATA_VIEW = "data:view"
    DATA_CREATE = "data:create"
    DATA_EDIT = "data:edit"
    DATA_DELETE = "data:delete"
    DATA_EXPORT = "data:export"

    # Agents
    AGENTS_VIEW = "agents:view"
    AGENTS_CREATE = "agents:create"
    AGENTS_EDIT = "agents:edit"
    AGENTS_DELETE = "agents:delete"
    AGENTS_DEPLOY = "agents:deploy"

    # Workflows
    WORKFLOWS_VIEW = "workflows:view"
    WORKFLOWS_CREATE = "workflows:create"
    WORKFLOWS_EDIT = "workflows:edit"
    WORKFLOWS_DELETE = "workflows:delete"
    WORKFLOWS_EXECUTE = "workflows:execute"

    # Integrations
    INTEGRATIONS_VIEW = "integrations:view"
    INTEGRATIONS_MANAGE = "integrations:manage"

    # Settings
    SETTINGS_VIEW = "settings:view"
    SETTINGS_EDIT = "settings:edit"

    # Admin
    ADMIN_FULL = "admin:full"


class AuditAction(str, Enum):
    LOGIN = "login"
    LOGOUT = "logout"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    EXPORT = "export"
    INVITE = "invite"
    APPROVE = "approve"
    REJECT = "reject"
    DEPLOY = "deploy"


# ============================================
# Data Models
# ============================================


@dataclass
class Role:
    """Role definition with permissions."""

    id: str = field(default_factory=lambda: f"role_{uuid4().hex[:12]}")
    name: str = ""
    description: str = ""
    permissions: list[Permission] = field(default_factory=list)
    organization_id: Optional[str] = None  # None = system role
    is_system: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "permissions": [p.value for p in self.permissions],
            "organizationId": self.organization_id,
            "isSystem": self.is_system,
            "createdAt": self.created_at.isoformat(),
        }

    def has_permission(self, perm: Permission) -> bool:
        return Permission.ADMIN_FULL in self.permissions or perm in self.permissions


@dataclass
class User:
    """User account."""

    id: str = field(default_factory=lambda: f"user_{uuid4().hex[:12]}")
    email: str = ""
    name: str = ""
    avatar_url: Optional[str] = None
    phone: Optional[str] = None

    # Auth
    password_hash: Optional[str] = None
    mfa_enabled: bool = False
    mfa_secret: Optional[str] = None

    # SSO
    sso_provider: Optional[str] = None  # google, microsoft, saml
    sso_id: Optional[str] = None

    # Status
    status: UserStatus = UserStatus.ACTIVE
    email_verified: bool = False

    # Preferences
    language: str = "en"
    timezone: str = "UTC"

    # Metadata
    last_login: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "avatarUrl": self.avatar_url,
            "phone": self.phone,
            "mfaEnabled": self.mfa_enabled,
            "ssoProvider": self.sso_provider,
            "status": self.status.value,
            "emailVerified": self.email_verified,
            "language": self.language,
            "timezone": self.timezone,
            "lastLogin": self.last_login.isoformat() if self.last_login else None,
            "createdAt": self.created_at.isoformat(),
        }


@dataclass
class Organization:
    """Organization/Tenant."""

    id: str = field(default_factory=lambda: f"org_{uuid4().hex[:12]}")
    name: str = ""
    slug: str = ""  # URL-friendly name
    logo_url: Optional[str] = None

    # Plan & Billing
    plan: OrganizationPlan = OrganizationPlan.FREE
    stripe_customer_id: Optional[str] = None

    # Settings
    settings: dict[str, Any] = field(default_factory=dict)

    # Limits
    max_users: int = 5
    max_agents: int = 3
    max_workflows: int = 10

    # Status
    is_active: bool = True

    # Metadata
    owner_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "logoUrl": self.logo_url,
            "plan": self.plan.value,
            "settings": self.settings,
            "maxUsers": self.max_users,
            "maxAgents": self.max_agents,
            "maxWorkflows": self.max_workflows,
            "isActive": self.is_active,
            "ownerId": self.owner_id,
            "createdAt": self.created_at.isoformat(),
        }


@dataclass
class OrganizationMember:
    """User membership in organization."""

    id: str = field(default_factory=lambda: f"mem_{uuid4().hex[:12]}")
    organization_id: str = ""
    user_id: str = ""
    role_id: str = ""

    # Status
    is_owner: bool = False
    invited_by: Optional[str] = None
    invited_at: Optional[datetime] = None
    joined_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organizationId": self.organization_id,
            "userId": self.user_id,
            "roleId": self.role_id,
            "isOwner": self.is_owner,
            "invitedBy": self.invited_by,
            "joinedAt": self.joined_at.isoformat() if self.joined_at else None,
        }


@dataclass
class Invitation:
    """Pending invitation."""

    id: str = field(default_factory=lambda: f"inv_{uuid4().hex[:12]}")
    organization_id: str = ""
    email: str = ""
    role_id: str = ""
    invited_by: str = ""
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    accepted_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organizationId": self.organization_id,
            "email": self.email,
            "roleId": self.role_id,
            "invitedBy": self.invited_by,
            "expiresAt": self.expires_at.isoformat(),
            "acceptedAt": self.accepted_at.isoformat() if self.accepted_at else None,
        }


@dataclass
class AuditLog:
    """Immutable audit trail entry."""

    id: str = field(default_factory=lambda: f"audit_{uuid4().hex[:12]}")
    organization_id: str = ""
    user_id: str = ""
    action: AuditAction = AuditAction.CREATE
    resource_type: str = ""  # user, agent, workflow, etc.
    resource_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organizationId": self.organization_id,
            "userId": self.user_id,
            "action": self.action.value,
            "resourceType": self.resource_type,
            "resourceId": self.resource_id,
            "details": self.details,
            "ipAddress": self.ip_address,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ApiKey:
    """API key for programmatic access."""

    id: str = field(default_factory=lambda: f"key_{uuid4().hex[:12]}")
    organization_id: str = ""
    name: str = ""
    key_hash: str = ""  # Hashed key, actual key shown once
    prefix: str = ""  # First 8 chars for identification
    permissions: list[Permission] = field(default_factory=list)
    last_used: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_by: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organizationId": self.organization_id,
            "name": self.name,
            "prefix": self.prefix,
            "permissions": [p.value for p in self.permissions],
            "lastUsed": self.last_used.isoformat() if self.last_used else None,
            "expiresAt": self.expires_at.isoformat() if self.expires_at else None,
            "createdAt": self.created_at.isoformat(),
        }


# ============================================
# System Roles
# ============================================

SYSTEM_ROLES = {
    "owner": Role(
        id="role_owner",
        name="Owner",
        description="Full access to everything",
        permissions=[Permission.ADMIN_FULL],
        is_system=True,
    ),
    "admin": Role(
        id="role_admin",
        name="Admin",
        description="Manage users, settings, and most resources",
        permissions=[
            Permission.ORG_VIEW,
            Permission.ORG_EDIT,
            Permission.ORG_BILLING,
            Permission.USERS_VIEW,
            Permission.USERS_INVITE,
            Permission.USERS_EDIT,
            Permission.ROLES_VIEW,
            Permission.ROLES_MANAGE,
            Permission.DATA_VIEW,
            Permission.DATA_CREATE,
            Permission.DATA_EDIT,
            Permission.DATA_DELETE,
            Permission.DATA_EXPORT,
            Permission.AGENTS_VIEW,
            Permission.AGENTS_CREATE,
            Permission.AGENTS_EDIT,
            Permission.AGENTS_DELETE,
            Permission.AGENTS_DEPLOY,
            Permission.WORKFLOWS_VIEW,
            Permission.WORKFLOWS_CREATE,
            Permission.WORKFLOWS_EDIT,
            Permission.WORKFLOWS_DELETE,
            Permission.WORKFLOWS_EXECUTE,
            Permission.INTEGRATIONS_VIEW,
            Permission.INTEGRATIONS_MANAGE,
            Permission.SETTINGS_VIEW,
            Permission.SETTINGS_EDIT,
        ],
        is_system=True,
    ),
    "manager": Role(
        id="role_manager",
        name="Manager",
        description="Manage data, agents, and workflows",
        permissions=[
            Permission.ORG_VIEW,
            Permission.USERS_VIEW,
            Permission.DATA_VIEW,
            Permission.DATA_CREATE,
            Permission.DATA_EDIT,
            Permission.DATA_DELETE,
            Permission.AGENTS_VIEW,
            Permission.AGENTS_CREATE,
            Permission.AGENTS_EDIT,
            Permission.WORKFLOWS_VIEW,
            Permission.WORKFLOWS_CREATE,
            Permission.WORKFLOWS_EDIT,
            Permission.WORKFLOWS_EXECUTE,
            Permission.INTEGRATIONS_VIEW,
            Permission.SETTINGS_VIEW,
        ],
        is_system=True,
    ),
    "member": Role(
        id="role_member",
        name="Member",
        description="Standard access",
        permissions=[
            Permission.ORG_VIEW,
            Permission.USERS_VIEW,
            Permission.DATA_VIEW,
            Permission.DATA_CREATE,
            Permission.DATA_EDIT,
            Permission.AGENTS_VIEW,
            Permission.WORKFLOWS_VIEW,
            Permission.WORKFLOWS_EXECUTE,
            Permission.INTEGRATIONS_VIEW,
            Permission.SETTINGS_VIEW,
        ],
        is_system=True,
    ),
    "viewer": Role(
        id="role_viewer",
        name="Viewer",
        description="Read-only access",
        permissions=[
            Permission.ORG_VIEW,
            Permission.USERS_VIEW,
            Permission.DATA_VIEW,
            Permission.AGENTS_VIEW,
            Permission.WORKFLOWS_VIEW,
            Permission.SETTINGS_VIEW,
        ],
        is_system=True,
    ),
}


# ============================================
# Control Plane Service
# ============================================


class ControlPlaneService(PersistentService):
    """Control Plane - Users, Roles, Permissions, Organizations.

    Supports both in-memory (default) and persistent storage via repositories.
    Set VOS3_STORAGE_BACKEND=convex for production persistence (default).
    """

    def __init__(
        self,
        user_repo: "UserRepository" = None,
        org_repo: "OrganizationRepository" = None,
        membership_repo: "MembershipRepository" = None,
        api_key_repo: "ApiKeyRepository" = None,
        audit_log_repo: "AuditLogRepository" = None,
        use_persistence: bool = None,
    ):
        """Initialize ControlPlaneService.

        Args:
            user_repo: Optional user repository (uses in-memory if None)
            org_repo: Optional organization repository
            membership_repo: Optional membership repository
            api_key_repo: Optional API key repository
            audit_log_repo: Optional audit log repository
            use_persistence: If True, auto-create repositories from environment.
                           If None, check VOS3_STORAGE_BACKEND env var.
        """
        self._using_persistence = self._bootstrap_persistence(use_persistence)

        # Initialize repositories (or use in-memory fallback)
        if self._using_persistence:
            self._user_repo = user_repo or get_user_repository()
            self._org_repo = org_repo or get_organization_repository()
            self._membership_repo = membership_repo or get_membership_repository()
            self._api_key_repo = api_key_repo or get_api_key_repository()
            self._audit_log_repo = audit_log_repo or get_audit_log_repository()
        else:
            self._user_repo = None
            self._org_repo = None
            self._membership_repo = None
            self._api_key_repo = None
            self._audit_log_repo = None

        # In-memory storage (always available as cache/fallback)
        self._users: dict[str, User] = {}
        self._organizations: dict[str, Organization] = {}
        self._memberships: dict[str, OrganizationMember] = {}
        self._roles: dict[str, Role] = {r.id: r for r in SYSTEM_ROLES.values()}
        self._invitations: dict[str, Invitation] = {}
        self._audit_logs: list[AuditLog] = []
        self._api_keys: dict[str, ApiKey] = {}

        # Only init sample data for in-memory mode
        if not self._using_persistence:
            self._init_sample_data()

    def _init_sample_data(self):
        """Initialize with sample data."""
        # Create demo user
        demo_user = User(
            id="dev_seed_user",
            email="demo@example.com",
            name="Demo User",
            status=UserStatus.ACTIVE,
            email_verified=True,
        )
        self._users[demo_user.id] = demo_user

        # Create demo org
        demo_org = Organization(
            id="org_demo",
            name="Demo Company",
            slug="demo-company",
            plan=OrganizationPlan.PROFESSIONAL,
            owner_id=demo_user.id,
            max_users=25,
            max_agents=10,
            max_workflows=50,
        )
        self._organizations[demo_org.id] = demo_org

        # Add membership
        membership = OrganizationMember(
            id="mem_demo",
            organization_id=demo_org.id,
            user_id=demo_user.id,
            role_id="role_owner",
            is_owner=True,
            joined_at=datetime.now(timezone.utc),
        )
        self._memberships[membership.id] = membership

    # ==========================================
    # Users
    # ==========================================

    def create_user(
        self,
        email: str,
        name: str,
        password: Optional[str] = None,
        sso_provider: Optional[str] = None,
        sso_id: Optional[str] = None,
    ) -> User:
        """Create a new user."""
        user = User(email=email, name=name, sso_provider=sso_provider, sso_id=sso_id)
        if password:
            # Use bcrypt for secure password hashing (salted, with work factor)
            user.password_hash = hash_password(password)

        # Always store in local cache
        self._users[user.id] = user

        # Persist to repository if available
        if self._user_repo:
            self._user_repo.create(user.to_dict())

        return user

    def get_user(self, user_id: str) -> Optional[User]:
        # Check local cache first
        if user_id in self._users:
            return self._users[user_id]

        # Try repository if available
        if self._user_repo:
            data = self._user_repo.get_by_id(user_id)
            if data:
                user = self._dict_to_user(data)
                self._users[user.id] = user  # Cache locally
                return user

        return None

    def get_user_by_email(self, email: str) -> Optional[User]:
        # Check local cache first
        for user in self._users.values():
            if user.email == email:
                return user

        # Try repository if available
        if self._user_repo:
            data = self._user_repo.get_by_email(email)
            if data:
                user = self._dict_to_user(data)
                self._users[user.id] = user  # Cache locally
                return user

        return None

    def update_user(self, user_id: str, updates: dict) -> Optional[User]:
        user = self.get_user(user_id)
        if not user:
            return None
        for key, value in updates.items():
            if hasattr(user, key):
                setattr(user, key, value)
        user.updated_at = datetime.now(timezone.utc)

        # Persist to repository if available
        if self._user_repo:
            self._user_repo.update(user_id, updates)

        return user

    def list_users(self, organization_id: Optional[str] = None) -> list[User]:
        # If using persistence and requesting by org, try repository
        if self._user_repo and organization_id:
            data_list = self._user_repo.list_by_org(organization_id)
            users = [self._dict_to_user(d) for d in data_list]
            # Update cache
            for user in users:
                self._users[user.id] = user
            return users

        if organization_id:
            member_user_ids = {
                m.user_id
                for m in self._memberships.values()
                if m.organization_id == organization_id
            }
            return [u for u in self._users.values() if u.id in member_user_ids]
        return list(self._users.values())

    def _dict_to_user(self, data: dict) -> User:
        """Convert dict from repository to User dataclass."""
        return User(
            id=data.get("id", f"user_{uuid4().hex[:12]}"),
            email=data.get("email", ""),
            name=data.get("name", ""),
            avatar_url=data.get("avatar_url"),
            phone=data.get("phone"),
            password_hash=data.get("password_hash"),
            mfa_enabled=data.get("mfa_enabled", False),
            mfa_secret=data.get("mfa_secret"),
            sso_provider=data.get("sso_provider"),
            sso_id=data.get("sso_id"),
            status=UserStatus(data.get("status", "active")),
            email_verified=data.get("email_verified", False),
            language=data.get("language", "en"),
            timezone=data.get("timezone", "UTC"),
        )

    # ==========================================
    # Organizations
    # ==========================================

    def create_organization(
        self, name: str, owner_id: str, plan: OrganizationPlan = OrganizationPlan.FREE
    ) -> Organization:
        """Create a new organization."""
        slug = name.lower().replace(" ", "-").replace("_", "-")
        org = Organization(name=name, slug=slug, plan=plan, owner_id=owner_id)

        # Set limits based on plan
        limits = {
            OrganizationPlan.FREE: (5, 3, 10),
            OrganizationPlan.STARTER: (15, 5, 25),
            OrganizationPlan.PROFESSIONAL: (50, 20, 100),
            OrganizationPlan.ENTERPRISE: (999, 999, 999),
        }
        org.max_users, org.max_agents, org.max_workflows = limits[plan]

        # Store locally
        self._organizations[org.id] = org

        # Persist to repository if available
        if self._org_repo:
            self._org_repo.create(org.to_dict())

        # Add owner as member
        membership = OrganizationMember(
            organization_id=org.id,
            user_id=owner_id,
            role_id="role_owner",
            is_owner=True,
            joined_at=datetime.now(timezone.utc),
        )
        self._memberships[membership.id] = membership

        # Persist membership if available
        if self._membership_repo:
            self._membership_repo.create(membership.to_dict())

        return org

    def get_organization(self, org_id: str) -> Optional[Organization]:
        # Check local cache first
        if org_id in self._organizations:
            return self._organizations[org_id]

        # Try repository if available
        if self._org_repo:
            data = self._org_repo.get_by_id(org_id)
            if data:
                org = self._dict_to_organization(data)
                self._organizations[org.id] = org
                return org

        return None

    def get_organization_by_slug(self, slug: str) -> Optional[Organization]:
        # Check local cache first
        for org in self._organizations.values():
            if org.slug == slug:
                return org

        # Try repository if available
        if self._org_repo:
            data = self._org_repo.get_by_slug(slug)
            if data:
                org = self._dict_to_organization(data)
                self._organizations[org.id] = org
                return org

        return None

    def update_organization(self, org_id: str, updates: dict) -> Optional[Organization]:
        org = self.get_organization(org_id)
        if not org:
            return None
        for key, value in updates.items():
            if hasattr(org, key):
                setattr(org, key, value)
        org.updated_at = datetime.now(timezone.utc)

        # Persist to repository if available
        if self._org_repo:
            self._org_repo.update(org_id, updates)

        return org

    def list_user_organizations(self, user_id: str) -> list[Organization]:
        org_ids = {
            m.organization_id
            for m in self._memberships.values()
            if m.user_id == user_id
        }
        return [o for o in self._organizations.values() if o.id in org_ids]

    def _dict_to_organization(self, data: dict) -> Organization:
        """Convert dict from repository to Organization dataclass."""
        return Organization(
            id=data.get("id", f"org_{uuid4().hex[:12]}"),
            name=data.get("name", ""),
            slug=data.get("slug", ""),
            logo_url=data.get("logo_url"),
            plan=OrganizationPlan(data.get("plan", "free")),
            stripe_customer_id=data.get("stripe_customer_id"),
            settings=data.get("settings", {}),
            max_users=data.get("max_users", 5),
            max_agents=data.get("max_agents", 3),
            max_workflows=data.get("max_workflows", 10),
            is_active=data.get("is_active", True),
            owner_id=data.get("owner_id"),
        )

    # ==========================================
    # Memberships
    # ==========================================

    def add_member(
        self, org_id: str, user_id: str, role_id: str, invited_by: Optional[str] = None
    ) -> OrganizationMember:
        """Add user to organization."""
        membership = OrganizationMember(
            organization_id=org_id,
            user_id=user_id,
            role_id=role_id,
            invited_by=invited_by,
            joined_at=datetime.now(timezone.utc),
        )
        self._memberships[membership.id] = membership
        return membership

    def remove_member(self, org_id: str, user_id: str) -> bool:
        """Remove user from organization."""
        for mem_id, mem in list(self._memberships.items()):
            if mem.organization_id == org_id and mem.user_id == user_id:
                del self._memberships[mem_id]
                return True
        return False

    def get_membership(self, org_id: str, user_id: str) -> Optional[OrganizationMember]:
        for mem in self._memberships.values():
            if mem.organization_id == org_id and mem.user_id == user_id:
                return mem
        return None

    def update_member_role(
        self, org_id: str, user_id: str, role_id: str
    ) -> Optional[OrganizationMember]:
        mem = self.get_membership(org_id, user_id)
        if mem:
            mem.role_id = role_id
        return mem

    def list_members(self, org_id: str) -> list[dict]:
        """List all members with user and role info."""
        members = []
        for mem in self._memberships.values():
            if mem.organization_id == org_id:
                user = self._users.get(mem.user_id)
                role = self._roles.get(mem.role_id)
                if user:
                    members.append(
                        {
                            **mem.to_dict(),
                            "user": user.to_dict(),
                            "role": role.to_dict() if role else None,
                        }
                    )
        return members

    # ==========================================
    # Roles
    # ==========================================

    def create_role(
        self,
        org_id: str,
        name: str,
        permissions: list[Permission],
        description: str = "",
    ) -> Role:
        """Create custom role for organization."""
        role = Role(
            name=name,
            description=description,
            permissions=permissions,
            organization_id=org_id,
        )
        self._roles[role.id] = role
        return role

    def get_role(self, role_id: str) -> Optional[Role]:
        return self._roles.get(role_id)

    def list_roles(
        self, org_id: Optional[str] = None, include_system: bool = True
    ) -> list[Role]:
        roles = []
        for role in self._roles.values():
            if role.is_system and include_system:
                roles.append(role)
            elif role.organization_id == org_id:
                roles.append(role)
        return roles

    def delete_role(self, role_id: str) -> bool:
        role = self._roles.get(role_id)
        if role and not role.is_system:
            del self._roles[role_id]
            return True
        return False

    # ==========================================
    # Permissions
    # ==========================================

    def check_permission(
        self, user_id: str, org_id: str, permission: Permission
    ) -> bool:
        """Check if user has permission in organization."""
        mem = self.get_membership(org_id, user_id)
        if not mem:
            return False
        role = self._roles.get(mem.role_id)
        if not role:
            return False
        return role.has_permission(permission)

    def get_user_permissions(self, user_id: str, org_id: str) -> list[Permission]:
        """Get all permissions for user in organization."""
        mem = self.get_membership(org_id, user_id)
        if not mem:
            return []
        role = self._roles.get(mem.role_id)
        if not role:
            return []
        if Permission.ADMIN_FULL in role.permissions:
            return list(Permission)
        return role.permissions

    # ==========================================
    # Invitations
    # ==========================================

    def create_invitation(
        self, org_id: str, email: str, role_id: str, invited_by: str
    ) -> Invitation:
        """Create invitation to join organization."""
        from datetime import timedelta

        inv = Invitation(
            organization_id=org_id,
            email=email,
            role_id=role_id,
            invited_by=invited_by,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        self._invitations[inv.id] = inv
        return inv

    def accept_invitation(
        self, token: str, user_id: str
    ) -> Optional[OrganizationMember]:
        """Accept invitation and join organization."""
        for inv in self._invitations.values():
            if inv.token == token and not inv.accepted_at:
                if datetime.now(timezone.utc) > inv.expires_at:
                    return None
                inv.accepted_at = datetime.now(timezone.utc)
                return self.add_member(
                    inv.organization_id, user_id, inv.role_id, inv.invited_by
                )
        return None

    def list_pending_invitations(self, org_id: str) -> list[Invitation]:
        return [
            i
            for i in self._invitations.values()
            if i.organization_id == org_id and not i.accepted_at
        ]

    # ==========================================
    # Audit Logs
    # ==========================================

    def log_action(
        self,
        org_id: str,
        user_id: str,
        action: AuditAction,
        resource_type: str,
        resource_id: str,
        details: dict = None,
        ip_address: str = None,
    ) -> AuditLog:
        """Log an action (immutable)."""
        log = AuditLog(
            organization_id=org_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
        )
        self._audit_logs.append(log)

        # Persist to repository if available
        if self._audit_log_repo:
            self._audit_log_repo.create(log.to_dict())

        return log

    def get_audit_logs(
        self,
        org_id: str,
        limit: int = 100,
        offset: int = 0,
        user_id: Optional[str] = None,
        action: Optional[AuditAction] = None,
        resource_type: Optional[str] = None,
    ) -> list[AuditLog]:
        """Get audit logs with filters."""
        logs = [log for log in self._audit_logs if log.organization_id == org_id]
        if user_id:
            logs = [log for log in logs if log.user_id == user_id]
        if action:
            logs = [log for log in logs if log.action == action]
        if resource_type:
            logs = [log for log in logs if log.resource_type == resource_type]
        logs.sort(key=lambda log: log.timestamp, reverse=True)
        return logs[offset : offset + limit]

    # ==========================================
    # API Keys
    # ==========================================

    def create_api_key(
        self,
        org_id: str,
        name: str,
        permissions: list[Permission],
        created_by: str,
        expires_days: int = None,
    ) -> tuple[ApiKey, str]:
        """Create API key. Returns (key_object, actual_key). Key is shown only once."""
        from datetime import timedelta

        actual_key = f"vk_{secrets.token_urlsafe(32)}"
        key = ApiKey(
            organization_id=org_id,
            name=name,
            key_hash=hashlib.sha256(actual_key.encode()).hexdigest(),
            prefix=actual_key[:12],
            permissions=permissions,
            created_by=created_by,
            expires_at=(
                datetime.now(timezone.utc) + timedelta(days=expires_days)
                if expires_days
                else None
            ),
        )
        self._api_keys[key.id] = key
        return key, actual_key

    def validate_api_key(self, key: str) -> Optional[ApiKey]:
        """Validate API key and return key object if valid."""
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        for api_key in self._api_keys.values():
            if api_key.key_hash == key_hash:
                if (
                    api_key.expires_at
                    and datetime.now(timezone.utc) > api_key.expires_at
                ):
                    return None
                api_key.last_used = datetime.now(timezone.utc)
                return api_key
        return None

    def list_api_keys(self, org_id: str) -> list[ApiKey]:
        return [k for k in self._api_keys.values() if k.organization_id == org_id]

    def revoke_api_key(self, key_id: str) -> bool:
        if key_id in self._api_keys:
            del self._api_keys[key_id]
            return True
        return False


# Singleton
_control_plane_service: Optional[ControlPlaneService] = None


def get_control_plane_service() -> ControlPlaneService:
    global _control_plane_service
    if _control_plane_service is None:
        _control_plane_service = ControlPlaneService()
    return _control_plane_service


__all__ = [
    "ControlPlaneService",
    "User",
    "Organization",
    "OrganizationMember",
    "Role",
    "Permission",
    "Invitation",
    "AuditLog",
    "ApiKey",
    "UserStatus",
    "OrganizationPlan",
    "AuditAction",
    "SYSTEM_ROLES",
    "get_control_plane_service",
]
