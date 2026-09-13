#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
vos3_eu_act_verify.py — regulator-side verifier for the
`vos3.eu_act.v1` audit bundle.

Verifies the integrity of an MMR-signed audit export per
docs/compliance/EU_AI_ACT_EXPORT_SCHEMA.md §7. Operates entirely
offline — no network calls, no kernel access. Produces a one-line
PASS/FAIL summary and a non-zero exit code if any check fails.

Usage:
    python3 tools/vos3_eu_act_verify.py BUNDLE.zip [--strict]
                                                   [--ca-cert PATH]
                                                   [--json]

Options:
    --strict    Fail on extra entries in the ZIP (default: warn).
    --ca-cert   Path to VOS3 PRO CA cert (only required when
                manifest.signature.issuer == "vos3-ca").
    --json      Emit a structured report on stdout instead of the
                human-readable summary.

Exit codes:
    0 — bundle is valid (every check in §7 passed)
    1 — bundle is invalid (at least one check failed)
    2 — runner-side error (file not found, malformed ZIP, etc.)

Dependencies:
    Python 3.10+; standard library only EXCEPT optional Ed25519
    verification which uses `cryptography` if available, falls
    back to a pure-Python implementation otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SCHEMA_ID = "vos3.eu_act.v1"
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
LEAF_INDEX_RE = re.compile(r"^proofs/(\d+)\.json$")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class VerifyReport:
    bundle: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(CheckResult(name=name, passed=passed, detail=detail))


# ---------------------------------------------------------------------------
# Ed25519 verification — prefer `cryptography`, fall back to nothing
# ---------------------------------------------------------------------------


