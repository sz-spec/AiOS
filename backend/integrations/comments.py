"""
Comments integration module.

Provides data models and service for project code comments.
Used by api/comment_routes.py via the api/comments.py shim.
"""

import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Any

from pydantic import BaseModel, Field

# =============================================================================
# Enums
# =============================================================================


class CommentStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    WONTFIX = "wontfix"


class CommentType(str, Enum):
    COMMENT = "comment"
    SUGGESTION = "suggestion"
    REVIEW = "review"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class CommentLocation:
    """Location of a comment in a file."""

    file_path: str
    line_start: int = 1
    line_end: Optional[int] = None
    commit_sha: Optional[str] = None


@dataclass
class Comment:
    """A comment on a project."""

    id: str
    project_id: str
    user_id: str
    user_name: str
    content: str
    comment_type: CommentType = CommentType.COMMENT
    status: CommentStatus = CommentStatus.OPEN
    user_avatar: Optional[str] = None
    location: Optional[CommentLocation] = None
    parent_id: Optional[str] = None
    mentioned_users: List[str] = field(default_factory=list)
    reactions: List[dict] = field(default_factory=list)
    suggestion_code: Optional[str] = None
    reply_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None


# =============================================================================
# Request/Response Models
# =============================================================================


class CreateCommentRequest(BaseModel):
    """Request to create a new comment."""

    content: str = Field(min_length=1)
    file_path: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    commit_sha: Optional[str] = None
    comment_type: CommentType = CommentType.COMMENT
    parent_id: Optional[str] = None


class UpdateCommentRequest(BaseModel):
    """Request to update a comment."""

    content: Optional[str] = None
    status: Optional[CommentStatus] = None


class ReactionRequest(BaseModel):
    """Request to add a reaction."""

    emoji: str


class CommentResponse(BaseModel):
    """Comment response model."""

    id: str
    project_id: str
    user_id: str
    user_name: str
    user_avatar: Optional[str]
    content: str
    location: Optional[Any]
    comment_type: str
    status: str
    parent_id: Optional[str]
    mentioned_users: List[str]
    reactions: List[Any]
    suggestion_code: Optional[str]
    reply_count: int
    created_at: str
    updated_at: str
    resolved_by: Optional[str]
    resolved_at: Optional[str]
    apply_suggestion: Optional[dict] = None


# =============================================================================
# Service
# =============================================================================


class CommentsService:
    """In-memory comments service."""

    def __init__(self):
        self._comments: dict[str, Comment] = {}

    async def create_comment(
        self,
        project_id: str,
        user_id: str,
        user_name: str,
        user_avatar: Optional[str],
        content: str,
        location: Optional[CommentLocation],
        comment_type: CommentType = CommentType.COMMENT,
        parent_id: Optional[str] = None,
    ) -> Comment:
        now = datetime.now(timezone.utc).isoformat()
        comment = Comment(
            id=f"cmt_{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            user_id=user_id,
            user_name=user_name,
            user_avatar=user_avatar,
            content=content,
            location=location,
            comment_type=comment_type,
            parent_id=parent_id,
            created_at=now,
            updated_at=now,
        )
        self._comments[comment.id] = comment
        if parent_id and parent_id in self._comments:
            self._comments[parent_id].reply_count += 1
        return comment

    async def get_comments(
        self,
        project_id: str,
        file_path: Optional[str] = None,
        status: Optional[CommentStatus] = None,
        limit: int = 50,
    ) -> List[Comment]:
        comments = [c for c in self._comments.values() if c.project_id == project_id]
        if file_path:
            comments = [
                c for c in comments if c.location and c.location.file_path == file_path
            ]
        if status:
            comments = [c for c in comments if c.status == status]
        return comments[:limit]

    async def get_comment(self, comment_id: str) -> Optional[Comment]:
        return self._comments.get(comment_id)

    async def update_comment(
        self,
        comment_id: str,
        content: Optional[str] = None,
        status: Optional[CommentStatus] = None,
    ) -> Optional[Comment]:
        comment = self._comments.get(comment_id)
        if not comment:
            return None
        if content is not None:
            comment.content = content
        if status is not None:
            comment.status = status
        comment.updated_at = datetime.now(timezone.utc).isoformat()
        return comment

    async def delete_comment(self, comment_id: str) -> bool:
        if comment_id in self._comments:
            del self._comments[comment_id]
            return True
        return False

    async def add_reaction(
        self, comment_id: str, user_id: str, emoji: str
    ) -> Optional[Comment]:
        comment = self._comments.get(comment_id)
        if not comment:
            return None
        comment.reactions.append({"user_id": user_id, "emoji": emoji})
        return comment

    async def resolve_comment(
        self,
        comment_id: str,
        user_id: str,
        apply_suggestion: bool = False,
    ) -> Optional[Comment]:
        comment = self._comments.get(comment_id)
        if not comment:
            return None
        comment.status = CommentStatus.RESOLVED
        comment.resolved_by = user_id
        comment.resolved_at = datetime.now(timezone.utc).isoformat()
        return comment
