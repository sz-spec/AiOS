"""
Profiling Module
================
Observability and profiling integrations for LangGraph agents.

Available Integrations:
- Langfuse: Full tracing, metrics, and scoring
- Advanced: Distributed tracing, custom spans, auto-scoring

Basic Usage:
    from profiling import ProfiledWorkflow, LangfuseManager

    workflow = ProfiledWorkflow()
    result = workflow.run("AI trends 2025")

Advanced Usage:
    from profiling import AdvancedProfiler, profiled

    profiler = AdvancedProfiler()

    # Context manager approach
    with profiler.trace("my-operation", user_id="user-123"):
        result = app.invoke(state, profiler.get_config("thread-1"))
        profiler.score("accuracy", 0.95)

    # Decorator approach
    @profiled(name="my-function")
    def my_function(state):
        return {"result": "ok"}
"""

from .langfuse_integration import (
    ProfiledWorkflow,
    LangfuseManager,
    LangfuseConfig,
    WorkflowConfig,
    AgentState,
    LANGFUSE_AVAILABLE,
)

from .advanced_profiling import (
    AdvancedProfiler,
    AdvancedLangfuseConfig,
    profiled,
    profiled_observe,
    get_profiler,
    quick_profile,
)

__all__ = [
    # Basic profiling
    "ProfiledWorkflow",
    "LangfuseManager",
    "LangfuseConfig",
    "WorkflowConfig",
    "AgentState",
    "LANGFUSE_AVAILABLE",
    # Advanced profiling
    "AdvancedProfiler",
    "AdvancedLangfuseConfig",
    "profiled",
    "profiled_observe",
    "get_profiler",
    "quick_profile",
]
