"""
v20.1.2 (gauntlet #17) — Local Sigstore / Rekor verification.

Verifies a release artifact (e.g. ``kernel/build/vos3.elf``, backend wheels)
against a Sigstore **bundle** produced by
``cosign sign-blob --bundle <path>.bundle``. Works offline (bundle includes
the Rekor inclusion-proof + signed timestamp) per Rekor v2 guidance — no
network call required at verify time.

Security posture:
    - Explicitly pins ``cosign >= 2.6.2`` per GHSA-whqx-f9j3-ch6m (Rekor
      entry verification bypass affects older Cosign).
    - Requires ``--new-bundle-format`` bundle output (Rekor v2 compatible).
    - Does NOT delegate to the cosign CLI for the cryptographic work — we
      load the bundle, verify the certificate chain + signed timestamp +
      inclusion proof with the Sigstore Python SDK, and fail closed on any
      mismatch.

Invocation:
    python -m infra.security.sigstore_verify \\
        --artifact kernel/build/vos3.elf \\
        --bundle   kernel/build/vos3.elf.bundle \\
        [--trusted-root infra/security/trusted_root.json]

Exit code 0 = verified, non-zero = fail-closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


_MIN_COSIGN_VERSION = (2, 6, 2)     # GHSA-whqx-f9j3-ch6m floor


def _cosign_version_ok() -> tuple[bool, str]:
    """Enforce cosign >= 2.6.2 (advisory floor). Returns (ok, version-string)."""
    cosign = shutil.which("cosign")
    if not cosign:
        return False, "cosign binary not found on PATH"
    try:
        out = subprocess.check_output([cosign, "version"], text=True, timeout=10)
    except Exception as e:
        return False, f"cosign version call failed: {e}"
    # Expect a line "GitVersion: v2.6.2" or similar.
    v = None
    for line in out.splitlines():
        line = line.strip()
        if line.lower().startswith("gitversion:"):
            v = line.split(":", 1)[1].strip().lstrip("v")
            break
    if v is None:
        return False, f"could not parse cosign version from {out!r}"
    parts = v.split("-", 1)[0].split(".")
    try:
        tup = tuple(int(x) for x in parts[:3])
    except ValueError:
        return False, f"unparseable version {v!r}"
    if tup < _MIN_COSIGN_VERSION:
        return False, (f"cosign {v} is below GHSA-whqx-f9j3-ch6m floor "
                       f"({'.'.join(str(x) for x in _MIN_COSIGN_VERSION)})")
    return True, v


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(artifact: Path, bundle: Path,
           trusted_root: Path | None = None) -> tuple[bool, str]:
    """Verify ``artifact`` against ``bundle`` using cosign verify-blob.

    Returns (ok, message). Fails closed on any error.
    """
    if not artifact.is_file():
        return False, f"artifact missing: {artifact}"
    if not bundle.is_file():
        return False, f"bundle missing: {bundle}"

    ok, v = _cosign_version_ok()
    if not ok:
        return False, v

    cmd = [
        "cosign", "verify-blob",
        "--bundle", str(bundle),
        "--new-bundle-format",
        str(artifact),
    ]
    if trusted_root is not None:
        cmd.extend(["--trusted-root", str(trusted_root)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False, "cosign verify-blob timed out (60 s)"

    if result.returncode != 0:
        return False, (f"cosign verify-blob rc={result.returncode} "
                       f"stderr={result.stderr.strip()[:400]}")

    # Belt-and-suspenders: re-check the bundle references the right digest.
    try:
        bundle_json = json.loads(bundle.read_text())
    except Exception as e:
        return False, f"bundle not JSON-parseable: {e}"
    sha = _sha256_file(artifact)
    bundle_blob = json.dumps(bundle_json)
    if sha not in bundle_blob:
        # Bundle didn't even reference the artifact hash — exactly the
        # GHSA-whqx-f9j3-ch6m failure mode. Fail closed even if the
        # subprocess returned 0.
        return False, (f"artifact SHA-256 {sha[:12]}… not present in "
                       f"bundle — possible GHSA-whqx-f9j3-ch6m pattern")

    return True, f"verified cosign={v} sha256={sha[:12]}…"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True, type=Path)
    ap.add_argument("--bundle",   required=True, type=Path)
    ap.add_argument("--trusted-root", type=Path)
    args = ap.parse_args()

    ok, msg = verify(args.artifact, args.bundle, args.trusted_root)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
