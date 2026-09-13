"""
backend/security/policy_compiler.py
=====================================

Sprint 16 / Item B4 — Policy DSL → eBPF LSM rule compiler.

What this is
------------

From the 80-problem agent-era catalog, B4:
  "Seccomp/Landlock not designed for agent semantics — no notion of
   'this tool, this URL, this filename pattern' the way agent policies
   express intent."

This module is a small **declarative DSL** (YAML/JSON-shaped Python
dicts) for expressing per-tool, per-URL, per-filename-pattern policy.
It compiles to the CapGateRule format defined in B3
(backend/security/lsm_cap_gate.py).

The DSL is intentionally minimal — the kind of policy an operator can
write by hand without learning a new programming model. Each policy
entry maps to exactly one CapGateRule.

DSL shape
---------

    policy:
      - capability: NET_RAW
        match:
          tool: "fetch_*"           # fnmatch over tool_call_name
          process_comm: "agent-*"   # fnmatch over comm
          exe_prefix: "/opt/vos3/"
          taint_at_most: UNTRUSTED  # mirror C7 TaintLabel name
          rate_limit:
            max_calls: 100
            window_ms: 1000
        decision: ALLOW
        reason: "agent_fetch_tool_allowed_net_raw"
        description: "agents may issue raw network reads for fetch tools"

Public surface
--------------

  compile_policy(spec) -> list[CapGateRule]
  load_policy_yaml(path_or_str) -> list[CapGateRule]   (uses yaml.safe_load)
  PolicyCompileError                                      (raised on bad spec)

Honest scope ceiling
--------------------

  - Compiler is stateless. Each call returns a fresh rule list; caller
    is responsible for adding the rules into a CapGate instance.
  - Validation is strict — unknown keys, missing required fields, or
    invalid capability/decision strings raise PolicyCompileError with
    a path pointing at the offending entry.
  - YAML support is OPTIONAL (only imported when load_policy_yaml is
    called). Tests use the dict-shape directly to avoid the dep.
  - Rate-limit predicate from B3 is used as-is; the eBPF compiler
    that emits actual BPF for these rules is a Sprint 17 follow-up.
"""

from __future__ import annotations

import importlib.util
from typing import Any

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PolicyCompileError(ValueError):
    """Raised when the input spec is invalid. .path identifies where."""

    def __init__(self, message: str, path: str = ""):
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


# ---------------------------------------------------------------------------
# Reference back to B3 module — loaded lazily to avoid an import cycle if
# B3 is later refactored.
# ---------------------------------------------------------------------------


_lcg_cached = None


def _load_lcg():
    """Return the lsm_cap_gate module. Cached after first load to keep
    CapGateRule/LinuxCapability identities stable across compile calls."""
    global _lcg_cached
    if _lcg_cached is not None:
        return _lcg_cached
    import sys as _sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    lcg_path = repo_root / "backend" / "security" / "lsm_cap_gate.py"
    module_name = "vos3_lcg_from_compiler"
    if module_name in _sys.modules:
        _lcg_cached = _sys.modules[module_name]
        return _lcg_cached
    spec = importlib.util.spec_from_file_location(module_name, lcg_path)
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec_module so @dataclass can resolve cls.__module__.
    _sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    _lcg_cached = mod
    return mod


# ---------------------------------------------------------------------------
# Capability + decision + taint-label name maps
# ---------------------------------------------------------------------------


_VALID_KEYS_AT_ENTRY = {"capability", "match", "decision", "reason", "description"}
_VALID_KEYS_AT_MATCH = {
    "tool",
    "process_comm",
    "exe_prefix",
    "taint_at_most",
    "rate_limit",
}
_VALID_KEYS_AT_RATE = {"max_calls", "window_ms"}

# Mirror of C7 TaintLabel — must stay in sync.
_TAINT_NAME_TO_INT = {
    "PUBLIC": 0,
    "UNTRUSTED": 1,
    "SECRET": 2,
    "TOXIC": 3,
}


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def compile_policy(spec: dict[str, Any]):
    """Compile a policy spec into a list of CapGateRule instances.

    Args:
        spec: dict with shape {"policy": [<entry>, <entry>, ...]}.

    Returns:
        list[CapGateRule]

    Raises:
        PolicyCompileError on any structural / value error, with .path
        pointing at the offending entry.
    """
    if not isinstance(spec, dict):
        raise PolicyCompileError("spec must be a dict")
    if "policy" not in spec:
        raise PolicyCompileError("spec missing required key 'policy'")
    policy = spec["policy"]
    if not isinstance(policy, list):
        raise PolicyCompileError("spec.policy must be a list", path="policy")

    lcg = _load_lcg()
    rules = []
    for idx, entry in enumerate(policy):
        path = f"policy[{idx}]"
        rules.append(_compile_entry(entry, path=path, lcg=lcg))
    return rules


