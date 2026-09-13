"""
BMAD Project Manager
=====================

Creates and manages project folders with Git integration.
Each BMAD session gets a project directory with:
- Git repository
- Planning and implementation artifact directories
- Source directory

Uses the existing Git class from src/git_integration.py.
"""

import logging
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)

# Base directory for all BMAD projects
PROJECTS_BASE = Path(__file__).parent.parent / "projects"

# Import Git class
try:
    from src.git_integration import Git, GitCommit, GitError

    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    Git = None
    GitCommit = None
    GitError = Exception


class ProjectManager:
    """
    Manages project folders and Git operations for BMAD sessions.

    Creates projects/{user_id}/{project_name}/ with:
        .git/
        docs/planning-artifacts/
        docs/implementation-artifacts/
        src/
    """

    def __init__(self, base_dir: Path = None):
        self.base_dir = base_dir or PROJECTS_BASE

    def create_project(
        self,
        user_id: str,
        project_name: str,
        project_type: str = "fullstack",
        github_token: str = None,
        github_repo_url: str = None,
    ) -> Path:
        """
        Create a new project folder with git repo.

        Args:
            user_id: User identifier
            project_name: Name of the project (sanitized for filesystem)
            project_type: Type of project for gitignore template
            github_token: Optional GitHub token for remote setup
            github_repo_url: Optional existing GitHub repo URL

        Returns:
            Path to the created project directory
        """
        safe_name = self._sanitize_name(project_name)
        project_path = self.base_dir / user_id / safe_name

        if project_path.exists():
            logger.info(f"Project already exists: {project_path}")
            return project_path

        # Create directory structure
        project_path.mkdir(parents=True, exist_ok=True)
        (project_path / "docs" / "planning-artifacts").mkdir(
            parents=True, exist_ok=True
        )
        (project_path / "docs" / "implementation-artifacts").mkdir(
            parents=True, exist_ok=True
        )
        (project_path / "src").mkdir(parents=True, exist_ok=True)

        # Initialize git
        if GIT_AVAILABLE:
            git = Git(project_path, auto_init=True)

            # Create gitignore
            template = (
                "python" if project_type in ("python", "fastapi", "django") else "node"
            )
            git.create_gitignore(template)

            # Initial commit
            git.add_all()
            git.commit("chore: initialize project structure", allow_empty=True)

            # Set up remote if provided
            if github_repo_url:
                try:
                    if github_token:
                        # Insert token into URL for auth
                        if (
                            "github.com" in github_repo_url
                            and "@" not in github_repo_url
                        ):
                            github_repo_url = github_repo_url.replace(
                                "https://github.com",
                                f"https://{github_token}@github.com",
                            )
                    git.set_remote("origin", github_repo_url)
                    logger.info(f"Set remote origin: {github_repo_url}")
                except Exception as e:
                    logger.warning(f"Failed to set remote: {e}")

        logger.info(f"Created project: {project_path}")
        return project_path

    def get_project_path(self, user_id: str, project_name: str) -> Path:
        """Get the path for a project."""
        safe_name = self._sanitize_name(project_name)
        return self.base_dir / user_id / safe_name

    def write_artifact(
        self,
        user_id: str,
        project_name: str,
        file_path: str,
        content: str,
    ) -> Path:
        """
        Write a file to the project directory.

        Args:
            user_id: User identifier
            project_name: Project name
            file_path: Relative path within project (e.g. "docs/planning-artifacts/prd.md")
            content: File content

        Returns:
            Absolute path to the written file
        """
        project_path = self.get_project_path(user_id, project_name)
        full_path = project_path / file_path

        # Ensure parent directory exists
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

        logger.debug(f"Wrote artifact: {full_path}")
        return full_path

    def commit_phase(
        self,
        user_id: str,
        project_name: str,
        message: str,
        files: List[str] = None,
        push: bool = False,
    ) -> Optional[str]:
        """
        Stage, commit, and optionally push project files.

        Args:
            user_id: User identifier
            project_name: Project name
            message: Commit message
            files: Specific files to stage (None = all)
            push: Whether to push to remote

        Returns:
            Commit SHA or None if nothing to commit
        """
        if not GIT_AVAILABLE:
            logger.warning("Git not available, skipping commit")
            return None

        project_path = self.get_project_path(user_id, project_name)

        if not (project_path / ".git").exists():
            logger.warning(f"No git repo at {project_path}")
            return None

        try:
            git = Git(project_path)

            if files:
                git.add(*files)
            else:
                git.add_all()

            commit = git.commit(message)

            if commit is None:
                logger.debug("Nothing to commit")
                return None

            sha = commit.sha
            logger.info(f"Committed: {sha[:8]} - {message}")

            if push:
                try:
                    git.push()
                except Exception as e:
                    logger.warning(f"Push failed: {e}")

            return sha

        except Exception as e:
            logger.error(f"Commit failed: {e}")
            return None

    def _sanitize_name(self, name: str) -> str:
        """Sanitize project name for filesystem use."""
        safe = name.lower().strip()
        safe = safe.replace(" ", "-")
        # Keep only alphanumeric, hyphens, underscores
        safe = "".join(c for c in safe if c.isalnum() or c in "-_")
        return safe or "unnamed-project"
