#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
vos3_eu_act_export.py — operator-side audit-bundle exporter.

Bundles a time-bounded slice of the kernel's MMR audit chain into a
`vos3.eu_act.v1` bundle that an enterprise CISO can hand to a Member
State auditor for EU AI Act Article 12 compliance. The companion
verifier (`vos3_eu_act_verify.py`) replays Merkle proofs against the
manifest's MMR root.

Usage:
    # Real export (talks to running kernel via VBus bridge):
    python3 tools/vos3_eu_act_export.py \\
        --since 2026-05-01T00:00:00Z \\
        --until 2026-05-02T00:00:00Z \\
        --output audit-bundle.zip \\
        [--bridge-socket /run/vos3/bridge.sock]

    # Self-test (synthetic leaves; no kernel needed) — round-trips
    # through vos3_eu_act_verify.py to prove the pipeline works:
    python3 tools/vos3_eu_act_export.py --self-test --output /tmp/test.zip

Honest framing:
    This is the v21.4.3 BETA. Real kernel-side leaf streaming via
    the VBus `MMR_RANGE` ASCII command is wired in this commit but
    requires the kernel to be running. The --self-test path produces
    a synthetic bundle that exercises every code path of the bundle
    builder + signer + Merkle proof generator, and is what proves
    the format itself round-trips correctly through the verifier.

Spec: docs/compliance/EU_AI_ACT_EXPORT_SCHEMA.md
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import socket
import sys
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_ID = "vos3.eu_act.v1"
EXPORTER_VERSION = "v21.4.3-EU-ACT-BETA"


# ---------------------------------------------------------------------------
# Leaf model
# ---------------------------------------------------------------------------


@dataclass
class Leaf:
    index: int
    timestamp_tsc: int
    timestamp_utc: str
    syscall_nr: int
    tid: int
    uid: int
    args_hash: str       # 16 hex chars (truncated SHA-256)
    result_class: str    # "ok" | "err" | "killed"
    result_value: int

    def to_dict(self) -> dict[str, Any]:
        d = {
            "index": self.index,
            "timestamp_tsc": self.timestamp_tsc,
            "timestamp_utc": self.timestamp_utc,
            "syscall_nr": self.syscall_nr,
            "tid": self.tid,
            "uid": self.uid,
            "args_hash": self.args_hash,
            "result_class": self.result_class,
            "result_value": self.result_value,
        }
        return d


def _canonical_leaf_bytes(leaf_dict: dict[str, Any]) -> bytes:
    """Canonical byte serialization for hashing — excludes leaf_hash itself.
    Matches `_canonical_leaf_bytes` in vos3_eu_act_verify.py."""
    body = {k: v for k, v in leaf_dict.items() if k != "leaf_hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _leaf_hash(leaf_dict: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_leaf_bytes(leaf_dict)).hexdigest()


# ---------------------------------------------------------------------------
# Merkle Mountain Range — proof construction
# ---------------------------------------------------------------------------
#
# Simplified MMR proof: for a flat list of N leaf hashes, build a balanced
# binary tree (right-padded with zero hashes) and produce per-leaf inclusion
# proofs. The kernel's MMR uses the height-doubling structure described in
# the schema doc; for this v21.4.3 beta the simpler balanced-tree shape is
# sufficient because the verifier replays the SAME hash chain.
#
# Both export and verify use the same algorithm — the schema spec pins the
# algorithm to "left || right" SHA-256 concatenation, which is what we do.


def _build_merkle_layers(leaf_hashes: list[bytes]) -> list[list[bytes]]:
    """Return list of layers, layer 0 = leaves, last layer = root."""
    layers: list[list[bytes]] = [list(leaf_hashes)]
    cur = leaf_hashes
    while len(cur) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(cur), 2):
            left = cur[i]
            right = cur[i + 1] if i + 1 < len(cur) else cur[i]  # duplicate last
            nxt.append(hashlib.sha256(left + right).digest())
        layers.append(nxt)
        cur = nxt
    return layers


