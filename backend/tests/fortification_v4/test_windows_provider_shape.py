"""
Cross-platform shape tests for WindowsAppContainerProvider — runs on
every host. Verifies the pure-function helpers and the Job Object
constants match the Windows SDK contract.
"""

from __future__ import annotations

import re
import sys

import pytest

from services.app_sandbox import (
    SecuritySandboxError,
    WindowsAppContainerProvider,
    WindowsJobObject,
)

# ---------------------------------------------------------------------------
# Container-name generator — uuid-backed, prefix-stable, length-bounded
# ---------------------------------------------------------------------------


def test_container_name_starts_with_prefix():
    name = WindowsAppContainerProvider._generate_container_name()
    assert name.startswith(WindowsAppContainerProvider.CONTAINER_NAME_PREFIX)


def test_container_name_under_64_chars():
    name = WindowsAppContainerProvider._generate_container_name()
    assert 1 <= len(name) <= 64


@pytest.mark.parametrize("iteration", list(range(50)))
def test_container_name_unique_across_50_runs(iteration):
    """Across 50 invocations, no two names should collide."""
    names = {WindowsAppContainerProvider._generate_container_name() for _ in range(50)}
    # All 50 unique.
    assert len(names) == 50


def test_container_name_passes_validator():
    name = WindowsAppContainerProvider._generate_container_name()
    assert WindowsAppContainerProvider._validate_container_name(name)


# ---------------------------------------------------------------------------
# Container-name validator — exhaustive parametrize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("VOS3_Sandbox_abc123", True),
        ("a", True),
        ("X" * 64, True),
        ("X" * 65, False),
        ("", False),
        ("has space", False),
        ("has/slash", False),
        ("has\\backslash", False),
        ("has-dash", False),  # only alnum + underscore per spec
        ("has.dot", False),
        ("has@at", False),
        ("VOS3_Sandbox_" + "a" * 60, False),  # > 64 chars total
    ],
)
def test_container_name_validator_matrix(name, expected):
    assert WindowsAppContainerProvider._validate_container_name(name) is expected


@pytest.mark.parametrize(
    "ch",
    [
        "!",
        "#",
        "$",
        "%",
        "&",
        "(",
        ")",
        "+",
        "=",
        "[",
        "]",
        ";",
        ":",
        "'",
        '"',
        "<",
        ">",
        "?",
        "|",
        "~",
    ],
)
def test_container_name_validator_rejects_special_char(ch):
    name = f"VOS3_Sandbox_{ch}123"
    assert WindowsAppContainerProvider._validate_container_name(name) is False


# ---------------------------------------------------------------------------
# SDDL grant builder — verify format matches Windows ACL grammar
# ---------------------------------------------------------------------------

# Reference SDDL: D:(A;OICI;FA;;;<SID>)
#   D:        — DACL
#   (A;...)   — Allow ACE
#   OICI      — Object Inherit + Container Inherit
#   FA        — File All-access
#   ;;        — (no ACE flags) ; (no object type) ; (no inherited object type)
#   ;<SID>    — Trustee SID
_SDDL_RE = re.compile(r"^D:\(A;OICI;FA;;;S-1-15-\d+-[\d-]+\)$")


@pytest.mark.parametrize(
    "sid",
    [
        "S-1-15-2-1234567890-1234567890-1234567890-1234567890-1234567890-1234567890-1234567890",
        "S-1-15-2-0-0-0-0-0-0-0",
        "S-1-15-2-1-2-3-4-5-6-7",
    ],
)
def test_sddl_grant_format_matches_windows_grammar(sid):
    sddl = WindowsAppContainerProvider._sddl_grant_for_sandbox(sid, "/tmp/sandbox")
    assert _SDDL_RE.match(sddl), f"SDDL {sddl!r} doesn't match expected grammar"


def test_sddl_grant_contains_sid_verbatim():
    sid = "S-1-15-2-1-2-3-4-5-6-7-8-9-10"
    sddl = WindowsAppContainerProvider._sddl_grant_for_sandbox(sid, "/x")
    assert sid in sddl


