"""
In-Memory Repository Implementations
====================================
Used for development, testing, and backward compatibility.
Data is stored in dictionaries and lost on restart.
"""

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


class InMemoryUserRepository(UserRepository):
    """In-memory user repository. Data lost on restart."""

    def __init__(self):
        self._users: Dict[str, Dict[str, Any]] = {}

    def create(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in user_data:
            user_data["id"] = f"user_{uuid4().hex[:12]}"
        if "created_at" not in user_data:
            user_data["created_at"] = datetime.now(timezone.utc).isoformat()
        if "updated_at" not in user_data:
            user_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._users[user_data["id"]] = user_data
        return user_data

    def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self._users.get(user_id)

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        for user in self._users.values():
            if user.get("email") == email:
                return user
        return None

    def update(self, user_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if user_id not in self._users:
            return None
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._users[user_id].update(updates)
        return self._users[user_id]

    def delete(self, user_id: str) -> bool:
        if user_id in self._users:
            del self._users[user_id]
            return True
        return False

    def list_all(self) -> List[Dict[str, Any]]:
        return list(self._users.values())

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        # Note: This requires membership info, handled at service level
        return list(self._users.values())


class InMemoryOrganizationRepository(OrganizationRepository):
    """In-memory organization repository."""

    def __init__(self):
        self._orgs: Dict[str, Dict[str, Any]] = {}

    def create(self, org_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in org_data:
            org_data["id"] = f"org_{uuid4().hex[:12]}"
        if "created_at" not in org_data:
            org_data["created_at"] = datetime.now(timezone.utc).isoformat()
        if "updated_at" not in org_data:
            org_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._orgs[org_data["id"]] = org_data
        return org_data

    def get_by_id(self, org_id: str) -> Optional[Dict[str, Any]]:
        return self._orgs.get(org_id)

    def get_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        for org in self._orgs.values():
            if org.get("slug") == slug:
                return org
        return None

    def update(self, org_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if org_id not in self._orgs:
            return None
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._orgs[org_id].update(updates)
        return self._orgs[org_id]

    def delete(self, org_id: str) -> bool:
        if org_id in self._orgs:
            del self._orgs[org_id]
            return True
        return False

    def list_all(self) -> List[Dict[str, Any]]:
        return list(self._orgs.values())

    def list_by_user(
        self, user_id: str, membership_repo: MembershipRepository
    ) -> List[Dict[str, Any]]:
        memberships = membership_repo.list_by_user(user_id)
        org_ids = {m["organization_id"] for m in memberships}
        return [org for org in self._orgs.values() if org["id"] in org_ids]


class InMemoryMembershipRepository(MembershipRepository):
    """In-memory membership repository."""

    def __init__(self):
        self._memberships: Dict[str, Dict[str, Any]] = {}

    def create(self, membership_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in membership_data:
            membership_data["id"] = f"mem_{uuid4().hex[:12]}"
        if "joined_at" not in membership_data:
            membership_data["joined_at"] = datetime.now(timezone.utc).isoformat()
        self._memberships[membership_data["id"]] = membership_data
        return membership_data

    def get_by_id(self, membership_id: str) -> Optional[Dict[str, Any]]:
        return self._memberships.get(membership_id)

    def get_by_org_and_user(
        self, org_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        for mem in self._memberships.values():
            if mem.get("organization_id") == org_id and mem.get("user_id") == user_id:
                return mem
        return None

    def update(
        self, membership_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if membership_id not in self._memberships:
            return None
        self._memberships[membership_id].update(updates)
        return self._memberships[membership_id]

    def delete(self, membership_id: str) -> bool:
        if membership_id in self._memberships:
            del self._memberships[membership_id]
            return True
        return False

    def delete_by_org_and_user(self, org_id: str, user_id: str) -> bool:
        for mem_id, mem in list(self._memberships.items()):
            if mem.get("organization_id") == org_id and mem.get("user_id") == user_id:
                del self._memberships[mem_id]
                return True
        return False

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        return [
            m for m in self._memberships.values() if m.get("organization_id") == org_id
        ]

    def list_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        return [m for m in self._memberships.values() if m.get("user_id") == user_id]


class InMemoryApiKeyRepository(ApiKeyRepository):
    """In-memory API key repository."""

    def __init__(self):
        self._keys: Dict[str, Dict[str, Any]] = {}

    def create(self, key_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in key_data:
            key_data["id"] = f"key_{uuid4().hex[:12]}"
        if "created_at" not in key_data:
            key_data["created_at"] = datetime.now(timezone.utc).isoformat()
        self._keys[key_data["id"]] = key_data
        return key_data

    def get_by_id(self, key_id: str) -> Optional[Dict[str, Any]]:
        return self._keys.get(key_id)

    def get_by_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        for key in self._keys.values():
            if key.get("key_hash") == key_hash:
                return key
        return None

    def update(self, key_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if key_id not in self._keys:
            return None
        self._keys[key_id].update(updates)
        return self._keys[key_id]

    def delete(self, key_id: str) -> bool:
        if key_id in self._keys:
            del self._keys[key_id]
            return True
        return False

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        return [k for k in self._keys.values() if k.get("organization_id") == org_id]


class InMemoryAuditLogRepository(AuditLogRepository):
    """In-memory audit log repository."""

    def __init__(self):
        self._logs: List[Dict[str, Any]] = []

    def create(self, log_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in log_data:
            log_data["id"] = f"audit_{uuid4().hex[:12]}"
        if "timestamp" not in log_data:
            log_data["timestamp"] = datetime.now(timezone.utc).isoformat()
        self._logs.append(log_data)
        return log_data

    def get_by_id(self, log_id: str) -> Optional[Dict[str, Any]]:
        for log in self._logs:
            if log.get("id") == log_id:
                return log
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
        logs = [log for log in self._logs if log.get("organization_id") == org_id]
        if user_id:
            logs = [log for log in logs if log.get("user_id") == user_id]
        if action:
            logs = [log for log in logs if log.get("action") == action]
        if resource_type:
            logs = [log for log in logs if log.get("resource_type") == resource_type]
        # Sort by timestamp descending
        logs.sort(key=lambda log: log.get("timestamp", ""), reverse=True)
        return logs[offset : offset + limit]


class InMemoryEntityRepository(EntityRepository):
    """In-memory entity repository. Data lost on restart."""

    def __init__(self):
        self._entities: Dict[str, Dict[str, Any]] = {}

    def create(self, entity_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in entity_data:
            entity_data["id"] = f"ent_{uuid4().hex[:12]}"
        if "created_at" not in entity_data:
            entity_data["created_at"] = datetime.now(timezone.utc).isoformat()
        if "updated_at" not in entity_data:
            entity_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._entities[entity_data["id"]] = entity_data
        return entity_data

    def get_by_id(self, entity_id: str) -> Optional[Dict[str, Any]]:
        return self._entities.get(entity_id)

    def update(
        self, entity_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if entity_id not in self._entities:
            return None
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._entities[entity_id].update(updates)
        return self._entities[entity_id]

    def delete(self, entity_id: str) -> bool:
        if entity_id in self._entities:
            del self._entities[entity_id]
            return True
        return False

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        return [
            e for e in self._entities.values() if e.get("organization_id") == org_id
        ]


class InMemoryRecordRepository(RecordRepository):
    """In-memory record repository. Data lost on restart."""

    def __init__(self):
        self._records: Dict[str, Dict[str, Any]] = {}

    def create(self, record_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in record_data:
            record_data["id"] = f"rec_{uuid4().hex[:12]}"
        if "created_at" not in record_data:
            record_data["created_at"] = datetime.now(timezone.utc).isoformat()
        if "updated_at" not in record_data:
            record_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._records[record_data["id"]] = record_data
        return record_data

    def get_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        return self._records.get(record_id)

    def update(
        self, record_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if record_id not in self._records:
            return None
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._records[record_id].update(updates)
        return self._records[record_id]

    def delete(self, record_id: str) -> bool:
        if record_id in self._records:
            del self._records[record_id]
            return True
        return False

    def list_by_entity(self, entity_id: str) -> List[Dict[str, Any]]:
        return [r for r in self._records.values() if r.get("entity_id") == entity_id]


class InMemoryApprovalRepository(ApprovalRepository):
    """In-memory approval repository. Data lost on restart."""

    def __init__(self):
        self._approvals: Dict[str, Dict[str, Any]] = {}

    def create(self, approval_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in approval_data:
            approval_data["id"] = f"apr_{uuid4().hex[:12]}"
        if "created_at" not in approval_data:
            approval_data["created_at"] = datetime.now(timezone.utc).isoformat()
        self._approvals[approval_data["id"]] = approval_data
        return approval_data

    def update(
        self, approval_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if approval_id not in self._approvals:
            return None
        self._approvals[approval_id].update(updates)
        return self._approvals[approval_id]

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        return [
            a for a in self._approvals.values() if a.get("organization_id") == org_id
        ]


class InMemoryAlertRepository(AlertRepository):
    """In-memory alert repository. Data lost on restart."""

    def __init__(self):
        self._alerts: List[Dict[str, Any]] = []

    def create(self, alert_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in alert_data:
            alert_data["id"] = f"alert_{uuid4().hex[:12]}"
        if "created_at" not in alert_data:
            alert_data["created_at"] = datetime.now(timezone.utc).isoformat()
        self._alerts.append(alert_data)
        return alert_data

    def update(
        self, alert_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        for alert in self._alerts:
            if alert.get("id") == alert_id:
                alert.update(updates)
                return alert
        return None

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        return [a for a in self._alerts if a.get("organization_id") == org_id]


class InMemoryWorkflowRepository(WorkflowRepository):
    """In-memory workflow repository. Data lost on restart."""

    def __init__(self):
        self._workflows: Dict[str, Dict[str, Any]] = {}

    def create(self, workflow_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in workflow_data:
            workflow_data["id"] = f"wf_{uuid4().hex[:12]}"
        if "created_at" not in workflow_data:
            workflow_data["created_at"] = datetime.now(timezone.utc).isoformat()
        self._workflows[workflow_data["id"]] = workflow_data
        return workflow_data

    def get_by_id(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        return self._workflows.get(workflow_id)

    def update(
        self, workflow_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if workflow_id not in self._workflows:
            return None
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._workflows[workflow_id].update(updates)
        return self._workflows[workflow_id]

    def delete(self, workflow_id: str) -> bool:
        if workflow_id in self._workflows:
            del self._workflows[workflow_id]
            return True
        return False

    def list_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        return [
            w for w in self._workflows.values() if w.get("organization_id") == org_id
        ]


class InMemoryWorkflowExecutionRepository(WorkflowExecutionRepository):
    """In-memory workflow execution repository. Data lost on restart."""

    def __init__(self):
        self._executions: Dict[str, Dict[str, Any]] = {}

    def create(self, execution_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in execution_data:
            execution_data["id"] = f"exec_{uuid4().hex[:12]}"
        if "created_at" not in execution_data:
            execution_data["created_at"] = datetime.now(timezone.utc).isoformat()
        self._executions[execution_data["id"]] = execution_data
        return execution_data

    def update(
        self, execution_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if execution_id not in self._executions:
            return None
        self._executions[execution_id].update(updates)
        return self._executions[execution_id]

    def list_by_workflow(
        self, workflow_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        execs = [
            e for e in self._executions.values() if e.get("workflow_id") == workflow_id
        ]
        execs.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        return execs[:limit]


class InMemoryProjectMemoryRepository(ProjectMemoryRepository):
    """In-memory project memory repository for ADRs, specs, and session summaries."""

    def __init__(self):
        self._memories: Dict[str, Dict[str, Any]] = {}

    def create(self, memory_data: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in memory_data:
            memory_data["id"] = f"mem_{uuid4().hex[:12]}"
        if "created_at" not in memory_data:
            memory_data["created_at"] = datetime.now(timezone.utc).isoformat()
        if "updated_at" not in memory_data:
            memory_data["updated_at"] = datetime.now(timezone.utc).isoformat()
        if "tags" not in memory_data:
            memory_data["tags"] = []
        if "metadata" not in memory_data:
            memory_data["metadata"] = {}
        self._memories[memory_data["id"]] = memory_data
        return memory_data

    def get_by_id(self, memory_id: str) -> Optional[Dict[str, Any]]:
        return self._memories.get(memory_id)

    def update(
        self, memory_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if memory_id not in self._memories:
            return None
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._memories[memory_id].update(updates)
        return self._memories[memory_id]

    def delete(self, memory_id: str) -> bool:
        if memory_id in self._memories:
            del self._memories[memory_id]
            return True
        return False

    def list_by_project(
        self,
        project_id: str,
        memory_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        memories = [
            m for m in self._memories.values() if m.get("project_id") == project_id
        ]
        if memory_type:
            memories = [m for m in memories if m.get("memory_type") == memory_type]
        # Sort by created_at descending
        memories.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return memories[offset : offset + limit]

    def list_by_type(
        self,
        memory_type: str,
        project_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        memories = [
            m for m in self._memories.values() if m.get("memory_type") == memory_type
        ]
        if project_id:
            memories = [m for m in memories if m.get("project_id") == project_id]
        memories.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return memories[offset : offset + limit]

    def search_by_tags(
        self,
        tags: List[str],
        project_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        tag_set = set(tags)
        memories = []
        for m in self._memories.values():
            if project_id and m.get("project_id") != project_id:
                continue
            mem_tags = set(m.get("tags", []))
            if tag_set & mem_tags:  # Any overlap
                memories.append(m)
        memories.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return memories[:limit]

    def get_recent(
        self,
        project_id: str,
        limit: int = 10,
        memory_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.list_by_project(project_id, memory_type, limit, 0)
