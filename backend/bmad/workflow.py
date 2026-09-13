"""
BMAD Workflow Engine
=====================

LangGraph-based workflow orchestration for BMAD phases:
- Phase-based routing with entry/exit criteria
- HITL checkpoints between phases
- Auto-commit on phase completion
- Real-time event streaming
"""

from typing import Dict, List, Optional, Any, AsyncGenerator, TypedDict, Annotated
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone
import asyncio
import json

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages

# Import HITL components
try:
    from langgraph.types import Command, interrupt
    from langgraph.checkpoint.memory import MemorySaver

    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False

    class Command:
        def __init__(self, goto=None, update=None, resume=None):
            self.goto = goto
            self.update = update or {}

    def interrupt(message: str):
        return None

    MemorySaver = None

from bmad.session import (
    BMADSession,
    BMADPhase,
    BMADMode,
    PhaseStatus,
    update_session,
)
from bmad.agents import (
    BMADAgent,
    get_agents_for_phase,
    get_primary_agent_for_phase,
)

# Import LLM if available
try:
    from ai.llm.providers import LLM

    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    LLM = None

# Import BMAD bridge components
try:
    from bmad.llm_bridge import BMADLLMBridge
    from bmad.prompt_loader import build_system_prompt
    from bmad.project_manager import ProjectManager

    BMAD_BRIDGE_AVAILABLE = True
except ImportError:
    BMAD_BRIDGE_AVAILABLE = False
    BMADLLMBridge = None
    build_system_prompt = None
    ProjectManager = None


# =============================================================================
# Event Types
# =============================================================================


class WorkflowEventType(str, Enum):
    """Types of events emitted by the workflow."""

    SESSION_STARTED = "session_started"
    PHASE_STARTED = "phase_started"
    PHASE_COMPLETED = "phase_completed"
    AGENT_STARTED = "agent_started"
    AGENT_MESSAGE = "agent_message"
    AGENT_COMPLETED = "agent_completed"
    ARTIFACT_CREATED = "artifact_created"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    GIT_COMMIT = "git_commit"
    DEPLOYMENT_STARTED = "deployment_started"
    DEPLOYMENT_COMPLETED = "deployment_completed"
    ERROR = "error"
    WORKFLOW_COMPLETED = "workflow_completed"


