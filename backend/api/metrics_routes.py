"""
Metrics API Routes
==================

Observability endpoints for router and efficiency metrics.
"""

from fastapi import APIRouter, Depends
from api.deps import get_current_user, AuthenticatedUser
from typing import Dict, Any, List
from datetime import datetime

from src.observability import (
    get_metrics,
    get_cost_breakdown,
    reset_metrics,
    get_health,
    get_errors,
    record_error,
    update_service_status,
)
from src.efficiency import router as smart_router

router = APIRouter()


@router.get(
    "/",
    summary="Get all metrics",
    description="Return all observability metrics including request counts, costs, and response times",
)
async def get_all_metrics(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get all observability metrics."""
    return get_metrics()


@router.get("/summary", summary="Get metrics summary")
async def get_metrics_summary(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get summary metrics only."""
    metrics = get_metrics()
    return metrics.get("summary", {})


@router.get(
    "/costs",
    summary="Get cost breakdown",
    description="Return cost breakdown aggregated by model and role",
)
async def get_costs(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get cost breakdown by model and role."""
    return get_cost_breakdown()


@router.get(
    "/models",
    summary="Get router model info",
    description="Return SmartRouter model configuration including role mappings and enabled models",
)
async def get_models_info(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get information about configured models."""
    models = {}
    for name in smart_router.list_models():
        model = smart_router.get_model(name)
        models[name] = {
            "name": model.name,
            "provider": model.provider,
            "model_id": model.model_id,
            "enabled": model.enabled,
            "priority": model.priority,
        }

    return {
        "models": models,
        "default": smart_router.config.default_model,
        "complexity_threshold": smart_router.config.complexity_threshold,
        "role_mappings": smart_router.config.role_mappings,
        "enabled_models": smart_router.list_enabled_models(),
    }


@router.get(
    "/recent",
    summary="Get recent requests",
    description="Return recent request history with model selections and response times",
)
async def get_recent_requests(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get recent request history."""
    metrics = get_metrics()
    return {
        "recent_requests": metrics.get("recent_requests", []),
        "last_hour": metrics.get("last_hour", {}),
    }


@router.post(
    "/reset",
    summary="Reset all metrics",
    description="Clear all accumulated metrics data. Use with caution in production",
)
async def reset_all_metrics(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Reset all metrics (use with caution)."""
    reset_metrics()
    return {"success": True, "message": "All metrics have been reset"}


# ===== Health & Bug Tracking Endpoints =====


@router.get(
    "/health",
    summary="Get system health",
    description="Return health status of all backend services including memory, router, and external dependencies",
    responses={503: {"description": "One or more services degraded or unavailable"}},
)
async def get_system_health(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get system health status."""
    get_health()

    # Check memory service
    try:
        from memory.dev_memory import get_dev_memory

        memory = get_dev_memory()
        stats = memory.get_stats()
        update_service_status(
            "memory",
            "healthy" if stats.get("initialized") else "degraded",
            {
                "total_memories": stats.get("total_memories", 0),
            },
        )
    except Exception as e:
        update_service_status("memory", "error", {"error": str(e)})

    # Check router
    try:
        models = smart_router.list_enabled_models()
        update_service_status(
            "router",
            "healthy",
            {
                "enabled_models": len(models),
            },
        )
    except Exception as e:
        update_service_status("router", "error", {"error": str(e)})

    return get_health()


@router.get(
    "/health/langgraph",
    summary="Check LangGraph health",
    description="Return LangGraph version, feature flags, and upgrade status for command routing and parallel execution",
)
async def langgraph_health(
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Check LangGraph upgrade status and feature flags."""
    import os
    import langgraph

    # Import feature flags and availability from upgraded modules
    from ai.agents.router_agent import USE_COMMAND_ROUTING, _COMMAND_AVAILABLE
    from ai.agents.multi_agent import USE_PARALLEL_EXECUTION, _SEND_AVAILABLE

    # Check environment variable overrides
    env_command = os.environ.get("USE_COMMAND_ROUTING", "")
    env_parallel = os.environ.get("USE_PARALLEL_EXECUTION", "")

    # Determine status
    if not _COMMAND_AVAILABLE or not _SEND_AVAILABLE:
        status = "degraded"  # LangGraph version doesn't support features
    elif not USE_COMMAND_ROUTING or not USE_PARALLEL_EXECUTION:
        status = "rollback"  # Features disabled via env var
    else:
        status = "ok"

    return {
        "langgraph_version": getattr(langgraph, "__version__", "unknown"),
        "features": {
            "command_routing": {
                "available": _COMMAND_AVAILABLE,
                "enabled": USE_COMMAND_ROUTING,
                "env_override": env_command or None,
            },
            "parallel_execution": {
                "available": _SEND_AVAILABLE,
                "enabled": USE_PARALLEL_EXECUTION,
                "env_override": env_parallel or None,
            },
        },
        "status": status,
        "upgrade_complete": USE_COMMAND_ROUTING and USE_PARALLEL_EXECUTION,
        "rollback_instructions": {
            "phase2": "export USE_COMMAND_ROUTING=false",
            "phase3": "export USE_PARALLEL_EXECUTION=false",
        },
    }


async def check_stuck_threads(
    checkpointer, max_age_hours: float = 24
) -> List[Dict[str, Any]]:
    """
    Find threads stuck in interrupted state (waiting for human input).

    Args:
        checkpointer: LangGraph checkpointer instance (MemorySaver, Redis, Postgres)
        max_age_hours: Threads idle longer than this are considered stuck

    Returns:
        List of stuck thread info dicts
    """
    stuck = []

    # Handle MemorySaver (dev/staging)
    if hasattr(checkpointer, "storage"):
        for thread_id, checkpoint in checkpointer.storage.items():
            state = checkpoint.get("channel_values", {})

            # Check for interrupt marker
            if state.get("__interrupt__"):
                updated = checkpoint.get("ts", "")
                if updated:
                    try:
                        ts = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        age = (
                            datetime.now(ts.tzinfo) - ts
                            if ts.tzinfo
                            else datetime.now() - ts
                        )
                        age_hours = age.total_seconds() / 3600

                        if age_hours > max_age_hours:
                            stuck.append(
                                {
                                    "thread_id": thread_id,
                                    "age_hours": round(age_hours, 2),
                                    "phase": state.get("current_phase", "unknown"),
                                    "interrupt_type": state.get(
                                        "__interrupt__", {}
                                    ).get("type", "unknown"),
                                    "last_updated": updated,
                                }
                            )
                    except (ValueError, TypeError):
                        # Invalid timestamp format
                        pass

    # TODO: Add Redis/Postgres checkpointer support when needed
    # elif hasattr(checkpointer, 'conn'):  # Postgres
    #     ...

    return sorted(stuck, key=lambda x: x["age_hours"], reverse=True)


@router.get(
    "/health/stuck-threads",
    summary="Find stuck HITL threads",
    description="Identify human-in-the-loop threads that have been waiting for input longer than the specified threshold",
)
async def get_stuck_threads(
    max_age_hours: float = 24, user: AuthenticatedUser = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Find HITL threads stuck waiting for human input.

    These are threads that have been interrupted (waiting for approval/input)
    for longer than max_age_hours.
    """
    try:
        # Try to get checkpointer from HITL workflow
        from hitl import HITLWorkflow

        workflow = HITLWorkflow()

        if hasattr(workflow, "_workflow") and hasattr(
            workflow._workflow, "checkpointer"
        ):
            checkpointer = workflow._workflow.checkpointer
            stuck = await check_stuck_threads(checkpointer, max_age_hours)

            return {
                "stuck_threads": stuck,
                "total": len(stuck),
                "max_age_hours": max_age_hours,
                "status": "warning" if stuck else "ok",
            }
        else:
            return {
                "stuck_threads": [],
                "total": 0,
                "status": "ok",
                "note": "No checkpointer available (stateless mode)",
            }

    except ImportError:
        return {
            "stuck_threads": [],
            "total": 0,
            "status": "ok",
            "note": "HITL module not available",
        }
    except Exception as e:
        return {
            "stuck_threads": [],
            "total": 0,
            "status": "error",
            "error": str(e),
        }


@router.get(
    "/errors",
    summary="Get error list",
    description="Return recent errors and bugs, optionally including resolved ones",
)
async def get_error_list(
    limit: int = 50,
    include_resolved: bool = False,
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get recent errors/bugs."""
    errors = get_errors(limit, include_resolved)
    return {
        "errors": errors,
        "total": len(errors),
    }


@router.post(
    "/errors/report",
    summary="Report error",
    description="Record a new error or bug with severity, source, and optional stack trace",
)
async def report_error(
    error_type: str,
    message: str,
    source: str,
    severity: str = "error",
    stack_trace: str = None,
    user: AuthenticatedUser = Depends(get_current_user),
) -> Dict[str, Any]:
    """Report a new error/bug."""
    record_error(error_type, message, source, severity, stack_trace)
    return {"success": True, "message": "Error recorded"}
