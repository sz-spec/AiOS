"""
API tests for github_sync_routes — GitHub sync and version control.

Router:  api/github_sync_routes.py  (prefix "/github", mounted at "/api")
Service: GitHubSyncService (tools.github_sync_enhanced)
         injected via Depends(get_sync_service) defined in routes file.

The dependency is overridden via app.dependency_overrides so no real
GitHub token is needed.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app
from api.github_sync_routes import get_sync_service

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def mock_github():
    svc = MagicMock()

    # Sync result objects — routes call SyncResultResponse(...) using result fields
    def _sync_result(branch="main", sha="abc123", files=None):
        r = MagicMock()
        r.status = MagicMock()
        r.status.value = "success"
        r.commit_sha = sha
        r.branch = branch
        r.url = "https://github.com/user/repo"
        r.files_synced = files or ["main.py"]
        r.conflicts = []
        r.message = "Synced"
        return r

    svc.export_to_github = AsyncMock(return_value=_sync_result())
    svc.sync_branch = AsyncMock(
        return_value=_sync_result(branch="dev", sha="def456", files=["app.py"])
    )
    svc.create_version_branch = AsyncMock(return_value="v1.0")

    # Freeze/lock objects
    def _lock_result():
        lock = MagicMock()
        lock.path = "src/main.py"
        lock.status = MagicMock()
        lock.status.value = "locked"
        lock.locked_by = "user_123"
        from datetime import datetime

        lock.locked_at = datetime(2024, 1, 1)
        lock.reason = "Production file"
        return lock

    svc.freeze_file = AsyncMock(return_value=_lock_result())
    svc.unfreeze_file = AsyncMock(return_value=True)
    svc.get_frozen_files = AsyncMock(return_value=[])

    svc.create_pull_request = AsyncMock(
        return_value={
            "status": "success",
            "pr_url": "https://github.com/user/repo/pull/1",
            "pr_number": 1,
            "title": "Test PR",
        }
    )
    svc.merge_pull_request = AsyncMock(return_value=True)

    svc.get_sync_history = MagicMock(return_value=[])

    app.dependency_overrides[get_sync_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_sync_service, None)


# =============================================================================
# Health check
# =============================================================================


class TestHealthCheck:
    def test_health_returns_healthy(self, client, mock_github):
        resp = client.get("/api/github/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["service"] == "github-sync"
        assert "timestamp" in body

    def test_health_no_service_override_needed(self, client):
        """Health endpoint does not use the sync service — always reachable."""
        resp = client.get("/api/github/health")
        assert resp.status_code == 200


# =============================================================================
# Sync history
# =============================================================================


class TestSyncHistory:
    def test_get_history_empty(self, client, mock_github):
        resp = client.get("/api/github/sync-history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_history_with_limit(self, client, mock_github):
        resp = client.get("/api/github/sync-history?limit=10")
        assert resp.status_code == 200
        mock_github.get_sync_history.assert_called_once_with(10)

    def test_get_history_default_limit(self, client, mock_github):
        client.get("/api/github/sync-history")
        mock_github.get_sync_history.assert_called_once_with(50)


# =============================================================================
# Export to GitHub
# =============================================================================


class TestExportToGitHub:
    def test_export_success(self, client, mock_github):
        resp = client.post(
            "/api/github/projects/proj1/export",
            json={
                "repo_name": "my-repo",
                "files": {"main.py": "print('hello')"},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["commit_sha"] == "abc123"
        assert body["branch"] == "main"
        assert "main.py" in body["files_synced"]

    def test_export_calls_service(self, client, mock_github):
        client.post(
            "/api/github/projects/proj1/export",
            json={"repo_name": "test-repo", "files": {"a.py": "x = 1"}},
        )
        mock_github.export_to_github.assert_called_once()
        call_kwargs = mock_github.export_to_github.call_args[1]
        assert call_kwargs["project_id"] == "proj1"
        assert call_kwargs["repo_name"] == "test-repo"

    def test_export_missing_repo_name(self, client, mock_github):
        resp = client.post(
            "/api/github/projects/proj1/export",
            json={"files": {}},
        )
        assert resp.status_code == 422

    def test_export_with_custom_branch(self, client, mock_github):
        resp = client.post(
            "/api/github/projects/proj1/export",
            json={
                "repo_name": "my-repo",
                "branch": "feature/x",
                "files": {"app.py": ""},
            },
        )
        assert resp.status_code == 200


# =============================================================================
# Sync branch
# =============================================================================


class TestSyncBranch:
    def test_sync_branch_success(self, client, mock_github):
        resp = client.post(
            "/api/github/projects/proj1/sync-branch",
            json={"repo_name": "my-repo", "files": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["branch"] == "dev"

    def test_sync_branch_calls_service(self, client, mock_github):
        client.post(
            "/api/github/projects/proj1/sync-branch",
            json={"repo_name": "repo-x", "files": {"b.py": "pass"}},
        )
        mock_github.sync_branch.assert_called_once()
        call_kwargs = mock_github.sync_branch.call_args[1]
        assert call_kwargs["project_id"] == "proj1"
        assert call_kwargs["repo_name"] == "repo-x"


# =============================================================================
# Create version branch
# =============================================================================


class TestCreateVersionBranch:
    def test_create_version_success(self, client, mock_github):
        resp = client.post(
            "/api/github/projects/proj1/create-version",
            json={"repo_name": "my-repo", "version": "v1.0"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "branch" in body or "message" in body

    def test_create_version_invalid_version_format(self, client, mock_github):
        resp = client.post(
            "/api/github/projects/proj1/create-version",
            json={"repo_name": "my-repo", "version": "not-a-version"},
        )
        assert resp.status_code == 422

    def test_create_version_calls_service(self, client, mock_github):
        client.post(
            "/api/github/projects/proj1/create-version",
            json={"repo_name": "my-repo", "version": "v2.0"},
        )
        mock_github.create_version_branch.assert_called_once()


# =============================================================================
# File freeze / unfreeze
# =============================================================================


class TestFileFreeze:
    def test_freeze_file_success(self, client, mock_github):
        resp = client.post(
            "/api/github/projects/proj1/files/freeze",
            json={"path": "src/main.py", "reason": "critical"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == "src/main.py"
        assert body["status"] == "locked"
        assert body["locked_by"] == "user_123"

    def test_freeze_file_missing_path(self, client, mock_github):
        resp = client.post(
            "/api/github/projects/proj1/files/freeze",
            json={"reason": "critical"},
        )
        assert resp.status_code == 422

    def test_get_frozen_files_empty(self, client, mock_github):
        resp = client.get("/api/github/projects/proj1/files/frozen")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_unfreeze_file_success(self, client, mock_github):
        resp = client.delete("/api/github/projects/proj1/files/freeze/src/main.py")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_unfreeze_file_not_found(self, client, mock_github):
        mock_github.unfreeze_file = AsyncMock(return_value=False)
        resp = client.delete("/api/github/projects/proj1/files/freeze/missing.py")
        assert resp.status_code == 404


# =============================================================================
# Pull requests
# =============================================================================


class TestPullRequests:
    def test_create_pr_success(self, client, mock_github):
        resp = client.post(
            "/api/github/pull-requests",
            json={
                "repo_name": "my-repo",
                "title": "Test PR",
                "head": "feature",
                "base": "main",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pr_number"] == 1
        assert "pr_url" in body

    def test_create_pr_missing_required_fields(self, client, mock_github):
        resp = client.post(
            "/api/github/pull-requests",
            json={"repo_name": "my-repo"},
        )
        assert resp.status_code == 422


# =============================================================================
# Auto-sync webhook
# =============================================================================


class TestAutoSyncWebhook:
    def test_auto_sync_queued(self, client, mock_github):
        resp = client.post("/api/github/webhooks/auto-sync?project_id=proj1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "queued"
        assert "message" in body

    def test_auto_sync_returns_immediately(self, client, mock_github):
        """Webhook must respond quickly (background task); no blocking call."""
        resp = client.post("/api/github/webhooks/auto-sync?project_id=any-project")
        assert resp.status_code == 200


# =============================================================================
# Parametrized: status codes for key endpoints
# =============================================================================


@pytest.mark.parametrize(
    "endpoint,body",
    [
        ("/api/github/projects/proj1/export", {"repo_name": "r", "files": {}}),
        ("/api/github/projects/proj1/sync-branch", {"repo_name": "r", "files": {}}),
        (
            "/api/github/projects/proj1/create-version",
            {"repo_name": "r", "version": "v1.0"},
        ),
        ("/api/github/projects/proj1/files/freeze", {"path": "x.py"}),
        (
            "/api/github/pull-requests",
            {"repo_name": "r", "title": "T", "head": "feat", "base": "main"},
        ),
    ],
)
def test_post_endpoints_return_200(client, mock_github, endpoint, body):
    resp = client.post(endpoint, json=body)
    assert (
        resp.status_code == 200
    ), f"Expected 200 for POST {endpoint}, got {resp.status_code}: {resp.text}"
