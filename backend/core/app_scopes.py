"""
App OAuth 2.0 Scopes
====================
OAuth 2.0 scope model for VOS3 app permissions.
Follows incremental authorization — apps request scopes as needed, not upfront.
"""

from enum import Enum
from typing import List, Set
from functools import wraps

from fastapi import HTTPException, Request


class AppScope(str, Enum):
    """Available OAuth 2.0 scopes for app API access."""

    ENTITIES_READ = "vos3:entities:read"
    ENTITIES_WRITE = "vos3:entities:write"
    RECORDS_READ = "vos3:records:read"
    RECORDS_WRITE = "vos3:records:write"
    WORKFLOWS_EXECUTE = "vos3:workflows:execute"
    AI_GENERATE = "vos3:ai:generate"
    FILES_READ = "vos3:files:read"
    FILES_WRITE = "vos3:files:write"
    KERNEL_EXECUTE = "vos3:kernel:execute"


# Scope descriptions for consent screen
SCOPE_DESCRIPTIONS = {
    AppScope.ENTITIES_READ: "View your business entities and their schemas",
    AppScope.ENTITIES_WRITE: "Create and modify business entities",
    AppScope.RECORDS_READ: "Read data records",
    AppScope.RECORDS_WRITE: "Create, update, and delete data records",
    AppScope.WORKFLOWS_EXECUTE: "Execute automated workflows",
    AppScope.AI_GENERATE: "Generate text and code using AI models",
    AppScope.FILES_READ: "Read project files",
    AppScope.FILES_WRITE: "Create and modify project files",
    AppScope.KERNEL_EXECUTE: "Execute programs on the VOS3 kernel",
}

# Scope categories for grouping in consent UI
SCOPE_CATEGORIES = {
    "Data": [
        AppScope.ENTITIES_READ,
        AppScope.ENTITIES_WRITE,
        AppScope.RECORDS_READ,
        AppScope.RECORDS_WRITE,
    ],
    "Automation": [AppScope.WORKFLOWS_EXECUTE],
    "AI": [AppScope.AI_GENERATE],
    "Files": [AppScope.FILES_READ, AppScope.FILES_WRITE],
    "Kernel": [AppScope.KERNEL_EXECUTE],
}


def validate_scopes(requested: List[str]) -> List[AppScope]:
    """Validate and parse scope strings. Raises ValueError for invalid scopes."""
    valid = []
    for scope_str in requested:
        try:
            valid.append(AppScope(scope_str))
        except ValueError:
            raise ValueError(f"Invalid scope: {scope_str}")
    return valid


def check_scopes(granted: Set[str], required: List[str]) -> bool:
    """Check if all required scopes are in the granted set."""
    return all(s in granted for s in required)


def requires_scope(*scopes: str):
    """Decorator to require OAuth scopes on an endpoint.

    Usage:
        @requires_scope("vos3:records:read")
        async def list_records(request: Request):
            ...
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if request is None:
                request = kwargs.get("request")

            if request:
                granted = getattr(request.state, "app_scopes", set())
                missing = [s for s in scopes if s not in granted]
                if missing:
                    raise HTTPException(
                        status_code=403,
                        detail=f"Missing required scopes: {', '.join(missing)}",
                    )

            return await func(*args, **kwargs)

        return wrapper

    return decorator
