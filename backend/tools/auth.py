"""
JWT Authentication Service
===========================
Token-based authentication for AI App Builder API.
Based on community recommendations for secure API access.

Features:
- JWT token generation and validation
- Password hashing with bcrypt
- Refresh tokens
- Role-based access control
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from passlib.context import CryptContext
import jwt
from pydantic import BaseModel, EmailStr

# =============================================================================
# Configuration
# =============================================================================

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    import secrets

    JWT_SECRET = secrets.token_hex(32)
    print("Warning: JWT_SECRET not set, using ephemeral key (dev mode)")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


# =============================================================================
# Password Hashing
# =============================================================================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


# =============================================================================
# Models
# =============================================================================


class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"
    DEVELOPER = "developer"


class UserCreate(BaseModel):
    """User creation model."""

    email: EmailStr
    password: str
    name: Optional[str] = None


class UserLogin(BaseModel):
    """User login model."""

    email: EmailStr
    password: str


class TokenData(BaseModel):
    """Token payload data."""

    user_id: str
    email: str
    role: UserRole = UserRole.USER
    exp: Optional[datetime] = None


class TokenResponse(BaseModel):
    """Token response model."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


@dataclass
class User:
    """User model."""

    id: str
    email: str
    name: Optional[str]
    hashed_password: str
    role: UserRole = UserRole.USER
    is_active: bool = True
    created_at: datetime = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "role": self.role.value,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# =============================================================================
# Token Service
# =============================================================================