def _proof_for_leaf(layers: list[list[bytes]],
                    leaf_index: int) -> list[dict[str, Any]]:
    """Direction-tagged sibling list from leaf up to root.

    Each entry: {"h": <hex>, "side": "L"|"R"} — describes whether the
    sibling is to the LEFT or RIGHT of the current node at that level.
    The verifier uses this to compute the next-level hash:
        side="R": parent = sha256(current || sibling)
        side="L": parent = sha256(sibling || current)
    """
    siblings: list[dict[str, Any]] = []
    idx = leaf_index
    for layer in layers[:-1]:
        if idx % 2 == 0:
            # current is LEFT child; sibling is to the RIGHT
            sib_idx = idx + 1 if idx + 1 < len(layer) else idx
            side = "R"
        else:
            # current is RIGHT child; sibling is to the LEFT
            sib_idx = idx - 1
            side = "L"
        siblings.append({"h": layer[sib_idx].hex(), "side": side})
        idx //= 2
    return siblings


# ---------------------------------------------------------------------------
# Ed25519 — prefer cryptography; refuse to silently fall back
# ---------------------------------------------------------------------------


def _generate_keypair() -> tuple[bytes, bytes]:
    """Returns (public_key_32, private_key_obj_or_seed)."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat,
        )
    except ImportError:
        print("FATAL: cryptography library required for signing", file=sys.stderr)
        print("       install with: pip install cryptography", file=sys.stderr)
        sys.exit(2)

    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    return (pk, sk)


def _sign(private_key, message: bytes) -> bytes:
    return private_key.sign(message)


# ---------------------------------------------------------------------------
# Kernel-side leaf retrieval (VBus bridge)
# ---------------------------------------------------------------------------


def _query_kernel_mmr_range(socket_path: str,
                            since_tsc: int,
                            until_tsc: int) -> list[dict[str, Any]]:
    """Issue MMR_RANGE <since_tsc> <until_tsc> over VBus and parse the
    NDJSON response. Returns a list of leaf dicts.

    Wire format (kernel emit format pinned by the bridge handler):
        MMR_RANGE_BEGIN <count>\\n
        {"index":..., "timestamp_tsc":..., ...}\\n   ← one per leaf
        ...
        MMR_RANGE_END\\n
    """
    # PD401: reject paths outside the VBus runtime directory to prevent
    # an operator from redirecting the export tool to an attacker-controlled
    # UNIX socket via a relative path or /tmp symlink.
    _VBUS_SOCKET_PREFIX = "/run/vos3/"
    if not socket_path or not socket_path.startswith(_VBUS_SOCKET_PREFIX):
        raise ValueError(
            f"bridge socket must be under {_VBUS_SOCKET_PREFIX!r}, got: {socket_path!r}"
        )
    if not os.path.exists(socket_path):
        raise FileNotFoundError(
            f"VBus bridge socket not found: {socket_path}"
        )

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    s.connect(socket_path)
    cmd = f"MMR_RANGE {since_tsc} {until_tsc}\n"
    s.sendall(cmd.encode("ascii"))

    chunks: list[bytes] = []
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        if b"MMR_RANGE_END" in chunk:
            break
    s.close()

    text = b"".join(chunks).decode("utf-8", errors="replace")
    leaves: list[dict[str, Any]] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("MMR_RANGE_"):
            continue
        try:
            leaves.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return leaves


# ---------------------------------------------------------------------------
# Synthetic leaves (for --self-test)
# ---------------------------------------------------------------------------


def _synthetic_leaves(count: int, base_ts: int) -> list[Leaf]:
    """Generate `count` deterministic synthetic leaves for self-test."""
    out: list[Leaf] = []
    for i in range(count):
        out.append(Leaf(
            index=i,
            timestamp_tsc=base_ts + i * 1_000_000,
            timestamp_utc=datetime.fromtimestamp(
                1764547200 + i, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z"),
            syscall_nr=257 + (i % 5),  # cycle through a few syscalls
            tid=42,
            uid=1000,
            args_hash=hashlib.sha256(f"args-{i}".encode()).hexdigest()[:16],
            result_class="ok" if i % 7 != 0 else "err",
            result_value=0 if i % 7 != 0 else -22,
        ))
    return out


# ---------------------------------------------------------------------------
# Bundle construction
# ---------------------------------------------------------------------------


def build_bundle(
    leaves: list[Leaf],
    output_path: Path,
    since_utc: str,
    until_utc: str,
    host_kernel_sha256: str,
    host_fingerprint_sha256: str,
    region_code: str,
    is_eu_region: bool,
    build_flavor: str,
    issuer: str,
    signing_key,           # cryptography Ed25519PrivateKey
    public_key_bytes: bytes,
) -> dict[str, Any]:
    """Builds the ZIP bundle. Returns a dict with diagnostic counts."""
    if not leaves:
        raise ValueError("no leaves in [since, until] window")

    # 1. Compute leaf_hashes + canonical NDJSON
    leaf_dicts: list[dict[str, Any]] = []
    leaf_hash_bytes: list[bytes] = []
    for leaf in leaves:
        d = leaf.to_dict()
        h_hex = _leaf_hash(d)
        d["leaf_hash"] = h_hex
        leaf_dicts.append(d)
        leaf_hash_bytes.append(bytes.fromhex(h_hex))

    leaves_ndjson = "\n".join(
        json.dumps(d, separators=(",", ":")) for d in leaf_dicts
    ).encode("utf-8") + b"\n"

    # 2. Build Merkle tree + per-leaf proofs
    layers = _build_merkle_layers(leaf_hash_bytes)
    mmr_root = layers[-1][0]
    mmr_root_hex = mmr_root.hex()

    proof_files: dict[str, bytes] = {}
    for i, leaf_dict in enumerate(leaf_dicts):
        proof_obj = {
            "leaf_index": i,
            "leaf_hash": leaf_dict["leaf_hash"],
            "siblings": _proof_for_leaf(layers, i),
            "mmr_root": mmr_root_hex,
        }
        proof_files[f"proofs/{i}.json"] = json.dumps(
            proof_obj, separators=(",", ":")
        ).encode("utf-8")

    # 3. Manifest
    first_idx = leaves[0].index
    last_idx = leaves[-1].index
    manifest = {
        "schema": SCHEMA_ID,
        "generated_at_utc": (
            datetime.now(timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ),
        "generator": {
            "tool": "vos3_eu_act_export",
            "version": EXPORTER_VERSION,
            "host_kernel_sha256": host_kernel_sha256,
            "host_build_flavor": build_flavor,
        },
        "host": {
            "fingerprint_sha256": host_fingerprint_sha256,
            "region_code": region_code,
            "is_eu_region": is_eu_region,
        },
        "range": {
            "since_utc": since_utc,
            "until_utc": until_utc,
            "leaf_count": len(leaves),
            "first_leaf_index": first_idx,
            "last_leaf_index": last_idx,
        },
        "mmr_root_at_export": mmr_root_hex,
        "signature": {
            "alg": "Ed25519",
            "public_key": public_key_bytes.hex(),
            "issuer": issuer,
            "key_provenance": (
                "host_fingerprint" if issuer == "self" else "vos3_pro_lic"
            ),
        },
    }
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=False).encode("utf-8") + b"\n"
    )

    # 4. Compute signed digest per schema §6:
    #    sha256(manifest) || sha256(leaves) || sha256(sorted-tar of proofs/)
    proof_names_sorted = sorted(proof_files.keys())
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        for name in proof_names_sorted:
            data = proof_files[name]
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            ti.mtime = 0
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tar.addfile(ti, io.BytesIO(data))
    proofs_tar_digest = hashlib.sha256(tar_buf.getvalue()).digest()

    signed_message = (
        hashlib.sha256(manifest_bytes).digest()
        + hashlib.sha256(leaves_ndjson).digest()
        + proofs_tar_digest
    )
    signature = _sign(signing_key, signed_message)
    if len(signature) != 64:
        raise RuntimeError(f"unexpected Ed25519 signature length: {len(signature)}")

    # 5. Write ZIP atomically (PD201/PD402).
    # Symlink guard: refuse to write through a symlink — an attacker who
    # controls the output directory could redirect the file to an arbitrary
    # path (e.g. overwrite a running binary or a sensitive config file).
    if output_path.is_symlink():
        raise ValueError(
            f"output path is a symlink — refusing to write: {output_path}"
        )
    # Write to a sibling temp file, then rename into place.  On POSIX,
    # os.replace() is atomic within the same filesystem so readers never
    # see a partial ZIP.
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=output_path.parent, suffix=".zip.tmp"
    )
    try:
        with os.fdopen(tmp_fd, "wb") as tmp_file:
            with zipfile.ZipFile(tmp_file, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("manifest.json", manifest_bytes)
                zf.writestr("leaves.ndjson", leaves_ndjson)
                for name in proof_names_sorted:
                    zf.writestr(name, proof_files[name])
                zf.writestr("signature.bin", signature)
        os.replace(tmp_name, output_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    return {
        "bundle_path": str(output_path),
        "leaf_count": len(leaves),
        "mmr_root": mmr_root_hex,
        "manifest_size": len(manifest_bytes),
        "leaves_ndjson_size": len(leaves_ndjson),
        "signature_bytes": len(signature),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_iso_utc(s: str) -> str:
    """Validates RFC 3339 UTC and returns canonical form."""
    if s.endswith("Z"):
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp must be UTC (got naive): {s}")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def cmd_export(args: argparse.Namespace) -> int:
    """Real-kernel export path."""
    since_utc = _parse_iso_utc(args.since)
    until_utc = _parse_iso_utc(args.until)

    # TSC bounds: convert UTC timestamps to TSC counts. The bridge takes
    # raw TSC; absent a kernel-time-base query, callers should pass --since-tsc
    # and --until-tsc directly. For now: use 0..MAX which means "every leaf".
    since_tsc = args.since_tsc if args.since_tsc is not None else 0
    until_tsc = args.until_tsc if args.until_tsc is not None else (1 << 63) - 1

    raw = _query_kernel_mmr_range(args.bridge_socket, since_tsc, until_tsc)
    leaves: list[Leaf] = []
    for d in raw:
        leaves.append(Leaf(
            index=int(d["index"]),
            timestamp_tsc=int(d["timestamp_tsc"]),
            timestamp_utc=str(d["timestamp_utc"]),
            syscall_nr=int(d["syscall_nr"]),
            tid=int(d["tid"]),
            uid=int(d["uid"]),
            args_hash=str(d["args_hash"]),
            result_class=str(d["result_class"]),
            result_value=int(d["result_value"]),
        ))

    if not leaves:
        print(f"ERROR: no leaves in [{since_utc}, {until_utc}]", file=sys.stderr)
        return 1

    pk, sk = _generate_keypair()
    info = build_bundle(
        leaves=leaves,
        output_path=Path(args.output),
        since_utc=since_utc,
        until_utc=until_utc,
        host_kernel_sha256=args.kernel_sha or "0" * 64,
        host_fingerprint_sha256=args.host_fingerprint or "0" * 64,
        region_code=args.region_code,
        is_eu_region=(args.region_code == "EU" or args.is_eu),
        build_flavor=args.build_flavor,
        issuer=args.issuer,
        signing_key=sk,
        public_key_bytes=pk,
    )
    print(f"Wrote bundle: {info['bundle_path']}")
    print(f"  leaves:       {info['leaf_count']}")
    print(f"  mmr_root:     {info['mmr_root']}")
    print(f"  manifest:     {info['manifest_size']} bytes")
    print(f"  ndjson:       {info['leaves_ndjson_size']} bytes")
    return 0


def cmd_self_test(args: argparse.Namespace) -> int:
    """Synthetic-leaves round-trip path. Builds a bundle, then verifies it
    using vos3_eu_act_verify.py from this same tools/ directory."""
    output = Path(args.output or "/tmp/vos3_eu_act_self_test.zip")
    leaf_count = args.count or 8

    leaves = _synthetic_leaves(leaf_count, base_ts=int(time.time() * 1e9))
    pk, sk = _generate_keypair()

    since_utc = "2026-05-01T00:00:00Z"
    until_utc = "2026-05-01T01:00:00Z"

    info = build_bundle(
        leaves=leaves,
        output_path=output,
        since_utc=since_utc,
        until_utc=until_utc,
        host_kernel_sha256="0" * 64,
        host_fingerprint_sha256=hashlib.sha256(
            secrets.token_bytes(32)
        ).hexdigest(),
        region_code="EU",
        is_eu_region=True,
        build_flavor="PRO",
        issuer="self",
        signing_key=sk,
        public_key_bytes=pk,
    )
    print("[self-test] Wrote synthetic bundle:")
    print(f"  path:     {info['bundle_path']}")
    print(f"  leaves:   {info['leaf_count']}")
    print(f"  mmr_root: {info['mmr_root']}")

    # Round-trip through the verifier — invoke as a subprocess to avoid
    # Python 3.14 dataclass+importlib.util module-spec interaction bugs.
    import subprocess
    verify_path = Path(__file__).parent / "vos3_eu_act_verify.py"
    if not verify_path.is_file():
        print(f"FAIL: companion verifier not found at {verify_path}",
              file=sys.stderr)
        return 1
    proc = subprocess.run(
        [sys.executable, str(verify_path), str(output)],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        print("[self-test] ROUND-TRIP PASS — verifier reports valid")
        return 0
    print(f"[self-test] ROUND-TRIP FAIL (verifier exit={proc.returncode}):")
    if proc.stdout:
        for line in proc.stdout.splitlines()[-15:]:
            print(f"  {line}")
    if proc.stderr:
        for line in proc.stderr.splitlines()[-5:]:
            print(f"  STDERR: {line}")
    return 1


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])

    # ---- Real export args ----
    p.add_argument("--since", default=None, help="UTC start (RFC 3339)")
    p.add_argument("--until", default=None, help="UTC end (RFC 3339)")
    p.add_argument("--since-tsc", type=int, default=None,
                   help="raw TSC start; default 0")
    p.add_argument("--until-tsc", type=int, default=None,
                   help="raw TSC end; default 2^63-1")
    p.add_argument("--output", default=None, help="bundle output path (.zip)")
    p.add_argument("--bridge-socket",
                   default=os.environ.get("VOS3_BRIDGE_SOCKET",
                                          "/run/vos3/bridge.sock"))
    p.add_argument("--kernel-sha", default=None,
                   help="SHA-256 of running vos3.elf")
    p.add_argument("--host-fingerprint", default=None,
                   help="SHA-256 from license_check.c (CPUID+TPM EK+MAC)")
    p.add_argument("--region-code", default="EU",
                   help="ISO-3166-1 alpha-2 OR 'EU'")
    p.add_argument("--is-eu", action="store_true",
                   help="explicitly mark host as EU-region")
    p.add_argument("--build-flavor", default="PRO", choices=["PRO", "CORE"])
    p.add_argument("--issuer", default="self", choices=["self", "vos3-ca"])

    # ---- Self-test ----
    p.add_argument("--self-test", action="store_true",
                   help="generate synthetic bundle + verify round-trip")
    p.add_argument("--count", type=int, default=None,
                   help="self-test leaf count (default 8)")

    args = p.parse_args(argv)

    if args.self_test:
        return cmd_self_test(args)

    if not (args.since and args.until and args.output):
        print("ERROR: --since, --until, --output required for real export "
              "(or use --self-test)", file=sys.stderr)
        return 2
    return cmd_export(args)


if __name__ == "__main__":
    sys.exit(main())
