"""
Tests for Version Control API routes (/api/version-control/*).

Covers all endpoints in api/version_control_routes.py using a mocked
VersionControlService injected via dependency override on get_vc_service.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

try:
    from api.version_control_routes import router as _vc_router, get_vc_service
except ImportError:
    pytest.skip(
        "get_vc_service not implemented in version_control_routes",
        allow_module_level=True,
    )

# Mount the version control router (not mounted in main.py for production yet)
_VC_PREFIX = "/api/version-control"
_mounted_vc = False
for route in app.routes:
    if hasattr(route, "path") and route.path.startswith(_VC_PREFIX):
        _mounted_vc = True
        break
if not _mounted_vc:
    app.include_router(_vc_router, prefix="/api", tags=["VersionControl-Test"])
from tools.version_control import (
    Branch,
    Commit,
    FileChange,
    FileSnapshot,
    FileDiff,
    DiffHunk,
    MergeResult,
    MergeConflict,
)

# =============================================================================
# Helpers — build real dataclass objects so BranchResponse.from_branch() and
# CommitResponse.from_commit() work without AttributeError.
# =============================================================================


def _make_branch(
    name="main",
    project_id="proj1",
    is_default=True,
    description="Main branch",
) -> Branch:
    return Branch(
        name=name,
        project_id=project_id,
        head_commit_id="commit1",
        base_branch=None,
        created_at=datetime.fromisoformat("2024-01-01T00:00:00"),
        created_by="user_123",
        is_default=is_default,
        is_protected=False,
        description=description,
    )


def _make_commit(
    commit_id="commit1",
    project_id="proj1",
    branch="main",
    message="Initial commit",
) -> Commit:
    snapshot = FileSnapshot.from_content("main.py", "print('hello')")
    change = FileChange(path="main.py", change_type="added", additions=1, deletions=0)
    return Commit(
        id=commit_id,
        project_id=project_id,
        branch=branch,
        message=message,
        author_id="user_123",
        author_name="Test User",
        parent_id=None,
        files={"main.py": snapshot},
        changes=[change],
        created_at=datetime.fromisoformat("2024-01-01T00:00:00"),
    )


def _make_file_diff(path="main.py", change_type="modified") -> FileDiff:
    hunk = DiffHunk(
        old_start=1, old_count=1, new_start=1, new_count=1, lines=["- old", "+ new"]
    )
    return FileDiff(
        path=path,
        old_path=None,
        change_type=change_type,
        hunks=[hunk],
        additions=1,
        deletions=1,
    )


def _make_merge_result(success=True) -> MergeResult:
    return MergeResult(
        success=success,
        commit_id="merge1",
        conflicts=[],
        merged_files=["main.py"],
        message="Merged successfully",
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_vc():
    """Create and inject a mock VersionControlService for every test."""
    svc = MagicMock()

    svc.list_branches = AsyncMock(return_value=[_make_branch()])
    svc.create_branch = AsyncMock(return_value=_make_branch())
    svc.get_branch = AsyncMock(return_value=_make_branch())
    svc.delete_branch = AsyncMock(return_value=True)
    svc.switch_branch = AsyncMock(return_value={"main.py": "print('hello')"})
    svc.get_branch_stats = AsyncMock(return_value={"commits": 5, "files_changed": 3})
    svc.create_commit = AsyncMock(return_value=_make_commit())
    svc.get_commit_history = AsyncMock(return_value=[_make_commit()])
    svc.get_commit = AsyncMock(return_value=_make_commit())
    svc.get_file_at_commit = AsyncMock(return_value="print('hello')")
    svc.revert_to_commit = AsyncMock(
        return_value=_make_commit(commit_id="commit2", message="Revert to commit1")
    )
    svc.diff_branches = AsyncMock(return_value=[_make_file_diff()])
    svc.diff_commits = AsyncMock(return_value=[_make_file_diff()])
    svc.diff_file = AsyncMock(return_value=_make_file_diff())
    svc.merge_branches = AsyncMock(return_value=_make_merge_result())
    svc.resolve_conflicts = AsyncMock(
        return_value=_make_commit(commit_id="resolve1", message="Resolved conflicts")
    )

    app.dependency_overrides[get_vc_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_vc_service, None)


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


# =============================================================================
# Health check
# =============================================================================


def test_health_check(client, mock_vc):
    resp = client.get("/api/version-control/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "version-control"
    assert "timestamp" in data


# =============================================================================
# Branch — list
# =============================================================================


def test_list_branches_returns_200(client, mock_vc):
    resp = client.get("/api/version-control/projects/proj1/branches")
    assert resp.status_code == 200
    branches = resp.json()
    assert isinstance(branches, list)
    assert len(branches) == 1
    mock_vc.list_branches.assert_called_once_with("proj1")


def test_list_branches_fields(client, mock_vc):
    resp = client.get("/api/version-control/projects/proj1/branches")
    branch = resp.json()[0]
    assert branch["name"] == "main"
    assert branch["project_id"] == "proj1"
    assert branch["is_default"] is True


# =============================================================================
# Branch — create
# =============================================================================


def test_create_branch_success(client, mock_vc):
    resp = client.post(
        "/api/version-control/projects/proj1/branches",
        json={"name": "feature-x", "description": "New feature"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "main"  # mock returns _make_branch() which has name="main"
    mock_vc.create_branch.assert_called_once()


def test_create_branch_missing_name_returns_422(client, mock_vc):
    resp = client.post(
        "/api/version-control/projects/proj1/branches",
        json={"description": "No name provided"},
    )
    assert resp.status_code == 422


def test_create_branch_service_value_error_returns_400(client, mock_vc):
    mock_vc.create_branch.side_effect = ValueError("Branch already exists")
    resp = client.post(
        "/api/version-control/projects/proj1/branches",
        json={"name": "main"},
    )
    assert resp.status_code == 400
    assert "Branch already exists" in resp.json()["detail"]


# =============================================================================
# Branch — get single
# =============================================================================


def test_get_branch_found(client, mock_vc):
    resp = client.get("/api/version-control/projects/proj1/branches/main")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "main"
    mock_vc.get_branch.assert_called_once_with("proj1", "main")


def test_get_branch_not_found_returns_404(client, mock_vc):
    mock_vc.get_branch.return_value = None
    resp = client.get("/api/version-control/projects/proj1/branches/nonexistent")
    assert resp.status_code == 404
    assert "Branch not found" in resp.json()["detail"]


# =============================================================================
# Branch — delete
# =============================================================================


def test_delete_branch_success(client, mock_vc):
    resp = client.delete("/api/version-control/projects/proj1/branches/feature-x")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "feature-x" in data["message"]


def test_delete_branch_not_found_returns_404(client, mock_vc):
    mock_vc.delete_branch.return_value = False
    resp = client.delete("/api/version-control/projects/proj1/branches/ghost")
    assert resp.status_code == 404


def test_delete_branch_protected_returns_400(client, mock_vc):
    mock_vc.delete_branch.side_effect = ValueError("Cannot delete protected branch")
    resp = client.delete("/api/version-control/projects/proj1/branches/main")
    assert resp.status_code == 400


# =============================================================================
# Branch — switch
# =============================================================================


def test_switch_branch_success(client, mock_vc):
    resp = client.post("/api/version-control/projects/proj1/branches/main/switch")
    assert resp.status_code == 200
    data = resp.json()
    assert data["branch"] == "main"
    assert "files" in data
    assert "file_count" in data
    assert data["file_count"] == 1


def test_switch_branch_not_found_returns_404(client, mock_vc):
    mock_vc.switch_branch.return_value = None
    resp = client.post("/api/version-control/projects/proj1/branches/ghost/switch")
    assert resp.status_code == 404


# =============================================================================
# Branch — stats
# =============================================================================


def test_get_branch_stats(client, mock_vc):
    resp = client.get("/api/version-control/projects/proj1/branches/main/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["branch"] == "main"
    assert data["commits"] == 5
    assert data["files_changed"] == 3
    mock_vc.get_branch_stats.assert_called_once_with("proj1", "main")


# =============================================================================
# Commits — create
# =============================================================================


def test_create_commit_success(client, mock_vc):
    resp = client.post(
        "/api/version-control/projects/proj1/commits",
        json={
            "branch": "main",
            "message": "Fix bug",
            "files": {"main.py": "print('fixed')"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "commit1"
    assert data["branch"] == "main"
    mock_vc.create_commit.assert_called_once()


def test_create_commit_missing_message_returns_422(client, mock_vc):
    resp = client.post(
        "/api/version-control/projects/proj1/commits",
        json={"branch": "main", "files": {}},
    )
    assert resp.status_code == 422


def test_create_commit_service_error_returns_400(client, mock_vc):
    mock_vc.create_commit.side_effect = ValueError("Branch does not exist")
    resp = client.post(
        "/api/version-control/projects/proj1/commits",
        json={"branch": "ghost", "message": "fail", "files": {}},
    )
    assert resp.status_code == 400


# =============================================================================
# Commits — history
# =============================================================================


def test_get_commit_history(client, mock_vc):
    resp = client.get("/api/version-control/projects/proj1/commits?branch=main")
    assert resp.status_code == 200
    commits = resp.json()
    assert isinstance(commits, list)
    assert len(commits) == 1
    assert commits[0]["message"] == "Initial commit"
    mock_vc.get_commit_history.assert_called_once()


# =============================================================================
# Commits — get single
# =============================================================================


def test_get_commit_found(client, mock_vc):
    resp = client.get("/api/version-control/commits/commit1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "commit1"
    assert data["author_id"] == "user_123"
    mock_vc.get_commit.assert_called_once_with("commit1")


def test_get_commit_not_found_returns_404(client, mock_vc):
    mock_vc.get_commit.return_value = None
    resp = client.get("/api/version-control/commits/nonexistent")
    assert resp.status_code == 404
    assert "Commit not found" in resp.json()["detail"]


# =============================================================================
# Commits — file at commit
# =============================================================================


def test_get_file_at_commit_found(client, mock_vc):
    resp = client.get("/api/version-control/commits/commit1/files/main.py")
    assert resp.status_code == 200
    data = resp.json()
    assert data["commit_id"] == "commit1"
    assert data["path"] == "main.py"
    assert data["content"] == "print('hello')"
    mock_vc.get_file_at_commit.assert_called_once_with("commit1", "main.py")


def test_get_file_at_commit_not_found_returns_404(client, mock_vc):
    mock_vc.get_file_at_commit.return_value = None
    resp = client.get("/api/version-control/commits/commit1/files/missing.py")
    assert resp.status_code == 404
    assert "File not found" in resp.json()["detail"]


# =============================================================================
# Revert
# =============================================================================


def test_revert_to_commit_success(client, mock_vc):
    resp = client.post(
        "/api/version-control/projects/proj1/revert",
        json={"branch": "main", "commit_id": "commit1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "id" in data
    mock_vc.revert_to_commit.assert_called_once()


def test_revert_to_commit_error_returns_400(client, mock_vc):
    mock_vc.revert_to_commit.side_effect = ValueError("Commit not found on branch")
    resp = client.post(
        "/api/version-control/projects/proj1/revert",
        json={"branch": "main", "commit_id": "bad"},
    )
    assert resp.status_code == 400


# =============================================================================
# Diff — branches
# =============================================================================


def test_diff_branches(client, mock_vc):
    resp = client.get(
        "/api/version-control/projects/proj1/diff/branches?source=feature&target=main"
    )
    assert resp.status_code == 200
    diffs = resp.json()
    assert isinstance(diffs, list)
    assert diffs[0]["path"] == "main.py"
    assert diffs[0]["change_type"] == "modified"
    mock_vc.diff_branches.assert_called_once_with("proj1", "feature", "main")


def test_diff_branches_service_error_returns_400(client, mock_vc):
    mock_vc.diff_branches.side_effect = ValueError("Branch not found")
    resp = client.get(
        "/api/version-control/projects/proj1/diff/branches?source=ghost&target=main"
    )
    assert resp.status_code == 400


# =============================================================================
# Diff — commits
# =============================================================================


def test_diff_commits(client, mock_vc):
    resp = client.get(
        "/api/version-control/diff/commits?commit_a=commit1&commit_b=commit2"
    )
    assert resp.status_code == 200
    diffs = resp.json()
    assert isinstance(diffs, list)
    assert len(diffs) == 1
    mock_vc.diff_commits.assert_called_once_with("commit1", "commit2")


def test_diff_commits_missing_params_returns_422(client, mock_vc):
    resp = client.get("/api/version-control/diff/commits?commit_a=only_one")
    assert resp.status_code == 422


# =============================================================================
# Diff — single file
# =============================================================================


def test_diff_file_found(client, mock_vc):
    resp = client.get(
        "/api/version-control/projects/proj1/diff/file?branch=main&path=main.py"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "main.py"
    assert "hunks" in data
    mock_vc.diff_file.assert_called_once_with("proj1", "main", "main.py", None)


def test_diff_file_not_found_returns_404(client, mock_vc):
    mock_vc.diff_file.return_value = None
    resp = client.get(
        "/api/version-control/projects/proj1/diff/file?branch=main&path=missing.py"
    )
    assert resp.status_code == 404


# =============================================================================
# Merge
# =============================================================================


def test_merge_branches_success(client, mock_vc):
    resp = client.post(
        "/api/version-control/projects/proj1/merge",
        json={"source_branch": "feature", "target_branch": "main"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["commit_id"] == "merge1"
    assert data["conflicts"] == []
    mock_vc.merge_branches.assert_called_once()


def test_merge_branches_with_conflicts(client, mock_vc):
    conflict = MergeConflict(
        path="main.py",
        ours="our content",
        theirs="their content",
        base=None,
        conflict_markers="<<<<<<< ours\nour content\n=======\ntheir content\n>>>>>>> theirs",
    )
    mock_vc.merge_branches.return_value = MergeResult(
        success=False,
        commit_id=None,
        conflicts=[conflict],
        merged_files=[],
        message="Conflicts detected",
    )
    resp = client.post(
        "/api/version-control/projects/proj1/merge",
        json={"source_branch": "feature", "target_branch": "main"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert len(data["conflicts"]) == 1
    assert data["conflicts"][0]["path"] == "main.py"


# =============================================================================
# Resolve conflicts
# =============================================================================


def test_resolve_conflicts_success(client, mock_vc):
    resp = client.post(
        "/api/version-control/projects/proj1/resolve-conflicts",
        json={
            "branch": "main",
            "resolutions": {"main.py": "resolved content"},
            "message": "Resolved",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "resolve1"
    mock_vc.resolve_conflicts.assert_called_once()


def test_resolve_conflicts_service_error_returns_400(client, mock_vc):
    mock_vc.resolve_conflicts.side_effect = ValueError("No conflicts to resolve")
    resp = client.post(
        "/api/version-control/projects/proj1/resolve-conflicts",
        json={
            "branch": "main",
            "resolutions": {},
            "message": "Oops",
        },
    )
    assert resp.status_code == 400
