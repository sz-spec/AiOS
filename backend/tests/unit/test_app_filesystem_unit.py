"""
Stage 1 · Atomic unit isolation for services/app_filesystem.py.

Pure-function tests of `_resolve_inside_sandbox` — each of the 6
defense gates exercised independently. NO subprocess, NO real DB
beyond an init for the audit log writer.

Maps to spring-2026 CVE class:
  CVE-2026-42048 (Langflow), CVE-2026-43888 (Outline Zip Slip),
  CVE-2026-40318 (SiYuan), CVE-2026-41612 (VS Code Live Preview).
"""

from __future__ import annotations


import pytest

from core.database.sqlite_setup import _reset_for_tests, init_db


@pytest.fixture
def fs_env(unit_env, tmp_path, monkeypatch):
    """Tighter env for path-confinement tests — explicit app data dir."""
    monkeypatch.setenv("VOS3_APP_DATA_DIR", str(tmp_path / "vos"))
    _reset_for_tests()
    init_db()
    from services.app_sandbox import _reset_gate_for_tests, SANDBOX_MANAGER

    _reset_gate_for_tests()
    # Install a real app to give us a sandbox root.
    res = SANDBOX_MANAGER.install(
        {"name": "fs-unit", "version": "1.0", "scopes": ["filesystem.read"]},
        workspace_id="ws-fs-unit",
    )
    yield res["app_id"]


# ---------------------------------------------------------------------------
# G1 — type + empty
# ---------------------------------------------------------------------------


def test_g1_rejects_empty_string(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "")


def test_g1_rejects_none(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, None)  # type: ignore[arg-type]


def test_g1_rejects_int(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, 42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# G2 — NUL byte
# ---------------------------------------------------------------------------


def test_g2_rejects_embedded_nul(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "ok\x00/etc/passwd")


def test_g2_rejects_leading_nul(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "\x00leading-nul.txt")


# ---------------------------------------------------------------------------
# G3 — absolute / drive-letter
# ---------------------------------------------------------------------------


def test_g3_rejects_posix_absolute(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "/etc/passwd")


def test_g3_rejects_windows_drive_letter(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "C:\\Windows\\System32")


def test_g3_rejects_unc_path(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "\\\\server\\share\\evil")


def test_g3_rejects_backslash_leading(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "\\bin\\sh")


# ---------------------------------------------------------------------------
# G5 — literal ".." component
# ---------------------------------------------------------------------------


def test_g5_rejects_simple_parent_traversal(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "../../etc/passwd")


def test_g5_rejects_traversal_with_intermediate_segment(fs_env):
    """`foo/../../etc/passwd` — `..` is a literal component."""
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "foo/../../etc/passwd")


def test_g5_rejects_windows_style_traversal_on_posix(fs_env):
    """Backslash-separated parents — normalized to / so G5 sees `..`."""
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "..\\..\\Windows\\foo")


def test_g5_rejects_dotdot_in_basename(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "dir/..")


def test_g5_allows_single_dot_filename(fs_env):
    """A literal '.' as a path COMPONENT means current-dir; not a
    traversal. Our gate explicitly checks for '..' only."""
    from services.app_filesystem import _resolve_inside_sandbox

    result = _resolve_inside_sandbox(fs_env, "./readme.txt")
    # `.` is normalized away by resolve(); result is the sandbox-rooted path.
    assert str(result).endswith("/storage/readme.txt")


# ---------------------------------------------------------------------------
# G6 — happy path: containment check
# ---------------------------------------------------------------------------


def test_g6_simple_relative_path_resolves_inside(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox

    result = _resolve_inside_sandbox(fs_env, "subdir/data.json")
    assert str(result).endswith("/storage/subdir/data.json")


def test_g6_deep_nested_relative_path_resolves_inside(fs_env):
    from services.app_filesystem import _resolve_inside_sandbox

    result = _resolve_inside_sandbox(fs_env, "a/b/c/d/e/f/g.txt")
    assert "/storage/a/b/c/d/e/f/g.txt" in str(result)


# ---------------------------------------------------------------------------
# Auto-isolate triggers on any reject
# ---------------------------------------------------------------------------


def test_traversal_auto_isolates_app(fs_env):
    """Any rejected resolve flips the app to status='isolated' and
    writes a path_traversal_attempt audit row before raising."""
    from services.app_filesystem import _resolve_inside_sandbox, PathTraversalAttempt
    from services.app_sandbox import SANDBOX_MANAGER
    from core.database.sqlite_setup import SecurityAuditLog, get_session

    # Sanity: app is active.
    record = SANDBOX_MANAGER.get(fs_env)
    assert record["status"] == "active"

    with pytest.raises(PathTraversalAttempt):
        _resolve_inside_sandbox(fs_env, "../../etc/passwd")

    # App is now isolated.
    record = SANDBOX_MANAGER.get(fs_env)
    assert record["status"] == "isolated"

    # Audit row was written.
    with get_session() as s:
        rows = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="path_traversal_attempt",
            )
            .all()
        )
        assert any(r.appId == fs_env for r in rows)
