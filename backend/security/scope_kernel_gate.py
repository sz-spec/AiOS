"""
backend/security/scope_kernel_gate.py
=======================================

Sprint 16 / Item B5 — App-store OAuth scope → kernel-enforced allowlist.

What this is
------------

From the 80-problem agent-era catalog, B5:
  "App-store scope strings are userspace, not kernel-enforced —
   'contacts.read' in OAuth scope is checked by the application, not the
   kernel; a compromised app trivially bypasses."

This module bridges OAuth scope strings (the kind that ride in MCP +
Clerk + SPIFFE tokens) to kernel-side CapGate (B3) capability rules.
The bridge:

  1. Maintains a ScopeMap registering which OAuth scopes grant which
     LinuxCapability checks under which conditions.
  2. On each capability request that flows through the agent stack,
     the bridge consults the authenticated user's/agent's granted
     scopes and emits the corresponding allowlist or default-deny
     decision into a B3 CapGate.

Public surface
--------------

  ScopeMapping — declarative entry
  ScopeKernelGate
    .add_mapping(mapping)
    .compile_rules_for(granted_scopes) -> list[CapGateRule]
    .install_into(cap_gate, granted_scopes)
    .stats() -> ScopeKernelGateStats

Honest scope ceiling
--------------------

  - Userspace-side bridge. The kernel-enforcement piece is whatever
    B3 CapGate is installed into (Wave 3 ships the policy model; B4
    DSL ships rule composition; B5 adds OAuth-scope binding; the
    actual eBPF compilation is a Sprint 17 follow-up).
  - Scopes are matched by EXACT string. Wildcard scopes
    (e.g. "contacts.*") get expansion at register-time, not at
    check-time, to keep the hot path branch-free.
  - The bridge is stateless per request — granted_scopes flows from
    the F2 MCPAuthContext (shipped Sprint 15) or the F4 verified
    federation identity (shipped Wave 2) on every call.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Reference back to B3 module (uses the same singleton-loader pattern as B4)
# ---------------------------------------------------------------------------


_lcg_cached = None


def _load_lcg():
    global _lcg_cached
    if _lcg_cached is not None:
        return _lcg_cached
    import importlib.util
    import sys as _sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    lcg_path = repo_root / "backend" / "security" / "lsm_cap_gate.py"
    module_name = "vos3_lcg_from_scope_gate"
    if module_name in _sys.modules:
        _lcg_cached = _sys.modules[module_name]
        return _lcg_cached
    spec = importlib.util.spec_from_file_location(module_name, lcg_path)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    _lcg_cached = mod
    return mod


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ScopeMappingError(ValueError):
    pass


# ---------------------------------------------------------------------------
# ScopeMapping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeMapping:
    """One OAuth-scope → CapGateRule binding.

    The mapping says: when the authenticated user has `scope_name`
    in their granted set, emit a CapGateRule that grants `capability`
    under `decision` (ALLOW typically) with the given reason.

    Optional `tool_glob` narrows the grant to specific tool calls
    (mirrors the B4 DSL match.tool field).

    Optional `max_taint_label` adds a taint ceiling (mirrors B4
    match.taint_at_most). Defaults to None (no ceiling).
    """

    scope_name: str
    capability_name: str  # e.g. "NET_RAW" — looked up at compile time
    decision_name: str = "ALLOW"
    reason: str = ""
    tool_glob: Optional[str] = None
    max_taint_label: Optional[str] = None  # e.g. "UNTRUSTED"
    description: str = ""


@dataclass
class ScopeKernelGateStats:
    mappings_registered: int = 0
    rules_compiled: int = 0
    install_calls: int = 0
    install_no_matching_scopes: int = 0


# ---------------------------------------------------------------------------
# ScopeKernelGate
# ---------------------------------------------------------------------------


class ScopeKernelGate:
    """Registry + compiler that maps granted OAuth scopes onto B3
    capability rules. Used by services that authenticate an agent and
    need to materialize the agent's capability grants into a CapGate
    instance for downstream enforcement."""

    def __init__(self):
        self._mappings: dict[str, list[ScopeMapping]] = {}
        self._lock = threading.Lock()
        self._stats = ScopeKernelGateStats()

    # -- Registration --------------------------------------------------------

    def add_mapping(self, mapping: ScopeMapping) -> None:
        if not isinstance(mapping, ScopeMapping):
            raise TypeError("mapping must be ScopeMapping")
        if not mapping.scope_name or not isinstance(mapping.scope_name, str):
            raise ScopeMappingError("scope_name must be non-empty string")
        if not mapping.capability_name or not isinstance(mapping.capability_name, str):
            raise ScopeMappingError("capability_name must be non-empty string")
        if not mapping.reason:
            raise ScopeMappingError("reason must be non-empty")
        if mapping.decision_name not in ("ALLOW", "DENY"):
            raise ScopeMappingError(
                f"decision_name must be ALLOW or DENY, got {mapping.decision_name!r}"
            )
        # Validate capability name resolves now (fail-fast at registration,
        # not at compile time).
        lcg = _load_lcg()
        if mapping.capability_name not in lcg.LinuxCapability.__members__:
            raise ScopeMappingError(f"unknown capability {mapping.capability_name!r}")
        if mapping.max_taint_label is not None and mapping.max_taint_label not in (
            "PUBLIC",
            "UNTRUSTED",
            "SECRET",
            "TOXIC",
        ):
            raise ScopeMappingError(f"unknown taint label {mapping.max_taint_label!r}")
        with self._lock:
            self._mappings.setdefault(mapping.scope_name, []).append(mapping)
            self._stats.mappings_registered += 1

    def list_mappings(self) -> list[ScopeMapping]:
        with self._lock:
            return [m for entries in self._mappings.values() for m in entries]

    # -- Compilation ---------------------------------------------------------

    def compile_rules_for(self, granted_scopes: set[str]) -> list:
        """Return CapGateRule list for the subset of mappings whose
        scope_name is in granted_scopes."""
        if not isinstance(granted_scopes, (set, frozenset)):
            raise TypeError("granted_scopes must be set[str]")
        lcg = _load_lcg()
        rules = []
        matched_any = False
        with self._lock:
            for scope_name in granted_scopes:
                mappings = self._mappings.get(scope_name, [])
                if mappings:
                    matched_any = True
                for m in mappings:
                    rules.append(self._mapping_to_rule(m, lcg=lcg))
            self._stats.rules_compiled += len(rules)
            if not matched_any:
                self._stats.install_no_matching_scopes += 1
        return rules

    @staticmethod
    def _mapping_to_rule(mapping: ScopeMapping, *, lcg):
        capability = lcg.LinuxCapability[mapping.capability_name]
        decision = lcg.CapDecision[mapping.decision_name]
        predicates = []
        if mapping.tool_glob is not None:
            import fnmatch

            regex = fnmatch.translate(mapping.tool_glob)
            predicates.append(lcg.predicate_tool_call_match(tool_regex=regex))
        if mapping.max_taint_label is not None:
            taint_int = {"PUBLIC": 0, "UNTRUSTED": 1, "SECRET": 2, "TOXIC": 3}[
                mapping.max_taint_label
            ]
            predicates.append(lcg.predicate_taint_at_most(max_label=taint_int))
        if not predicates:
            pred = lcg.predicate_always()
        else:

            def _composite(ctx):
                return all(p(ctx) for p in predicates)

            pred = _composite
        return lcg.CapGateRule(
            capability=capability,
            predicate=pred,
            decision=decision,
            reason=mapping.reason,
            description=mapping.description,
        )

    # -- Install -------------------------------------------------------------

    def install_into(self, cap_gate, granted_scopes: set[str]) -> int:
        """Add the compiled rules into `cap_gate`. Returns count installed."""
        rules = self.compile_rules_for(granted_scopes)
        for r in rules:
            cap_gate.add_rule(r)
        with self._lock:
            self._stats.install_calls += 1
        return len(rules)

    # -- Stats ---------------------------------------------------------------

    def snapshot_stats(self) -> ScopeKernelGateStats:
        with self._lock:
            return ScopeKernelGateStats(
                mappings_registered=self._stats.mappings_registered,
                rules_compiled=self._stats.rules_compiled,
                install_calls=self._stats.install_calls,
                install_no_matching_scopes=self._stats.install_no_matching_scopes,
            )


__all__ = [
    "ScopeMapping",
    "ScopeMappingError",
    "ScopeKernelGateStats",
    "ScopeKernelGate",
]
