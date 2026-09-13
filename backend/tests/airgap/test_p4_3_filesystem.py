"""
P4.3 — Sandboxed filesystem tests.

Coverage groups:

  1. SovereignFilesystemManager unit-level — direct calls into
     `write_app_file` / `read_app_file` exercise the path
     confinement algorithm without an HTTP hop.

  2. Path-traversal attack surface — `..`, absolute paths, NUL
     bytes, Windows drive letters, mixed separators, sneaky
     symlink-in-prefix shapes — every variant must raise
     `PathTraversalAttempt` BEFORE any `open()` call.

  3. HTTP endpoints — POST /api/apps/{id}/fs/write and GET
     /api/apps/{id}/fs/read behind `get_app_context()`. Includes
     the full "app A tries to read app B's sandbox" cross-check.

All tests stay on the airgap loopback kill-switch; the filesystem
manager doesn't touch the network at all.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database.sqlite_setup import (
    _reset_for_tests,
    init_db,
    resolve_app_data_dir,
)
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
from core.database.sqlite_setup import SecurityAuditLog, get_session

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_db(airgap_env):
    """Fresh SQLite + empty gate cache."""
    _reset_for_tests()
    init_db()
    _reset_gate_for_tests()
    yield airgap_env["sqlite_path"]
    _reset_for_tests()
    _reset_gate_for_tests()


def _install(scopes, restrictions=()) -> tuple:
    """Install an app under SANDBOX_MANAGER. Returns (app_id, secret)."""
    result = SANDBOX_MANAGER.install(
        {
            "name": "fs-test-app",
            "version": "1.0.0",
            "scopes": list(scopes),
            "restrictions": list(restrictions),
        }
    )
    return result["app_id"], result["secret"]


# ---------------------------------------------------------------------------
# (1) Install auto-provisions the storage directory
# ---------------------------------------------------------------------------


def test_install_provisions_storage_directory(local_db):
    """The install path must create `{app_data}/apps/{id}/storage/`
    on disk with 0o700 perms (owner-only)."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    storage = app_storage_root(app_id)
    assert storage.is_dir(), f"storage dir not created: {storage}"
    # Path layout sanity.
    assert storage.name == "storage"
    assert storage.parent.name == app_id
    assert storage.parent.parent.name == "apps"
    assert storage.parent.parent.parent == resolve_app_data_dir()
    # 0o700 (best-effort; Windows runs may skip the chmod).
    if os.name != "nt":
        mode = storage.stat().st_mode & 0o777
        assert mode == 0o700, oct(mode)


def test_each_app_gets_its_own_isolated_root(local_db):
    a_id, _ = _install(["filesystem.read"])
    b_id, _ = _install(["filesystem.read"])
    assert a_id != b_id
    assert app_storage_root(a_id) != app_storage_root(b_id)


# ---------------------------------------------------------------------------
# (2) Capability + happy-path roundtrip
# ---------------------------------------------------------------------------


def test_write_succeeds_with_filesystem_write_scope(local_db):
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    result = FILESYSTEM_MANAGER.write_app_file(app_id, "notes/today.txt", "hello vOS")
    assert result["bytes_written"] == len(b"hello vOS")
    written = app_storage_root(app_id) / "notes" / "today.txt"
    assert written.read_bytes() == b"hello vOS"


def test_write_denied_without_filesystem_write_scope(local_db):
    """Read-only app must NOT be able to write."""
    from fastapi import HTTPException

    app_id, _ = _install(["filesystem.read"])
    with pytest.raises(HTTPException) as exc:
        FILESYSTEM_MANAGER.write_app_file(app_id, "x.txt", "denied")
    assert exc.value.status_code == 403
    assert exc.value.detail["scope"] == "filesystem.write"


def test_read_returns_written_content(local_db):
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    FILESYSTEM_MANAGER.write_app_file(app_id, "data.txt", "payload")
    assert FILESYSTEM_MANAGER.read_app_file(app_id, "data.txt") == "payload"


