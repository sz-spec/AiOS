"""
v20.1.4 — Development-tier signing pipeline for release artifacts.

**What this is:** a locally-rooted ECDSA P-256 sign/verify pipeline proving
the end-to-end flow works against real artifacts (notably the CycloneDX
SBOM). Produces the same envelope shape (signature + public key) the
production Sigstore flow will emit.

**What this is NOT:** a production release signature. Production signing
uses ``cosign sign-blob`` with GitHub-Actions OIDC identity, which writes
the signing event to the Rekor v2 public transparency log and produces a
bundle verifiable against the Sigstore root of trust. **This script does
not write to Rekor.** Any artifact signed here is labeled as ``dev-tier``
in the metadata; ``infra/security/sigstore_verify.py`` enforces
Cosign ≥ 2.6.2 + Rekor bundle for the production path.

Why both exist:
    1. Dev pipeline proves: "our artifacts are hashable, signable, and
       round-trip-verifiable". Scheduled 2026-05-06 ceremony replaces
       the dev key with an OIDC-attested one and attaches a Rekor
       bundle. Nothing else changes in the release workflow.
    2. Keeping the dev pipeline separately named + reviewed prevents an
       accidental prod-labeled artifact from shipping with a local key.

Files produced next to ``<artifact>``:
    <artifact>.sig   — base64-encoded DER ECDSA signature
    <artifact>.pub   — PEM-encoded public key
    <artifact>.meta  — JSON envelope (artifact SHA-256 + signing alg +
                        dev-tier flag + timestamp)

Usage:
    python -m infra.security.dev_sign \\
        --key-dir  infra/security/keys \\
        --artifact infra/security/vos3_code_sbom.json

    # Verify
    python -m infra.security.dev_sign --verify \\
        --artifact infra/security/vos3_code_sbom.json
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


_KEY_FILENAME = "vos3_dev_signing.key"
_PUB_FILENAME = "vos3_dev_signing.pub"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_keypair(key_dir: Path) -> tuple[ec.EllipticCurvePrivateKey,
                                            ec.EllipticCurvePublicKey]:
    """Create-if-missing an ECDSA P-256 keypair for dev-tier signing."""
    key_dir.mkdir(parents=True, exist_ok=True)
    priv_path = key_dir / _KEY_FILENAME
    pub_path  = key_dir / _PUB_FILENAME

    if priv_path.is_file() and pub_path.is_file():
        priv = serialization.load_pem_private_key(
            priv_path.read_bytes(), password=None)
        pub = priv.public_key()
        return priv, pub

    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key()
    priv_path.write_bytes(priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    pub_path.write_bytes(pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    # Restrict private key perms.
    priv_path.chmod(0o600)
    return priv, pub


def sign(artifact: Path, key_dir: Path) -> Path:
    priv, _pub = ensure_keypair(key_dir)
    sha = _sha256(artifact)
    sig_bytes = priv.sign(artifact.read_bytes(), ec.ECDSA(hashes.SHA256()))

    sig_path  = artifact.with_suffix(artifact.suffix + ".sig")
    meta_path = artifact.with_suffix(artifact.suffix + ".meta")
    sig_path.write_text(base64.b64encode(sig_bytes).decode("ascii") + "\n")

    meta = {
        "artifact":       str(artifact.relative_to(artifact.parents[1])),
        "artifact_sha256": sha,
        "alg":            "ECDSA-P256-SHA256",
        "tier":           "dev",
        "signed_at":      datetime.now(timezone.utc).isoformat(),
        "key_fingerprint": hashlib.sha256(
            (key_dir / _PUB_FILENAME).read_bytes()).hexdigest()[:16],
        "note": (
            "Development-tier signature. Production release signing goes "
            "through cosign >= 2.6.2 + GitHub-Actions OIDC identity + "
            "Rekor v2 transparency log. See infra/security/sigstore_verify.py."
        ),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    return sig_path


def verify(artifact: Path, key_dir: Path) -> tuple[bool, str]:
    pub_path = key_dir / _PUB_FILENAME
    sig_path = artifact.with_suffix(artifact.suffix + ".sig")
    meta_path = artifact.with_suffix(artifact.suffix + ".meta")
    for p in (pub_path, sig_path, meta_path):
        if not p.is_file():
            return False, f"missing {p}"
    pub = serialization.load_pem_public_key(pub_path.read_bytes())
    sig = base64.b64decode(sig_path.read_text().strip())
    try:
        pub.verify(sig, artifact.read_bytes(), ec.ECDSA(hashes.SHA256()))
    except Exception as e:
        return False, f"signature mismatch: {type(e).__name__}"
    meta = json.loads(meta_path.read_text())
    measured = _sha256(artifact)
    if meta.get("artifact_sha256") != measured:
        return False, (f"artifact SHA-256 drift: "
                       f"meta={meta.get('artifact_sha256')[:16]}… "
                       f"measured={measured[:16]}…")
    return True, (f"verified  tier={meta.get('tier')}  "
                  f"alg={meta.get('alg')}  "
                  f"key_fp={meta.get('key_fingerprint')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True, type=Path)
    ap.add_argument("--key-dir", type=Path, default=Path("infra/security/keys"))
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    if args.verify:
        ok, msg = verify(args.artifact, args.key_dir)
        print(msg)
        return 0 if ok else 1

    sig_path = sign(args.artifact, args.key_dir)
    print(f"signed   {args.artifact}")
    print(f"         {sig_path}")
    print(f"         {args.artifact.with_suffix(args.artifact.suffix + '.meta')}")
    ok, msg = verify(args.artifact, args.key_dir)
    print(f"verify   {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
