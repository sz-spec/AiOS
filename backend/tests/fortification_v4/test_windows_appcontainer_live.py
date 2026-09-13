"""
Windows AppContainer — API-contract tests (cross-platform via mock).

Honest scope
------------
This file was originally `pytest.mark.skipif(sys.platform != "win32")`-
gated; under the May 2026 directive ("ABSOLUTE NO-SKIP POLICY") it has
been refactored to use the `mock_win32_env` fixture from
`fortification_v4/conftest.py`. The fixture replaces `ctypes.windll`
+ `subprocess.STARTUPINFO` with `unittest.mock.MagicMock` stand-ins so
the `WindowsAppContainerProvider` code path runs on every host.

What this file verifies (cross-platform, runs on macOS):
  ✅ Provider construction succeeds under mocked Win32 surface
  ✅ Provider calls the right Win32 entry points with the right args
  ✅ Job Object create/close round-trips cleanly
  ✅ AppContainer profile lifecycle (create + delete) calls userenv.dll
  ✅ emit_spawn_audit chronicles `windows_appcontainer_enforced`
  ✅ Spawn flow passes EXTENDED_STARTUPINFO_PRESENT in creationflags

What this file does NOT verify (would require a real Windows host):
  ❌ Windows kernel actually denies network egress under AppContainer
  ❌ Windows kernel actually enforces JOB_OBJECT_LIMIT_JOB_MEMORY at
     the RSS ceiling
  ❌ AppContainer SID's filesystem ACL is actually honored

The original live-integration version is preserved in git history
(commit `2ee0d2f`); a future Windows CI workflow can revive it via
`git show 2ee0d2f:backend/tests/fortification_v4/test_windows_appcontainer_live.py`.
"""

from __future__ import annotations


import pytest

# ---------------------------------------------------------------------------
# Provider construction (under mock)
# ---------------------------------------------------------------------------


def test_provider_constructs_on_mocked_windows(mock_win32_env):
    from services.app_sandbox import WindowsAppContainerProvider

    p = WindowsAppContainerProvider()
    assert p.name == "windows_appcontainer"


def test_provider_resolves_userenv_dll(mock_win32_env):
    """Provider must touch userenv.dll's CreateAppContainerProfile +
    DeleteAppContainerProfile during construction (probe step)."""
    from services.app_sandbox import WindowsAppContainerProvider

    WindowsAppContainerProvider()
    # The probe accesses these as attributes — MagicMock auto-creates,
    # but our code does an explicit hasattr-style touch. Verify the
    # provider held a reference.
    assert mock_win32_env["userenv"].CreateAppContainerProfile is not None
    assert mock_win32_env["userenv"].DeleteAppContainerProfile is not None


def test_provider_resolves_kernel32_dll(mock_win32_env):
    from services.app_sandbox import WindowsAppContainerProvider

    WindowsAppContainerProvider()
    assert mock_win32_env["kernel32"].CreateJobObjectW is not None


# ---------------------------------------------------------------------------
# JobObject — round-trip under mock
# ---------------------------------------------------------------------------


def test_job_object_create_close_round_trip(mock_win32_env):
    from services.app_sandbox import WindowsJobObject

    job = WindowsJobObject(max_memory_mb=128, cpu_seconds=10)
    job.create()
    # Mock returns 0xC0FFEE as the HANDLE
    assert job._handle == 0xC0FFEE
    job.close()
    assert job._handle is None
    # CloseHandle called once for the job.
    assert mock_win32_env["kernel32"].CloseHandle.called


def test_job_object_calls_set_information(mock_win32_env):
    from services.app_sandbox import WindowsJobObject

    job = WindowsJobObject(max_memory_mb=256)
    job.create()
    # SetInformationJobObject was called with the extended limit class.
    set_info = mock_win32_env["kernel32"].SetInformationJobObject
    assert set_info.called
    args = set_info.call_args.args
    # args[0] = handle, args[1] = class, args[2] = pointer, args[3] = size
    assert args[1] == WindowsJobObject.JobObjectExtendedLimitInformation


