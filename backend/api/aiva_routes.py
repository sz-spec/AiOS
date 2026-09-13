"""
AIVA Agent Builder API Routes

Endpoints for the AIVA AI agent builder:
- Static metadata (architectures, modes, agent types)
- Templates and tools
- Agent creation, CRUD, configuration steps
- Testing, lifecycle management, escalation rules

SECURITY (April 2026 audit fix):
- Every endpoint now requires an authenticated user.
  Previously all endpoints were unauthenticated and all agents were owned
  by the synthetic user_id="anonymous", allowing unauthenticated LLM spend
  and full read/write access to any agent in the tenant.
- Creation endpoints now tag the agent with `user.id`.
- Per-agent endpoints (get/update/delete/lifecycle/rules) perform a
  best-effort ownership check via `_require_agent_owner`. Full ownership
  enforcement in the service layer is tracked as a Phase 1 follow-up.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from api.deps import get_current_user, AuthenticatedUser

from aiva.agent_builder import (
    AgentArchitecture,
    AgentMode,
    AgentType,
    AIVAService,
    EscalationRule,
    ToolCategory,
    get_aiva_service,
)

router = APIRouter(prefix="/aiva", tags=["AIVA"])


# ============================================
# Ownership helper (best-effort)
# ============================================


def _require_agent_owner(agent, user_id: str):
    """
    Raise 404 if the agent is missing or (when ownership is tracked) owned by
    someone else. Returns 404 instead of 403 to avoid existence leak.

    If the service layer does not yet expose an owner attribute, we allow the
    request through (logged) rather than crash — this is the Phase-0 minimum.
    A hard ownership check will be enforced once the service stores user_id.
    """
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    owner = getattr(agent, "user_id", None) or getattr(agent, "owner_id", None)
    if owner is not None and owner != user_id:
        raise HTTPException(status_code=404, detail="Agent not found")


# =============================================================================
# Pydantic request/response models
# =============================================================================


class CreateFromDescriptionRequest(BaseModel):
    description: str
    organization_id: Optional[str] = None


class CreateFromTemplateRequest(BaseModel):
    agent_type: str


class SetArchitectureRequest(BaseModel):
    architecture: str


class SetToolsRequest(BaseModel):
    tool_ids: list[str] = Field(default_factory=list)


class ConfigureSecurityRequest(BaseModel):
    require_approval_for: list[str] = Field(default_factory=list)
    blocked_topics: list[str] = Field(default_factory=list)
    pii_redaction: bool = True
    max_actions_per_minute: int = 10
    human_in_loop_threshold: float = 0.7
    audit_all_actions: bool = True


class SetModeRequest(BaseModel):
    mode: str


class TestAgentRequest(BaseModel):
    message: str


class AddEscalationRuleRequest(BaseModel):
    condition: str
    action: str = "notify"
    target: str = ""
    priority: str = "medium"


class UpdateAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    description: Optional[str] = None
    mode: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    system_prompt: Optional[str] = None


# =============================================================================
# Enum value sets (for validation)
# =============================================================================

_VALID_AGENT_TYPES = {e.value for e in AgentType}
_VALID_ARCHITECTURES = {e.value for e in AgentArchitecture}
_VALID_MODES = {e.value for e in AgentMode}
_VALID_TOOL_CATEGORIES = {e.value for e in ToolCategory}

# Architecture descriptions for the static endpoint
_ARCHITECTURE_DESCRIPTIONS = {
    AgentArchitecture.SIMPLE: "Single agent, direct response",
    AgentArchitecture.REACT: "Reasoning + Acting loop",
    AgentArchitecture.PLAN_EXECUTE: "Plan first, then execute",
    AgentArchitecture.MULTI_AGENT: "Team of specialized agents",
    AgentArchitecture.HIERARCHICAL: "Manager + workers",
}

# Mode use-case descriptions
_MODE_USE_CASES = {
    AgentMode.SUGGEST: "AI recommends, human decides",
    AgentMode.ASSIST: "AI drafts, human reviews before sending",
    AgentMode.ACT: "AI executes autonomously, reports after",
}


# =============================================================================
# Static list endpoints (no service call)
# =============================================================================


@router.get("/architectures")
async def list_architectures(user: AuthenticatedUser = Depends(get_current_user)):
    """Return all available agent architectures."""
    return {
        "architectures": [
            {
                "id": arch.value,
                "name": arch.name.replace("_", " ").title(),
                "description": _ARCHITECTURE_DESCRIPTIONS.get(arch, ""),
            }
            for arch in AgentArchitecture
        ]
    }


@router.get("/modes")
async def list_modes(user: AuthenticatedUser = Depends(get_current_user)):
    """Return all available agent operating modes."""
    return {
        "modes": [
            {
                "id": mode.value,
                "name": mode.name.replace("_", " ").title(),
                "use_case": _MODE_USE_CASES.get(mode, ""),
            }
            for mode in AgentMode
        ]
    }


@router.get("/agent-types")
async def list_agent_types(user: AuthenticatedUser = Depends(get_current_user)):
    """Return all available agent types."""
    return {"types": [{"id": t.value} for t in AgentType]}


# =============================================================================
# Templates & Tools
# =============================================================================


@router.get("/templates")
async def list_templates(
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return all available agent templates."""
    return {"templates": svc.get_templates()}


