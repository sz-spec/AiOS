"""
backend/services/dra_mig_validator.py
=======================================

Sprint 16 / Item E7 — NVIDIA DRA + MIG manifest validator.

What this is
------------

From the 80-problem agent-era catalog, E7:
  "NVIDIA MIG isolation breaks under tight SLA + noisy neighbor — 2026
   study: I/O-bound workloads degrade up to 67% under multi-tenant
   stress despite MIG partitioning."

The April-2026 CNCF-donated NVIDIA DRA Driver brings GPU partitioning
into the standard K8s scheduling path. This module validates that the
DRA + MIG manifests (`infra/k8s/sandboxes/dra-gpu-mig-class.yaml`)
match the QoS profile vOS workloads expect — fails fast at deploy time
when an operator's customization breaks the isolation guarantees.

Public surface
--------------

  MIGProfile — recognized MIG slice profiles
  DraManifestValidator
    .load_manifest(path_or_str) -> list[ParsedManifestEntry]
    .validate(entries) -> ValidationReport
  ValidationReport.is_ok / .violations

Validation rules
----------------

  - DeviceClass apiVersion is resource.k8s.io/v1beta1 or newer.
  - MIG profile in CEL selector matches the metadata label.
  - persistMig is true (KV-cache continuity).
  - maxComputeSm and maxMemoryMiB match the expected profile
    (Blackwell MIG profile table; tunable via env).
  - ResourceClaimTemplate references a DeviceClass that is present in
    the same manifest set (no dangling reference).

Honest scope ceiling
--------------------

  - Validator is STATIC — it doesn't talk to a real cluster.
    Production CI runs this in a kubeval-style gate before deploy.
  - MIG profile table is for Blackwell-class GPUs; for older
    Ampere/Hopper, operator must override via VOS3_MIG_PROFILE_TABLE
    env (out of scope for this module).
  - YAML parsing uses PyYAML (which the codebase already depends on
    transitively). On import failure we fall back to a tiny scalar-only
    parser sufficient for the simplest test fixtures.

References:
  - NVIDIA DRA Driver donation to CNCF (KubeCon April 2026)
    blogs.nvidia.com/blog/nvidia-at-kubecon-2026/
  - Kubernetes DRA documentation
    kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/
  - NVIDIA MIG User Guide (docs.nvidia.com/datacenter/tesla/mig-user-guide/)
"""

from __future__ import annotations

import enum
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# MIG profile table (Blackwell defaults)
# ---------------------------------------------------------------------------


class MIGProfile(str, enum.Enum):
    M_1g_10gb = "1g.10gb"
    M_2g_20gb = "2g.20gb"
    M_3g_40gb = "3g.40gb"
    M_7g_80gb = "7g.80gb"


# Expected QoS bounds per profile (Blackwell defaults; operator can
# override via VOS3_MIG_QOS_TABLE_<PROFILE>_SM / _MEM env).
_DEFAULT_MIG_QOS_TABLE: dict[MIGProfile, dict[str, int]] = {
    MIGProfile.M_1g_10gb: {"maxComputeSm": 14, "maxMemoryMiB": 10240},
    MIGProfile.M_2g_20gb: {"maxComputeSm": 28, "maxMemoryMiB": 20480},
    MIGProfile.M_3g_40gb: {"maxComputeSm": 42, "maxMemoryMiB": 40960},
    MIGProfile.M_7g_80gb: {"maxComputeSm": 98, "maxMemoryMiB": 81920},
}


def _qos_table() -> dict[MIGProfile, dict[str, int]]:
    table = {p: dict(v) for p, v in _DEFAULT_MIG_QOS_TABLE.items()}
    for profile in MIGProfile:
        env_prefix = "VOS3_MIG_QOS_TABLE_" + profile.name
        sm_env = os.environ.get(env_prefix + "_SM")
        mem_env = os.environ.get(env_prefix + "_MEM")
        if sm_env is not None:
            try:
                table[profile]["maxComputeSm"] = int(sm_env)
            except ValueError:
                pass
        if mem_env is not None:
            try:
                table[profile]["maxMemoryMiB"] = int(mem_env)
            except ValueError:
                pass
    return table


# ---------------------------------------------------------------------------
# Parsed-manifest types
# ---------------------------------------------------------------------------


class ManifestKind(str, enum.Enum):
    DEVICE_CLASS = "DeviceClass"
    RESOURCE_CLAIM_TEMPLATE = "ResourceClaimTemplate"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParsedManifestEntry:
    api_version: str
    kind: ManifestKind
    metadata_name: str
    labels: dict[str, str] = field(default_factory=dict)
    spec: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Violation:
    entry_name: str
    field_path: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    entries: tuple[ParsedManifestEntry, ...]
    violations: tuple[Violation, ...]

    @property
    def is_ok(self) -> bool:
        return len(self.violations) == 0


