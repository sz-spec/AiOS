"""
VOS API Routes
==============
Endpoints for the VOS hidden system agent.
Admin observability + message processing.
"""

from fastapi import APIRouter, Depends
from api.deps import get_current_user, AuthenticatedUser
from pydantic import BaseModel
from typing import Optional, List, Dict

router = APIRouter()


class VosClassifyRequest(BaseModel):
    message: str
    context: Optional[List[Dict[str, str]]] = None


class VosExecuteRequest(BaseModel):
    message: str
    intent: str
    context: Optional[List[Dict[str, str]]] = None
    source: str = "chat"
    source_id: Optional[str] = None


class VosProcessRequest(BaseModel):
    message: str
    context: Optional[List[Dict[str, str]]] = None
    source: str = "chat"
    source_id: Optional[str] = None


def _get_engine():
    from vos.engine import get_vos_engine

    return get_vos_engine()


@router.post("/classify")
async def classify_intent(
    request: VosClassifyRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> dict:
    """Fast path: classify message intent without executing."""
    engine = _get_engine()
    intent = await engine.classify_intent(request.message, request.context)
    return {"intent": intent, "has_intent": intent is not None}


@router.post("/execute")
async def execute_action(
    request: VosExecuteRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> dict:
    """Execute a VOS action for a pre-classified intent."""
    engine = _get_engine()
    result = await engine.execute(
        message=request.message,
        intent_category=request.intent,
        context=request.context,
        source=request.source,
        source_id=request.source_id,
    )
    return {
        "intent": result.intent,
        "tool_calls": result.tool_calls,
        "results": result.results,
        "summary": result.summary,
        "source": result.source,
    }


@router.post("/process")
async def process_message(
    request: VosProcessRequest, user: AuthenticatedUser = Depends(get_current_user)
) -> dict:
    """Combined: classify + execute if intent found. Main entry point."""
    engine = _get_engine()

    # Step 1: Classify
    intent = await engine.classify_intent(request.message, request.context)

    if not intent:
        return {
            "had_intent": False,
            "intent": None,
            "results": None,
            "summary": None,
            "pass_through": True,
        }

    # Step 2: Execute
    result = await engine.execute(
        message=request.message,
        intent_category=intent,
        context=request.context,
        source=request.source,
        source_id=request.source_id,
    )

    return {
        "had_intent": True,
        "intent": result.intent,
        "tool_calls": result.tool_calls,
        "results": result.results,
        "summary": result.summary,
        "pass_through": False,
    }


@router.get("/actions")
async def get_recent_actions(
    limit: int = 50, user: AuthenticatedUser = Depends(get_current_user)
) -> dict:
    """Admin: get recent VOS actions."""
    engine = _get_engine()
    actions = engine.get_recent_actions(limit)
    return {"actions": actions, "total": len(actions)}


@router.get("/actions/stats")
async def get_action_stats(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    """Admin: get VOS action statistics."""
    engine = _get_engine()
    return engine.get_action_stats()


@router.get("/tools")
async def list_tools(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    """Admin: list all available VOS tools."""
    engine = _get_engine()
    tools = engine.get_available_tools()
    categories = engine.registry.get_categories()
    return {"tools": tools, "categories": categories, "total": len(tools)}
