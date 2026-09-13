"""
Git Integration
===============
Git operations for version control of generated projects.
"""

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from .config import get_config, GitConfig
from .errors import GitError, get_logger


@dataclass
class GitCommit:
    """Represents a Git commit."""

    sha: str
    message: str
    author: str
    timestamp: datetime
    files_changed: List[str] = field(default_factory=list)


@dataclass
class GitStatus:
    """Represents Git repository status."""

    branch: str
    staged: List[str]
    modified: List[str]
    untracked: List[str]
    ahead: int = 0
    behind: int = 0


class Git:
    """
    Git operations wrapper.

    Usage:
        git = Git("/path/to/project")
        git.init()
        git.add_all()
        git.commit("Initial commit")
        git.push()
    """

    def __init__(
        self, repo_path: Path, config: GitConfig = None, auto_init: bool = False
    ):
        self.repo_path = Path(repo_path)
        self.config = config or get_config().git
        self.logger = get_logger()

        if auto_init and not self.is_repo():
            self.init()

    def _run(
        self, *args: str, capture: bool = True, check: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a git command."""

        cmd = ["git"] + list(args)
        self.logger.debug(f"Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd, cwd=self.repo_path, capture_output=capture, text=True, check=check
            )
            return result
        except subprocess.CalledProcessError as e:
            raise GitError(
                f"Git command failed: {' '.join(args)}",
                operation=args[0] if args else "unknown",
                details={"stderr": e.stderr, "returncode": e.returncode},
            )
        except FileNotFoundError:
            raise GitError("Git is not installed or not in PATH", "init")

    def is_repo(self) -> bool:
        """Check if directory is a Git repository."""
        return (self.repo_path / ".git").exists()

    def init(self, branch: str = "main") -> bool:
        """Initialize a new Git repository."""

        self.repo_path.mkdir(parents=True, exist_ok=True)

        self._run("init", "-b", branch)
        self.logger.info(f"Initialized repository at {self.repo_path}")

        # Configure user if not set globally
        try:
            self._run("config", "user.email")
        except GitError:
            self._run("config", "user.email", "ai-builder@local")
            self._run("config", "user.name", "AI Builder")

        return True

    def clone(self, url: str, branch: str = None) -> bool:
        """Clone a repository."""

        args = ["clone", url, str(self.repo_path)]
        if branch:
            args.extend(["-b", branch])

        subprocess.run(args, check=True)
        self.logger.info(f"Cloned {url} to {self.repo_path}")
        return True

    def status(self) -> GitStatus:
        """Get repository status."""

        # Get branch
        result = self._run("branch", "--show-current")
        branch = result.stdout.strip()

        # Get status
        result = self._run("status", "--porcelain")

        staged = []
        modified = []
        untracked = []

        for line in result.stdout.splitlines():
            if not line:
                continue

            status_code = line[:2]
            filename = line[3:]

            if status_code[0] in "MADRC":
                staged.append(filename)
            if status_code[1] in "MD":
                modified.append(filename)
            if status_code == "??":
                untracked.append(filename)

        return GitStatus(
            branch=branch, staged=staged, modified=modified, untracked=untracked
        )

    def add(self, *files: str) -> bool:
        """Stage files for commit."""

        if not files:
            return False

        self._run("add", *files)
        self.logger.debug(f"Staged: {', '.join(files)}")
        return True

    def add_all(self) -> bool:
        """Stage all changes."""
        self._run("add", "-A")
        return True

    def commit(
        self, message: str, author: str = None, allow_empty: bool = False
    ) -> Optional[GitCommit]:
        """Create a commit."""

        args = ["commit", "-m", message]

        if author:
            args.extend(["--author", author])

        if allow_empty:
            args.append("--allow-empty")

        try:
            self._run(*args)

            # Get commit SHA
            sha_result = self._run("rev-parse", "HEAD")
            sha = sha_result.stdout.strip()

            self.logger.info(f"Created commit: {sha[:8]} - {message}")

            return GitCommit(
                sha=sha,
                message=message,
                author=author or "AI Builder",
                timestamp=datetime.now(),
            )

        except GitError as e:
            if "nothing to commit" in str(e.details.get("stderr", "")):
                self.logger.debug("Nothing to commit")
                return None
            raise

    def push(
        self, remote: str = "origin", branch: str = None, force: bool = False
    ) -> bool:
        """Push commits to remote."""

        args = ["push", remote]

        if branch:
            args.append(branch)

        if force:
            args.append("--force")

        self._run(*args)
        self.logger.info(f"Pushed to {remote}")
        return True

    def pull(self, remote: str = "origin", branch: str = None) -> bool:
        """Pull changes from remote."""

        args = ["pull", remote]
        if branch:
            args.append(branch)

        self._run(*args)
        return True

    def branch(self, name: str = None, delete: bool = False) -> str:
        """Create, list, or delete branches."""

        if delete and name:
            self._run("branch", "-D", name)
            return name

        if name:
            self._run("checkout", "-b", name)
            return name

        result = self._run("branch", "--show-current")
        return result.stdout.strip()

    def checkout(self, ref: str, create: bool = False) -> bool:
        """Checkout a branch or commit."""

        args = ["checkout"]
        if create:
            args.append("-b")
        args.append(ref)

        self._run(*args)
        return True

    def log(self, n: int = 10) -> List[GitCommit]:
        """Get commit history."""

        result = self._run("log", f"-{n}", "--format=%H|%s|%an|%aI")

        commits = []
        for line in result.stdout.splitlines():
            if not line:
                continue

            parts = line.split("|", 3)
            if len(parts) >= 4:
                commits.append(
                    GitCommit(
                        sha=parts[0],
                        message=parts[1],
                        author=parts[2],
                        timestamp=datetime.fromisoformat(parts[3]),
                    )
                )

        return commits

    def diff(self, ref: str = None, staged: bool = False) -> str:
        """Get diff output."""

        args = ["diff"]
        if staged:
            args.append("--staged")
        if ref:
            args.append(ref)

        result = self._run(*args)
        return result.stdout

    def set_remote(self, name: str, url: str) -> bool:
        """Add or update remote."""

        try:
            self._run("remote", "add", name, url)
        except GitError:
            self._run("remote", "set-url", name, url)

        return True

    def create_gitignore(self, template: str = "node") -> bool:
        """Create a .gitignore file."""

        templates = {
            "node": """
# Dependencies
node_modules/
package-lock.json

# Build output
dist/
build/
.next/

# Environment
.env
.env.local

# IDE
.idea/
.vscode/
*.swp

# OS
.DS_Store
Thumbs.db

# Logs
logs/
*.log
""",
            "python": """
# Byte-compiled
__pycache__/
*.py[cod]
*$py.class

# Virtual environments
venv/
.venv/
env/

# Distribution
dist/
build/
*.egg-info/

# Environment
.env
.env.local

# IDE
.idea/
.vscode/
*.swp

# OS
.DS_Store
Thumbs.db

# Cache
.cache/
.pytest_cache/
""",
            "react": """
# Dependencies
node_modules/
.pnp/
.pnp.js

# Build
build/
dist/
.next/
out/

# Testing
coverage/

# Environment
.env
.env.local
.env.development.local
.env.test.local
.env.production.local

# Logs
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# IDE
.idea/
.vscode/

# OS
.DS_Store
""",
        }

        content = templates.get(template, templates["node"])

        gitignore_path = self.repo_path / ".gitignore"
        gitignore_path.write_text(content.strip())

        return True


class GitManager:
    """
    High-level Git management for AI-generated projects.

    Usage:
        manager = GitManager()
        manager.save_project(project, "Created todo app")
    """

    def __init__(self, base_dir: Path = None, config: GitConfig = None):
        self.base_dir = base_dir or Path(get_config().output_dir)
        self.config = config or get_config().git
        self.logger = get_logger()

    def save_project(
        self,
        project: Any,  # GeneratedProject
        message: str,
        project_name: str = None,
        create_branch: bool = True,
    ) -> Dict[str, Any]:
        """
        Save a generated project with Git versioning.

        Args:
            project: GeneratedProject to save
            message: Commit message
            project_name: Optional project name (generates unique name if not provided)
            create_branch: Whether to create a feature branch

        Returns:
            Dict with project info and commit details
        """

        # Generate project name if not provided
        if not project_name:
            project_name = f"project_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        project_path = self.base_dir / project_name

        # Initialize Git
        git = Git(project_path, self.config, auto_init=True)

        # Create gitignore based on project type
        if hasattr(project, "framework") and project.framework:
            template = project.framework.lower()
            if template in ["react", "vue", "angular", "nextjs"]:
                template = "react"
            elif template in ["express", "node", "nodejs"]:
                template = "node"
            else:
                template = "node"
        else:
            template = "node"

        git.create_gitignore(template)

        # Create feature branch if requested
        if create_branch:
            branch_name = f"{self.config.branch_prefix}/{project_name}"
            git.branch(branch_name)

        # Save project files
        project.save(project_path)

        # Stage and commit
        git.add_all()
        commit = git.commit(message)

        result = {
            "project_name": project_name,
            "path": str(project_path),
            "files": list(project.files.keys()) if hasattr(project, "files") else [],
            "commit": commit.to_dict() if commit else None,
            "branch": git.branch(),
        }

        # Push if remote configured
        if self.config.remote_url:
            try:
                git.set_remote("origin", self.config.remote_url)
                git.push()
                result["pushed"] = True
            except GitError as e:
                self.logger.warning(f"Failed to push: {e}")
                result["pushed"] = False

        self.logger.info(f"Project saved: {project_path}")
        return result

    def list_projects(self) -> List[Dict[str, Any]]:
        """List all saved projects."""

        projects = []

        for path in self.base_dir.iterdir():
            if path.is_dir() and (path / ".git").exists():
                git = Git(path)

                try:
                    status = git.status()
                    commits = git.log(n=1)

                    projects.append(
                        {
                            "name": path.name,
                            "path": str(path),
                            "branch": status.branch,
                            "last_commit": commits[0].to_dict() if commits else None,
                            "modified_files": len(status.modified),
                            "untracked_files": len(status.untracked),
                        }
                    )
                except GitError:
                    continue

        return projects

    def get_project_history(self, project_name: str, n: int = 20) -> List[GitCommit]:
        """Get commit history for a project."""

        project_path = self.base_dir / project_name

        if not project_path.exists():
            raise GitError(f"Project not found: {project_name}", "history")

        git = Git(project_path)
        return git.log(n=n)

    def restore_version(self, project_name: str, commit_sha: str) -> bool:
        """Restore a project to a specific version."""

        project_path = self.base_dir / project_name
        git = Git(project_path)

        # Checkout the commit (creates detached HEAD or branch)
        git.checkout(commit_sha, create=True)

        return True

    def export_to_github(
        self, project_name: str, repo_name: str, github_token: str, private: bool = True
    ) -> Dict[str, Any]:
        """
        Export project to a new GitHub repository.

        Args:
            project_name: Local project name
            repo_name: GitHub repository name
            github_token: GitHub personal access token
            private: Whether to create private repo

        Returns:
            Dict with repository info
        """

        import requests

        # Create GitHub repo
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json",
        }

        response = requests.post(
            "https://api.github.com/user/repos",
            headers=headers,
            json={"name": repo_name, "private": private, "auto_init": False},
        )

        if response.status_code not in [200, 201]:
            raise GitError(f"Failed to create GitHub repo: {response.text}", "github")

        repo_data = response.json()

        # Push to GitHub
        project_path = self.base_dir / project_name
        git = Git(project_path)

        remote_url = f"https://{github_token}@github.com/{repo_data['full_name']}.git"
        git.set_remote("github", remote_url)
        git.push(remote="github")

        return {
            "name": repo_data["name"],
            "full_name": repo_data["full_name"],
            "url": repo_data["html_url"],
            "clone_url": repo_data["clone_url"],
            "private": repo_data["private"],
        }
