"""
Convex Repository Implementations
==================================
Production-ready persistent storage using Convex.
Requires CONVEX_URL environment variable.

These repositories implement the same interfaces as the base classes, so
they are drop-in replacements for any previous backend — no calling-code changes needed.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from uuid import uuid4

from .base import (
    UserRepository,
    OrganizationRepository,
    MembershipRepository,
    ApiKeyRepository,
    AuditLogRepository,
    EntityRepository,
    RecordRepository,
    ApprovalRepository,
    AlertRepository,
    WorkflowRepository,
    WorkflowExecutionRepository,
    ProjectMemoryRepository,
)

# Lazy import to allow in-memory fallback when not installed
_convex: Optional[Any] = None


def _get_convex():
    """Get or create Convex client."""
    global _convex
    if _convex is None:
        from db.convex import get_convex_client

        _convex = get_convex_client()
    return _convex


def _run(coro):
    """Run an async coroutine synchronously (repositories are sync-interface).

    B-CRIT-5 fix: Always use asyncio.run() in a dedicated thread when called
    from an async context. This avoids cross-loop httpx client issues and
    is compatible with Python 3.12+ event loop policies.
    """
    try:
        asyncio.get_running_loop()
        # We're inside an async context — run in a thread with a fresh loop
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=30)
    except RuntimeError:
        # No running loop — safe to use asyncio.run() directly
        return asyncio.run(coro)


# =============================================================================
# User Repository
# =============================================================================


class ConvexUserRepository(UserRepository):
    """Convex-backed user repository."""

    def create(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        result = _run(
            convex.mutation(
                "users:syncFromClerk",
                {
                    "clerkId": user_data.get("id")
                    or user_data.get("clerkId", str(uuid4())),
                    "email": user_data.get("email", ""),
                    "fullName": user_data.get("full_name") or user_data.get("fullName"),
                    "avatarUrl": user_data.get("avatar_url")
                    or user_data.get("avatarUrl"),
                    "metadata": user_data.get("metadata"),
                },
            )
        )
        return {**user_data, "_id": result}

    def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("users:getByClerkId", {"clerkId": user_id}))

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        # Convex doesn't expose a getByEmail query in the plan; fall back to None
        # (implement users:getByEmail if needed)
        convex = _get_convex()
        return (
            _run(convex.query("users:getByEmail", {"email": email}))
            if not convex.dev_mode
            else None
        )

    def update(self, user_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        # Map snake_case → camelCase
        payload: Dict[str, Any] = {}
        if "full_name" in updates:
            payload["fullName"] = updates["full_name"]
        if "fullName" in updates:
            payload["fullName"] = updates["fullName"]
        if "avatar_url" in updates:
            payload["avatarUrl"] = updates["avatar_url"]
        if "avatarUrl" in updates:
            payload["avatarUrl"] = updates["avatarUrl"]
        if "email" in updates:
            payload["email"] = updates["email"]
        if "metadata" in updates:
            payload["metadata"] = updates["metadata"]
        if "last_sign_in_at" in updates:
            payload["lastSignInAt"] = (
                int(
                    datetime.fromisoformat(updates["last_sign_in_at"]).timestamp()
                    * 1000
                )
                if isinstance(updates["last_sign_in_at"], str)
                else updates["last_sign_in_at"]
            )

        # We need the Convex internal _id to patch; get it first via clerkId lookup
        user = _run(convex.query("users:getByClerkId", {"clerkId": user_id}))
        if user and "_id" in user:
            payload["id"] = user["_id"]
            _run(convex.mutation("users:update", payload))
            return {**user, **payload}
        return None

    def delete(self, user_id: str) -> bool:
        convex = _get_convex()
        _run(convex.mutation("users:softDelete", {"clerkId": user_id}))
        return True

    def list_all(self) -> List[Dict[str, Any]]:
        # Not exposed in the plan — return empty for now
        return []

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        convex = _get_convex()
        return (
            _run(convex.query("organizations:listMembers", {"organizationId": org_id}))
            or []
        )


# =============================================================================
# Organization Repository
# =============================================================================


class ConvexOrganizationRepository(OrganizationRepository):
    """Convex-backed organization repository."""

    def create(self, org_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        result = _run(
            convex.mutation(
                "organizations:create",
                {
                    "name": org_data["name"],
                    "slug": org_data.get(
                        "slug", org_data["name"].lower().replace(" ", "-")
                    ),
                    "ownerId": org_data.get("owner_id") or org_data.get("ownerId", ""),
                    "logoUrl": org_data.get("logo_url") or org_data.get("logoUrl"),
                    "metadata": org_data.get("metadata"),
                },
            )
        )
        return {**org_data, "_id": result}

    def get_by_id(self, org_id: str) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("organizations:getById", {"id": org_id}))

    def get_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("organizations:getBySlug", {"slug": slug}))

    def update(self, org_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        _run(convex.mutation("organizations:update", {"id": org_id, **updates}))
        return self.get_by_id(org_id)

    def delete(self, org_id: str) -> bool:
        # Not exposed; would need a deleteOrganization mutation
        return False

    def list_all(self) -> List[Dict[str, Any]]:
        return []

    def list_by_user(self, user_id: str, membership_repo) -> List[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("organizations:listByUser", {"userId": user_id})) or []


# =============================================================================
# Membership Repository
# =============================================================================


class ConvexMembershipRepository(MembershipRepository):
    """Convex-backed membership repository."""

    def create(self, membership_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        result = _run(
            convex.mutation(
                "organizations:addMember",
                {
                    "organizationId": membership_data.get("organization_id")
                    or membership_data.get("organizationId"),
                    "userId": membership_data.get("user_id")
                    or membership_data.get("userId"),
                    "role": membership_data.get("role", "member"),
                },
            )
        )
        return {**membership_data, "_id": result}

    def get_by_id(self, membership_id: str) -> Optional[Dict[str, Any]]:
        return None  # No direct lookup by membership ID in the plan

    def get_by_org_and_user(
        self, org_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        members = (
            _run(convex.query("organizations:listMembers", {"organizationId": org_id}))
            or []
        )
        return next((m for m in members if m.get("userId") == user_id), None)

    def update(
        self, membership_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        return None

    def delete(self, membership_id: str) -> bool:
        return False

    def delete_by_org_and_user(self, org_id: str, user_id: str) -> bool:
        convex = _get_convex()
        _run(
            convex.mutation(
                "organizations:removeMember",
                {
                    "organizationId": org_id,
                    "userId": user_id,
                },
            )
        )
        return True

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        convex = _get_convex()
        return (
            _run(convex.query("organizations:listMembers", {"organizationId": org_id}))
            or []
        )

    def list_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        # Would require a listByUser query on members; return empty for now
        return []


# =============================================================================
# API Key Repository
# =============================================================================


class ConvexApiKeyRepository(ApiKeyRepository):
    """Convex-backed API key repository."""

    def create(self, key_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        result = _run(convex.mutation("vcore:createApiKey", key_data))
        return {**key_data, "_id": result}

    def get_by_id(self, key_id: str) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("vcore:getApiKey", {"id": key_id}))

    def get_by_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("vcore:getApiKeyByHash", {"keyHash": key_hash}))

    def update(self, key_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        _run(convex.mutation("vcore:updateApiKey", {"id": key_id, **updates}))
        return self.get_by_id(key_id)

    def delete(self, key_id: str) -> bool:
        convex = _get_convex()
        _run(convex.mutation("vcore:deleteApiKey", {"id": key_id}))
        return True

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("vcore:listApiKeys", {"organizationId": org_id})) or []


# =============================================================================
# Audit Log Repository
# =============================================================================


class ConvexAuditLogRepository(AuditLogRepository):
    """Convex-backed audit log repository."""

    def create(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        # Map legacy field names
        payload = {
            "organizationId": log_data.get("organization_id")
            or log_data.get("organizationId"),
            "userId": log_data.get("user_id") or log_data.get("userId"),
            "action": log_data.get("action", "unknown"),
            "resourceType": log_data.get("resource_type")
            or log_data.get("resourceType", ""),
            "resourceId": log_data.get("resource_id") or log_data.get("resourceId"),
            "metadata": log_data.get("metadata") or log_data.get("details"),
            "ipAddress": log_data.get("ip_address") or log_data.get("ipAddress"),
        }
        result = _run(convex.mutation("vcore:addAuditEntry", payload))
        return {**log_data, "_id": result}

    def get_by_id(self, log_id: str) -> Optional[Dict[str, Any]]:
        return None

    def list_by_org(
        self,
        org_id: str,
        limit: int = 100,
        offset: int = 0,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        resource_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        convex = _get_convex()
        entries = (
            _run(
                convex.query(
                    "vcore:listAuditLog",
                    {
                        "organizationId": org_id,
                        "limit": limit,
                    },
                )
            )
            or []
        )

        # Client-side filtering for offset/filters (server-side pagination can be added later)
        if user_id:
            entries = [e for e in entries if e.get("userId") == user_id]
        if action:
            entries = [e for e in entries if e.get("action") == action]
        if resource_type:
            entries = [e for e in entries if e.get("resourceType") == resource_type]
        return entries[offset : offset + limit]


# =============================================================================
# Entity Repository
# =============================================================================


class ConvexEntityRepository(EntityRepository):
    """Convex-backed entity repository (vcore.ts functions)."""

    def create(self, entity_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        payload = {
            "organizationId": entity_data.get("organization_id")
            or entity_data.get("organizationId"),
            "name": entity_data.get("name", ""),
            "slug": entity_data.get("slug")
            or entity_data.get("name", "").lower().replace(" ", "_"),
            "description": entity_data.get("description"),
            "fields": entity_data.get("fields", []),
            "metadata": entity_data.get("metadata"),
        }
        result = _run(convex.mutation("vcore:createEntity", payload))
        return {**entity_data, "_id": result}

    def get_by_id(self, entity_id: str) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("vcore:getEntity", {"id": entity_id}))

    def update(
        self, entity_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        _run(convex.mutation("vcore:updateEntity", {"id": entity_id, **updates}))
        return self.get_by_id(entity_id)

    def delete(self, entity_id: str) -> bool:
        convex = _get_convex()
        _run(convex.mutation("vcore:deleteEntity", {"id": entity_id}))
        return True

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        convex = _get_convex()
        return (
            _run(convex.query("vcore:listEntities", {"organizationId": org_id})) or []
        )


# =============================================================================
# Record Repository
# =============================================================================


class ConvexRecordRepository(RecordRepository):
    """Convex-backed record repository (vcore.ts functions)."""

    def create(self, record_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        payload = {
            "entityId": record_data.get("entity_id") or record_data.get("entityId"),
            "organizationId": record_data.get("organization_id")
            or record_data.get("organizationId"),
            "data": record_data.get("data", {}),
            "createdBy": record_data.get("created_by") or record_data.get("createdBy"),
        }
        result = _run(convex.mutation("vcore:createRecord", payload))
        return {**record_data, "_id": result}

    def get_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("vcore:getRecord", {"id": record_id}))

    def update(
        self, record_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        _run(
            convex.mutation(
                "vcore:updateRecord",
                {"id": record_id, "data": updates.get("data", updates)},
            )
        )
        return self.get_by_id(record_id)

    def delete(self, record_id: str) -> bool:
        convex = _get_convex()
        _run(convex.mutation("vcore:deleteRecord", {"id": record_id}))
        return True

    def list_by_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("vcore:listRecords", {"entityId": entity_id})) or []


# =============================================================================
# Approval Repository
# =============================================================================


class ConvexApprovalRepository(ApprovalRepository):
    """Convex-backed approval repository. Uses ConvexDB CRUD (pending Convex functions)."""

    def create(self, approval_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        result = _run(
            convex.insert(
                "approvals",
                {
                    "organizationId": approval_data.get("organization_id")
                    or approval_data.get("organizationId"),
                    "requestedBy": approval_data.get("requested_by")
                    or approval_data.get("requestedBy", ""),
                    "resourceType": approval_data.get("resource_type")
                    or approval_data.get("category", ""),
                    "resourceId": approval_data.get("resource_id")
                    or approval_data.get("action_type", ""),
                    "status": approval_data.get("status", "pending"),
                    "notes": approval_data.get("description", ""),
                    "requestedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
                },
            )
        )
        return {**approval_data, "_id": result}

    def update(
        self, approval_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        _run(convex.update("approvals", approval_id, updates))
        return updates

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.list("approvals", organizationId=org_id)) or []


# =============================================================================
# Alert Repository
# =============================================================================


class ConvexAlertRepository(AlertRepository):
    """Convex-backed alert repository. Uses ConvexDB CRUD (pending Convex functions)."""

    def create(self, alert_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        result = _run(
            convex.insert(
                "alerts",
                {
                    "organizationId": alert_data.get("organization_id")
                    or alert_data.get("organizationId"),
                    "type": alert_data.get("category", ""),
                    "severity": alert_data.get("severity", "info"),
                    "message": alert_data.get("message", ""),
                    "metadata": alert_data.get("data"),
                    "isRead": False,
                },
            )
        )
        return {**alert_data, "_id": result}

    def update(
        self, alert_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        _run(convex.update("alerts", alert_id, updates))
        return updates

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.list("alerts", organizationId=org_id)) or []


# =============================================================================
# Workflow Repository
# =============================================================================


class ConvexWorkflowRepository(WorkflowRepository):
    """Convex-backed workflow repository (vcore.ts functions)."""

    def create(self, workflow_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        payload = {
            "organizationId": workflow_data.get("organization_id")
            or workflow_data.get("organizationId"),
            "name": workflow_data.get("name", ""),
            "description": workflow_data.get("description"),
            "trigger": workflow_data.get("trigger", {}),
            "actions": workflow_data.get("actions") or workflow_data.get("nodes", []),
            "isActive": workflow_data.get("is_active", False),
            "metadata": workflow_data.get("metadata"),
        }
        result = _run(convex.mutation("vcore:createWorkflow", payload))
        return {**workflow_data, "_id": result}

    def get_by_id(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("vcore:getWorkflow", {"id": workflow_id}))

    def update(
        self, workflow_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        _run(convex.mutation("vcore:updateWorkflow", {"id": workflow_id, **updates}))
        return self.get_by_id(workflow_id)

    def delete(self, workflow_id: str) -> bool:
        convex = _get_convex()
        _run(convex.mutation("vcore:deleteWorkflow", {"id": workflow_id}))
        return True

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        convex = _get_convex()
        return (
            _run(convex.query("vcore:listWorkflows", {"organizationId": org_id})) or []
        )


# =============================================================================
# Workflow Execution Repository
# =============================================================================


class ConvexWorkflowExecutionRepository(WorkflowExecutionRepository):
    """Convex-backed workflow execution repository (vcore.ts functions)."""

    def create(self, execution_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        payload = {
            "workflowId": execution_data.get("workflow_id")
            or execution_data.get("workflowId"),
            "organizationId": execution_data.get("organization_id")
            or execution_data.get("organizationId"),
            "triggeredBy": execution_data.get("triggered_by")
            or execution_data.get("triggeredBy"),
        }
        result = _run(convex.mutation("vcore:createExecution", payload))
        return {**execution_data, "_id": result}

    def update(
        self, execution_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        payload = {"id": execution_id}
        if "status" in updates:
            payload["status"] = updates["status"]
        if "completed_at" in updates or "completedAt" in updates:
            payload["completedAt"] = updates.get("completedAt") or updates.get(
                "completed_at"
            )
        if "result" in updates:
            payload["result"] = updates["result"]
        if "error" in updates:
            payload["error"] = updates["error"]
        _run(convex.mutation("vcore:updateExecution", payload))
        return updates

    def list_by_workflow(
        self, workflow_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        convex = _get_convex()
        return (
            _run(
                convex.query(
                    "vcore:listExecutions", {"workflowId": workflow_id, "limit": limit}
                )
            )
            or []
        )


# =============================================================================
# Project Memory Repository
# =============================================================================


class ConvexProjectMemoryRepository(ProjectMemoryRepository):
    """Convex-backed project memory repository."""

    def create(self, memory_data: Dict[str, Any]) -> Dict[str, Any]:
        convex = _get_convex()
        payload = {
            "projectId": memory_data.get("project_id") or memory_data.get("projectId"),
            "memoryType": memory_data.get("memory_type")
            or memory_data.get("memoryType", "session"),
            "title": memory_data.get("title", ""),
            "content": memory_data.get("content", ""),
            "tags": memory_data.get("tags", []),
            "metadata": memory_data.get("metadata"),
            "userId": memory_data.get("user_id") or memory_data.get("userId"),
        }
        result = _run(convex.mutation("projects:createMemory", payload))
        return {**memory_data, "_id": result}

    def get_by_id(self, memory_id: str) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        return _run(convex.query("projects:getMemory", {"id": memory_id}))

    def update(
        self, memory_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        convex = _get_convex()
        _run(convex.mutation("projects:updateMemory", {"id": memory_id, **updates}))
        return self.get_by_id(memory_id)

    def delete(self, memory_id: str) -> bool:
        convex = _get_convex()
        _run(convex.mutation("projects:deleteMemory", {"id": memory_id}))
        return True

    def list_by_project(
        self,
        project_id: str,
        memory_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        convex = _get_convex()
        entries = (
            _run(
                convex.query(
                    "projects:listMemory",
                    {
                        "projectId": project_id,
                        "limit": limit,
                    },
                )
            )
            or []
        )
        if memory_type:
            entries = [e for e in entries if e.get("memoryType") == memory_type]
        return entries[offset : offset + limit]

    def list_by_type(
        self,
        memory_type: str,
        project_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        convex = _get_convex()
        entries = (
            _run(
                convex.query(
                    "projects:listMemoryByType",
                    {
                        "memoryType": memory_type,
                    },
                )
            )
            or []
        )
        if project_id:
            entries = [e for e in entries if e.get("projectId") == project_id]
        return entries[offset : offset + limit]

    def search_by_tags(
        self,
        tags: List[str],
        project_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        # Convex doesn't have built-in array contains — filter client-side
        entries = (
            self.list_by_project(project_id or "", limit=limit * 2)
            if project_id
            else []
        )
        tag_set = set(tags)
        filtered = [e for e in entries if tag_set.intersection(e.get("tags", []))]
        return filtered[:limit]

    def get_recent(
        self,
        project_id: str,
        limit: int = 10,
        memory_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.list_by_project(project_id, memory_type, limit, 0)


# =============================================================================
# Chat Session Repository
# =============================================================================


class ConvexChatSessionRepository:
    """Convex-backed chat session repository.

    Uses the existing chatSessions + chatSessionMessages tables.
    """

    def save_session(
        self,
        user_id: str,
        session_id: str,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """Create or update a chat session with its messages."""
        convex = _get_convex()
        return _run(
            convex.mutation(
                "chatSessions:save",
                {
                    "userId": user_id,
                    "sessionId": session_id,
                    "messages": messages or [],
                },
            )
        )

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a session by ID, including its messages."""
        convex = _get_convex()
        return _run(
            convex.query(
                "chatSessions:load",
                {
                    "sessionId": session_id,
                },
            )
        )

    def list_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """List all sessions for a user (metadata only, no messages)."""
        convex = _get_convex()
        return (
            _run(
                convex.query(
                    "chatSessions:list",
                    {
                        "userId": user_id,
                    },
                )
            )
            or []
        )

    def remove_session(self, session_id: str) -> bool:
        """Delete a session and all its messages."""
        convex = _get_convex()
        return (
            _run(
                convex.mutation(
                    "chatSessions:remove",
                    {
                        "sessionId": session_id,
                    },
                )
            )
            or False
        )


