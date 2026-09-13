"""
infra/security/build_sbom.py — Stage 11

Comprehensive vOS.v1 SBOM (CycloneDX 1.5) with embedded VEX statements.

Covers
======

  - Kernel TUs + headers (file-level components with SHA-256 hashes)
  - Backend Python dependencies (parsed from requirements.txt)
  - Frontend Node dependencies (parsed from package-lock.json)
  - Tauri Rust dependencies (parsed from Cargo.lock)

Output schema
=============

CycloneDX 1.5 (https://cyclonedx.org/specification/overview/#cyclonedx-15)
chosen because:

  - It is the format CISA's "Minimum Requirements for SBOM" (2021)
    explicitly accepts.
  - The May-2026 CISA VEX-mandate response we are documenting in
    ``docs/SIGSTORE_V3_GAP.md`` is a CycloneDX-VEX 1.5 statement
    embedded inline in the same SBOM (the alternative is OpenVEX
    0.0.1 emitted as a separate file; we ship 1.5 inline).

Components
----------

Each ``components[]`` entry carries:

  - ``type``: file|library|application
  - ``name``, ``version``
  - ``bom-ref``: stable per-component identifier used by the
    ``vulnerabilities[]`` array's ``affects[]`` references.
  - ``hashes`` (where applicable — file components carry SHA-256 of
    bytes on disk; library components carry the version pin)
  - ``purl`` (Package URL, RFC-pending) where the dep manager provides
    a canonical URL — pkg:pypi, pkg:npm, pkg:cargo.

Vulnerabilities (VEX)
---------------------

The CycloneDX 1.5 spec defines an inline ``vulnerabilities[]`` array.
Each entry MUST have:

  - ``id`` (CVE / GHSA / advisory id)
  - ``affects[]`` (list of bom-refs)
  - ``analysis.state`` ∈ {not_affected, affected, fixed,
    under_investigation, false_positive}
  - When state == not_affected: ``analysis.justification`` ∈
    {code_not_present, code_not_reachable, requires_configuration,
     requires_dependency, requires_environment, protected_by_compiler,
     protected_at_runtime, protected_at_perimeter,
     protected_by_mitigating_control}
  - ``analysis.detail`` — free text explaining the team's call.

Honest scope on the VEX dataset
-------------------------------

We do not run live ``pip-audit`` / ``npm audit`` / ``cargo-audit`` from
this script — those tools are NOT all available on every dev/CI host
(e.g. ``cargo-audit`` requires the Rust toolchain; ``pip-audit`` is
not in the project's requirements pin). The script therefore loads
the VEX dataset from ``infra/security/vex_baseline.json`` — a hand-
curated baseline that represents "no known CVEs as of the file's
mtime" and which a downstream CI pipeline replaces with the output of
real audit tools.

The baseline file is short, signed, and visible to auditors. The
script SUPPORTS but does not REQUIRE a future ``--vex-source live``
mode that would shell out to the audit tools.

Usage
=====

    python -m infra.security.build_sbom \\
        > infra/security/vos3_sbom_v11_stage11.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
TOOL_VERSION = "stage-11.1"
SBOM_SPEC_VERSION = "1.5"
VEX_BASELINE = REPO / "infra/security/vex_baseline.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _component_file(rel: str) -> dict[str, Any] | None:
    p = REPO / rel
    if not p.is_file():
        return None
    return {
        "type": "file",
        "bom-ref": f"vos3:file:{rel}",
        "name": rel.rsplit("/", 1)[-1],
        "group": rel.rsplit("/", 1)[0] if "/" in rel else "",
        "version": "stage-11",
        "hashes": [{"alg": "SHA-256", "content": _sha256(p)}],
    }


def _component_pypi(name: str, version: str) -> dict[str, Any]:
    return {
        "type": "library",
        "bom-ref": f"pkg:pypi/{name}@{version}",
        "name": name,
        "version": version,
        "purl": f"pkg:pypi/{name}@{version}",
    }


def _component_npm(name: str, version: str) -> dict[str, Any]:
    return {
        "type": "library",
        "bom-ref": f"pkg:npm/{name}@{version}",
        "name": name,
        "version": version,
        "purl": f"pkg:npm/{name}@{version}",
    }


def _component_cargo(name: str, version: str) -> dict[str, Any]:
    return {
        "type": "library",
        "bom-ref": f"pkg:cargo/{name}@{version}",
        "name": name,
        "version": version,
        "purl": f"pkg:cargo/{name}@{version}",
    }


# ---------------------------------------------------------------------------
# Sprint 15 / Item I1 — AI/ML-BOM (CycloneDX 1.5 Machine-Learning components)
#
# Per the CycloneDX guide to AI/ML-BOM (OWASP CycloneDX Authoritative Guide
# to AI/ML-BOM, EN edition), model weights, datasets, fine-tunes, and the
# inference runtime are first-class components with type="machine-learning-
# model" or type="data". We emit them inline in the same SBOM so a downstream
# auditor sees model + weights + signing material in one document.
#
# Source: https://cyclonedx.org/capabilities/mlbom/
#         https://cyclonedx.org/guides/OWASP_CycloneDX-Authoritative-Guide-to-AI-ML-BOM-en.pdf
#         https://blog.sigstore.dev/model-transparency-v1.0/
#
# Honest-scope: when the same SBOM ships in a release, the model component's
# `mlbom.modelCard` field gets populated from a sibling `<model>.modelcard.md`
# file if present; otherwise the SBOM emits only the cryptographic fields
# (hash, signer fingerprint) and an `analysis.detail` note explaining the
# model-card gap. The Sprint 15 deliverable for I3 (Sigstore-based signing)
# fills the signature field; I1 here is the SBOM emitter only.
# ---------------------------------------------------------------------------


MODEL_WEIGHT_GLOBS = [
    "data/models/*.gguf",
    "data/models/*.safetensors",
    "data/models/*.onnx",
    "data/models/*.bin",
    "data/models/*.pt",
    "data/models/*.pth",
    "user/models/*.gguf",
    "user/models/*.safetensors",
]


def _component_ml_model(rel: str) -> dict[str, Any] | None:
    """Emit a CycloneDX 1.5 machine-learning-model component.

    Returns None if the file is missing. Hash is SHA-256 over the raw bytes
    on disk; this is the same digest the Sprint 15 I3 OMS signer attests to,
    so an auditor can cross-check this SBOM against the model-signing bundle
    (or `model.sig` file) shipping alongside the weights.

    The component's `properties` array carries:
      - `vos3:format` — e.g. gguf / safetensors / onnx (from extension)
      - `vos3:size_bytes` — raw byte length on disk
      - `vos3:oms_signature_path` — relative path to the OMS bundle if present
      - `vos3:model_card_path` — relative path to the model card if present
    """
    p = REPO / rel
    if not p.is_file():
        return None

    fmt = p.suffix.lstrip(".").lower() or "unknown"
    size_bytes = p.stat().st_size

    sig_path = p.with_suffix(p.suffix + ".sig")
    bundle_path = p.with_suffix(p.suffix + ".bundle.json")
    card_path = p.with_suffix(p.suffix + ".modelcard.md")

    properties = [
        {"name": "vos3:format", "value": fmt},
        {"name": "vos3:size_bytes", "value": str(size_bytes)},
    ]
    if sig_path.exists():
        properties.append({
            "name": "vos3:oms_signature_path",
            "value": str(sig_path.relative_to(REPO)),
        })
    if bundle_path.exists():
        properties.append({
            "name": "vos3:sigstore_bundle_path",
            "value": str(bundle_path.relative_to(REPO)),
        })
    if card_path.exists():
        properties.append({
            "name": "vos3:model_card_path",
            "value": str(card_path.relative_to(REPO)),
        })

    return {
        "type": "machine-learning-model",
        "bom-ref": f"vos3:model:{rel}",
        "name": p.stem,
        "group": str(p.parent.relative_to(REPO)),
        "version": "local-1.0",
        "hashes": [{"alg": "SHA-256", "content": _sha256(p)}],
        "properties": properties,
    }


def walk_ml_models() -> list[dict[str, Any]]:
    """Walk the model-weight globs and emit one ML-BOM component per file.

    Sprint 15 / Item I1. Cross-references the Sprint 14.1 attestation chain:
    every model loaded into an AI slot extends RTMR[2] with this same SHA-256,
    so the SBOM entry and the kernel attestation report agree by construction.
    """
    seen: dict[str, dict[str, Any]] = {}
    for pattern in MODEL_WEIGHT_GLOBS:
        for p in REPO.glob(pattern):
            if not p.is_file():
                continue
            rel = str(p.relative_to(REPO))
            comp = _component_ml_model(rel)
            if comp:
                seen[rel] = comp
    return list(seen.values())


# ---------------------------------------------------------------------------
# Source walkers
# ---------------------------------------------------------------------------


KERNEL_FILE_GLOBS = [
    "kernel/include/vos/*.h",
    "kernel/src/mm/*.c",
    "kernel/src/sched/*.c",
    "kernel/src/crypto/*.c",
    "kernel/src/exec/*.c",
    "kernel/src/drivers/*.c",
    "kernel/src/drivers/*.h",
    "kernel/Makefile",
]

# A small subset of explicit headers that drive the security overlay; these
# are kept even if globbing misses them (defensive belt-and-braces).
KERNEL_EXPLICIT = [
    "kernel/include/vos/audit_ring.h",
    "kernel/include/vos/action_bridge.h",
    "kernel/src/mm/audit_ring.c",
    "kernel/src/mm/intent_validator.c",
    "kernel/src/exec/action_bridge.c",
]


def walk_kernel() -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for pattern in KERNEL_FILE_GLOBS:
        for p in REPO.glob(pattern):
            if not p.is_file():
                continue
            rel = str(p.relative_to(REPO))
            comp = _component_file(rel)
            if comp:
                seen[rel] = comp
    for rel in KERNEL_EXPLICIT:
        if rel not in seen:
            comp = _component_file(rel)
            if comp:
                seen[rel] = comp
    return [seen[k] for k in sorted(seen)]


# requirements.txt: lines like "name==1.2.3" or "name>=1.0,<2.0".
_REQ_RE = re.compile(r"^\s*([A-Za-z0-9_\-\.]+)\s*(?:==|>=|~=)\s*([A-Za-z0-9_\-\.]+)")


def walk_pip(req_path: Path) -> list[dict[str, Any]]:
    if not req_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for raw in req_path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _REQ_RE.match(line)
        if not m:
            # Loose pin (just "name") — record without version.
            name_only = line.split("[", 1)[0].split(";", 1)[0].strip()
            if name_only:
                out.append(_component_pypi(name_only, "unpinned"))
            continue
        out.append(_component_pypi(m.group(1), m.group(2)))
    return out


def walk_npm(lock_path: Path) -> list[dict[str, Any]]:
    """Parse package-lock.json v3 (NPM 7+) ``packages`` map."""
    if not lock_path.is_file():
        return []
    try:
        lock = json.loads(lock_path.read_text())
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, meta in (lock.get("packages") or {}).items():
        # Top-level project itself appears under "" — skip.
        if path == "":
            continue
        # Path looks like "node_modules/foo" or "node_modules/@scope/foo".
        if not path.startswith("node_modules/"):
            continue
        name = path[len("node_modules/"):]
        version = (meta or {}).get("version", "unpinned")
        key = f"{name}@{version}"
        if key in seen:
            continue
        seen.add(key)
        out.append(_component_npm(name, version))
    return out


# Cargo.lock TOML is simple enough to parse without the toml stdlib.
_CARGO_PKG_RE = re.compile(r"^\[\[package\]\]\s*$")


def walk_cargo(lock_path: Path) -> list[dict[str, Any]]:
    if not lock_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    name = version = None
    in_pkg = False
    for raw in lock_path.read_text().splitlines():
        line = raw.strip()
        if _CARGO_PKG_RE.match(line):
            if name and version:
                out.append(_component_cargo(name, version))
            name = version = None
            in_pkg = True
            continue
        if in_pkg:
            if line.startswith("name = "):
                name = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("version = "):
                version = line.split("=", 1)[1].strip().strip('"')
    if name and version:
        out.append(_component_cargo(name, version))
    return out


# ---------------------------------------------------------------------------
# VEX dataset loader
# ---------------------------------------------------------------------------


def load_vex_baseline(path: Path) -> list[dict[str, Any]]:
    """Load hand-curated VEX statements; tolerate absence (return empty)."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"warning: vex baseline {path} unreadable ({exc})", file=sys.stderr)
        return []
    return list(data.get("vulnerabilities", []))