def test_read_denied_without_filesystem_read_scope(local_db):
    from fastapi import HTTPException

    # Write the file first as an admin (no app_id → gate skipped).
    write_app_id, _ = _install(["filesystem.write"])
    FILESYSTEM_MANAGER.write_app_file(write_app_id, "secret.txt", "shhh")

    # Now try to read from an app without filesystem.read — uses the
    # SAME sandbox root via the same app_id, but the gate denies.
    read_app_id, _ = _install([])  # no scopes
    # Plant the file under read_app_id's sandbox so this isn't a
    # "no such file" miss before the gate fires.
    target = app_storage_root(read_app_id) / "secret.txt"
    target.write_text("planted")
    with pytest.raises(HTTPException) as exc:
        FILESYSTEM_MANAGER.read_app_file(read_app_id, "secret.txt")
    assert exc.value.status_code == 403
    assert exc.value.detail["scope"] == "filesystem.read"


def test_read_missing_file_raises_file_not_found(local_db):
    app_id, _ = _install(["filesystem.read"])
    with pytest.raises(FileNotFoundError):
        FILESYSTEM_MANAGER.read_app_file(app_id, "nope.txt")


# ---------------------------------------------------------------------------
# (3) Path-traversal attack surface — every variant rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malicious",
    [
        "../secrets.txt",
        "../../etc/passwd",
        "subdir/../../../etc/shadow",
        "/etc/passwd",  # absolute POSIX
        "/tmp/anything",
        "..",
        "..\\..\\Windows\\System32",
        "C:\\Windows\\System32\\cmd.exe",  # Windows absolute
        "\\Windows\\System32\\evil.dll",
        "with\x00nul.txt",  # NUL byte
    ],
)
def test_path_traversal_attempt_rejected(local_db, malicious):
    """Every malicious shape must raise PathTraversalAttempt
    BEFORE any open() call. Both read AND write must reject.

    P4.3 — traversal detection now AUTO-ISOLATES the app on first
    hit, so we use a fresh install for the write probe and another
    for the read probe to make sure each side raises
    PathTraversalAttempt (not AppIsolated from the prior probe)."""
    app_id_w, _ = _install(["filesystem.read", "filesystem.write"])
    with pytest.raises(PathTraversalAttempt) as wexc:
        FILESYSTEM_MANAGER.write_app_file(app_id_w, malicious, "hax")
    assert wexc.value.app_id == app_id_w
    assert wexc.value.requested_path == malicious

    app_id_r, _ = _install(["filesystem.read", "filesystem.write"])
    with pytest.raises(PathTraversalAttempt):
        FILESYSTEM_MANAGER.read_app_file(app_id_r, malicious)


def test_symlink_escape_blocked(local_db):
    """If an attacker plants a symlink inside the sandbox pointing
    outside, the read path must refuse to follow it.

    We use O_NOFOLLOW on POSIX; the kernel returns ELOOP and the
    manager translates it into PathTraversalAttempt."""
    if os.name == "nt" or not hasattr(os, "symlink"):
        pytest.skip("symlink test is POSIX-only")

    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    sandbox = app_storage_root(app_id)

    # Plant `escape -> /etc/hosts` inside the sandbox.
    target_outside = Path("/etc/hosts")
    if not target_outside.exists():
        pytest.skip("no /etc/hosts on this host")
    link_inside = sandbox / "escape.txt"
    os.symlink(str(target_outside), str(link_inside))

    with pytest.raises(PathTraversalAttempt):
        FILESYSTEM_MANAGER.read_app_file(app_id, "escape.txt")


