"""
BMAD API Routes
================

REST API endpoints for the BMAD framework:
- Session management
- Phase control
- Agent messaging
- Approvals
- Git operations
- Deployment triggers
"""

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends
from api.deps import get_current_user, AuthenticatedUser
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

from bmad.session import (
    BMADSession,
    BMADMode,
    BMADPhase,
    PhaseStatus,
    ArtifactType,
    create_session,
    get_session,
    update_session,
    delete_session,
    list_sessions,
)
from bmad.agents import (
    BMADAgentRole,
    BMAD_AGENTS,
    get_agent,
    get_agents_for_phase,
)
from bmad.workflow import (
    BMADWorkflow,
    QuickModeWorkflow,
)
from bmad.vercel import (
    VercelService,
    DeploymentConfig,
    deploy_to_vercel,
)

# Import BMAD bridge components
try:
    from bmad.project_manager import ProjectManager
    from bmad.llm_bridge import BMADLLMBridge
    from bmad.prompt_loader import build_system_prompt

    BMAD_BRIDGE_AVAILABLE = True
except ImportError:
    BMAD_BRIDGE_AVAILABLE = False
    ProjectManager = None
    BMADLLMBridge = None
    build_system_prompt = None

# Module-level singletons
_project_manager = ProjectManager() if BMAD_BRIDGE_AVAILABLE else None
_llm_bridge = BMADLLMBridge() if BMAD_BRIDGE_AVAILABLE else None


router = APIRouter(prefix="/api/bmad", tags=["BMAD"])


# =============================================================================
# Request/Response Models
# =============================================================================


class CreateSessionRequest(BaseModel):
    """Request to create a new BMAD session."""

    project_name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    mode: str = Field(default="guided")
    git_repo: Optional[str] = None
    auto_commit: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StartWorkflowRequest(BaseModel):
    """Request to start or continue a workflow."""

    user_input: str = Field(..., min_length=1, max_length=10000)


class AdvancePhaseRequest(BaseModel):
    """Request to manually advance to a phase."""

    to_phase: Optional[str] = None


class ApprovalRequest(BaseModel):
    """Request to approve or reject."""

    approved: bool
    feedback: Optional[str] = None


class GitCommitRequest(BaseModel):
    """Request to create a git commit."""

    message: str = Field(..., min_length=1, max_length=200)
    files: List[str] = Field(default_factory=list)


class DeployRequest(BaseModel):
    """Request to deploy to Vercel."""

    environment: str = Field(default="production")
    env_vars: Dict[str, str] = Field(default_factory=dict)


class QuickModeRequest(BaseModel):
    """Request for quick mode (bug fixes, small changes)."""

    description: str = Field(..., min_length=1, max_length=5000)
    project_name: str = Field(default="Quick Fix")


class UpdateSessionRequest(BaseModel):
    """Request to update session details."""

    project_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)


class CloneSessionRequest(BaseModel):
    """Request to clone a session."""

    project_name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None


class SessionResponse(BaseModel):
    """Response containing session data."""

    id: str
    project_name: str
    description: str
    mode: str
    current_phase: str
    phases: Dict[str, Dict]
    artifacts_count: int
    pending_approvals: int
    git_enabled: bool
    is_active: bool
    deployment_url: Optional[str]
    created_at: str
    updated_at: str


# =============================================================================
# Session Endpoints
# =============================================================================


