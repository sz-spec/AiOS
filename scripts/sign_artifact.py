#!/usr/bin/env python3
"""scripts/sign_artifact.py — Sign an arbitrary artifact with the project's
in-tree Sigstore v3-shaped dev signing chain.

Used by CI (.github/workflows/build-msi.yml) and operators who want to attach
an authenticity bundle to any release artifact (MSI, ISO, qcow2, VHDX, etc.).

Usage:
  python scripts/sign_artifact.py <artifact-path> [<bundle-out-path>]

Default bundle output: <artifact-path>.bundle.json

Exit codes:
  0  signed + verified
  1  verification failed
  2  artifact missing / I/O error
  3  signing key missing
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str]) -> int:
    if len(argv) < 2 or len(argv) > 3:
        sys.stderr.write(f"usage: {argv[0]} <artifact> [<bundle.json>]\n")
        return 2

    artifact = Path(argv[1])
    if not artifact.exists():
        sys.stderr.write(f"ERROR: artifact not found: {artifact}\n")
        return 2

    bundle_path = Path(argv[2]) if len(argv) == 3 else artifact.with_suffix(
        artifact.suffix + ".bundle.json"
    )

    sys.path.insert(0, str(REPO_ROOT))

    key_path = REPO_ROOT / "infra/security/keys/vos3_dev_signing.key"
    pub_path = REPO_ROOT / "infra/security/keys/vos3_dev_signing.pub"
    rekor_path = REPO_ROOT / "infra/security/rekor_v2.jsonl"

    if not key_path.exists() or not pub_path.exists():
        sys.stderr.write(
            f"ERROR: dev signing key pair missing at {key_path.parent}/\n"
        )
        return 3

    try:
        from infra.security.sigstore_v3_bundle import Signer, Verifier
        from infra.security.rekor_v2_log import RekorV2Log
    except ImportError as exc:
        sys.stderr.write(f"ERROR: cannot import Sigstore modules: {exc}\n")
        return 3

    log = RekorV2Log(path=str(rekor_path))
    signer = Signer.from_dev_key(str(key_path))
    bundle = signer.sign_artifact(str(artifact), rekor=log)
    bundle.write(str(bundle_path))

    result = Verifier.from_dev_key(str(pub_path)).verify_artifact(
        str(artifact), str(bundle_path)
    )

    verified = bool(result.get("verified"))
    rekor = result.get("rekor_inclusion", "unknown")
    sha = result.get("computed_sha256_hex", "")

    print(f"artifact:        {artifact}")
    print(f"bundle:          {bundle_path}")
    print(f"sha256:          {sha}")
    print(f"verified:        {verified}")
    print(f"rekor_inclusion: {rekor}")
    print(f"tier:            dev (CN=VOS3-DEV-NOT-FULCIO)")

    if not verified or rekor != "passed":
        sys.stderr.write("ERROR: post-sign verification failed\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