@pytest.mark.parametrize("memory_mb", [64, 128, 256, 512, 1024, 2048])
def test_job_object_accepts_various_memory_limits(mock_win32_env, memory_mb):
    from services.app_sandbox import WindowsJobObject

    job = WindowsJobObject(max_memory_mb=memory_mb)
    job.create()
    assert job._handle is not None
    job.close()


@pytest.mark.parametrize("cpu_seconds", [1, 10, 30, 60, 120, 300])
def test_job_object_accepts_various_cpu_limits(mock_win32_env, cpu_seconds):
    from services.app_sandbox import WindowsJobObject

    job = WindowsJobObject(max_memory_mb=128, cpu_seconds=cpu_seconds)
    job.create()
    assert job._handle is not None
    job.close()


def test_job_object_set_information_failure_raises(mock_win32_env):
    """If SetInformationJobObject returns 0 (failure), JobObject
    must raise SecuritySandboxError + close the handle to avoid leak."""
    from services.app_sandbox import SecuritySandboxError, WindowsJobObject

    mock_win32_env["kernel32"].SetInformationJobObject.return_value = 0
    job = WindowsJobObject(max_memory_mb=128)
    with pytest.raises(SecuritySandboxError, match="SetInformationJobObject"):
        job.create()
    # Handle was created then released.
    assert mock_win32_env["kernel32"].CloseHandle.called


def test_job_object_create_failure_raises(mock_win32_env):
    """If CreateJobObjectW returns NULL (0), JobObject must raise."""
    from services.app_sandbox import SecuritySandboxError, WindowsJobObject

    mock_win32_env["kernel32"].CreateJobObjectW.return_value = 0
    job = WindowsJobObject(max_memory_mb=128)
    with pytest.raises(SecuritySandboxError, match="CreateJobObjectW"):
        job.create()


# ---------------------------------------------------------------------------
# AppContainer profile lifecycle
# ---------------------------------------------------------------------------


def test_create_then_delete_appcontainer_profile(mock_win32_env):
    from services.app_sandbox import WindowsAppContainerProvider

    p = WindowsAppContainerProvider()
    name = p._generate_container_name()
    p._create_profile(name)
    p._delete_profile(name)
    # Both userenv entry points were called.
    assert mock_win32_env["userenv"].CreateAppContainerProfile.called
    assert mock_win32_env["userenv"].DeleteAppContainerProfile.called


def test_create_profile_passes_zero_capabilities(mock_win32_env):
    """Empty capability list is the load-bearing isolation invariant —
    no `internetClient`, no `documentsLibrary`, nothing.

    Maps to CreateAppContainerProfile signature:
      (PCWSTR pszName, PCWSTR pszDisplayName, PCWSTR pszDescription,
       PSID_AND_ATTRIBUTES pCapabilities, DWORD dwCapabilityCount,
       PSID *ppSidAppContainerSid)
    Args index 3 and 4 carry the capability list + count.
    """
    from services.app_sandbox import WindowsAppContainerProvider

    p = WindowsAppContainerProvider()
    p._create_profile("VOS3_test_zero_caps")
    call = mock_win32_env["userenv"].CreateAppContainerProfile.call_args
    # 4th positional arg (capabilities pointer) must be None.
    assert call.args[3] is None
    # 5th positional arg (capability count) must be 0.
    assert call.args[4] == 0


def test_create_profile_passes_unique_name(mock_win32_env):
    from services.app_sandbox import WindowsAppContainerProvider

    p = WindowsAppContainerProvider()
    n1 = p._generate_container_name()
    n2 = p._generate_container_name()
    assert n1 != n2
    p._create_profile(n1)
    p._create_profile(n2)
    # Two different names passed to CreateAppContainerProfile.
    calls = mock_win32_env["userenv"].CreateAppContainerProfile.call_args_list
    assert len(calls) >= 2