def test_nested_subdirs_allowed_inside_sandbox(local_db):
    """Legitimate nested paths (no traversal) must STILL work."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    FILESYSTEM_MANAGER.write_app_file(
        app_id,
        "a/b/c/deep.txt",
        "nested ok",
    )
    assert FILESYSTEM_MANAGER.read_app_file(app_id, "a/b/c/deep.txt") == "nested ok"


def test_content_size_cap(local_db, monkeypatch):
    """Oversized writes raise ValueError before touching disk.

    We patch the module-level constant directly rather than reloading
    `services.app_filesystem` — a reload would rebind
    `PathTraversalAttempt` to a new class object and break the
    `except PathTraversalAttempt` block in `api/app_routes` (which
    was imported under the original class) for every subsequent
    test in this session."""
    from services import app_filesystem

    monkeypatch.setattr(app_filesystem, "_MAX_FILE_BYTES", 16)
    app_id, _ = _install(["filesystem.write"])
    with pytest.raises(ValueError, match="exceeds"):
        FILESYSTEM_MANAGER.write_app_file(app_id, "big.txt", "x" * 1024)


# ---------------------------------------------------------------------------
# (4) HTTP endpoints behind get_app_context
# ---------------------------------------------------------------------------


def _build_apps_http() -> FastAPI:
    from api.app_routes import router

    app = FastAPI()
    app.include_router(router)
    return app


def test_http_write_then_read_roundtrip(local_db, network_guard):
    app_id, secret = _install(["filesystem.read", "filesystem.write"])
    client = TestClient(_build_apps_http())

    w = client.post(
        f"/api/apps/{app_id}/fs/write",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"path": "memo.txt", "content": "via http"},
    )
    assert w.status_code == 200, w.text
    assert w.json()["bytes_written"] == len(b"via http")

    r = client.get(
        f"/api/apps/{app_id}/fs/read",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        params={"path": "memo.txt"},
    )
    assert r.status_code == 200
    assert r.json()["content"] == "via http"


def test_http_write_traversal_returns_403(local_db, network_guard):
    """The directive's penetration-test scenario: an app WITH full
    filesystem.read + filesystem.write attempts to escape its
    sandbox. The route returns 403 with `path_traversal_attempt`."""
    app_id, secret = _install(["filesystem.read", "filesystem.write"])
    client = TestClient(_build_apps_http())

    r = client.post(
        f"/api/apps/{app_id}/fs/write",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"path": "../../etc/passwd", "content": "hax"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["detail"]["error"] == "path_traversal_attempt"
    assert body["detail"]["requested_path"] == "../../etc/passwd"

    # The traversal auto-isolates the first app, so the read pen-test
    # uses a fresh install — otherwise `get_app_context` would short-
    # circuit with `app_unauthorized` before reaching the path
    # resolver, masking the traversal-detection assertion below.
    app_id_r, secret_r = _install(["filesystem.read", "filesystem.write"])
    r = client.get(
        f"/api/apps/{app_id_r}/fs/read",
        headers={"X-App-Id": app_id_r, "X-App-Secret": secret_r},
        params={"path": "/etc/passwd"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "path_traversal_attempt"


def test_http_write_without_write_scope_returns_403(local_db, network_guard):
    app_id, secret = _install(["filesystem.read"])  # read-only
    client = TestClient(_build_apps_http())
    r = client.post(
        f"/api/apps/{app_id}/fs/write",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"path": "x.txt", "content": "denied"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["scope"] == "filesystem.write"


def test_http_read_without_read_scope_returns_403(local_db, network_guard):
    """App without filesystem.read can't read even when the file
    physically exists inside its sandbox (planted by us)."""
    app_id, secret = _install(["filesystem.write"])  # write-only
    (app_storage_root(app_id) / "preset.txt").write_text("planted")
    client = TestClient(_build_apps_http())
    r = client.get(
        f"/api/apps/{app_id}/fs/read",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        params={"path": "preset.txt"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["scope"] == "filesystem.read"


def test_http_cross_app_access_blocked(local_db, network_guard):
    """App A with a valid secret must NOT be able to operate on
    app B's sandbox by varying the URL's {app_id}."""
    a_id, a_secret = _install(["filesystem.read", "filesystem.write"])
    b_id, _ = _install(["filesystem.read", "filesystem.write"])
    client = TestClient(_build_apps_http())

    # A's headers, B's URL → app_id_mismatch / 403.
    r = client.post(
        f"/api/apps/{b_id}/fs/write",
        headers={"X-App-Id": a_id, "X-App-Secret": a_secret},
        json={"path": "stolen.txt", "content": "hax"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] in (
        "app_id_mismatch",
        "app_unauthorized",
    )


def test_http_read_missing_file_returns_404(local_db, network_guard):
    app_id, secret = _install(["filesystem.read"])
    client = TestClient(_build_apps_http())
    r = client.get(
        f"/api/apps/{app_id}/fs/read",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        params={"path": "ghost.txt"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "not_found"


# ===========================================================================
# P4.3 directive extensions:
#   * Auto-isolation on traversal detection (the load-bearing change)
#   * AppFilesystemService facade with read_file / write_file /
#     list_dir / delete_file shape
#   * /api/apps/{id}/state/file (GET + POST) wire shape
# ===========================================================================


# ---------------------------------------------------------------------------
# Auto-isolation — every traversal attempt instantly freezes the app
# ---------------------------------------------------------------------------


def test_traversal_auto_isolates_app_on_disk(local_db):
    """ADVERSARIAL — the directive's pen-test. A traversal raise
    MUST flip the app's status to 'isolated' BEFORE the exception
    leaves the call site. Verified by reading SANDBOX_MANAGER.get
    after the catch."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    assert SANDBOX_MANAGER.get(app_id)["status"] == "active"

    with pytest.raises(PathTraversalAttempt):
        FILESYSTEM_MANAGER.read_app_file(app_id, "../../../../etc/passwd")

    # Status flipped to isolated on the same call.
    record = SANDBOX_MANAGER.get(app_id)
    assert record["status"] == "isolated", record

    # Audit log carries the traversal event with `auto_isolated=True`.
    with get_session() as session:
        rows = (
            session.query(SecurityAuditLog)
            .filter_by(
                kind="path_traversal_attempt",
                appId=app_id,
            )
            .all()
        )
        assert rows, "expected a path_traversal_attempt row"
        import json as _json

        assert any(
            _json.loads(r.details_json or "{}").get("auto_isolated") is True
            for r in rows
        )