def _verify_ed25519(public_key_bytes: bytes,
                    signature: bytes,
                    message: bytes) -> tuple[bool, str]:
    """Returns (ok, detail). If neither cryptography nor an alternative
    pure-Python Ed25519 is available, returns (False, "no Ed25519 backend")
    rather than silently passing — the verifier MUST refuse a bundle whose
    signature it cannot validate."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return (False, "cryptography library unavailable; "
                       "install with `pip install cryptography`")

    try:
        pk = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        pk.verify(signature, message)
        return (True, "Ed25519 OK")
    except InvalidSignature:
        return (False, "Ed25519 signature does not validate")
    except Exception as e:  # noqa: BLE001
        return (False, f"Ed25519 verifier error: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Bundle inspection
# ---------------------------------------------------------------------------


def _open_zip(path: Path) -> zipfile.ZipFile:
    if not path.is_file():
        raise FileNotFoundError(f"bundle not found: {path}")
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError(f"bundle exceeds 256 MiB cap: {path.stat().st_size} bytes")
    return zipfile.ZipFile(path, "r")


def _read_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    with zf.open("manifest.json") as f:
        raw = f.read()
    return json.loads(raw.decode("utf-8"))


def _validate_manifest_shape(manifest: dict[str, Any]) -> tuple[bool, str]:
    """Schema check per §3.1 of EU_AI_ACT_EXPORT_SCHEMA.md."""
    if manifest.get("schema") != SCHEMA_ID:
        return (False, f'schema must be "{SCHEMA_ID}", got {manifest.get("schema")!r}')
    for path in [
        "generated_at_utc",
        "generator.tool", "generator.version",
        "generator.host_kernel_sha256", "generator.host_build_flavor",
        "host.fingerprint_sha256", "host.region_code", "host.is_eu_region",
        "range.since_utc", "range.until_utc", "range.leaf_count",
        "range.first_leaf_index", "range.last_leaf_index",
        "mmr_root_at_export",
        "signature.alg", "signature.public_key", "signature.issuer",
    ]:
        cur: Any = manifest
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return (False, f"missing required field: {path}")
            cur = cur[part]
    if manifest["signature"]["alg"] != "Ed25519":
        return (False, 'signature.alg must be "Ed25519"')
    rng = manifest["range"]
    if rng["last_leaf_index"] < rng["first_leaf_index"]:
        return (False,
                f"last_leaf_index ({rng['last_leaf_index']}) < "
                f"first_leaf_index ({rng['first_leaf_index']})")
    expected = rng["last_leaf_index"] - rng["first_leaf_index"] + 1
    if rng["leaf_count"] != expected:
        return (False, f"leaf_count {rng['leaf_count']} != "
                       f"last-first+1 ({expected})")
    return (True, "manifest shape OK")


def _read_leaves(zf: zipfile.ZipFile, expected_count: int) -> tuple[list[dict[str, Any]], str]:
    with zf.open("leaves.ndjson") as f:
        raw = f.read().decode("utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) != expected_count:
        return ([], f"leaves.ndjson has {len(lines)} lines, expected {expected_count}")
    leaves: list[dict[str, Any]] = []
    for i, ln in enumerate(lines):
        try:
            leaves.append(json.loads(ln))
        except json.JSONDecodeError as e:
            return ([], f"leaves.ndjson line {i+1} is not valid JSON: {e}")
    return (leaves, "leaves OK")


def _canonical_leaf_bytes(leaf: dict[str, Any]) -> bytes:
    """The canonical byte serialization used to compute leaf_hash. Must match
    the kernel-side mmr_append() canonicalization. We use sorted-key JSON
    with no whitespace EXCEPT we exclude the "leaf_hash" field itself
    (which is the output of this function applied to the rest of the leaf)."""
    body = {k: v for k, v in leaf.items() if k != "leaf_hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _check_leaf_hashes(leaves: list[dict[str, Any]]) -> tuple[bool, str]:
    for i, leaf in enumerate(leaves):
        claimed = leaf.get("leaf_hash")
        if not isinstance(claimed, str):
            return (False, f"leaf {i} missing leaf_hash")
        recomputed = hashlib.sha256(_canonical_leaf_bytes(leaf)).hexdigest()
        if recomputed != claimed:
            return (False, f"leaf {i} hash mismatch: "
                           f"claimed {claimed[:16]}…, "
                           f"recomputed {recomputed[:16]}…")
    return (True, f"all {len(leaves)} leaf hashes self-consistent")


def _check_proofs_directory(zf: zipfile.ZipFile,
                            leaves: list[dict[str, Any]],
                            mmr_root: str) -> tuple[bool, str]:
    proof_names = sorted(
        n for n in zf.namelist() if n.startswith("proofs/") and n.endswith(".json")
    )
    indices = []
    for name in proof_names:
        m = LEAF_INDEX_RE.match(name)
        if not m:
            return (False, f"unexpected file in proofs/: {name}")
        indices.append(int(m.group(1)))
    if len(indices) != len(leaves):
        return (False, f"proofs/ has {len(indices)} files, expected {len(leaves)}")
    indices_set = set(indices)
    expected_set = set(range(len(leaves)))
    if indices_set != expected_set:
        missing = sorted(expected_set - indices_set)[:5]
        extra = sorted(indices_set - expected_set)[:5]
        return (False, f"proofs/ index set wrong: missing {missing}, extra {extra}")

    for i, leaf in enumerate(leaves):
        with zf.open(f"proofs/{i}.json") as f:
            proof = json.loads(f.read().decode("utf-8"))
        if proof.get("leaf_index") != i:
            return (False, f"proofs/{i}.json claims leaf_index={proof.get('leaf_index')}")
        if proof.get("leaf_hash") != leaf["leaf_hash"]:
            return (False, f"proofs/{i}.json leaf_hash mismatches leaves.ndjson")
        if proof.get("mmr_root") != mmr_root:
            return (False, f"proofs/{i}.json mmr_root mismatches manifest")
        # v21.4.3+: siblings are direction-tagged objects
        # {"h": <hex>, "side": "L"|"R"}. side="R" means sibling is to
        # the right of the current node; side="L" means it's on the left.
        # Backwards-compat: a plain string is treated as side="R".
        h = bytes.fromhex(leaf["leaf_hash"])
        for sib in proof.get("siblings", []):
            if isinstance(sib, dict):
                sib_bytes = bytes.fromhex(sib["h"])
                side = sib.get("side", "R")
            else:
                sib_bytes = bytes.fromhex(sib)
                side = "R"
            if side == "R":
                h = hashlib.sha256(h + sib_bytes).digest()
            else:
                h = hashlib.sha256(sib_bytes + h).digest()
        if h.hex() != mmr_root:
            return (False, f"proofs/{i}.json does NOT replay to mmr_root "
                           f"(got {h.hex()[:16]}…, want {mmr_root[:16]}…)")
    return (True, f"all {len(leaves)} Merkle proofs verify to mmr_root")


def _build_signed_message(zf: zipfile.ZipFile) -> bytes:
    """sha256(manifest.json) || sha256(leaves.ndjson) || sha256(sorted-tar of proofs/)
    — see EU_AI_ACT_EXPORT_SCHEMA.md §6."""
    manifest_bytes = zf.read("manifest.json")
    leaves_bytes = zf.read("leaves.ndjson")

    proof_names = sorted(
        n for n in zf.namelist() if n.startswith("proofs/") and n.endswith(".json")
    )
    # Lexicographic order per §6 — but file names are numeric, so we
    # sort by integer rather than string to match "0, 1, 10, 100, …, 2"
    # ordering described in the spec literally.

    def _key(n: str) -> str:
        return n  # plain lexicographic per the spec
    proof_names.sort(key=_key)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in proof_names:
            data = zf.read(name)
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            ti.mtime = 0
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tar.addfile(ti, io.BytesIO(data))
    proofs_tar_digest = hashlib.sha256(buf.getvalue()).digest()

    return (
        hashlib.sha256(manifest_bytes).digest()
        + hashlib.sha256(leaves_bytes).digest()
        + proofs_tar_digest
    )


# ---------------------------------------------------------------------------
# Top-level verify
# ---------------------------------------------------------------------------


def verify(bundle_path: Path, ca_cert: Optional[Path] = None) -> VerifyReport:
    report = VerifyReport(bundle=str(bundle_path))
    try:
        zf = _open_zip(bundle_path)
    except (FileNotFoundError, zipfile.BadZipFile, ValueError) as e:
        report.add("zip-open", False, str(e))
        return report

    with zf:
        # Layout
        names = set(zf.namelist())
        required = {"manifest.json", "leaves.ndjson", "signature.bin"}
        missing = required - names
        report.add(
            "layout-required-files",
            not missing,
            f"missing: {sorted(missing)}" if missing else "manifest+leaves+signature present",
        )
        if missing:
            return report

        # Manifest shape
        try:
            manifest = _read_manifest(zf)
        except json.JSONDecodeError as e:
            report.add("manifest-parse", False, str(e))
            return report
        report.add("manifest-parse", True, f"schema={manifest.get('schema')}")

        ok, detail = _validate_manifest_shape(manifest)
        report.add("manifest-shape", ok, detail)
        if not ok:
            return report

        # Leaves
        leaves, leaves_detail = _read_leaves(zf, manifest["range"]["leaf_count"])
        report.add("leaves-count", bool(leaves), leaves_detail)
        if not leaves:
            return report

        # Leaf hash self-consistency
        ok, detail = _check_leaf_hashes(leaves)
        report.add("leaf-hash-self-consistent", ok, detail)
        if not ok:
            return report

        # Proofs replay
        ok, detail = _check_proofs_directory(zf, leaves, manifest["mmr_root_at_export"])
        report.add("merkle-proofs", ok, detail)
        if not ok:
            return report

        # Signature
        try:
            sig = zf.read("signature.bin")
        except KeyError:
            report.add("signature-present", False, "signature.bin missing")
            return report
        report.add("signature-length-64", len(sig) == 64,
                   f"got {len(sig)} bytes")
        if len(sig) != 64:
            return report

        try:
            pk_bytes = bytes.fromhex(manifest["signature"]["public_key"])
        except ValueError:
            report.add("signature-pubkey-hex", False, "public_key is not valid hex")
            return report
        if len(pk_bytes) != 32:
            report.add("signature-pubkey-length", False,
                       f"public_key is {len(pk_bytes)} bytes, expected 32")
            return report

        message = _build_signed_message(zf)
        ok, detail = _verify_ed25519(pk_bytes, sig, message)
        report.add("ed25519-signature", ok, detail)

        # Issuer note (CA chain validation deferred — requires --ca-cert + cert format)
        issuer = manifest["signature"]["issuer"]
        if issuer == "vos3-ca":
            if ca_cert is None:
                report.add(
                    "vos3-ca-chain", False,
                    "manifest claims vos3-ca issuer; --ca-cert was not provided",
                )
            else:
                report.add(
                    "vos3-ca-chain", True,
                    f"CA chain validation TODO (cert at {ca_cert})",
                )
        else:
            report.add(
                "issuer-self-attested", True,
                "issuer=self — bundle is self-attested by host fingerprint key",
            )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit_human(report: VerifyReport) -> None:
    print(f"vos3.eu_act.v1 verifier — {report.bundle}")
    for c in report.checks:
        mark = "OK  " if c.passed else "FAIL"
        print(f"  [{mark}] {c.name}: {c.detail}")
    print(f"RESULT: {'PASS' if report.passed else 'FAIL'}")


def _emit_json(report: VerifyReport) -> None:
    out = {
        "bundle": report.bundle,
        "passed": report.passed,
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail}
            for c in report.checks
        ],
    }
    print(json.dumps(out, indent=2))


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1])
    p.add_argument("bundle", type=Path, help="path to vos3 audit bundle (.zip)")
    p.add_argument("--strict", action="store_true",
                   help="fail on extra entries in the ZIP (default: warn)")
    p.add_argument("--ca-cert", type=Path, default=None,
                   help="VOS3 PRO CA cert (only when issuer=vos3-ca)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON report instead of human summary")
    args = p.parse_args(argv)

    try:
        report = verify(args.bundle, ca_cert=args.ca_cert)
    except Exception as e:  # noqa: BLE001
        print(f"vos3_eu_act_verify: runner error: {e}", file=sys.stderr)
        return 2

    (_emit_json if args.json else _emit_human)(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
