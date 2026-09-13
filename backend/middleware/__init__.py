"""
VOS3 Middleware
===============
Authentication and other middleware for the FastAPI application.
"""

from .auth import (
    verify_auth,
    get_current_user,
    require_permission,
    AuthenticatedUser,
)

__all__ = [
    "verify_auth",
    "get_current_user",
    "require_permission",
    "AuthenticatedUser",
]
