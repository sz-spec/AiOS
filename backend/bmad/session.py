"""
BMAD Session Management
========================

Manages session state for BMAD workflows including:
- Session lifecycle (create, read, update, delete)
- Phase tracking and transitions
- Artifact storage
- Approval management
- Git integration state
"""

from typing import Dict, List, Optional, Any, TypedDict
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
import uuid

# =============================================================================
# Enums
# =============================================================================


class BMADMode(str, Enum):
    """UI/interaction modes for BMAD."""

    SIMPLE = "simple"  # Quick path for bug fixes
    GUIDED = "guided"  # Step-by-step wizard
    EXPERT = "expert"  # Full control dashboard
    PARTY = "party"  # Watch agents collaborate


class BMADPhase(str, Enum):
    """Workflow phases from ideation to operations."""

    IDEATION = "ideation"
    DISCOVERY = "discovery"
    PLANNING = "planning"
    DESIGN = "design"
    DEVELOPMENT = "development"
    TESTING = "testing"
    REVIEW = "review"
    DEPLOYMENT = "deployment"
    OPERATIONS = "operations"


class PhaseStatus(str, Enum):
    """Status of a phase."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class ArtifactType(str, Enum):
    """Types of artifacts produced by agents."""

    VISION = "vision"
    PRD = "prd"
    ARCHITECTURE = "architecture"
    DESIGN_SPEC = "design_spec"
    CODE = "code"
    TEST = "test"
    REVIEW = "review"
    DEPLOYMENT = "deployment"
    DOCUMENTATION = "documentation"


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class Artifact:
    """An artifact produced during the workflow."""

    id: str
    type: ArtifactType
    name: str
    content: Any
    phase: BMADPhase
    agent: str
    created_at: datetime
    version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "content": self.content,
            "phase": self.phase.value,
            "agent": self.agent,
            "created_at": self.created_at.isoformat(),
            "version": self.version,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Artifact":
        return cls(
            id=data["id"],
            type=ArtifactType(data["type"]),
            name=data["name"],
            content=data["content"],
            phase=BMADPhase(data["phase"]),
            agent=data["agent"],
            created_at=datetime.fromisoformat(data["created_at"]),
            version=data.get("version", 1),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Approval:
    """An approval request for human review."""

    id: str
    phase: BMADPhase
    agent: str
    description: str
    artifact_ids: List[str]
    status: PhaseStatus
    created_at: datetime
    resolved_at: Optional[datetime] = None
    resolver: Optional[str] = None
    feedback: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "phase": self.phase.value,
            "agent": self.agent,
            "description": self.description,
            "artifact_ids": self.artifact_ids,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolver": self.resolver,
            "feedback": self.feedback,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Approval":
        return cls(
            id=data["id"],
            phase=BMADPhase(data["phase"]),
            agent=data["agent"],
            description=data["description"],
            artifact_ids=data["artifact_ids"],
            status=PhaseStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            resolved_at=(
                datetime.fromisoformat(data["resolved_at"])
                if data.get("resolved_at")
                else None
            ),
            resolver=data.get("resolver"),
            feedback=data.get("feedback"),
        )


@dataclass
class PhaseState:
    """State of a single phase."""

    phase: BMADPhase
    status: PhaseStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    artifacts: List[str] = field(default_factory=list)  # artifact IDs
    approvals: List[str] = field(default_factory=list)  # approval IDs
    git_commit: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "phase": self.phase.value,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "artifacts": self.artifacts,
            "approvals": self.approvals,
            "git_commit": self.git_commit,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PhaseState":
        return cls(
            phase=BMADPhase(data["phase"]),
            status=PhaseStatus(data["status"]),
            started_at=(
                datetime.fromisoformat(data["started_at"])
                if data.get("started_at")
                else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at")
                else None
            ),
            artifacts=data.get("artifacts", []),
            approvals=data.get("approvals", []),
            git_commit=data.get("git_commit"),
            error=data.get("error"),
        )


@dataclass
class GitState:
    """Git repository state for the session."""

    enabled: bool = False
    repo_url: Optional[str] = None
    branch: str = "main"
    auto_commit: bool = True
    commits: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "repo_url": self.repo_url,
            "branch": self.branch,
            "auto_commit": self.auto_commit,
            "commits": self.commits,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "GitState":
        return cls(
            enabled=data.get("enabled", False),
            repo_url=data.get("repo_url"),
            branch=data.get("branch", "main"),
            auto_commit=data.get("auto_commit", True),
            commits=data.get("commits", []),
        )


@dataclass
class DeploymentState:
    """Deployment state for the session."""

    provider: str = "vercel"
    project_id: Optional[str] = None
    deployment_url: Optional[str] = None
    status: str = "not_deployed"
    last_deployment: Optional[datetime] = None
    history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "provider": self.provider,
            "project_id": self.project_id,
            "deployment_url": self.deployment_url,
            "status": self.status,
            "last_deployment": (
                self.last_deployment.isoformat() if self.last_deployment else None
            ),
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "DeploymentState":
        return cls(
            provider=data.get("provider", "vercel"),
            project_id=data.get("project_id"),
            deployment_url=data.get("deployment_url"),
            status=data.get("status", "not_deployed"),
            last_deployment=(
                datetime.fromisoformat(data["last_deployment"])
                if data.get("last_deployment")
                else None
            ),
            history=data.get("history", []),
        )


# =============================================================================
# Session State TypedDict (for LangGraph compatibility)
# =============================================================================


class SessionState(TypedDict):
    """TypedDict for LangGraph state management."""

    session_id: str
    project_name: str
    description: str
    mode: str
    current_phase: str
    phases: Dict[str, Dict]
    artifacts: Dict[str, Dict]
    approvals: Dict[str, Dict]
    messages: List[Dict]
    git: Dict
    deployment: Dict
    created_at: str
    updated_at: str
    user_id: Optional[str]
    metadata: Dict[str, Any]


# =============================================================================
# BMAD Session Class
# =============================================================================


@dataclass
class BMADSession:
    """
    Main session class for BMAD workflows.

    Manages all state for a development project including:
    - Phase progression
    - Artifacts produced
    - Approvals required
    - Git commits
    - Deployment state
    """

    id: str
    project_name: str
    description: str
    mode: BMADMode
    current_phase: BMADPhase
    phases: Dict[BMADPhase, PhaseState]
    artifacts: Dict[str, Artifact]
    approvals: Dict[str, Approval]
    messages: List[Dict[str, Any]]
    git: GitState
    deployment: DeploymentState
    created_at: datetime
    updated_at: datetime
    user_id: Optional[str] = None
    is_active: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        project_name: str,
        description: str = "",
        mode: BMADMode = BMADMode.GUIDED,
        user_id: str = None,
        git_repo: str = None,
        **kwargs,
    ) -> "BMADSession":
        """Create a new BMAD session."""
        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Initialize all phases
        phases = {}
        for phase in BMADPhase:
            phases[phase] = PhaseState(phase=phase, status=PhaseStatus.PENDING)

        # Set first phase based on mode
        start_phase = BMADPhase.IDEATION
        if mode == BMADMode.SIMPLE:
            start_phase = BMADPhase.DEVELOPMENT

        phases[start_phase].status = PhaseStatus.IN_PROGRESS
        phases[start_phase].started_at = now

        # Git state
        git = GitState(
            enabled=git_repo is not None,
            repo_url=git_repo,
            auto_commit=kwargs.get("auto_commit", True),
        )

        return cls(
            id=session_id,
            project_name=project_name,
            description=description,
            mode=mode,
            current_phase=start_phase,
            phases=phases,
            artifacts={},
            approvals={},
            messages=[],
            git=git,
            deployment=DeploymentState(),
            created_at=now,
            updated_at=now,
            user_id=user_id,
            is_active=True,
            metadata=kwargs.get("metadata", {}),
        )

    def to_dict(self) -> Dict:
        """Convert session to dictionary."""
        return {
            "id": self.id,
            "project_name": self.project_name,
            "description": self.description,
            "mode": self.mode.value,
            "current_phase": self.current_phase.value,
            "phases": {p.value: s.to_dict() for p, s in self.phases.items()},
            "artifacts": {k: v.to_dict() for k, v in self.artifacts.items()},
            "approvals": {k: v.to_dict() for k, v in self.approvals.items()},
            "messages": self.messages,
            "git": self.git.to_dict(),
            "deployment": self.deployment.to_dict(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "user_id": self.user_id,
            "is_active": self.is_active,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "BMADSession":
        """Create session from dictionary."""
        phases = {}
        for phase_str, state_dict in data.get("phases", {}).items():
            phase = BMADPhase(phase_str)
            phases[phase] = PhaseState.from_dict(state_dict)

        artifacts = {}
        for art_id, art_dict in data.get("artifacts", {}).items():
            artifacts[art_id] = Artifact.from_dict(art_dict)

        approvals = {}
        for app_id, app_dict in data.get("approvals", {}).items():
            approvals[app_id] = Approval.from_dict(app_dict)

        return cls(
            id=data["id"],
            project_name=data["project_name"],
            description=data.get("description", ""),
            mode=BMADMode(data["mode"]),
            current_phase=BMADPhase(data["current_phase"]),
            phases=phases,
            artifacts=artifacts,
            approvals=approvals,
            messages=data.get("messages", []),
            git=GitState.from_dict(data.get("git", {})),
            deployment=DeploymentState.from_dict(data.get("deployment", {})),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            user_id=data.get("user_id"),
            is_active=data.get("is_active", True),
            metadata=data.get("metadata", {}),
        )

    def to_state(self) -> SessionState:
        """Convert to LangGraph-compatible state."""
        return SessionState(
            session_id=self.id,
            project_name=self.project_name,
            description=self.description,
            mode=self.mode.value,
            current_phase=self.current_phase.value,
            phases={p.value: s.to_dict() for p, s in self.phases.items()},
            artifacts={k: v.to_dict() for k, v in self.artifacts.items()},
            approvals={k: v.to_dict() for k, v in self.approvals.items()},
            messages=self.messages,
            git=self.git.to_dict(),
            deployment=self.deployment.to_dict(),
            created_at=self.created_at.isoformat(),
            updated_at=self.updated_at.isoformat(),
            user_id=self.user_id,
            metadata=self.metadata,
        )

    # -------------------------------------------------------------------------
    # Phase Management
    # -------------------------------------------------------------------------

    def get_phase_state(self, phase: BMADPhase) -> PhaseState:
        """Get state for a specific phase."""
        return self.phases.get(phase)

    def advance_phase(self, to_phase: BMADPhase = None) -> BMADPhase:
        """
        Advance to the next phase or specified phase.
        Returns the new current phase.
        """
        now = datetime.now(timezone.utc)

        # Complete current phase
        current = self.phases[self.current_phase]
        current.status = PhaseStatus.COMPLETED
        current.completed_at = now

        # Determine next phase
        if to_phase:
            next_phase = to_phase
        else:
            phases_list = list(BMADPhase)
            current_idx = phases_list.index(self.current_phase)
            if current_idx < len(phases_list) - 1:
                next_phase = phases_list[current_idx + 1]
            else:
                return self.current_phase  # Already at last phase

        # Start next phase
        self.current_phase = next_phase
        self.phases[next_phase].status = PhaseStatus.IN_PROGRESS
        self.phases[next_phase].started_at = now
        self.updated_at = now

        return next_phase

    def get_pending_approvals(self) -> List[Approval]:
        """Get all pending approvals."""
        return [
            a
            for a in self.approvals.values()
            if a.status == PhaseStatus.AWAITING_APPROVAL
        ]

    # -------------------------------------------------------------------------
    # Artifact Management
    # -------------------------------------------------------------------------

    def add_artifact(
        self,
        type: ArtifactType,
        name: str,
        content: Any,
        agent: str,
        metadata: Dict = None,
    ) -> Artifact:
        """Add a new artifact to the session."""
        artifact = Artifact(
            id=str(uuid.uuid4()),
            type=type,
            name=name,
            content=content,
            phase=self.current_phase,
            agent=agent,
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        self.artifacts[artifact.id] = artifact
        self.phases[self.current_phase].artifacts.append(artifact.id)
        self.updated_at = datetime.now(timezone.utc)

        return artifact

    def get_artifacts_by_phase(self, phase: BMADPhase) -> List[Artifact]:
        """Get all artifacts for a phase."""
        artifact_ids = self.phases[phase].artifacts
        return [self.artifacts[aid] for aid in artifact_ids if aid in self.artifacts]

    def get_artifacts_by_type(self, type: ArtifactType) -> List[Artifact]:
        """Get all artifacts of a specific type."""
        return [a for a in self.artifacts.values() if a.type == type]

    # -------------------------------------------------------------------------
    # Approval Management
    # -------------------------------------------------------------------------

    def request_approval(
        self, description: str, agent: str, artifact_ids: List[str] = None
    ) -> Approval:
        """Request human approval."""
        approval = Approval(
            id=str(uuid.uuid4()),
            phase=self.current_phase,
            agent=agent,
            description=description,
            artifact_ids=artifact_ids or [],
            status=PhaseStatus.AWAITING_APPROVAL,
            created_at=datetime.now(timezone.utc),
        )

        self.approvals[approval.id] = approval
        self.phases[self.current_phase].approvals.append(approval.id)
        self.phases[self.current_phase].status = PhaseStatus.AWAITING_APPROVAL
        self.updated_at = datetime.now(timezone.utc)

        return approval

    def resolve_approval(
        self,
        approval_id: str,
        approved: bool,
        resolver: str = None,
        feedback: str = None,
    ) -> Approval:
        """Resolve an approval request."""
        if approval_id not in self.approvals:
            raise ValueError(f"Approval not found: {approval_id}")

        approval = self.approvals[approval_id]
        approval.status = PhaseStatus.APPROVED if approved else PhaseStatus.REJECTED
        approval.resolved_at = datetime.now(timezone.utc)
        approval.resolver = resolver
        approval.feedback = feedback

        # Update phase status if this was the blocking approval
        phase_state = self.phases[self.current_phase]
        pending = [
            self.approvals[aid]
            for aid in phase_state.approvals
            if self.approvals[aid].status == PhaseStatus.AWAITING_APPROVAL
        ]

        if not pending:
            if approved:
                phase_state.status = PhaseStatus.IN_PROGRESS
            else:
                phase_state.status = PhaseStatus.REJECTED

        self.updated_at = datetime.now(timezone.utc)
        return approval

    # -------------------------------------------------------------------------
    # Message Management
    # -------------------------------------------------------------------------

    def add_message(
        self, role: str, content: str, agent: str = None, metadata: Dict = None
    ):
        """Add a message to the conversation history."""
        self.messages.append(
            {
                "role": role,
                "content": content,
                "agent": agent,
                "phase": self.current_phase.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata or {},
            }
        )
        self.updated_at = datetime.now(timezone.utc)

    # -------------------------------------------------------------------------
    # Git Integration
    # -------------------------------------------------------------------------

    def record_commit(self, sha: str, message: str, files: List[str]):
        """Record a git commit."""
        self.git.commits.append(
            {
                "sha": sha,
                "message": message,
                "files": files,
                "phase": self.current_phase.value,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.phases[self.current_phase].git_commit = sha
        self.updated_at = datetime.now(timezone.utc)


# =============================================================================
# Session Store (In-Memory)
# =============================================================================

_sessions: Dict[str, BMADSession] = {}


def create_session(
    project_name: str,
    description: str = "",
    mode: BMADMode = BMADMode.GUIDED,
    user_id: str = None,
    **kwargs,
) -> BMADSession:
    """Create and store a new session."""
    session = BMADSession.create(
        project_name=project_name,
        description=description,
        mode=mode,
        user_id=user_id,
        **kwargs,
    )
    _sessions[session.id] = session
    return session


def get_session(session_id: str) -> Optional[BMADSession]:
    """Get a session by ID."""
    return _sessions.get(session_id)


def update_session(session: BMADSession) -> BMADSession:
    """Update a session in the store."""
    session.updated_at = datetime.now(timezone.utc)
    _sessions[session.id] = session
    return session


def delete_session(session_id: str) -> bool:
    """Delete a session."""
    if session_id in _sessions:
        del _sessions[session_id]
        return True
    return False


def list_sessions(user_id: str = None) -> List[BMADSession]:
    """List all sessions, optionally filtered by user."""
    sessions = list(_sessions.values())
    if user_id:
        sessions = [s for s in sessions if s.user_id == user_id]
    return sorted(sessions, key=lambda s: s.updated_at, reverse=True)
