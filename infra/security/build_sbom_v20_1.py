"""
v20.1.4 — Build a CycloneDX 1.7 SBOM covering the v20.1.x code artifacts.

`cyclonedx-py environment` emits a component list without artifact-level
SHA-256 hashes (PyPI metadata does not carry them). For supply-chain
integrity re-verification, we need hashes anchored to real files on disk.
This script produces a hash-anchored SBOM covering:

  - Kernel TUs added/modified in v20.1.x (.c, .h)
  - Backend Python modules added in v20.1.x (.py)
  - Lockfiles + manifests (uv.lock, pyproject.toml)

Each component carries:
  - name, version (20.1.4)
  - path (repo-relative)
  - hashes[{alg: SHA-256, content: <hex>}]

The resulting SBOM is verifiable bit-for-bit with
``infra/security/sbom_verify.py``.

Usage:
    python -m infra.security.build_sbom > infra/security/vos3_code_sbom.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# v20.1.x-touched files — deliberate explicit list, not a glob, so the
# SBOM documents exactly the surface under this release.
V20_1_ARTIFACTS = [
    # Kernel C + headers (added or modified in v20.1.x)
    "kernel/include/vos/hcs.h",
    "kernel/include/vos/tee.h",
    "kernel/include/vos/sha384.h",
    "kernel/src/mm/hcs.c",
    "kernel/src/mm/tee.c",
    "kernel/src/crypto/sha384.c",
    "kernel/src/sched/core_cookie.c",
    "kernel/src/tests/harness.c",

    # Backend — new modules
    "backend/core/vcore_bridge.py",
    "backend/core/observability/leak_detector.py",
    "backend/core/observability/__init__.py",
    "backend/core/repositories/local_vault.py",

    # Infra
    "infra/security/sigstore_verify.py",
    "infra/security/sbom_verify.py",
    "infra/security/build_sbom.py",

    # Bench / formal-verification
    "backend/tests/benchmarks/scheduler_jitter_sim.py",
    "backend/tests/benchmarks/native_audit_bench.c",
    "backend/tests/benchmarks/ioctl_fuzz.py",
    "backend/tests/benchmarks/sched_core_z3_proof.py",

    # Manifests / lock
    "backend/pyproject.toml",
    "backend/uv.lock",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build() -> dict:
    components = []
    missing = []
    for rel in V20_1_ARTIFACTS:
        p = REPO / rel
        if not p.is_file():
            missing.append(rel)
            continue
        components.append({
            "type": "file",
            "name": rel.rsplit("/", 1)[-1],
            "version": "20.1.4",
            "bom-ref": f"vos3:{rel}",
            "path": rel,
            "hashes": [{"alg": "SHA-256", "content": _sha256(p)}],
        })
    if missing:
        print(f"WARNING: {len(missing)} expected artifacts missing:",
              file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)

    return {
        "bomFormat":    "CycloneDX",
        "specVersion":  "1.7",
        "serialNumber": f"urn:uuid:vos3-v20.1.4-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "component": {
                "type": "application",
                "name": "vos3-cyber",
                "version": "20.1.4",
                "description": "VOS-Cyber v20.1.4 — hash-anchored code SBOM",
            },
            "tools": {
                "components": [{
                    "type": "application",
                    "name": "vos3-build-sbom",
                    "version": "1.0.0",
                }],
            },
        },
        "components": components,
    }


if __name__ == "__main__":
    sbom = build()
    print(json.dumps(sbom, indent=2))
