"""
Checkpoint Service
==================
Manages version history (checkpoints) for projects.
Each checkpoint stores a snapshot of all project files.
Persists to Convex when available, falls back to in-memory.
"""

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class Checkpoint:
    id: str = ""
    project_id: str = ""
    files_snapshot: Dict[str, str] = field(default_factory=dict)
    description: str = ""
    created_at: str = ""
    created_by: Optional[str] = None

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


def _checkpoint_repo():
    """Lazy accessor for the checkpoint repository.

    Returns None on import/init failure so the service can fall back to
    its in-memory store without crashing the caller.
    """
    try:
        from core.repositories import get_checkpoint_repository

        return get_checkpoint_repository()
    except Exception:
        return None


class CheckpointService:
    """Checkpoint storage with Convex persistence and in-memory fallback."""

    def __init__(self):
        self._checkpoints: Dict[str, List[Checkpoint]] = {}

    def create(
        self,
        project_id: str,
        files: Dict[str, str],
        description: str = "",
        created_by: Optional[str] = None,
    ) -> Checkpoint:
        cp = Checkpoint(
            project_id=project_id,
            files_snapshot=dict(files),
            description=description,
            created_by=created_by,
        )
        # Persist to Convex via the repository layer.
        repo = _checkpoint_repo()
        if repo is not None:
            try:
                repo.create(
                    project_id=project_id,
                    description=description,
                    files_snapshot=dict(files),
                    created_by=created_by,
                )
            except Exception:
                pass
        # Also keep in-memory for fast access
        if project_id not in self._checkpoints:
            self._checkpoints[project_id] = []
        self._checkpoints[project_id].append(cp)
        return cp

    def list_by_project(self, project_id: str) -> List[Checkpoint]:
        # Try Convex first
        repo = _checkpoint_repo()
        if repo is not None:
            try:
                results = repo.list_by_project(project_id=project_id)
                if results:
                    return [
                        Checkpoint(
                            id=str(r.get("_id", "")),
                            project_id=project_id,
                            files_snapshot=r.get("filesSnapshot", {}),
                            description=r.get("description", ""),
                            created_at=str(r.get("_creationTime", "")),
                            created_by=r.get("createdBy"),
                        )
                        for r in results
                    ]
            except Exception:
                pass
        return self._checkpoints.get(project_id, [])

    def get(self, project_id: str, checkpoint_id: str) -> Optional[Checkpoint]:
        for cp in self.list_by_project(project_id):
            if cp.id == checkpoint_id:
                return cp
        return None

    def restore(self, project_id: str, checkpoint_id: str) -> Optional[Dict[str, str]]:
        """Get the files snapshot from a checkpoint for restoring."""
        cp = self.get(project_id, checkpoint_id)
        if cp:
            return dict(cp.files_snapshot)
        return None


_service: Optional[CheckpointService] = None


def get_checkpoint_service() -> CheckpointService:
    global _service
    if _service is None:
        _service = CheckpointService()
    return _service
