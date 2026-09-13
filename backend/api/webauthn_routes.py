"""
v21.7 — WebAuthn step-up scaffold.

HONEST SCOPE STATEMENT — read before adding `app.include_router(...)`:

  This file is a TYPED SCAFFOLD. The endpoints below define the wire
  contract for the eventual step-up flow but DO NOT YET PERFORM
  CRYPTOGRAPHIC VERIFICATION. They return 501 Not Implemented until a
  real WebAuthn library (e.g. duo-labs/py_webauthn) is added to
  requirements.txt and the verifier is wired in.

  Why scaffold-only: shipping a "mostly working" verifier creates the
  worst-of-both-worlds scenario where future code mounts these routes
  as a security gate, trusts them, and silently allows authentication
  bypass. Returning 501 fails-loud the moment an operator tries to use
  this — the failure mode you want.

  Integration plan: see docs/WEBAUTHN_DESIGN_v21.7.md for:
    - Convex schema additions (webauthn_credentials table)
    - Real verifier wiring with py_webauthn
    - Which destructive operations should require step-up
    - Test plan and threat model
"""
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/webauthn", tags=["webauthn"])


# ---------------------------------------------------------------------------
# Pydantic contracts — the eventual verifier reads/writes these shapes.
# ---------------------------------------------------------------------------


class RegistrationOptionsRequest(BaseModel):
    """Client requests registration options for the current user."""
    user_id: str = Field(..., min_length=1, max_length=128)
    user_name: str = Field(..., min_length=1, max_length=128)
    user_display_name: str = Field(..., min_length=1, max_length=128)


class RegistrationOptionsResponse(BaseModel):
    """Server returns the WebAuthn `PublicKeyCredentialCreationOptions`."""
    challenge_b64: str = Field(..., description="base64url-encoded challenge")
    rp_id: str = Field(..., description="Relying party ID, e.g. 'vos-shield.example.com'")
    rp_name: str
    user_id_b64: str
    user_name: str
    user_display_name: str
    pub_key_cred_params: List[Dict[str, Any]]
    timeout_ms: int = 60000


class RegistrationVerificationRequest(BaseModel):
    """Client returns the signed attestation."""
    user_id: str
    credential_id_b64: str
    client_data_json_b64: str
    attestation_object_b64: str


class AuthenticationOptionsRequest(BaseModel):
    """Client requests challenge for an existing user."""
    user_id: str = Field(..., min_length=1, max_length=128)


class AuthenticationOptionsResponse(BaseModel):
    challenge_b64: str
    rp_id: str
    allow_credentials: List[Dict[str, Any]]
    timeout_ms: int = 60000


class AuthenticationVerificationRequest(BaseModel):
    user_id: str
    credential_id_b64: str
    client_data_json_b64: str
    authenticator_data_b64: str
    signature_b64: str


# ---------------------------------------------------------------------------
# Routes — all 501 until the verifier is implemented.
# ---------------------------------------------------------------------------


def _not_yet_implemented():
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "WebAuthn step-up routes are scaffolded but the cryptographic "
            "verifier is not yet wired in. See docs/WEBAUTHN_DESIGN_v21.7.md "
            "for the integration plan. Do NOT mount this router in "
            "production until verification is complete."
        ),
    )


@router.post("/register/begin", response_model=RegistrationOptionsResponse)
async def register_begin(_req: RegistrationOptionsRequest):
    """Begin a registration ceremony. Server stores the challenge in
    the user's session, returns options for the browser to call
    ``navigator.credentials.create()``."""
    _not_yet_implemented()


@router.post("/register/finish", status_code=status.HTTP_204_NO_CONTENT)
async def register_finish(_req: RegistrationVerificationRequest):
    """Verify the attestation and persist the new credential to
    Convex (webauthn_credentials table)."""
    _not_yet_implemented()


@router.post("/authenticate/begin", response_model=AuthenticationOptionsResponse)
async def authenticate_begin(_req: AuthenticationOptionsRequest):
    """Begin an authentication (step-up) ceremony. Returns challenge +
    list of allowed credentials for this user."""
    _not_yet_implemented()


@router.post("/authenticate/finish", status_code=status.HTTP_204_NO_CONTENT)
async def authenticate_finish(_req: AuthenticationVerificationRequest):
    """Verify the assertion. On success, mint a short-lived (5 min)
    'webauthn-fresh' claim attached to the user's session that
    decorated routes can require."""
    _not_yet_implemented()


# ---------------------------------------------------------------------------
# FastAPI dependency for routes that require fresh step-up
# ---------------------------------------------------------------------------


async def require_fresh_webauthn(_session_token: str | None = None):
    """Dependency for destructive routes. Today: 501. Eventually: checks
    the user's session for a 'webauthn-fresh' claim minted in the last
    5 minutes by ``authenticate_finish``."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "require_fresh_webauthn dependency is scaffolded but not yet "
            "implemented. Do NOT use as a security gate yet."
        ),
    )
