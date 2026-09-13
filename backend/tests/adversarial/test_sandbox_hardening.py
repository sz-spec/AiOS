"""
Stage 4+ · P0 remediation — ProcessSandbox fail-closed lifecycle.

The pre-2026-05-15 ProcessSandbox at `services.app_sandbox.ProcessSandbox`
caught `(SubprocessError, OSError)` from `create_subprocess_exec(...,
preexec_fn=_sandbox_preexec(...))` and silently retried the same call
without `preexec_fn`. That collapsed the rlimit / NPROC / NOFILE /
FSIZE / RLIMIT_AS containment to zero. User-supplied code interpolated
into the wrapper at `f"{code}\n"` then ran unconstrained under the
host Python.

These tests verify the new fail-closed lifecycle:

  * preexec_fn failure → `SecuritySandboxError`
  * no second un-sandboxed `create_subprocess_exec` call
  * a `kind="sandbox_fail_closed"` audit row lands in
    `securityAuditLog` before the raise
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import patch

import pytest

from services.app_sandbox import (
    ProcessSandbox,
    SecuritySandboxError,
)
from core.database.sqlite_setup import SecurityAuditLog, get_session

# ---------------------------------------------------------------------------
# Faulty subprocess factory — fails the FIRST call (the preexec_fn one),
# would-succeed the SECOND. The new code must never reach the second.
# ---------------------------------------------------------------------------


def _make_faulty_exec(*, fail_with: type[BaseException] = OSError):
    """Fault-injector that raises on the FIRST hardened-spawn call.

    Hardened spawn is platform-specific:
      * Linux  → the call passes `preexec_fn=` (rlimit-setting hook)
      * macOS  → the call wraps in `/usr/bin/sandbox-exec`
    Both must fault; reaching any second un-sandboxed call is the
    P0 bug we're guarding against."""
    orig = asyncio.create_subprocess_exec
    counter = {"calls": 0}

    async def faulty_exec(*args, **kwargs):
        counter["calls"] += 1
        is_hardened_spawn = "preexec_fn" in kwargs or (
            args and isinstance(args[0], str) and "sandbox-exec" in args[0]
        )
        if is_hardened_spawn:
            raise fail_with("simulated hardened-spawn failure under test")
        # Reaching here is the bug we're testing for. Tag and bail.
        counter["unsandboxed_fallback_reached"] = True
        return await orig(*args, **kwargs)

    return faulty_exec, counter


# ---------------------------------------------------------------------------
# Core fail-closed contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preexec_oserror_raises_security_sandbox_error(adv_env):
    sandbox = ProcessSandbox(timeout_s=5.0)
    faulty, counter = _make_faulty_exec(fail_with=OSError)
    with patch("asyncio.create_subprocess_exec", side_effect=faulty):
        with pytest.raises(SecuritySandboxError) as ei:
            await sandbox.execute("result = 1", {})
    assert "CRITICAL" in str(ei.value)
    assert ei.value.platform  # populated
    assert counter["calls"] == 1, (
        f"expected exactly 1 subprocess call (the failing preexec one); "
        f"got {counter['calls']} — silent fallback was reached"
    )
    assert "unsandboxed_fallback_reached" not in counter


@pytest.mark.asyncio
async def test_preexec_subprocess_error_raises_security_sandbox_error(adv_env):
    from subprocess import SubprocessError

    sandbox = ProcessSandbox(timeout_s=5.0)
    faulty, counter = _make_faulty_exec(fail_with=SubprocessError)
    with patch("asyncio.create_subprocess_exec", side_effect=faulty):
        with pytest.raises(SecuritySandboxError):
            await sandbox.execute("result = 2", {})
    assert counter["calls"] == 1
    assert "unsandboxed_fallback_reached" not in counter


