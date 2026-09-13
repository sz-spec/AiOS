"""
Prompt History API Routes

Full CRUD, search, export, stats, rate, favorite, tags endpoints
for prompt history management.

SECURITY (April 2026 audit fix):
- All endpoints now require an authenticated user via `get_current_user`.
- Previously every endpoint scoped data to a synthetic `user_id="user_123"`,
  which made all users' prompt history cross-readable and cross-writable.
- Every path that accepts a prompt_id performs an ownership check before
  exposing or mutating the row, preventing IDOR.
"""

import csv
import io
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser

from prompts.prompt_history import (
    PromptType,
    PromptStatus,
    get_prompt_history_service,
    PromptHistoryService,
)

router = APIRouter(prefix="/prompts", tags=["Prompts"])


# ============================================
# Ownership guard
# ============================================


def _require_owner(entry, user_id: str):
    """Raise 404 if the entry is missing or 403 if it's owned by someone else."""
    if not entry:
        raise HTTPException(status_code=404, detail="Prompt not found")
    if getattr(entry, "user_id", None) != user_id:
        # Return 404 (not 403) to avoid leaking existence of other users' entries.
        raise HTTPException(status_code=404, detail="Prompt not found")


# ============================================
# Request / Response Models
# ============================================


class CreatePromptRequest(BaseModel):
    input_prompt: str
    prompt_type: PromptType = PromptType.CHAT
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    tags: list[str] = []


class UpdatePromptRequest(BaseModel):
    status: Optional[str] = None
    output_response: Optional[str] = None
    tags: Optional[list[str]] = None


class RatePromptRequest(BaseModel):
    rating: int
    feedback: Optional[str] = None


class AddTagsRequest(BaseModel):
    tags: list[str]


# ============================================
# Helper
# ============================================


def entry_to_response(entry) -> dict:
    """Convert a PromptEntry (or mock) to a JSON-safe response dict."""
    return {
        "id": entry.id,
        "user_id": entry.user_id,
        "project_id": entry.project_id,
        "prompt_type": (
            entry.prompt_type.value
            if hasattr(entry.prompt_type, "value")
            else entry.prompt_type
        ),
        "status": (
            entry.status.value if hasattr(entry.status, "value") else entry.status
        ),
        "input_prompt": entry.input_prompt,
        "output_response": entry.output_response,
        "generated_code": (
            entry.generated_code if isinstance(entry.generated_code, list) else []
        ),
        "model": entry.model,
        "provider": entry.provider,
        "prompt_tokens": entry.prompt_tokens,
        "completion_tokens": entry.completion_tokens,
        "total_tokens": entry.total_tokens,
        "duration_ms": entry.duration_ms,
        "error": entry.error,
        "tags": entry.tags,
        "rating": entry.rating,
        "feedback": entry.feedback,
        "is_favorite": entry.is_favorite,
        "created_at": (
            entry.created_at.isoformat()
            if hasattr(entry.created_at, "isoformat")
            else entry.created_at
        ),
        "completed_at": (
            entry.completed_at.isoformat()
            if entry.completed_at and hasattr(entry.completed_at, "isoformat")
            else entry.completed_at
        ),
    }


# ============================================
# CRUD
# ============================================


