"""
GitHub Sync Service
====================
Push generated code to GitHub repositories.
Based on community recommendations from r/vbuilder discussions.

Features:
- Auto-sync generated code to GitHub
- Create/update files in repos
- Branch management
- Commit history
"""

import os
import base64
import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

import httpx
from pydantic import BaseModel

# =============================================================================
# Configuration
# =============================================================================

GITHUB_API_URL = "https://api.github.com"


# =============================================================================
# Models
# =============================================================================


class GitHubFile(BaseModel):
    """File to sync to GitHub."""

    path: str
    content: str
    message: Optional[str] = None


class SyncResult(BaseModel):
    """Result of sync operation."""

    success: bool
    files_synced: List[str]
    commit_sha: Optional[str] = None
    error: Optional[str] = None


@dataclass
class GitHubConfig:
    """GitHub configuration."""

    token: str
    owner: str
    repo: str
    branch: str = "main"

    @classmethod
    def from_env(cls) -> "GitHubConfig":
        return cls(
            token=os.getenv("GITHUB_TOKEN", ""),
            owner=os.getenv("GITHUB_OWNER", ""),
            repo=os.getenv("GITHUB_REPO", ""),
            branch=os.getenv("GITHUB_BRANCH", "main"),
        )


# =============================================================================
# GitHub Sync Service
# =============================================================================