def test_sddl_grant_uses_inheritance_flags():
    """OICI = Object Inherit (0x01) + Container Inherit (0x02) — needed
    so the AppContainer grant propagates to files and subdirs created
    later inside the sandbox path."""
    sid = "S-1-15-2-1-2-3-4-5-6-7"
    sddl = WindowsAppContainerProvider._sddl_grant_for_sandbox(sid, "/x")
    assert "OICI" in sddl


def test_sddl_grant_uses_file_all_access():
    sid = "S-1-15-2-1-2-3-4-5-6-7"
    sddl = WindowsAppContainerProvider._sddl_grant_for_sandbox(sid, "/x")
    assert "FA" in sddl  # File All-access


# ---------------------------------------------------------------------------
# Job Object constants — must match Windows SDK winnt.h
# ---------------------------------------------------------------------------


def test_job_object_limit_process_time_constant():
    """JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002 per winnt.h"""
    assert WindowsJobObject.JOB_OBJECT_LIMIT_PROCESS_TIME == 0x00000002


def test_job_object_limit_job_memory_constant():
    """JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200 per winnt.h"""
    assert WindowsJobObject.JOB_OBJECT_LIMIT_JOB_MEMORY == 0x00000200


def test_job_object_limit_kill_on_close_constant():
    """JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000 per winnt.h"""
    assert WindowsJobObject.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE == 0x00002000


def test_job_object_extended_limit_info_class_constant():
    """JobObjectExtendedLimitInformation enum value 9 per winnt.h"""
    assert WindowsJobObject.JobObjectExtendedLimitInformation == 9


def test_job_object_cpu_unit_is_100ns():
    """Win32 LARGE_INTEGER time units are 100-ns intervals; 1 sec = 1e7."""
    assert WindowsJobObject.CPU_LIMIT_100NS_SEC == 10_000_000


# ---------------------------------------------------------------------------
# Cross-platform refusal — provider + job-object refuse on non-Windows
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows refusal test")
def test_appcontainer_provider_refuses_non_win32():
    with pytest.raises(SecuritySandboxError):
        WindowsAppContainerProvider()


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows refusal test")
def test_appcontainer_provider_error_mentions_platform():
    with pytest.raises(SecuritySandboxError) as ei:
        WindowsAppContainerProvider()
    msg = str(ei.value).lower()
    assert sys.platform.lower() in msg or "win32" in msg


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows refusal test")
def test_job_object_refuses_non_win32():
    with pytest.raises(SecuritySandboxError):
        WindowsJobObject(256)


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows refusal test")
@pytest.mark.parametrize("memory_mb", [128, 256, 512, 1024, 2048])
def test_job_object_refuses_non_win32_for_any_memory(memory_mb):
    """Memory size doesn't matter on non-Windows — refusal is platform-gated."""
    with pytest.raises(SecuritySandboxError):
        WindowsJobObject(memory_mb)


# ---------------------------------------------------------------------------
# Container-name prefix is stable + advertised
# ---------------------------------------------------------------------------


def test_container_name_prefix_is_VOS3():
    assert WindowsAppContainerProvider.CONTAINER_NAME_PREFIX.startswith("VOS3")


def test_container_name_prefix_under_32_chars():
    """Prefix must leave room for the 24-char hex suffix under 64 total."""
    assert len(WindowsAppContainerProvider.CONTAINER_NAME_PREFIX) < 32


# ---------------------------------------------------------------------------
# Class metadata
# ---------------------------------------------------------------------------


def test_provider_name_is_windows_appcontainer():
    assert WindowsAppContainerProvider.name == "windows_appcontainer"


def test_provider_inherits_from_base():
    from services.app_sandbox import _SandboxProvider

    assert issubclass(WindowsAppContainerProvider, _SandboxProvider)


def test_job_object_default_cpu_seconds():
    """CPU cap defaults to 60s — same envelope as the Linux rlimit path."""
    import inspect

    sig = inspect.signature(WindowsJobObject.__init__)
    cpu_param = sig.parameters.get("cpu_seconds")
    assert cpu_param is not None
    assert cpu_param.default == 60
