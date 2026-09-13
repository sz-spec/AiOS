"""
GitHub Sync Service

Based on forum research (Dec 2025):
- Reddit r/vbuilder: Export to GitHub with auto-versioning
- Reddit r/Base44: File freeze for AI stability
- DEV.to: Hosting + VCS integration
- X: Automated version control for AI-generated apps

Features:
- Export project to GitHub
- Sync branches bidirectionally
- Auto-commit AI changes
- File freeze/lock for stability
- Pull request creation
"""

import os
import base64
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import httpx


class SyncDirection(str, Enum):
    """Sync direction."""

    PUSH = "push"  # Local → GitHub
    PULL = "pull"  # GitHub → Local
    BIDIRECTIONAL = "bidirectional"


class FileStatus(str, Enum):
    """File status in sync."""

    SYNCED = "synced"
    MODIFIED_LOCAL = "modified_local"
    MODIFIED_REMOTE = "modified_remote"
    CONFLICT = "conflict"
    NEW_LOCAL = "new_local"
    NEW_REMOTE = "new_remote"
    DELETED_LOCAL = "deleted_local"
    DELETED_REMOTE = "deleted_remote"
    FROZEN = "frozen"  # Locked from AI changes


@dataclass
class GitHubRepo:
    """GitHub repository info."""

    owner: str
    name: str
    default_branch: str = "main"
    private: bool = True
    url: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class FileSyncStatus:
    """Status of a file in sync."""

    path: str
    status: FileStatus
    local_sha: Optional[str] = None
    remote_sha: Optional[str] = None
    last_synced: Optional[datetime] = None


# Alias for backward compatibility
SyncStatus = FileSyncStatus


@dataclass
class SyncResult:
    """Result of sync operation."""

    success: bool
    files_pushed: list[str]
    files_pulled: list[str]
    conflicts: list[str]
    errors: list[str]
    commit_url: Optional[str] = None


@dataclass
class FrozenFile:
    """A file frozen from AI modifications."""

    path: str
    frozen_at: datetime
    frozen_by: str
    reason: Optional[str] = None
    sha: str = ""  # SHA at freeze time


