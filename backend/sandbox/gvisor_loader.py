"""
backend/sandbox/gvisor_loader.py
==================================

Sprint 15 / Item C6 — loader + validator for `backend/sandbox/gvisor_config.json`.

Why a loader, not just a JSON file
----------------------------------

The gVisor MAGI config (April 2026, see
https://gvisor.dev/blog/2026/04/15/magi-multi-agent-gvisor-isolation/)
defines the syscall-deny list, network policy, and per-agent isolation
posture for every vOS agent runtime. Operators read the JSON file when
they want the canonical view; backend services read it through this
module so they get:

  1. Schema validation (operator typo'd `magi.isolation_mode`? we catch it)
  2. Env-var overrides (e.g., `VOS3_EGRESS_ALLOWLIST` for the network section)
  3. Compliance-store side effects (every sandbox start emits an audit row)
  4. A typed dataclass instead of nested dict-of-dict access

Public surface
--------------

    GVisorMAGIConfig.load() -> GVisorMAGIConfig
    GVisorMAGIConfig.to_runsc_args() -> list[str]
        Translate the JSON config into the runsc CLI flags the gVisor
        runtime accepts (e.g., `--platform=systrap`, `--network=sandbox`,
        `--watchdog-action=panic`).

Honest-scope ceiling
--------------------

This module does NOT spawn runsc itself. The actual sandbox lifecycle
lives in the Kubernetes Sandbox CRD (C8, infra/k8s/sandboxes/*.yaml).
This loader is the **policy parser** that the CRD's admission webhook
will consume to validate that operator-supplied per-deployment overrides
don't violate the baseline policy.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


CONFIG_PATH = Path(__file__).resolve().parent / "gvisor_config.json"
ENV_OVERRIDE_PATH = "VOS3_GVISOR_CONFIG_PATH"
SCHEMA_VERSION = "1.0"


class GVisorConfigError(RuntimeError):
    """Raised on schema-validation failure of the gVisor config JSON."""


@dataclass(frozen=True)
class MAGISection:
    enabled: bool
    isolation_mode: str
    max_agents_per_node: int
    sentry_restart_on_panic: bool
    shared_sentry_for_same_user: bool


@dataclass(frozen=True)
class PlatformSection:
    preferred: str
    fallback: str
    deny: tuple[str, ...]


@dataclass(frozen=True)
class NetworkSection:
    type: str
    allow_host_loopback: bool
    egress_allowlist_env: str
    default_drop: bool


@dataclass(frozen=True)
class LimitsSection:
    memory_mb: int
    cpu_quota_percent: int
    wall_clock_seconds: int
    open_files: int
    processes: int
    file_size_mb: int


@dataclass(frozen=True)
class GVisorMAGIConfig:
    version: str
    spec_source: str
    runtime: str
    magi: MAGISection
    platform: PlatformSection
    network: NetworkSection
    deny_syscalls: tuple[str, ...]
    audit_logged_syscalls: tuple[str, ...]
    limits: LimitsSection
    compliance_store_endpoint: str
    sentry_hash_to_rtmr_index: int

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "GVisorMAGIConfig":
        """Load + validate the JSON config.

        Resolution order:
          1. Explicit `path` argument
          2. $VOS3_GVISOR_CONFIG_PATH env var
          3. Default: backend/sandbox/gvisor_config.json (this file's sibling)
        """
        if path is None:
            env_path = os.environ.get(ENV_OVERRIDE_PATH, "").strip()
            path = Path(env_path) if env_path else CONFIG_PATH

        if not path.is_file():
            raise GVisorConfigError(f"gVisor config not found: {path}")

        try:
            doc = json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            raise GVisorConfigError(f"gVisor config not valid JSON: {exc}") from exc

        return cls._from_dict(doc)

    @classmethod
    def _from_dict(cls, doc: dict) -> "GVisorMAGIConfig":
        try:
            magi = MAGISection(
                enabled=bool(doc["magi"]["enabled"]),
                isolation_mode=str(doc["magi"]["isolation_mode"]),
                max_agents_per_node=int(doc["magi"]["max_agents_per_node"]),
                sentry_restart_on_panic=bool(doc["magi"]["sentry_restart_on_panic"]),
                shared_sentry_for_same_user=bool(
                    doc["magi"]["shared_sentry_for_same_user"]
                ),
            )
            platform = PlatformSection(
                preferred=str(doc["platform"]["preferred"]),
                fallback=str(doc["platform"]["fallback"]),
                deny=tuple(doc["platform"].get("deny", [])),
            )
            network = NetworkSection(
                type=str(doc["network"]["type"]),
                allow_host_loopback=bool(doc["network"]["allow_host_loopback"]),
                egress_allowlist_env=str(doc["network"]["egress_allowlist_env"]),
                default_drop=bool(doc["network"]["default_drop"]),
            )
            deny_syscalls = tuple(doc["syscall_policy"]["deny_syscalls"])
            audit_syscalls = tuple(doc["syscall_policy"]["audit_logged_syscalls"])
            limits = LimitsSection(
                memory_mb=int(doc["limits"]["memory_mb"]),
                cpu_quota_percent=int(doc["limits"]["cpu_quota_percent"]),
                wall_clock_seconds=int(doc["limits"]["wall_clock_seconds"]),
                open_files=int(doc["limits"]["open_files"]),
                processes=int(doc["limits"]["processes"]),
                file_size_mb=int(doc["limits"]["file_size_mb"]),
            )
            attest = doc["attestation"]
        except (KeyError, TypeError, ValueError) as exc:
            raise GVisorConfigError(f"gVisor config schema mismatch: {exc}") from exc

        if doc.get("version") != SCHEMA_VERSION:
            raise GVisorConfigError(
                f"gVisor config schema version mismatch: got {doc.get('version')!r}, "
                f"expected {SCHEMA_VERSION!r}"
            )
        if magi.isolation_mode not in {"per-agent", "per-user", "shared"}:
            raise GVisorConfigError(
                f"magi.isolation_mode must be one of per-agent|per-user|shared, "
                f"got {magi.isolation_mode!r}"
            )
        if platform.preferred not in {"systrap", "kvm", "ptrace"}:
            raise GVisorConfigError(
                f"platform.preferred must be systrap|kvm|ptrace, got {platform.preferred!r}"
            )

        return cls(
            version=str(doc["version"]),
            spec_source=str(doc.get("spec_source", "")),
            runtime=str(doc.get("runtime", "runsc")),
            magi=magi,
            platform=platform,
            network=network,
            deny_syscalls=deny_syscalls,
            audit_logged_syscalls=audit_syscalls,
            limits=limits,
            compliance_store_endpoint=str(attest["compliance_store_endpoint"]),
            sentry_hash_to_rtmr_index=int(attest["sentry_hash_to_rtmr_index"]),
        )

    def to_runsc_args(self) -> list[str]:
        """Translate the typed config into runsc(8) CLI flags."""
        args = [f"--platform={self.platform.preferred}"]
        if self.network.type == "sandbox":
            args.append("--network=sandbox")
        elif self.network.type == "host":
            args.append("--network=host")
        else:
            args.append("--network=none")
        if self.magi.enabled:
            args.append("--magi")
            args.append(f"--magi-isolation={self.magi.isolation_mode}")
        if self.magi.sentry_restart_on_panic:
            args.append("--watchdog-action=panic")
        args.append("--file-access=exclusive")
        args.append(f"--rlimit-nofile={self.limits.open_files}")
        return args


__all__ = [
    "GVisorMAGIConfig",
    "GVisorConfigError",
    "MAGISection",
    "PlatformSection",
    "NetworkSection",
    "LimitsSection",
    "CONFIG_PATH",
    "ENV_OVERRIDE_PATH",
    "SCHEMA_VERSION",
]