# ---------------------------------------------------------------------------
# Audit chronicle — every failure must leave a securityAuditLog row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_closed_writes_audit_row(adv_env):
    with get_session() as s:
        pre = s.query(SecurityAuditLog).filter_by(kind="sandbox_fail_closed").count()

    sandbox = ProcessSandbox(timeout_s=5.0)
    faulty, _ = _make_faulty_exec(fail_with=OSError)
    with patch("asyncio.create_subprocess_exec", side_effect=faulty):
        with pytest.raises(SecuritySandboxError):
            await sandbox.execute("result = 1", {})

    with get_session() as s:
        post = s.query(SecurityAuditLog).filter_by(kind="sandbox_fail_closed").count()
    assert post == pre + 1


@pytest.mark.asyncio
async def test_audit_row_carries_diagnostic_details(adv_env):
    import json

    sandbox = ProcessSandbox(timeout_s=5.0)
    faulty, _ = _make_faulty_exec(fail_with=OSError)
    with patch("asyncio.create_subprocess_exec", side_effect=faulty):
        with pytest.raises(SecuritySandboxError):
            await sandbox.execute("result = 1", {})

    with get_session() as s:
        row = (
            s.query(SecurityAuditLog)
            .filter_by(kind="sandbox_fail_closed")
            .order_by(SecurityAuditLog.timestamp.desc())
            .first()
        )
    assert row is not None
    assert "CRITICAL" in (row.reason or "")
    details = json.loads(row.details_json or "{}")
    assert details.get("exc_type") == "OSError"
    assert details.get("platform")  # populated, exact value host-dependent
    assert details.get("max_memory_mb") == 256


# ---------------------------------------------------------------------------
# Source-level invariants — the silent fallback must be gone
# ---------------------------------------------------------------------------


def test_source_does_not_contain_silent_fallback():
    """Belt-and-braces — even if a future refactor reintroduces a
    second spawn call inside the same except handler, this source-scan
    fails CI.

    After the 2026-05-16 factory refactor, `_execute_inner` no longer
    calls `asyncio.create_subprocess_exec` directly — it delegates to
    `provider.spawn(...)`. We therefore assert that:
      * `_execute_inner` contains exactly ONE call to `provider.spawn(`
      * No `provider.spawn` lives inside an `except` block in the same
        method (would-be silent-fallback pattern)
    """
    import re as _re
    import services.app_sandbox as mod

    src = inspect.getsource(mod.ProcessSandbox._execute_inner)
    spawn_count = src.count("provider.spawn(")
    assert spawn_count == 1, (
        f"ProcessSandbox._execute_inner contains {spawn_count} "
        f"provider.spawn() calls — the fail-closed contract requires "
        f"exactly 1 (silent fallback removed)."
    )
    # Make sure no spawn lives inside an `except` (would be silent fallback).
    in_except = bool(
        _re.search(
            r"except[^:]*:\s*[^#\n]*provider\.spawn\(",
            src,
            _re.DOTALL,
        )
    )
    assert not in_except, (
        "provider.spawn() found inside an except block — that is the "
        "silent-fallback pattern the P0 fix eliminated. Refusing CI."
    )


def test_security_sandbox_error_is_runtime_error_subclass():
    assert issubclass(SecuritySandboxError, RuntimeError)


def test_security_sandbox_error_carries_reason_and_platform():
    err = SecuritySandboxError("test reason", platform="darwin-test")
    assert err.reason == "test reason"
    assert err.platform == "darwin-test"
    assert "CRITICAL" in str(err)
    assert "Aborting execution" in str(err)


# ---------------------------------------------------------------------------
# Cross-platform invariant — fail-closed must apply on every platform.
# We can't actually run macOS+Linux from the same host, so we mock
# `sys.platform` and assert the message reflects whatever platform
# the fault occurred under.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("plat", ["darwin", "linux", "win32", "freebsd14"])
async def test_fail_closed_on_every_platform_label(adv_env, plat):
    sandbox = ProcessSandbox(timeout_s=5.0)
    faulty, _ = _make_faulty_exec(fail_with=OSError)
    with patch("asyncio.create_subprocess_exec", side_effect=faulty), patch(
        "services.app_sandbox.sys"
    ) as mock_sys:
        mock_sys.platform = plat
        mock_sys.executable = "/usr/bin/python3"
        with pytest.raises(SecuritySandboxError) as ei:
            await sandbox.execute("result = 1", {})
    assert ei.value.platform == plat
