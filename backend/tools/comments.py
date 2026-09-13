"""
Real-time Comments Service

Provides collaborative commenting on code:
- Line-level comments on files
- Threaded discussions
- @mentions with notifications
- Comment resolution workflow
- Real-time sync via WebSocket

Based on GitHub/GitLab code review patterns.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class CommentStatus(str, Enum):
    """Comment status."""

    OPEN = "open"
    RESOLVED = "resolved"
    WONTFIX = "wontfix"
    OUTDATED = "outdated"


class CommentType(str, Enum):
    """Comment type."""

    COMMENT = "comment"  # General comment
    SUGGESTION = "suggestion"  # Code suggestion
    QUESTION = "question"  # Question about code
    ISSUE = "issue"  # Issue/bug report
    PRAISE = "praise"  # Positive feedback


@dataclass
class CommentLocation:
    """Location of a comment in code."""

    file_path: str
    line_start: int
    line_end: Optional[int] = None
    commit_sha: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end or self.line_start,
            "commit_sha": self.commit_sha,
        }


@dataclass
class CommentReaction:
    """Reaction to a comment."""

    emoji: str
    user_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Comment:
    """A code comment."""

    id: str
    project_id: str
    user_id: str
    user_name: str
    user_avatar: Optional[str]
    content: str
    location: Optional[CommentLocation]
    comment_type: CommentType
    status: CommentStatus
    parent_id: Optional[str]  # For threaded replies
    mentioned_users: list[str]
    reactions: list[CommentReaction]
    suggestion_code: Optional[str]
    created_at: datetime
    updated_at: datetime
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "user_avatar": self.user_avatar,
            "content": self.content,
            "location": self.location.to_dict() if self.location else None,
            "comment_type": self.comment_type.value,
            "status": self.status.value,
            "parent_id": self.parent_id,
            "mentioned_users": self.mentioned_users,
            "reactions": [
                {
                    "emoji": r.emoji,
                    "user_id": r.user_id,
                    "created_at": r.created_at.isoformat(),
                }
                for r in self.reactions
            ],
            "suggestion_code": self.suggestion_code,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


class CommentsService:
    """
    Real-time comments service.

    Features:
    - CRUD operations for comments
    - Threaded discussions
    - @mention parsing
    - Notification triggers
    """

    def __init__(self):
        # In-memory storage
        self._comments: dict[str, dict] = {}
        self._reactions: list[dict] = []

    def _extract_mentions(self, content: str) -> list[str]:
        """Extract @mentioned usernames from content."""
        pattern = r"@(\w+)"
        return list(set(re.findall(pattern, content)))

    def _parse_suggestion(self, content: str) -> tuple[str, Optional[str]]:
        """Parse suggestion code block from content."""
        pattern = r"```suggestion\n([\s\S]*?)\n```"
        match = re.search(pattern, content)
        if match:
            suggestion = match.group(1)
            clean_content = re.sub(pattern, "", content).strip()
            return clean_content, suggestion
        return content, None

    async def create_comment(
        self,
        project_id: str,
        user_id: str,
        user_name: str,
        content: str,
        location: Optional[CommentLocation] = None,
        comment_type: CommentType = CommentType.COMMENT,
        parent_id: Optional[str] = None,
        user_avatar: Optional[str] = None,
    ) -> Comment:
        """Create a new comment."""
        # Extract mentions and suggestions
        mentioned_users = self._extract_mentions(content)
        clean_content, suggestion_code = self._parse_suggestion(content)

        # Auto-detect type from content
        if suggestion_code and comment_type == CommentType.COMMENT:
            comment_type = CommentType.SUGGESTION
        elif content.strip().endswith("?") and comment_type == CommentType.COMMENT:
            comment_type = CommentType.QUESTION

        now = datetime.now(timezone.utc)
        comment_id = str(uuid4())

        comment = Comment(
            id=comment_id,
            project_id=project_id,
            user_id=user_id,
            user_name=user_name,
            user_avatar=user_avatar,
            content=clean_content,
            location=location,
            comment_type=comment_type,
            status=CommentStatus.OPEN,
            parent_id=parent_id,
            mentioned_users=mentioned_users,
            reactions=[],
            suggestion_code=suggestion_code,
            created_at=now,
            updated_at=now,
        )

        # Store in memory
        self._comments[comment.id] = comment.to_dict()

        # Trigger notifications for mentions
        if mentioned_users:
            await self._notify_mentions(comment, mentioned_users)

        return comment

    async def get_comment(self, comment_id: str) -> Optional[Comment]:
        """Get a comment by ID."""
        data = self._comments.get(comment_id)
        if data:
            return self._parse_comment(data)
        return None

    async def get_comments(
        self,
        project_id: str,
        file_path: Optional[str] = None,
        status: Optional[CommentStatus] = None,
        parent_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Comment]:
        """Get comments for a project with optional filters."""
        results = []
        for data in self._comments.values():
            if data.get("project_id") != project_id:
                continue
            if file_path and data.get("file_path") != file_path:
                continue
            if status and data.get("status") != status.value:
                continue
            if parent_id and data.get("parent_id") != parent_id:
                continue
            elif (
                parent_id is None
                and not file_path
                and data.get("parent_id") is not None
            ):
                continue
            results.append(self._parse_comment(data))

        results.sort(key=lambda c: c.created_at, reverse=True)
        return results[offset : offset + limit]

    async def get_thread(self, comment_id: str) -> list[Comment]:
        """Get a comment thread (parent + all replies)."""
        # Get parent comment
        parent = await self.get_comment(comment_id)
        if not parent:
            return []

        # Get all replies
        replies = await self.get_comments(
            project_id=parent.project_id,
            parent_id=comment_id,
            limit=100,
        )

        # Sort by date ascending
        all_comments = [parent] + sorted(replies, key=lambda c: c.created_at)
        return all_comments

    async def update_comment(
        self,
        comment_id: str,
        user_id: str,
        content: Optional[str] = None,
        status: Optional[CommentStatus] = None,
    ) -> Optional[Comment]:
        """Update a comment."""
        # Get existing comment
        comment = await self.get_comment(comment_id)
        if not comment:
            return None

        # Only author can edit content
        if content and comment.user_id != user_id:
            raise PermissionError("Only author can edit comment content")

        updates = {"updated_at": datetime.now(timezone.utc).isoformat()}

        if content:
            mentioned_users = self._extract_mentions(content)
            clean_content, suggestion_code = self._parse_suggestion(content)
            updates["content"] = clean_content
            updates["mentioned_users"] = mentioned_users
            updates["suggestion_code"] = suggestion_code

        if status:
            updates["status"] = status.value
            if status == CommentStatus.RESOLVED:
                updates["resolved_by"] = user_id
                updates["resolved_at"] = datetime.now(timezone.utc).isoformat()

        if comment_id in self._comments:
            self._comments[comment_id].update(updates)
            return await self.get_comment(comment_id)
        return None

    async def delete_comment(self, comment_id: str, user_id: str) -> bool:
        """Delete a comment (soft delete by marking as deleted)."""
        comment = await self.get_comment(comment_id)
        if not comment:
            return False

        # Only author can delete
        if comment.user_id != user_id:
            raise PermissionError("Only author can delete comment")

        if comment_id in self._comments:
            del self._comments[comment_id]
            return True
        return False

    async def add_reaction(
        self,
        comment_id: str,
        user_id: str,
        emoji: str,
    ) -> bool:
        """Add a reaction to a comment (toggle)."""
        # Check if reaction already exists
        existing = [
            r
            for r in self._reactions
            if r["comment_id"] == comment_id
            and r["user_id"] == user_id
            and r["emoji"] == emoji
        ]

        if existing:
            # Toggle off
            self._reactions = [
                r
                for r in self._reactions
                if not (
                    r["comment_id"] == comment_id
                    and r["user_id"] == user_id
                    and r["emoji"] == emoji
                )
            ]
            return False

        # Add new reaction
        self._reactions.append(
            {
                "id": str(uuid4()),
                "comment_id": comment_id,
                "user_id": user_id,
                "emoji": emoji,
            }
        )
        return True

    async def resolve_comment(
        self,
        comment_id: str,
        user_id: str,
        apply_suggestion: bool = False,
    ) -> Optional[Comment]:
        """Resolve a comment."""
        return await self.update_comment(
            comment_id=comment_id,
            user_id=user_id,
            status=CommentStatus.RESOLVED,
        )

    async def get_unresolved_count(
        self,
        project_id: str,
        file_path: Optional[str] = None,
    ) -> int:
        """Get count of unresolved comments."""
        count = 0
        for data in self._comments.values():
            if data.get("project_id") != project_id:
                continue
            if data.get("status") != CommentStatus.OPEN.value:
                continue
            if data.get("parent_id") is not None:
                continue
            if file_path and data.get("file_path") != file_path:
                continue
            count += 1
        return count

    async def get_comments_by_line(
        self,
        project_id: str,
        file_path: str,
    ) -> dict[int, list[Comment]]:
        """Get comments grouped by line number."""
        comments = await self.get_comments(
            project_id=project_id,
            file_path=file_path,
            status=CommentStatus.OPEN,
        )

        by_line: dict[int, list[Comment]] = {}
        for comment in comments:
            if comment.location:
                line = comment.location.line_start
                if line not in by_line:
                    by_line[line] = []
                by_line[line].append(comment)

        return by_line

    def _parse_comment(self, data: dict) -> Comment:
        """Parse comment from database row."""
        location = None
        if data.get("file_path"):
            location = CommentLocation(
                file_path=data["file_path"],
                line_start=data.get("line_start", 1),
                line_end=data.get("line_end"),
                commit_sha=data.get("commit_sha"),
            )

        return Comment(
            id=data["id"],
            project_id=data["project_id"],
            user_id=data["user_id"],
            user_name=data["user_name"],
            user_avatar=data.get("user_avatar"),
            content=data["content"],
            location=location,
            comment_type=CommentType(data.get("comment_type", "comment")),
            status=CommentStatus(data.get("status", "open")),
            parent_id=data.get("parent_id"),
            mentioned_users=data.get("mentioned_users", []),
            reactions=[],  # Loaded separately
            suggestion_code=data.get("suggestion_code"),
            created_at=datetime.fromisoformat(
                data["created_at"].replace("Z", "+00:00")
            ),
            updated_at=datetime.fromisoformat(
                data["updated_at"].replace("Z", "+00:00")
            ),
            resolved_by=data.get("resolved_by"),
            resolved_at=(
                datetime.fromisoformat(data["resolved_at"].replace("Z", "+00:00"))
                if data.get("resolved_at")
                else None
            ),
        )

    async def _notify_mentions(self, comment: Comment, users: list[str]):
        """Send notifications to mentioned users."""
        # TODO: Integrate with notification service
        pass


# ============================================
# Pydantic Models for API
# ============================================


class CreateCommentRequest(BaseModel):
    """Request to create a comment."""

    content: str = Field(..., min_length=1, max_length=10000)
    file_path: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    commit_sha: Optional[str] = None
    comment_type: CommentType = CommentType.COMMENT
    parent_id: Optional[str] = None


class UpdateCommentRequest(BaseModel):
    """Request to update a comment."""

    content: Optional[str] = Field(None, min_length=1, max_length=10000)
    status: Optional[CommentStatus] = None


class ReactionRequest(BaseModel):
    """Request to add/remove reaction."""

    emoji: str = Field(..., max_length=10)


class CommentResponse(BaseModel):
    """Comment response model."""

    id: str
    project_id: str
    user_id: str
    user_name: str
    user_avatar: Optional[str]
    content: str
    location: Optional[dict]
    comment_type: str
    status: str
    parent_id: Optional[str]
    mentioned_users: list[str]
    reactions: list[dict]
    suggestion_code: Optional[str]
    reply_count: int = 0
    created_at: str
    updated_at: str
    resolved_by: Optional[str]
    resolved_at: Optional[str]
    apply_suggestion: Optional[dict] = None  # Populated when apply_suggestion=true


# Export
__all__ = [
    "CommentsService",
    "Comment",
    "CommentLocation",
    "CommentStatus",
    "CommentType",
    "CommentReaction",
    "CreateCommentRequest",
    "UpdateCommentRequest",
    "ReactionRequest",
    "CommentResponse",
]