def test_traversal_inheritance_is_permission_error():
    """The directive's contract — `PathTraversalAttempt` inherits
    from PermissionError so route-level `except PermissionError`
    paths catch it as a 403."""
    assert issubclass(PathTraversalAttempt, PermissionError)


def test_isolated_app_cannot_resume_filesystem_after_traversal(local_db):
    """Defense-in-depth: after auto-isolation, even a LEGITIMATE
    path within the sandbox is refused — the gate denies before
    the path resolver runs."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    with pytest.raises(PathTraversalAttempt):
        FILESYSTEM_MANAGER.write_app_file(app_id, "../escape.txt", "x")

    # The app is now isolated. _enforce_fs_scope raises
    # HTTPException(403) because PERMISSION_GATE.check trips on
    # AppIsolated.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        FILESYSTEM_MANAGER.read_app_file(app_id, "legitimate.txt")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "app_unauthorized"


def test_http_traversal_returns_403_and_isolates(local_db, network_guard):
    """Same scenario through the HTTP surface: a traversal POST
    returns 403 + `auto_isolated: True` in the body."""
    app_id, secret = _install(["filesystem.read", "filesystem.write"])
    client = TestClient(_build_apps_http())
    r = client.post(
        f"/api/apps/{app_id}/fs/write",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"path": "../../etc/passwd", "content": "hax"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["detail"]["error"] == "path_traversal_attempt"
    assert body["detail"].get("auto_isolated") is True
    # On-disk row reflects the isolation.
    assert SANDBOX_MANAGER.get(app_id)["status"] == "isolated"


# ---------------------------------------------------------------------------
# AppFilesystemService — read_file / write_file / list_dir / delete_file
# ---------------------------------------------------------------------------


def test_service_read_file_and_write_file_alias_existing_manager(local_db):
    """The directive-shaped facade routes through the same
    SovereignFilesystemManager — no behavior drift."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    APP_FILESYSTEM_SERVICE.write_file(app_id, "alias.txt", "via service")
    assert APP_FILESYSTEM_SERVICE.read_file(app_id, "alias.txt") == "via service"