class GitHubSyncService:
    """
    Service for syncing generated code to GitHub.

    Usage:
        sync = GitHubSyncService(token="...", owner="user", repo="my-app")
        result = await sync.sync_files([
            GitHubFile(path="src/App.tsx", content="..."),
            GitHubFile(path="package.json", content="..."),
        ])
    """

    def __init__(
        self,
        token: str = None,
        owner: str = None,
        repo: str = None,
        branch: str = "main",
    ):
        config = GitHubConfig.from_env()
        self.token = token or config.token
        self.owner = owner or config.owner
        self.repo = repo or config.repo
        self.branch = branch or config.branch

        self.client = httpx.AsyncClient(
            base_url=GITHUB_API_URL,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()

    # -------------------------------------------------------------------------
    # Repository Operations
    # -------------------------------------------------------------------------

    async def get_repo(self) -> Dict[str, Any]:
        """Get repository information."""
        response = await self.client.get(f"/repos/{self.owner}/{self.repo}")
        response.raise_for_status()
        return response.json()

    async def create_repo(self, name: str, private: bool = False) -> Dict[str, Any]:
        """Create a new repository."""
        response = await self.client.post(
            "/user/repos",
            json={
                "name": name,
                "private": private,
                "auto_init": True,
            },
        )
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # File Operations
    # -------------------------------------------------------------------------

    async def get_file(self, path: str) -> Optional[Dict[str, Any]]:
        """Get file content and metadata."""
        try:
            response = await self.client.get(
                f"/repos/{self.owner}/{self.repo}/contents/{path}",
                params={"ref": self.branch},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def create_or_update_file(
        self, path: str, content: str, message: str = None
    ) -> Dict[str, Any]:
        """Create or update a file in the repository."""

        # Check if file exists (to get SHA for update)
        existing = await self.get_file(path)

        # Encode content
        encoded_content = base64.b64encode(content.encode()).decode()

        # Build request
        data = {
            "message": message or f"Update {path}",
            "content": encoded_content,
            "branch": self.branch,
        }

        if existing:
            data["sha"] = existing["sha"]

        response = await self.client.put(
            f"/repos/{self.owner}/{self.repo}/contents/{path}", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_file(self, path: str, message: str = None) -> Dict[str, Any]:
        """Delete a file from the repository."""
        existing = await self.get_file(path)
        if not existing:
            raise ValueError(f"File not found: {path}")

        response = await self.client.delete(
            f"/repos/{self.owner}/{self.repo}/contents/{path}",
            json={
                "message": message or f"Delete {path}",
                "sha": existing["sha"],
                "branch": self.branch,
            },
        )
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # Batch Sync
    # -------------------------------------------------------------------------

    async def sync_files(
        self, files: List[GitHubFile], commit_message: str = None
    ) -> SyncResult:
        """
        Sync multiple files to GitHub.

        For large batches, uses the Git Data API for a single commit.
        For small batches (<5 files), uses individual file updates.
        """

        if not files:
            return SyncResult(success=True, files_synced=[])

        if len(files) < 5:
            # Simple approach for few files
            return await self._sync_files_simple(files)
        else:
            # Batch approach using Git Data API
            return await self._sync_files_batch(files, commit_message)

    async def _sync_files_simple(self, files: List[GitHubFile]) -> SyncResult:
        """Sync files one by one (for small batches)."""
        synced = []
        errors = []

        for file in files:
            try:
                await self.create_or_update_file(
                    path=file.path,
                    content=file.content,
                    message=file.message or f"Update {file.path}",
                )
                synced.append(file.path)
            except Exception as e:
                errors.append(f"{file.path}: {str(e)}")

        return SyncResult(
            success=len(errors) == 0,
            files_synced=synced,
            error="; ".join(errors) if errors else None,
        )

    async def _sync_files_batch(
        self, files: List[GitHubFile], commit_message: str = None
    ) -> SyncResult:
        """
        Sync files in a single commit using Git Data API.
        More efficient for large batches.
        """
        try:
            # 1. Get current commit SHA
            ref_response = await self.client.get(
                f"/repos/{self.owner}/{self.repo}/git/refs/heads/{self.branch}"
            )
            ref_response.raise_for_status()
            current_commit_sha = ref_response.json()["object"]["sha"]

            # 2. Get current tree
            commit_response = await self.client.get(
                f"/repos/{self.owner}/{self.repo}/git/commits/{current_commit_sha}"
            )
            commit_response.raise_for_status()
            current_tree_sha = commit_response.json()["tree"]["sha"]

            # 3. Create blobs for each file
            tree_items = []
            for file in files:
                blob_response = await self.client.post(
                    f"/repos/{self.owner}/{self.repo}/git/blobs",
                    json={"content": file.content, "encoding": "utf-8"},
                )
                blob_response.raise_for_status()
                blob_sha = blob_response.json()["sha"]

                tree_items.append(
                    {
                        "path": file.path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_sha,
                    }
                )

            # 4. Create new tree
            tree_response = await self.client.post(
                f"/repos/{self.owner}/{self.repo}/git/trees",
                json={"base_tree": current_tree_sha, "tree": tree_items},
            )
            tree_response.raise_for_status()
            new_tree_sha = tree_response.json()["sha"]

            # 5. Create commit
            commit_response = await self.client.post(
                f"/repos/{self.owner}/{self.repo}/git/commits",
                json={
                    "message": commit_message or f"AI Generated: {len(files)} files",
                    "tree": new_tree_sha,
                    "parents": [current_commit_sha],
                },
            )
            commit_response.raise_for_status()
            new_commit_sha = commit_response.json()["sha"]

            # 6. Update reference
            await self.client.patch(
                f"/repos/{self.owner}/{self.repo}/git/refs/heads/{self.branch}",
                json={"sha": new_commit_sha},
            )

            return SyncResult(
                success=True,
                files_synced=[f.path for f in files],
                commit_sha=new_commit_sha,
            )

        except Exception as e:
            return SyncResult(success=False, files_synced=[], error=str(e))

    # -------------------------------------------------------------------------
    # Branch Operations
    # -------------------------------------------------------------------------

    async def create_branch(
        self, branch_name: str, from_branch: str = None
    ) -> Dict[str, Any]:
        """Create a new branch."""
        from_branch = from_branch or self.branch

        # Get SHA of source branch
        ref_response = await self.client.get(
            f"/repos/{self.owner}/{self.repo}/git/refs/heads/{from_branch}"
        )
        ref_response.raise_for_status()
        sha = ref_response.json()["object"]["sha"]

        # Create new branch
        response = await self.client.post(
            f"/repos/{self.owner}/{self.repo}/git/refs",
            json={"ref": f"refs/heads/{branch_name}", "sha": sha},
        )
        response.raise_for_status()
        return response.json()

    async def create_pull_request(
        self, title: str, head: str, base: str = None, body: str = ""
    ) -> Dict[str, Any]:
        """Create a pull request."""
        response = await self.client.post(
            f"/repos/{self.owner}/{self.repo}/pulls",
            json={
                "title": title,
                "head": head,
                "base": base or self.branch,
                "body": body,
            },
        )
        response.raise_for_status()
        return response.json()


# =============================================================================
# Convenience Functions
# =============================================================================


async def sync_project_to_github(
    files: Dict[str, str],
    repo: str = None,
    owner: str = None,
    token: str = None,
    message: str = None,
) -> SyncResult:
    """
    Convenience function to sync a project to GitHub.

    Args:
        files: Dict of {path: content}
        repo: Repository name
        owner: Repository owner
        token: GitHub token
        message: Commit message

    Returns:
        SyncResult with status
    """
    service = GitHubSyncService(token=token, owner=owner, repo=repo)

    try:
        github_files = [
            GitHubFile(path=path, content=content) for path, content in files.items()
        ]

        result = await service.sync_files(github_files, message)
        return result
    finally:
        await service.close()


# =============================================================================
# Example Usage
# =============================================================================

if __name__ == "__main__":

    async def main():
        # Example: Sync generated files to GitHub
        files = {
            "src/App.tsx": "import React from 'react';\nexport default function App() { return <div>Hello</div>; }",
            "package.json": '{"name": "my-app", "version": "1.0.0"}',
        }

        result = await sync_project_to_github(
            files=files, repo="my-generated-app", message="AI Generated App"
        )

        print(f"Success: {result.success}")
        print(f"Files synced: {result.files_synced}")
        if result.commit_sha:
            print(f"Commit: {result.commit_sha}")

    asyncio.run(main())
