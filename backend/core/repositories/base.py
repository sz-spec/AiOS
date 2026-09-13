"""
Repository Base Interfaces
==========================
Abstract base classes defining the repository contract.
All implementations (memory, convex) must implement these interfaces.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any


class UserRepository(ABC):
    """Repository interface for User operations."""

    @abstractmethod
    def create(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new user. Returns the created user dict."""
        pass

    @abstractmethod
    def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID. Returns None if not found."""
        pass

    @abstractmethod
    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get user by email. Returns None if not found."""
        pass

    @abstractmethod
    def update(self, user_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update user. Returns updated user or None if not found."""
        pass

    @abstractmethod
    def delete(self, user_id: str) -> bool:
        """Delete user. Returns True if deleted, False if not found."""
        pass

    @abstractmethod
    def list_all(self) -> List[Dict[str, Any]]:
        """List all users."""
        pass

    @abstractmethod
    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        """List users belonging to an organization."""
        pass


class OrganizationRepository(ABC):
    """Repository interface for Organization operations."""

    @abstractmethod
    def create(self, org_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new organization."""
        pass

    @abstractmethod
    def get_by_id(self, org_id: str) -> Optional[Dict[str, Any]]:
        """Get organization by ID."""
        pass

    @abstractmethod
    def get_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get organization by slug."""
        pass

    @abstractmethod
    def update(self, org_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update organization."""
        pass

    @abstractmethod
    def delete(self, org_id: str) -> bool:
        """Delete organization."""
        pass

    @abstractmethod
    def list_all(self) -> List[Dict[str, Any]]:
        """List all organizations."""
        pass

    @abstractmethod
    def list_by_user(
        self, user_id: str, membership_repo: "MembershipRepository"
    ) -> List[Dict[str, Any]]:
        """List organizations a user belongs to."""
        pass


class MembershipRepository(ABC):
    """Repository interface for Organization Membership operations."""

    @abstractmethod
    def create(self, membership_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new membership."""
        pass

    @abstractmethod
    def get_by_id(self, membership_id: str) -> Optional[Dict[str, Any]]:
        """Get membership by ID."""
        pass

    @abstractmethod
    def get_by_org_and_user(
        self, org_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get membership by org and user."""
        pass

    @abstractmethod
    def update(
        self, membership_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update membership."""
        pass

    @abstractmethod
    def delete(self, membership_id: str) -> bool:
        """Delete membership."""
        pass

    @abstractmethod
    def delete_by_org_and_user(self, org_id: str, user_id: str) -> bool:
        """Delete membership by org and user."""
        pass

    @abstractmethod
    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        """List all memberships for an organization."""
        pass

    @abstractmethod
    def list_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """List all memberships for a user."""
        pass


class ApiKeyRepository(ABC):
    """Repository interface for API Key operations."""

    @abstractmethod
    def create(self, key_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new API key."""
        pass

    @abstractmethod
    def get_by_id(self, key_id: str) -> Optional[Dict[str, Any]]:
        """Get API key by ID."""
        pass

    @abstractmethod
    def get_by_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        """Get API key by hash (for validation)."""
        pass

    @abstractmethod
    def update(self, key_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update API key."""
        pass

    @abstractmethod
    def delete(self, key_id: str) -> bool:
        """Delete API key."""
        pass

    @abstractmethod
    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        """List all API keys for an organization."""
        pass


class AuditLogRepository(ABC):
    """Repository interface for Audit Log operations (append-only)."""

    @abstractmethod
    def create(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new audit log entry."""
        pass

    @abstractmethod
    def get_by_id(self, log_id: str) -> Optional[Dict[str, Any]]:
        """Get audit log by ID."""
        pass

    @abstractmethod
    def list_by_org(
        self,
        org_id: str,
        limit: int = 100,
        offset: int = 0,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List audit logs with filters."""
        pass


class EntityRepository(ABC):
    """Repository interface for V-Core Entity operations."""

    @abstractmethod
    def create(self, entity_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new entity definition. Returns the created entity dict."""
        pass

    @abstractmethod
    def get_by_id(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get entity by ID."""
        pass

    @abstractmethod
    def update(
        self, entity_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update entity. Returns updated entity or None."""
        pass

    @abstractmethod
    def delete(self, entity_id: str) -> bool:
        """Delete entity. Returns True if deleted."""
        pass

    @abstractmethod
    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        """List all entities for an organization."""
        pass


class RecordRepository(ABC):
    """Repository interface for V-Core Record operations."""

    @abstractmethod
    def create(self, record_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new record. Returns the created record dict."""
        pass

    @abstractmethod
    def get_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Get record by ID."""
        pass

    @abstractmethod
    def update(
        self, record_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update record. Returns updated record or None."""
        pass

    @abstractmethod
    def delete(self, record_id: str) -> bool:
        """Delete record. Returns True if deleted."""
        pass

    @abstractmethod
    def list_by_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        """List all records for an entity."""
        pass


class ApprovalRepository(ABC):
    """Repository interface for Approval operations."""

    @abstractmethod
    def create(self, approval_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new approval request."""
        pass

    @abstractmethod
    def update(
        self, approval_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update approval status."""
        pass

    @abstractmethod
    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        """List approvals for an organization."""
        pass


class AlertRepository(ABC):
    """Repository interface for Alert operations."""

    @abstractmethod
    def create(self, alert_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new alert."""
        pass

    @abstractmethod
    def update(
        self, alert_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update alert (resolve, mark read)."""
        pass

    @abstractmethod
    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        """List alerts for an organization."""
        pass


class WorkflowRepository(ABC):
    """Repository interface for Workflow operations."""

    @abstractmethod
    def create(self, workflow_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new workflow. Returns the created workflow dict."""
        pass

    @abstractmethod
    def get_by_id(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get workflow by ID."""
        pass

    @abstractmethod
    def update(
        self, workflow_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update workflow. Returns updated workflow or None."""
        pass

    @abstractmethod
    def delete(self, workflow_id: str) -> bool:
        """Delete workflow. Returns True if deleted."""
        pass

    @abstractmethod
    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        """List all workflows for an organization."""
        pass


class WorkflowExecutionRepository(ABC):
    """Repository interface for Workflow Execution operations."""

    @abstractmethod
    def create(self, execution_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new execution record. Returns the created execution dict."""
        pass

    @abstractmethod
    def update(
        self, execution_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update execution status/result."""
        pass

    @abstractmethod
    def list_by_workflow(
        self, workflow_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """List executions for a workflow."""
        pass


class ProjectMemoryRepository(ABC):
    """
    Repository interface for Project Memory operations.

    Stores ADRs, Project Specs, Session Summaries, and other
    persistent memory items for semantic recall across sessions.
    """

    @abstractmethod
    def create(self, memory_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new memory entry.

        Required fields:
        - memory_type: 'adr' | 'project_spec' | 'session_summary' | 'decision' | 'context'
        - content: The text content to store
        - project_id: Project identifier

        Optional fields:
        - title: Human-readable title
        - tags: List of tags for filtering
        - metadata: Additional structured data
        - embedding: Pre-computed embedding vector
        """
        pass

    @abstractmethod
    def get_by_id(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get memory entry by ID."""
        pass

    @abstractmethod
    def update(
        self, memory_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update memory entry."""
        pass

    @abstractmethod
    def delete(self, memory_id: str) -> bool:
        """Delete memory entry."""
        pass

    @abstractmethod
    def list_by_project(
        self,
        project_id: str,
        memory_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List memory entries for a project, optionally filtered by type."""
        pass

    @abstractmethod
    def list_by_type(
        self,
        memory_type: str,
        project_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List memory entries by type, optionally filtered by project."""
        pass

    @abstractmethod
    def search_by_tags(
        self,
        tags: List[str],
        project_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Search memory entries by tags."""
        pass

    @abstractmethod
    def get_recent(
        self,
        project_id: str,
        limit: int = 10,
        memory_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get most recent memory entries for a project."""
        pass