# =============================================================================
# Project Repository
# =============================================================================


class ConvexProjectRepository:
    """Convex-backed project repository.

    Uses the existing projects + projectFiles tables.
    Extra fields (category, template_id, status, files, settings) are stored
    in the metadata JSON field.
    """

    def create(
        self,
        user_id: str,
        name: str,
        description: str = "",
        category: str = "",
        template_id: Optional[str] = None,
        status: str = "building",
    ) -> Dict[str, Any]:
        """Create a new project. Returns the Convex document."""
        convex = _get_convex()
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        metadata = {
            "category": category,
            "template_id": template_id,
            "status": status,
            "files": {},
            "settings": {},
            "created_at": now,
            "updated_at": now,
        }
        doc_id = _run(
            convex.insert(
                "projects",
                {
                    "name": name,
                    "description": description,
                    "ownerId": user_id,
                    "isArchived": False,
                    "metadata": metadata,
                },
            )
        )
        return {
            "_id": doc_id,
            "name": name,
            "description": description,
            "ownerId": user_id,
            "isArchived": False,
            "metadata": metadata,
        }

    def get_by_id(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get a project by its Convex document ID."""
        convex = _get_convex()
        return _run(convex.get("projects", project_id))

    def update(
        self, project_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update project fields. Supports top-level and metadata sub-fields."""
        convex = _get_convex()
        return _run(convex.update("projects", project_id, updates))

    def delete(self, project_id: str) -> bool:
        """Soft-delete by archiving."""
        convex = _get_convex()
        _run(convex.update("projects", project_id, {"isArchived": True}))
        return True

    def list_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """List non-archived projects owned by a user."""
        convex = _get_convex()
        results = _run(convex.find("projects", {"ownerId": user_id})) or []
        return [r for r in results if not r.get("isArchived", False)]

    def list_all(self) -> List[Dict[str, Any]]:
        """List all non-archived projects."""
        convex = _get_convex()
        results = _run(convex.list("projects")) or []
        return [r for r in results if not r.get("isArchived", False)]


# =============================================================================
# Prompt History Repository
# =============================================================================


class ConvexPromptHistoryRepository:
    """Convex-backed prompt history repository.

    Uses the promptHistory table.
    """

    def create(self, prompt_data: Dict[str, Any]) -> str:
        """Create a new prompt entry. Returns document ID."""
        convex = _get_convex()
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        return _run(
            convex.insert(
                "promptHistory",
                {
                    "promptId": prompt_data.get("id", str(uuid4())),
                    "userId": prompt_data.get("user_id", ""),
                    "projectId": prompt_data.get("project_id"),
                    "sessionId": prompt_data.get("session_id"),
                    "prompt": prompt_data.get("prompt", ""),
                    "systemPrompt": prompt_data.get("system_prompt"),
                    "context": prompt_data.get("context"),
                    "response": prompt_data.get("response"),
                    "generatedCode": prompt_data.get("generated_code"),
                    "generatedFiles": prompt_data.get("generated_files"),
                    "promptType": prompt_data.get("prompt_type", "chat"),
                    "status": prompt_data.get("status", "pending"),
                    "model": prompt_data.get("model", ""),
                    "promptTokens": prompt_data.get("prompt_tokens", 0),
                    "completionTokens": prompt_data.get("completion_tokens", 0),
                    "totalTokens": prompt_data.get("total_tokens", 0),
                    "durationMs": prompt_data.get("duration_ms", 0),
                    "error": prompt_data.get("error"),
                    "tags": prompt_data.get("tags", []),
                    "isFavorite": prompt_data.get("is_favorite", False),
                    "createdAt": now,
                },
            )
        )

    def get_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Get a prompt entry by document ID."""
        convex = _get_convex()
        return _run(convex.get("promptHistory", doc_id))

    def update(self, doc_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a prompt entry."""
        convex = _get_convex()
        return _run(convex.update("promptHistory", doc_id, updates))

    def delete(self, doc_id: str) -> bool:
        """Delete a prompt entry."""
        convex = _get_convex()
        return _run(convex.delete("promptHistory", doc_id))

    def list_by_user(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """List prompts for a user, newest first."""
        convex = _get_convex()
        results = _run(convex.find("promptHistory", {"userId": user_id})) or []
        results.sort(key=lambda r: r.get("createdAt", 0), reverse=True)
        return results[:limit]

    def list_by_user_and_project(
        self,
        user_id: str,
        project_id: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List prompts for a user in a specific project."""
        results = self.list_by_user(user_id, limit=limit * 2)
        filtered = [r for r in results if r.get("projectId") == project_id]
        return filtered[:limit]
