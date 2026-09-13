"""
Tests for api/comments.py shim.

Verifies that the shim correctly re-exports all expected names from
integrations/comments.py without going through integrations/__init__.py.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_shim_imports_all_expected_names():
    """All expected names are importable from the shim."""
    from api.comments import (
        CommentsService,
        Comment,
        CommentLocation,
        CommentStatus,
        CommentType,
        CreateCommentRequest,
        UpdateCommentRequest,
        ReactionRequest,
        CommentResponse,
    )

    assert CommentsService is not None
    assert Comment is not None
    assert CommentLocation is not None
    assert CommentStatus is not None
    assert CommentType is not None
    assert CreateCommentRequest is not None
    assert UpdateCommentRequest is not None
    assert ReactionRequest is not None
    assert CommentResponse is not None


def test_shim_classes_are_from_correct_source():
    """CommentsService comes from integrations/comments, not a stub."""
    from api.comments import CommentsService as ShimService

    # The shim loads via spec_from_file_location("integrations.comments", ...)
    # so the class's __module__ should reference that name
    assert ShimService.__name__ == "CommentsService"
    assert "comments" in ShimService.__module__


def test_comment_status_enum_values():
    """CommentStatus enum has OPEN and RESOLVED."""
    from api.comments import CommentStatus

    names = [s.name for s in CommentStatus]
    assert "OPEN" in names
    assert "RESOLVED" in names


def test_comment_type_enum_values():
    """CommentType enum has expected variants."""
    from api.comments import CommentType

    names = [t.name for t in CommentType]
    assert "COMMENT" in names
    assert "SUGGESTION" in names


def test_create_comment_request_validation():
    """CreateCommentRequest validates content correctly."""
    from api.comments import CreateCommentRequest

    req = CreateCommentRequest(content="test comment")
    assert req.content == "test comment"
    # Optional fields default to None / default CommentType
    assert req.file_path is None
    assert req.line_start is None


def test_create_comment_request_empty_content_fails():
    """CreateCommentRequest rejects empty content (min_length=1)."""
    import pytest
    from pydantic import ValidationError
    from api.comments import CreateCommentRequest

    with pytest.raises(ValidationError):
        CreateCommentRequest(content="")


def test_update_comment_request():
    """UpdateCommentRequest allows partial updates."""
    from api.comments import UpdateCommentRequest

    req = UpdateCommentRequest(content="new content")
    assert req.content == "new content"
    assert req.status is None


def test_reaction_request():
    """ReactionRequest validates emoji field."""
    from api.comments import ReactionRequest

    req = ReactionRequest(emoji="👍")
    assert req.emoji == "👍"


def test_comment_response_model():
    """CommentResponse is a Pydantic BaseModel with required fields."""
    from api.comments import CommentResponse

    resp = CommentResponse(
        id="c1",
        project_id="proj1",
        user_id="user1",
        user_name="Test User",
        user_avatar=None,
        content="Hello",
        location=None,
        comment_type="comment",
        status="open",
        parent_id=None,
        mentioned_users=[],
        reactions=[],
        suggestion_code=None,
        reply_count=0,
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
        resolved_by=None,
        resolved_at=None,
    )
    assert resp.id == "c1"
    assert resp.reply_count == 0