class GitHubSyncService:
    """
    GitHub synchronization service.

    Based on forum recommendations:
    - Export to private repos
    - Branch strategy (main/dev)
    - Auto-commit AI changes
    - File freeze for stability
    """

    GITHUB_API = "https://api.github.com"

    def __init__(
        self,
        token: Optional[str] = None,
    ):
        self.token = token or os.getenv("GITHUB_TOKEN")

        # In-memory storage (replace with DB in production)
        self._frozen_files: dict[str, FrozenFile] = {}
        self._sync_status: dict[str, FileSyncStatus] = {}
        self._linked_repos: dict[str, GitHubRepo] = {}  # project_id -> repo

    # ==========================================
    # GitHub API Helpers
    # ==========================================

    async def _github_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict] = None,
        token: Optional[str] = None,
    ) -> dict:
        """Make GitHub API request."""
        headers = {
            "Authorization": f"Bearer {token or self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{self.GITHUB_API}{endpoint}",
                headers=headers,
                json=data,
                timeout=30.0,
            )

            if response.status_code >= 400:
                error = (
                    response.json()
                    if response.content
                    else {"message": "Unknown error"}
                )
                raise Exception(f"GitHub API error: {error.get('message', str(error))}")

            return response.json() if response.content else {}

    # ==========================================
    # Repository Management
    # ==========================================

    async def create_repo(
        self,
        name: str,
        description: str = "",
        private: bool = True,
        token: Optional[str] = None,
    ) -> GitHubRepo:
        """Create a new GitHub repository."""
        data = {
            "name": name,
            "description": description or f"AI App Builder project - {name}",
            "private": private,
            "auto_init": True,
            "gitignore_template": "Node",
        }

        result = await self._github_request("POST", "/user/repos", data, token)

        return GitHubRepo(
            owner=result["owner"]["login"],
            name=result["name"],
            default_branch=result["default_branch"],
            private=result["private"],
            url=result["html_url"],
        )

    async def get_repo(
        self,
        owner: str,
        name: str,
        token: Optional[str] = None,
    ) -> Optional[GitHubRepo]:
        """Get repository info."""
        try:
            result = await self._github_request(
                "GET", f"/repos/{owner}/{name}", token=token
            )
            return GitHubRepo(
                owner=result["owner"]["login"],
                name=result["name"],
                default_branch=result["default_branch"],
                private=result["private"],
                url=result["html_url"],
            )
        except:
            return None

    async def link_project_to_repo(
        self,
        project_id: str,
        repo_owner: str,
        repo_name: str,
        token: Optional[str] = None,
    ) -> GitHubRepo:
        """Link a project to a GitHub repo (create if not exists)."""
        repo = await self.get_repo(repo_owner, repo_name, token)

        if not repo:
            repo = await self.create_repo(repo_name, token=token)

        self._linked_repos[project_id] = repo
        return repo

    # ==========================================
    # Export to GitHub (Forum: Reddit best practices)
    # ==========================================

    async def export_to_github(
        self,
        project_id: str,
        files: dict[str, str],  # path -> content
        branch: str = "main",
        commit_message: str = "AI generated code",
        token: Optional[str] = None,
    ) -> SyncResult:
        """
        Export project files to GitHub.

        Based on Reddit recommendation:
        - Create versioned folders (v1/, v2/)
        - Use descriptive commit messages
        - Support branch selection
        """
        repo = self._linked_repos.get(project_id)
        if not repo:
            return SyncResult(
                success=False,
                files_pushed=[],
                files_pulled=[],
                conflicts=[],
                errors=["Project not linked to a GitHub repository"],
            )

        pushed_files = []
        errors = []

        for path, content in files.items():
            # Skip frozen files
            if self.is_file_frozen(project_id, path):
                errors.append(f"Skipped frozen file: {path}")
                continue

            try:
                await self._create_or_update_file(
                    repo=repo,
                    path=path,
                    content=content,
                    branch=branch,
                    message=f"{commit_message}: {path}",
                    token=token,
                )
                pushed_files.append(path)

                # Update sync status
                self._sync_status[f"{project_id}:{path}"] = FileSyncStatus(
                    path=path,
                    status=FileStatus.SYNCED,
                    local_sha=self._hash_content(content),
                    last_synced=datetime.now(timezone.utc),
                )
            except Exception as e:
                errors.append(f"Failed to push {path}: {str(e)}")

        return SyncResult(
            success=len(errors) == 0,
            files_pushed=pushed_files,
            files_pulled=[],
            conflicts=[],
            errors=errors,
            commit_url=f"{repo.url}/commits/{branch}",
        )

    async def _create_or_update_file(
        self,
        repo: GitHubRepo,
        path: str,
        content: str,
        branch: str,
        message: str,
        token: Optional[str] = None,
    ):
        """Create or update a file in GitHub."""
        # Check if file exists
        existing_sha = None
        try:
            result = await self._github_request(
                "GET",
                f"/repos/{repo.full_name}/contents/{path}?ref={branch}",
                token=token,
            )
            existing_sha = result.get("sha")
        except:
            pass  # File doesn't exist

        # Prepare content
        content_b64 = base64.b64encode(content.encode()).decode()

        data = {
            "message": message,
            "content": content_b64,
            "branch": branch,
        }

        if existing_sha:
            data["sha"] = existing_sha

        await self._github_request(
            "PUT",
            f"/repos/{repo.full_name}/contents/{path}",
            data,
            token,
        )

    # ==========================================
    # Pull from GitHub
    # ==========================================

    async def pull_from_github(
        self,
        project_id: str,
        branch: str = "main",
        token: Optional[str] = None,
    ) -> tuple[dict[str, str], SyncResult]:
        """
        Pull files from GitHub to local project.
        Returns (files dict, result).
        """
        repo = self._linked_repos.get(project_id)
        if not repo:
            return {}, SyncResult(
                success=False,
                files_pushed=[],
                files_pulled=[],
                conflicts=[],
                errors=["Project not linked to a GitHub repository"],
            )

        files = {}
        pulled = []
        errors = []

        try:
            # Get tree
            tree = await self._github_request(
                "GET",
                f"/repos/{repo.full_name}/git/trees/{branch}?recursive=1",
                token=token,
            )

            for item in tree.get("tree", []):
                if item["type"] != "blob":
                    continue

                path = item["path"]

                # Skip certain files
                if path.startswith(".git") or path == ".gitignore":
                    continue

                try:
                    content = await self._get_file_content(repo, path, branch, token)
                    files[path] = content
                    pulled.append(path)

                    # Update sync status
                    self._sync_status[f"{project_id}:{path}"] = FileSyncStatus(
                        path=path,
                        status=FileStatus.SYNCED,
                        remote_sha=item["sha"],
                        last_synced=datetime.now(timezone.utc),
                    )
                except Exception as e:
                    errors.append(f"Failed to pull {path}: {str(e)}")

        except Exception as e:
            errors.append(f"Failed to get repository tree: {str(e)}")

        return files, SyncResult(
            success=len(errors) == 0,
            files_pushed=[],
            files_pulled=pulled,
            conflicts=[],
            errors=errors,
        )

    async def _get_file_content(
        self,
        repo: GitHubRepo,
        path: str,
        branch: str,
        token: Optional[str] = None,
    ) -> str:
        """Get file content from GitHub."""
        result = await self._github_request(
            "GET",
            f"/repos/{repo.full_name}/contents/{path}?ref={branch}",
            token=token,
        )

        content_b64 = result.get("content", "")
        return base64.b64decode(content_b64).decode()

    # ==========================================
    # Auto-Commit AI Changes (Forum: AI auto-versioning)
    # ==========================================

    async def auto_commit_ai_changes(
        self,
        project_id: str,
        files: dict[str, str],
        ai_session_id: str,
        prompt_summary: str = "",
        token: Optional[str] = None,
    ) -> SyncResult:
        """
        Automatically commit AI-generated changes.

        Based on forum recommendation:
        - Auto-commit with descriptive messages
        - Include AI session ID for tracking
        - Skip frozen files
        """
        # Filter out frozen files
        unfrozen_files = {
            path: content
            for path, content in files.items()
            if not self.is_file_frozen(project_id, path)
        }

        if not unfrozen_files:
            return SyncResult(
                success=True,
                files_pushed=[],
                files_pulled=[],
                conflicts=[],
                errors=["All files are frozen"],
            )

        # Create commit message
        message = f"AI Auto-commit [{ai_session_id[:8]}]"
        if prompt_summary:
            message += f": {prompt_summary[:100]}"

        return await self.export_to_github(
            project_id=project_id,
            files=unfrozen_files,
            branch="dev",  # AI changes go to dev branch
            commit_message=message,
            token=token,
        )

    # ==========================================
    # File Freeze (Forum: Base44 Reddit)
    # ==========================================

    def freeze_file(
        self,
        project_id: str,
        path: str,
        user_id: str,
        reason: Optional[str] = None,
        current_content: Optional[str] = None,
    ) -> FrozenFile:
        """
        Freeze a file from AI modifications.

        Based on Base44 Reddit recommendation:
        - Lock files for VCS stability
        - Prevent AI from modifying critical files
        """
        key = f"{project_id}:{path}"

        frozen = FrozenFile(
            path=path,
            frozen_at=datetime.now(timezone.utc),
            frozen_by=user_id,
            reason=reason,
            sha=self._hash_content(current_content) if current_content else "",
        )

        self._frozen_files[key] = frozen

        # Update sync status
        self._sync_status[key] = FileSyncStatus(
            path=path,
            status=FileStatus.FROZEN,
        )

        return frozen

    def unfreeze_file(
        self,
        project_id: str,
        path: str,
        user_id: str,
    ) -> bool:
        """Unfreeze a file."""
        key = f"{project_id}:{path}"

        if key in self._frozen_files:
            del self._frozen_files[key]

            # Update status
            if key in self._sync_status:
                self._sync_status[key].status = FileStatus.SYNCED

            return True

        return False

    def is_file_frozen(self, project_id: str, path: str) -> bool:
        """Check if file is frozen."""
        return f"{project_id}:{path}" in self._frozen_files

    def get_frozen_files(self, project_id: str) -> list[FrozenFile]:
        """Get all frozen files for a project."""
        prefix = f"{project_id}:"
        return [f for key, f in self._frozen_files.items() if key.startswith(prefix)]

    # ==========================================
    # Branch Management (Forum: Git flow)
    # ==========================================

    async def create_branch(
        self,
        project_id: str,
        branch_name: str,
        from_branch: str = "main",
        token: Optional[str] = None,
    ) -> dict:
        """Create a new branch in GitHub."""
        repo = self._linked_repos.get(project_id)
        if not repo:
            raise ValueError("Project not linked to GitHub")

        # Get SHA of source branch
        ref = await self._github_request(
            "GET",
            f"/repos/{repo.full_name}/git/refs/heads/{from_branch}",
            token=token,
        )
        sha = ref["object"]["sha"]

        # Create new branch
        result = await self._github_request(
            "POST",
            f"/repos/{repo.full_name}/git/refs",
            {
                "ref": f"refs/heads/{branch_name}",
                "sha": sha,
            },
            token,
        )

        return {
            "branch": branch_name,
            "sha": result["object"]["sha"],
            "url": f"{repo.url}/tree/{branch_name}",
        }

    async def create_pull_request(
        self,
        project_id: str,
        title: str,
        source_branch: str,
        target_branch: str = "main",
        body: str = "",
        token: Optional[str] = None,
    ) -> dict:
        """Create a pull request."""
        repo = self._linked_repos.get(project_id)
        if not repo:
            raise ValueError("Project not linked to GitHub")

        result = await self._github_request(
            "POST",
            f"/repos/{repo.full_name}/pulls",
            {
                "title": title,
                "body": body or f"Merge {source_branch} into {target_branch}",
                "head": source_branch,
                "base": target_branch,
            },
            token,
        )

        return {
            "number": result["number"],
            "url": result["html_url"],
            "state": result["state"],
        }

    # ==========================================
    # Sync Status
    # ==========================================

    async def get_sync_status(
        self,
        project_id: str,
        local_files: dict[str, str],
        branch: str = "main",
        token: Optional[str] = None,
    ) -> list[FileSyncStatus]:
        """Get sync status for all files."""
        repo = self._linked_repos.get(project_id)
        if not repo:
            return []

        statuses = []

        # Get remote files
        try:
            remote_files, _ = await self.pull_from_github(project_id, branch, token)
        except:
            remote_files = {}

        all_paths = set(local_files.keys()) | set(remote_files.keys())

        for path in all_paths:
            local_content = local_files.get(path)
            remote_content = remote_files.get(path)

            local_sha = self._hash_content(local_content) if local_content else None
            remote_sha = self._hash_content(remote_content) if remote_content else None

            # Check frozen
            if self.is_file_frozen(project_id, path):
                status = FileStatus.FROZEN
            elif local_sha and not remote_sha:
                status = FileStatus.NEW_LOCAL
            elif remote_sha and not local_sha:
                status = FileStatus.NEW_REMOTE
            elif local_sha == remote_sha:
                status = FileStatus.SYNCED
            else:
                # Both modified - check last sync
                cached = self._sync_status.get(f"{project_id}:{path}")
                if cached and cached.local_sha == local_sha:
                    status = FileStatus.MODIFIED_REMOTE
                elif cached and cached.remote_sha == remote_sha:
                    status = FileStatus.MODIFIED_LOCAL
                else:
                    status = FileStatus.CONFLICT

            statuses.append(
                FileSyncStatus(
                    path=path,
                    status=status,
                    local_sha=local_sha,
                    remote_sha=remote_sha,
                )
            )

        return statuses

    # ==========================================
    # Utilities
    # ==========================================

    def _hash_content(self, content: Optional[str]) -> str:
        """Hash content for comparison."""
        if not content:
            return ""
        return hashlib.sha256(content.encode()).hexdigest()[:12]


