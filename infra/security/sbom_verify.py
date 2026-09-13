"""
v20.1.2 (gauntlet #19) — CycloneDX SBOM hash re-verification.

Walks a CycloneDX 1.7 SBOM (SPDX-JSON or cyclonedx-json form), re-hashes each
referenced local artifact, and asserts bit-for-bit equality with the SBOM's
declared hash. Fails closed on any mismatch — that's the whole point of
having a signed SBOM in the first place.

Supports:
    - CycloneDX 1.5 / 1.6 / 1.7 ``hashes[]`` array ({alg, content}).
    - Algorithms: SHA-256 (required), SHA-384, SHA-512, BLAKE3 (via
      the ``blake3`` package if installed; otherwise that algorithm is
      counted as unverifiable and flagged).
    - Paths can be absolute or relative; components with no path are
      reported as 'unresolvable' (metadata-only entries, which the SBOM
      uses for dataset / model / service references).

Invocation:
    python -m infra.security.sbom_verify \\
        --sbom kernel/build/sbom.cdx.json \\
        --artifact-root .

Exit code 0 = all hashes verified, non-zero = at least one mismatch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_KNOWN_ALGS = {"SHA-256", "SHA-384", "SHA-512", "SHA-1", "MD5", "BLAKE3"}


@dataclass
class VerifyRow:
    component: str
    version: str
    path: Optional[str]
    alg: str
    declared: str
    measured: Optional[str]
    status: str        # "verified" | "mismatch" | "missing" | "unresolvable" | "unsupported-alg"


def _hash_file(path: Path, alg: str) -> Optional[str]:
    algo = alg.upper().replace("-", "")
    if algo == "BLAKE3":
        try:
            import blake3
        except ImportError:
            return None
        h = blake3.blake3()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    try:
        h = hashlib.new(alg.lower().replace("-", ""))
    except ValueError:
        return None
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_components(sbom: Dict) -> List[Dict]:
    comps: List[Dict] = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            if "hashes" in node and isinstance(node.get("hashes"), list):
                comps.append(node)
            for k, v in node.items():
                if k != "hashes":
                    walk(v)
    walk(sbom)
    return comps


def _resolve_path(comp: Dict, root: Path) -> Optional[Path]:
    """Pull a file path from common CycloneDX fields."""
    for key in ("path", "evidence.identity.concludedValue"):
        if key in comp and isinstance(comp[key], str):
            p = Path(comp[key])
            return p if p.is_absolute() else (root / p)
    props = comp.get("properties") or []
    for prop in props:
        if isinstance(prop, dict) and prop.get("name") in (
                "cdx:filepath", "vos3:artifact-path"):
            p = Path(prop.get("value", ""))
            return p if p.is_absolute() else (root / p)
    return None


def _check_one_hash(name: str, ver: str, path: Optional[Path],
                    alg: str, declared: str,
                    artifact_root: Path) -> Tuple[VerifyRow, bool]:
    """Single-hash comparison. Returns (row, is_failure)."""
    if alg not in _KNOWN_ALGS:
        return VerifyRow(name, ver, str(path) if path else None,
                         alg, declared, None, "unsupported-alg"), True
    if path is None:
        return VerifyRow(name, ver, None, alg, declared,
                         None, "unresolvable"), False
    if not path.is_file():
        return VerifyRow(name, ver, str(path), alg, declared,
                         None, "missing"), True
    measured = _hash_file(path, alg)
    if measured is None:
        return VerifyRow(name, ver, str(path), alg, declared,
                         None, "unsupported-alg"), True
    status = "verified" if measured.lower() == declared else "mismatch"
    return (VerifyRow(name, ver, str(path), alg, declared, measured, status),
            status == "mismatch")


def verify(sbom_path: Path, artifact_root: Path) -> Tuple[int, List[VerifyRow]]:
    sbom = json.loads(sbom_path.read_text())
    rows: List[VerifyRow] = []
    failures = 0

    for comp in _iter_components(sbom):
        name = str(comp.get("name", "?"))
        ver  = str(comp.get("version", "?"))
        path = _resolve_path(comp, artifact_root)
        for h in comp.get("hashes") or []:
            alg = str(h.get("alg", "")).upper()
            declared = str(h.get("content", "")).lower()
            row, is_fail = _check_one_hash(name, ver, path, alg, declared,
                                           artifact_root)
            rows.append(row)
            if is_fail:
                failures += 1
    return failures, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sbom", required=True, type=Path)
    ap.add_argument("--artifact-root", type=Path, default=Path("."))
    ap.add_argument("--fail-on-unresolvable", action="store_true",
                    help="Treat path-less components as failures.")
    args = ap.parse_args()

    if not args.sbom.is_file():
        print(f"SBOM missing: {args.sbom}", file=sys.stderr)
        return 2

    failures, rows = verify(args.sbom, args.artifact_root)
    if args.fail_on_unresolvable:
        failures += sum(1 for r in rows if r.status == "unresolvable")

    verified = sum(1 for r in rows if r.status == "verified")
    print(f"components-with-hashes: {len(rows)}  "
          f"verified: {verified}  failures: {failures}")
    for r in rows:
        if r.status != "verified":
            print(f"  [{r.status}] {r.component}=={r.version} "
                  f"({r.alg}) path={r.path}")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
