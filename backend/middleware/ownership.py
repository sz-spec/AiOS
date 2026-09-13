# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
Centralized resource-ownership enforcement for FastAPI routes.

Eliminates horizontal privilege escalation (B-H-2, F-H-1, F-H-2 in the
v20.6 audit) by routing every mutating endpoint through a uniform
ownership check.

Per V20_6_MASTER_REPAIR_SPEC.md §3.3.

Usage
-----

    from middleware.auth import AuthenticatedUser, get_current_user
    from middleware.ownership import require_ownership

    @router.delete("/projects/{project_id}")
    @require_ownership("project", id_param="project_id")
    async def delete_project(
        project_id: str,
        user: AuthenticatedUser = Depends(get_current_user),
    ):
        ...

A resolver must be registered at startup for each ResourceType:

    from middleware.ownership import register_owner_resolver
    from db.convex import get_convex_client

    async def _resolve_project_owner(project_id: str) -> str | None:
        db = get_convex_client()
        doc = await db.query("projects:get", {"id": project_id})
        return doc.get("ownerId") if doc else None

    register_owner_resolver("project", _resolve_project_owner)

Honest scoping
--------------
This module ships the DECORATOR + RESOLVER REGISTRY. Migrating every
existing mutating route to use it is a separate, scoped change that
spans 100+ endpoints across api/billing, api/agents, api/v_core,
api/memory, etc. The migration plan is documented in §3.3 of the
master spec; the CI coverage test (test_ownership_coverage.py) is
provided alongside.
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, Literal, Optional

from fastapi import HTTPException

from middleware.auth import AuthenticatedUser

ResourceType = Literal[
    "project",
    "agent",
    "billing",
    "org",
    "memory",
    "file",
    "workflow",
    "entity",
]

# Map resource type -> async fn(resource_id) -> owner_user_id
_OWNER_RESOLVERS: Dict[ResourceType, Callable[[str], Awaitable[Optional[str]]]] = {}


def register_owner_resolver(
    resource: ResourceType,
    resolver: Callable[[str], Awaitable[Optional[str]]],
) -> None:
    """Register the async function that, given a resource ID, returns
    the owner's user ID (or None when the resource doesn't exist).

    Call once per ResourceType during FastAPI lifespan startup.
    """
    _OWNER_RESOLVERS[resource] = resolver


def has_resolver(resource: ResourceType) -> bool:
    """Test helper — does a resolver exist for this resource type?"""
    return resource in _OWNER_RESOLVERS


def require_ownership(
    resource: ResourceType,
    id_param: str = "id",
    *,
    admin_permission: Optional[str] = None,
):
    """Decorator: enforce that the calling user owns the resource being
    accessed.

    Parameters
    ----------
    resource : ResourceType
        Name of the resolver to consult — must have been registered via
        ``register_owner_resolver(resource, ...)`` at startup.
    id_param : str
        Name of the path/query/body parameter holding the resource ID.
    admin_permission : str | None
        If set and the user has this permission, ownership is bypassed
        (e.g. ``"project:admin"``).

    Behavior
    --------
    * 401 — no AuthenticatedUser found in the call args.
    * 400 — id_param missing from the request.
    * 404 — resource ID not found by the resolver.
    * 500 — no resolver registered for this resource type (developer
            misconfiguration).
    * 403 — user is neither the owner nor an admin.
    """

    def decorator(func: Callable[..., Awaitable[Any]]):
        sig = inspect.signature(func)
        if id_param not in sig.parameters:
            raise TypeError(
                f"@require_ownership({resource!r}, id_param={id_param!r}) — "
                f"endpoint {func.__qualname__} has no parameter named "
                f"{id_param!r}. Either rename your path parameter or pass "
                f"id_param=<your_param_name> to the decorator."
            )

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Find the AuthenticatedUser argument.
            user: Optional[AuthenticatedUser] = kwargs.get("user")
            if user is None:
                for v in list(args) + list(kwargs.values()):
                    if isinstance(v, AuthenticatedUser):
                        user = v
                        break
            if user is None:
                raise HTTPException(401, "Authentication required")

            # Resolve the resource ID from kwargs or positional args.
            resource_id = kwargs.get(id_param)
            if resource_id is None:
                bound = sig.bind_partial(*args, **kwargs)
                resource_id = bound.arguments.get(id_param)
            if resource_id is None:
                raise HTTPException(400, f"Missing {id_param}")

            resolver = _OWNER_RESOLVERS.get(resource)
            if resolver is None:
                raise HTTPException(
                    500,
                    f"No owner resolver registered for resource '{resource}' "
                    f"— call register_owner_resolver() at startup",
                )

            owner_id = await resolver(str(resource_id))
            if owner_id is None:
                raise HTTPException(404, f"{resource} {resource_id} not found")

            is_admin = admin_permission is not None and user.has_permission(
                admin_permission
            )
            if owner_id != user.id and not is_admin:
                raise HTTPException(
                    403,
                    f"User {user.id} not authorized for " f"{resource} {resource_id}",
                )
            return await func(*args, **kwargs)

        return wrapper

    return decorator


__all__ = [
    "ResourceType",
    "register_owner_resolver",
    "has_resolver",
    "require_ownership",
]