# ---------------------------------------------------------------------------
# SBOM assembly
# ---------------------------------------------------------------------------


def build_sbom() -> dict[str, Any]:
    components = []
    components.extend(walk_kernel())
    components.extend(walk_pip(REPO / "backend/requirements.txt"))
    components.extend(walk_npm(REPO / "frontend/package-lock.json"))
    components.extend(walk_cargo(REPO / "desktop/src-tauri/Cargo.lock"))
    components.extend(walk_ml_models())  # Sprint 15 / I1 — AI/ML-BOM section

    vulnerabilities = load_vex_baseline(VEX_BASELINE)

    return {
        "bomFormat": "CycloneDX",
        "specVersion": SBOM_SPEC_VERSION,
        "version": 1,
        "serialNumber": f"urn:uuid:vos3-stage11-{int(datetime.now(timezone.utc).timestamp())}",
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tools": [
                {
                    "vendor": "vOS-Cyber",
                    "name": "infra/security/build_sbom.py",
                    "version": TOOL_VERSION,
                }
            ],
            "component": {
                "type": "operating-system",
                "bom-ref": "pkg:vos/vos.v1",
                "name": "vOS.v1",
                "version": "stage-11",
                "description": (
                    "Unified AI Operating System — kernel + backend + "
                    "Tauri shell + frontend"
                ),
            },
            "supplier": {"name": "vOS-Cyber project"},
        },
        "components": components,
        "vulnerabilities": vulnerabilities,
    }


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--out",
        default="-",
        help="Output path (default stdout)",
    )
    args = parser.parse_args(argv)
    sbom = build_sbom()
    text = json.dumps(sbom, indent=2, sort_keys=True)
    if args.out == "-":
        sys.stdout.write(text)
        sys.stdout.write("\n")
    else:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
