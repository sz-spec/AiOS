"""
Efficiency Package
==================
Modular efficiency optimizations for LangGraph agents.

This package provides:
- Smart model routing via shared-ai-router
- Error handling and retry middleware
- Quantized LLM support
- Hybrid search (BM25 + semantic)
- Context management
- Efficient workflow building

Usage:
    from src.efficiency import assign_model, EfficiencyConfig, HybridRetriever
"""

# Configuration
from .config import (
    EfficiencyConfig,
    EfficientState,
    ReflectiveState,
    create_initial_state,
    create_reflective_state,
)

# Smart Router
from .router import (
    router,
    HAS_SMART_ROUTER,
    assign_model,
    assign_model_with_tracking,
    track_request,
    get_metrics,
    get_cost_breakdown,
    get_os_pipeline_model,
    get_os_pipeline_stages,
)

# Middleware
from .middleware import (
    error_middleware,
    retry_middleware,
    logic_error_middleware,
    combined_middleware,
    LogicError,
    BusinessRule,
    BusinessRuleRegistry,
    register_business_rule,
)

# LLM utilities
from .llm import (
    create_quantized_llm,
    create_efficient_embeddings,
    LLMCallReducer,
    OLLAMA_AVAILABLE,
    HF_AVAILABLE,
)

# Retrieval
from .retrieval import (
    HybridRetriever,
    ContextManager,
    FAISS_AVAILABLE,
)

# Workflow
from .workflow import (
    build_efficient_workflow,
    build_reflective_workflow,
    build_self_correcting_agent,
)

# Reflection
from .reflection import (
    CritiqueResult,
    CritiqueReport,
    ReflectionState,
    RequirementChecker,
    LogicErrorDetector,
    ReflectionLoop,
    reflection_middleware,
    create_critique_node,
    create_correction_router,
)

# LLM Factory (MUST use for all LLM instantiation)
from .factory import (
    get_llm_for_task,
    get_llm_for_rag,
    get_llm_for_agents,
    calculate_complexity,
)

__all__ = [
    # Config
    "EfficiencyConfig",
    "EfficientState",
    "ReflectiveState",
    "create_initial_state",
    "create_reflective_state",
    # Smart Router
    "router",
    "assign_model",
    "assign_model_with_tracking",
    # OS Development Pipeline
    "get_os_pipeline_model",
    "get_os_pipeline_stages",
    # Observability
    "track_request",
    "get_metrics",
    "get_cost_breakdown",
    # Middleware
    "error_middleware",
    "retry_middleware",
    "logic_error_middleware",
    "combined_middleware",
    "LogicError",
    "BusinessRule",
    "BusinessRuleRegistry",
    "register_business_rule",
    # LLM
    "create_quantized_llm",
    "create_efficient_embeddings",
    "LLMCallReducer",
    # Search
    "HybridRetriever",
    # Context
    "ContextManager",
    # Workflow
    "build_efficient_workflow",
    "build_reflective_workflow",
    "build_self_correcting_agent",
    # Reflection
    "CritiqueResult",
    "CritiqueReport",
    "ReflectionState",
    "RequirementChecker",
    "LogicErrorDetector",
    "ReflectionLoop",
    "reflection_middleware",
    "create_critique_node",
    "create_correction_router",
    # Availability flags
    "HAS_SMART_ROUTER",
    "OLLAMA_AVAILABLE",
    "FAISS_AVAILABLE",
    "HF_AVAILABLE",
    # LLM Factory
    "get_llm_for_task",
    "get_llm_for_rag",
    "get_llm_for_agents",
    "calculate_complexity",
]
