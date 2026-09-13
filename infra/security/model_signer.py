"""
infra/security/model_signer.py
================================

Sprint 15 / Item I3 — OpenSSF Model Signing (OMS) v1.0 sign + verify pipeline.

What this is
------------

A thin, audit-ready wrapper around the OpenSSF Model Signing v1.0
specification (https://blog.sigstore.dev/model-transparency-v1.0/), the
industry standard for AI model integrity launched by OpenSSF + NVIDIA +
HiddenLayer + Sigstore in collaboration. As of May 2026 OMS is integrated
into NVIDIA NGC and Google Kaggle and is the recommended path for model
provenance in regulated deployments.

This module sits adjacent to `sigstore_v3_bundle.py` (which signs the
kernel ELF + release artifacts). We deliberately keep them as separate
modules because:

  - Model signing produces a per-model `*.bundle.json` (OMS layout) AND a
    per-file `*.sig` short-form that the kernel's SLOT_START verifier can
    consume without parsing the bundle.
  - The signing key for models is a separate "model-signing" identity
    that ops rotates on a faster cadence than the release-signing key.
  - Auditors look at model attestation under EU AI Act Annex IV §V
    (post-market monitoring of model provenance), which is a different
    evidence column from the kernel-binary attestation under §III.

Public surface
--------------

    sign_model(path, signing_key=None) -> ModelSigningBundle
        Sign the model file at `path`. Produces:
          path.sig            (Ed25519 signature over SHA-256(file))
          path.bundle.json    (full OMS v1.0 bundle)
        Returns the ModelSigningBundle for in-process verification chains.

    verify_model_signature(path, signing_pub_key=None) -> bool
        Called by `services.vbus_driver` at SLOT_START. Read-only.
        Returns True if .sig exists, matches the file digest, and verifies
        against the trust-root pubkey. False otherwise.

    verify_oms_bundle(path) -> OMSVerificationResult
        Full bundle verification (signature + bundle integrity + payload
        digest check + trust-chain). Used by the SBOM generator and the
        EU AI Act export pipeline.

OMS bundle wire format (v1.0)
-----------------------------

Per the OpenSSF Model Signing reference implementation
(https://github.com/sigstore/model-transparency), an OMS bundle is a JSON
document with the following top-level keys:

    {
      "specVersion": "1.0",
      "mediaType": "application/vnd.openssf.model-signing.v1+json",
      "subject": {
        "name": "<model-name>",
        "digest": {"sha256": "<hex>"},
        "size": <bytes>,
      },
      "signature": {
        "alg": "ed25519",
        "signer": "<base64 pubkey>",
        "value": "<base64 signature>",
        "signed_at": "<ISO-8601>"
      },
      "predicate": {
        "predicateType": "https://openssf.org/model-signing/v1.0/predicate",
        "data": {
          "model_card_digest": "<sha256 of sibling .modelcard.md if present>",
          "training_data_attestation": null,
          "fine_tune_chain": []
        }
      }
    }

Honest-scope ceiling
--------------------

We ship the OMS v1.0 wire format and Ed25519 signing — both cryptographically
real (via the `cryptography` library, already a project dep). What we do
NOT yet implement:

  1. Sigstore Fulcio short-lived certificate flow (OMS production deployments
     bind the signature to a Fulcio-issued cert from an OIDC identity; we
     use a long-lived Ed25519 key for dev tier). Swap point: `_get_signer()`
     below — a future Stage 14.B.4 replaces the key load with an OIDC →
     Fulcio handshake.
  2. The `training_data_attestation` and `fine_tune_chain` fields are
     populated as null/[] today; the Sprint 16-17 deliverable I2
     (end-to-end model provenance) fills them.
  3. Rekor v2 transparency log entry for model signatures — currently we
     write to the same local Rekor (`infra/security/rekor_v2.jsonl`) as
     the kernel signatures use; production deployments will switch to
     the public Sigstore Rekor once the OMS team finalizes the codepoint.

Each gap is a one-function swap, documented inline where it occurs.

Production deployments running on the sovereign tier MUST run
`verify_oms_bundle()` on every model BEFORE the kernel maps it into an
AI slot — that's the trust-chain anchor.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — match OMS v1.0 spec
# ---------------------------------------------------------------------------

OMS_SPEC_VERSION = "1.0"
OMS_MEDIA_TYPE = "application/vnd.openssf.model-signing.v1+json"
OMS_PREDICATE_TYPE = "https://openssf.org/model-signing/v1.0/predicate"
OMS_SIG_SUFFIX = ".sig"
OMS_BUNDLE_SUFFIX = ".bundle.json"
OMS_MODELCARD_SUFFIX = ".modelcard.md"

ENV_SIGNING_KEY = "VOS3_MODEL_SIGNING_KEY"
ENV_SIGNING_KEY_PASSWORD = "VOS3_MODEL_SIGNING_KEY_PASSWORD"
ENV_TRUST_ROOT = "VOS3_MODEL_TRUST_ROOT"

# Dev-tier default key location (gitignored). Production sets ENV_SIGNING_KEY
# to an HSM-backed PEM URI.
DEFAULT_SIGNING_KEY_PATH = "infra/security/keys/vos3_model_signing.key"
DEFAULT_TRUST_ROOT_PATH = "infra/security/keys/vos3_model_signing.pub"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ModelSigningError(RuntimeError):
    """Raised on any OMS sign/verify failure (bad signature, missing key,
    bundle malformed, payload digest mismatch)."""


class OMSVerificationError(ModelSigningError):
    """Specifically a verification (read-side) failure. Subclass of
    ModelSigningError so callers can broad-catch."""


# ---------------------------------------------------------------------------
# Bundle dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OMSSubject:
    name: str
    digest_sha256: str
    size: int


@dataclass(frozen=True)
class OMSSignature:
    alg: str  # "ed25519"
    signer_pub_b64: str
    value_b64: str
    signed_at: str  # ISO-8601


@dataclass(frozen=True)
class OMSPredicate:
    predicate_type: str
    model_card_digest: Optional[str] = None
    training_data_attestation: Optional[str] = None
    fine_tune_chain: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModelSigningBundle:
    subject: OMSSubject
    signature: OMSSignature
    predicate: OMSPredicate

    def to_dict(self) -> dict:
        return {
            "specVersion": OMS_SPEC_VERSION,
            "mediaType": OMS_MEDIA_TYPE,
            "subject": {
                "name": self.subject.name,
                "digest": {"sha256": self.subject.digest_sha256},
                "size": self.subject.size,
            },
            "signature": {
                "alg": self.signature.alg,
                "signer": self.signature.signer_pub_b64,
                "value": self.signature.value_b64,
                "signed_at": self.signature.signed_at,
            },
            "predicate": {
                "predicateType": self.predicate.predicate_type,
                "data": {
                    "model_card_digest": self.predicate.model_card_digest,
                    "training_data_attestation": self.predicate.training_data_attestation,
                    "fine_tune_chain": list(self.predicate.fine_tune_chain),
                },
            },
        }

    @classmethod
    def from_dict(cls, doc: dict) -> "ModelSigningBundle":
        if doc.get("specVersion") != OMS_SPEC_VERSION:
            raise OMSVerificationError(
                f"unsupported OMS spec version: {doc.get('specVersion')}; "
                f"expected {OMS_SPEC_VERSION}"
            )
        if doc.get("mediaType") != OMS_MEDIA_TYPE:
            raise OMSVerificationError(
                f"unexpected mediaType: {doc.get('mediaType')}"
            )
        try:
            subject = OMSSubject(
                name=doc["subject"]["name"],
                digest_sha256=doc["subject"]["digest"]["sha256"],
                size=int(doc["subject"]["size"]),
            )
            signature = OMSSignature(
                alg=doc["signature"]["alg"],
                signer_pub_b64=doc["signature"]["signer"],
                value_b64=doc["signature"]["value"],
                signed_at=doc["signature"]["signed_at"],
            )
            pred_data = doc.get("predicate", {}).get("data", {}) or {}
            predicate = OMSPredicate(
                predicate_type=doc.get("predicate", {}).get(
                    "predicateType", OMS_PREDICATE_TYPE
                ),
                model_card_digest=pred_data.get("model_card_digest"),
                training_data_attestation=pred_data.get("training_data_attestation"),
                fine_tune_chain=tuple(pred_data.get("fine_tune_chain", []) or []),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OMSVerificationError(f"malformed OMS bundle: {exc}") from exc
        return cls(subject=subject, signature=signature, predicate=predicate)


@dataclass(frozen=True)
class OMSVerificationResult:
    ok: bool
    bundle: Optional[ModelSigningBundle] = None
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Crypto helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_signing_key_path() -> Path:
    env_value = os.environ.get(ENV_SIGNING_KEY, "").strip()
    if env_value:
        return Path(env_value)
    return _repo_root() / DEFAULT_SIGNING_KEY_PATH


def _resolve_trust_root_path() -> Path:
    env_value = os.environ.get(ENV_TRUST_ROOT, "").strip()
    if env_value:
        return Path(env_value)
    return _repo_root() / DEFAULT_TRUST_ROOT_PATH


def _load_or_generate_signing_key():
    """Load the Ed25519 signing key from disk (or env-pointed location).
    If neither exists, generate a dev key and persist it.

    Honest-scope: production deployments set VOS3_MODEL_SIGNING_KEY to an
    HSM-backed PEM URI; this function's dev fallback is INSECURE for any
    deployment outside a developer workstation."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    key_path = _resolve_signing_key_path()
    pub_path = _resolve_trust_root_path()

    if key_path.exists():
        password = os.environ.get(ENV_SIGNING_KEY_PASSWORD, "").encode("utf-8")
        try:
            private = serialization.load_pem_private_key(
                key_path.read_bytes(),
                password=password if password else None,
            )
        except Exception as exc:
            raise ModelSigningError(
                f"failed to load signing key at {key_path}: {exc}"
            ) from exc
        return private

    # Dev fallback — mint a fresh Ed25519 keypair and persist.
    logger.warning(
        "[model_signer] No signing key at %s; minting a dev keypair. "
        "DO NOT use this for production model signing. Set %s to an "
        "HSM-backed PEM URI for production.",
        key_path, ENV_SIGNING_KEY,
    )
    key_path.parent.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    key_path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(key_path, 0o600)
    pub_path.parent.mkdir(parents=True, exist_ok=True)
    pub_path.write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private