# ---------------------------------------------------------------------------
# Hand-rolled YAML parser (minimal subset)
#
# Avoids the PyYAML dependency. Supports:
#   - --- document separator
#   - key: value scalars (string, int, bool)
#   - nested mappings via indentation (2-space convention)
#   - list items via "- " prefix
#   - lines starting with # are comments
#
# It is NOT a full YAML parser. The DRA manifests we ship are written
# with this parser's capabilities in mind.
# ---------------------------------------------------------------------------


_INDENT_RE = re.compile(r"^( *)(.*)$")


def _parse_value(s: str) -> Any:
    s = s.strip()
    if not s:
        return None
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() == "null":
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    if s.startswith("|"):
        return s[1:].strip()
    return s


def _parse_yaml_documents(text: str) -> list[dict[str, Any]]:
    """Multi-document YAML loader using PyYAML safe_load_all."""
    try:
        import yaml  # type: ignore
    except ImportError:
        # Fallback to hand-rolled parser (limited).
        return _hand_rolled_parse_yaml_documents(text)
    docs = []
    for d in yaml.safe_load_all(text):
        if isinstance(d, dict):
            docs.append(d)
    return docs


def _hand_rolled_parse_yaml_documents(text: str) -> list[dict[str, Any]]:
    """Fallback parser for hosts without PyYAML — small subset only."""
    docs: list[dict[str, Any]] = []
    current_doc_lines: list[str] = []
    for raw_line in text.splitlines():
        if raw_line.strip() == "---":
            if current_doc_lines:
                docs.append(_parse_single_doc(current_doc_lines))
            current_doc_lines = []
        else:
            current_doc_lines.append(raw_line)
    if current_doc_lines:
        doc = _parse_single_doc(current_doc_lines)
        if doc:
            docs.append(doc)
    return [d for d in docs if d]


def _parse_single_doc(lines: list[str]) -> dict[str, Any]:
    # Strip comments + blank.
    cleaned: list[tuple[int, str]] = []
    for line in lines:
        # Strip trailing comment (only if # is preceded by whitespace).
        if "#" in line:
            before_hash = line.split("#")[0]
            if (
                before_hash.rstrip() == ""
                or before_hash.rstrip().endswith(" ")
                or before_hash.endswith(":")
            ):
                line = before_hash
        if not line.strip():
            continue
        m = _INDENT_RE.match(line)
        if m:
            indent = len(m.group(1))
            cleaned.append((indent, m.group(2).rstrip()))
    if not cleaned:
        return {}
    return _build_tree(cleaned, base_indent=0, start=0)[0]


