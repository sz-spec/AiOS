# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
Wasmtime engine wrapper. One Engine instance per process; Stores are
per-invocation. Fuel + epoch interruption are enabled at engine level
so individual modules can trigger them via the Policy.
"""

from __future__ import annotations

import threading

_engine_lock = threading.Lock()
_engine_singleton = None


def _make_config():
    """Build the canonical Wasmtime config for vOS sandbox use."""
    import wasmtime  # type: ignore[import-not-found]

    cfg = wasmtime.Config()
    # Fuel — strict bound on instructions executed per invocation.
    cfg.consume_fuel = True
    # Epoch interruption — wall-clock kill (separate from fuel).
    cfg.epoch_interruption = True
    # WASI Preview 2 + component model are spec-aligned.
    cfg.wasm_component_model = True
    return cfg


class Engine:
    """Process-wide Wasmtime engine singleton.

    Calling `Engine()` always returns the same underlying engine
    instance. This matches Wasmtime's recommendation: engines are
    expensive to construct, stores are cheap, and reusing one engine
    across stores is the canonical pattern.
    """

    def __new__(cls):
        global _engine_singleton
        with _engine_lock:
            if _engine_singleton is None:
                import wasmtime  # type: ignore[import-not-found]

                _engine_singleton = super().__new__(cls)
                _engine_singleton._engine = wasmtime.Engine(_make_config())
        return _engine_singleton

    @property
    def engine(self):
        """Underlying wasmtime.Engine — used by runner.run_module()."""
        return self._engine
