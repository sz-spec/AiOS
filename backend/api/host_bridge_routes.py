"""
backend/api/host_bridge_routes.py — Host Bridge HTTP surface (P6.3).

Routes
------
  POST /api/host_bridge/execute
    Body: { app_id, target_system, payload: {action, ...} }
    → 200 { status:"ok", executor, result }
       | { status:"awaiting_approval", approval_id, reason }
    → 403 if the app lacks `host.automation` (or is isolated, or
       its workspace_id doesn't match the host).
    → 400 on missing fields / unknown target / unknown action.

  GET  /api/host_bridge/approvals
    Bearer-auth-only.
    → 200 { approvals: [...] }

  GET  /api/host_bridge/approvals/{id}
    → 200 { ...row... } | 404

  POST /api/host_bridge/approvals/{id}/approve
    Body: { approver_user_id, signature_hex, public_key_hex }
    → 200 { status:"ok", executor, result } once the held call runs.
    → 200 { status:"error", error } on signature/policy failure.

  POST /api/host_bridge/approvals/{id}/reject
    Symmetric to /approve; terminates the row without executing.

The Bearer-auth gate is shared with every other operator-facing
route. The app-side `app_id` field is the sandboxed-app identity;
the route does NOT trust the app's manifest declarations beyond
what the gate validates.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import AuthenticatedUser, get_current_user
from services.host_bridge import (
    HOST_BRIDGE,
    HostActionUnknown,
    HostBridgeError,
    HostScopeDenied,
    HostTargetUnknown,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/host_bridge", tags=["HostBridge"])


def _require_str(body: dict, field: str) -> str:
    val = body.get(field)
    if not isinstance(val, str) or not val:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": f"{field} required"},
        )
    return val


@router.post("/execute")
async def execute(
    payload: dict,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Run a sandboxed app's host-automation request."""
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": "body must be a JSON object"},
        )
    app_id = _require_str(payload, "app_id")
    target_system = _require_str(payload, "target_system")
    script_payload = payload.get("payload")
    if not isinstance(script_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": "payload (dict) required"},
        )
    try:
        result = HOST_BRIDGE.execute_host_automation(
            app_id=app_id,
            target_system=target_system,
            script_payload=script_payload,
            operator_user_id=user.id,
        )
    except HostScopeDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "scope_denied", "reason": str(exc)},
        ) from exc
    except (HostTargetUnknown, HostActionUnknown) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "unknown_target", "reason": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": str(exc)},
        ) from exc
    except HostBridgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "executor_failed", "reason": str(exc)},
        ) from exc
    return result.to_dict()


@router.get("/approvals")
async def list_approvals(
    user: AuthenticatedUser = Depends(get_current_user),
    app_id: str = "",
) -> dict[str, Any]:
    """List pending approval rows. Optional `?app_id=` narrows."""
    rows = HOST_BRIDGE.list_pending_approvals(app_id=app_id or None)
    return {"approvals": rows}


@router.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    row = HOST_BRIDGE.get_approval(approval_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "approval_not_found", "approval_id": approval_id},
        )
    return row


@router.post("/_sign")
async def sign_approval_decision(
    payload: dict,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """P7.2 — Server-side Ed25519 signer used by the ApprovalDrawer.

    The frontend collects a biometric gesture (TouchID / Windows Hello /
    confirm() fallback) as proof-of-presence, then POSTs the approval_id
    + decision here. The backend's keyring-pinned workflow-signing key
    signs the canonical bytes server-side and returns the (sig, pub,
    user_id) triple. The UI then POSTs that triple to /approve or
    /reject — those routes' verifier (which pins the public key to the
    keyring) catches any cross-user signature attempt.

    Trust model:
      * The Bearer JWT IS the operator's identity (Clerk-issued).
      * The keyring holds the workflow-signing private key (P6.1).
      * The signing key is bound to the host (machine-id seed) — copying
        secrets.enc to another box invalidates it.
      * The frontend never touches the private key.

    Body shape:
        {"approval_id": "<uuid>", "decision": "approve" | "reject"}

    Response:
        {"approval_id":      "<uuid>",
         "approver_user_id": "<operator clerk id>",
         "decision":         "approve" | "reject",
         "signature_hex":    "<128 hex chars · Ed25519>",
         "public_key_hex":   "<64 hex chars>"}
    """
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": "body must be JSON object"},
        )
    approval_id = _require_str(payload, "approval_id")
    decision = _require_str(payload, "decision")
    if decision not in ("approve", "reject"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "bad_request",
                "reason": "decision must be 'approve' or 'reject'",
            },
        )

    # Verify the approval exists + is still pending. Signing a row that
    # has already terminated is wasted work and would let an attacker
    # spam the audit log; reject early.
    row = HOST_BRIDGE.get_approval(approval_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "approval_not_found", "approval_id": approval_id},
        )
    if row["status"] != "awaiting_human_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "approval_already_terminal",
                "current_status": row["status"],
            },
        )

    try:
        from services.app_crypto import sign_manifest
        from services.crypto_keyring import get_or_mint_workflow_signing_key
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "crypto_unavailable", "reason": str(exc)},
        ) from exc

    priv_hex, pub_hex = get_or_mint_workflow_signing_key()
    canon = {
        "approval_id": approval_id,
        "approver_user_id": user.id,
        "decision": decision,
    }
    try:
        sig_hex = sign_manifest(canon, private_key_hex=priv_hex)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[host_bridge] _sign failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "sign_failed"},
        ) from exc

    return {
        "approval_id": approval_id,
        "approver_user_id": user.id,
        "decision": decision,
        "signature_hex": sig_hex,
        "public_key_hex": pub_hex,
    }


@router.post("/approvals/{approval_id}/approve")
async def approve_and_execute(
    approval_id: str,
    payload: dict,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Verify the operator's Ed25519 signature, then run the held call."""
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": "body must be JSON object"},
        )
    approver = _require_str(payload, "approver_user_id")
    sig = _require_str(payload, "signature_hex")
    pub = _require_str(payload, "public_key_hex")
    result = HOST_BRIDGE.approve_and_execute(
        approval_id=approval_id,
        approver_user_id=approver,
        signature_hex=sig,
        public_key_hex=pub,
    )
    return result.to_dict()


@router.post("/approvals/{approval_id}/reject")
async def reject_approval(
    approval_id: str,
    payload: dict,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "bad_request", "reason": "body must be JSON object"},
        )
    approver = _require_str(payload, "approver_user_id")
    sig = _require_str(payload, "signature_hex")
    pub = _require_str(payload, "public_key_hex")
    result = HOST_BRIDGE.reject_approval(
        approval_id=approval_id,
        approver_user_id=approver,
        signature_hex=sig,
        public_key_hex=pub,
    )
    return result.to_dict()


__all__ = ["router"]
