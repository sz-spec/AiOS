"""
Fortification-v3 — Phase 1-3 expansion of the security test surface.

Honest-scope ceiling
--------------------
The kernel-mitigation tests in `phase1_*` verify what is provably
testable from a host pytest run:

  * source-level invariants (every `copy_to_user`-class function
    wraps the load between stac() and clac()),
  * built-ELF symbol presence (compiled-in kernel features cannot be
    silently dropped without breaking the symbol audit),
  * header-defined constants (`VOS3_CR4_SMAP`, `VOS3_CR4_CET`, etc.)
    match the published documentation.

These are NECESSARY conditions for the mitigations to work. They are
not SUFFICIENT — actually triggering a #PF on a SMAP violation
requires kernel runtime (QEMU). Where a test verifies the necessary
condition, the docstring says so explicitly. The audit-honesty
discipline established in earlier sessions: no overclaiming.

Phase 2 VBus tests are fully hermetic — they exercise the frame
format the Python driver and kernel parser share, without a real
socket.

Phase 3 sandbox tests run the real `ProcessSandbox` (the P0 fix
landed in commit `a34728b`) and verify nested-isolation +
cleanup invariants.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
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


@pytest.fixture(scope="session")
def repo_root() -> pathlib.Path:
    return _REPO_ROOT


@pytest.fixture(scope="session")
def kernel_dir(repo_root) -> pathlib.Path:
    return repo_root / "kernel"


@pytest.fixture(scope="session")
def kernel_elf(kernel_dir) -> pathlib.Path:
    return kernel_dir / "build" / "vos3.elf"


@pytest.fixture
def v3_env(monkeypatch, tmp_path):
    """Same shape as adv_env / perf_env / gov_env."""
    monkeypatch.setenv("VOS3_KEYRING_MODE", "local")
    monkeypatch.setenv("VOS3_KEYRING_PATH", str(tmp_path / "secrets.enc"))
    monkeypatch.setenv("VOS3_KEYRING_SEED_OVERRIDE", "v3-test-seed")
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
    from services.app_sandbox import _reset_gate_for_tests

    _reset_gate_for_tests()
    yield tmp_path
