"""
Version Control Service

Git-like version control for AI App Builder projects:
- Branches (create, switch, delete, list)
- Commits (create, view history, revert)
- Diffs (compare branches, commits)
- Merge (with conflict detection)
- File snapshots and restoration

Dependencies:
- difflib (standard library)
- hashlib (standard library)
"""

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import unified_diff, SequenceMatcher
from enum import Enum
from typing import Optional
from uuid import uuid4


class MergeStrategy(str, Enum):
    """Merge strategies."""

    FAST_FORWARD = "fast_forward"
    THREE_WAY = "three_way"
    SQUASH = "squash"


class ConflictResolution(str, Enum):
    """Conflict resolution strategies."""

    OURS = "ours"
    THEIRS = "theirs"
    MANUAL = "manual"


@dataclass
class FileChange:
    """Represents a file change."""

    path: str
    change_type: str  # "added", "modified", "deleted", "renamed"
    old_path: Optional[str] = None  # For renames
    additions: int = 0
    deletions: int = 0


@dataclass
class FileSnapshot:
    """Snapshot of a file at a point in time."""

    path: str
    content: str
    hash: str
    size: int
    language: Optional[str] = None

    @classmethod
    def from_content(cls, path: str, content: str) -> "FileSnapshot":
        """Create snapshot from content."""
        return cls(
            path=path,
            content=content,
            hash=hashlib.sha256(content.encode()).hexdigest()[:12],
            size=len(content.encode()),
            language=cls._detect_language(path),
        )

    @staticmethod
    def _detect_language(path: str) -> Optional[str]:
        """Detect language from file extension."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".jsx": "javascriptreact",
            ".html": "html",
            ".css": "css",
            ".json": "json",
            ".md": "markdown",
            ".sql": "sql",
            ".sh": "bash",
            ".yml": "yaml",
            ".yaml": "yaml",
        }
        ext = os.path.splitext(path)[1].lower()
        return ext_map.get(ext)


@dataclass
class Commit:
    """Represents a commit."""

    id: str
    project_id: str
    branch: str
    message: str
    author_id: str
    author_name: str
    parent_id: Optional[str]
    files: dict[str, FileSnapshot]  # path -> snapshot
    changes: list[FileChange]
    created_at: datetime
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "branch": self.branch,
            "message": self.message,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "parent_id": self.parent_id,
            "files": {
                k: {"path": v.path, "hash": v.hash, "size": v.size}
                for k, v in self.files.items()
            },
            "changes": [
                {
                    "path": c.path,
                    "type": c.change_type,
                    "additions": c.additions,
                    "deletions": c.deletions,
                }
                for c in self.changes
            ],
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class Branch:
    """Represents a branch."""

    name: str
    project_id: str
    head_commit_id: Optional[str]
    base_branch: Optional[str]  # Branch it was created from
    created_at: datetime
    created_by: str
    is_default: bool = False
    is_protected: bool = False
    description: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "project_id": self.project_id,
            "head_commit_id": self.head_commit_id,
            "base_branch": self.base_branch,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "is_default": self.is_default,
            "is_protected": self.is_protected,
            "description": self.description,
        }


@dataclass
class DiffHunk:
    """A diff hunk."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


@dataclass
class FileDiff:
    """Diff for a single file."""

    path: str
    old_path: Optional[str]  # For renames
    change_type: str
    hunks: list[DiffHunk]
    additions: int
    deletions: int
    old_content: Optional[str] = None
    new_content: Optional[str] = None


@dataclass
class MergeConflict:
    """Represents a merge conflict."""

    path: str
    ours: str
    theirs: str
    base: Optional[str]
    conflict_markers: str  # Content with conflict markers


@dataclass
class MergeResult:
    """Result of a merge operation."""

    success: bool
    commit_id: Optional[str]
    conflicts: list[MergeConflict]
    merged_files: list[str]
    message: str


