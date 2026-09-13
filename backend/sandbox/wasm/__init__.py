# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
backend.sandbox.wasm — Phase-1 WASM agent isolation POC.

Implements the agent-execution sandbox described in the Product
Specification §2.4: Wasmtime-backed isolation with fuel + memory +
wall-clock limits, defense-in-depth on top of (not replacing) the
existing rlimit-based sandbox.

Pinned to wasmtime==44.0.2 per migration_plan.md (May-2026 Spectre-V4
mitigation). Phase-1 scope is limited to: engine bring-up, policy
enforcement, three POC fixtures (hello, runaway, capability-rejected).
Phase 2 (call-site identification) and beyond are roadmap items in
migration_plan.md.
"""

from .engine import Engine
from .policy import Policy
from .runner import run_module, RunResult

__all__ = ["Engine", "Policy", "run_module", "RunResult"]
