# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
Module runner — instantiates a Wasmtime store, applies the Policy, and
invokes the module's entry point with the given argv/stdin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class RunResult:
    """Return value of run_module()."""

    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    fuel_consumed: int = 0
    exceeded_limit: Optional[str] = None  # "fuel" | "epoch" | None
    failed_imports: list = field(default_factory=list)


# WASI imports we DENY at link time. If a module references any of these,
# the Phase-1 runner refuses to instantiate. Future phases will move to
# explicit per-call capability grants; for now this is a conservative
# allowlist.
_DENIED_WASI_MODULES = {
    "wasi:filesystem",
    "wasi:filesystem/types",
    "wasi:filesystem/preopens",
    "wasi:sockets",
    "wasi:sockets/tcp",
    "wasi:http",
    "wasi:http/types",
    "wasi:http/outgoing-handler",
}


def run_module(
    wasm_bytes: bytes,
    policy,  # Policy
    args: Optional[Iterable[str]] = None,
    stdin: bytes = b"",
) -> RunResult:
    """Run a WASM module under the given Policy.

    Returns RunResult — never raises on a normal limit breach
    (`exceeded_limit` is set instead). Raises only on engine setup
    errors or on a denied capability import.
    """
    import wasmtime  # type: ignore[import-not-found]
    from .engine import Engine

    eng = Engine().engine
    module = wasmtime.Module(eng, wasm_bytes)

    # Capability gate — refuse modules that import any DENIED WASI surface.
    failed: list = []
    for imp in module.imports:
        # imp.module is the import-namespace, e.g. "wasi_snapshot_preview1"
        # or for component model "wasi:filesystem".
        if imp.module in _DENIED_WASI_MODULES:
            failed.append(imp.module)
    if failed:
        return RunResult(
            exit_code=None,
            exceeded_limit=None,
            failed_imports=failed,
        )

    store = wasmtime.Store(eng)
    # wasmtime-py renamed add_fuel(n) -> set_fuel(n) in 14.0. Support both
    # so the sandbox runs against whichever wheel is installed.
    if hasattr(store, "set_fuel"):
        store.set_fuel(policy.fuel)
    else:  # pragma: no cover - older wasmtime
        store.add_fuel(policy.fuel)
    store.set_epoch_deadline(1)

    # Memory limits: enforce policy.memory_pages. wasmtime-py changed the
    # limits API across versions — old releases had StoreLimitsBuilder;
    # 14+ take set_limits(memory_size=...) kwargs directly. Support both.
    mem_bytes = policy.memory_pages * 64 * 1024
    if hasattr(wasmtime, "StoreLimitsBuilder"):  # pragma: no cover - old wasmtime
        limits = wasmtime.StoreLimitsBuilder().memory_size(mem_bytes).build()
        store.set_limits(limits)
    else:
        store.set_limits(memory_size=mem_bytes)

    # WASI environment.
    wasi = wasmtime.WasiConfig()
    wasi.argv = list(args) if args else ["wasm-module"]
    if stdin:
        # wasmtime-py's stdin_str isn't bytes-aware; encode then write.
        wasi.stdin_str = stdin.decode("utf-8", errors="replace")
    # No preopens, no env passthrough — Phase-1 is hermetic.
    store.set_wasi(wasi)

    linker = wasmtime.Linker(eng)
    linker.define_wasi()

    # Schedule the wall-clock kill.
    import threading

    def _trip_epoch():
        store.set_epoch_deadline(0)
        eng.increment_epoch()

    timer = threading.Timer(policy.wall_clock_ms / 1000.0, _trip_epoch)
    timer.daemon = True
    timer.start()

    exceeded = None
    exit_code = 0
    try:
        instance = linker.instantiate(store, module)
        export = instance.exports(store).get("_start")
        if export is None:
            # Component-model entry; not implemented in Phase 1.
            exit_code = 0
        else:
            export(store)
    except wasmtime.Trap as t:
        msg = str(t).lower()
        if "fuel" in msg:
            exceeded = "fuel"
        elif "interrupt" in msg or "epoch" in msg:
            exceeded = "epoch"
        else:
            exit_code = 1
    finally:
        timer.cancel()

    fuel_after = 0
    try:
        if hasattr(store, "get_fuel"):
            # wasmtime >= 14: get_fuel() returns REMAINING fuel; consumed
            # is the policy budget minus what's left.
            remaining = store.get_fuel() or 0
            fuel_after = max(0, policy.fuel - remaining)
        else:  # pragma: no cover - older wasmtime
            fuel_after = store.fuel_consumed() or 0
    except Exception:
        pass

    return RunResult(
        exit_code=exit_code,
        stdout="",
        stderr="",
        fuel_consumed=fuel_after,
        exceeded_limit=exceeded,
        failed_imports=[],
    )
