"""
P5.5 — Sandboxed App File Manager tests.

Coverage groups:

  1. Service-level `list_dir` — flat + recursive enumeration; size
     and modified-time fields populated; descent bounded by
     max_depth / max_entries.

  2. /api/apps/{id}/fs/list — happy path, auth, scope gate,
     traversal pen-test (auto-isolation chain re-validated end-
     to-end), 404 for non-existent or non-directory paths.

  3. Cross-app guard — A's secret can't enumerate B's sandbox.
"""

from __future__ import annotations


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database.sqlite_setup import _reset_for_tests, init_db
from services.app_filesystem import (
    APP_FILESYSTEM_SERVICE,
    FILESYSTEM_MANAGER,
    PathTraversalAttempt,
)
from services.app_sandbox import (
    SANDBOX_MANAGER,
    _reset_gate_for_tests,
    app_storage_root,
)


@pytest.fixture
def local_db(airgap_env):
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()


def _install(scopes, restrictions=()) -> tuple:
    res = SANDBOX_MANAGER.install(
        {
            "name": "p55-test",
            "version": "1.0.0",
            "scopes": list(scopes),
            "restrictions": list(restrictions),
        }
    )
    return res["app_id"], res["secret"]


def _build_http() -> FastAPI:
    from api.app_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# (1) Service-level list_dir — flat + recursive
# ---------------------------------------------------------------------------


def test_list_dir_returns_files_with_metadata(local_db):
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    FILESYSTEM_MANAGER.write_app_file(app_id, "alpha.txt", "hello")
    FILESYSTEM_MANAGER.write_app_file(app_id, "notes.md", "# hi\n")

    entries = APP_FILESYSTEM_SERVICE.list_dir(app_id, "")
    names = sorted(e["name"] for e in entries)
    assert names == ["alpha.txt", "notes.md"]
    alpha = next(e for e in entries if e["name"] == "alpha.txt")
    assert alpha["is_dir"] is False
    assert alpha["size_bytes"] == 5  # len("hello")
    assert isinstance(alpha["modified_at_ms"], int)
    assert alpha["modified_at_ms"] > 0
    assert alpha["path"] == "alpha.txt"


def test_list_dir_nested_non_recursive_only_shows_top_level(local_db):
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    FILESYSTEM_MANAGER.write_app_file(app_id, "root.txt", "r")
    FILESYSTEM_MANAGER.write_app_file(app_id, "nested/inside.txt", "in")

    entries = APP_FILESYSTEM_SERVICE.list_dir(app_id, "")
    paths = sorted(e["path"] for e in entries)
    assert paths == ["nested", "root.txt"]  # no descent
    nested = next(e for e in entries if e["name"] == "nested")
    assert nested["is_dir"] is True


def test_list_dir_recursive_descends_subdirs(local_db):
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    FILESYSTEM_MANAGER.write_app_file(app_id, "root.txt", "r")
    FILESYSTEM_MANAGER.write_app_file(app_id, "a/b/c/deep.txt", "z")

    entries = APP_FILESYSTEM_SERVICE.list_dir(app_id, "", recursive=True)
    paths = sorted(e["path"] for e in entries)
    assert "root.txt" in paths
    assert "a/b/c/deep.txt" in paths


def test_list_dir_recursive_respects_max_depth(local_db):
    """A depth-1 cap MUST stop at the first level — deep files
    don't appear even with recursive=True."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    FILESYSTEM_MANAGER.write_app_file(app_id, "a/b/c/d/leaf.txt", "x")
    entries = APP_FILESYSTEM_SERVICE.list_dir(
        app_id,
        "",
        recursive=True,
        max_depth=1,
    )
    paths = [e["path"] for e in entries]
    # 'a' (the top-level dir) is included at depth 0.
    # 'a/b' appears at depth 1 — the limit; 'a/b/c' would be depth 2.
    assert "a" in paths
    assert "a/b" in paths
    assert "a/b/c/d/leaf.txt" not in paths


def test_list_dir_blocked_without_read_scope(local_db):
    from fastapi import HTTPException

    app_id, _ = _install(["filesystem.write"])  # write-only
    FILESYSTEM_MANAGER.write_app_file(app_id, "x.txt", "y")
    with pytest.raises(HTTPException) as exc:
        APP_FILESYSTEM_SERVICE.list_dir(app_id, "")
    assert exc.value.status_code == 403
    assert exc.value.detail["scope"] == "filesystem.read"


def test_list_dir_traversal_auto_isolates(local_db):
    """The directive's containment guarantee — listing a path
    outside the sandbox is rejected AND the app is frozen."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    with pytest.raises(PathTraversalAttempt):
        APP_FILESYSTEM_SERVICE.list_dir(app_id, "../../etc")
    assert SANDBOX_MANAGER.get(app_id)["status"] == "isolated"