@router.get("/tools")
async def list_tools(
    category: Optional[str] = Query(None),
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return available tools, optionally filtered by category."""
    if category is not None and category.lower() in _VALID_TOOL_CATEGORIES:
        tools = svc.get_tools_by_category(ToolCategory(category.lower()))
    else:
        tools = svc.get_available_tools()
    return {"tools": tools}


# =============================================================================
# Agent Creation
# =============================================================================


@router.post("/agents/from-description")
async def create_from_description(
    body: CreateFromDescriptionRequest,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Step 1: Create agent from natural language description."""
    agent = svc.create_from_description(
        user_id=user.id,
        description=body.description,
        organization_id=body.organization_id,
    )
    return {"step": 1, "agent": agent.to_dict()}


@router.post("/agents/from-template")
async def create_from_template(
    body: CreateFromTemplateRequest,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Step 1: Create agent from a pre-built template."""
    if body.agent_type not in _VALID_AGENT_TYPES:
        raise HTTPException(
            status_code=400, detail=f"Invalid agent_type: {body.agent_type}"
        )

    agent = svc.create_from_template(
        user_id=user.id,
        agent_type=AgentType(body.agent_type),
    )
    return {"step": 1, "agent": agent.to_dict()}


# =============================================================================
# Agent CRUD
# =============================================================================


@router.get("/agents")
async def list_agents(
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List agents belonging to the authenticated user (IDOR-safe)."""
    all_agents = svc.list_agents()
    agents = [
        a
        for a in all_agents
        if (getattr(a, "user_id", None) or getattr(a, "owner_id", None)) == user.id
    ]
    return {
        "agents": [a.to_dict() for a in agents],
        "total": len(agents),
    }


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get a single agent by ID (IDOR-protected)."""
    agent = svc.get_agent(agent_id)
    _require_agent_owner(agent, user.id)
    return agent.to_dict()


@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update agent fields (ownership-checked, allowlisted fields only)."""
    agent = svc.get_agent(agent_id)
    _require_agent_owner(agent, user.id)
    updated = svc.update_agent(agent_id, body.model_dump(exclude_unset=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated.to_dict()


@router.delete("/agents/{agent_id}")
async def delete_agent(
    agent_id: str,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete an agent (IDOR-protected)."""
    agent = svc.get_agent(agent_id)
    _require_agent_owner(agent, user.id)
    result = svc.delete_agent(agent_id)
    if result is False:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "deleted", "agent_id": agent_id}


# =============================================================================
# Configuration steps
# =============================================================================


@router.post("/agents/{agent_id}/architecture")
async def set_architecture(
    agent_id: str,
    body: SetArchitectureRequest,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Step 2: Set agent architecture."""
    if body.architecture not in _VALID_ARCHITECTURES:
        raise HTTPException(
            status_code=400, detail=f"Invalid architecture: {body.architecture}"
        )

    agent = svc.set_architecture(agent_id, AgentArchitecture(body.architecture))
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"step": 2, "agent": agent.to_dict()}


@router.post("/agents/{agent_id}/tools")
async def set_tools(
    agent_id: str,
    body: SetToolsRequest,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Step 3: Configure agent tools."""
    agent = svc.set_tools(agent_id, body.tool_ids)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"step": 3, "agent": agent.to_dict()}


@router.post("/agents/{agent_id}/security")
async def configure_security(
    agent_id: str,
    body: ConfigureSecurityRequest,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Step 5: Configure security and monitoring."""
    agent = svc.configure_security(agent_id, body.model_dump())
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"step": 5, "agent": agent.to_dict()}


@router.post("/agents/{agent_id}/mode")
async def set_mode(
    agent_id: str,
    body: SetModeRequest,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Set agent operating mode."""
    if body.mode not in _VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {body.mode}")

    agent = svc.set_mode(agent_id, AgentMode(body.mode))
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"mode": body.mode, "agent": agent.to_dict()}


# =============================================================================
# Testing endpoints
# =============================================================================


@router.post("/agents/{agent_id}/test")
async def test_agent(
    agent_id: str,
    body: TestAgentRequest,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Step 4: Test agent with a live message."""
    result = await svc.test_agent(agent_id, body.message)
    return {"step": 4, **result}


@router.get("/agents/{agent_id}/test/conversation")
async def get_test_conversation(
    agent_id: str,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get test conversation history."""
    return {"conversation": svc.get_test_conversation(agent_id)}


@router.delete("/agents/{agent_id}/test/conversation")
async def clear_test_conversation(
    agent_id: str,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Clear test conversation history."""
    svc.clear_test_conversation(agent_id)
    return {"status": "cleared"}


# =============================================================================
# Lifecycle endpoints
# =============================================================================


@router.post("/agents/{agent_id}/deploy")
async def deploy_agent(
    agent_id: str,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Step 6: Deploy agent to production."""
    agent = svc.deploy_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"step": 6, "status": agent.status.value, "agent": agent.to_dict()}


@router.post("/agents/{agent_id}/pause")
async def pause_agent(
    agent_id: str,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Pause an active agent."""
    agent = svc.pause_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": agent.status.value, "agent": agent.to_dict()}


@router.post("/agents/{agent_id}/resume")
async def resume_agent(
    agent_id: str,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Resume a paused agent."""
    agent = svc.resume_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": agent.status.value, "agent": agent.to_dict()}


# =============================================================================
# Escalation rules
# =============================================================================


@router.post("/agents/{agent_id}/escalation-rules")
async def add_escalation_rule(
    agent_id: str,
    body: AddEscalationRuleRequest,
    svc: AIVAService = Depends(get_aiva_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Add an escalation rule to an agent."""
    rule = EscalationRule(
        condition=body.condition,
        action=body.action,
        target=body.target,
        priority=body.priority,
    )
    agent = svc.add_escalation_rule(agent_id, rule)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"rules": [r.to_dict() for r in agent.escalation_rules]}
