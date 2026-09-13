"""
GitHub Sync API Routes

Based on forum research (Dec 2025):
- Export to GitHub with auto-versioning
- File freeze/lock for stability
- Branch management (main/dev/feature)
- AI auto-commit integration
"""

import logging
from datetime import datetime, timezone
from typing import Optional
import os

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Import from enhanced sync service
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.deps import get_current_user, AuthenticatedUser

from tools.github_sync_enhanced import (
    GitHubSyncService,
)

router = APIRouter(prefix="/github", tags=["github-sync"])


# ============================================
# Dependencies
# ============================================


def get_sync_service() -> GitHubSyncService:
    """Get GitHub sync service instance."""
    return GitHubSyncService(
        github_token=os.getenv("GITHUB_TOKEN"),
    )


def _github_token(user: AuthenticatedUser) -> Optional[str]:
    """Extract GitHub token from user metadata or environment."""
    if user.metadata and user.metadata.get("github_token"):
        return user.metadata["github_token"]
    return os.getenv("GITHUB_TOKEN")


# ============================================
# Request/Response Models
# ============================================


class ExportToGitHubRequest(BaseModel):
    repo_name: str = Field(..., min_length=1, max_length=100)
    branch: str = Field(default="main")
    files: dict[str, str]  # path -> content
    commit_message: Optional[str] = None
    version_prefix: Optional[str] = None
    create_repo: bool = True


class SyncBranchRequest(BaseModel):
    repo_name: str
    source_branch: str = Field(default="dev")
    target_branch: str = Field(default="main")
    files: dict[str, str]


class CreateVersionRequest(BaseModel):
    repo_name: str
    version: str = Field(..., pattern=r"^v?\d+(\.\d+)*$")
    base_branch: str = Field(default="main")


class FreezeFileRequest(BaseModel):
    path: str
    reason: Optional[str] = None
    lock_ai: bool = False


class CreatePRRequest(BaseModel):
    repo_name: str
    title: str
    head: str
    base: str = Field(default="main")
    body: Optional[str] = None


class SyncResultResponse(BaseModel):
    status: str
    commit_sha: Optional[str]
    branch: Optional[str]
    url: Optional[str]
    files_synced: list[str]
    conflicts: list[str]
    message: str


class FileLockResponse(BaseModel):
    path: str
    status: str
    locked_by: str
    locked_at: str
    reason: Optional[str]


# ============================================
# Export Endpoints
# ============================================


@router.post("/projects/{project_id}/export")
async def export_to_github(
    project_id: str,
    request: ExportToGitHubRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GitHubSyncService = Depends(get_sync_service),
) -> SyncResultResponse:
    """
    Export project to GitHub.

    Forum recommendation: Use branches for versions
    - main: production/stable
    - dev: testing/development
    """
    result = await service.export_to_github(
        project_id=project_id,
        files=request.files,
        repo_name=request.repo_name,
        branch=request.branch,
        commit_message=request.commit_message,
        token=_github_token(user),
        version_prefix=request.version_prefix,
    )

    return SyncResultResponse(
        status=result.status.value,
        commit_sha=result.commit_sha,
        branch=result.branch,
        url=result.url,
        files_synced=result.files_synced,
        conflicts=result.conflicts,
        message=result.message,
    )


@router.post("/projects/{project_id}/sync-branch")
async def sync_to_branch(
    project_id: str,
    request: SyncBranchRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GitHubSyncService = Depends(get_sync_service),
) -> SyncResultResponse:
    """
    Sync files to a branch for testing.

    Forum workflow:
    1. Export to dev branch
    2. Test
    3. Create PR to merge to main
    """
    result = await service.sync_branch(
        project_id=project_id,
        files=request.files,
        repo_name=request.repo_name,
        source_branch=request.source_branch,
        target_branch=request.target_branch,
        token=_github_token(user),
    )

    return SyncResultResponse(
        status=result.status.value,
        commit_sha=result.commit_sha,
        branch=result.branch,
        url=result.url,
        files_synced=result.files_synced,
        conflicts=result.conflicts,
        message=result.message,
    )


@router.post("/projects/{project_id}/create-version")
async def create_version_branch(
    project_id: str,
    request: CreateVersionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GitHubSyncService = Depends(get_sync_service),
) -> dict:
    """
    Create a version branch (e.g., v1, v2).

    Forum recommendation: Use branches for major versions.
    """
    branch_name = await service.create_version_branch(
        repo_name=request.repo_name,
        version=request.version,
        base_branch=request.base_branch,
        token=_github_token(user),
    )

    return {
        "branch": branch_name,
        "message": f"Created version branch: {branch_name}",
    }


# ============================================
# File Freeze/Lock Endpoints
# ============================================


