"""
backend/api/p2p_routes.py — P6.2 Air-Gapped P2P Mesh HTTP surface.

Two endpoints, both Bearer-auth-gated:

  POST /api/p2p/sync/compare
    Body: {
      "workspace_id": str,         # peer asserts which workspace
                                   # it belongs to
      "table":         str,        # e.g. "projects" / "chatSessions"
      "fields":        list[str],  # optional projection hint
    }
    → 200 { workspace_id, table, summary: {hash, row_count, latest_updated_at} }
    → 403 if `workspace_id` doesn't match the local node's
            VOS3_P2P_WORKSPACE_ID env (or the configured workspace).
            ALSO emits a `local_tampering_blocked` audit row.

  POST /api/p2p/sync/delta
    Body: {
      "workspace_id": str,
      "table":         str,
      "since_ms":      int,
    }
    → 200 { workspace_id, table, rows: [...] }
    → 403 on workspace mismatch (with audit row).

Workspace gating
----------------
The local node's workspace_id comes from one of:
  1. `VOS3_P2P_WORKSPACE_ID` env var (tests + the Tauri sidecar set
     this when launching the backend).
  2. The first workspace discovered in the `apps` table (best-effort
     for in-dev backends that haven't been provisioned yet).

A request whose `workspace_id` matches that value is honored;
anything else is rejected with audit. The routes never trust the
peer's assertion BLINDLY — but in the absence of a vault-issued
workspace certificate the local match is the strongest check we
can perform on a plain-HTTP endpoint. In production the same
endpoints sit behind a TLS-terminating reverse proxy that's
provisioned with the Tauri shell's workspace certificate; that
work belongs to a separate phase.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/p2p", tags=["P2P"])


# ---------------------------------------------------------------------------
# Local workspace resolution
# ---------------------------------------------------------------------------


def _resolve_local_workspace_id() -> Optional[str]:
    """Return the workspace id this node belongs to. None when unset.

    A None return forces every request to 403 — operators MUST set
    `VOS3_P2P_WORKSPACE_ID` before the P2P routes are useful.
    """
    explicit = os.getenv("VOS3_P2P_WORKSPACE_ID", "").strip()
    if explicit:
        return explicit
    # Fallback: pick any workspace from the local apps registry.
    try:
        from core.database.sqlite_setup import App, get_session, init_db

        init_db()
        with get_session() as session:
            row = (
                session.query(App.workspaceId)
                .filter(
                    App.workspaceId.isnot(None),
                )
                .first()
            )
            if row and row[0]:
                return row[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[p2p] workspace fallback lookup failed: %s", exc)
    return None


def _audit_blocked(*, kind: str, reason: str, details: dict) -> None:
    """Record a denied request to securityAuditLog.

    Best-effort — the route returns 403 regardless of whether the
    audit row landed."""
    try:
        from services.app_sandbox import _record_security_event

        _record_security_event(
            kind=kind,
            reason=f"p2p route blocked: {reason}",
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[p2p] audit failed: %s", exc)


# ---------------------------------------------------------------------------
# Request validators
# ---------------------------------------------------------------------------


def _validate_body(payload: Any, *, require_since: bool = False) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": "body must be a JSON object"},
        )
    workspace_id = payload.get("workspace_id")
    table = payload.get("table")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": "workspace_id required"},
        )
    if not isinstance(table, str) or not table:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": "table required"},
        )
    out = {"workspace_id": workspace_id, "table": table}
    if require_since:
        since = payload.get("since_ms", 0)
        if not isinstance(since, int) or since < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "bad_request",
                    "reason": "since_ms must be a non-negative int",
                },
            )
        out["since_ms"] = since
    fields = payload.get("fields")
    if fields is not None:
        if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "bad_request",
                    "reason": "fields must be a list of strings",
                },
            )
        out["fields"] = fields
    return out


def _gate_workspace(client_id: str, body: dict) -> str:
    """Validate the peer-asserted workspace_id against the local one.

    Returns the matched workspace_id. Raises HTTPException(403) +
    records a `local_tampering_blocked` audit row on mismatch.
    """
    local = _resolve_local_workspace_id()
    if not local:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "p2p_disabled",
                "reason": "local workspace_id not configured",
            },
        )
    if body["workspace_id"] != local:
        _audit_blocked(
            kind="local_tampering_blocked",
            reason="workspace_id_mismatch",
            details={
                "client_id": client_id,
                "peer_workspace": body["workspace_id"],
                "local_workspace": local,
                "table": body["table"],
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "workspace_mismatch",
                "reason": "peer workspace_id does not match local node",
            },
        )
    return local


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/sync/compare")
async def sync_compare(
    payload: dict,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Return a state-summary hash for the requested table + workspace.

    The peer compares its local summary to this; if they match, no
    delta is needed.
    """
    body = _validate_body(payload, require_since=False)
    workspace_id = _gate_workspace(user.id, body)

    from services.p2p_sync import compute_table_summary

    summary = compute_table_summary(
        body["table"],
        workspace_id=workspace_id,
        fields=body.get("fields"),
    )
    return {
        "workspace_id": workspace_id,
        "table": body["table"],
        "summary": summary,
    }


@router.post("/sync/delta")
async def sync_delta(
    payload: dict,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Return rows of `table` in `workspace_id` updated since `since_ms`."""
    body = _validate_body(payload, require_since=True)
    workspace_id = _gate_workspace(user.id, body)

    from services.p2p_sync import compute_table_delta

    rows = compute_table_delta(
        body["table"],
        workspace_id=workspace_id,
        since_ms=body["since_ms"],
    )
    return {
        "workspace_id": workspace_id,
        "table": body["table"],
        "rows": rows,
    }


@router.get("/peers")
async def list_peers(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Snapshot the DB-backed `discoveredPeers` table.

    Useful for the dashboard's mesh-status panel + for testing.
    Returns every row regardless of status so operators see rejected
    peers too.
    """
    out = []
    try:
        from core.database.sqlite_setup import (
            DiscoveredPeer,
            get_session,
            init_db,
        )

        init_db()
        with get_session() as session:
            rows = (
                session.query(DiscoveredPeer)
                .order_by(
                    DiscoveredPeer.lastSeenAt.desc(),
                )
                .all()
            )
            for r in rows:
                out.append(
                    {
                        "node_id": r.nodeId,
                        "workspace_id": r.workspaceId,
                        "host": r.host,
                        "sync_port": r.syncPort,
                        "status": r.status,
                        "verified": r.verified,
                        "first_seen_at": r.firstSeenAt,
                        "last_seen_at": r.lastSeenAt,
                    }
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[p2p] peers list failed: %s", exc)
    return {"peers": out}


__all__ = ["router"]
