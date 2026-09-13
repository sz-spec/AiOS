"""
V Core API Routes

REST API for:
- Control Plane (users, orgs, roles, permissions)
- Business Core (entities, fields, records)
- Workflow Engine (workflows, executions)
- Mission Control (activities, approvals, metrics)
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser

from core import (
    get_control_plane_service,
    get_business_core_service,
    get_workflow_engine_service,
    get_mission_control_service,
    Permission,
    OrganizationPlan,
    TriggerType,
    ActionType,
    WorkflowStatus,
    ApprovalStatus,
    ApprovalPriority,
    AlertSeverity,
    ActivityType,
)

router = APIRouter(tags=["v-core"])


# ============================================
# Request Models
# ============================================


class CreateOrgRequest(BaseModel):
    name: str
    plan: str = "free"


class CreateUserRequest(BaseModel):
    email: str
    name: str
    password: Optional[str] = None


class InviteRequest(BaseModel):
    email: str
    role_id: str


class CreateEntityRequest(BaseModel):
    name: str
    label: str
    fields: list[dict] = []


class CreateRecordRequest(BaseModel):
    data: dict


class CreateWorkflowRequest(BaseModel):
    name: str
    description: str = ""


class AddNodeRequest(BaseModel):
    type: str
    name: str
    config: dict = {}


class CreateApprovalRequest(BaseModel):
    title: str
    category: str
    action_type: str
    action_data: dict
    priority: str = "normal"


# ============================================
# Control Plane Routes
# ============================================


@router.get(
    "/organizations",
    summary="List organizations",
    description="Return all organizations the authenticated user belongs to",
)
async def list_organizations(user: AuthenticatedUser = Depends(get_current_user)):
    service = get_control_plane_service()
    orgs = service.list_user_organizations(user.id)
    return {"organizations": [o.to_dict() for o in orgs]}


@router.post("/organizations", summary="Create organization")
async def create_organization(
    request: CreateOrgRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_control_plane_service()
    org = service.create_organization(
        request.name, user.id, OrganizationPlan(request.plan)
    )
    return {"organization": org.to_dict()}


@router.get("/organizations/{org_id}", summary="Get organization")
async def get_organization(
    org_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    # Ownership check: user must belong to this org
    if (
        user.org_id is not None
        and user.org_id != org_id
        and not user.has_permission("admin:full")
    ):
        raise HTTPException(403, "Not a member of this organization")
    service = get_control_plane_service()
    org = service.get_organization(org_id)
    if not org:
        raise HTTPException(404, "Organization not found")
    return org.to_dict()


@router.get("/organizations/{org_id}/members", summary="List organization members")
async def list_members(
    org_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    # Ownership check: user must belong to this org
    if (
        user.org_id is not None
        and user.org_id != org_id
        and not user.has_permission("admin:full")
    ):
        raise HTTPException(403, "Not a member of this organization")
    service = get_control_plane_service()
    members = service.list_members(org_id)
    return {"members": members}


@router.post(
    "/organizations/{org_id}/invitations", summary="Invite organization member"
)
async def invite_member(
    org_id: str,
    request: InviteRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    # Ownership check: user must belong to this org
    if (
        user.org_id is not None
        and user.org_id != org_id
        and not user.has_permission("admin:full")
    ):
        raise HTTPException(403, "Not a member of this organization")
    service = get_control_plane_service()
    invitation = service.create_invitation(
        org_id, request.email, request.role_id, user.id
    )
    return {"invitation": invitation.to_dict()}


@router.get("/roles", summary="List roles")
async def list_roles(
    org_id: Optional[str] = None, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_control_plane_service()
    roles = service.list_roles(org_id)
    return {"roles": [r.to_dict() for r in roles]}


@router.get("/permissions", summary="List permissions")
async def list_permissions(user: AuthenticatedUser = Depends(get_current_user)):
    return {"permissions": [p.value for p in Permission]}


@router.get(
    "/audit-logs",
    summary="Get audit logs",
    description="Retrieve audit log entries for an organization with optional limit",
)
async def get_audit_logs(
    org_id: str, limit: int = 100, user: AuthenticatedUser = Depends(get_current_user)
):
    # Ownership check: user must belong to this org
    if (
        user.org_id is not None
        and user.org_id != org_id
        and not user.has_permission("admin:full")
    ):
        raise HTTPException(403, "Not a member of this organization")
    service = get_control_plane_service()
    logs = service.get_audit_logs(org_id, limit=limit)
    return {"logs": [l.to_dict() for l in logs]}


# ============================================
# Business Core Routes
# ============================================


@router.get("/entities", summary="List entities")
async def list_entities(user: AuthenticatedUser = Depends(get_current_user)):
    service = get_business_core_service()
    entities = service.list_entities(user.org_id)
    return {"entities": [e.to_dict() for e in entities]}


@router.post(
    "/entities",
    summary="Create entity",
    description="Define a new business entity with custom fields",
)
async def create_entity(
    request: CreateEntityRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_business_core_service()
    entity = service.create_entity(
        user.org_id, request.name, request.label, request.fields
    )
    return {"entity": entity.to_dict()}


@router.get("/entities/{entity_id}", summary="Get entity")
async def get_entity(
    entity_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_business_core_service()
    entity = service.get_entity(entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")
    return entity.to_dict()


@router.post("/entities/{entity_id}/fields", summary="Add field to entity")
async def add_field(
    entity_id: str, field: dict, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_business_core_service()
    result = service.add_field(entity_id, field)
    if not result:
        raise HTTPException(404, "Entity not found")
    return {"field": result.to_dict()}


@router.get(
    "/entities/{entity_id}/records",
    summary="List entity records",
    description="Paginated listing of records for a business entity",
)
async def list_records(
    entity_id: str,
    limit: int = 100,
    offset: int = 0,
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = get_business_core_service()
    # Ownership check: verify entity exists and belongs to user's org
    entity = service.get_entity(entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")
    entity_org = getattr(entity, "org_id", None)
    if (
        entity_org
        and entity_org != user.org_id
        and not user.has_permission("admin:full")
    ):
        raise HTTPException(403, "Access denied")
    records = service.list_records(entity_id, user.org_id, limit=limit, offset=offset)
    return {
        "records": [r.to_dict() for r in records],
        "total": service.count_records(entity_id, user.org_id),
    }


@router.post("/entities/{entity_id}/records", summary="Create record")
async def create_record(
    entity_id: str,
    request: CreateRecordRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = get_business_core_service()
    record = service.create_record(entity_id, user.org_id, request.data, user.id)
    return {"record": record.to_dict()}


@router.get("/records/{record_id}", summary="Get record")
async def get_record(
    record_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_business_core_service()
    record = service.get_record(record_id)
    if not record:
        raise HTTPException(404, "Record not found")
    return record.to_dict()


@router.patch("/records/{record_id}", summary="Update record")
async def update_record(
    record_id: str, data: dict, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_business_core_service()
    record = service.update_record(record_id, data, user.id)
    if not record:
        raise HTTPException(404, "Record not found")
    return {"record": record.to_dict()}


@router.delete("/records/{record_id}", summary="Delete record")
async def delete_record(
    record_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_business_core_service()
    if not service.delete_record(record_id, user.id):
        raise HTTPException(404, "Record not found")
    return {"deleted": True}


@router.get(
    "/records/{record_id}/history",
    summary="Get record history",
    description="Retrieve the full change history for a specific record",
)
async def get_record_history(
    record_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_business_core_service()
    # Ownership check: verify the record exists and belongs to user's org
    record = service.get_record(record_id)
    if not record:
        raise HTTPException(404, "Record not found")
    # If record has org_id, check ownership
    record_org = getattr(record, "org_id", None)
    if (
        record_org
        and record_org != user.org_id
        and not user.has_permission("admin:full")
    ):
        raise HTTPException(403, "Access denied")
    history = service.get_record_history(record_id)
    return {"history": [h.to_dict() for h in history]}


# ============================================
# Workflow Engine Routes
# ============================================


@router.get(
    "/workflows",
    summary="List workflows",
    description="Return all workflows, optionally filtered by status",
)
async def list_workflows(
    status: Optional[str] = None, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_workflow_engine_service()
    wf_status = WorkflowStatus(status) if status else None
    workflows = service.list_workflows(user.org_id, wf_status)
    return {"workflows": [w.to_dict() for w in workflows]}


@router.post("/workflows", summary="Create workflow")
async def create_workflow(
    request: CreateWorkflowRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_workflow_engine_service()
    workflow = service.create_workflow(
        user.org_id, request.name, user.id, description=request.description
    )
    return {"workflow": workflow.to_dict()}


@router.get("/workflows/{workflow_id}", summary="Get workflow")
async def get_workflow(
    workflow_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_workflow_engine_service()
    workflow = service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(404, "Workflow not found")
    return workflow.to_dict()


@router.post(
    "/workflows/{workflow_id}/nodes",
    summary="Add workflow node",
    description="Add an action node to a workflow definition",
)
async def add_workflow_node(
    workflow_id: str,
    request: AddNodeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = get_workflow_engine_service()
    # Ownership check: verify workflow exists and belongs to user's org
    workflow = service.get_workflow(workflow_id)
    if not workflow:
        raise HTTPException(404, "Workflow not found")
    workflow_org = getattr(workflow, "org_id", None)
    if (
        workflow_org
        and workflow_org != user.org_id
        and not user.has_permission("admin:full")
    ):
        raise HTTPException(403, "Access denied")
    node = service.add_node(
        workflow_id, ActionType(request.type), request.name, request.config
    )
    if not node:
        raise HTTPException(404, "Workflow not found")
    return {"node": node.to_dict()}


@router.post("/workflows/{workflow_id}/activate", summary="Activate workflow")
async def activate_workflow(
    workflow_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_workflow_engine_service()
    workflow = service.activate_workflow(workflow_id)
    if not workflow:
        raise HTTPException(404, "Workflow not found")
    return {"workflow": workflow.to_dict()}


@router.post("/workflows/{workflow_id}/pause", summary="Pause workflow")
async def pause_workflow(
    workflow_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_workflow_engine_service()
    workflow = service.pause_workflow(workflow_id)
    if not workflow:
        raise HTTPException(404, "Workflow not found")
    return {"workflow": workflow.to_dict()}


@router.post(
    "/workflows/{workflow_id}/execute",
    summary="Execute workflow",
    description="Manually trigger a workflow execution with optional trigger data",
)
async def execute_workflow(
    workflow_id: str,
    trigger_data: dict = {},
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = get_workflow_engine_service()
    try:
        execution = await service.execute_workflow(
            workflow_id, trigger_data, user.id, "manual"
        )
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"execution": execution.to_dict()}


@router.get("/workflows/{workflow_id}/executions", summary="List workflow executions")
async def list_workflow_executions(
    workflow_id: str,
    limit: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = get_workflow_engine_service()
    executions = service.list_executions(workflow_id, limit)
    return {"executions": [e.to_dict() for e in executions]}


@router.get("/workflows/{workflow_id}/stats", summary="Get workflow stats")
async def get_workflow_stats(
    workflow_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_workflow_engine_service()
    return service.get_workflow_stats(workflow_id)


@router.get("/workflow-actions", summary="List workflow action types")
async def list_workflow_actions(user: AuthenticatedUser = Depends(get_current_user)):
    return {
        "actions": [
            {"id": a.value, "name": a.name.replace("_", " ").title()}
            for a in ActionType
        ]
    }


@router.get("/workflow-triggers", summary="List workflow trigger types")
async def list_workflow_triggers(user: AuthenticatedUser = Depends(get_current_user)):
    return {
        "triggers": [
            {"id": t.value, "name": t.name.replace("_", " ").title()}
            for t in TriggerType
        ]
    }


# ============================================
# Mission Control Routes
# ============================================


@router.get(
    "/dashboard",
    summary="Get dashboard data",
    description="Return aggregated dashboard metrics, activities, and alerts for mission control",
)
async def get_dashboard(user: AuthenticatedUser = Depends(get_current_user)):
    service = get_mission_control_service()
    return service.get_dashboard_data(user.org_id)


@router.get(
    "/activities",
    summary="List activities",
    description="Return recent activities, optionally filtered by type",
)
async def list_activities(
    type: Optional[str] = None,
    limit: int = 100,
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = get_mission_control_service()
    act_type = ActivityType(type) if type else None
    activities = service.get_activities(user.org_id, type=act_type, limit=limit)
    return {"activities": [a.to_dict() for a in activities]}


@router.get(
    "/activities/summary",
    summary="Get activity summary",
    description="Return aggregated activity statistics for the specified time window",
)
async def get_activity_summary(
    hours: int = 24, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_mission_control_service()
    return service.get_activity_summary(user.org_id, hours)


@router.get(
    "/approvals",
    summary="List approvals",
    description="Return pending and processed approval requests, optionally filtered by status",
)
async def list_approvals(
    status: Optional[str] = None, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_mission_control_service()
    apr_status = ApprovalStatus(status) if status else None
    approvals = service.list_approvals(user.org_id, status=apr_status)
    return {"approvals": [a.to_dict() for a in approvals]}


@router.post("/approvals", summary="Create approval request")
async def create_approval(
    request: CreateApprovalRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_mission_control_service()
    approval = service.create_approval(
        user.org_id,
        request.title,
        request.category,
        "user",
        user.id,
        "User",
        request.action_type,
        request.action_data,
        priority=ApprovalPriority(request.priority),
    )
    return {"approval": approval.to_dict()}


@router.post("/approvals/{approval_id}/approve", summary="Approve request")
async def approve_request(
    approval_id: str,
    note: str = "",
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = get_mission_control_service()
    approval = service.approve(approval_id, user.id, note)
    if not approval:
        raise HTTPException(404, "Approval not found or already processed")
    return {"approval": approval.to_dict()}


@router.post("/approvals/{approval_id}/reject", summary="Reject request")
async def reject_request(
    approval_id: str,
    note: str = "",
    user: AuthenticatedUser = Depends(get_current_user),
):
    service = get_mission_control_service()
    approval = service.reject(approval_id, user.id, note)
    if not approval:
        raise HTTPException(404, "Approval not found or already processed")
    return {"approval": approval.to_dict()}


@router.get(
    "/alerts",
    summary="List alerts",
    description="Return active alerts, optionally filtered by severity level",
)
async def list_alerts(
    severity: Optional[str] = None, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_mission_control_service()
    sev = AlertSeverity(severity) if severity else None
    alerts = service.get_alerts(user.org_id, severity=sev)
    return {"alerts": [a.to_dict() for a in alerts]}


@router.get("/alerts/counts", summary="Get alert counts")
async def get_alert_counts(user: AuthenticatedUser = Depends(get_current_user)):
    service = get_mission_control_service()
    return service.get_alert_counts(user.org_id)


@router.post("/alerts/{alert_id}/read", summary="Mark alert as read")
async def mark_alert_read(
    alert_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_mission_control_service()
    if not service.mark_alert_read(alert_id):
        raise HTTPException(404, "Alert not found")
    return {"read": True}


@router.post("/alerts/{alert_id}/resolve", summary="Resolve alert")
async def resolve_alert(
    alert_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_mission_control_service()
    if not service.resolve_alert(alert_id, user.id):
        raise HTTPException(404, "Alert not found")
    return {"resolved": True}


@router.get(
    "/metrics",
    summary="Get business metrics",
    description="Return metric definitions and current values, optionally filtered by category",
)
async def get_metrics(
    category: Optional[str] = None, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_mission_control_service()
    definitions = service.get_metric_definitions(category)
    values = service.get_metrics([d.id for d in definitions])
    return {
        "metrics": [{**d.to_dict(), "value": values.get(d.id)} for d in definitions]
    }


@router.get(
    "/metrics/{metric_id}/history",
    summary="Get metric history",
    description="Return historical values for a specific metric over a given time window",
)
async def get_metric_history(
    metric_id: str, hours: int = 24, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_mission_control_service()
    history = service.get_metric_history(metric_id, hours)
    return {"history": [h.to_dict() for h in history]}


@router.get(
    "/agents/performance",
    summary="Get agent performance",
    description="Return performance metrics for all agents or a specific agent",
)
async def get_agent_performance(
    agent_id: Optional[str] = None, user: AuthenticatedUser = Depends(get_current_user)
):
    service = get_mission_control_service()
    return service.get_agent_performance(user.org_id, agent_id)


__all__ = ["router"]
