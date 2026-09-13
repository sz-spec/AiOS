"""Chat service — Convex-backed session persistence.

Sessions are stored in Convex chatSessions + chatSessionMessages tables.
A local cache avoids redundant Convex queries within the same process lifetime.
On restart, sessions are recovered from Convex (messages intact).
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _get_repo():
    """Lazy-init Convex chat session repository."""
    from core.repositories.convex import ConvexChatSessionRepository

    return ConvexChatSessionRepository()


class ChatService:
    """Manages chat sessions with Convex persistence."""

    def __init__(self):
        # In-memory cache for fast reads; lazily populated from Convex
        self._cache: dict = {}

    def get_session(self, session_id: str) -> Optional[dict]:
        """Get a session by ID. Checks cache first, then Convex."""
        if session_id in self._cache:
            return self._cache[session_id]
        try:
            result = _get_repo().load_session(session_id)
            if result:
                session = {
                    "id": result.get("sessionId", session_id),
                    "user_id": result.get("userId", ""),
                    "model": os.getenv("VOS3_DEFAULT_CHAT_MODEL", "claude-sonnet"),
                    "messages": [
                        {"role": m["role"], "content": m["content"]}
                        for m in result.get("messages", [])
                    ],
                }
                self._cache[session_id] = session
                return session
        except Exception as e:
            logger.warning("Convex load_session failed for %s: %s", session_id, e)
        return None

    def create_session(self, session_id: str, user_id: str, model: str = None) -> dict:
        """Create a new chat session, persisted to Convex."""
        if model is None:
            model = os.getenv("VOS3_DEFAULT_CHAT_MODEL", "claude-sonnet")
        session = {
            "id": session_id,
            "user_id": user_id,
            "model": model,
            "messages": [],
        }
        try:
            _get_repo().save_session(user_id, session_id, messages=[])
        except Exception as e:
            logger.warning("Convex save_session failed: %s", e)
        self._cache[session_id] = session
        return session

    def list_sessions(self, user_id: str) -> list:
        """List all sessions for a user from Convex."""
        try:
            results = _get_repo().list_sessions(user_id)
            if results:
                sessions = []
                for r in results:
                    sid = r.get("sessionId", "")
                    session = {
                        "id": sid,
                        "user_id": r.get("userId", user_id),
                        "model": os.getenv("VOS3_DEFAULT_CHAT_MODEL", "claude-sonnet"),
                        "messages": [],
                    }
                    self._cache[sid] = session
                    sessions.append(session)
                return sessions
        except Exception as e:
            logger.warning("Convex list_sessions failed: %s", e)
        # Fallback to local cache
        return [s for s in self._cache.values() if s["user_id"] == user_id]


_service: Optional[ChatService] = None


def get_chat_service() -> ChatService:
    global _service
    if _service is None:
        _service = ChatService()
    return _service
