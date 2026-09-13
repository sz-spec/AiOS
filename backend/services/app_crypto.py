"""
backend/services/app_crypto.py — Ed25519 manifest signing/verification.

P5.2 — every "production" app manifest is signed by its developer
with an Ed25519 keypair. The signature lives in the `signature`
column of the `apps` table; the corresponding raw public key lives
in `developer_public_key`. Both are stored as hex strings.

Verification chain
------------------

  ┌─────────────────────────────────────────────────────────────┐
  │ Developer:                                                  │
  │   keypair = Ed25519PrivateKey.generate()                    │
  │   manifest_bytes = canonical_manifest_bytes(manifest)       │
  │   signature_hex = sign(keypair, manifest_bytes)             │
  │   → ships {manifest, signature_hex, public_key_hex}         │
  └────────────────────────┬────────────────────────────────────┘
                           │
                           ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ AppSandboxManager.install():                                │
  │   1. canonicalize manifest → bytes                          │
  │   2. verify_manifest_signature(bytes, sig, pubkey)          │
  │   3. on success → persist manifest + sig + pubkey           │
  │   4. on failure → InvalidManifest (no row written)          │
  └────────────────────────┬────────────────────────────────────┘
                           │
                           ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ PermissionGate._hydrate_from_db() — every cache miss:        │
  │   1. read row.manifest_json + row.signature + row.pubkey    │
  │   2. canonicalize manifest_json → bytes                     │
  │   3. verify_manifest_signature(bytes, sig, pubkey)          │
  │   4. on failure → row.status='isolated'                     │
  │                  → _record_security_event(manifest_tampered)│
  │                  → raise ManifestTampered                   │
  │                  (the dispatcher / route layer translates   │
  │                   it to HTTPException(403))                 │
  └─────────────────────────────────────────────────────────────┘

Canonicalization
----------------
The signature is taken over `canonical_manifest_bytes(manifest_dict)`
— a deterministic JSON dump with sorted keys and no whitespace.
This makes the signature stable across:
  * key reordering in transit
  * pretty-printing in dev tools
  * Python dict iteration order differences across versions
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CryptoUnavailable(RuntimeError):
    """Raised when the `cryptography` package isn't importable. Dev
    boxes can run unsigned installs; signing/verification anywhere
    in the chain becomes an explicit error so operators can't
    silently downgrade from signed to unsigned by uninstalling the
    library."""


# ---------------------------------------------------------------------------
# Lazy loader — keeps the broader backend bootable when the
# `cryptography` C extensions aren't on the host.
# ---------------------------------------------------------------------------


def _ed25519_module():
    """Return the `cryptography.hazmat.primitives.asymmetric.ed25519`
    module or raise CryptoUnavailable with an install hint."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise CryptoUnavailable(
            "The `cryptography` package is required for signed app "
            "manifests. Install it with: "
            "`pip install cryptography`."
        ) from exc
    return ed25519


def _serialization_module():
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:
        raise CryptoUnavailable("The `cryptography` package is required.") from exc
    return serialization


