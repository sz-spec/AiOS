"""
backend/tests/security/test_wasm_sandbox.py — Phase-1 WASM sandbox tests.

Verifies the three core sandbox guarantees:
    1. A clean module runs to completion under the default Policy.
    2. A runaway (infinite-loop) module is killed by fuel exhaustion.
    3. A module importing a DENIED capability is rejected at link time.

Skipped when wasmtime is not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Skip the whole file if wasmtime isn't installed (CI runners may not).
wasmtime = pytest.importorskip("wasmtime")

from sandbox.wasm import Policy, run_module  # noqa: E402

POC_DIR = Path(__file__).resolve().parents[2] / "sandbox" / "wasm" / "poc"


def _wat_to_wasm(path: Path) -> bytes:
    """Compile a .wat fixture to .wasm via wasmtime-py's parser."""
    return wasmtime.wat2wasm(path.read_text(encoding="utf-8"))


def test_hello_module_runs_clean():
    wasm = _wat_to_wasm(POC_DIR / "hello.wat")
    result = run_module(wasm, Policy(fuel=10_000))
    assert result.exceeded_limit is None
    assert result.failed_imports == []
    assert result.exit_code in (0, None)


def test_runaway_module_trips_fuel_limit():
    wasm = _wat_to_wasm(POC_DIR / "runaway.wat")
    result = run_module(wasm, Policy(fuel=10_000, wall_clock_ms=5_000))
    assert (
        result.exceeded_limit == "fuel"
    ), f"Expected fuel exhaustion, got {result.exceeded_limit!r}"


def test_filesystem_import_is_rejected():
    wasm = _wat_to_wasm(POC_DIR / "filesystem_import.wat")
    result = run_module(wasm, Policy())
    assert (
        result.failed_imports
    ), "Module importing wasi:filesystem must be rejected at link time"
    assert any("filesystem" in m for m in result.failed_imports)