def test_list_dir_404_for_missing_path(local_db):
    app_id, _ = _install(["filesystem.read"])
    # No file planted at "ghost-dir" — call should raise
    # FileNotFoundError (the route translates to 404).
    with pytest.raises(FileNotFoundError):
        APP_FILESYSTEM_SERVICE.list_dir(app_id, "ghost-dir")


def test_list_dir_not_a_directory_raises(local_db):
    """list_dir on a file (not a directory) raises NotADirectoryError."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    FILESYSTEM_MANAGER.write_app_file(app_id, "regular.txt", "y")
    with pytest.raises(NotADirectoryError):
        APP_FILESYSTEM_SERVICE.list_dir(app_id, "regular.txt")


# ---------------------------------------------------------------------------
# (2) HTTP endpoint — GET /api/apps/{id}/fs/list
# ---------------------------------------------------------------------------


def test_http_list_returns_written_file(local_db, network_guard):
    """Directive's Test 1 — write a file, GET /fs/list, see it
    in the response with metadata intact."""
    app_id, secret = _install(["filesystem.read", "filesystem.write"])
    FILESYSTEM_MANAGER.write_app_file(app_id, "memo.txt", "vOS rules")

    client = TestClient(_build_http())
    r = client.get(
        f"/api/apps/{app_id}/fs/list",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == app_id
    assert body["path"] == ""
    assert body["recursive"] is False
    assert body["count"] == 1
    entry = body["entries"][0]
    assert entry["name"] == "memo.txt"
    assert entry["is_dir"] is False
    assert entry["size_bytes"] == len("vOS rules")
    assert entry["modified_at_ms"] > 0


def test_http_list_recursive_query_param(local_db, network_guard):
    app_id, secret = _install(["filesystem.read", "filesystem.write"])
    FILESYSTEM_MANAGER.write_app_file(app_id, "top.txt", "t")
    FILESYSTEM_MANAGER.write_app_file(app_id, "nested/leaf.txt", "l")

    client = TestClient(_build_http())
    r = client.get(
        f"/api/apps/{app_id}/fs/list",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        params={"recursive": "true"},
    )
    assert r.status_code == 200
    body = r.json()
    paths = sorted(e["path"] for e in body["entries"])
    assert "top.txt" in paths
    assert "nested/leaf.txt" in paths


def test_http_list_403_without_read_scope(local_db, network_guard):
    """Directive's Test 2 — an app without `filesystem.read`
    can't enumerate its own sandbox."""
    app_id, secret = _install(["filesystem.write"])  # write-only
    FILESYSTEM_MANAGER.write_app_file(app_id, "planted.txt", "x")
    client = TestClient(_build_http())
    r = client.get(
        f"/api/apps/{app_id}/fs/list",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["scope"] == "filesystem.read"


def test_http_list_traversal_returns_403_and_isolates(local_db, network_guard):
    app_id, secret = _install(["filesystem.read", "filesystem.write"])
    client = TestClient(_build_http())
    r = client.get(
        f"/api/apps/{app_id}/fs/list",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        params={"path": "../../etc"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["detail"]["error"] == "path_traversal_attempt"
    assert body["detail"].get("auto_isolated") is True
    assert SANDBOX_MANAGER.get(app_id)["status"] == "isolated"


def test_http_list_404_for_missing_directory(local_db, network_guard):
    app_id, secret = _install(["filesystem.read"])
    client = TestClient(_build_http())
    r = client.get(
        f"/api/apps/{app_id}/fs/list",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        params={"path": "nope"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "not_found"


def test_http_list_cross_app_blocked(local_db, network_guard):
    """A's secret + B's URL → 403 via _require_matching_app, never
    even reaches the list helper."""
    a, a_secret = _install(["filesystem.read"])
    b, _ = _install(["filesystem.read"])
    client = TestClient(_build_http())
    r = client.get(
        f"/api/apps/{b}/fs/list",
        headers={"X-App-Id": a, "X-App-Secret": a_secret},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] in (
        "app_id_mismatch",
        "app_unauthorized",
    )


def test_http_list_empty_sandbox(local_db, network_guard):
    """A fresh app with no files returns an empty entries list."""
    app_id, secret = _install(["filesystem.read"])
    # Ensure the storage dir exists (install provisions it).
    assert app_storage_root(app_id).is_dir()
    client = TestClient(_build_http())
    r = client.get(
        f"/api/apps/{app_id}/fs/list",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
    )
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert r.json()["entries"] == []
