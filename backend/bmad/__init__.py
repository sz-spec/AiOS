"""
BMAD Framework (Business-driven Multi-Agent Development)
=========================================================

Full-lifecycle development platform transforming VOS3 from a code generator
into a complete development environment with:

- 15 specialized agent personas covering ideation → operations
- Human-in-the-Loop observation and intervention at every step
- Auto-commit to Git at each phase completion
- Vercel deployment integration
- Simple guided UI with expert drill-down capability

Usage:
    from bmad import BMADSession, BMADWorkflow, BMADMode

    # Create a new session
    session = BMADSession.create(
        project_name="My App",
        mode=BMADMode.GUIDED
    )

    # Start the workflow
    workflow = BMADWorkflow(session)
    async for event in workflow.run("Build a todo app with authentication"):
        print(event)

Components:
    - agents.py: 15 BMAD agent definitions
    - session.py: Session state management
    - workflow.py: Phase orchestration with LangGraph
    - vercel.py: Vercel API integration
    - project_manager.py: Project folder + Git management
    - llm_bridge.py: Smart Router ↔ LLM bridge
    - prompt_loader.py: BMAD persona YAML loader
"""

from bmad.session import (
    BMADSession,
    BMADMode,
    BMADPhase,
    SessionState,
    PhaseStatus,
    Artifact,
    Approval,
    create_session,
    get_session,
    update_session,
    delete_session,
)

from bmad.agents import (
    BMADAgent,
    BMADAgentRole,
    BMAD_AGENTS,
    get_agent,
    get_agents_for_phase,
)

from bmad.workflow import (
    BMADWorkflow,
    WorkflowEvent,
    WorkflowEventType,
)

from bmad.vercel import (
    VercelService,
    DeploymentResult,
    deploy_to_vercel,
)

from bmad.project_manager import ProjectManager
from bmad.llm_bridge import BMADLLMBridge
from bmad.prompt_loader import load_agent_persona, build_system_prompt

__all__ = [
    # Session
    "BMADSession",
    "BMADMode",
    "BMADPhase",
    "SessionState",
    "PhaseStatus",
    "Artifact",
    "Approval",
    "create_session",
    "get_session",
    "update_session",
    "delete_session",
    # Agents
    "BMADAgent",
    "BMADAgentRole",
    "BMAD_AGENTS",
    "get_agent",
    "get_agents_for_phase",
    # Workflow
    "BMADWorkflow",
    "WorkflowEvent",
    "WorkflowEventType",
    # Vercel
    "VercelService",
    "DeploymentResult",
    "deploy_to_vercel",
    # Project Management
    "ProjectManager",
    # LLM Bridge
    "BMADLLMBridge",
    # Prompt Loader
    "load_agent_persona",
    "build_system_prompt",
]