@dataclass
class WorkflowEvent:
    """Event emitted during workflow execution."""

    type: WorkflowEventType
    session_id: str
    phase: Optional[BMADPhase]
    agent: Optional[str]
    data: Dict[str, Any]
    timestamp: datetime

    def to_dict(self) -> Dict:
        return {
            "type": self.type.value,
            "session_id": self.session_id,
            "phase": self.phase.value if self.phase else None,
            "agent": self.agent,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# =============================================================================
# Workflow State
# =============================================================================


class WorkflowState(TypedDict):
    """State for the BMAD workflow graph."""

    messages: Annotated[List[BaseMessage], add_messages]
    session_id: str
    current_phase: str
    phase_status: str
    active_agent: Optional[str]
    pending_approval: Optional[str]
    artifacts: List[Dict]
    error: Optional[str]
    user_input: Optional[str]
    iteration: int


# =============================================================================
# Phase Entry/Exit Criteria
# =============================================================================

PHASE_ENTRY_CRITERIA: Dict[BMADPhase, List[str]] = {
    BMADPhase.IDEATION: [],  # No prerequisites
    BMADPhase.DISCOVERY: ["vision_approved"],
    BMADPhase.PLANNING: ["prd_approved"],
    BMADPhase.DESIGN: ["architecture_approved"],
    BMADPhase.DEVELOPMENT: ["design_specs_approved"],
    BMADPhase.TESTING: ["code_compiles"],
    BMADPhase.REVIEW: ["tests_pass"],
    BMADPhase.DEPLOYMENT: ["review_approved"],
    BMADPhase.OPERATIONS: ["deployment_successful"],
}

PHASE_EXIT_CRITERIA: Dict[BMADPhase, List[str]] = {
    BMADPhase.IDEATION: ["vision_created"],
    BMADPhase.DISCOVERY: ["prd_created"],
    BMADPhase.PLANNING: ["architecture_created", "sprint_planned"],
    BMADPhase.DESIGN: ["design_specs_created"],
    BMADPhase.DEVELOPMENT: ["code_complete"],
    BMADPhase.TESTING: ["tests_pass", "security_review_complete"],
    BMADPhase.REVIEW: ["review_approved"],
    BMADPhase.DEPLOYMENT: ["deployment_successful"],
    BMADPhase.OPERATIONS: [],  # Ongoing
}

PHASE_COMMIT_MESSAGES: Dict[BMADPhase, str] = {
    BMADPhase.IDEATION: "feat(ideation): vision and project scope",
    BMADPhase.DISCOVERY: "feat(discovery): PRD and requirements",
    BMADPhase.PLANNING: "feat(planning): architecture and sprint plan",
    BMADPhase.DESIGN: "feat(design): detailed specifications",
    BMADPhase.DEVELOPMENT: "feat(dev): implementation",
    BMADPhase.TESTING: "test: test suite and security review",
    BMADPhase.REVIEW: "refactor: code review feedback",
    BMADPhase.DEPLOYMENT: "chore: deployment configuration",
    BMADPhase.OPERATIONS: "chore: operational updates",
}


# =============================================================================
# BMAD Workflow
# =============================================================================


class BMADWorkflow:
    """
    BMAD workflow orchestrator using LangGraph.

    Manages the full development lifecycle from ideation to operations
    with human-in-the-loop checkpoints and auto-commit.
    """

    def __init__(self, session: BMADSession = None, llm: Any = None):
        self.session = session
        self.llm = llm
        self._graph = None
        self._checkpointer = None
        self._event_queue: asyncio.Queue = None

        # Initialize BMAD bridge for real LLM calls
        self._bridge = BMADLLMBridge() if BMAD_BRIDGE_AVAILABLE else None
        self._project_manager = (
            ProjectManager() if BMAD_BRIDGE_AVAILABLE and ProjectManager else None
        )

        if LANGGRAPH_AVAILABLE and MemorySaver:
            self._checkpointer = MemorySaver()

        if session:
            self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph workflow."""
        workflow = StateGraph(WorkflowState)

        # Add phase nodes
        for phase in BMADPhase:
            workflow.add_node(phase.value, self._create_phase_node(phase))

        # Add utility nodes
        workflow.add_node("approval_gate", self._approval_gate_node)
        workflow.add_node("git_commit", self._git_commit_node)
        workflow.add_node("finalize", self._finalize_node)

        # Entry point
        start_phase = self.session.current_phase if self.session else BMADPhase.IDEATION
        workflow.add_edge(START, start_phase.value)

        # Phase transitions with approval gates
        phases = list(BMADPhase)
        for i, phase in enumerate(phases[:-1]):
            next_phase = phases[i + 1]
            # Phase -> Approval -> Git Commit -> Next Phase
            workflow.add_conditional_edges(
                phase.value,
                self._route_after_phase,
                {
                    "approval": "approval_gate",
                    "next": next_phase.value,
                    "end": "finalize",
                },
            )

        # Approval gate routing
        workflow.add_conditional_edges(
            "approval_gate",
            self._route_after_approval,
            {
                "approved": "git_commit",
                "rejected": self._get_current_phase_value(),
                "waiting": END,
            },
        )

        # Git commit -> Next phase
        workflow.add_conditional_edges(
            "git_commit",
            self._route_after_commit,
            {phase.value: phase.value for phase in BMADPhase} | {"end": "finalize"},
        )

        # Last phase -> Finalize
        workflow.add_edge(BMADPhase.OPERATIONS.value, "finalize")
        workflow.add_edge("finalize", END)

        self._graph = workflow.compile(checkpointer=self._checkpointer)
        return self._graph

    def _get_current_phase_value(self) -> str:
        """Get current phase value for routing."""
        if self.session:
            return self.session.current_phase.value
        return BMADPhase.IDEATION.value

    # -------------------------------------------------------------------------
    # Phase Node Factory
    # -------------------------------------------------------------------------

    def _create_phase_node(self, phase: BMADPhase):
        """Create a node function for a phase."""

        async def phase_node(state: WorkflowState) -> Dict:
            # Emit phase started event
            await self._emit_event(
                WorkflowEventType.PHASE_STARTED,
                {
                    "phase": phase.value,
                },
            )

            # Get agents for this phase
            get_agents_for_phase(phase)
            primary_agent = get_primary_agent_for_phase(phase)

            results = {
                "current_phase": phase.value,
                "phase_status": PhaseStatus.IN_PROGRESS.value,
                "active_agent": primary_agent.role.value if primary_agent else None,
                "artifacts": [],
            }

            # Execute primary agent
            if primary_agent and self.llm:
                try:
                    agent_result = await self._execute_agent(
                        primary_agent, state, phase
                    )
                    results.update(agent_result)
                except Exception as e:
                    results["error"] = str(e)
                    await self._emit_event(
                        WorkflowEventType.ERROR,
                        {
                            "phase": phase.value,
                            "agent": primary_agent.role.value,
                            "error": str(e),
                        },
                    )

            # Check exit criteria
            exit_met = self._check_exit_criteria(phase, results)

            if exit_met:
                results["phase_status"] = PhaseStatus.AWAITING_APPROVAL.value
            else:
                results["phase_status"] = PhaseStatus.IN_PROGRESS.value

            return results

        return phase_node

    async def _execute_agent(
        self, agent: BMADAgent, state: WorkflowState, phase: BMADPhase
    ) -> Dict:
        """Execute an agent with real LLM calls via BMAD bridge."""
        await self._emit_event(
            WorkflowEventType.AGENT_STARTED,
            {
                "agent": agent.role.value,
                "name": agent.name,
            },
        )

        # Build context from state and previous artifacts
        state.get("messages", [])
        user_input = state.get("user_input", "")

        # Gather previous artifact summaries for context
        previous_artifacts = ""
        if self.session:
            for art in self.session.artifacts.values():
                if art.phase != phase:
                    previous_artifacts += f"\n### {art.name} ({art.phase.value})\n{str(art.content)[:500]}\n"

        project_context = f"""Project: {self.session.project_name if self.session else 'Unknown'}
Description: {self.session.description if self.session else user_input}
Current Phase: {phase.value}
Mode: {self.session.mode.value if self.session else 'guided'}
"""
        if previous_artifacts:
            project_context += f"\n## Previous Artifacts\n{previous_artifacts}"

        prompt = f"""User Request: {user_input}

Please produce the appropriate artifact for the {phase.value} phase.
Build on any previous artifacts provided in your context."""

        # Use BMAD bridge for real LLM call
        if self._bridge:
            system_prompt = (
                build_system_prompt(
                    agent_role=agent.role.value,
                    phase=phase.value,
                    project_context=project_context,
                )
                if build_system_prompt
                else agent.system_prompt
            )

            response = self._bridge.generate(
                agent_role=agent.role.value,
                phase=phase.value,
                prompt=prompt,
                system_prompt=system_prompt,
            )
            content = response.content
        elif LLM_AVAILABLE and self.llm:
            # Fallback to direct LLM
            llm_response = self.llm.generate(prompt, system=agent.system_prompt)
            content = (
                llm_response.content
                if hasattr(llm_response, "content")
                else str(llm_response)
            )
        else:
            content = f"[{agent.name}] Processing {phase.value} phase...\n\nProject: {self.session.project_name if self.session else 'Unknown'}\nInput: {user_input[:200]}"

        await self._emit_event(
            WorkflowEventType.AGENT_MESSAGE,
            {
                "agent": agent.role.value,
                "content": content,
            },
        )

        # Create artifact from response
        artifact = self._create_artifact_from_response(agent, content, phase)

        # Write artifact to project disk
        if artifact and self.session and self._project_manager:
            user_id = getattr(self.session, "user_id", None) or "1"
            artifact_filename = self._get_artifact_filename(phase, agent)
            try:
                self._project_manager.write_artifact(
                    user_id=user_id,
                    project_name=self.session.project_name,
                    file_path=artifact_filename,
                    content=content,
                )
                if artifact:
                    artifact["file_path"] = artifact_filename
            except Exception as e:
                await self._emit_event(
                    WorkflowEventType.ERROR,
                    {
                        "phase": phase.value,
                        "agent": agent.role.value,
                        "error": f"Failed to write artifact: {e}",
                    },
                )

        # Store artifact in session
        if artifact and self.session:
            from bmad.session import ArtifactType

            try:
                artifact_type = ArtifactType(artifact["type"])
                self.session.add_artifact(
                    type=artifact_type,
                    name=artifact["name"],
                    content=content,
                    agent=agent.role.value,
                )
                update_session(self.session)
            except Exception:
                pass

        await self._emit_event(
            WorkflowEventType.AGENT_COMPLETED,
            {
                "agent": agent.role.value,
                "artifact_id": artifact.get("id") if artifact else None,
            },
        )

        await self._emit_event(
            WorkflowEventType.ARTIFACT_CREATED,
            {
                "phase": phase.value,
                "agent": agent.role.value,
                "artifact": artifact,
            },
        )

        return {
            "messages": [AIMessage(content=content, name=agent.name)],
            "artifacts": [artifact] if artifact else [],
        }

    def _get_artifact_filename(self, phase: BMADPhase, agent: BMADAgent) -> str:
        """Determine the file path for an artifact based on phase."""
        phase_to_dir = {
            BMADPhase.IDEATION: "docs/planning-artifacts",
            BMADPhase.DISCOVERY: "docs/planning-artifacts",
            BMADPhase.PLANNING: "docs/planning-artifacts",
            BMADPhase.DESIGN: "docs/planning-artifacts",
            BMADPhase.DEVELOPMENT: "docs/implementation-artifacts",
            BMADPhase.TESTING: "docs/implementation-artifacts",
            BMADPhase.REVIEW: "docs/implementation-artifacts",
            BMADPhase.DEPLOYMENT: "docs/implementation-artifacts",
            BMADPhase.OPERATIONS: "docs/implementation-artifacts",
        }
        phase_to_filename = {
            BMADPhase.IDEATION: "product-brief.md",
            BMADPhase.DISCOVERY: "prd.md",
            BMADPhase.PLANNING: "architecture.md",
            BMADPhase.DESIGN: "design-spec.md",
            BMADPhase.DEVELOPMENT: "implementation-notes.md",
            BMADPhase.TESTING: "test-report.md",
            BMADPhase.REVIEW: "code-review.md",
            BMADPhase.DEPLOYMENT: "deployment-config.md",
            BMADPhase.OPERATIONS: "operations-runbook.md",
        }
        directory = phase_to_dir.get(phase, "docs")
        filename = phase_to_filename.get(phase, f"{phase.value}-{agent.role.value}.md")
        return f"{directory}/{filename}"

    def _create_artifact_from_response(
        self, agent: BMADAgent, content: str, phase: BMADPhase
    ) -> Optional[Dict]:
        """Create an artifact from agent response."""
        if not agent.artifacts:
            return None

        artifact_type = agent.artifacts[0]  # Primary artifact type

        return {
            "id": f"art_{phase.value}_{agent.role.value}",
            "type": artifact_type.value,
            "name": f"{phase.value.title()} - {agent.name}",
            "content": content,
            "phase": phase.value,
            "agent": agent.role.value,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _check_exit_criteria(self, phase: BMADPhase, results: Dict) -> bool:
        """Check if phase exit criteria are met."""
        criteria = PHASE_EXIT_CRITERIA.get(phase, [])

        # For now, consider criteria met if we have artifacts
        artifacts = results.get("artifacts", [])
        return len(artifacts) > 0 or not criteria

    # -------------------------------------------------------------------------
    # Routing Functions
    # -------------------------------------------------------------------------

    def _route_after_phase(self, state: WorkflowState) -> str:
        """Route after phase completion."""
        status = state.get("phase_status", "")
        error = state.get("error")

        if error:
            return "end"

        if status == PhaseStatus.AWAITING_APPROVAL.value:
            return "approval"

        return "next"

    def _route_after_approval(self, state: WorkflowState) -> str:
        """Route after approval gate."""
        status = state.get("phase_status", "")

        if status == PhaseStatus.APPROVED.value:
            return "approved"
        elif status == PhaseStatus.REJECTED.value:
            return "rejected"
        else:
            return "waiting"

    def _route_after_commit(self, state: WorkflowState) -> str:
        """Route after git commit."""
        current = state.get("current_phase", "")

        # Get next phase
        phases = list(BMADPhase)
        try:
            current_phase = BMADPhase(current)
            current_idx = phases.index(current_phase)
            if current_idx < len(phases) - 1:
                return phases[current_idx + 1].value
        except (ValueError, IndexError):
            pass

        return "end"

    # -------------------------------------------------------------------------
    # Utility Nodes
    # -------------------------------------------------------------------------

    async def _approval_gate_node(self, state: WorkflowState) -> Dict:
        """Gate node that waits for human approval."""
        phase = state.get("current_phase", "")
        artifacts = state.get("artifacts", [])

        # Emit approval request
        await self._emit_event(
            WorkflowEventType.APPROVAL_REQUESTED,
            {
                "phase": phase,
                "artifacts": artifacts,
                "message": f"Approve {phase} phase completion?",
            },
        )

        # In a real implementation, this would use interrupt()
        if LANGGRAPH_AVAILABLE:
            try:
                response = interrupt(f"Approve {phase} phase?")
                if response and str(response).lower() in ["yes", "y", "approve"]:
                    return {"phase_status": PhaseStatus.APPROVED.value}
                else:
                    return {"phase_status": PhaseStatus.REJECTED.value}
            except Exception:
                pass

        # Auto-approve in dev mode
        return {"phase_status": PhaseStatus.APPROVED.value}

    async def _git_commit_node(self, state: WorkflowState) -> Dict:
        """Node that creates a real git commit for the phase."""
        phase = state.get("current_phase", "")
        artifacts = state.get("artifacts", [])

        commit_message = PHASE_COMMIT_MESSAGES.get(
            BMADPhase(phase), f"chore({phase}): phase completion"
        )

        sha = None
        files = [a.get("file_path", a.get("id", "")) for a in artifacts]

        # Real git commit via ProjectManager
        if self._project_manager and self.session:
            user_id = getattr(self.session, "user_id", None) or "1"
            try:
                sha = self._project_manager.commit_phase(
                    user_id=user_id,
                    project_name=self.session.project_name,
                    message=commit_message,
                    push=bool(self.session.git.repo_url),
                )
            except Exception as e:
                await self._emit_event(
                    WorkflowEventType.ERROR,
                    {
                        "phase": phase,
                        "error": f"Git commit failed: {e}",
                    },
                )

        # Record commit in session
        if sha and self.session:
            self.session.record_commit(sha, commit_message, files)
            update_session(self.session)

        # Emit git commit event
        await self._emit_event(
            WorkflowEventType.GIT_COMMIT,
            {
                "phase": phase,
                "message": commit_message,
                "sha": sha,
                "files": files,
            },
        )

        return {
            "phase_status": PhaseStatus.COMPLETED.value,
        }

    async def _finalize_node(self, state: WorkflowState) -> Dict:
        """Finalize the workflow."""
        await self._emit_event(
            WorkflowEventType.WORKFLOW_COMPLETED,
            {
                "iterations": state.get("iteration", 0),
                "artifacts_count": len(state.get("artifacts", [])),
            },
        )

        return {"phase_status": "completed"}

    # -------------------------------------------------------------------------
    # Event Emission
    # -------------------------------------------------------------------------

    async def _emit_event(self, event_type: WorkflowEventType, data: Dict[str, Any]):
        """Emit a workflow event."""
        event = WorkflowEvent(
            type=event_type,
            session_id=self.session.id if self.session else "unknown",
            phase=BMADPhase(data.get("phase")) if data.get("phase") else None,
            agent=data.get("agent"),
            data=data,
            timestamp=datetime.now(timezone.utc),
        )

        if self._event_queue:
            await self._event_queue.put(event)

    # -------------------------------------------------------------------------
    # Execution Methods
    # -------------------------------------------------------------------------

    async def run(
        self, user_input: str, stream_events: bool = True
    ) -> AsyncGenerator[WorkflowEvent, None]:
        """
        Run the workflow with the given user input.

        Args:
            user_input: The user's project description/request
            stream_events: Whether to yield events as they occur

        Yields:
            WorkflowEvent objects as the workflow progresses
        """
        if not self._graph:
            self._build_graph()

        self._event_queue = asyncio.Queue()

        # Initial state
        initial_state: WorkflowState = {
            "messages": [HumanMessage(content=user_input)],
            "session_id": self.session.id if self.session else "temp",
            "current_phase": (
                self.session.current_phase.value
                if self.session
                else BMADPhase.IDEATION.value
            ),
            "phase_status": PhaseStatus.PENDING.value,
            "active_agent": None,
            "pending_approval": None,
            "artifacts": [],
            "error": None,
            "user_input": user_input,
            "iteration": 0,
        }

        # Emit session started
        await self._emit_event(
            WorkflowEventType.SESSION_STARTED,
            {
                "project_name": (
                    self.session.project_name if self.session else "New Project"
                ),
                "mode": (
                    self.session.mode.value if self.session else BMADMode.GUIDED.value
                ),
            },
        )

        if stream_events:
            # Yield events as they are emitted
            while not self._event_queue.empty():
                event = await self._event_queue.get()
                yield event

        # Run the graph
        config = {
            "configurable": {"thread_id": self.session.id if self.session else "temp"}
        }

        try:
            async for step in self._graph.astream(initial_state, config):
                # Process step
                if stream_events and self._event_queue:
                    while not self._event_queue.empty():
                        event = await self._event_queue.get()
                        yield event
        except Exception as e:
            await self._emit_event(
                WorkflowEventType.ERROR,
                {
                    "error": str(e),
                },
            )
            if stream_events:
                while not self._event_queue.empty():
                    yield await self._event_queue.get()

    def run_sync(self, user_input: str) -> Dict[str, Any]:
        """
        Run the workflow synchronously.

        Args:
            user_input: The user's project description/request

        Returns:
            Final workflow state
        """
        if not self._graph:
            self._build_graph()

        initial_state: WorkflowState = {
            "messages": [HumanMessage(content=user_input)],
            "session_id": self.session.id if self.session else "temp",
            "current_phase": (
                self.session.current_phase.value
                if self.session
                else BMADPhase.IDEATION.value
            ),
            "phase_status": PhaseStatus.PENDING.value,
            "active_agent": None,
            "pending_approval": None,
            "artifacts": [],
            "error": None,
            "user_input": user_input,
            "iteration": 0,
        }

        config = {
            "configurable": {"thread_id": self.session.id if self.session else "temp"}
        }

        final_state = None
        for event in self._graph.stream(initial_state, config, stream_mode="values"):
            final_state = event

        return final_state

    def resume(
        self, approval_id: str, approved: bool, feedback: str = None
    ) -> Dict[str, Any]:
        """
        Resume the workflow after an approval.

        Args:
            approval_id: The approval request ID
            approved: Whether the approval was granted
            feedback: Optional feedback

        Returns:
            Updated workflow state
        """
        if not LANGGRAPH_AVAILABLE:
            return {"error": "LangGraph not available"}

        if not self.session:
            return {"error": "No session"}

        # Resolve approval in session
        try:
            self.session.resolve_approval(approval_id, approved, feedback=feedback)
            update_session(self.session)
        except ValueError as e:
            return {"error": str(e)}

        # Resume graph with response
        config = {"configurable": {"thread_id": self.session.id}}
        response = "approve" if approved else "reject"

        try:
            result = self._graph.invoke(Command(resume=response), config)
            return result
        except Exception as e:
            return {"error": str(e)}


# =============================================================================
# Quick Mode Workflow
# =============================================================================


class QuickModeWorkflow:
    """
    Simplified workflow for bug fixes and small changes.

    Skips ideation/discovery phases and goes straight to development.
    """

    def __init__(self, session: BMADSession = None, llm: Any = None):
        self.session = session
        self.llm = llm

    async def run(self, description: str) -> AsyncGenerator[WorkflowEvent, None]:
        """Run a quick mode workflow."""
        # Create simplified session starting at development
        if not self.session:
            from bmad.session import create_session

            self.session = create_session(
                project_name="Quick Fix",
                description=description,
                mode=BMADMode.SIMPLE,
            )

        # Run abbreviated workflow
        workflow = BMADWorkflow(self.session, self.llm)

        async for event in workflow.run(description):
            yield event


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "WorkflowEventType",
    "WorkflowEvent",
    "WorkflowState",
    "BMADWorkflow",
    "QuickModeWorkflow",
    "PHASE_ENTRY_CRITERIA",
    "PHASE_EXIT_CRITERIA",
    "PHASE_COMMIT_MESSAGES",
]
