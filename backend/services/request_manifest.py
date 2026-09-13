"""
Request Manifest — Per-Request Privacy Intent
==============================================

A `RequestManifest` is built once per HTTP request before the model router
is consulted. It carries the privacy / locality intent for that single
request, assembled from three independent sources:

    1. **Request body field** — `require_local_inference: bool` on the POST
       (e.g. `ChatRequest`, `GenerateRequest`). Per-call explicit opt-in.
    2. **HTTP header** — `X-VOS3-Require-Local: true`. Useful for SDKs
       that want to flip the policy without modifying request bodies.
    3. **User metadata** — `requires_local_inference` flag in Clerk public
       metadata. Persistent enterprise / per-user setting, configured once
       in account settings UI.

OR-logic — **any** True value forces local inference. Defaults to False.

This module also enforces the strict invariant that once local inference
is required for a request, the request must NEVER fall back to a cloud
provider. If local Ollama is unavailable, the request is denied with
HTTP 503 + Retry-After.

Spec reference:
- v20.4 Phase 3 — Privacy Mandate
- EU AI Act Article 12 (Annex III, 2026-08-02)
- VPKIntentManifest.requires_local_inference (vpacker.py)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException, Request, status

if TYPE_CHECKING:
    from middleware.auth import AuthenticatedUser

logger = logging.getLogger("vos3.request_manifest")


# ---------------------------------------------------------------------------
# Manifest data class
# ---------------------------------------------------------------------------


@dataclass
class RequestManifest:
    """The privacy / locality intent for a single HTTP request."""

    requires_local_inference: bool = False
    source: str = (
        "default"  # which signal won — "body" | "header" | "user_metadata" | "eu_region" | "default"
    )

    def __bool__(self) -> bool:
        return self.requires_local_inference


# ---------------------------------------------------------------------------
# Builder — assembles from the three sources
# ---------------------------------------------------------------------------


def build_request_manifest(
    *,
    body_require_local: Optional[bool] = None,
    http_request: Optional[Request] = None,
    user: Optional["AuthenticatedUser"] = None,
) -> RequestManifest:
    """Build a RequestManifest from any combination of the three sources.

    OR-logic — any True forces local inference. The `source` field records
    which signal was decisive (for audit logs).
    """
    # 1. Explicit request body field — highest priority for per-call control
    if body_require_local is True:
        return RequestManifest(requires_local_inference=True, source="body")

    # 2. HTTP header — `X-VOS3-Require-Local`
    if http_request is not None:
        hdr = http_request.headers.get("X-VOS3-Require-Local", "").strip().lower()
        if hdr in ("1", "true", "yes"):
            return RequestManifest(requires_local_inference=True, source="header")

    # 3. User persistent metadata — Clerk public metadata
    if user is not None:
        meta = getattr(user, "metadata", None) or {}
        if isinstance(meta, dict):
            if bool(meta.get("requires_local_inference", False)):
                return RequestManifest(
                    requires_local_inference=True, source="user_metadata"
                )

        # 4. EU AI Act Article 12 — region-driven mandatory local. The
        # regional-policy module already enforces this for its routing path,
        # but we surface it here so chat / codegen can short-circuit BEFORE
        # the router is consulted at all.
        if getattr(user, "requires_local_inference", None):
            try:
                if user.requires_local_inference():
                    return RequestManifest(
                        requires_local_inference=True, source="eu_region"
                    )
            except Exception:
                pass

    return RequestManifest(requires_local_inference=False, source="default")


# ---------------------------------------------------------------------------
# Enforcement — the strict invariant
# ---------------------------------------------------------------------------


class LocalInferenceUnavailableError(HTTPException):
    """503 raised when local inference is required but Ollama is offline.

    The strict invariant: once a request demands local-only routing, the
    system MUST NOT fall back to a cloud provider. This error guarantees
    the request fails closed.
    """

    def __init__(self, source: str, role: str):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "local_inference_unavailable",
                "message": (
                    "This request requires local inference (privacy mandate). "
                    "The local Ollama instance is not reachable. Cloud fallback "
                    "is not permitted under this policy. Please retry shortly "
                    "or contact your administrator if Ollama should be running."
                ),
                "source": source,
                "role": role,
                "retry_after_seconds": 30,
            },
            headers={"Retry-After": "30"},
        )


# Roles get the same local-model mapping as services.regional_policy +
# src.efficiency.router so all three paths agree.
_LOCAL_BY_ROLE: dict[str, str] = {
    "architect": "local-default",
    "frontend": "local-default",
    "backend": "local-default",
    "tester": "local-snappy",
    "reviewer": "local-default",
    "coding": "local-code",
    "coding-complex": "local-code",
    "researcher": "local-default",
    "researcher-deep": "local-default",
}


def enforce_local_or_503(
    manifest: RequestManifest,
    role: str,
    *,
    user: Optional["AuthenticatedUser"] = None,
) -> Optional[str]:
    """Strict invariant gate. Returns the chosen local model name when local
    inference is required AND available; raises 503 when required AND
    unavailable. Returns None when local inference is NOT required (caller
    proceeds with normal cloud routing).

    Side-effect: emits a structured audit log on every enforcement decision
    so the privacy trail is queryable post-hoc via the Transparency API.
    """
    if not manifest.requires_local_inference:
        return None

    # Local inference is mandatory. Probe Ollama availability.
    available = _check_ollama_available()
    if not available:
        # Strict invariant — never silently fall back to cloud.
        logger.warning(
            "PRIVACY_MANDATE_DENY source=%s role=%s user=%s reason=ollama_unavailable",
            manifest.source,
            role,
            getattr(user, "id", "<anon>"),
        )
        raise LocalInferenceUnavailableError(source=manifest.source, role=role)

    chosen = _LOCAL_BY_ROLE.get(role, "local-snappy")
    logger.info(
        "PRIVACY_MANDATE_ROUTE source=%s role=%s model=%s user=%s",
        manifest.source,
        role,
        chosen,
        getattr(user, "id", "<anon>"),
    )
    return chosen


def _check_ollama_available() -> bool:
    """Single source of truth for Ollama health — delegates to the existing
    probe in `tool_provider.py` to avoid drift."""
    try:
        from ai.llm.tool_provider import _is_ollama_available

        return bool(_is_ollama_available())
    except Exception:
        return False


__all__ = [
    "RequestManifest",
    "LocalInferenceUnavailableError",
    "build_request_manifest",
    "enforce_local_or_503",
]