@router.post("/projects/{project_id}/files/freeze")
async def freeze_file(
    project_id: str,
    request: FreezeFileRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GitHubSyncService = Depends(get_sync_service),
) -> FileLockResponse:
    """
    Freeze a file to prevent sync.

    Forum recommendation: Freeze files in AI controls for stability.
    """
    lock = await service.freeze_file(
        project_id=project_id,
        path=request.path,
        user_id=user.id,
        reason=request.reason,
        lock_ai=request.lock_ai,
    )

    return FileLockResponse(
        path=lock.path,
        status=lock.status.value,
        locked_by=lock.locked_by,
        locked_at=lock.locked_at.isoformat(),
        reason=lock.reason,
    )


@router.delete("/projects/{project_id}/files/freeze/{path:path}")
async def unfreeze_file(
    project_id: str,
    path: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GitHubSyncService = Depends(get_sync_service),
) -> dict:
    """Unfreeze a file."""
    success = await service.unfreeze_file(
        project_id=project_id,
        path=path,
        user_id=user.id,
    )

    if not success:
        raise HTTPException(
            status_code=404, detail="File lock not found or unauthorized"
        )

    return {"success": True, "message": f"Unfroze {path}"}


@router.get("/projects/{project_id}/files/frozen")
async def get_frozen_files(
    project_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GitHubSyncService = Depends(get_sync_service),
) -> list[FileLockResponse]:
    """Get all frozen files for a project."""
    locks = await service.get_frozen_files(project_id)

    return [
        FileLockResponse(
            path=lock.path,
            status=lock.status.value,
            locked_by=lock.locked_by,
            locked_at=lock.locked_at.isoformat(),
            reason=lock.reason,
        )
        for lock in locks
    ]


# ============================================
# Pull Request Endpoints
# ============================================


@router.post("/pull-requests")
async def create_pull_request(
    request: CreatePRRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GitHubSyncService = Depends(get_sync_service),
) -> dict:
    """Create a pull request for merging branches."""
    try:
        pr = await service.create_pull_request(
            repo_name=request.repo_name,
            title=request.title,
            head=request.head,
            base=request.base,
            body=request.body,
            token=_github_token(user),
        )
        return pr
    except Exception as e:
        logger.error("Pull request creation failed: %s", e)
        raise HTTPException(status_code=400, detail="Pull request creation failed")


@router.post("/pull-requests/{repo_name}/{pr_number}/merge")
async def merge_pull_request(
    repo_name: str,
    pr_number: int,
    merge_method: str = "merge",
    user: AuthenticatedUser = Depends(get_current_user),
    service: GitHubSyncService = Depends(get_sync_service),
) -> dict:
    """Merge a pull request."""
    success = await service.merge_pull_request(
        repo_name=repo_name,
        pr_number=pr_number,
        merge_method=merge_method,
        token=_github_token(user),
    )

    if not success:
        raise HTTPException(status_code=400, detail="Failed to merge PR")

    return {"success": True, "message": f"Merged PR #{pr_number}"}


# ============================================
# Sync History
# ============================================


@router.get("/sync-history")
async def get_sync_history(
    limit: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GitHubSyncService = Depends(get_sync_service),
) -> list[SyncResultResponse]:
    """Get sync operation history."""
    history = service.get_sync_history(limit)

    return [
        SyncResultResponse(
            status=r.status.value,
            commit_sha=r.commit_sha,
            branch=r.branch,
            url=r.url,
            files_synced=r.files_synced,
            conflicts=r.conflicts,
            message=r.message,
        )
        for r in history
    ]


# ============================================
# Auto-Sync Webhook
# ============================================


@router.post("/webhooks/auto-sync")
async def auto_sync_webhook(
    project_id: str,
    background_tasks: BackgroundTasks,
    user: AuthenticatedUser = Depends(get_current_user),
    service: GitHubSyncService = Depends(get_sync_service),
):
    """
    Webhook for auto-syncing on project changes.

    Forum recommendation: Auto-sync on commit.
    """
    # This would be triggered by project save events
    # Background task to not block the response
    background_tasks.add_task(
        _perform_auto_sync,
        project_id,
        service,
    )

    return {"status": "queued", "message": "Auto-sync started"}


async def _perform_auto_sync(project_id: str, service: GitHubSyncService):
    """Background task for auto-sync."""
    try:
        from services.project_service import get_project_service

        project_service = get_project_service()
        project = project_service.get(project_id)
        if project and project.files and project.settings.get("github_repo"):
            repo = project.settings["github_repo"]
            await service.export_to_github(
                project_id=project_id,
                repo=repo,
                files=project.files,
                message=f"Auto-sync from VOS3: {project.name}",
            )
    except Exception:
        pass  # Auto-sync is best-effort


# ============================================
# Health Check
# ============================================


@router.get("/health")
async def health_check(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "github-sync",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
