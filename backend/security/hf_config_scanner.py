"""
backend/security/hf_config_scanner.py
======================================

Sprint 15 / Item I6 — Hugging Face configuration-file attack scanner.

Background
----------

In 2024-2025, multiple research groups documented that the threat surface
for Hugging Face models is not just the weight files but the **configuration
files** that accompany them. The "Rusty Link" paper
(https://arxiv.org/pdf/2505.01067) and the "Models Are Codes" study
(https://arxiv.org/pdf/2409.09368) both show that:

  1. `config.json` can carry pickle-import entries that fire arbitrary
     Python before model weights are even read.
  2. The `auto_map` field in HF configs maps tokenizer / model classes to
     module paths — those paths can resolve to remote code on the user's
     machine (or worse, on a CDN).
  3. The `trust_remote_code=True` flag explicitly opts the user into RCE
     by remote code from the model repo (HF requires the flag, but many
     downstream consumers silently set it to enable wider model support).
  4. Tokenizer configs (`tokenizer_config.json`) can reference `.py`
     shims that the HF Tokenizers library will eval()-equivalent on load.

The HF platform itself **lacks tools to alert users when malicious
configurations are loaded** (per the Rusty Link paper's recommendation
section). This module fills that gap for vOS deployments.

Public surface
--------------

    scan_directory(path: str) -> ConfigScanResult
        Walk a HF model directory and run all checks.

    scan_config_file(path: str) -> list[Finding]
        Scan one config file (config.json, tokenizer_config.json, etc.)
        for known dangerous patterns.

    Finding (dataclass)
        - kind        : "pickle_import" | "auto_map_remote" |
                        "trust_remote_code" | "py_shim_in_tokenizer" |
                        "dangerous_loader_signature"
        - path        : file where the finding was raised
        - severity    : "low" | "medium" | "high" | "critical"
        - detail      : free-text explanation
        - field_path  : JSON dotted path inside the config (for triage)

    ConfigScanResult (dataclass)
        - is_safe      : True iff zero critical or high findings
        - findings     : list of Finding
        - severity     : highest single-finding severity
        - directory    : the scanned root

Scanner design
--------------

This is a **defense-in-depth gate**, NOT a research-grade detector. We
optimize for:

  1. **Zero false negatives on known attack classes** (pickle imports,
     auto_map remote loaders, trust_remote_code=True) — the cost of a
     false negative is RCE in the agent runtime.
  2. **Acceptable false positives** on benign-but-suspicious patterns
     (e.g., custom tokenizer with a local .py shim) — we surface them
     and let the operator approve.
  3. **Speed** — scanning runs at model-load time, so per-file work must
     be sub-100ms even on a slow disk.

What this does NOT catch
------------------------

  * Malicious weights themselves (those are the I3 / OMS signing chain's
    job to detect).
  * Adversarial training-data backdoors / sleeper-agent triggers
    (industry-wide unsolved per the catalog item I4).
  * Sophisticated obfuscation (base64-encoded pickle bytes inside a
    seemingly-benign field).

We document the residuals in the operator runbook and use the OMS
signature chain (I3) plus the sandbox isolation (C6 / C8) as
defense-in-depth complements.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — known-dangerous patterns
# ---------------------------------------------------------------------------

# Files we know to inspect in HF model directories.
_KNOWN_CONFIG_FILENAMES = frozenset(
    {
        "config.json",
        "tokenizer_config.json",
        "generation_config.json",
        "preprocessor_config.json",
        "scheduler_config.json",
        "feature_extractor_config.json",
        "model_index.json",
    }
)

# JSON keys that are known to carry executable references and require gating.
_AUTO_MAP_KEYS = frozenset({"auto_map", "AutoModel", "AutoTokenizer", "auto_class"})
_TRUST_REMOTE_KEYS = frozenset({"trust_remote_code"})

# Pickle imports inside model_card or any string field.
_PICKLE_PATTERNS = (
    re.compile(rb"\x80\x04"),  # pickle protocol 4 header
    re.compile(rb"\x80\x05"),  # pickle protocol 5 header
    re.compile(rb"cposix\nsystem\n"),  # `os.system` via pickle
    re.compile(rb"c__builtin__\neval\n"),  # `eval` via pickle (py2)
    re.compile(rb"cbuiltins\neval\n"),  # `eval` via pickle (py3)
    re.compile(rb"csubprocess\n"),  # `subprocess.*` via pickle
    re.compile(rb"c__builtin__\nexec\n"),  # `exec` via pickle (py2)
    re.compile(rb"cbuiltins\nexec\n"),  # `exec` via pickle (py3)
)

# auto_map entries containing remote-resolve markers.
_REMOTE_LOADER_RE = re.compile(
    r"(https?://|s3://|gs://|hf://|github\.com|raw\.githubusercontent\.com)",
    re.IGNORECASE,
)

# A "dangerous loader signature" is a class path that ends in a known-evil
# module pattern. This is a starter list; ops can extend via env.
_DANGEROUS_LOADER_PATTERNS = (
    re.compile(r"^os\.system$"),
    re.compile(r"^subprocess\.[A-Za-z_]+$"),
    re.compile(r"^posix\.system$"),
    re.compile(r"^pickle\.loads?$"),
    re.compile(r"^eval$|^exec$"),
)

# Tokenizer config keys that reference `.py` shims.
_PY_SHIM_FIELDS = frozenset(
    {
        "tokenizer_class",
        "tokenizer_file",
        "AutoTokenizer",
        "auto_class",
    }
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    severity: str  # low | medium | high | critical
    detail: str
    field_path: Optional[str] = None


@dataclass(frozen=True)
class ConfigScanResult:
    directory: str
    findings: tuple[Finding, ...]
    is_safe: bool
    severity: str  # "none" | "low" | "medium" | "high" | "critical"


_SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _max_severity(findings: Iterable[Finding]) -> str:
    best = "none"
    for f in findings:
        if _SEVERITY_RANK.get(f.severity, 0) > _SEVERITY_RANK.get(best, 0):
            best = f.severity
    return best


# ---------------------------------------------------------------------------
# Per-field checks
# ---------------------------------------------------------------------------


def _walk_json(obj: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    """Yield (dotted-path, value) for every leaf + intermediate node."""
    if isinstance(obj, dict):
        yield prefix or "$", obj
        for k, v in obj.items():
            sub_prefix = f"{prefix}.{k}" if prefix else k
            yield from _walk_json(v, sub_prefix)
    elif isinstance(obj, list):
        yield prefix or "$", obj
        for i, v in enumerate(obj):
            sub_prefix = f"{prefix}[{i}]"
            yield from _walk_json(v, sub_prefix)
    else:
        yield prefix, obj


def _check_pickle_bytes(path: Path) -> list[Finding]:
    """Look for pickle protocol headers or dangerous opcodes in raw bytes."""
    out: list[Finding] = []
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning("[hf_config_scanner] could not read %s: %s", path, exc)
        return out

    # Don't scan giant files for pickle bytes — they're usually weights, not
    # configs. Cap at 1 MB.
    if len(data) > 1024 * 1024:
        return out

    for pat in _PICKLE_PATTERNS:
        if pat.search(data):
            out.append(
                Finding(
                    kind="pickle_import",
                    path=str(path),
                    severity="critical",
                    detail=(
                        f"Pickle byte-pattern {pat.pattern!r} found in config "
                        "file. Configs MUST be pure JSON; pickle bytes indicate "
                        "either obfuscation or a misuse of the HF format."
                    ),
                )
            )
    return out


def _check_auto_map(field_path: str, value: Any, source: Path) -> list[Finding]:
    """`auto_map` and friends are dicts mapping class names to module paths.
    Remote URL targets or dangerous-loader signatures are findings."""
    out: list[Finding] = []
    if not isinstance(value, dict):
        return out
    for cls_name, target in value.items():
        target_s = str(target)
        if _REMOTE_LOADER_RE.search(target_s):
            out.append(
                Finding(
                    kind="auto_map_remote",
                    path=str(source),
                    severity="critical",
                    detail=(
                        f"auto_map entry {cls_name!r} points to a remote "
                        f"loader: {target_s!r}. HF auto-loading from remote "
                        "URLs is RCE-on-load. Refuse to load this model."
                    ),
                    field_path=f"{field_path}.{cls_name}",
                )
            )
            continue
        for dpat in _DANGEROUS_LOADER_PATTERNS:
            if dpat.match(target_s):
                out.append(
                    Finding(
                        kind="dangerous_loader_signature",
                        path=str(source),
                        severity="critical",
                        detail=(
                            f"auto_map entry {cls_name!r} → {target_s!r} matches "
                            f"a known-dangerous loader pattern ({dpat.pattern!r}). "
                            "This is RCE; refuse to load."
                        ),
                        field_path=f"{field_path}.{cls_name}",
                    )
                )
    return out


def _check_trust_remote_code(
    field_path: str, value: Any, source: Path
) -> list[Finding]:
    """`trust_remote_code: true` is a flag that opts in to RCE on load."""
    if value is True or (isinstance(value, str) and value.strip().lower() == "true"):
        return [
            Finding(
                kind="trust_remote_code",
                path=str(source),
                severity="high",
                detail=(
                    "trust_remote_code=true is set in this config. This flag "
                    "tells HF Transformers to execute arbitrary Python from the "
                    "model repository on load. Refuse unless the operator has "
                    "explicitly approved this specific model after manual review."
                ),
                field_path=field_path,
            )
        ]
    return []


def _check_py_shim(field_path: str, value: Any, source: Path) -> list[Finding]:
    """Tokenizer / preprocessor configs sometimes reference `.py` shims."""
    if isinstance(value, str) and value.endswith(".py"):
        return [
            Finding(
                kind="py_shim_in_tokenizer",
                path=str(source),
                severity="high",
                detail=(
                    f"Config field {field_path!r} references a Python file "
                    f"({value!r}). HF tokenizer-class fields should refer to "
                    "Transformers built-in classes (e.g. 'GPT2Tokenizer'), not "
                    "sibling .py files. .py shim references are typically used "
                    "by malicious model repos to inject arbitrary code on load."
                ),
                field_path=field_path,
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Public scan API
# ---------------------------------------------------------------------------


def scan_config_file(path: str | os.PathLike) -> list[Finding]:
    """Scan ONE config file for known dangerous patterns.

    Returns a list of Findings (empty list = clean).
    """
    p = Path(path)
    if not p.is_file():
        return []

    findings: list[Finding] = []
    findings.extend(_check_pickle_bytes(p))

    # Parse as JSON; if it isn't valid JSON, that's itself worth a finding.
    try:
        doc = json.loads(p.read_text("utf-8"))
    except UnicodeDecodeError:
        findings.append(
            Finding(
                kind="dangerous_loader_signature",
                path=str(p),
                severity="medium",
                detail=(
                    f"Config file {p.name} contains non-UTF-8 bytes; HF configs "
                    "are documented as UTF-8 JSON. Non-text content indicates "
                    "binary obfuscation or corruption."
                ),
            )
        )
        return findings
    except json.JSONDecodeError as exc:
        findings.append(
            Finding(
                kind="dangerous_loader_signature",
                path=str(p),
                severity="medium",
                detail=(
                    f"Config file {p.name} is not valid JSON: {exc.msg}. "
                    "HF configs MUST be parseable JSON."
                ),
            )
        )
        return findings

    # Walk the parsed JSON for known-dangerous fields.
    for field_path, value in _walk_json(doc):
        # Strip the bracket index suffix to compare against field-name sets.
        bare_key = field_path.rsplit(".", 1)[-1].split("[", 1)[0]
        if bare_key in _AUTO_MAP_KEYS:
            findings.extend(_check_auto_map(field_path, value, p))
        if bare_key in _TRUST_REMOTE_KEYS:
            findings.extend(_check_trust_remote_code(field_path, value, p))
        if bare_key in _PY_SHIM_FIELDS:
            findings.extend(_check_py_shim(field_path, value, p))

    return findings


def scan_directory(path: str | os.PathLike) -> ConfigScanResult:
    """Scan a HF model directory for ALL known config files.

    Walks the top level only (HF model repos are flat, not nested). Returns
    a `ConfigScanResult` with the aggregate findings.
    """
    root = Path(path)
    if not root.is_dir():
        return ConfigScanResult(
            directory=str(root),
            findings=(),
            is_safe=False,
            severity="medium",
        )

    findings: list[Finding] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_file():
            continue
        if entry.name in _KNOWN_CONFIG_FILENAMES:
            findings.extend(scan_config_file(entry))

    severity = _max_severity(findings)
    is_safe = severity not in {"high", "critical"}
    return ConfigScanResult(
        directory=str(root),
        findings=tuple(findings),
        is_safe=is_safe,
        severity=severity,
    )


__all__ = [
    "ConfigScanResult",
    "Finding",
    "scan_config_file",
    "scan_directory",
]
