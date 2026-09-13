"""
Cross-platform factory dispatch — runs on every host.

`_get_sandbox_provider()` must select the correct provider class based
on `sys.platform`, and refuse outright on anything else.
"""

from __future__ import annotations

import sys

import pytest

from services.app_sandbox import (
    LinuxRlimitProvider,
    MacOSSeatbeltProvider,
    SecuritySandboxError,
    WindowsAppContainerProvider,
    _get_sandbox_provider,
)

# ---------------------------------------------------------------------------
# Factory dispatch — happy path per platform (mocked sys.platform)
# ---------------------------------------------------------------------------


def test_factory_returns_linux_provider_when_linux(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    provider = _get_sandbox_provider()
    assert isinstance(provider, LinuxRlimitProvider)
    assert provider.name == "linux_rlimit"


def test_factory_returns_linux_provider_for_any_linux_variant(monkeypatch):
    # `sys.platform` is sometimes "linux" + suffix; the canonical form
    # is just "linux" since Python 3.3 (PEP 513). Exact match expected.
    monkeypatch.setattr("sys.platform", "linux")
    assert isinstance(_get_sandbox_provider(), LinuxRlimitProvider)


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="MacOSSeatbeltProvider construction requires /usr/bin/sandbox-exec",
)
def test_factory_returns_macos_provider_when_darwin():
    # Real darwin call — no mock needed.
    provider = _get_sandbox_provider()
    assert isinstance(provider, MacOSSeatbeltProvider)
    assert provider.name == "macos_seatbelt"


# ---------------------------------------------------------------------------
# Factory refusal — unsupported platforms must fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_platform",
    [
        "cygwin",
        "sunos5",
        "aix",
        "freebsd14",
        "openbsd7",
        "netbsd9",
        "haiku",
        "",
        "unknown",
    ],
)
def test_factory_refuses_unsupported_platform(monkeypatch, bad_platform):
    monkeypatch.setattr("sys.platform", bad_platform)
    with pytest.raises(SecuritySandboxError) as ei:
        _get_sandbox_provider()
    assert bad_platform in str(ei.value) or "supported" in str(ei.value).lower()


@pytest.mark.parametrize(
    "bad_platform",
    [
        "cygwin",
        "sunos5",
        "freebsd14",
        "haiku",
    ],
)
def test_factory_error_lists_supported_platforms(monkeypatch, bad_platform):
    monkeypatch.setattr("sys.platform", bad_platform)
    with pytest.raises(SecuritySandboxError) as ei:
        _get_sandbox_provider()
    msg = str(ei.value).lower()
    # Error should name the supported alternatives.
    assert "linux" in msg
    assert "darwin" in msg
    assert "win32" in msg


# ---------------------------------------------------------------------------
# Cross-platform refuse — every provider class refuses on a wrong platform
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="WindowsAppContainerProvider only meaningfully constructable on win32",
)
def test_windows_provider_refuses_on_non_windows():
    with pytest.raises(SecuritySandboxError) as ei:
        WindowsAppContainerProvider()
    assert "win32" in str(ei.value).lower()


def test_macos_provider_refuses_when_sandbox_exec_missing(monkeypatch):
    """Verify the refusal path without leaving darwin — point the class
    constant at a path we know doesn't exist."""
    monkeypatch.setattr(
        MacOSSeatbeltProvider,
        "SEATBELT_BIN",
        "/nonexistent/path/to/sandbox-exec",
    )
    with pytest.raises(SecuritySandboxError) as ei:
        MacOSSeatbeltProvider()
    assert "sandbox-exec" in str(ei.value).lower()


# ---------------------------------------------------------------------------
# Three platforms supported — name constants are stable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "klass,expected_name",
    [
        (LinuxRlimitProvider, "linux_rlimit"),
        (MacOSSeatbeltProvider, "macos_seatbelt"),
        (WindowsAppContainerProvider, "windows_appcontainer"),
    ],
)
def test_provider_name_constants(klass, expected_name):
    assert klass.name == expected_name


@pytest.mark.parametrize(
    "klass",
    [
        LinuxRlimitProvider,
        MacOSSeatbeltProvider,
        WindowsAppContainerProvider,
    ],
)
def test_every_provider_has_spawn_method(klass):
    assert hasattr(klass, "spawn")
    assert callable(klass.spawn)


@pytest.mark.parametrize(
    "klass",
    [
        LinuxRlimitProvider,
        MacOSSeatbeltProvider,
        WindowsAppContainerProvider,
    ],
)
def test_every_provider_has_emit_spawn_audit(klass):
    assert hasattr(klass, "emit_spawn_audit")
    assert callable(klass.emit_spawn_audit)


# ---------------------------------------------------------------------------
# Factory consistency — repeated calls on the same platform return the
# same provider class (idempotence)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plat,expected_cls",
    [
        ("linux", LinuxRlimitProvider),
    ],
)
def test_factory_idempotent_on_linux(monkeypatch, plat, expected_cls):
    monkeypatch.setattr("sys.platform", plat)
    p1 = _get_sandbox_provider()
    p2 = _get_sandbox_provider()
    assert type(p1) is type(p2) is expected_cls


# ---------------------------------------------------------------------------
# Provider construction — Linux provider is constructable everywhere
# (no platform-specific syscalls in its __init__).
# ---------------------------------------------------------------------------


def test_linux_provider_constructable_on_any_host():
    p = LinuxRlimitProvider()
    assert p.name == "linux_rlimit"


def test_linux_provider_has_no_emit_audit_side_effects():
    """The default emit_spawn_audit is a no-op for the Linux provider."""
    p = LinuxRlimitProvider()
    # Just check it doesn't raise.
    p.emit_spawn_audit()
