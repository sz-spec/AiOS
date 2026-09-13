"""
V Core - The Brain

Complete Business OS modules:
1. Control Plane - Users, roles, permissions, organizations
2. Business Core - Customizable data model + custom fields
3. Workflow Engine - Process automation
4. Mission Control - AI monitoring, approvals, metrics

Based on V PRD Section 4
"""

from .control_plane import (
    ControlPlaneService,
    User,
    Organization,
    OrganizationMember,
    Role,
    Permission,
    Invitation,
    AuditLog,
    ApiKey,
    UserStatus,
    OrganizationPlan,
    AuditAction,
    SYSTEM_ROLES,
    get_control_plane_service,
)

from .business_core import (
    BusinessCoreService,
    EntityDefinition,
    FieldDefinition,
    Record,
    RecordHistory,
    SelectOption,
    FieldValidation,
    FieldType,
    RelationType,
    ValidationRule,
    get_business_core_service,
    create_contact_entity,
    create_deal_entity,
    create_task_entity,
    create_appointment_entity,
)

from .workflow_engine import (
    WorkflowEngineService,
    Workflow,
    WorkflowNode,
    TriggerConfig,
    WorkflowExecution,
    NodeExecution,
    Condition,
    ConditionGroup,
    WorkflowStatus,
    TriggerType,
    ActionType,
    ExecutionStatus,
    ConditionOperator,
    get_workflow_engine_service,
)

from .mission_control import (
    MissionControlService,
    Activity,
    ApprovalRequest,
    Alert,
    MetricDefinition,
    MetricValue,
    DashboardWidget,
    ActivityType,
    ActivityStatus,
    ApprovalStatus,
    ApprovalPriority,
    AlertSeverity,
    MetricType,
    SYSTEM_METRICS,
    get_mission_control_service,
)

__all__ = [
    # Control Plane
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
    # Business Core
    "BusinessCoreService",
    "EntityDefinition",
    "FieldDefinition",
    "Record",
    "RecordHistory",
    "SelectOption",
    "FieldValidation",
    "FieldType",
    "RelationType",
    "ValidationRule",
    "get_business_core_service",
    "create_contact_entity",
    "create_deal_entity",
    "create_task_entity",
    "create_appointment_entity",
    # Workflow Engine
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
    # Mission Control
    "MissionControlService",
    "Activity",
    "ApprovalRequest",
    "Alert",
    "MetricDefinition",
    "MetricValue",
    "DashboardWidget",
    "ActivityType",
    "ActivityStatus",
    "ApprovalStatus",
    "ApprovalPriority",
    "AlertSeverity",
    "MetricType",
    "SYSTEM_METRICS",
    "get_mission_control_service",
]
