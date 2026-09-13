"""
backend/api/system_routes.py — public locality / system-status surface.

W6.3 — exposes a single PUBLIC (no-auth) endpoint so the frontend can
discover the backend's locality stance before login. The shape is
intentionally narrow:

  GET /api/system/status →
    {
      "locality":      "local-first" | "auto" | "cloud-first",
      "database":      "SQLite" | "Convex",
      "llm_provider":  "local-first" | "cloud" | <future router name>,
      "is_airgapped":  bool,
      "version":       <semver string>,
    }

What this endpoint deliberately does NOT do:
  - It does not probe outbound network. Doing so would defeat the
    air-gap commitment under local-first. `is_airgapped` is derived
    from the operator's pinned preference + the absence of cloud
    credentials.
  - It does not include user-scoped data. Anyone can hit this — it's
    a system-level posture indicator, not an identity claim.
  - It does not include any secret values (CLERK keys, OLLAMA tokens,
    etc.). Only structural mode names.

This endpoint is exempt from the W3.3 CSRF gate (GET) and from the
W5.3 offline auth gate (it's in the auth public-paths list in
backend/app.py). Adding `/api/system/status` to those gates would
defeat its purpose as a pre-login status indicator.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["System"])


def _locality_preference() -> str:
    """Normalize the operator's pinned locality preference."""
    raw = os.environ.get("VOS3_LOCALITY_PREFERENCE", "").strip().lower()
    if raw not in ("local-first", "cloud-first", "auto"):
        return "auto"
    return raw


def _database_label() -> str:
    """Which storage layer is currently active.

    The W5.1 dispatch checks VOS3_LOCALITY_PREFERENCE; we mirror that
    decision here so the UI shows the same value the repositories
    layer actually uses.
    """
    return "SQLite" if _locality_preference() == "local-first" else "Convex"


def _llm_provider_label() -> str:
    """Active LLM router name from the W6.25 dispatcher.

    Falls back to the operator's locality preference if the dispatcher
    module is unavailable (e.g. mid-deploy import failure). Never
    raises — this endpoint is a status indicator and must respond.
    """
    try:
        from services.llm_dispatcher import get_dispatcher

        return get_dispatcher().name
    except Exception as exc:  # pragma: no cover
        logger.debug("llm_dispatcher unavailable for status: %s", exc)
        # Synthesize a fallback so the UI still gets something useful.
        return "local-first" if _locality_preference() == "local-first" else "cloud"


def _is_airgapped() -> bool:
    """Derived from intent + missing cloud creds — NEVER from a probe.

    True iff BOTH:
      - operator pinned local-first locality
      - no cloud LLM credentials are present (OPENAI/ANTHROPIC/GOOGLE)

    The combination is what an air-gapped deployment actually looks
    like: the user explicitly opted into sovereignty AND the box has
    no keys to leak. A box with locality=local-first but cloud keys
    set is sovereign-by-policy but not strictly air-gapped — the UI
    surfaces that nuance.
    """
    if _locality_preference() != "local-first":
        return False
    cloud_keys = (
        os.environ.get("OPENAI_API_KEY", "").strip(),
        os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        os.environ.get("GOOGLE_API_KEY", "").strip(),
    )
    return not any(k for k in cloud_keys)


def _version() -> str:
    """Best-effort version. Pulled from backend/app.py if available."""
    try:
        from app import VOS3_VERSION

        return VOS3_VERSION
    except Exception:
        return "unknown"


@router.get("/status")
async def system_status() -> dict[str, Any]:
    """Return the backend's locality + LLM posture.

    Public — no auth required. The frontend fetches this on cold-mount
    to drive the locality context + UI badges + feature gates.
    """
    return {
        "locality": _locality_preference(),
        "database": _database_label(),
        "llm_provider": _llm_provider_label(),
        "is_airgapped": _is_airgapped(),
        "version": _version(),
    }


@router.get("/audit-logs")
async def audit_logs(
    limit: int = 100,
    offset: int = 0,
    app_id: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Return security-audit rows for the dashboard.

    Query parameters:
      limit   — max rows (1..500, default 100)
      offset  — pagination offset (default 0)
      app_id  — filter to a single app (optional)
      kind    — filter to a single event kind (optional;
                 e.g. "scope_violation", "path_traversal_attempt")

    Each row:
      {
        "id":         "<uuid>",
        "timestamp":  <ms>,
        "kind":       "<event kind>",
        "app_id":     "<uuid>" | null,
        "scope":      "<scope>" | null,
        "reason":     "<text>" | null,
        "details":    { ...JSON object... }
      }

    Rows are newest-first. The endpoint is public-shaped (no auth
    here) for now because the dashboard SSE is itself behind the
    operator's session — a future P5.x will add a tighter gate.
    """
    # Clamp inputs.
    try:
        limit_int = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit_int = 100
    try:
        offset_int = max(0, int(offset))
    except (TypeError, ValueError):
        offset_int = 0

    try:
        # Lazy imports so a partial backend boot doesn't import-fail
        # the rest of the system_routes surface.
        import json as _json
        from sqlalchemy import desc, select
        from core.database.sqlite_setup import SecurityAuditLog, get_session, init_db

        init_db()
        with get_session() as session:
            stmt = select(SecurityAuditLog).order_by(desc(SecurityAuditLog.timestamp))
            if app_id:
                stmt = stmt.where(SecurityAuditLog.appId == app_id)
            if kind:
                stmt = stmt.where(SecurityAuditLog.kind == kind)
            stmt = stmt.offset(offset_int).limit(limit_int)
            rows = session.execute(stmt).scalars().all()
            payload = []
            for r in rows:
                try:
                    details = _json.loads(r.details_json) if r.details_json else {}
                except (ValueError, TypeError):
                    details = {}
                payload.append(
                    {
                        "id": r.id,
                        "timestamp": r.timestamp,
                        "kind": r.kind,
                        "app_id": r.appId,
                        "scope": r.scope,
                        "reason": r.reason,
                        "details": details,
                    }
                )
        return {
            "rows": payload,
            "count": len(payload),
            "limit": limit_int,
            "offset": offset_int,
            "filters": {"app_id": app_id, "kind": kind},
        }
    except ImportError:
        return {
            "rows": [],
            "count": 0,
            "limit": limit_int,
            "offset": offset_int,
            "filters": {"app_id": app_id, "kind": kind},
        }


@router.get("/sync/status")
async def sync_status() -> dict[str, Any]:
    """Return the local→cloud sync watermark and dirty-row counts.

    P3.4 — drives the UI's "synced N seconds ago / 3 pending" pill.
    The endpoint is intentionally cheap (two SQL aggregates per
    table) so it can be polled at a 5–10s cadence without warming
    the connection pool.

    Shape:
      {
        "last_synced_at_ms": int | null,    # MAX(lastSyncedAt) across tables
        "dirty_count":       int,           # total rows still pending push
        "tables": {
          "projects":            { "dirty": int, "last_synced_at_ms": int|null },
          "chatSessions":        { "dirty": int, "last_synced_at_ms": int|null },
          "chatSessionMessages": { "dirty": int, "last_synced_at_ms": int|null },
        }
      }

    Falls back to a zero-shape on cloud-first deployments — the
    journal columns still exist but no rows are ever marked dirty.
    """
    try:
        from services.sync_engine import sync_status_snapshot

        return sync_status_snapshot()
    except ImportError:
        # Sync engine unavailable (e.g. cold boot before module import)
        # — return a stable empty shape so the UI doesn't 500.
        return {
            "last_synced_at_ms": None,
            "dirty_count": 0,
            "tables": {},
        }


__all__ = ["router"]