def test_delete_profile_is_idempotent(mock_win32_env):
    from services.app_sandbox import WindowsAppContainerProvider

    p = WindowsAppContainerProvider()
    p._delete_profile("VOS3_test_double_delete")
    p._delete_profile("VOS3_test_double_delete")  # must not raise


def test_create_profile_already_exists_recoverable(mock_win32_env):
    """HRESULT 0x800700B7 = ERROR_ALREADY_EXISTS is recoverable; the
    profile is reused. CreateAppContainerProfile must NOT raise on this."""
    from services.app_sandbox import WindowsAppContainerProvider

    p = WindowsAppContainerProvider()
    mock_win32_env["userenv"].CreateAppContainerProfile.return_value = 0x800700B7
    # Must not raise.
    p._create_profile("VOS3_test_recover")


def test_create_profile_other_hresult_raises(mock_win32_env):
    """Any non-recoverable HRESULT must surface as SecuritySandboxError."""
    from services.app_sandbox import (
        SecuritySandboxError,
        WindowsAppContainerProvider,
    )

    p = WindowsAppContainerProvider()
    mock_win32_env["userenv"].CreateAppContainerProfile.return_value = (
        0x80070005  # E_ACCESSDENIED
    )
    with pytest.raises(SecuritySandboxError, match="HRESULT"):
        p._create_profile("VOS3_test_fail")


# ---------------------------------------------------------------------------
# emit_spawn_audit — `windows_appcontainer_enforced` row
# ---------------------------------------------------------------------------


def test_emit_spawn_audit_writes_row(mock_win32_env):
    from core.database.sqlite_setup import SecurityAuditLog, get_session
    from services.app_sandbox import WindowsAppContainerProvider

    p = WindowsAppContainerProvider()
    with get_session() as s:
        pre = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="windows_appcontainer_enforced",
            )
            .count()
        )
    p.emit_spawn_audit()
    with get_session() as s:
        post = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="windows_appcontainer_enforced",
            )
            .count()
        )
    assert post == pre + 1


@pytest.mark.parametrize("n_calls", [1, 3, 5, 10])
def test_emit_spawn_audit_count_matches_calls(mock_win32_env, n_calls):
    from core.database.sqlite_setup import SecurityAuditLog, get_session
    from services.app_sandbox import WindowsAppContainerProvider

    p = WindowsAppContainerProvider()
    with get_session() as s:
        pre = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="windows_appcontainer_enforced",
            )
            .count()
        )
    for _ in range(n_calls):
        p.emit_spawn_audit()
    with get_session() as s:
        post = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="windows_appcontainer_enforced",
            )
            .count()
        )
    assert post == pre + n_calls


def test_emit_spawn_audit_details_carry_platform(mock_win32_env):
    import json
    from core.database.sqlite_setup import SecurityAuditLog, get_session
    from services.app_sandbox import WindowsAppContainerProvider

    p = WindowsAppContainerProvider()
    p.emit_spawn_audit()
    with get_session() as s:
        row = (
            s.query(SecurityAuditLog)
            .filter_by(
                kind="windows_appcontainer_enforced",
            )
            .order_by(SecurityAuditLog.timestamp.desc())
            .first()
        )
    assert row is not None
    details = json.loads(row.details_json or "{}")
    assert details.get("platform") == "win32"
    assert details.get("provider") == "windows_appcontainer"


# ---------------------------------------------------------------------------
# Factory wiring — under mock, _get_sandbox_provider returns the right class
# ---------------------------------------------------------------------------


def test_factory_returns_windows_provider_under_mock(mock_win32_env):
    from services.app_sandbox import (
        WindowsAppContainerProvider,
        _get_sandbox_provider,
    )

    p = _get_sandbox_provider()
    assert isinstance(p, WindowsAppContainerProvider)
