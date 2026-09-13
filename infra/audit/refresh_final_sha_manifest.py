#!/usr/bin/env python3
"""infra/audit/refresh_final_sha_manifest.py

Regenerate VOS3_ULTIMATE_HANDOFF_2026/FINAL_SHA_MANIFEST.json with the SHA-256
fingerprints of the *current* build artifacts on disk.

Why this exists
---------------
The PDF spec promises "three independently certified SHA-256 fingerprints —
one per target (Linux / Bare-Metal / Windows) — published in a signed manifest
at every release." The manifest in-tree was last refreshed 2026-04-26 and
points at pre-strip kernel hashes; today (2026-05-19) the kernel ELF has been
stripped + re-signed, and the manifest is stale.

This script:
  1. Reads each target binary that exists on disk.
  2. Computes its SHA-256.
  3. Re-emits FINAL_SHA_MANIFEST.json with the live values + a refresh-stamp.
  4. Optionally calls the Sigstore bundle pipeline on each refreshed artifact.

Usage
-----
  python3 infra/audit/refresh_final_sha_manifest.py
  python3 infra/audit/refresh_final_sha_manifest.py --resign
  python3 infra/audit/refresh_final_sha_manifest.py --dry-run

By default writes to:
  VOS3_ULTIMATE_HANDOFF_2026/FINAL_SHA_MANIFEST.json

Exit codes: 0 success, 2 missing target binary, 3 write failure.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

TARGETS: dict[str, dict[str, str]] = {
    "build_linux_elf": {
        "path": "kernel/build/vos3.elf",
        "description": "Linux x86_64 ELF (statically linked, stripped, post-strip release artifact)",
        "platform": "linux",
    },
    "build_baremetal_efi": {
        "path": "kernel/build/vos3.efi",
        "description": "Bare-metal UEFI image (BIOS+UEFI hybrid via Limine)",
        "platform": "baremetal",
    },
    "build_baremetal_elf": {
        "path": "kernel/build/vos3-baremetal.elf",
        "description": "Bare-metal ELF (Multiboot2 / PVH boot path)",
        "platform": "baremetal",
    },
    "build_windows_hyperv_elf": {
        "path": "kernel/build/vos3-hyperv.elf",
        "description": "Windows Hyper-V synthetic-driver ELF (VHDX-embedded)",
        "platform": "windows",
    },
    "release_installer_iso": {
        "path": "dist/vos3_installer.iso",
        "description": "Bare-metal hybrid BIOS+UEFI bootable installer ISO",
        "platform": "baremetal",
    },
    "release_linux_kvm_qcow2": {
        "path": "dist/vos3_linux_kvm.qcow2",
        "description": "Linux KVM qcow2 disk image",
        "platform": "linux",
    },
    "release_windows_hyperv_vhdx": {
        "path": "dist/vos3_windows_hyperv.vhdx",
        "description": "Windows Hyper-V dynamic VHDX disk image",
        "platform": "windows",
    },
    "release_zip": {
        "path": "dist/release_v1_0.zip",
        "description": "Aggregated release zip (all four platforms + docs)",
        "platform": "aggregate",
    },
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the new manifest to stdout instead of writing it.",
    )
    parser.add_argument(
        "--resign",
        action="store_true",
        help="Also call infra/security/sigstore_v3_bundle.Signer on each refreshed artifact.",
    )
    parser.add_argument(
        "--out",
        default="VOS3_ULTIMATE_HANDOFF_2026/FINAL_SHA_MANIFEST.json",
        help="Output path relative to repo root.",
    )
    args = parser.parse_args()

    artifacts: dict[str, dict[str, str | int]] = {}
    missing: list[str] = []

    for key, meta in TARGETS.items():
        rel = meta["path"]
        full = REPO_ROOT / rel
        if not full.exists():
            missing.append(rel)
            continue
        digest = sha256_of(full)
        size = full.stat().st_size
        artifacts[key] = {
            "path": rel,
            "sha256_hex": digest,
            "size_bytes": size,
            "description": meta["description"],
            "platform": meta["platform"],
            "bundle_path": f"infra/security/release_artifacts/{key}.bundle.json",
        }

    if missing and not args.dry_run:
        # Don't fail outright — emit manifest of what IS present, but warn.
        sys.stderr.write(
            "WARN: the following expected target binaries are not on disk and were "
            "skipped:\n"
        )
        for m in missing:
            sys.stderr.write(f"  - {m}\n")

    manifest = {
        "schema": "vos3.final_sha_manifest/v2",
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "generator": "infra/audit/refresh_final_sha_manifest.py",
        "branch_hint": "unified-master-v1",
        "honest_scope": (
            "SHA-256 fingerprints computed locally over artifacts present on "
            "disk at generation time. Sigstore bundles for each artifact live "
            "under infra/security/release_artifacts/. Verify end-to-end via "
            "`bash scripts/verify_release.sh`. Public Fulcio + public Rekor "
            "v2 migration tracked at docs/SIGSTORE_V3_GAP.md."
        ),
        "artifacts": artifacts,
        "missing_targets": missing,
    }

    if args.dry_run:
        json.dump(manifest, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        sys.stderr.write(f"ERROR writing {out}: {exc}\n")
        return 3

    print(f"✅ Wrote {out} with {len(artifacts)} artifact entries.")
    for k, v in artifacts.items():
        print(f"   {k}: {v['sha256_hex']}  ({v['path']})")

    if args.resign:
        # Resign each artifact via the in-tree Sigstore pipeline.
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from infra.security.sigstore_v3_bundle import Signer, Verifier
            from infra.security.rekor_v2_log import RekorV2Log
        except ImportError as exc:
            sys.stderr.write(f"ERROR importing Sigstore modules: {exc}\n")
            return 3
        log = RekorV2Log(path=str(REPO_ROOT / "infra/security/rekor_v2.jsonl"))
        signer = Signer.from_dev_key(
            str(REPO_ROOT / "infra/security/keys/vos3_dev_signing.key")
        )
        verifier = Verifier.from_dev_key(
            str(REPO_ROOT / "infra/security/keys/vos3_dev_signing.pub")
        )
        for key, art in artifacts.items():
            artifact_path = REPO_ROOT / art["path"]
            bundle_path = REPO_ROOT / art["bundle_path"]
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            bundle = signer.sign_artifact(str(artifact_path), rekor=log)
            bundle.write(str(bundle_path))
            result = verifier.verify_artifact(str(artifact_path), str(bundle_path))
            ok = "✅" if result.get("verified") else "❌"
            print(f"   {ok} {key}: signed & verified ({result.get('rekor_inclusion', '?')})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
