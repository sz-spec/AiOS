"""
VOS3 Sovereign API Gateway — FastAPI Routes
=============================================

REST endpoints for the Sovereign API Gateway. Provides authenticated access
to kernel inference slots, token-quota management, and HMAC provenance
verification.

All routes require Bearer token authentication via Clerk JWT.

Routes:
    POST /api/gateway/v1/inference  — Dispatch inference to kernel slot
    GET  /api/gateway/v1/quota      — Check user's token quota status
    GET  /api/gateway/v1/slots      — List all 8 kernel inference slots
    POST /api/gateway/v1/verify     — Verify HMAC provenance header

Phase: Sovereign Genesis (April 2026)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from api.deps import get_current_user, AuthenticatedUser
from core.errors import VOS3Error, ErrorCode

logger = logging.getLogger("gateway_routes")

router = APIRouter(prefix="/api/gateway/v1", tags=["gateway"])


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------


class InferenceRequest(BaseModel):
    """Request body for the inference endpoint."""

    slot_id: int = Field(..., ge=0, le=7, description="Kernel model slot (0-7)")
    prompt: str = Field(
        ..., min_length=1, max_length=100_000, description="Input prompt text"
    )
    max_tokens: int = Field(
        256, ge=1, le=4096, description="Maximum tokens to generate"
    )
    temperature: float = Field(1.0, ge=0.0, le=2.0, description="Sampling temperature")


class ProvenanceModel(BaseModel):
    """Provenance header attached to inference responses."""

    hmac_hex: str
    node_id: str
    slot_id: int
    timestamp: float
    chain_hash: str


class InferenceResponse(BaseModel):
    """Response body from the inference endpoint."""

    text: str
    tokens_generated: int
    latency_us: int
    provenance: ProvenanceModel


class QuotaResponse(BaseModel):
    """Response body from the quota endpoint."""

    allowed: bool
    remaining_tokens: int
    tier: str
    reset_at: float
    daily_limit: int


class SlotInfo(BaseModel):
    """Status information for a single kernel inference slot."""

    slot_id: int
    status: str
    raw: Optional[str] = None
    error: Optional[str] = None


class SlotsResponse(BaseModel):
    """Response body from the slots endpoint."""

    slots: list[SlotInfo]
    total: int


class VerifyRequest(BaseModel):
    """Request body for provenance verification."""

    response_body: str = Field(
        ..., min_length=1, description="Original response text to verify"
    )
    provenance_header: ProvenanceModel = Field(
        ..., description="Provenance header to verify"
    )


class VerifyResponse(BaseModel):
    """Response body from the verify endpoint."""

    valid: bool
    details: dict


# ---------------------------------------------------------------------------
# Service Access
# ---------------------------------------------------------------------------


def _get_gateway():
    """Retrieve the ApiGatewayService singleton.

    Raises HTTPException(503) if the gateway has not been initialized yet
    (i.e., no VBus connection established).
    """
    try:
        from services.api_gateway import get_api_gateway

        return get_api_gateway()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"API Gateway not available: {exc}",
        )


def _resolve_tier(
    user: AuthenticatedUser,
) -> "QuotaTier":  # noqa: F821 - QuotaTier imported lazily inside function
    """Resolve the user's subscription tier from metadata or default to FREE.

    Checks user.metadata for a 'tier' key set by the billing system. Falls
    back to FREE if not present.
    """
    from services.api_gateway import QuotaTier

    tier_str = (user.metadata or {}).get("tier", "free")
    try:
        return QuotaTier(tier_str)
    except ValueError:
        return QuotaTier.FREE


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/inference", response_model=InferenceResponse)
async def inference(
    req: InferenceRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Dispatch an inference request to a kernel model slot.

    Pipeline:
    1. Resolves user tier from auth metadata
    2. Checks token quota (daily cap + rate limit + concurrency)
    3. Dispatches KIM_GENERATE to the kernel via VBus
    4. Collects generated tokens from the stream
    5. Signs the response with HMAC-SHA256 provenance
    6. Records usage against the user's quota

    The response includes an X-VOS3-Provenance header (via the provenance
    field in the JSON body) containing the HMAC, node ID, slot ID, timestamp,
    and chain hash for tamper detection.

    Raises:
        429: Quota exceeded (daily tokens, rate limit, or concurrency)
        502: Kernel communication failure
        422: Invalid parameters
    """
    from services.api_gateway import TIER_LIMITS

    gateway = _get_gateway()
    tier = _resolve_tier(user)

    # Pre-flight quota check
    allowed, status = gateway.check_quota(user.id, req.max_tokens, tier)
    if not allowed:
        raise VOS3Error(
            ErrorCode.QUOTA_EXCEEDED,
            f"Token quota exceeded: {status.remaining_tokens} remaining "
            f"(tier={tier.value}, resets at {status.reset_at:.0f})",
            details={
                "remaining_tokens": status.remaining_tokens,
                "tier": tier.value,
                "reset_at": status.reset_at,
                "daily_limit": TIER_LIMITS[tier]["daily_tokens"],
            },
        )

    try:
        result = await gateway.dispatch_inference(
            slot_id=req.slot_id,
            prompt=req.prompt,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            user_id=user.id,
            tier=tier,
        )
    except ValueError as exc:
        raise VOS3Error(
            ErrorCode.VALIDATION_ERROR,
            str(exc),
        )
    except RuntimeError as exc:
        error_msg = str(exc)
        if "Quota exceeded" in error_msg:
            raise VOS3Error(
                ErrorCode.QUOTA_EXCEEDED,
                error_msg,
            )
        raise VOS3Error(
            ErrorCode.KERNEL_ERROR,
            f"Kernel inference failed: {error_msg}",
            details={"slot_id": req.slot_id},
        )
    except Exception as exc:
        logger.exception("Unexpected error in inference dispatch")
        raise VOS3Error(
            ErrorCode.INTERNAL_ERROR,
            f"Internal gateway error: {type(exc).__name__}",
        )

    # Build provenance model
    prov = None
    if result.provenance:
        prov = ProvenanceModel(
            hmac_hex=result.provenance.hmac_hex,
            node_id=result.provenance.node_id,
            slot_id=result.provenance.slot_id,
            timestamp=result.provenance.timestamp,
            chain_hash=result.provenance.chain_hash,
        )

    return InferenceResponse(
        text=result.text,
        tokens_generated=result.tokens,
        latency_us=result.latency_us,
        provenance=prov,
    )


