"""JWT token creation and rotation utilities."""

import os
from datetime import datetime, timedelta, timezone

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_REFRESH_SECRET = os.environ.get("JWT_REFRESH_SECRET", "dev-refresh-secret")
JWT_EXPIRY_MINUTES = int(os.environ.get("JWT_EXPIRY_MINUTES", "15"))
REFRESH_EXPIRY_DAYS = 7


def create_access_token(user_id: str, email: str) -> str:
    """Create a short-lived JWT access token."""
    import jwt

    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRY_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def create_refresh_token(user_id: str) -> str:
    """Create a long-lived JWT refresh token."""
    import jwt

    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_EXPIRY_DAYS),
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
    }
    return jwt.encode(payload, JWT_REFRESH_SECRET, algorithm="HS256")


def verify_access_token(token: str) -> dict:
    """Verify and decode an access token. Raises on failure."""
    import jwt

    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])


def verify_refresh_token(token: str) -> dict:
    """Verify and decode a refresh token. Raises on failure."""
    import jwt

    payload = jwt.decode(token, JWT_REFRESH_SECRET, algorithms=["HS256"])
    if payload.get("type") != "refresh":
        raise ValueError("Not a refresh token")
    return payload
