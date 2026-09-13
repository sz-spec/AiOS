"""User model with bcrypt password hashing."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid


@dataclass
class User:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    email: str = ""
    password_hash: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None

    def set_password(self, plain_password: str) -> None:
        """Hash password with bcrypt."""
        import bcrypt

        self.password_hash = bcrypt.hashpw(
            plain_password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

    def verify_password(self, plain_password: str) -> bool:
        """Verify password against stored bcrypt hash."""
        import bcrypt

        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            self.password_hash.encode("utf-8"),
        )