def test_service_list_dir_returns_entries(local_db):
    """list_dir returns name/is_dir/size_bytes for each child."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    APP_FILESYSTEM_SERVICE.write_file(app_id, "a.txt", "alpha")
    APP_FILESYSTEM_SERVICE.write_file(app_id, "nested/b.txt", "beta")

    entries = APP_FILESYSTEM_SERVICE.list_dir(app_id, "")
    names = sorted(e["name"] for e in entries)
    assert "a.txt" in names and "nested" in names
    a_row = next(e for e in entries if e["name"] == "a.txt")
    nested_row = next(e for e in entries if e["name"] == "nested")
    assert a_row["is_dir"] is False
    assert a_row["size_bytes"] == 5  # len("alpha")
    assert nested_row["is_dir"] is True


def test_service_list_dir_blocked_without_read_scope(local_db):
    from fastapi import HTTPException

    app_id, _ = _install(["filesystem.write"])  # write-only
    with pytest.raises(HTTPException) as exc:
        APP_FILESYSTEM_SERVICE.list_dir(app_id, "")
    assert exc.value.status_code == 403
    assert exc.value.detail["scope"] == "filesystem.read"


def test_service_delete_file_unlinks_target(local_db):
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    APP_FILESYSTEM_SERVICE.write_file(app_id, "doomed.txt", "bye")
    assert APP_FILESYSTEM_SERVICE.read_file(app_id, "doomed.txt") == "bye"

    APP_FILESYSTEM_SERVICE.delete_file(app_id, "doomed.txt")
    with pytest.raises(FileNotFoundError):
        APP_FILESYSTEM_SERVICE.read_file(app_id, "doomed.txt")


def test_service_delete_refuses_directories(local_db):
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    APP_FILESYSTEM_SERVICE.write_file(app_id, "subdir/x.txt", "inside")
    with pytest.raises(IsADirectoryError):
        APP_FILESYSTEM_SERVICE.delete_file(app_id, "subdir")


def test_service_delete_traversal_auto_isolates(local_db):
    """The directive's auto-isolation contract — every method
    delegates to the same `_resolve_inside_sandbox` so delete is
    just as hardened as read/write."""
    app_id, _ = _install(["filesystem.read", "filesystem.write"])
    with pytest.raises(PathTraversalAttempt):
        APP_FILESYSTEM_SERVICE.delete_file(app_id, "../../etc/passwd")
    assert SANDBOX_MANAGER.get(app_id)["status"] == "isolated"


# ---------------------------------------------------------------------------
# /api/apps/{id}/state/file — directive-shaped HTTP endpoint
# ---------------------------------------------------------------------------


def test_http_state_file_write_then_read_roundtrip(local_db, network_guard):
    app_id, secret = _install(["filesystem.read", "filesystem.write"])
    client = TestClient(_build_apps_http())
    w = client.post(
        f"/api/apps/{app_id}/state/file",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"path": "memo.txt", "content": "via state/file"},
    )
    assert w.status_code == 200, w.text
    r = client.get(
        f"/api/apps/{app_id}/state/file",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        params={"path": "memo.txt"},
    )
    assert r.status_code == 200
    assert r.json()["content"] == "via state/file"


def test_http_state_file_traversal_returns_403_and_isolates(local_db, network_guard):
    """Same auto-isolation guarantee on the directive-shaped path."""
    app_id, secret = _install(["filesystem.read", "filesystem.write"])
    client = TestClient(_build_apps_http())
    r = client.post(
        f"/api/apps/{app_id}/state/file",
        headers={"X-App-Id": app_id, "X-App-Secret": secret},
        json={"path": "../../../../etc/passwd", "content": "hax"},
    )
    assert r.status_code == 403
    body = r.json()
    assert body["detail"]["error"] == "path_traversal_attempt"
    assert body["detail"].get("auto_isolated") is True
    assert SANDBOX_MANAGER.get(app_id)["status"] == "isolated"
