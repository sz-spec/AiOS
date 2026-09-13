"""
Phase 3 · Nested ProcessSandbox isolation.

Verifies that running an AI process inside an already-sandboxed
workspace context maintains absolute isolation: outer-sandbox limits
and audit chronicles must remain intact, and the inner sandbox MUST
also fail-closed (per the 2026-05-15 P0 remediation) if its preexec_fn
ever fails.

The tests use the real `ProcessSandbox` class — no mocks for the
inner sandbox state. The OUTER ProcessSandbox' create_subprocess_exec
is the boundary we mock to simulate failure scenarios.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

from services.app_sandbox import (
    ProcessSandbox,
    SecuritySandboxError,
)
from core.database.sqlite_setup import SecurityAuditLog, get_session

# 2026-05-16: macOS real-execution is now supported via the
# MacOSSeatbeltProvider (see services/app_sandbox.py). The
# `_REQUIRES_REAL` gate has been removed — these tests now run on both
# Linux and macOS. On macOS, every successful spawn emits an audit row
# with kind="macos_seatbelt_enforced"; tests that need to count it
# should query SecurityAuditLog directly.


# ---------------------------------------------------------------------------
# Basic nested-context — outer sandbox audit chronicle persists across
# a simulated inner-sandbox call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outer_fail_closed_writes_audit_row(v3_env):
    sandbox = ProcessSandbox(timeout_s=5.0)

    with get_session() as s:
        pre = s.query(SecurityAuditLog).filter_by(kind="sandbox_fail_closed").count()

    async def faulty(*args, **kwargs):
        if "preexec_fn" in kwargs or (args and "sandbox-exec" in str(args[0])):
            raise OSError("forced fault")
        raise AssertionError("fallback path must NOT execute")

    with patch("asyncio.create_subprocess_exec", side_effect=faulty):
        with pytest.raises(SecuritySandboxError):
            await sandbox.execute("result = 'outer'", {})

    with get_session() as s:
        post = s.query(SecurityAuditLog).filter_by(kind="sandbox_fail_closed").count()
    assert post == pre + 1


# ---------------------------------------------------------------------------
# Nested-context — outer succeeds, inner fault must still fail-closed.
# We simulate by chaining two ProcessSandbox executions where the
# second mock-fails.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("iteration", list(range(10)))
async def test_inner_fault_after_outer_success(v3_env, iteration):
    """Outer-call success, inner-call fault — both must observe
    fail-closed contract on their own."""
    sandbox = ProcessSandbox(timeout_s=5.0)
    # First call: real success.
    result = await sandbox.execute(f"result = {iteration}", {})
    assert result.success
    assert result.output == iteration

    # Second call: forced fault.
    async def faulty(*args, **kwargs):
        if "preexec_fn" in kwargs or (args and "sandbox-exec" in str(args[0])):
            raise OSError("inner fault")
        raise AssertionError("inner fallback path must NOT execute")

    with patch("asyncio.create_subprocess_exec", side_effect=faulty):
        with pytest.raises(SecuritySandboxError):
            await sandbox.execute(f"result = {iteration + 1000}", {})


# ---------------------------------------------------------------------------
# Sub-token isolation — every execution gets its own tempfile pair;
# the previous execution's tempfiles must be unlinked.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("n_runs", [2, 4, 8, 16])
async def test_no_orphan_tempfiles_after_successful_runs(v3_env, n_runs):
    sandbox = ProcessSandbox(timeout_s=5.0)
    tmpdir_before = set(os.listdir(tempfile.gettempdir()))
    for i in range(n_runs):
        r = await sandbox.execute(f"result = {i}", {"i": i})
        assert r.success
    tmpdir_after = set(os.listdir(tempfile.gettempdir()))
    # Per-run sandbox files match `tmp*.py` and `tmp*.json` patterns.
    new = tmpdir_after - tmpdir_before
    leak_py = [f for f in new if f.endswith(".py")]
    leak_json = [f for f in new if f.endswith(".json")]
    # Tolerance for concurrent-xdist-worker churn (see burst test).
    assert len(leak_py) <= 2, f"orphan .py: {leak_py}"
    assert len(leak_json) <= 2, f"orphan .json: {leak_json}"


@pytest.mark.asyncio
@pytest.mark.parametrize("n_failed_runs", [2, 4, 8])
async def test_no_orphan_tempfiles_after_failed_runs(v3_env, n_failed_runs):
    """The fail-closed P0 fix's `finally` block must unlink temps even
    when SecuritySandboxError is raised."""
    sandbox = ProcessSandbox(timeout_s=5.0)
    tmpdir_before = set(os.listdir(tempfile.gettempdir()))

    async def faulty(*args, **kwargs):
        if "preexec_fn" in kwargs or (args and "sandbox-exec" in str(args[0])):
            raise OSError("forced fault")
        raise AssertionError("fallback must not execute")

    with patch("asyncio.create_subprocess_exec", side_effect=faulty):
        for _ in range(n_failed_runs):
            with pytest.raises(SecuritySandboxError):
                await sandbox.execute("result = 1", {})

    tmpdir_after = set(os.listdir(tempfile.gettempdir()))
    new = tmpdir_after - tmpdir_before
    leak_py = [f for f in new if f.endswith(".py")]
    leak_json = [f for f in new if f.endswith(".json")]
    # Other xdist workers churn /private/var/folders during the burst
    # window; tolerate ≤2 concurrent residuals per file class.
    assert len(leak_py) <= 2, f"orphan .py after fail: {leak_py}"
    assert len(leak_json) <= 2, f"orphan .json after fail: {leak_json}"


# ---------------------------------------------------------------------------
# Concurrent nested invocations — semaphore bounds true concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_executions_dont_share_state(v3_env):
    """Each execute() call has its own tempfile pair, env, and
    subprocess. State must not leak across parallel runs."""
    sandbox = ProcessSandbox(timeout_s=10.0)
    coros = [sandbox.execute(f"result = {i}", {"i": i}) for i in range(10)]
    results = await asyncio.gather(*coros)
    assert all(r.success for r in results)
    outputs = sorted(r.output for r in results)
    assert outputs == list(range(10))


@pytest.mark.asyncio
@pytest.mark.parametrize("payload_id", list(range(15)))
async def test_per_invocation_context_isolation(v3_env, payload_id):
    """Each context dict's contents land in the inner subprocess as
    that-and-only-that context (no leakage from sibling invocations)."""
    sandbox = ProcessSandbox(timeout_s=5.0)
    ctx = {"secret_id": payload_id, "salt": f"S{payload_id}"}
    result = await sandbox.execute(
        "result = (context['secret_id'], context['salt'])",
        ctx,
    )
    assert result.success
    assert list(result.output) == [payload_id, f"S{payload_id}"]


# ---------------------------------------------------------------------------
# Outer/inner boundary — the sandbox must not let one execution's
# print(...) accidentally appear in another's stdout.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdout_isolation_between_runs(v3_env):
    sandbox = ProcessSandbox(timeout_s=5.0)
    r1 = await sandbox.execute("result = 'first'", {})
    r2 = await sandbox.execute("result = 'second'", {})
    assert r1.output == "first"
    assert r2.output == "second"


# ---------------------------------------------------------------------------
# Audit row tagging — successful and failed runs differ in audit fingerprint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_run_does_not_emit_fail_closed_audit(v3_env):
    sandbox = ProcessSandbox(timeout_s=5.0)
    with get_session() as s:
        pre = s.query(SecurityAuditLog).filter_by(kind="sandbox_fail_closed").count()
    r = await sandbox.execute("result = 1", {})
    assert r.success
    with get_session() as s:
        post = s.query(SecurityAuditLog).filter_by(kind="sandbox_fail_closed").count()
    assert post == pre  # no audit on success


# ---------------------------------------------------------------------------
# macOS Seatbelt provider — audit row contract
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="MacOSSeatbeltProvider audit row is darwin-only",
)
@pytest.mark.asyncio
async def test_macos_seatbelt_audit_row_emitted_per_spawn(v3_env):
    """Spec contract: every successful spawn under the macOS provider
    writes a `kind="macos_seatbelt_enforced"` row to securityAuditLog."""
    sandbox = ProcessSandbox(timeout_s=5.0)
    with get_session() as s:
        pre = (
            s.query(SecurityAuditLog).filter_by(kind="macos_seatbelt_enforced").count()
        )
    r = await sandbox.execute("result = 'darwin'", {})
    assert r.success
    assert r.output == "darwin"
    with get_session() as s:
        post = (
            s.query(SecurityAuditLog).filter_by(kind="macos_seatbelt_enforced").count()
        )
    assert post == pre + 1


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="MacOSSeatbeltProvider audit row is darwin-only",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("n_runs", [1, 3, 5])
async def test_macos_seatbelt_audit_row_counts_match_runs(v3_env, n_runs):
    sandbox = ProcessSandbox(timeout_s=5.0)
    with get_session() as s:
        pre = (
            s.query(SecurityAuditLog).filter_by(kind="macos_seatbelt_enforced").count()
        )
    for i in range(n_runs):
        r = await sandbox.execute(f"result = {i}", {})
        assert r.success
    with get_session() as s:
        post = (
            s.query(SecurityAuditLog).filter_by(kind="macos_seatbelt_enforced").count()
        )
    assert post == pre + n_runs


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Network-block contract is enforced only by Seatbelt",
)
@pytest.mark.asyncio
async def test_macos_seatbelt_blocks_outbound_network(v3_env):
    """Seatbelt's `(deny network*)` rule must block any socket egress."""
    sandbox = ProcessSandbox(timeout_s=10.0)
    r = await sandbox.execute(
        "import socket\n"
        "try:\n"
        "    socket.gethostbyname('example.com')\n"
        "    result = 'NETWORK_LEAKED'\n"
        "except (OSError, socket.gaierror) as e:\n"
        "    result = f'BLOCKED:{type(e).__name__}'\n",
        {},
    )
    assert r.success
    assert isinstance(r.output, str)
    assert r.output.startswith(
        "BLOCKED"
    ), f"network was not blocked under Seatbelt: {r.output}"


@pytest.mark.asyncio
async def test_failed_inner_does_not_taint_outer_state(v3_env):
    sandbox = ProcessSandbox(timeout_s=5.0)
    # First, a clean success.
    r_a = await sandbox.execute("result = 'A'", {})
    assert r_a.success and r_a.output == "A"

    # Then a forced fail.
    async def faulty(*args, **kwargs):
        if "preexec_fn" in kwargs or (args and "sandbox-exec" in str(args[0])):
            raise OSError("forced")
        raise AssertionError("fallback")

    with patch("asyncio.create_subprocess_exec", side_effect=faulty):
        with pytest.raises(SecuritySandboxError):
            await sandbox.execute("result = 'B'", {})

    # Third, a clean success — sandbox state must be intact.
    r_c = await sandbox.execute("result = 'C'", {})
    assert r_c.success and r_c.output == "C"