class VersionControlService:
    """
    Git-like version control for projects.

    Features:
    - Branch management
    - Commit history
    - File diffing
    - Merge with conflict detection
    """

    def __init__(self):
        # In-memory cache
        self._commits: dict[str, Commit] = {}
        self._branches: dict[str, Branch] = {}
        self._file_contents: dict[str, str] = {}  # hash -> content

    # ==========================================
    # Branch Operations
    # ==========================================

    async def create_branch(
        self,
        project_id: str,
        name: str,
        base_branch: str,
        user_id: str,
        description: Optional[str] = None,
    ) -> Branch:
        """Create a new branch from an existing branch."""
        # Validate branch name
        if not self._validate_branch_name(name):
            raise ValueError(f"Invalid branch name: {name}")

        # Check if branch already exists
        existing = await self.get_branch(project_id, name)
        if existing:
            raise ValueError(f"Branch '{name}' already exists")

        # Get base branch
        base = await self.get_branch(project_id, base_branch)
        if not base:
            raise ValueError(f"Base branch '{base_branch}' not found")

        branch = Branch(
            name=name,
            project_id=project_id,
            head_commit_id=base.head_commit_id,
            base_branch=base_branch,
            created_at=datetime.now(timezone.utc),
            created_by=user_id,
            description=description,
        )

        # Store branch
        await self._store_branch(branch)

        return branch

    async def get_branch(self, project_id: str, name: str) -> Optional[Branch]:
        """Get a branch by name."""
        key = f"{project_id}:{name}"
        return self._branches.get(key)

    async def list_branches(self, project_id: str) -> list[Branch]:
        """List all branches for a project."""
        return [b for b in self._branches.values() if b.project_id == project_id]

    async def delete_branch(
        self,
        project_id: str,
        name: str,
        user_id: str,
    ) -> bool:
        """Delete a branch (cannot delete default or protected)."""
        branch = await self.get_branch(project_id, name)
        if not branch:
            return False

        if branch.is_default:
            raise ValueError("Cannot delete default branch")
        if branch.is_protected:
            raise ValueError("Cannot delete protected branch")

        key = f"{project_id}:{name}"
        del self._branches[key]
        return True

    async def switch_branch(
        self,
        project_id: str,
        name: str,
    ) -> Optional[dict[str, str]]:
        """
        Switch to a branch and return its files.
        Returns dict of path -> content.
        """
        branch = await self.get_branch(project_id, name)
        if not branch:
            return None

        if not branch.head_commit_id:
            return {}

        commit = await self.get_commit(branch.head_commit_id)
        if not commit:
            return {}

        # Return file contents
        return {
            path: self._file_contents.get(snap.hash, snap.content)
            for path, snap in commit.files.items()
        }

    def _validate_branch_name(self, name: str) -> bool:
        """Validate branch name."""
        if not name or len(name) > 100:
            return False
        # Allow alphanumeric, dash, underscore, slash
        import re

        return bool(re.match(r"^[a-zA-Z0-9_/-]+$", name))

    async def _store_branch(self, branch: Branch):
        """Store branch (in-memory or DB)."""
        key = f"{branch.project_id}:{branch.name}"
        self._branches[key] = branch

    # ==========================================
    # Commit Operations
    # ==========================================

    async def create_commit(
        self,
        project_id: str,
        branch: str,
        message: str,
        files: dict[str, str],  # path -> content
        author_id: str,
        author_name: str,
        metadata: Optional[dict] = None,
    ) -> Commit:
        """Create a new commit."""
        # Get current branch
        branch_obj = await self.get_branch(project_id, branch)
        if not branch_obj:
            # Create default branch if not exists
            branch_obj = Branch(
                name=branch,
                project_id=project_id,
                head_commit_id=None,
                base_branch=None,
                created_at=datetime.now(timezone.utc),
                created_by=author_id,
                is_default=(branch == "main"),
            )
            await self._store_branch(branch_obj)

        # Get parent commit
        parent_commit = None
        if branch_obj.head_commit_id:
            parent_commit = await self.get_commit(branch_obj.head_commit_id)

        # Create file snapshots
        file_snapshots = {}
        for path, content in files.items():
            snapshot = FileSnapshot.from_content(path, content)
            file_snapshots[path] = snapshot
            self._file_contents[snapshot.hash] = content

        # Calculate changes
        changes = self._calculate_changes(
            parent_commit.files if parent_commit else {},
            file_snapshots,
        )

        # Create commit
        commit_id = f"c{uuid4().hex[:11]}"
        commit = Commit(
            id=commit_id,
            project_id=project_id,
            branch=branch,
            message=message,
            author_id=author_id,
            author_name=author_name,
            parent_id=branch_obj.head_commit_id,
            files=file_snapshots,
            changes=changes,
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        # Store commit
        self._commits[commit_id] = commit

        # Update branch head
        branch_obj.head_commit_id = commit_id
        await self._store_branch(branch_obj)

        return commit

    async def get_commit(self, commit_id: str) -> Optional[Commit]:
        """Get a commit by ID."""
        return self._commits.get(commit_id)

    async def get_commit_history(
        self,
        project_id: str,
        branch: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Commit]:
        """Get commit history for a branch."""
        branch_obj = await self.get_branch(project_id, branch)
        if not branch_obj or not branch_obj.head_commit_id:
            return []

        # Walk back through commits
        history = []
        current_id = branch_obj.head_commit_id
        skip = offset

        while current_id and len(history) < limit:
            commit = await self.get_commit(current_id)
            if not commit:
                break

            if skip > 0:
                skip -= 1
            else:
                history.append(commit)

            current_id = commit.parent_id

        return history

    async def revert_to_commit(
        self,
        project_id: str,
        branch: str,
        commit_id: str,
        author_id: str,
        author_name: str,
    ) -> Commit:
        """Revert to a specific commit."""
        target = await self.get_commit(commit_id)
        if not target:
            raise ValueError(f"Commit {commit_id} not found")

        # Get file contents from target commit
        files = {
            path: self._file_contents.get(snap.hash, snap.content)
            for path, snap in target.files.items()
        }

        # Create revert commit
        return await self.create_commit(
            project_id=project_id,
            branch=branch,
            message=f"Revert to {commit_id[:8]}: {target.message}",
            files=files,
            author_id=author_id,
            author_name=author_name,
            metadata={"revert_of": commit_id},
        )

    def _calculate_changes(
        self,
        old_files: dict[str, FileSnapshot],
        new_files: dict[str, FileSnapshot],
    ) -> list[FileChange]:
        """Calculate changes between two file sets."""
        changes = []

        all_paths = set(old_files.keys()) | set(new_files.keys())

        for path in all_paths:
            old = old_files.get(path)
            new = new_files.get(path)

            if not old and new:
                # Added
                changes.append(
                    FileChange(
                        path=path,
                        change_type="added",
                        additions=new.content.count("\n") + 1,
                    )
                )
            elif old and not new:
                # Deleted
                old_content = self._file_contents.get(old.hash, "")
                changes.append(
                    FileChange(
                        path=path,
                        change_type="deleted",
                        deletions=old_content.count("\n") + 1,
                    )
                )
            elif old and new and old.hash != new.hash:
                # Modified
                old_content = self._file_contents.get(old.hash, "")
                new_content = self._file_contents.get(new.hash, new.content)
                adds, dels = self._count_diff_lines(old_content, new_content)
                changes.append(
                    FileChange(
                        path=path,
                        change_type="modified",
                        additions=adds,
                        deletions=dels,
                    )
                )

        return changes

    def _count_diff_lines(self, old: str, new: str) -> tuple[int, int]:
        """Count additions and deletions."""
        old_lines = old.splitlines()
        new_lines = new.splitlines()

        matcher = SequenceMatcher(None, old_lines, new_lines)
        additions = 0
        deletions = 0

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "delete":
                deletions += i2 - i1
            elif tag == "insert":
                additions += j2 - j1
            elif tag == "replace":
                deletions += i2 - i1
                additions += j2 - j1

        return additions, deletions

    # ==========================================
    # Diff Operations
    # ==========================================

    async def diff_commits(
        self,
        commit_a_id: str,
        commit_b_id: str,
    ) -> list[FileDiff]:
        """Generate diff between two commits."""
        commit_a = await self.get_commit(commit_a_id)
        commit_b = await self.get_commit(commit_b_id)

        if not commit_a or not commit_b:
            raise ValueError("One or both commits not found")

        return self._generate_diff(commit_a.files, commit_b.files)

    async def diff_branches(
        self,
        project_id: str,
        branch_a: str,
        branch_b: str,
    ) -> list[FileDiff]:
        """Generate diff between two branches."""
        branch_a_obj = await self.get_branch(project_id, branch_a)
        branch_b_obj = await self.get_branch(project_id, branch_b)

        if not branch_a_obj or not branch_b_obj:
            raise ValueError("One or both branches not found")

        files_a = {}
        files_b = {}

        if branch_a_obj.head_commit_id:
            commit_a = await self.get_commit(branch_a_obj.head_commit_id)
            if commit_a:
                files_a = commit_a.files

        if branch_b_obj.head_commit_id:
            commit_b = await self.get_commit(branch_b_obj.head_commit_id)
            if commit_b:
                files_b = commit_b.files

        return self._generate_diff(files_a, files_b)

    async def diff_file(
        self,
        project_id: str,
        branch: str,
        path: str,
        commit_id: Optional[str] = None,
    ) -> Optional[FileDiff]:
        """Get diff for a specific file."""
        branch_obj = await self.get_branch(project_id, branch)
        if not branch_obj or not branch_obj.head_commit_id:
            return None

        current = await self.get_commit(branch_obj.head_commit_id)
        if not current:
            return None

        compare_to = None
        if commit_id:
            compare_to = await self.get_commit(commit_id)
        elif current.parent_id:
            compare_to = await self.get_commit(current.parent_id)

        current_file = current.files.get(path)
        compare_file = compare_to.files.get(path) if compare_to else None

        if not current_file and not compare_file:
            return None

        return self._diff_single_file(path, compare_file, current_file)

    def _generate_diff(
        self,
        old_files: dict[str, FileSnapshot],
        new_files: dict[str, FileSnapshot],
    ) -> list[FileDiff]:
        """Generate diffs for all changed files."""
        diffs = []
        all_paths = set(old_files.keys()) | set(new_files.keys())

        for path in sorted(all_paths):
            old = old_files.get(path)
            new = new_files.get(path)

            if old and new and old.hash == new.hash:
                continue  # No change

            diff = self._diff_single_file(path, old, new)
            if diff:
                diffs.append(diff)

        return diffs

    def _diff_single_file(
        self,
        path: str,
        old: Optional[FileSnapshot],
        new: Optional[FileSnapshot],
    ) -> FileDiff:
        """Generate diff for a single file."""
        old_content = self._file_contents.get(old.hash, "") if old else ""
        new_content = (
            self._file_contents.get(new.hash, new.content if new else "") if new else ""
        )

        # Determine change type
        if not old:
            change_type = "added"
        elif not new:
            change_type = "deleted"
        else:
            change_type = "modified"

        # Generate unified diff
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diff_lines = list(
            unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )

        # Parse hunks
        hunks = self._parse_diff_hunks(diff_lines)

        # Count additions/deletions
        additions = sum(
            1
            for line in diff_lines
            if line.startswith("+") and not line.startswith("+++")
        )
        deletions = sum(
            1
            for line in diff_lines
            if line.startswith("-") and not line.startswith("---")
        )

        return FileDiff(
            path=path,
            old_path=None,
            change_type=change_type,
            hunks=hunks,
            additions=additions,
            deletions=deletions,
            old_content=old_content if change_type != "added" else None,
            new_content=new_content if change_type != "deleted" else None,
        )

    def _parse_diff_hunks(self, diff_lines: list[str]) -> list[DiffHunk]:
        """Parse diff output into hunks."""
        hunks = []
        current_hunk = None

        import re

        hunk_header = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

        for line in diff_lines:
            match = hunk_header.match(line)
            if match:
                if current_hunk:
                    hunks.append(current_hunk)

                current_hunk = DiffHunk(
                    old_start=int(match.group(1)),
                    old_count=int(match.group(2) or 1),
                    new_start=int(match.group(3)),
                    new_count=int(match.group(4) or 1),
                    lines=[line],
                )
            elif current_hunk:
                current_hunk.lines.append(line)

        if current_hunk:
            hunks.append(current_hunk)

        return hunks

    # ==========================================
    # Merge Operations
    # ==========================================

    async def merge_branches(
        self,
        project_id: str,
        source_branch: str,
        target_branch: str,
        author_id: str,
        author_name: str,
        strategy: MergeStrategy = MergeStrategy.THREE_WAY,
        squash: bool = False,
    ) -> MergeResult:
        """Merge source branch into target branch."""
        source = await self.get_branch(project_id, source_branch)
        target = await self.get_branch(project_id, target_branch)

        if not source or not target:
            return MergeResult(
                success=False,
                commit_id=None,
                conflicts=[],
                merged_files=[],
                message="Branch not found",
            )

        # Get commits
        source_commit = (
            await self.get_commit(source.head_commit_id)
            if source.head_commit_id
            else None
        )
        target_commit = (
            await self.get_commit(target.head_commit_id)
            if target.head_commit_id
            else None
        )

        if not source_commit:
            return MergeResult(
                success=False,
                commit_id=None,
                conflicts=[],
                merged_files=[],
                message="Source branch has no commits",
            )

        # Find common ancestor (simplified - in real impl would walk commit graph)
        base_commit = await self._find_common_ancestor(source_commit, target_commit)

        # Get file contents
        source_files = {
            p: self._file_contents.get(s.hash, s.content)
            for p, s in source_commit.files.items()
        }
        target_files = {
            p: self._file_contents.get(s.hash, s.content)
            for p, s in (target_commit.files if target_commit else {}).items()
        }
        base_files = {
            p: self._file_contents.get(s.hash, s.content)
            for p, s in (base_commit.files if base_commit else {}).items()
        }

        # Perform merge
        merged_files, conflicts = self._three_way_merge(
            base_files, source_files, target_files
        )

        if conflicts:
            return MergeResult(
                success=False,
                commit_id=None,
                conflicts=conflicts,
                merged_files=list(merged_files.keys()),
                message=f"Merge conflict in {len(conflicts)} file(s)",
            )

        # Create merge commit
        message = f"Merge branch '{source_branch}' into {target_branch}"
        if squash:
            message = f"Squash merge '{source_branch}' into {target_branch}"

        commit = await self.create_commit(
            project_id=project_id,
            branch=target_branch,
            message=message,
            files=merged_files,
            author_id=author_id,
            author_name=author_name,
            metadata={
                "merge": True,
                "source_branch": source_branch,
                "source_commit": source.head_commit_id,
                "strategy": strategy.value,
            },
        )

        return MergeResult(
            success=True,
            commit_id=commit.id,
            conflicts=[],
            merged_files=list(merged_files.keys()),
            message=f"Successfully merged {source_branch} into {target_branch}",
        )

    async def _find_common_ancestor(
        self,
        commit_a: Optional[Commit],
        commit_b: Optional[Commit],
    ) -> Optional[Commit]:
        """Find common ancestor of two commits (simplified)."""
        if not commit_a or not commit_b:
            return None

        # Walk back from both commits to find common ancestor
        ancestors_a = set()
        current = commit_a
        while current:
            ancestors_a.add(current.id)
            current = (
                await self.get_commit(current.parent_id) if current.parent_id else None
            )

        current = commit_b
        while current:
            if current.id in ancestors_a:
                return current
            current = (
                await self.get_commit(current.parent_id) if current.parent_id else None
            )

        return None

    def _three_way_merge(
        self,
        base: dict[str, str],
        ours: dict[str, str],
        theirs: dict[str, str],
    ) -> tuple[dict[str, str], list[MergeConflict]]:
        """Perform three-way merge."""
        merged = {}
        conflicts = []

        all_paths = set(base.keys()) | set(ours.keys()) | set(theirs.keys())

        for path in all_paths:
            base_content = base.get(path, "")
            ours_content = ours.get(path, "")
            theirs_content = theirs.get(path, "")

            # Simple cases
            if ours_content == theirs_content:
                # Both same - use either
                if ours_content:
                    merged[path] = ours_content
                continue

            if base_content == ours_content:
                # Only theirs changed
                if theirs_content:
                    merged[path] = theirs_content
                continue

            if base_content == theirs_content:
                # Only ours changed
                if ours_content:
                    merged[path] = ours_content
                continue

            # Both changed differently - attempt line-by-line merge
            merged_content, has_conflict = self._merge_content(
                base_content, ours_content, theirs_content
            )

            if has_conflict:
                conflicts.append(
                    MergeConflict(
                        path=path,
                        ours=ours_content,
                        theirs=theirs_content,
                        base=base_content if base_content else None,
                        conflict_markers=merged_content,
                    )
                )
            else:
                merged[path] = merged_content

        return merged, conflicts

    def _merge_content(
        self,
        base: str,
        ours: str,
        theirs: str,
    ) -> tuple[str, bool]:
        """Attempt to merge content, returns (result, has_conflict)."""
        base_lines = base.splitlines(keepends=True)
        ours_lines = ours.splitlines(keepends=True)
        theirs_lines = theirs.splitlines(keepends=True)

        # Simple approach: if changes don't overlap, merge; otherwise conflict
        # For a real implementation, use diff3 algorithm

        matcher_ours = SequenceMatcher(None, base_lines, ours_lines)
        matcher_theirs = SequenceMatcher(None, base_lines, theirs_lines)

        ours_changes = [
            (i1, i2)
            for tag, i1, i2, j1, j2 in matcher_ours.get_opcodes()
            if tag != "equal"
        ]
        theirs_changes = [
            (i1, i2)
            for tag, i1, i2, j1, j2 in matcher_theirs.get_opcodes()
            if tag != "equal"
        ]

        # Check for overlapping changes
        for o_start, o_end in ours_changes:
            for t_start, t_end in theirs_changes:
                if not (o_end <= t_start or t_end <= o_start):
                    # Overlapping - create conflict markers
                    conflict_content = (
                        "<<<<<<< ours\n"
                        + ours
                        + "=======\n"
                        + theirs
                        + ">>>>>>> theirs\n"
                    )
                    return conflict_content, True

        # Non-overlapping changes - merge (simplified: just use ours)
        return ours, False

    async def resolve_conflicts(
        self,
        project_id: str,
        branch: str,
        resolutions: dict[str, str],  # path -> resolved content
        author_id: str,
        author_name: str,
        message: str = "Resolve merge conflicts",
    ) -> Commit:
        """Create commit with conflict resolutions."""
        branch_obj = await self.get_branch(project_id, branch)
        if not branch_obj or not branch_obj.head_commit_id:
            raise ValueError("Branch not found")

        # Get current files
        current = await self.get_commit(branch_obj.head_commit_id)
        files = {
            path: self._file_contents.get(snap.hash, snap.content)
            for path, snap in current.files.items()
        }

        # Apply resolutions
        files.update(resolutions)

        return await self.create_commit(
            project_id=project_id,
            branch=branch,
            message=message,
            files=files,
            author_id=author_id,
            author_name=author_name,
            metadata={"conflict_resolution": True},
        )

    # ==========================================
    # Utility Methods
    # ==========================================

    async def get_file_at_commit(
        self,
        commit_id: str,
        path: str,
    ) -> Optional[str]:
        """Get file content at a specific commit."""
        commit = await self.get_commit(commit_id)
        if not commit:
            return None

        snapshot = commit.files.get(path)
        if not snapshot:
            return None

        return self._file_contents.get(snapshot.hash, snapshot.content)

    async def get_branch_stats(
        self,
        project_id: str,
        branch: str,
    ) -> dict:
        """Get statistics for a branch."""
        history = await self.get_commit_history(project_id, branch, limit=1000)

        if not history:
            return {
                "commits": 0,
                "files": 0,
                "authors": [],
                "first_commit": None,
                "last_commit": None,
            }

        authors = set()
        for commit in history:
            authors.add(commit.author_name)

        latest = history[0]
        oldest = history[-1]

        return {
            "commits": len(history),
            "files": len(latest.files),
            "authors": list(authors),
            "first_commit": oldest.created_at.isoformat(),
            "last_commit": latest.created_at.isoformat(),
        }


# Database schema (reference)
VERSION_CONTROL_SCHEMA = """
-- Branches table
CREATE TABLE IF NOT EXISTS branches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    head_commit_id TEXT,
    base_branch TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    is_protected BOOLEAN DEFAULT FALSE,
    description TEXT,
    
    UNIQUE(project_id, name)
);

CREATE INDEX idx_branches_project ON branches(project_id);

-- Commits table
CREATE TABLE IF NOT EXISTS commits (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    branch TEXT NOT NULL,
    message TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    parent_id TEXT,
    files JSONB NOT NULL,
    changes JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX idx_commits_project_branch ON commits(project_id, branch, created_at DESC);
CREATE INDEX idx_commits_parent ON commits(parent_id);

-- File snapshots table (content-addressable storage)
CREATE TABLE IF NOT EXISTS file_snapshots (
    hash TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    size INTEGER NOT NULL,
    language TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- RLS Policies
ALTER TABLE branches ENABLE ROW LEVEL SECURITY;
ALTER TABLE commits ENABLE ROW LEVEL SECURITY;
ALTER TABLE file_snapshots ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Project members can access branches"
ON branches FOR ALL
USING (true);

CREATE POLICY "Project members can access commits"
ON commits FOR ALL
USING (true);

CREATE POLICY "Anyone can read file snapshots"
ON file_snapshots FOR SELECT
USING (true);
"""


__all__ = [
    "VersionControlService",
    "Branch",
    "Commit",
    "FileDiff",
    "FileChange",
    "MergeResult",
    "MergeConflict",
    "MergeStrategy",
    "VERSION_CONTROL_SCHEMA",
]
