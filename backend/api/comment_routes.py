"""
Comments API Routes

REST and WebSocket endpoints for real-time comments:
- CRUD operations
- Thread management
- Reactions
- Real-time updates
"""

import dataclasses
from datetime import datetime, timezone
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from api.deps import get_current_user, AuthenticatedUser

from .comments import (
    CommentsService,
    Comment,
    CommentLocation,
    CommentStatus,
    CreateCommentRequest,
    UpdateCommentRequest,
    ReactionRequest,
    CommentResponse,
)

router = APIRouter(prefix="/comments", tags=["Comments"])

# WebSocket connections per project
_ws_connections: dict[str, list[WebSocket]] = {}


_comments_service: CommentsService | None = None


def get_comments_service() -> CommentsService:
    """Get or create comments service."""
    global _comments_service
    if _comments_service is None:
        _comments_service = CommentsService()
    return _comments_service


def _user_name(user: AuthenticatedUser) -> str:
    """Derive a display name from AuthenticatedUser."""
    return user.email or user.id


def _user_avatar(user: AuthenticatedUser):
    """Derive avatar URL from AuthenticatedUser (None if unavailable)."""
    return user.metadata.get("avatar") if user.metadata else None


# ============================================
# REST Endpoints
# ============================================


@router.post("/projects/{project_id}", response_model=CommentResponse)
async def create_comment(
    project_id: str,
    request: CreateCommentRequest,
    service: CommentsService = Depends(get_comments_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Create a new comment.

    Supports:
    - General comments
    - Line-level comments on files
    - Code suggestions with ```suggestion blocks
    - @mentions (parsed automatically)
    - Thread replies via parent_id
    """
    location = None
    if request.file_path:
        location = CommentLocation(
            file_path=request.file_path,
            line_start=request.line_start or 1,
            line_end=request.line_end,
            commit_sha=request.commit_sha,
        )

    comment = await service.create_comment(
        project_id=project_id,
        user_id=user.id,
        user_name=_user_name(user),
        user_avatar=_user_avatar(user),
        content=request.content,
        location=location,
        comment_type=request.comment_type,
        parent_id=request.parent_id,
    )

    # Broadcast to WebSocket clients
    await broadcast_comment(project_id, "created", comment)

    return _to_response(comment)


@router.get("/projects/{project_id}", response_model=list[CommentResponse])
async def get_comments(
    project_id: str,
    file_path: Optional[str] = Query(None),
    status: Optional[CommentStatus] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommentsService = Depends(get_comments_service),
):
    """
    Get comments for a project.

    Filters:
    - file_path: Filter by file
    - status: Filter by status (open, resolved, etc.)
    """
    comments = await service.get_comments(
        project_id=project_id,
        file_path=file_path,
        status=status,
        limit=limit,
        offset=offset,
    )

    # Get reply counts
    responses = []
    for comment in comments:
        replies = await service.get_comments(
            project_id=project_id,
            parent_id=comment.id,
            limit=1,
        )
        resp = _to_response(comment)
        resp.reply_count = len(replies)
        responses.append(resp)

    return responses


@router.get("/projects/{project_id}/file")
async def get_file_comments(
    project_id: str,
    file_path: str = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommentsService = Depends(get_comments_service),
):
    """
    Get comments grouped by line for a file.

    Returns: { line_number: [comments] }
    """
    by_line = await service.get_comments_by_line(project_id, file_path)
    return {
        line: [_to_response(c).dict() for c in comments]
        for line, comments in by_line.items()
    }


@router.get("/projects/{project_id}/unresolved")
async def get_unresolved_count(
    project_id: str,
    file_path: Optional[str] = Query(None),
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommentsService = Depends(get_comments_service),
):
    """Get count of unresolved comments."""
    count = await service.get_unresolved_count(project_id, file_path)
    return {"count": count}


@router.get("/{comment_id}", response_model=CommentResponse)
async def get_comment(
    comment_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommentsService = Depends(get_comments_service),
):
    """Get a single comment."""
    comment = await service.get_comment(comment_id)
    if not comment:
        raise HTTPException(404, "Comment not found")
    return _to_response(comment)


@router.get("/{comment_id}/thread", response_model=list[CommentResponse])
async def get_thread(
    comment_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: CommentsService = Depends(get_comments_service),
):
    """Get a comment thread (parent + all replies)."""
    thread = await service.get_thread(comment_id)
    return [_to_response(c) for c in thread]


@router.patch("/{comment_id}", response_model=CommentResponse)
async def update_comment(
    comment_id: str,
    request: UpdateCommentRequest,
    service: CommentsService = Depends(get_comments_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Update a comment.

    - Only author can edit content
    - Anyone can change status (resolve)
    """
    try:
        comment = await service.update_comment(
            comment_id=comment_id,
            user_id=user.id,
            content=request.content,
            status=request.status,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))

    if not comment:
        raise HTTPException(404, "Comment not found")

    # Broadcast update
    await broadcast_comment(comment.project_id, "updated", comment)

    return _to_response(comment)


@router.delete("/{comment_id}")
async def delete_comment(
    comment_id: str,
    service: CommentsService = Depends(get_comments_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete a comment (only author)."""
    # Get comment first for project_id
    comment = await service.get_comment(comment_id)
    if not comment:
        raise HTTPException(404, "Comment not found")

    try:
        deleted = await service.delete_comment(comment_id, user.id)
    except PermissionError as e:
        raise HTTPException(403, str(e))

    if not deleted:
        raise HTTPException(500, "Failed to delete comment")

    # Broadcast deletion
    await broadcast_comment(comment.project_id, "deleted", comment)

    return {"deleted": comment_id}


@router.post("/{comment_id}/resolve", response_model=CommentResponse)
async def resolve_comment(
    comment_id: str,
    apply_suggestion: bool = Query(False),
    service: CommentsService = Depends(get_comments_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Resolve a comment.

    If apply_suggestion=True and comment has suggestion_code,
    returns the suggestion to be applied.
    """
    comment = await service.resolve_comment(
        comment_id=comment_id,
        user_id=user.id,
        apply_suggestion=apply_suggestion,
    )

    if not comment:
        raise HTTPException(404, "Comment not found")

    await broadcast_comment(comment.project_id, "resolved", comment)

    response = _to_response(comment)

    # Include suggestion if requested
    if apply_suggestion and comment.suggestion_code:
        return {
            **response.dict(),
            "apply_suggestion": {
                "file_path": comment.location.file_path if comment.location else None,
                "line_start": comment.location.line_start if comment.location else None,
                "line_end": comment.location.line_end if comment.location else None,
                "code": comment.suggestion_code,
            },
        }

    return response


@router.post("/{comment_id}/reactions")
async def toggle_reaction(
    comment_id: str,
    request: ReactionRequest,
    service: CommentsService = Depends(get_comments_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Toggle a reaction on a comment.

    Common emojis: 👍 👎 ❤️ 🎉 😕 👀 🚀
    """
    added = await service.add_reaction(comment_id, user.id, request.emoji)

    comment = await service.get_comment(comment_id)
    if comment:
        await broadcast_comment(comment.project_id, "reaction", comment)

    return {"added": added, "emoji": request.emoji}


# ============================================
# WebSocket for Real-time Updates
# ============================================


@router.websocket("/ws/{project_id}")
async def comments_websocket(
    websocket: WebSocket,
    project_id: str,
):
    """
    WebSocket for real-time comment updates.

    Receives:
    - { type: "subscribe", file_path?: string }
    - { type: "ping" }

    Sends:
    - { type: "created", comment: {...} }
    - { type: "updated", comment: {...} }
    - { type: "resolved", comment: {...} }
    - { type: "deleted", comment_id: string }
    - { type: "reaction", comment: {...} }
    - { type: "pong" }
    """
    await websocket.accept()

    # Add to connections
    if project_id not in _ws_connections:
        _ws_connections[project_id] = []
    _ws_connections[project_id].append(websocket)

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

            elif data.get("type") == "subscribe":
                # Client subscribing to specific file
                # Could track per-file subscriptions for optimization
                await websocket.send_json(
                    {
                        "type": "subscribed",
                        "project_id": project_id,
                        "file_path": data.get("file_path"),
                    }
                )

    except WebSocketDisconnect:
        _ws_connections[project_id].remove(websocket)
        if not _ws_connections[project_id]:
            del _ws_connections[project_id]


async def broadcast_comment(project_id: str, event_type: str, comment: Comment):
    """Broadcast comment event to all connected clients."""
    if project_id not in _ws_connections:
        return

    message = {
        "type": event_type,
        "comment": dataclasses.asdict(comment) if event_type != "deleted" else None,
        "comment_id": comment.id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Send to all connected clients
    disconnected = []
    for ws in _ws_connections[project_id]:
        try:
            await ws.send_json(message)
        except Exception as e:
            import logging

            logging.warning(f"WebSocket broadcast failed: {e}")
            disconnected.append(ws)

    # Clean up disconnected
    for ws in disconnected:
        _ws_connections[project_id].remove(ws)
    # Fix memory leak: clean up empty entries
    if not _ws_connections[project_id]:
        del _ws_connections[project_id]


# ============================================
# Helpers
# ============================================


def _to_response(comment: Comment) -> CommentResponse:
    """Convert Comment to CommentResponse."""
    return CommentResponse(
        id=comment.id,
        project_id=comment.project_id,
        user_id=comment.user_id,
        user_name=comment.user_name,
        user_avatar=comment.user_avatar,
        content=comment.content,
        location=dataclasses.asdict(comment.location) if comment.location else None,
        comment_type=comment.comment_type.value,
        status=comment.status.value,
        parent_id=comment.parent_id,
        mentioned_users=comment.mentioned_users,
        reactions=[{"emoji": r.emoji, "user_id": r.user_id} for r in comment.reactions],
        suggestion_code=comment.suggestion_code,
        reply_count=0,
        created_at=comment.created_at.isoformat(),
        updated_at=comment.updated_at.isoformat(),
        resolved_by=comment.resolved_by,
        resolved_at=comment.resolved_at.isoformat() if comment.resolved_at else None,
    )
