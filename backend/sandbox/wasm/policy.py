# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
Sandbox execution policy — fuel, memory, wall-clock budget.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    """Per-invocation resource policy.

    fuel:           max instructions Wasmtime will execute before trapping.
                    Roughly 1 unit per WASM instruction; 1_000_000 is
                    enough for a small computation, small enough that
                    `runaway.wat` (infinite loop) trips it in <100 ms.
    memory_pages:   maximum linear memory pages (1 page = 64 KiB).
    wall_clock_ms:  hard ceiling on real time. Implemented via
                    epoch_interruption. Independent of fuel because a
                    module can sleep (host-call) without consuming fuel.
    """

    fuel: int = 1_000_000
    memory_pages: int = 64
    wall_clock_ms: int = 5_000

    def __post_init__(self):
        if self.fuel <= 0:
            raise ValueError("Policy.fuel must be > 0")
        if self.memory_pages <= 0:
            raise ValueError("Policy.memory_pages must be > 0")
        if self.wall_clock_ms <= 0:
            raise ValueError("Policy.wall_clock_ms must be > 0")
