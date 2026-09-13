"""
backend/api/transparency_routes.py — Phase 26 (Gap G3)
=======================================================

Live cryptographic transparency surface over the policy-integrity Merkle log.
Mounted under ``/api`` by router_registry → canonical paths:

  GET  /api/v1/transparency/root                — current signed tree head (STH)
  GET  /api/v1/transparency/proof?event_id=...  — live inclusion proof

The proof endpoint is no longer a stub: it calls the active
``PolicyTransparencyLedger`` (RFC-6962 SHA-256 Merkle log) to dynamically
generate an inclusion proof (audit path) for the queried event against the
CURRENT signed root. A non-existent / malformed event id is **fail-closed**
to HTTP 404 with a ``[SECURITY]`` tracking log — never a silent empty 200.

Auth: requires an authenticated user (Bearer JWT), matching every other route
module's convention. The returned payloads carry only opaque ids + schema
hashes (no PII), but the endpoint is gated to match house policy.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from api.deps import AuthenticatedUser, get_current_user

logger = logging.getLogger("vos3.security.transparency")

router = APIRouter()

_MAX_EVENT_ID_LEN = 256


@router.get("/v1/transparency/root", summary="Current signed tree head (STH)")
async def transparency_root(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Return the current Merkle root, its tree size, and an ECDSA-P256 signed
    tree head plus the public key needed to verify it."""
    from services.policy_transparency import get_policy_transparency_ledger

    return get_policy_transparency_ledger().signed_tree_head()


@router.get("/v1/transparency/proof", summary="Live cryptographic inclusion proof")
async def transparency_proof(
    event_id: str = Query(..., min_length=1, max_length=_MAX_EVENT_ID_LEN),
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Generate a live RFC-6962 inclusion proof for ``event_id`` against the
    current signed root. Fail-closed 404 for any unknown / invalid id."""
    from services.policy_transparency import get_policy_transparency_ledger

    proof = get_policy_transparency_ledger().generate_proof(event_id)
    if proof is None:
        logger.warning(
            "[SECURITY][transparency] proof requested for unknown event_id=%r "
            "by user=%s -> 404",
            event_id[:_MAX_EVENT_ID_LEN],
            getattr(user, "id", "<unknown>"),
        )
        raise HTTPException(
            status_code=404, detail="event_id not found in transparency log"
        )
    return proof


@router.get(
    "/v1/compliance/attest",
    summary="Self-attesting compliance bundle (G13): ledger STH + TPM checkpoint + proofs",
)
async def compliance_attest(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Phase 31/35/36: return a single, offline-verifiable compliance bundle
    (current signed tree head + latest TPM-EK Merkle checkpoint + inclusion proofs
    for the last 100 transactions + system taint status). FAIL-CLOSED: if the
    system is in Safe-Lock (a prior LedgerCompromise / IntegrityViolation), refuse
    to attest with HTTP 403 + ``X-Compliance-Status: CRITICAL_FAILURE``."""
    from services.remote_attestation import get_remote_attestation_provider

    provider = get_remote_attestation_provider()
    locked, reason = provider.compliance_status()
    if locked:
        logger.critical(
            "[SECURITY_CRITICAL][compliance-attest] Safe-Lock -> 403 reason=%s", reason
        )
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Forbidden",
                "code": "compliance_critical_failure",
                "reason": reason,
            },
            headers={"X-Compliance-Status": "CRITICAL_FAILURE"},
        )
    bundle = provider.build_bundle(last_n=100)
    return JSONResponse(
        status_code=200, content=bundle, headers={"X-Compliance-Status": "OK"}
    )