@router.post("")
async def create_prompt(
    request: CreatePromptRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Create a new prompt history entry owned by the authenticated user."""
    entry = svc.create_prompt(
        user_id=user.id,
        input_prompt=request.input_prompt,
        prompt_type=request.prompt_type,
        project_id=request.project_id,
        session_id=request.session_id,
        model=request.model,
        provider=request.provider,
        tags=request.tags,
    )
    return entry_to_response(entry)


@router.get("")
async def list_prompts(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    prompt_type: Optional[str] = None,
    status: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """List prompt history entries for the authenticated user."""
    entries, total = svc.list_prompts(
        user_id=user.id,
        limit=limit,
        offset=offset,
        prompt_type=prompt_type,
        status=status,
    )
    return {
        "prompts": [entry_to_response(e) for e in entries],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/search")
async def search_prompts(
    q: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Search the authenticated user's prompt history."""
    entries = svc.search_prompts(query=q, user_id=user.id, limit=limit)
    return {
        "prompts": [entry_to_response(e) for e in entries],
        "query": q,
        "count": len(entries),
    }


@router.get("/stats")
async def get_stats(
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Get prompt usage statistics for the authenticated user."""
    stats = svc.get_stats(user_id=user.id)
    return {
        "total_prompts": stats.total_prompts,
        "successful_prompts": stats.successful_prompts,
        "failed_prompts": stats.failed_prompts,
        "total_tokens": stats.total_tokens,
        "avg_tokens_per_prompt": stats.avg_tokens_per_prompt,
        "total_duration_ms": stats.total_duration_ms,
        "avg_duration_ms": stats.avg_duration_ms,
        "by_type": stats.by_type,
        "by_model": stats.by_model,
        "by_day": stats.by_day,
        "most_used_tags": stats.most_used_tags,
    }


@router.get("/export/json")
async def export_json(
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Export the authenticated user's prompt history as JSON."""
    data = svc.export_prompts(user_id=user.id, format="json")
    return data


@router.get("/export/csv")
async def export_csv(
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Export the authenticated user's prompt history as CSV."""
    result = svc.export_prompts(user_id=user.id, format="csv")
    rows = result.get("rows", [])
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(
            [
                {
                    k: json.dumps(v) if isinstance(v, (list, dict)) else str(v)
                    for k, v in row.items()
                }
                for row in rows
            ]
        )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=prompts.csv"},
    )


@router.get("/types/all")
async def get_prompt_types():
    """Get all prompt types and statuses (public — enum metadata only)."""
    return {
        "types": [
            {"id": t.value, "name": t.name.replace("_", " ").title()}
            for t in PromptType
        ],
        "statuses": [
            {"id": s.value, "name": s.name.replace("_", " ").title()}
            for s in PromptStatus
        ],
    }


@router.get("/{prompt_id}")
async def get_prompt(
    prompt_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Get a specific prompt entry (IDOR-protected)."""
    entry = svc.get_prompt(prompt_id)
    _require_owner(entry, user.id)
    return entry_to_response(entry)


@router.patch("/{prompt_id}")
async def update_prompt(
    prompt_id: str,
    request: UpdatePromptRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Update a prompt entry (IDOR-protected)."""
    existing = svc.get_prompt(prompt_id)
    _require_owner(existing, user.id)
    updated = svc.update_prompt(prompt_id, **request.dict(exclude_none=True))
    return entry_to_response(updated)


@router.delete("/{prompt_id}")
async def delete_prompt(
    prompt_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Delete a prompt entry (IDOR-protected)."""
    existing = svc.get_prompt(prompt_id)
    _require_owner(existing, user.id)
    deleted = svc.delete_prompt(prompt_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"status": "deleted", "prompt_id": prompt_id}


@router.post("/{prompt_id}/rate")
async def rate_prompt(
    prompt_id: str,
    request: RatePromptRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Rate a prompt entry (IDOR-protected)."""
    existing = svc.get_prompt(prompt_id)
    _require_owner(existing, user.id)
    success = svc.rate_prompt(prompt_id, request.rating, request.feedback)
    if not success:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"status": "rated", "prompt_id": prompt_id, "rating": request.rating}


@router.post("/{prompt_id}/favorite")
async def toggle_favorite(
    prompt_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Toggle favorite status for a prompt (IDOR-protected)."""
    existing = svc.get_prompt(prompt_id)
    _require_owner(existing, user.id)
    result = svc.toggle_favorite(prompt_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"status": "updated", "prompt_id": prompt_id, "is_favorite": result}


@router.post("/{prompt_id}/tags")
async def add_tags(
    prompt_id: str,
    request: AddTagsRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    svc: PromptHistoryService = Depends(get_prompt_history_service),
):
    """Add tags to a prompt entry (IDOR-protected)."""
    existing = svc.get_prompt(prompt_id)
    _require_owner(existing, user.id)
    success = svc.add_tags(prompt_id, request.tags)
    if not success:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {"status": "updated", "prompt_id": prompt_id, "tags": request.tags}
