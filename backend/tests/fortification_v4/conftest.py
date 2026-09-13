"""
Fortification-v4 — Windows AppContainer / Job Object provider tests.

Honest-scope ceiling
--------------------
Real `CreateAppContainerProfile` + `AssignProcessToJobObject` calls
require a Windows host. From a non-Windows host pytest run, we
verify the cross-platform layer:

  * factory dispatch picks the right provider per `sys.platform`
  * `WindowsAppContainerProvider.__init__` refuses on non-Windows with
    a P0-compliant `SecuritySandboxError`
  * the container-name generator + validator are pure functions and
    have full coverage
  * the SDDL string builder produces valid grant ACLs
  * the Job Object's `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` shape +
    flag constants match the Windows SDK headers
  * the factory raises on truly unsupported platforms (cygwin, sunos,
    aix, freebsd, etc.) — fail-closed contract

The Windows-gated live tests (`@pytest.mark.skipif(sys.platform !=
"win32")`) activate the moment your CI hits a `windows-2022` runner.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


@pytest.fixture(autouse=True)
def reset_services():
    yield


@pytest.fixture(autouse=True)
def reset_rate_limiters():
    yield


@pytest.fixture(scope="session", autouse=True)
def _isolate_dev_memory():
    yield


@pytest.fixture
def v4_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "v4-test-seed")
    monkeypatch.setenv("VOS3_LOCAL_DB_PATH", str(tmp_path / "vos3.db"))
    monkeypatch.setenv("VOS3_APP_DATA_DIR", str(tmp_path / "vos"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    try:
        from services.crypto_keyring import KEYRING

        KEYRING._reset_for_tests()
    except Exception:
        pass
    from core.database.sqlite_setup import _reset_for_tests, init_db

    _reset_for_tests()
    init_db()
    yield tmp_path


@pytest.fixture
def mock_win32_env(monkeypatch, v4_env):
    """High-fidelity Windows-API mocking layer.

    Honest scope (read carefully): this fixture lets us run the
    `WindowsAppContainerProvider` code path on a NON-Windows host by:

      1. monkeypatching `sys.platform` to `"win32"` for the duration
         of the test,
      2. replacing `ctypes.windll` with a `MagicMock` that responds
         to `.userenv`, `.kernel32`, `.advapi32` and the documented
         Win32 entry points with success codes (S_OK, valid HANDLEs,
         BOOL TRUE),
      3. providing a stub `subprocess.STARTUPINFO` class so the
         provider's `spawn()` can be invoked without Win32 stdlib
         present.

    What this verifies:
      ✅ Our code calls the right Win32 APIs (correct names, arity).
      ✅ Our code passes the right struct shapes (verifiable via
         `mock.call_args` introspection).
      ✅ The factory + provider class wiring is correct.

    What this does NOT verify (no test can, on macOS):
      ❌ Windows actually denies network egress under AppContainer.
      ❌ Windows actually enforces JOB_OBJECT_LIMIT_JOB_MEMORY.
      ❌ Windows actually grants the SDDL ACL on filesystem access.

    Live end-to-end verification requires a `windows-2022` CI runner.
    These mocked tests are an API-CONTRACT layer — they catch
    regressions in our wiring without replacing the integration
    test that runs on actual Windows.
    """
    from unittest.mock import MagicMock

    monkeypatch.setattr("sys.platform", "win32")

    # Mock the three Windows DLLs.
    mock_userenv = MagicMock()
    mock_userenv.CreateAppContainerProfile = MagicMock(return_value=0)  # S_OK
    mock_userenv.DeleteAppContainerProfile = MagicMock(return_value=0)

    mock_kernel32 = MagicMock()
    mock_kernel32.CreateJobObjectW = MagicMock(return_value=0xC0FFEE)
    mock_kernel32.SetInformationJobObject = MagicMock(return_value=1)
    mock_kernel32.AssignProcessToJobObject = MagicMock(return_value=1)
    mock_kernel32.CloseHandle = MagicMock(return_value=1)
    mock_kernel32.ResumeThread = MagicMock(return_value=0)

    mock_advapi32 = MagicMock()

    # Build the windll namespace. ctypes.windll on Windows is a
    # LibraryLoader; here a MagicMock that resolves .userenv etc. is fine.
    mock_windll = MagicMock()
    mock_windll.userenv = mock_userenv
    mock_windll.kernel32 = mock_kernel32
    mock_windll.advapi32 = mock_advapi32

    # On non-Windows, ctypes.windll doesn't exist as an attribute.
    # Set with raising=False so monkeypatch creates it.
    import ctypes as _ctypes

    monkeypatch.setattr(_ctypes, "windll", mock_windll, raising=False)
    # ctypes.GetLastError also doesn't exist on non-Windows.
    monkeypatch.setattr(_ctypes, "GetLastError", lambda: 0, raising=False)

    # subprocess.STARTUPINFO is Windows-only; provide a stub.
    import subprocess as _sp

    if not hasattr(_sp, "STARTUPINFO"):

        class _StubStartupInfo:
            def __init__(self):
                self.dwFlags = 0
                self.hStdInput = None
                self.hStdOutput = None
                self.hStdError = None
                self.wShowWindow = 0

        monkeypatch.setattr(_sp, "STARTUPINFO", _StubStartupInfo, raising=False)

    yield {
        "userenv": mock_userenv,
        "kernel32": mock_kernel32,
        "advapi32": mock_advapi32,
        "windll": mock_windll,
    }