class JWTService:
    """
    JWT token service for authentication.

    Usage:
        jwt_service = JWTService()

        # Create tokens
        tokens = jwt_service.create_tokens(user_id="123", email="user@example.com")

        # Verify token
        payload = jwt_service.verify_token(tokens.access_token)
    """

    def __init__(
        self,
        secret: str = JWT_SECRET,
        algorithm: str = JWT_ALGORITHM,
        access_expire_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES,
        refresh_expire_days: int = REFRESH_TOKEN_EXPIRE_DAYS,
    ):
        self.secret = secret
        self.algorithm = algorithm
        self.access_expire_minutes = access_expire_minutes
        self.refresh_expire_days = refresh_expire_days

    def create_access_token(
        self,
        user_id: str,
        email: str,
        role: UserRole = UserRole.USER,
        expires_delta: timedelta = None,
    ) -> str:
        """Create an access token."""
        expire = datetime.now(timezone.utc) + (
            expires_delta or timedelta(minutes=self.access_expire_minutes)
        )

        payload = {
            "sub": user_id,
            "email": email,
            "role": role.value,
            "exp": expire,
            "type": "access",
        }

        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def create_refresh_token(
        self, user_id: str, expires_delta: timedelta = None
    ) -> str:
        """Create a refresh token."""
        expire = datetime.now(timezone.utc) + (
            expires_delta or timedelta(days=self.refresh_expire_days)
        )

        payload = {
            "sub": user_id,
            "exp": expire,
            "type": "refresh",
        }

        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    def create_tokens(
        self, user_id: str, email: str, role: UserRole = UserRole.USER
    ) -> TokenResponse:
        """Create both access and refresh tokens."""
        access_token = self.create_access_token(user_id, email, role)
        refresh_token = self.create_refresh_token(user_id)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self.access_expire_minutes * 60,
        )

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify and decode a token.

        Returns:
            Decoded payload if valid, None otherwise.
        """
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
            return payload
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def verify_access_token(self, token: str) -> Optional[TokenData]:
        """Verify an access token and return TokenData."""
        payload = self.verify_token(token)

        if not payload:
            return None

        if payload.get("type") != "access":
            return None

        return TokenData(
            user_id=payload["sub"],
            email=payload["email"],
            role=UserRole(payload.get("role", "user")),
            exp=datetime.fromtimestamp(payload["exp"]),
        )

    def verify_refresh_token(self, token: str) -> Optional[str]:
        """
        Verify a refresh token.

        Returns:
            User ID if valid, None otherwise.
        """
        payload = self.verify_token(token)

        if not payload:
            return None

        if payload.get("type") != "refresh":
            return None

        return payload["sub"]

    def refresh_access_token(
        self, refresh_token: str, email: str, role: UserRole = UserRole.USER
    ) -> Optional[str]:
        """
        Create a new access token using a refresh token.

        Returns:
            New access token if refresh token is valid, None otherwise.
        """
        user_id = self.verify_refresh_token(refresh_token)

        if not user_id:
            return None

        return self.create_access_token(user_id, email, role)


# =============================================================================
# User Store (In-Memory for dev, replace with DB in production)
# =============================================================================


class UserStore:
    """
    Simple in-memory user store.
    Replace with database in production.
    """

    def __init__(self):
        self.users: Dict[str, User] = {}
        self._email_index: Dict[str, str] = {}  # email -> user_id

    def create_user(
        self,
        email: str,
        password: str,
        name: str = None,
        role: UserRole = UserRole.USER,
    ) -> User:
        """Create a new user."""
        from uuid import uuid4

        if email in self._email_index:
            raise ValueError("Email already registered")

        user_id = str(uuid4())
        user = User(
            id=user_id,
            email=email,
            name=name,
            hashed_password=hash_password(password),
            role=role,
            created_at=datetime.now(timezone.utc),
        )

        self.users[user_id] = user
        self._email_index[email] = user_id

        return user

    def get_user(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        return self.users.get(user_id)

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        user_id = self._email_index.get(email)
        if user_id:
            return self.users.get(user_id)
        return None

    def authenticate(self, email: str, password: str) -> Optional[User]:
        """Authenticate a user."""
        user = self.get_user_by_email(email)

        if not user:
            return None

        if not verify_password(password, user.hashed_password):
            return None

        if not user.is_active:
            return None

        return user

    def update_password(self, user_id: str, new_password: str) -> bool:
        """Update user password."""
        user = self.get_user(user_id)
        if not user:
            return False

        user.hashed_password = hash_password(new_password)
        return True


# =============================================================================
# Auth Service (combines JWT + User Store)
# =============================================================================


class AuthService:
    """
    Complete authentication service.

    Usage:
        auth = AuthService()

        # Register
        user, tokens = auth.register("user@example.com", "password123")

        # Login
        tokens = auth.login("user@example.com", "password123")

        # Verify
        user = auth.get_current_user(tokens.access_token)
    """

    def __init__(self, user_store: UserStore = None, jwt_service: JWTService = None):
        self.users = user_store or UserStore()
        self.jwt = jwt_service or JWTService()

    def register(
        self, email: str, password: str, name: str = None
    ) -> tuple[User, TokenResponse]:
        """Register a new user."""
        user = self.users.create_user(email, password, name)
        tokens = self.jwt.create_tokens(user.id, user.email, user.role)
        return user, tokens

    def login(self, email: str, password: str) -> Optional[TokenResponse]:
        """Login a user."""
        user = self.users.authenticate(email, password)

        if not user:
            return None

        return self.jwt.create_tokens(user.id, user.email, user.role)

    def refresh(self, refresh_token: str) -> Optional[str]:
        """Refresh an access token."""
        user_id = self.jwt.verify_refresh_token(refresh_token)

        if not user_id:
            return None

        user = self.users.get_user(user_id)
        if not user:
            return None

        return self.jwt.create_access_token(user.id, user.email, user.role)

    def get_current_user(self, access_token: str) -> Optional[User]:
        """Get the current user from an access token."""
        token_data = self.jwt.verify_access_token(access_token)

        if not token_data:
            return None

        return self.users.get_user(token_data.user_id)

    def verify_token(self, token: str) -> Optional[TokenData]:
        """Verify an access token."""
        return self.jwt.verify_access_token(token)


# =============================================================================
# FastAPI Dependency
# =============================================================================

# Singleton instance
_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    """Get the auth service singleton."""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service


async def get_current_user_dependency(authorization: str = None) -> Optional[User]:
    """
    FastAPI dependency to get current user.

    Usage:
        @app.get("/me")
        async def get_me(user: User = Depends(get_current_user_dependency)):
            return user.to_dict()
    """
    if not authorization:
        return None

    # Remove "Bearer " prefix
    token = authorization.replace("Bearer ", "")

    auth = get_auth_service()
    return auth.get_current_user(token)


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":
    # Initialize
    auth = AuthService()

    # Register
    user, tokens = auth.register("test@example.com", "password123", "Test User")
    print(f"Registered: {user.email}")
    print(f"Access Token: {tokens.access_token[:50]}...")

    # Login
    tokens2 = auth.login("test@example.com", "password123")
    print(f"Logged in: {tokens2 is not None}")

    # Verify
    current_user = auth.get_current_user(tokens.access_token)
    print(f"Current user: {current_user.email}")

    # Refresh
    new_token = auth.refresh(tokens.refresh_token)
    print(f"Refreshed: {new_token is not None}")