def is_available() -> bool:
    """Probe — True iff the cryptography backend can be loaded."""
    try:
        _ed25519_module()
        return True
    except CryptoUnavailable:
        return False


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def canonical_manifest_bytes(manifest: dict) -> bytes:
    """Deterministic byte form of a manifest dict.

    Uses `sort_keys=True` + tight separators so different JSON
    libraries / formatters can re-emit the same bytes. The byte
    output IS what gets signed — NOT the raw JSON the operator
    passed in.

    Lists keep their order (scopes / restrictions are order-
    sensitive to the developer's intent). Nested dicts get
    recursively sorted via `sort_keys`.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a dict")
    return json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def manifest_digest_sha256(manifest: dict) -> str:
    """Hex SHA-256 of the canonical manifest bytes — useful for
    debugging mismatches without exposing the signature."""
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def to_signable_form(manifest: dict) -> dict:
    """Return the manifest in the shape the install path will store.

    The install pipeline runs `AppManifest.from_dict()` which:
      * strips whitespace from `name` / `version`
      * coerces `scopes` / `restrictions` to ordered lists
      * fills in `config: None` when omitted
      * drops any non-schema fields

    Developers MUST sign THIS form — not the raw dict they author
    in the IDE — so the hydration verifier (which canonicalizes the
    persisted form) computes the same byte stream.

    Lazy-imports AppManifest so this helper stays usable outside
    the sandbox-manager import path (e.g. in a developer signing
    CLI that doesn't want to pull the whole backend).
    """
    from services.app_sandbox import AppManifest  # late import — avoids cycle

    am = AppManifest.from_dict(manifest)
    return json.loads(am.to_json())


# ---------------------------------------------------------------------------
# Key generation + signing — primarily used by tests + dev tooling
# ---------------------------------------------------------------------------


def generate_keypair_hex() -> tuple:
    """Mint a fresh Ed25519 keypair and return (private_hex, public_hex).

    The private hex is 32 raw bytes (64 hex chars); the public hex
    matches `Ed25519PublicKey.public_bytes(Raw, Raw)` (also 32
    bytes / 64 hex chars).
    """
    ed25519 = _ed25519_module()
    serialization = _serialization_module()
    priv = ed25519.Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv_raw.hex(), pub_raw.hex()


def sign_manifest(manifest: dict, *, private_key_hex: str) -> str:
    """Return the hex-encoded Ed25519 signature for `manifest`.

    The byte form being signed is `canonical_manifest_bytes(manifest)`.
    """
    ed25519 = _ed25519_module()
    _serialization_module()
    try:
        priv_raw = bytes.fromhex(private_key_hex)
    except ValueError as exc:
        raise ValueError("private_key_hex must be valid hex") from exc
    if len(priv_raw) != 32:
        raise ValueError("Ed25519 private key must be 32 bytes (64 hex chars)")
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(priv_raw)
    sig = priv.sign(canonical_manifest_bytes(manifest))
    return sig.hex()


# ---------------------------------------------------------------------------
# Verification — the only entry point install/hydrate call
# ---------------------------------------------------------------------------


def verify_manifest_signature(
    manifest_bytes: bytes,
    signature_hex: str,
    public_key_hex: str,
) -> bool:
    """Verify an Ed25519 signature against canonical manifest bytes.

    Returns True iff the signature is well-formed AND verifies
    against `public_key_hex`. Returns False on any failure path:
      * hex decode error
      * wrong key length
      * cryptographic verification mismatch

    Raises only `CryptoUnavailable` — never wraps a verification
    failure in an exception (callers branch on the bool).
    """
    if not isinstance(manifest_bytes, (bytes, bytearray)):
        return False
    if not isinstance(signature_hex, str) or not signature_hex:
        return False
    if not isinstance(public_key_hex, str) or not public_key_hex:
        return False

    ed25519 = _ed25519_module()
    try:
        sig = bytes.fromhex(signature_hex)
        pub_raw = bytes.fromhex(public_key_hex)
    except ValueError:
        return False
    if len(pub_raw) != 32:
        return False
    # Ed25519 signatures are always exactly 64 bytes.
    if len(sig) != 64:
        return False

    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(pub_raw)
    except Exception:  # noqa: BLE001 - unparsable key bytes
        return False

    # Lazy import — cryptography raises a specific InvalidSignature.
    try:
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        return False

    try:
        pub.verify(sig, bytes(manifest_bytes))
        return True
    except InvalidSignature:
        return False
    except Exception as exc:  # noqa: BLE001
        logger.debug("verify_manifest_signature unexpected error: %s", exc)
        return False


def verify_manifest_dict(
    manifest: dict,
    *,
    signature_hex: Optional[str],
    public_key_hex: Optional[str],
) -> bool:
    """Convenience wrapper: canonicalize THEN verify.

    Returns False if either `signature_hex` or `public_key_hex` is
    missing — i.e. "unsigned" manifests never pass this gate.
    Callers that want to allow unsigned dev installs must branch
    on the (None, None) case BEFORE calling this.
    """
    if not signature_hex or not public_key_hex:
        return False
    try:
        canon = canonical_manifest_bytes(manifest)
    except (TypeError, ValueError):
        return False
    return verify_manifest_signature(canon, signature_hex, public_key_hex)


__all__ = [
    "CryptoUnavailable",
    "is_available",
    "canonical_manifest_bytes",
    "manifest_digest_sha256",
    "to_signable_form",
    "generate_keypair_hex",
    "sign_manifest",
    "verify_manifest_signature",
    "verify_manifest_dict",
]
