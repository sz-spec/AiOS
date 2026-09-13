"""
User Model - User data access layer.
Provides CRUD operations for user records.
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

# In-memory store (replace with database in production)
_users: Dict[str, dict] = {}


async def get_user_by_email(email: str) -> Optional[dict]:
    """Find a user by email address."""
    for user in _users.values():
        if user["email"] == email:
            return user
    return None


async def get_user_by_id(user_id: str) -> Optional[dict]:
    """Find a user by ID."""
    return _users.get(user_id)


async def create_user(email: str, password_hash: str) -> dict:
    """Create a new user record."""
    user_id = str(uuid.uuid4())
    user = {
        "id": user_id,
        "email": email,
        "password_hash": password_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _users[user_id] = user
    return user
