"""
Project Service
===============
Manages project CRUD operations with Convex persistent storage.
Maintains backward-compatible API with the Project dataclass.

Extra fields not in the Convex projects schema (category, template_id,
status, files, settings) are stored in the metadata JSON field.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class Project:
    id: str = ""
    user_id: str = "anonymous"
    name: str = ""
    description: str = ""
    category: str = ""
    template_id: Optional[str] = None
    status: str = "draft"  # draft, building, ready, deployed, error
    files: Dict[str, str] = field(default_factory=dict)
    settings: Dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict:
        return asdict(self)


def _get_repo():
    """Lazy-init Convex project repository."""
    from core.repositories.convex import ConvexProjectRepository

    return ConvexProjectRepository()


def _doc_to_project(doc: dict) -> Project:
    """Convert a Convex document to a Project dataclass."""
    meta = doc.get("metadata") or {}
    return Project(
        id=doc.get("_id", ""),
        user_id=doc.get("ownerId", "anonymous"),
        name=doc.get("name", ""),
        description=doc.get("description", ""),
        category=meta.get("category", ""),
        template_id=meta.get("template_id"),
        status=meta.get("status", "draft"),
        files=meta.get("files", {}),
        settings=meta.get("settings", {}),
        created_at=meta.get("created_at", doc.get("_creationTime", "")),
        updated_at=meta.get("updated_at", doc.get("updatedAt", "")),
    )


class ProjectService:
    """Project storage backed by Convex. Falls back gracefully on error."""

    def __init__(self):
        # Local cache: project_id -> Project (lazily populated from Convex)
        self._cache: Dict[str, Project] = {}
        # Track explicitly deleted IDs so get() doesn't re-fetch from Convex
        self._deleted: set = set()

    def create(
        self,
        user_id: str,
        name: str,
        description: str,
        category: str,
        template_id: Optional[str] = None,
    ) -> Project:
        project = Project(
            user_id=user_id,
            name=name,
            description=description,
            category=category,
            template_id=template_id,
            status="building",
        )
        try:
            result = _get_repo().create(
                user_id=user_id,
                name=name,
                description=description,
                category=category,
                template_id=template_id,
                status="building",
            )
            if result and result.get("_id"):
                project.id = result["_id"]
        except Exception as e:
            logger.warning("Convex project create failed: %s", e)
        self._cache[project.id] = project
        return project

    def get(self, project_id: str) -> Optional[Project]:
        if project_id in self._deleted:
            return None
        if project_id in self._cache:
            return self._cache[project_id]
        try:
            doc = _get_repo().get_by_id(project_id)
            if doc:
                project = _doc_to_project(doc)
                self._cache[project.id] = project
                return project
        except Exception as e:
            logger.warning("Convex project get failed for %s: %s", project_id, e)
        return None

    def list_by_user(self, user_id: str) -> List[Project]:
        try:
            docs = _get_repo().list_by_user(user_id)
            if docs:
                projects = [_doc_to_project(d) for d in docs]
                for p in projects:
                    self._cache[p.id] = p
                return projects
        except Exception as e:
            logger.warning("Convex project list_by_user failed: %s", e)
        # Fallback to cache
        return [p for p in self._cache.values() if p.user_id == user_id]

    def list_all(self) -> List[Project]:
        try:
            docs = _get_repo().list_all()
            if docs:
                projects = [_doc_to_project(d) for d in docs]
                for p in projects:
                    self._cache[p.id] = p
                return projects
        except Exception as e:
            logger.warning("Convex project list_all failed: %s", e)
        return list(self._cache.values())

    def update(self, project_id: str, **kwargs) -> Optional[Project]:
        project = self.get(project_id)
        if not project:
            return None
        for key, value in kwargs.items():
            if key.startswith("__"):
                continue
            if hasattr(project, key):
                setattr(project, key, value)
        project.updated_at = datetime.now(timezone.utc).isoformat()
        # Write-through to Convex
        try:
            meta = {
                "category": project.category,
                "template_id": project.template_id,
                "status": project.status,
                "files": project.files,
                "settings": project.settings,
                "updated_at": project.updated_at,
            }
            _get_repo().update(
                project_id,
                {
                    "name": project.name,
                    "description": project.description,
                    "metadata": meta,
                },
            )
        except Exception as e:
            logger.warning("Convex project update failed for %s: %s", project_id, e)
        self._cache[project_id] = project
        return project

    def delete(self, project_id: str) -> bool:
        # Check if project exists before attempting delete
        if project_id not in self._cache and self.get(project_id) is None:
            return False
        self._cache.pop(project_id, None)
        self._deleted.add(project_id)
        try:
            _get_repo().delete(project_id)
        except Exception as e:
            logger.warning("Convex project delete failed for %s: %s", project_id, e)
        return True

    def update_files(self, project_id: str, files: Dict[str, str]) -> Optional[Project]:
        project = self.get(project_id)
        if not project:
            return None
        project.files.update(files)
        project.updated_at = datetime.now(timezone.utc).isoformat()
        # Write-through
        try:
            meta = {
                "category": project.category,
                "template_id": project.template_id,
                "status": project.status,
                "files": project.files,
                "settings": project.settings,
                "updated_at": project.updated_at,
            }
            _get_repo().update(project_id, {"metadata": meta})
        except Exception as e:
            logger.warning("Convex project update_files failed: %s", e)
        self._cache[project_id] = project
        return project


# Singleton
_service: Optional[ProjectService] = None


def get_project_service() -> ProjectService:
    global _service
    if _service is None:
        _service = ProjectService()
    return _service
