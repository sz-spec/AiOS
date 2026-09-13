"""
Centralized FastAPI Dependencies
=================================
Single source of truth for auth and common dependencies.

ALL route files MUST import get_current_user from this module:

    from api.deps import get_current_user, AuthenticatedUser

NEVER define a local get_current_user in a route file.
"""

from middleware.auth import (
    AuthenticatedUser,
    get_current_user,
    require_permission,
    require_org_membership,
    DEV_MODE,
)

__all__ = [
    "AuthenticatedUser",
    "get_current_user",
    "require_permission",
    "require_org_membership",
    "DEV_MODE",
]
