"""
Version Control API Routes

Endpoints for version control operations:
- Branch management (list, create, get, delete, switch, stats)
- Commit tracking (create, history, get, file-at-commit, revert)
- Diff (branches, commits, single file)
- Merge and conflict resolution
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from api.deps import get_current_user, AuthenticatedUser
from tools.version_control import VersionControlService

router = APIRouter(prefix="/version-control", tags=["Version Control"])


# ---------------------------------------------------------------------------
# Service dependency
# ---------------------------------------------------------------------------

_vc_service: Optional[VersionControlService] = None


def get_vc_service() -> VersionControlService:
    """Get or create the singleton VersionControlService."""
    global _vc_service
    if _vc_service is None:
        _vc_service = VersionControlService()
    return _vc_service


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateBranchRequest(BaseModel):
    name: str
    description: Optional[str] = None


class CreateCommitRequest(BaseModel):
    branch: str
    message: str
    files: dict


class RevertRequest(BaseModel):
    branch: str
    commit_id: str


class MergeRequest(BaseModel):
    source_branch: str
    target_branch: str


class ResolveConflictsRequest(BaseModel):
    branch: str
    resolutions: dict
    message: str


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_branch(branch) -> dict:
    """Convert a Branch dataclass to a JSON-safe dict."""
    return {
        "name": branch.name,
        "project_id": branch.project_id,
        "head_commit_id": branch.head_commit_id,
        "base_branch": branch.base_branch,
        "created_at": (
            branch.created_at.isoformat()
            if isinstance(branch.created_at, datetime)
            else str(branch.created_at)
        ),
        "created_by": branch.created_by,
        "is_default": branch.is_default,
        "is_protected": branch.is_protected,
        "description": getattr(branch, "description", None),
    }


def _serialize_commit(commit) -> dict:
    """Convert a Commit dataclass to a JSON-safe dict."""
    changes = []
    for c in getattr(commit, "changes", []):
        changes.append(
            {
                "path": c.path,
                "change_type": c.change_type,
                "additions": c.additions,
                "deletions": c.deletions,
            }
        )
    return {
        "id": commit.id,
        "project_id": commit.project_id,
        "branch": commit.branch,
        "message": commit.message,
        "author_id": commit.author_id,
        "author_name": commit.author_name,
        "parent_id": commit.parent_id,
        "created_at": (
            commit.created_at.isoformat()
            if isinstance(commit.created_at, datetime)
            else str(commit.created_at)
        ),
        "changes": changes,
        "file_count": len(getattr(commit, "files", {})),
    }


def _serialize_diff(diff) -> dict:
    """Convert a FileDiff dataclass to a JSON-safe dict."""
    hunks = []
    for h in getattr(diff, "hunks", []):
        hunks.append(
            {
                "old_start": h.old_start,
                "old_count": h.old_count,
                "new_start": h.new_start,
                "new_count": h.new_count,
                "lines": h.lines,
            }
        )
    return {
        "path": diff.path,
        "old_path": diff.old_path,
        "change_type": diff.change_type,
        "hunks": hunks,
        "additions": diff.additions,
        "deletions": diff.deletions,
    }


def _serialize_merge_result(result) -> dict:
    """Convert a MergeResult dataclass to a JSON-safe dict."""
    conflicts = []
    for c in getattr(result, "conflicts", []):
        conflicts.append(
            {
                "path": c.path,
                "ours": c.ours,
                "theirs": c.theirs,
                "base": c.base,
                "conflict_markers": c.conflict_markers,
            }
        )
    return {
        "success": result.success,
        "commit_id": result.commit_id,
        "conflicts": conflicts,
        "merged_files": result.merged_files,
        "message": result.message,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health")
async def version_control_health():
    """Version control service health check."""
    return {
        "status": "healthy",
        "service": "version-control",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/branches")
async def list_branches(
    project_id: str,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    branches = await svc.list_branches(project_id)
    return [_serialize_branch(b) for b in branches]


@router.post("/projects/{project_id}/branches")
async def create_branch(
    project_id: str,
    body: CreateBranchRequest,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        branch = await svc.create_branch(
            project_id=project_id,
            name=body.name,
            created_by=user.id,
            description=body.description,
        )
        return _serialize_branch(branch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/projects/{project_id}/branches/{branch_name}")
async def get_branch(
    project_id: str,
    branch_name: str,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    branch = await svc.get_branch(project_id, branch_name)
    if branch is None:
        raise HTTPException(status_code=404, detail="Branch not found")
    return _serialize_branch(branch)


@router.delete("/projects/{project_id}/branches/{branch_name}")
async def delete_branch(
    project_id: str,
    branch_name: str,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        result = await svc.delete_branch(project_id, branch_name)
        if not result:
            raise HTTPException(status_code=404, detail="Branch not found")
        return {"success": True, "message": f"Deleted branch {branch_name}"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/projects/{project_id}/branches/{branch_name}/switch")
async def switch_branch(
    project_id: str,
    branch_name: str,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    files = await svc.switch_branch(project_id, branch_name)
    if files is None:
        raise HTTPException(status_code=404, detail="Branch not found")
    return {"branch": branch_name, "files": files, "file_count": len(files)}


@router.get("/projects/{project_id}/branches/{branch_name}/stats")
async def get_branch_stats(
    project_id: str,
    branch_name: str,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    stats = await svc.get_branch_stats(project_id, branch_name)
    return {"branch": branch_name, **stats}


# ---------------------------------------------------------------------------
# Commits
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/commits")
async def create_commit(
    project_id: str,
    body: CreateCommitRequest,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        commit = await svc.create_commit(
            project_id=project_id,
            branch=body.branch,
            message=body.message,
            files=body.files,
            author_id=user.id,
        )
        return _serialize_commit(commit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/projects/{project_id}/commits")
async def get_commit_history(
    project_id: str,
    branch: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    commits = await svc.get_commit_history(project_id, branch=branch, limit=limit)
    return [_serialize_commit(c) for c in commits]


@router.get("/commits/{commit_id}")
async def get_commit(
    commit_id: str,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    commit = await svc.get_commit(commit_id)
    if commit is None:
        raise HTTPException(status_code=404, detail="Commit not found")
    return _serialize_commit(commit)


@router.get("/commits/{commit_id}/files/{path:path}")
async def get_file_at_commit(
    commit_id: str,
    path: str,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    content = await svc.get_file_at_commit(commit_id, path)
    if content is None:
        raise HTTPException(status_code=404, detail="File not found at commit")
    return {"commit_id": commit_id, "path": path, "content": content}


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/revert")
async def revert_to_commit(
    project_id: str,
    body: RevertRequest,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        commit = await svc.revert_to_commit(
            project_id=project_id,
            branch=body.branch,
            commit_id=body.commit_id,
            author_id=user.id,
        )
        return _serialize_commit(commit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Diffs
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/diff/branches")
async def diff_branches(
    project_id: str,
    source: str = Query(...),
    target: str = Query(...),
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        diffs = await svc.diff_branches(project_id, source, target)
        return [_serialize_diff(d) for d in diffs]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/diff/commits")
async def diff_commits(
    commit_a: str = Query(...),
    commit_b: str = Query(...),
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    diffs = await svc.diff_commits(commit_a, commit_b)
    return [_serialize_diff(d) for d in diffs]


@router.get("/projects/{project_id}/diff/file")
async def diff_file(
    project_id: str,
    branch: str = Query(...),
    path: str = Query(...),
    commit_id: Optional[str] = Query(default=None),
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    diff = await svc.diff_file(project_id, branch, path, commit_id)
    if diff is None:
        raise HTTPException(status_code=404, detail="File diff not found")
    return _serialize_diff(diff)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/merge")
async def merge_branches(
    project_id: str,
    body: MergeRequest,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    result = await svc.merge_branches(
        project_id=project_id,
        source_branch=body.source_branch,
        target_branch=body.target_branch,
        author_id=user.id,
    )
    return _serialize_merge_result(result)


@router.post("/projects/{project_id}/resolve-conflicts")
async def resolve_conflicts(
    project_id: str,
    body: ResolveConflictsRequest,
    svc: VersionControlService = Depends(get_vc_service),
    user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        commit = await svc.resolve_conflicts(
            project_id=project_id,
            branch=body.branch,
            resolutions=body.resolutions,
            message=body.message,
            author_id=user.id,
        )
        return _serialize_commit(commit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
