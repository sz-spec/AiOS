# Re-export from integrations/comments.py without triggering integrations/__init__.py
import importlib.util, pathlib

_spec = importlib.util.spec_from_file_location(
    "integrations.comments",
    pathlib.Path(__file__).parent.parent / "integrations" / "comments.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

CommentsService = _mod.CommentsService
Comment = _mod.Comment
CommentLocation = _mod.CommentLocation
CommentStatus = _mod.CommentStatus
CommentType = _mod.CommentType
CreateCommentRequest = _mod.CreateCommentRequest
UpdateCommentRequest = _mod.UpdateCommentRequest
ReactionRequest = _mod.ReactionRequest
CommentResponse = _mod.CommentResponse
