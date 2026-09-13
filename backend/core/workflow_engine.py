"""
V Core - Workflow Engine

Visual Process Builder with Triggers, Conditions, Actions
Based on V PRD Section 4.4

Features:
- Visual workflow designer
- Multiple trigger types (event, schedule, webhook, manual)
- Conditional logic (if/else, switch)
- Built-in actions (email, SMS, AI, data, integration)
- Parallel and sequential execution
- Error handling and retries
- Execution history and logs
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Callable
from uuid import uuid4
import asyncio

from core.base_service import PersistentService

logger = logging.getLogger(__name__)

try:
    from core.repositories import (
        get_workflow_repository,
        get_workflow_execution_repository,
    )
except ImportError:
    pass


# ============================================
# Enums
# ============================================


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class TriggerType(str, Enum):
    MANUAL = "manual"
    SCHEDULE = "schedule"  # Cron-based
    EVENT = "event"  # Record created/updated/deleted
    WEBHOOK = "webhook"  # External HTTP trigger
    FORM = "form"  # Form submission
    EMAIL = "email"  # Incoming email
    AI_INSIGHT = "ai_insight"  # Proactive AI trigger


class ActionType(str, Enum):
    # Communication
    SEND_EMAIL = "send_email"
    SEND_SMS = "send_sms"
    SEND_WHATSAPP = "send_whatsapp"
    SEND_NOTIFICATION = "send_notification"

    # Data
    CREATE_RECORD = "create_record"
    UPDATE_RECORD = "update_record"
    DELETE_RECORD = "delete_record"
    LOOKUP_RECORD = "lookup_record"

    # Logic
    CONDITION = "condition"
    SWITCH = "switch"
    LOOP = "loop"
    DELAY = "delay"

    # AI
    AI_GENERATE = "ai_generate"
    AI_CLASSIFY = "ai_classify"
    AI_EXTRACT = "ai_extract"
    AI_SUMMARIZE = "ai_summarize"
    AI_AGENT = "ai_agent"

    # Integration
    HTTP_REQUEST = "http_request"
    WEBHOOK_CALL = "webhook_call"

    # Control
    SET_VARIABLE = "set_variable"
    TRANSFORM_DATA = "transform_data"
    MERGE_DATA = "merge_data"

    # Human
    APPROVAL = "approval"
    ASSIGN_TASK = "assign_task"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"  # Waiting for approval/input
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ConditionOperator(str, Enum):
    EQUALS = "eq"
    NOT_EQUALS = "ne"
    GREATER_THAN = "gt"
    LESS_THAN = "lt"
    GREATER_OR_EQUAL = "gte"
    LESS_OR_EQUAL = "lte"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"
    IN_LIST = "in"
    NOT_IN_LIST = "not_in"


# ============================================
# Trigger Definitions
# ============================================


@dataclass
class TriggerConfig:
    """Workflow trigger configuration."""

    type: TriggerType

    # Event trigger
    entity_id: Optional[str] = None
    event_type: Optional[str] = None  # created, updated, deleted
    field_filters: dict = field(default_factory=dict)

    # Schedule trigger (cron)
    cron_expression: Optional[str] = None
    timezone: str = "UTC"

    # Webhook trigger
    webhook_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "entityId": self.entity_id,
            "eventType": self.event_type,
            "fieldFilters": self.field_filters,
            "cronExpression": self.cron_expression,
            "timezone": self.timezone,
            "webhookPath": self.webhook_path,
        }


# ============================================
# Condition Definitions
# ============================================


@dataclass
class Condition:
    """Single condition for conditional logic."""

    field: str
    operator: ConditionOperator
    value: Any

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "operator": self.operator.value,
            "value": self.value,
        }

    def evaluate(self, data: dict) -> bool:
        """Evaluate condition against data."""
        field_value = self._get_nested_value(data, self.field)

        if self.operator == ConditionOperator.EQUALS:
            return field_value == self.value
        elif self.operator == ConditionOperator.NOT_EQUALS:
            return field_value != self.value
        elif self.operator == ConditionOperator.GREATER_THAN:
            return (field_value or 0) > self.value
        elif self.operator == ConditionOperator.LESS_THAN:
            return (field_value or 0) < self.value
        elif self.operator == ConditionOperator.GREATER_OR_EQUAL:
            return (field_value or 0) >= self.value
        elif self.operator == ConditionOperator.LESS_OR_EQUAL:
            return (field_value or 0) <= self.value
        elif self.operator == ConditionOperator.CONTAINS:
            return self.value in str(field_value or "")
        elif self.operator == ConditionOperator.NOT_CONTAINS:
            return self.value not in str(field_value or "")
        elif self.operator == ConditionOperator.STARTS_WITH:
            return str(field_value or "").startswith(self.value)
        elif self.operator == ConditionOperator.ENDS_WITH:
            return str(field_value or "").endswith(self.value)
        elif self.operator == ConditionOperator.IS_EMPTY:
            return not field_value
        elif self.operator == ConditionOperator.IS_NOT_EMPTY:
            return bool(field_value)
        elif self.operator == ConditionOperator.IN_LIST:
            return field_value in self.value
        elif self.operator == ConditionOperator.NOT_IN_LIST:
            return field_value not in self.value
        return False

    def _get_nested_value(self, data: dict, path: str) -> Any:
        """Get nested value from dict using dot notation."""
        keys = path.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value


@dataclass
class ConditionGroup:
    """Group of conditions with AND/OR logic."""

    conditions: list[Condition] = field(default_factory=list)
    logic: str = "and"  # "and" or "or"

    def to_dict(self) -> dict:
        return {
            "conditions": [c.to_dict() for c in self.conditions],
            "logic": self.logic,
        }

    def evaluate(self, data: dict) -> bool:
        if not self.conditions:
            return True
        results = [c.evaluate(data) for c in self.conditions]
        return all(results) if self.logic == "and" else any(results)


# ============================================
# Action/Node Definitions
# ============================================


@dataclass
class WorkflowNode:
    """Workflow node (action step)."""

    id: str = field(default_factory=lambda: f"node_{uuid4().hex[:8]}")
    type: ActionType = ActionType.SET_VARIABLE
    name: str = ""
    description: str = ""

    # Configuration (varies by type)
    config: dict = field(default_factory=dict)

    # Conditional execution
    condition: Optional[ConditionGroup] = None

    # Branching
    next_nodes: list[str] = field(default_factory=list)  # Default next
    on_success: Optional[str] = None
    on_failure: Optional[str] = None
    true_branch: Optional[str] = None  # For conditions
    false_branch: Optional[str] = None

    # Error handling
    retry_count: int = 0
    retry_delay_seconds: int = 60
    continue_on_error: bool = False

    # Position (for visual editor)
    position_x: int = 0
    position_y: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "description": self.description,
            "config": self.config,
            "condition": self.condition.to_dict() if self.condition else None,
            "nextNodes": self.next_nodes,
            "onSuccess": self.on_success,
            "onFailure": self.on_failure,
            "trueBranch": self.true_branch,
            "falseBranch": self.false_branch,
            "retryCount": self.retry_count,
            "retryDelaySeconds": self.retry_delay_seconds,
            "continueOnError": self.continue_on_error,
            "positionX": self.position_x,
            "positionY": self.position_y,
        }


# ============================================
# Workflow Definition
# ============================================


@dataclass
class Workflow:
    """Complete workflow definition."""

    id: str = field(default_factory=lambda: f"wf_{uuid4().hex[:12]}")
    organization_id: str = ""
    name: str = ""
    description: str = ""
    icon: str = "⚡"
    color: str = "#6366f1"

    # Status
    status: WorkflowStatus = WorkflowStatus.DRAFT

    # Trigger
    trigger: Optional[TriggerConfig] = None

    # Nodes
    nodes: list[WorkflowNode] = field(default_factory=list)
    start_node_id: Optional[str] = None

    # Variables
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)

    # Settings
    timeout_seconds: int = 3600
    max_retries: int = 3

    # Stats
    run_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_run_at: Optional[datetime] = None

    # Metadata
    created_by: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "organizationId": self.organization_id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "status": self.status.value,
            "trigger": self.trigger.to_dict() if self.trigger else None,
            "nodes": [n.to_dict() for n in self.nodes],
            "startNodeId": self.start_node_id,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
            "timeoutSeconds": self.timeout_seconds,
            "maxRetries": self.max_retries,
            "runCount": self.run_count,
            "successCount": self.success_count,
            "failureCount": self.failure_count,
            "lastRunAt": self.last_run_at.isoformat() if self.last_run_at else None,
            "createdBy": self.created_by,
            "createdAt": self.created_at.isoformat(),
        }

    def get_node(self, node_id: str) -> Optional[WorkflowNode]:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None


# ============================================
# Execution
# ============================================


@dataclass
class NodeExecution:
    """Single node execution record."""

    id: str = field(default_factory=lambda: f"nexec_{uuid4().hex[:8]}")
    node_id: str = ""
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    input_data: dict = field(default_factory=dict)
    output_data: dict = field(default_factory=dict)
    error: Optional[str] = None
    retry_count: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "nodeId": self.node_id,
            "status": self.status.value,
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "inputData": self.input_data,
            "outputData": self.output_data,
            "error": self.error,
            "retryCount": self.retry_count,
        }


@dataclass
class WorkflowExecution:
    """Workflow execution instance."""

    id: str = field(default_factory=lambda: f"exec_{uuid4().hex[:12]}")
    workflow_id: str = ""
    organization_id: str = ""

    # Status
    status: ExecutionStatus = ExecutionStatus.PENDING

    # Data
    trigger_data: dict = field(default_factory=dict)
    variables: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)

    # Node executions
    node_executions: list[NodeExecution] = field(default_factory=list)
    current_node_id: Optional[str] = None

    # Timing
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Error
    error: Optional[str] = None

    # Triggered by
    triggered_by: str = ""  # user_id or "system"
    trigger_type: str = ""  # manual, schedule, event, webhook

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflowId": self.workflow_id,
            "status": self.status.value,
            "triggerData": self.trigger_data,
            "variables": self.variables,
            "output": self.output,
            "nodeExecutions": [n.to_dict() for n in self.node_executions],
            "currentNodeId": self.current_node_id,
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "triggeredBy": self.triggered_by,
            "triggerType": self.trigger_type,
        }


# ============================================
# Workflow Templates
# ============================================


def create_welcome_email_workflow(org_id: str) -> Workflow:
    """Template: Send welcome email on new contact."""
    return Workflow(
        organization_id=org_id,
        name="Welcome Email",
        description="Send welcome email when a new contact is created",
        icon="📧",
        color="#3b82f6",
        trigger=TriggerConfig(
            type=TriggerType.EVENT, entity_id="contacts", event_type="created"
        ),
        nodes=[
            WorkflowNode(
                id="node_1",
                type=ActionType.SEND_EMAIL,
                name="Send Welcome Email",
                config={
                    "to": "{{trigger.data.email}}",
                    "subject": "Welcome!",
                    "template": "welcome",
                },
                position_x=100,
                position_y=100,
            ),
        ],
        start_node_id="node_1",
    )


def create_deal_notification_workflow(org_id: str) -> Workflow:
    """Template: Notify on high-value deal."""
    return Workflow(
        organization_id=org_id,
        name="High Value Deal Alert",
        description="Notify team when deal value > $10,000",
        icon="💰",
        color="#10b981",
        trigger=TriggerConfig(
            type=TriggerType.EVENT,
            entity_id="deals",
            event_type="created",
            field_filters={"value": {"gt": 10000}},
        ),
        nodes=[
            WorkflowNode(
                id="node_1",
                type=ActionType.CONDITION,
                name="Check Value",
                config={
                    "field": "trigger.data.value",
                    "operator": "gt",
                    "value": 10000,
                },
                true_branch="node_2",
                position_x=100,
                position_y=100,
            ),
            WorkflowNode(
                id="node_2",
                type=ActionType.SEND_NOTIFICATION,
                name="Notify Team",
                config={
                    "channel": "slack",
                    "message": "🎉 New high-value deal: {{trigger.data.name}} - ${{trigger.data.value}}",
                },
                position_x=300,
                position_y=100,
            ),
        ],
        start_node_id="node_1",
    )


def create_follow_up_workflow(org_id: str) -> Workflow:
    """Template: Auto follow-up after appointment."""
    return Workflow(
        organization_id=org_id,
        name="Appointment Follow-up",
        description="Send follow-up email 24h after appointment",
        icon="📅",
        color="#ec4899",
        trigger=TriggerConfig(
            type=TriggerType.EVENT,
            entity_id="appointments",
            event_type="updated",
            field_filters={"status": "completed"},
        ),
        nodes=[
            WorkflowNode(
                id="node_1",
                type=ActionType.DELAY,
                name="Wait 24 Hours",
                config={"duration": 86400, "unit": "seconds"},
                next_nodes=["node_2"],
                position_x=100,
                position_y=100,
            ),
            WorkflowNode(
                id="node_2",
                type=ActionType.AI_GENERATE,
                name="Generate Follow-up",
                config={
                    "prompt": "Write a brief follow-up email for {{trigger.data.contact.name}} after their {{trigger.data.title}} appointment"
                },
                next_nodes=["node_3"],
                position_x=300,
                position_y=100,
            ),
            WorkflowNode(
                id="node_3",
                type=ActionType.SEND_EMAIL,
                name="Send Follow-up",
                config={
                    "to": "{{trigger.data.contact.email}}",
                    "subject": "Following up on your visit",
                },
                position_x=500,
                position_y=100,
            ),
        ],
        start_node_id="node_1",
    )


# ============================================
# Workflow Engine Service
# ============================================


class WorkflowEngineService(PersistentService):
    """Workflow Engine - Process automation."""

    def __init__(self, workflow_repo=None, execution_repo=None, use_persistence=None):
        """
        Initialize WorkflowEngineService.

        Args:
            workflow_repo: Optional workflow repository
            execution_repo: Optional execution repository
            use_persistence: If True, auto-create repos. If None, check env var.
        """
        self._using_persistence = self._bootstrap_persistence(use_persistence)

        if self._using_persistence:
            self._workflow_repo = workflow_repo or get_workflow_repository()
            self._execution_repo = execution_repo or get_workflow_execution_repository()
        else:
            self._workflow_repo = None
            self._execution_repo = None

        # In-memory cache (always available)
        self._workflows: dict[str, Workflow] = {}
        self._executions: dict[str, WorkflowExecution] = {}
        self._action_handlers: dict[ActionType, Callable] = {}

        self._register_default_handlers()
        if not self._using_persistence:
            self._init_sample_data()

    def _init_sample_data(self):
        """Initialize with sample workflows."""
        org_id = "org_demo"
        for wf in [
            create_welcome_email_workflow(org_id),
            create_deal_notification_workflow(org_id),
            create_follow_up_workflow(org_id),
        ]:
            wf.created_by = "dev_seed_user"
            self._workflows[wf.id] = wf

    def _register_default_handlers(self):
        """Register default action handlers."""
        self._action_handlers = {
            ActionType.SET_VARIABLE: self._handle_set_variable,
            ActionType.CONDITION: self._handle_condition,
            ActionType.DELAY: self._handle_delay,
            ActionType.SEND_EMAIL: self._handle_send_email,
            ActionType.SEND_SMS: self._handle_send_sms,
            ActionType.SEND_NOTIFICATION: self._handle_send_notification,
            ActionType.CREATE_RECORD: self._handle_create_record,
            ActionType.UPDATE_RECORD: self._handle_update_record,
            ActionType.AI_GENERATE: self._handle_ai_generate,
            ActionType.HTTP_REQUEST: self._handle_http_request,
        }

    # ==========================================
    # Workflow CRUD
    # ==========================================

    def create_workflow(
        self, org_id: str, name: str, user_id: str, **kwargs
    ) -> Workflow:
        """Create a new workflow."""
        wf = Workflow(
            organization_id=org_id,
            name=name,
            created_by=user_id,
            description=kwargs.get("description", ""),
            icon=kwargs.get("icon", "⚡"),
            color=kwargs.get("color", "#6366f1"),
        )
        self._workflows[wf.id] = wf

        if self._using_persistence and self._workflow_repo:
            try:
                self._workflow_repo.create(
                    {
                        "id": wf.id,
                        "organization_id": org_id,
                        "name": name,
                        "description": kwargs.get("description", ""),
                        "trigger": wf.trigger.to_dict() if wf.trigger else {},
                        "nodes": [n.to_dict() for n in wf.nodes],
                        "is_active": wf.status == WorkflowStatus.ACTIVE,
                    }
                )
            except Exception as e:
                logger.warning("Failed to persist workflow %s: %s", wf.id, e)

        return wf

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def list_workflows(
        self, org_id: str, status: Optional[WorkflowStatus] = None
    ) -> list[Workflow]:
        workflows = [w for w in self._workflows.values() if w.organization_id == org_id]
        if status:
            workflows = [w for w in workflows if w.status == status]
        return sorted(workflows, key=lambda w: w.updated_at, reverse=True)

    def update_workflow(self, workflow_id: str, updates: dict) -> Optional[Workflow]:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None
        for key, value in updates.items():
            if hasattr(wf, key):
                setattr(wf, key, value)
        wf.updated_at = datetime.now(timezone.utc)
        return wf

    def delete_workflow(self, workflow_id: str) -> bool:
        if workflow_id in self._workflows:
            del self._workflows[workflow_id]
            if self._using_persistence and self._workflow_repo:
                try:
                    self._workflow_repo.delete(workflow_id)
                except Exception as e:
                    logger.warning(
                        "Failed to persist workflow deletion %s: %s", workflow_id, e
                    )
            return True
        return False

    def activate_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self.update_workflow(workflow_id, {"status": WorkflowStatus.ACTIVE})

    def pause_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self.update_workflow(workflow_id, {"status": WorkflowStatus.PAUSED})

    # ==========================================
    # Node Management
    # ==========================================

    def add_node(
        self,
        workflow_id: str,
        node_type: ActionType,
        name: str,
        config: dict = None,
        **kwargs,
    ) -> Optional[WorkflowNode]:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None

        node = WorkflowNode(
            type=node_type,
            name=name,
            config=config or {},
            description=kwargs.get("description", ""),
            position_x=kwargs.get("position_x", 0),
            position_y=kwargs.get("position_y", 0),
        )
        wf.nodes.append(node)

        # Set as start node if first
        if not wf.start_node_id:
            wf.start_node_id = node.id

        wf.updated_at = datetime.now(timezone.utc)
        return node

    def update_node(
        self, workflow_id: str, node_id: str, updates: dict
    ) -> Optional[WorkflowNode]:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None
        node = wf.get_node(node_id)
        if not node:
            return None
        for key, value in updates.items():
            if hasattr(node, key):
                setattr(node, key, value)
        wf.updated_at = datetime.now(timezone.utc)
        return node

    def delete_node(self, workflow_id: str, node_id: str) -> bool:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return False
        wf.nodes = [n for n in wf.nodes if n.id != node_id]
        # Update references
        for node in wf.nodes:
            node.next_nodes = [n for n in node.next_nodes if n != node_id]
            if node.on_success == node_id:
                node.on_success = None
            if node.on_failure == node_id:
                node.on_failure = None
            if node.true_branch == node_id:
                node.true_branch = None
            if node.false_branch == node_id:
                node.false_branch = None
        if wf.start_node_id == node_id:
            wf.start_node_id = wf.nodes[0].id if wf.nodes else None
        wf.updated_at = datetime.now(timezone.utc)
        return True

    def connect_nodes(
        self,
        workflow_id: str,
        from_node_id: str,
        to_node_id: str,
        connection_type: str = "default",
    ) -> bool:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return False
        from_node = wf.get_node(from_node_id)
        if not from_node:
            return False

        if connection_type == "default":
            if to_node_id not in from_node.next_nodes:
                from_node.next_nodes.append(to_node_id)
        elif connection_type == "success":
            from_node.on_success = to_node_id
        elif connection_type == "failure":
            from_node.on_failure = to_node_id
        elif connection_type == "true":
            from_node.true_branch = to_node_id
        elif connection_type == "false":
            from_node.false_branch = to_node_id

        wf.updated_at = datetime.now(timezone.utc)
        return True

    # ==========================================
    # Trigger Management
    # ==========================================

    def set_trigger(
        self, workflow_id: str, trigger_type: TriggerType, **kwargs
    ) -> Optional[TriggerConfig]:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return None

        trigger = TriggerConfig(
            type=trigger_type,
            entity_id=kwargs.get("entity_id"),
            event_type=kwargs.get("event_type"),
            field_filters=kwargs.get("field_filters", {}),
            cron_expression=kwargs.get("cron_expression"),
            timezone=kwargs.get("timezone", "UTC"),
            webhook_path=kwargs.get("webhook_path"),
        )
        wf.trigger = trigger
        wf.updated_at = datetime.now(timezone.utc)
        return trigger

    # ==========================================
    # Execution
    # ==========================================

    async def execute_workflow(
        self,
        workflow_id: str,
        trigger_data: dict,
        triggered_by: str,
        trigger_type: str = "manual",
    ) -> WorkflowExecution:
        """Execute a workflow."""
        wf = self._workflows.get(workflow_id)
        if not wf or wf.status != WorkflowStatus.ACTIVE:
            raise ValueError(f"Workflow {workflow_id} not found or not active")

        # Create execution
        execution = WorkflowExecution(
            workflow_id=workflow_id,
            organization_id=wf.organization_id,
            trigger_data=trigger_data,
            triggered_by=triggered_by,
            trigger_type=trigger_type,
            status=ExecutionStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            variables={"trigger": trigger_data},
        )
        self._executions[execution.id] = execution

        if len(self._executions) > 1000:
            oldest_keys = sorted(
                self._executions,
                key=lambda k: self._executions[k].started_at or datetime.min,
            )[: len(self._executions) - 1000]
            for k in oldest_keys:
                del self._executions[k]

        if self._using_persistence and self._execution_repo:
            try:
                self._execution_repo.create(
                    {
                        "id": execution.id,
                        "workflow_id": workflow_id,
                        "organization_id": wf.organization_id,
                        "triggered_by": triggered_by,
                    }
                )
            except Exception as e:
                logger.warning("Failed to persist execution %s: %s", execution.id, e)

        # Update workflow stats
        wf.run_count += 1
        wf.last_run_at = datetime.now(timezone.utc)

        try:
            # Execute from start node
            if wf.start_node_id:
                await self._execute_node(execution, wf, wf.start_node_id)

            execution.status = ExecutionStatus.COMPLETED
            wf.success_count += 1
        except Exception as e:
            execution.status = ExecutionStatus.FAILED
            execution.error = str(e)
            wf.failure_count += 1

        execution.completed_at = datetime.now(timezone.utc)

        if self._using_persistence and self._execution_repo:
            try:
                self._execution_repo.update(
                    execution.id,
                    {
                        "status": execution.status.value,
                        "completedAt": int(execution.completed_at.timestamp() * 1000),
                        "error": execution.error,
                    },
                )
            except Exception as e:
                logger.warning(
                    "Failed to persist execution update %s: %s", execution.id, e
                )

        return execution

    async def _execute_node(
        self, execution: WorkflowExecution, workflow: Workflow, node_id: str
    ):
        """Execute a single node."""
        node = workflow.get_node(node_id)
        if not node:
            return

        execution.current_node_id = node_id

        # Create node execution record
        node_exec = NodeExecution(
            node_id=node_id,
            status=ExecutionStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            input_data=execution.variables.copy(),
        )
        execution.node_executions.append(node_exec)

        # Check condition
        if node.condition and not node.condition.evaluate(execution.variables):
            node_exec.status = ExecutionStatus.SKIPPED
            node_exec.completed_at = datetime.now(timezone.utc)
            return

        try:
            # Execute action
            handler = self._action_handlers.get(node.type)
            if handler:
                result = await handler(node, execution)
                node_exec.output_data = result or {}
                execution.variables.update({f"node_{node_id}": result})

            node_exec.status = ExecutionStatus.COMPLETED
            node_exec.completed_at = datetime.now(timezone.utc)

            # Determine next nodes
            next_nodes = []
            if node.type == ActionType.CONDITION:
                condition_result = self._evaluate_condition(
                    node.config, execution.variables
                )
                next_node = node.true_branch if condition_result else node.false_branch
                if next_node:
                    next_nodes = [next_node]
            elif node.on_success:
                next_nodes = [node.on_success]
            else:
                next_nodes = node.next_nodes

            # Execute next nodes
            for next_node_id in next_nodes:
                await self._execute_node(execution, workflow, next_node_id)

        except Exception as e:
            node_exec.status = ExecutionStatus.FAILED
            node_exec.error = str(e)
            node_exec.completed_at = datetime.now(timezone.utc)

            if not node.continue_on_error:
                raise

            # Try failure branch
            if node.on_failure:
                await self._execute_node(execution, workflow, node.on_failure)

    def _evaluate_condition(self, config: dict, variables: dict) -> bool:
        """Evaluate condition node."""
        field = config.get("field", "")
        operator = config.get("operator", "eq")
        value = config.get("value")

        condition = Condition(
            field=field, operator=ConditionOperator(operator), value=value
        )
        return condition.evaluate(variables)

    # ==========================================
    # Action Handlers
    # ==========================================

    async def _handle_set_variable(
        self, node: WorkflowNode, execution: WorkflowExecution
    ) -> dict:
        name = node.config.get("name", "")
        value = self._interpolate(node.config.get("value", ""), execution.variables)
        execution.variables[name] = value
        return {"name": name, "value": value}

    async def _handle_condition(
        self, node: WorkflowNode, execution: WorkflowExecution
    ) -> dict:
        result = self._evaluate_condition(node.config, execution.variables)
        return {"result": result}

    async def _handle_delay(
        self, node: WorkflowNode, execution: WorkflowExecution
    ) -> dict:
        duration = node.config.get("duration", 0)
        # In production, this would use a job queue
        await asyncio.sleep(min(duration, 5))  # Cap at 5s for demo
        return {"delayed": duration}

    async def _handle_send_email(
        self, node: WorkflowNode, execution: WorkflowExecution
    ) -> dict:
        to = self._interpolate(node.config.get("to", ""), execution.variables)
        subject = self._interpolate(node.config.get("subject", ""), execution.variables)
        # In production, this would send actual email
        return {"sent_to": to, "subject": subject, "status": "sent"}

    async def _handle_send_sms(
        self, node: WorkflowNode, execution: WorkflowExecution
    ) -> dict:
        to = self._interpolate(node.config.get("to", ""), execution.variables)
        message = self._interpolate(node.config.get("message", ""), execution.variables)
        return {"sent_to": to, "message": message[:160], "status": "sent"}

    async def _handle_send_notification(
        self, node: WorkflowNode, execution: WorkflowExecution
    ) -> dict:
        channel = node.config.get("channel", "app")
        message = self._interpolate(node.config.get("message", ""), execution.variables)
        return {"channel": channel, "message": message, "status": "sent"}

    async def _handle_create_record(
        self, node: WorkflowNode, execution: WorkflowExecution
    ) -> dict:
        entity_id = node.config.get("entity_id", "")
        data = {}
        for key, value in node.config.get("data", {}).items():
            data[key] = (
                self._interpolate(value, execution.variables)
                if isinstance(value, str)
                else value
            )
        return {"entity_id": entity_id, "data": data, "status": "created"}

    async def _handle_update_record(
        self, node: WorkflowNode, execution: WorkflowExecution
    ) -> dict:
        record_id = self._interpolate(
            node.config.get("record_id", ""), execution.variables
        )
        data = {}
        for key, value in node.config.get("data", {}).items():
            data[key] = (
                self._interpolate(value, execution.variables)
                if isinstance(value, str)
                else value
            )
        return {"record_id": record_id, "data": data, "status": "updated"}

    async def _handle_ai_generate(
        self, node: WorkflowNode, execution: WorkflowExecution
    ) -> dict:
        prompt = self._interpolate(node.config.get("prompt", ""), execution.variables)
        # In production, this would call AI service
        return {
            "prompt": prompt,
            "generated": f"AI generated response for: {prompt[:50]}...",
            "status": "completed",
        }

    async def _handle_http_request(
        self, node: WorkflowNode, execution: WorkflowExecution
    ) -> dict:
        url = self._interpolate(node.config.get("url", ""), execution.variables)
        method = node.config.get("method", "GET")
        # In production, this would make actual HTTP request
        return {"url": url, "method": method, "status_code": 200, "response": {}}

    def _interpolate(self, template: str, variables: dict) -> str:
        """Interpolate variables in template string."""

        def replace(match):
            path = match.group(1)
            keys = path.split(".")
            value = variables
            for key in keys:
                if isinstance(value, dict):
                    value = value.get(key, "")
                else:
                    return ""
            return str(value)

        return re.sub(r"\{\{([^}]+)\}\}", replace, template)

    # ==========================================
    # Execution History
    # ==========================================

    def get_execution(self, execution_id: str) -> Optional[WorkflowExecution]:
        return self._executions.get(execution_id)

    def list_executions(
        self, workflow_id: str, limit: int = 50
    ) -> list[WorkflowExecution]:
        executions = [
            e for e in self._executions.values() if e.workflow_id == workflow_id
        ]
        return sorted(
            executions, key=lambda e: e.started_at or datetime.min, reverse=True
        )[:limit]

    def get_workflow_stats(self, workflow_id: str) -> dict:
        wf = self._workflows.get(workflow_id)
        if not wf:
            return {}
        return {
            "runCount": wf.run_count,
            "successCount": wf.success_count,
            "failureCount": wf.failure_count,
            "successRate": wf.success_count / wf.run_count if wf.run_count > 0 else 0,
            "lastRunAt": wf.last_run_at.isoformat() if wf.last_run_at else None,
        }


# Singleton
_workflow_engine_service: Optional[WorkflowEngineService] = None


def get_workflow_engine_service() -> WorkflowEngineService:
    global _workflow_engine_service
    if _workflow_engine_service is None:
        _workflow_engine_service = WorkflowEngineService()
    return _workflow_engine_service


__all__ = [
    "WorkflowEngineService",
    "Workflow",
    "WorkflowNode",
    "TriggerConfig",
    "WorkflowExecution",
    "NodeExecution",
    "Condition",
    "ConditionGroup",
    "WorkflowStatus",
    "TriggerType",
    "ActionType",
    "ExecutionStatus",
    "ConditionOperator",
    "get_workflow_engine_service",
]
