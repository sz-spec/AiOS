"""Auth routes for custom JWT apps — login, register, refresh, logout."""

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/auth", tags=["auth"])

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_REFRESH_SECRET = os.environ.get("JWT_REFRESH_SECRET", "dev-refresh-secret")
JWT_EXPIRY_MINUTES = int(os.environ.get("JWT_EXPIRY_MINUTES", "15"))


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def _create_tokens(user_id: str, email: str) -> TokenResponse:
    import jwt

    now = datetime.now(timezone.utc)
    access_payload = {
        "sub": user_id,
        "email": email,
        "exp": now + timedelta(minutes=JWT_EXPIRY_MINUTES),
        "iat": now,
    }
    refresh_payload = {
        "sub": user_id,
        "exp": now + timedelta(days=7),
        "iat": now,
        "type": "refresh",
    }
    return TokenResponse(
        access_token=jwt.encode(access_payload, JWT_SECRET, algorithm="HS256"),
        refresh_token=jwt.encode(
            refresh_payload, JWT_REFRESH_SECRET, algorithm="HS256"
        ),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    """Authenticate with email/password and return JWT tokens."""
    # Replace with real user lookup and password verification
    import bcrypt  # noqa: F401 — available via template dependencies

    # Stub: accept any login in dev mode
    # In production, query your user database and verify bcrypt hash
    if not body.email or not body.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return _create_tokens(user_id=body.email, email=body.email)


@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest):
    """Register a new user with email/password."""
    if len(body.password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters"
        )

    # Replace with real user creation
    # In production: check for duplicate email, hash password with bcrypt
    return _create_tokens(user_id=body.email, email=body.email)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    """Refresh an expired access token using a valid refresh token."""
    try:
        import jwt

        payload = jwt.decode(
            body.refresh_token, JWT_REFRESH_SECRET, algorithms=["HS256"]
        )
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        return _create_tokens(user_id=payload["sub"], email=payload.get("email", ""))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


@router.post("/logout")
async def logout():
    """Client-side logout — server acknowledges."""
    return {"message": "Logged out"}
