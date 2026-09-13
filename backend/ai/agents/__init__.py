"""Multi-agent system for AI App Builder."""

from .multi_agent import (
    MultiAgentBuilder,
    AgentRole,
    ArchitectAgent,
    FrontendAgent,
    BackendAgent,
    TesterAgent,
    ReviewerAgent,
    analyze_requirements,
    generate_frontend,
    review_code,
)

from .router_agent import (
    MultiAgentRouter,
    AgentType,
    AgentConfig,
    quick_route,
    web_search,
    calculator,
    code_executor,
    file_analyzer,
)

from .research_workflow import (
    ResearchWorkflow,
    WorkflowConfig,
    WorkflowStage,
    WorkflowStatus,
    AgentState,
)

__all__ = [
    # Multi-Agent Builder (Code Generation)
    "MultiAgentBuilder",
    "AgentRole",
    "ArchitectAgent",
    "FrontendAgent",
    "BackendAgent",
    "TesterAgent",
    "ReviewerAgent",
    "analyze_requirements",
    "generate_frontend",
    "review_code",
    # Router Agent System (2025 Pattern)
    "MultiAgentRouter",
    "AgentType",
    "AgentConfig",
    "quick_route",
    "web_search",
    "calculator",
    "code_executor",
    "file_analyzer",
    # Research Workflow (Checkpointing + Retry)
    "ResearchWorkflow",
    "WorkflowConfig",
    "WorkflowStage",
    "WorkflowStatus",
    "AgentState",
]