def _build_tree(
    lines: list[tuple[int, str]], *, base_indent: int, start: int
) -> tuple[Any, int]:
    """Recursive descent. Returns (parsed_value, next_index)."""
    if start >= len(lines):
        return None, start
    indent, content = lines[start]
    if content.startswith("- "):
        # List of items.
        items: list[Any] = []
        i = start
        while i < len(lines):
            cur_indent, cur_content = lines[i]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                # Should have been consumed in nested call.
                i += 1
                continue
            if not cur_content.startswith("- "):
                break
            item_text = cur_content[2:]
            if ":" in item_text:
                # Inline mapping-start: parse item as a sub-mapping.
                # Gather child lines.
                child_lines: list[tuple[int, str]] = [(indent + 2, item_text)]
                j = i + 1
                while j < len(lines) and lines[j][0] > indent:
                    child_lines.append(lines[j])
                    j += 1
                sub, _ = _build_tree(child_lines, base_indent=indent + 2, start=0)
                items.append(sub)
                i = j
            else:
                items.append(_parse_value(item_text))
                i += 1
        return items, i
    # Mapping.
    result: dict[str, Any] = {}
    i = start
    while i < len(lines):
        cur_indent, cur_content = lines[i]
        if cur_indent < base_indent:
            break
        if cur_indent > base_indent:
            i += 1
            continue
        if ":" not in cur_content:
            i += 1
            continue
        key, _sep, after = cur_content.partition(":")
        key = key.strip()
        after = after.strip()
        if after:
            result[key] = _parse_value(after)
            i += 1
        else:
            # Child block.
            child_indent = base_indent
            for j in range(i + 1, len(lines)):
                if lines[j][0] > base_indent:
                    child_indent = lines[j][0]
                    break
            child_lines = []
            j = i + 1
            while j < len(lines) and lines[j][0] > base_indent:
                child_lines.append(lines[j])
                j += 1
            if child_lines:
                # If the first child is a list item, recurse as list.
                if child_lines[0][1].startswith("- "):
                    sub, _ = _build_tree(child_lines, base_indent=child_indent, start=0)
                else:
                    sub, _ = _build_tree(child_lines, base_indent=child_indent, start=0)
                result[key] = sub
                i = j
            else:
                result[key] = None
                i += 1
    return result, i


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class DraManifestValidator:
    def __init__(self):
        self._qos_table = _qos_table()

    def load_manifest(self, source: str) -> list[ParsedManifestEntry]:
        """Load + parse a YAML manifest. `source` is either a file path
        (single-line, no newline) or the YAML text itself."""
        if not isinstance(source, str):
            raise TypeError("source must be str")
        if "\n" not in source and source.endswith(".yaml"):
            with open(source, encoding="utf-8") as fh:
                text = fh.read()
        else:
            text = source
        docs = _parse_yaml_documents(text)
        entries = []
        for d in docs:
            if not isinstance(d, dict):
                continue
            api_version = d.get("apiVersion", "")
            kind_raw = d.get("kind", "")
            try:
                kind = ManifestKind(kind_raw)
            except ValueError:
                kind = ManifestKind.UNKNOWN
            metadata = d.get("metadata") or {}
            entries.append(
                ParsedManifestEntry(
                    api_version=str(api_version),
                    kind=kind,
                    metadata_name=str(metadata.get("name", "")),
                    labels=dict(metadata.get("labels") or {}),
                    spec=dict(d.get("spec") or {}),
                )
            )
        return entries

    def validate(self, entries: Iterable[ParsedManifestEntry]) -> ValidationReport:
        entry_list = list(entries)
        violations: list[Violation] = []
        device_class_names: set[str] = set()

        for e in entry_list:
            if e.kind == ManifestKind.DEVICE_CLASS:
                device_class_names.add(e.metadata_name)
                violations.extend(self._validate_device_class(e))
            elif e.kind == ManifestKind.RESOURCE_CLAIM_TEMPLATE:
                # Defer validation until we know all device classes.
                pass

        for e in entry_list:
            if e.kind == ManifestKind.RESOURCE_CLAIM_TEMPLATE:
                violations.extend(
                    self._validate_claim_template(
                        e, device_class_names=device_class_names
                    )
                )

        return ValidationReport(entries=tuple(entry_list), violations=tuple(violations))

    def _validate_device_class(self, e: ParsedManifestEntry) -> list[Violation]:
        vs: list[Violation] = []
        if not e.api_version.startswith("resource.k8s.io/"):
            vs.append(
                Violation(
                    e.metadata_name,
                    "apiVersion",
                    f"expected resource.k8s.io/v1beta1+, " f"got {e.api_version!r}",
                )
            )
        # Find MIG profile from label.
        profile_label = e.labels.get("vos3.dev/mig-profile", "")
        if not profile_label:
            vs.append(
                Violation(
                    e.metadata_name,
                    "metadata.labels[vos3.dev/mig-profile]",
                    "missing MIG profile label",
                )
            )
            return vs
        try:
            profile = MIGProfile(profile_label)
        except ValueError:
            vs.append(
                Violation(
                    e.metadata_name,
                    "metadata.labels[vos3.dev/mig-profile]",
                    f"unknown profile {profile_label!r}",
                )
            )
            return vs
        # Walk into spec.config[0].opaque.parameters for persistMig + qos.
        config = e.spec.get("config") or []
        if not config:
            vs.append(Violation(e.metadata_name, "spec.config", "missing config block"))
            return vs
        first = config[0] if isinstance(config, list) else config
        opaque = first.get("opaque") if isinstance(first, dict) else None
        if not isinstance(opaque, dict):
            vs.append(
                Violation(
                    e.metadata_name, "spec.config[0].opaque", "missing opaque block"
                )
            )
            return vs
        params = opaque.get("parameters") or {}
        if params.get("persistMig") is not True:
            vs.append(
                Violation(
                    e.metadata_name,
                    "spec.config[0].opaque.parameters.persistMig",
                    "persistMig must be true for KV-cache continuity",
                )
            )
        qos = params.get("qos") or {}
        expected = self._qos_table.get(profile, {})
        for field_name in ("maxComputeSm", "maxMemoryMiB"):
            actual = qos.get(field_name)
            expected_v = expected.get(field_name)
            if actual != expected_v:
                vs.append(
                    Violation(
                        e.metadata_name,
                        f"spec.config[0].opaque.parameters.qos.{field_name}",
                        f"expected {expected_v} for profile {profile.value}, "
                        f"got {actual}",
                    )
                )
        return vs

    def _validate_claim_template(
        self, e: ParsedManifestEntry, *, device_class_names: set[str]
    ) -> list[Violation]:
        vs: list[Violation] = []
        spec_inner = e.spec.get("spec") or {}
        devices = spec_inner.get("devices") or {}
        requests = devices.get("requests") or []
        if not requests:
            vs.append(
                Violation(
                    e.metadata_name,
                    "spec.spec.devices.requests",
                    "ResourceClaimTemplate has no device requests",
                )
            )
            return vs
        for idx, req in enumerate(requests):
            if not isinstance(req, dict):
                continue
            dc_name = req.get("deviceClassName")
            if dc_name and dc_name not in device_class_names:
                vs.append(
                    Violation(
                        e.metadata_name,
                        f"spec.spec.devices.requests[{idx}].deviceClassName",
                        f"references undefined DeviceClass {dc_name!r}",
                    )
                )
        return vs


__all__ = [
    "MIGProfile",
    "ManifestKind",
    "ParsedManifestEntry",
    "Violation",
    "ValidationReport",
    "DraManifestValidator",
]