def _compile_entry(entry: Any, *, path: str, lcg):
    if not isinstance(entry, dict):
        raise PolicyCompileError("policy entry must be a dict", path=path)

    unknown = set(entry.keys()) - _VALID_KEYS_AT_ENTRY
    if unknown:
        raise PolicyCompileError(f"unknown keys: {sorted(unknown)}", path=path)
    for required in ("capability", "decision", "reason"):
        if required not in entry:
            raise PolicyCompileError(f"missing required key {required!r}", path=path)

    # capability
    cap_name = entry["capability"]
    if not isinstance(cap_name, str):
        raise PolicyCompileError("capability must be string", path=f"{path}.capability")
    try:
        capability = lcg.LinuxCapability[cap_name]
    except KeyError:
        raise PolicyCompileError(
            f"unknown capability {cap_name!r}",
            path=f"{path}.capability",
        )

    # decision
    decision_name = entry["decision"]
    if not isinstance(decision_name, str):
        raise PolicyCompileError("decision must be string", path=f"{path}.decision")
    try:
        decision = lcg.CapDecision[decision_name]
    except KeyError:
        raise PolicyCompileError(
            f"unknown decision {decision_name!r} (expected ALLOW or DENY)",
            path=f"{path}.decision",
        )

    # reason
    reason = entry["reason"]
    if not isinstance(reason, str) or not reason:
        raise PolicyCompileError(
            "reason must be non-empty string", path=f"{path}.reason"
        )
    description = entry.get("description", "")
    if not isinstance(description, str):
        raise PolicyCompileError(
            "description must be string", path=f"{path}.description"
        )

    # match — build the composite predicate
    match_spec = entry.get("match")
    predicate = _compile_match(match_spec, path=f"{path}.match", lcg=lcg)

    return lcg.CapGateRule(
        capability=capability,
        predicate=predicate,
        decision=decision,
        reason=reason,
        description=description,
    )


def _compile_match(match_spec: Any, *, path: str, lcg):
    """Returns a composite predicate that AND's together all configured
    match clauses. Empty match spec → predicate_always."""
    if match_spec is None or (isinstance(match_spec, dict) and not match_spec):
        return lcg.predicate_always()

    if not isinstance(match_spec, dict):
        raise PolicyCompileError("match must be a dict", path=path)

    unknown = set(match_spec.keys()) - _VALID_KEYS_AT_MATCH
    if unknown:
        raise PolicyCompileError(f"unknown match keys: {sorted(unknown)}", path=path)

    predicates = []

    if "tool" in match_spec:
        tool_pattern = match_spec["tool"]
        if not isinstance(tool_pattern, str) or not tool_pattern:
            raise PolicyCompileError(
                "tool must be non-empty string", path=f"{path}.tool"
            )
        # Convert fnmatch glob to regex anchor pattern.
        import fnmatch

        regex = fnmatch.translate(tool_pattern)
        predicates.append(lcg.predicate_tool_call_match(tool_regex=regex))

    if "process_comm" in match_spec or "exe_prefix" in match_spec:
        comm_glob = match_spec.get("process_comm")
        exe_prefix = match_spec.get("exe_prefix")
        if comm_glob is not None and not isinstance(comm_glob, str):
            raise PolicyCompileError(
                "process_comm must be string", path=f"{path}.process_comm"
            )
        if exe_prefix is not None and not isinstance(exe_prefix, str):
            raise PolicyCompileError(
                "exe_prefix must be string", path=f"{path}.exe_prefix"
            )
        predicates.append(
            lcg.predicate_process_match(comm_glob=comm_glob, exe_prefix=exe_prefix)
        )

    if "taint_at_most" in match_spec:
        taint_name = match_spec["taint_at_most"]
        if not isinstance(taint_name, str):
            raise PolicyCompileError(
                "taint_at_most must be string", path=f"{path}.taint_at_most"
            )
        if taint_name not in _TAINT_NAME_TO_INT:
            raise PolicyCompileError(
                f"unknown taint label {taint_name!r}; valid: "
                f"{sorted(_TAINT_NAME_TO_INT)}",
                path=f"{path}.taint_at_most",
            )
        predicates.append(
            lcg.predicate_taint_at_most(max_label=_TAINT_NAME_TO_INT[taint_name])
        )

    if "rate_limit" in match_spec:
        rl = match_spec["rate_limit"]
        if not isinstance(rl, dict):
            raise PolicyCompileError(
                "rate_limit must be a dict", path=f"{path}.rate_limit"
            )
        unknown_rl = set(rl.keys()) - _VALID_KEYS_AT_RATE
        if unknown_rl:
            raise PolicyCompileError(
                f"unknown rate_limit keys: {sorted(unknown_rl)}",
                path=f"{path}.rate_limit",
            )
        if "max_calls" not in rl or "window_ms" not in rl:
            raise PolicyCompileError(
                "rate_limit requires max_calls + window_ms",
                path=f"{path}.rate_limit",
            )
        try:
            predicates.append(
                lcg.predicate_rate_limit(
                    max_calls=int(rl["max_calls"]),
                    window_ms=int(rl["window_ms"]),
                )
            )
        except ValueError as exc:
            raise PolicyCompileError(str(exc), path=f"{path}.rate_limit")

    if not predicates:
        return lcg.predicate_always()

    # Composite: AND all predicates together.
    def _and(ctx):
        return all(p(ctx) for p in predicates)

    return _and


# ---------------------------------------------------------------------------
# Optional YAML loader
# ---------------------------------------------------------------------------


def load_policy_yaml(yaml_str_or_path):
    """Load + compile a policy from YAML. Requires the optional
    PyYAML dependency."""
    try:
        import yaml  # type: ignore
    except ImportError:
        raise PolicyCompileError(
            "PyYAML is required for load_policy_yaml — install with "
            "`pip install pyyaml`"
        )
    if hasattr(yaml_str_or_path, "read"):
        spec = yaml.safe_load(yaml_str_or_path)
    elif isinstance(yaml_str_or_path, str) and "\n" not in yaml_str_or_path:
        # Treat as a path.
        with open(yaml_str_or_path, encoding="utf-8") as fh:
            spec = yaml.safe_load(fh)
    else:
        spec = yaml.safe_load(yaml_str_or_path)
    return compile_policy(spec)


__all__ = [
    "PolicyCompileError",
    "compile_policy",
    "load_policy_yaml",
]
