"""
Collaboration Service
=====================
Manages real-time collaboration sessions, invitations, and presence.
Persists to Convex when available, falls back to in-memory.
"""

from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid


@dataclass
class Collaborator:
    id: str = ""
    project_id: str = ""
    user_email: str = ""
    role: str = "editor"  # admin, editor, viewer
    invited_at: str = ""
    accepted: bool = False

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.invited_at:
            self.invited_at = datetime.now(timezone.utc).isoformat()


def _collaborator_repo():
    """Lazy accessor for the collaborator repository.

    Returns None on import/init failure so callers degrade to the
    in-memory store rather than crash.
    """
    try:
        from core.repositories import get_collaborator_repository

        return get_collaborator_repository()
    except Exception:
        return None


class CollabService:
    """Collaboration management with Convex persistence."""

    def __init__(self):
        self._collaborators: Dict[str, List[Collaborator]] = {}
        self._presence: Dict[str, Dict[str, bool]] = {}

    def invite(self, project_id: str, email: str, role: str = "editor") -> Collaborator:
        collab = Collaborator(project_id=project_id, user_email=email, role=role)
        repo = _collaborator_repo()
        if repo is not None:
            try:
                repo.invite(
                    project_id=project_id,
                    user_email=email,
                    role=role,
                    accepted=False,
                    invited_at_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
                )
            except Exception:
                pass
        if project_id not in self._collaborators:
            self._collaborators[project_id] = []
        self._collaborators[project_id].append(collab)
        return collab

    def list_collaborators(self, project_id: str) -> List[Collaborator]:
        repo = _collaborator_repo()
        if repo is not None:
            try:
                results = repo.list_by_project(project_id=project_id)
                if results:
                    return [
                        Collaborator(
                            id=str(r.get("_id", "")),
                            project_id=project_id,
                            user_email=r.get("userEmail", ""),
                            role=r.get("role", "editor"),
                            accepted=r.get("accepted", False),
                            invited_at=str(r.get("invitedAt", "")),
                        )
                        for r in results
                    ]
            except Exception:
                pass
        return self._collaborators.get(project_id, [])

    def remove(self, project_id: str, collaborator_id: str) -> bool:
        collabs = self._collaborators.get(project_id, [])
        before = len(collabs)
        self._collaborators[project_id] = [
            c for c in collabs if c.id != collaborator_id
        ]
        return len(self._collaborators[project_id]) < before

    def set_presence(self, project_id: str, user_id: str, online: bool):
        if project_id not in self._presence:
            self._presence[project_id] = {}
        self._presence[project_id][user_id] = online

    def get_presence(self, project_id: str) -> Dict[str, bool]:
        return self._presence.get(project_id, {})


_service: Optional[CollabService] = None


def get_collab_service() -> CollabService:
    global _service
    if _service is None:
        _service = CollabService()
    return _service
