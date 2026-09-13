"""
backend/api/orchestrator_routes.py — P6.0 Orchestrator HTTP surface.

Exposes the SovereignOrchestrator's submit + poll affordances over
HTTP. Both endpoints are Bearer-token-gated (operator identity, not
app identity — orchestrator runs are operator-authored). CSRF gating
is applied automatically by the global FastAPI app middleware chain
when this router is mounted under `app.py` (the airgap test harness
mounts the router directly without CSRF for unit-level coverage).

Endpoints
---------
  POST /api/orchestrator/workflows
    Body: { full workflow manifest }
    → 200  { run_id, status: "pending", ... }
    The route ALSO drives the run to completion synchronously
    when `execute: true` is set in the body — useful for the
    smoke-test path. Operators can submit + poll separately by
    omitting `execute`.

  GET  /api/orchestrator/workflows/{run_id}/status
    → 200  { run status + per-step rows }
    → 404 if run_id is unknown.

Why no background worker
------------------------
P6.0 ships with synchronous execution. The orchestrator's
`execute_workflow()` is async and runs on the request loop; for
small DAGs (the directive's 3-step test) this is fine. A future
P6.x will wire a background dispatcher so a long-running 50-step
workflow doesn't tie up the request thread.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import AuthenticatedUser, get_current_user
from services.agent_orchestrator import (
    ORCHESTRATOR,
    WorkflowValidationError,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/orchestrator", tags=["Orchestrator"])


@router.post("/workflows")
async def submit_workflow(
    payload: dict,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Validate + persist a workflow run.

    Body:
      {
        "manifest": { ...workflow_manifest... },
        "execute":  true  # optional — when true, run to completion
                          # synchronously and return the final status.
      }
    """
    manifest = payload.get("manifest") if isinstance(payload, dict) else None
    execute_now = bool(payload.get("execute")) if isinstance(payload, dict) else False
    if not isinstance(manifest, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": "body.manifest required"},
        )

    try:
        handle = ORCHESTRATOR.submit_workflow(manifest)
    except WorkflowValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_workflow", "reason": str(exc)},
        ) from exc

    logger.info(
        "[orch] %s submitted run_id=%s workspace=%s steps=%d execute=%s",
        user.id,
        handle.run_id,
        handle.workspace_id,
        handle.step_count,
        execute_now,
    )

    if not execute_now:
        return handle.to_dict()

    final = await ORCHESTRATOR.execute_workflow(handle.run_id)
    return final


@router.get("/workflows/{run_id}/status")
async def get_workflow_status(
    run_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Poll the run's status + per-step rows."""
    try:
        return ORCHESTRATOR.run_status(run_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "run_not_found", "run_id": run_id},
        ) from exc


__all__ = ["router"]