@router.get("/quota", response_model=QuotaResponse)
async def get_quota(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Check the authenticated user's current token quota status.

    Returns remaining tokens, tier, daily limit, and the Unix timestamp
    when the daily quota window resets (midnight UTC).
    """
    from services.api_gateway import TIER_LIMITS

    gateway = _get_gateway()
    tier = _resolve_tier(user)
    status = gateway.quota_manager.get_status(user.id, tier)
    limits = TIER_LIMITS[tier]

    return QuotaResponse(
        allowed=status.allowed,
        remaining_tokens=status.remaining_tokens,
        tier=status.tier.value,
        reset_at=status.reset_at,
        daily_limit=limits["daily_tokens"],
    )


@router.get("/slots", response_model=SlotsResponse)
async def list_slots(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List the status of all 8 kernel inference slots.

    Queries each slot via SLOT_STATUS|{i} VBus command. Slots that are
    not loaded or unreachable will have status "error" with details.
    """
    gateway = _get_gateway()
    slots = await gateway.list_slots()

    slot_infos = [
        SlotInfo(
            slot_id=s.get("slot_id", i),
            status=s.get("status", "unknown"),
            raw=s.get("raw"),
            error=s.get("error"),
        )
        for i, s in enumerate(slots)
    ]

    return SlotsResponse(
        slots=slot_infos,
        total=len(slot_infos),
    )


@router.post("/verify", response_model=VerifyResponse)
async def verify_provenance(
    req: VerifyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Verify an HMAC provenance header against a response body.

    Checks:
    1. Chain linkage: sha256(hmac_hex) == chain_hash
    2. HMAC format validity
    3. Timestamp plausibility (not in the future, not older than 24h)

    Note: Full HMAC verification requires the signing key, which is held
    server-side. This endpoint performs the verification using the gateway's
    key. External consumers who do not have the key can only verify chain
    linkage (step 1).

    Returns:
        {valid: bool, details: {chain_valid, timestamp_valid, ...}}
    """
    import hashlib
    import time as time_mod

    gateway = _get_gateway()
    header_data = req.provenance_header

    # Build a ProvenanceHeader dataclass for the signer
    from services.api_gateway import ProvenanceHeader as PH

    header = PH(
        hmac_hex=header_data.hmac_hex,
        node_id=header_data.node_id,
        slot_id=header_data.slot_id,
        timestamp=header_data.timestamp,
        chain_hash=header_data.chain_hash,
    )

    # Chain linkage check: sha256(hmac_hex) should equal chain_hash
    expected_chain = hashlib.sha256(header.hmac_hex.encode("utf-8")).hexdigest()
    chain_valid = expected_chain == header.chain_hash

    # Timestamp plausibility
    now = time_mod.time()
    timestamp_valid = (
        header.timestamp <= now + 60  # Allow 60s clock skew
        and header.timestamp >= now - 86400  # Not older than 24h
    )

    # HMAC format check (should be 64 hex chars = SHA-256)
    hmac_format_valid = len(header.hmac_hex) == 64 and all(
        c in "0123456789abcdef" for c in header.hmac_hex
    )

    # Full HMAC verification (server-side only, requires signing key)
    signer_valid = gateway.verify_provenance(req.response_body, header)

    overall_valid = (
        chain_valid and timestamp_valid and hmac_format_valid and signer_valid
    )

    return VerifyResponse(
        valid=overall_valid,
        details={
            "chain_valid": chain_valid,
            "timestamp_valid": timestamp_valid,
            "hmac_format_valid": hmac_format_valid,
            "signer_valid": signer_valid,
            "node_id": header.node_id,
            "slot_id": header.slot_id,
            "timestamp": header.timestamp,
        },
    )


# ---------------------------------------------------------------------------
# WebSocket Token Streaming
# ---------------------------------------------------------------------------


@router.websocket("/ws/inference")
async def ws_inference(websocket: WebSocket):
    """Zero-copy WebSocket token streaming endpoint.

    Accepts a WebSocket connection and streams inference tokens in real time
    from the KIM (Kernel Inference Module) pipeline. This bypasses the HTTP
    request/response cycle for sub-10ms token-to-client latency.

    Protocol:
        1. Client connects via WebSocket
        2. Client sends JSON: {slot_id, prompt, max_tokens, temperature, token}
        3. Server validates Bearer token (Clerk JWKS or dev-mode fallback)
        4. Server streams token events as JSON frames:
           - {"type": "token", "slot_id": int, "token_id": int, "text": str, "seq": int, "flags": int}
           - {"type": "done", "total_tokens": int, "latency_us": int} (final, with HMAC provenance)
           - {"type": "error", "message": str} (on failure)
        5. Connection closes after "done" or on error

    The token field in the initial message carries the Bearer JWT for auth.
    In dev mode (no Clerk keys configured), any non-empty token is accepted.
    """
    await websocket.accept()

    try:
        # Receive the initial request message
        raw = await websocket.receive_json()

        # Extract and validate required fields
        slot_id = raw.get("slot_id")
        prompt = raw.get("prompt")
        max_tokens = raw.get("max_tokens", 256)
        temperature = raw.get("temperature", 1.0)
        bearer_token = raw.get("token", "")

        if slot_id is None or prompt is None:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Missing required fields: slot_id, prompt",
                }
            )
            await websocket.close(code=1008)
            return

        # Validate Bearer token
        user_id: Optional[str] = None
        try:
            user_id = await _validate_ws_token(bearer_token)
        except Exception as exc:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Authentication failed: {exc}",
                }
            )
            await websocket.close(code=1008)
            return

        # Resolve tier for quota
        tier = _resolve_ws_tier(user_id)

        # Get gateway service
        try:
            gateway = _get_gateway()
        except HTTPException:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "API Gateway not available",
                }
            )
            await websocket.close(code=1013)
            return

        # Stream tokens via the gateway's async generator
        try:
            async for event in gateway.stream_inference(
                slot_id=slot_id,
                max_tokens=max_tokens,
                temperature=temperature,
                user_id=user_id,
                tier=tier,
            ):
                await websocket.send_json(event)
        except ValueError as exc:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Validation error: {exc}",
                }
            )
        except RuntimeError as exc:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Inference error: {exc}",
                }
            )
        except Exception as exc:
            logger.exception("Unexpected error in ws_inference stream")
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Internal error: {type(exc).__name__}",
                }
            )

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected during inference stream")
    except Exception as exc:
        logger.exception("Unhandled error in ws_inference")
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Server error: {type(exc).__name__}",
                }
            )
        except Exception:
            pass  # Client already gone
    finally:
        try:
            await websocket.close()
        except Exception:
            pass  # Already closed


async def _validate_ws_token(bearer_token: str) -> Optional[str]:
    """Validate a Bearer JWT from the WebSocket initial message.

    Attempts Clerk JWKS verification first. In dev mode (no Clerk keys
    configured), accepts any non-empty token and returns a dev user ID.

    Args:
        bearer_token: The raw JWT string from the client.

    Returns:
        The authenticated user_id (Clerk sub claim), or a dev-mode placeholder.

    Raises:
        ValueError: If the token is empty or invalid and dev mode is off.
    """
    from api.deps import DEV_MODE

    if not bearer_token:
        raise ValueError("No token provided")

    # Strip "Bearer " prefix if present
    if bearer_token.startswith("Bearer "):
        bearer_token = bearer_token[7:]

    if not bearer_token:
        raise ValueError("Empty token after prefix strip")

    # Try Clerk JWKS verification (B-CRIT-1 fix: use _verify_token)
    if not DEV_MODE:
        try:
            from middleware.auth import _verify_token

            user = await _verify_token(bearer_token)
            return user.id
        except Exception as exc:
            raise ValueError(f"JWT verification failed: {exc}") from exc

    # Dev mode: accept any non-empty token
    logger.debug("Dev mode: accepting WebSocket token without verification")
    return "dev_user_ws"


def _resolve_ws_tier(
    user_id: Optional[str],
) -> "QuotaTier":  # noqa: F821 - QuotaTier imported lazily inside function
    """Resolve quota tier for a WebSocket user.

    In production, this would look up the user's subscription tier from
    Clerk metadata or a billing service. For now, defaults to FREE for
    unknown users and PRO for identified users.

    Args:
        user_id: The authenticated user ID, or None.

    Returns:
        The resolved QuotaTier.
    """
    from services.api_gateway import QuotaTier

    if user_id is None or user_id.startswith("dev_"):
        return QuotaTier.FREE
    return QuotaTier.FREE  # Default; billing integration upgrades this