# ============================================
# API Routes
# ============================================

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

router = APIRouter(prefix="/github-sync", tags=["github-sync"])


class LinkRepoRequest(BaseModel):
    repo_owner: str
    repo_name: str
    token: str


class ExportRequest(BaseModel):
    files: dict[str, str]
    branch: str = "main"
    commit_message: str = "AI generated code"
    token: str


class AutoCommitRequest(BaseModel):
    files: dict[str, str]
    ai_session_id: str
    prompt_summary: str = ""
    token: str


class FreezeFileRequest(BaseModel):
    path: str
    reason: str = ""
    current_content: str = ""


class CreatePRRequest(BaseModel):
    title: str
    source_branch: str
    target_branch: str = "main"
    body: str = ""
    token: str


def get_sync_service() -> GitHubSyncService:
    return GitHubSyncService()


@router.post("/projects/{project_id}/link")
async def link_repo(
    project_id: str,
    request: LinkRepoRequest,
    service: GitHubSyncService = Depends(get_sync_service),
):
    """Link project to GitHub repository."""
    try:
        repo = await service.link_project_to_repo(
            project_id,
            request.repo_owner,
            request.repo_name,
            request.token,
        )
        return {"status": "linked", "repo": repo.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/projects/{project_id}/export")
async def export_to_github(
    project_id: str,
    request: ExportRequest,
    service: GitHubSyncService = Depends(get_sync_service),
):
    """Export project to GitHub."""
    result = await service.export_to_github(
        project_id,
        request.files,
        request.branch,
        request.commit_message,
        request.token,
    )
    return result


@router.post("/projects/{project_id}/pull")
async def pull_from_github(
    project_id: str,
    branch: str = "main",
    token: str = "",
    service: GitHubSyncService = Depends(get_sync_service),
):
    """Pull files from GitHub."""
    files, result = await service.pull_from_github(project_id, branch, token)
    return {"files": files, "result": result}


@router.post("/projects/{project_id}/auto-commit")
async def auto_commit(
    project_id: str,
    request: AutoCommitRequest,
    service: GitHubSyncService = Depends(get_sync_service),
):
    """Auto-commit AI changes."""
    result = await service.auto_commit_ai_changes(
        project_id,
        request.files,
        request.ai_session_id,
        request.prompt_summary,
        request.token,
    )
    return result


@router.post("/projects/{project_id}/freeze")
async def freeze_file(
    project_id: str,
    request: FreezeFileRequest,
    user_id: str = "user_123",
    service: GitHubSyncService = Depends(get_sync_service),
):
    """Freeze file from AI modifications."""
    frozen = service.freeze_file(
        project_id,
        request.path,
        user_id,
        request.reason,
        request.current_content,
    )
    return {"status": "frozen", "file": frozen}


@router.delete("/projects/{project_id}/freeze/{path:path}")
async def unfreeze_file(
    project_id: str,
    path: str,
    user_id: str = "user_123",
    service: GitHubSyncService = Depends(get_sync_service),
):
    """Unfreeze file."""
    success = service.unfreeze_file(project_id, path, user_id)
    return {"status": "unfrozen" if success else "not_found"}


@router.get("/projects/{project_id}/frozen")
async def get_frozen_files(
    project_id: str,
    service: GitHubSyncService = Depends(get_sync_service),
):
    """Get frozen files."""
    files = service.get_frozen_files(project_id)
    return {"frozen_files": files}


@router.post("/projects/{project_id}/branch")
async def create_branch(
    project_id: str,
    branch_name: str,
    from_branch: str = "main",
    token: str = "",
    service: GitHubSyncService = Depends(get_sync_service),
):
    """Create new branch."""
    try:
        result = await service.create_branch(
            project_id, branch_name, from_branch, token
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/projects/{project_id}/pull-request")
async def create_pull_request(
    project_id: str,
    request: CreatePRRequest,
    service: GitHubSyncService = Depends(get_sync_service),
):
    """Create pull request."""
    try:
        result = await service.create_pull_request(
            project_id,
            request.title,
            request.source_branch,
            request.target_branch,
            request.body,
            request.token,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


__all__ = [
    "GitHubSyncService",
    "GitHubRepo",
    "SyncResult",
    "FileStatus",
    "FrozenFile",
    "router",
]