@router.post("/sessions", response_model=SessionResponse)
async def create_bmad_session(
    request: CreateSessionRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Create a new BMAD session.

    This initializes a new development project with the specified mode:
    - simple: Quick path for bug fixes (starts at development)
    - guided: Step-by-step wizard (default)
    - expert: Full control dashboard
    - party: Watch agents collaborate
    """
    try:
        mode = BMADMode(request.mode)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {request.mode}")

    # IDOR fix: use authenticated user's ID as the session owner.
    session = create_session(
        project_name=request.project_name,
        description=request.description,
        mode=mode,
        user_id=user.id,
        git_repo=request.git_repo,
        auto_commit=request.auto_commit,
        metadata=request.metadata,
    )

    # Create project folder with git
    if _project_manager:
        try:
            project_path = _project_manager.create_project(
                user_id=session.user_id,
                project_name=request.project_name,
                github_repo_url=request.git_repo,
            )
            session.metadata["project_path"] = str(project_path)
            session.git.enabled = True
            update_session(session)
        except Exception as e:
            logger.warning(f"Failed to create project folder: {e}")

    return _session_to_response(session)


@router.get("/sessions", response_model=List[SessionResponse])
async def list_bmad_sessions(user: AuthenticatedUser = Depends(get_current_user)):
    """
    List all BMAD sessions owned by the authenticated user.

    SECURITY: removed the client-controllable `user_id` query parameter.
    Previously any authenticated user could list another user's sessions
    by passing ?user_id=<victim>.
    """
    sessions = list_sessions(user_id=user.id)
    return [_session_to_response(s) for s in sessions]


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_bmad_session(
    session_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get a BMAD session by ID."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_response(session)


@router.delete("/sessions/{session_id}")
async def delete_bmad_session(
    session_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Delete a BMAD session."""
    if not delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
async def update_bmad_session(
    session_id: str,
    request: UpdateSessionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update session name and/or description."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if request.project_name is not None:
        session.project_name = request.project_name
    if request.description is not None:
        session.description = request.description

    update_session(session)
    return _session_to_response(session)


@router.post("/sessions/{session_id}/clone", response_model=SessionResponse)
async def clone_bmad_session(
    session_id: str,
    request: CloneSessionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Clone a session with a new name, creating a new project folder and git repo."""
    source = get_session(session_id)
    if not source:
        raise HTTPException(status_code=404, detail="Session not found")

    # IDOR fix: new clone is always owned by the authenticated caller,
    # regardless of the source's owner. Prevents low-privilege accounts
    # from "adopting" high-privilege sessions by cloning them.
    new_session = create_session(
        project_name=request.project_name,
        description=(
            request.description
            if request.description is not None
            else source.description
        ),
        mode=source.mode,
        user_id=user.id,
    )

    if _project_manager:
        try:
            project_path = _project_manager.create_project(
                user_id=new_session.user_id,
                project_name=request.project_name,
            )
            new_session.metadata["project_path"] = str(project_path)
            new_session.git.enabled = True
            update_session(new_session)
        except Exception as e:
            logger.warning(f"Failed to create cloned project folder: {e}")

    return _session_to_response(new_session)


@router.post("/sessions/{session_id}/toggle-active", response_model=SessionResponse)
async def toggle_session_active(
    session_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Toggle the is_active flag on a session."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.is_active = not session.is_active
    update_session(session)
    return _session_to_response(session)


def _session_to_response(session: BMADSession) -> SessionResponse:
    """Convert session to response model."""
    return SessionResponse(
        id=session.id,
        project_name=session.project_name,
        description=session.description,
        mode=session.mode.value,
        current_phase=session.current_phase.value,
        phases={p.value: s.to_dict() for p, s in session.phases.items()},
        artifacts_count=len(session.artifacts),
        pending_approvals=len(session.get_pending_approvals()),
        git_enabled=session.git.enabled,
        is_active=session.is_active,
        deployment_url=session.deployment.deployment_url,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


# =============================================================================
# Workflow Endpoints
# =============================================================================


@router.post("/sessions/{session_id}/start")
async def start_workflow(
    session_id: str,
    request: StartWorkflowRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Start or continue the workflow for a session.

    This initiates the BMAD workflow which will:
    1. Process the user input through appropriate agents
    2. Generate artifacts for the current phase
    3. Request approval if needed
    4. Auto-commit to git if enabled
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Add message to session
    session.add_message("user", request.user_input)
    update_session(session)

    # Create workflow
    workflow = BMADWorkflow(session)

    # Run synchronously for now (async streaming via WebSocket)
    result = workflow.run_sync(request.user_input)

    # Update session with results
    session = get_session(session_id)  # Refresh

    return {
        "session_id": session_id,
        "current_phase": session.current_phase.value,
        "phase_status": session.phases[session.current_phase].status.value,
        "artifacts_created": len(result.get("artifacts", [])),
        "pending_approvals": len(session.get_pending_approvals()),
    }


@router.post("/sessions/{session_id}/phase/advance")
async def advance_phase(
    session_id: str,
    request: AdvancePhaseRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Manually advance to the next phase or a specific phase.

    Use this to skip phases or force progression.
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    to_phase = None
    if request.to_phase:
        try:
            to_phase = BMADPhase(request.to_phase)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid phase: {request.to_phase}"
            )

    new_phase = session.advance_phase(to_phase)
    update_session(session)

    return {
        "session_id": session_id,
        "previous_phase": session.current_phase.value,
        "current_phase": new_phase.value,
        "phase_status": session.phases[new_phase].status.value,
    }


# =============================================================================
# Agent Endpoints
# =============================================================================


@router.get("/agents")
async def list_agents(user: AuthenticatedUser = Depends(get_current_user)):
    """List all BMAD agents and their capabilities."""
    return {
        "agents": [agent.to_dict() for agent in BMAD_AGENTS.values()],
        "phases": {
            phase.value: [a.to_dict() for a in get_agents_for_phase(phase)]
            for phase in BMADPhase
        },
    }


@router.get("/agents/{role}")
async def get_agent_info(
    role: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get information about a specific agent."""
    try:
        agent_role = BMADAgentRole(role)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Agent not found: {role}")

    agent = get_agent(agent_role)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {role}")

    return agent.to_dict()


@router.post("/sessions/{session_id}/agents/{role}/message")
async def send_agent_message(
    session_id: str,
    role: str,
    request: StartWorkflowRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Send a message to a specific agent.

    This allows direct interaction with an agent outside the normal workflow.
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        agent_role = BMADAgentRole(role)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Agent not found: {role}")

    agent = get_agent(agent_role)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent not found: {role}")

    # Add message to session
    session.add_message("user", request.user_input, agent=role)
    update_session(session)

    # Generate real response via LLM bridge
    if _llm_bridge and build_system_prompt:
        project_context = (
            f"Project: {session.project_name}\nDescription: {session.description}"
        )
        system_prompt = build_system_prompt(
            agent_role=role,
            phase=session.current_phase.value,
            project_context=project_context,
        )
        bridge_response = _llm_bridge.generate(
            agent_role=role,
            phase=session.current_phase.value,
            prompt=request.user_input,
            system_prompt=system_prompt,
        )
        response = bridge_response.content
    else:
        response = f"[{agent.name}] Received: {request.user_input[:100]}..."

    session.add_message("assistant", response, agent=role)
    update_session(session)

    return {
        "session_id": session_id,
        "agent": role,
        "response": response,
    }


# =============================================================================
# Approval Endpoints
# =============================================================================


@router.get("/sessions/{session_id}/approvals")
async def list_approvals(
    session_id: str,
    status: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all approvals for a session."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    approvals = list(session.approvals.values())

    if status:
        try:
            filter_status = PhaseStatus(status)
            approvals = [a for a in approvals if a.status == filter_status]
        except ValueError:
            pass

    return {
        "session_id": session_id,
        "approvals": [a.to_dict() for a in approvals],
    }


@router.post("/sessions/{session_id}/approvals/{approval_id}/approve")
async def resolve_approval(
    session_id: str,
    approval_id: str,
    request: ApprovalRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Approve or reject an approval request.

    This is a key HITL checkpoint that gates phase transitions.
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        approval = session.resolve_approval(
            approval_id, request.approved, feedback=request.feedback
        )
        update_session(session)

        # Resume workflow if approved
        if request.approved:
            workflow = BMADWorkflow(session)
            workflow.resume(approval_id, request.approved, request.feedback)

        return {
            "session_id": session_id,
            "approval_id": approval_id,
            "status": approval.status.value,
            "phase_can_advance": request.approved,
        }

    except ValueError as e:
        logger.error("Approval resolution failed: %s", e)
        raise HTTPException(status_code=404, detail="Approval not found")


# =============================================================================
# Artifact Endpoints
# =============================================================================


@router.get("/sessions/{session_id}/artifacts")
async def list_artifacts(
    session_id: str,
    phase: Optional[str] = None,
    type: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all artifacts for a session."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    artifacts = list(session.artifacts.values())

    if phase:
        try:
            filter_phase = BMADPhase(phase)
            artifacts = [a for a in artifacts if a.phase == filter_phase]
        except ValueError:
            pass

    if type:
        try:
            filter_type = ArtifactType(type)
            artifacts = [a for a in artifacts if a.type == filter_type]
        except ValueError:
            pass

    return {
        "session_id": session_id,
        "artifacts": [a.to_dict() for a in artifacts],
    }


@router.get("/sessions/{session_id}/artifacts/{artifact_id}")
async def get_artifact(
    session_id: str,
    artifact_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Get a specific artifact."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    artifact = session.artifacts.get(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    return artifact.to_dict()


# =============================================================================
# Git Endpoints
# =============================================================================


@router.get("/sessions/{session_id}/git")
async def get_git_status(
    session_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get git status for a session."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session_id,
        "git": session.git.to_dict(),
    }


@router.post("/sessions/{session_id}/git/commit")
async def create_git_commit(
    session_id: str,
    request: GitCommitRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Create a git commit with the current artifacts.

    This is automatically called at phase completion if auto_commit is enabled.
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.git.enabled:
        raise HTTPException(status_code=400, detail="Git not enabled for this session")

    # Real git commit via ProjectManager
    files = request.files or [a.name for a in session.artifacts.values()]
    sha = None

    if _project_manager:
        # Ownership check: only the session's owner can commit on its behalf.
        if session.user_id != user.id:
            raise HTTPException(status_code=403, detail="Not session owner")
        sha = _project_manager.commit_phase(
            user_id=session.user_id,
            project_name=session.project_name,
            message=request.message,
            files=request.files if request.files else None,
            push=bool(session.git.repo_url),
        )

    if not sha:
        sha = f"no-changes_{datetime.now(timezone.utc).timestamp()}"

    session.record_commit(
        sha=sha,
        message=request.message,
        files=files,
    )
    update_session(session)

    return {
        "session_id": session_id,
        "commit_sha": sha,
        "message": request.message,
        "files_count": len(files),
    }


# =============================================================================
# Deployment Endpoints
# =============================================================================


@router.get("/sessions/{session_id}/deployment")
async def get_deployment_status(
    session_id: str, user: AuthenticatedUser = Depends(get_current_user)
):
    """Get deployment status for a session."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session_id,
        "deployment": session.deployment.to_dict(),
    }


@router.post("/sessions/{session_id}/deploy")
async def deploy_session(
    session_id: str,
    request: DeployRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Deploy the session's code to Vercel.

    Requires VERCEL_TOKEN environment variable to be set.
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Collect code artifacts
    code_artifacts = [
        a for a in session.artifacts.values() if a.type == ArtifactType.CODE
    ]

    if not code_artifacts:
        raise HTTPException(status_code=400, detail="No code artifacts to deploy")

    # Build files dict
    files = {}
    for artifact in code_artifacts:
        if isinstance(artifact.content, dict):
            files.update(artifact.content)
        elif isinstance(artifact.content, str):
            files[artifact.name] = artifact.content

    # Deploy to Vercel
    try:
        config = DeploymentConfig(
            project_name=session.project_name.lower().replace(" ", "-"),
            environment=request.env_vars,
        )

        result = await deploy_to_vercel(
            project_name=config.project_name,
            files=files,
            config=config,
        )

        if result.success:
            session.deployment.status = "deployed"
            session.deployment.deployment_url = result.url
            session.deployment.last_deployment = datetime.now(timezone.utc)
            session.deployment.history.append(
                {
                    "deployment_id": result.deployment_id,
                    "url": result.url,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            update_session(session)

        return {
            "session_id": session_id,
            "success": result.success,
            "deployment_id": result.deployment_id,
            "url": result.url,
            "error": result.error,
        }

    except Exception as e:
        logger.error("Deployment to Vercel failed: %s", e)
        raise HTTPException(status_code=500, detail="Deployment failed")


@router.post("/sessions/{session_id}/rollback")
async def rollback_deployment(
    session_id: str,
    deployment_id: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Rollback to a previous deployment."""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.deployment.history:
        raise HTTPException(status_code=400, detail="No deployment history")

    try:
        service = VercelService()
        result = await service.rollback(
            project_name=session.project_name.lower().replace(" ", "-"),
            deployment_id=deployment_id,
        )
        await service.close()

        return {
            "session_id": session_id,
            "success": result.success,
            "url": result.url,
            "error": result.error,
        }

    except Exception as e:
        logger.error("Deployment rollback failed: %s", e)
        raise HTTPException(status_code=500, detail="Rollback failed")


# =============================================================================
# Quick Mode Endpoints
# =============================================================================


@router.post("/quick")
async def quick_mode(
    request: QuickModeRequest, user: AuthenticatedUser = Depends(get_current_user)
):
    """
    Quick mode for bug fixes and small changes.

    Skips ideation/discovery phases and goes straight to development.
    """
    session = create_session(
        project_name=request.project_name,
        description=request.description,
        mode=BMADMode.SIMPLE,
        user_id=user.id,
    )

    workflow = QuickModeWorkflow(session)

    # Run workflow
    events = []
    async for event in workflow.run(request.description):
        events.append(event.to_dict())

    return {
        "session_id": session.id,
        "events": events,
        "current_phase": session.current_phase.value,
    }


# =============================================================================
# WebSocket for Real-time Updates
# =============================================================================


@router.websocket("/sessions/{session_id}/stream")
async def websocket_stream(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for real-time workflow updates.

    Streams WorkflowEvent objects as JSON.
    """
    session = get_session(session_id)
    if not session:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()

    workflow = BMADWorkflow(session)

    try:
        # Wait for start message
        start_msg = await websocket.receive_json()
        user_input = start_msg.get("user_input", "")

        if not user_input:
            await websocket.send_json({"error": "user_input required"})
            await websocket.close()
            return

        # Stream events
        async for event in workflow.run(user_input, stream_events=True):
            await websocket.send_json(event.to_dict())

        await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e}")
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# =============================================================================
# Phase Information
# =============================================================================


@router.get("/phases")
async def list_phases(user: AuthenticatedUser = Depends(get_current_user)):
    """Get information about all workflow phases."""
    from bmad.workflow import (
        PHASE_ENTRY_CRITERIA,
        PHASE_EXIT_CRITERIA,
        PHASE_COMMIT_MESSAGES,
    )

    phases = []
    for phase in BMADPhase:
        agents = get_agents_for_phase(phase)
        phases.append(
            {
                "phase": phase.value,
                "agents": [a.to_dict() for a in agents],
                "entry_criteria": PHASE_ENTRY_CRITERIA.get(phase, []),
                "exit_criteria": PHASE_EXIT_CRITERIA.get(phase, []),
                "commit_message": PHASE_COMMIT_MESSAGES.get(phase, ""),
            }
        )

    return {"phases": phases}