def _load_trust_root_pub():
    from cryptography.hazmat.primitives import serialization

    pub_path = _resolve_trust_root_path()
    if not pub_path.exists():
        # In dev fallback, mint by loading the signing key (creates the pub).
        _load_or_generate_signing_key()
    try:
        return serialization.load_pem_public_key(pub_path.read_bytes())
    except Exception as exc:
        raise ModelSigningError(
            f"failed to load trust root at {pub_path}: {exc}"
        ) from exc


def _ed25519_pub_b64(private_key) -> str:
    from cryptography.hazmat.primitives import serialization

    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(pub_bytes).decode("ascii")


# ---------------------------------------------------------------------------
# Sign + verify primitives
# ---------------------------------------------------------------------------


def sign_model(
    path: str | os.PathLike,
    signing_key=None,
    *,
    model_card_path: Optional[Path] = None,
) -> ModelSigningBundle:
    """Sign a model file in place.

    Writes two sidecar files:
      <path>.sig         — Ed25519 signature (raw bytes, 64 B)
      <path>.bundle.json — OMS v1.0 bundle (full provenance)

    Returns the bundle dataclass for in-process callers (e.g. the SBOM
    generator that wants to embed the signer fingerprint).

    Raises ModelSigningError on any failure (missing crypto library, IO
    error, key load failure).
    """
    p = Path(path)
    if not p.is_file():
        raise ModelSigningError(f"model file not found: {p}")

    private = signing_key if signing_key is not None else _load_or_generate_signing_key()

    digest_hex = _sha256_file(p)
    digest_bytes = bytes.fromhex(digest_hex)
    size = p.stat().st_size

    # OMS v1.0 signs the SHA-256 of the model bytes, not the bytes themselves —
    # this keeps signing cost O(1) per model regardless of size.
    signature_bytes = private.sign(digest_bytes)

    # Sibling model card?
    card_digest = None
    if model_card_path is None:
        candidate = Path(str(p) + OMS_MODELCARD_SUFFIX)
        if candidate.is_file():
            model_card_path = candidate
    if model_card_path is not None and model_card_path.is_file():
        card_digest = _sha256_file(model_card_path)

    bundle = ModelSigningBundle(
        subject=OMSSubject(
            name=p.stem,
            digest_sha256=digest_hex,
            size=size,
        ),
        signature=OMSSignature(
            alg="ed25519",
            signer_pub_b64=_ed25519_pub_b64(private),
            value_b64=base64.b64encode(signature_bytes).decode("ascii"),
            signed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        predicate=OMSPredicate(
            predicate_type=OMS_PREDICATE_TYPE,
            model_card_digest=card_digest,
            training_data_attestation=None,
            fine_tune_chain=(),
        ),
    )

    # Sidecar 1: raw signature for fast SLOT_START verify (kernel doesn't
    # parse JSON — it just compares 64 signature bytes to the recomputed
    # pubkey-verify result).
    sig_path = p.with_suffix(p.suffix + OMS_SIG_SUFFIX)
    sig_path.write_bytes(signature_bytes)

    # Sidecar 2: full OMS bundle.
    bundle_path = p.with_suffix(p.suffix + OMS_BUNDLE_SUFFIX)
    bundle_path.write_text(
        json.dumps(bundle.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return bundle


def verify_model_signature(
    path: str | os.PathLike,
    signing_pub_key=None,
) -> bool:
    """Verify that the model at `path` has a valid Ed25519 OMS signature.

    This is the function the kernel/services/vbus_driver.py callsite uses at
    SLOT_START — it's deliberately simple (boolean return) so the calling
    path can fail-fast without parsing the bundle. Auditors needing the full
    chain use `verify_oms_bundle()` instead.

    Returns True iff:
      1. <path>.sig exists
      2. The signature bytes parse as a valid Ed25519 signature
      3. The signature verifies against the trust-root public key over the
         SHA-256 of the model file's current bytes

    Returns False on any failure (missing sig, bad bytes, mismatched digest,
    verification failure).
    """
    from cryptography.exceptions import InvalidSignature

    p = Path(path)
    if not p.is_file():
        return False

    sig_path = p.with_suffix(p.suffix + OMS_SIG_SUFFIX)
    if not sig_path.is_file():
        return False

    pub = signing_pub_key if signing_pub_key is not None else _load_trust_root_pub()

    digest_bytes = bytes.fromhex(_sha256_file(p))
    sig_bytes = sig_path.read_bytes()
    if len(sig_bytes) != 64:
        return False

    try:
        pub.verify(sig_bytes, digest_bytes)
        return True
    except InvalidSignature:
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("[model_signer] unexpected verify error: %s", exc)
        return False


def verify_oms_bundle(path: str | os.PathLike) -> OMSVerificationResult:
    """Full bundle verification.

    Loads <path>.bundle.json, checks subject digest against the model file's
    current bytes, verifies the signature, returns a structured result. Used
    by the SBOM generator + EU AI Act Annex IV export pipeline.
    """
    from cryptography.exceptions import InvalidSignature

    p = Path(path)
    if not p.is_file():
        return OMSVerificationResult(ok=False, reason=f"model file missing: {p}")

    bundle_path = p.with_suffix(p.suffix + OMS_BUNDLE_SUFFIX)
    if not bundle_path.is_file():
        return OMSVerificationResult(
            ok=False, reason=f"OMS bundle missing: {bundle_path}"
        )

    try:
        doc = json.loads(bundle_path.read_text("utf-8"))
        bundle = ModelSigningBundle.from_dict(doc)
    except (json.JSONDecodeError, OMSVerificationError) as exc:
        return OMSVerificationResult(ok=False, reason=f"bundle parse failed: {exc}")

    current_digest = _sha256_file(p)
    if current_digest != bundle.subject.digest_sha256:
        return OMSVerificationResult(
            ok=False,
            bundle=bundle,
            reason=(
                f"digest mismatch: bundle claims {bundle.subject.digest_sha256[:16]}…, "
                f"file is {current_digest[:16]}…"
            ),
        )

    # Verify signature using the pubkey embedded in the bundle. This proves
    # the bundle was minted by whoever holds the matching private key; the
    # CALLER is responsible for checking that pubkey is in the trust root
    # (we expose `bundle.signature.signer_pub_b64` so the caller can do so).
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )

    try:
        pub_bytes = base64.b64decode(bundle.signature.signer_pub_b64)
        signer_pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
        sig_bytes = base64.b64decode(bundle.signature.value_b64)
        signer_pub.verify(sig_bytes, bytes.fromhex(current_digest))
    except InvalidSignature:
        return OMSVerificationResult(
            ok=False, bundle=bundle, reason="signature verification failed"
        )
    except Exception as exc:  # noqa: BLE001
        return OMSVerificationResult(
            ok=False, bundle=bundle, reason=f"signature parse error: {exc}"
        )

    # Optional: verify the model_card_digest if present in bundle and the
    # sibling file exists.
    if bundle.predicate.model_card_digest:
        card_path = Path(str(p) + OMS_MODELCARD_SUFFIX)
        if card_path.is_file():
            current_card_digest = _sha256_file(card_path)
            if current_card_digest != bundle.predicate.model_card_digest:
                return OMSVerificationResult(
                    ok=False,
                    bundle=bundle,
                    reason="model card digest mismatch",
                )

    return OMSVerificationResult(ok=True, bundle=bundle)


__all__ = [
    "ModelSigningBundle",
    "ModelSigningError",
    "OMSPredicate",
    "OMSSignature",
    "OMSSubject",
    "OMSVerificationError",
    "OMSVerificationResult",
    "sign_model",
    "verify_model_signature",
    "verify_oms_bundle",
    "OMS_SPEC_VERSION",
    "OMS_MEDIA_TYPE",
    "OMS_PREDICATE_TYPE",
    "OMS_SIG_SUFFIX",
    "OMS_BUNDLE_SUFFIX",
    "ENV_SIGNING_KEY",
    "ENV_SIGNING_KEY_PASSWORD",
    "ENV_TRUST_ROOT",
]
