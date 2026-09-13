"""
infra/security/sigstore_v3_bundle.py — Stage 11

v3-shaped Sigstore bundle producer + verifier.

Honest scope (read this before merging into a release pipeline)
================================================================

The April-2026 Sigstore v3 wire format is **not** in my training data
(cutoff January 2026). I cannot produce a bundle that is byte-identical
to upstream v3. This module ships a **v3-shaped** bundle whose:

  - Cryptographic primitives are correct and verifiable: ECDSA P-256
    signature over SHA-256 of the artefact, computed with the
    standard ``cryptography`` library.
  - Envelope structure follows the published DSSE
    (Dead-Simple Signing Envelope) spec — which is the same envelope
    Sigstore v2 already uses and the v3 evolution preserves.
  - Rekor v2 inclusion-proof is a real RFC-6962 Merkle path produced
    by ``rekor_v2_log.py``.
  - Trust-anchor chain is a self-signed dev-tier cert (clearly labelled
    ``CN=VOS3-DEV-NOT-FULCIO``), NOT a real Fulcio short-lived cert.

When the actual v3 spec lands the swap points are:

  (1) The top-level JSON key names: this module uses the v2-era keys
      ("dsseEnvelope", "verificationMaterial", "tlogEntries"); the v3
      spec may rename or restructure these. Single-layer change in
      ``Bundle.to_v3_dict()``.
  (2) The ``mediaType`` constant — v3 carries a new media-type;
      single-line change in ``BUNDLE_MEDIA_TYPE``.
  (3) The Rekor v2 endpoint in CI's ``cosign verify-blob`` invocation;
      see docs/SIGSTORE_V3_GAP.md for the operational checklist.

Everything that is cryptographically meaningful — the signature math,
the inclusion-proof verification, the SHA-256 binding to the artefact
bytes — is independent of the wire-format swap and does not change.

Usage
=====

    from infra.security.sigstore_v3_bundle import Signer, Verifier
    from infra.security.rekor_v2_log import RekorV2Log

    log = RekorV2Log(path="infra/security/rekor_v2.jsonl")
    signer = Signer.from_dev_key("infra/security/keys/vos3_dev_signing.key")

    bundle = signer.sign_artifact("kernel/build/vos3.elf", rekor=log)
    bundle.write("kernel/build/vos3.elf.bundle.json")

    # Later, on a fresh machine:
    Verifier.from_dev_key("infra/security/keys/vos3_dev_signing.pub") \\
        .verify_artifact("kernel/build/vos3.elf",
                         "kernel/build/vos3.elf.bundle.json")
    # → returns dict with verified=True + claimed digest +
    #   computed digest + rekor_entry, or raises VerifyError.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.exceptions import InvalidSignature

from .rekor_v2_log import RekorV2Log, verify_stored_entry

BUNDLE_MEDIA_TYPE = "application/vnd.dev.sigstore.bundle.v3+json"
DSSE_PAYLOAD_TYPE = "application/vnd.vos3.artifact.v1+sha256"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SignError(RuntimeError):
    pass


class VerifyError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Bundle dataclass + serialisation
# ---------------------------------------------------------------------------


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE Pre-Authentication Encoding (PAE) per the DSSE spec.

    Format: "DSSEv1 <len_pt> <pt> <len_p> <p>" (single ASCII space
    between fields, lengths in decimal). Ensures the signed bytes are
    unambiguous about field boundaries.
    """
    pt_b = payload_type.encode("utf-8")
    return (
        b"DSSEv1 "
        + str(len(pt_b)).encode("ascii")
        + b" "
        + pt_b
        + b" "
        + str(len(payload)).encode("ascii")
        + b" "
        + payload
    )


@dataclass
class Bundle:
    artifact_path: str
    artifact_sha256_hex: str
    payload_b64: str         # base64 of DSSE payload (the JSON statement)
    payload_type: str
    signature_b64: str       # base64 of DER-encoded ECDSA signature
    pubkey_pem: str          # PEM-encoded public key
    cert_pem: str            # self-signed dev cert (NOT a Fulcio cert)
    rekor_entry: dict        # full Rekor LogEntry (incl. inclusion proof)

    def to_v3_dict(self) -> dict[str, Any]:
        # Swap-point (1): the v3 spec MAY rename these keys. Until we have
        # the spec, we use the v2 names — every existing Sigstore SDK can
        # read this shape. Production code will pivot to v3 keys here.
        return {
            "mediaType": BUNDLE_MEDIA_TYPE,
            "artifact": {
                "path": self.artifact_path,
                "sha256_hex": self.artifact_sha256_hex,
            },
            "dsseEnvelope": {
                "payloadType": self.payload_type,
                "payload": self.payload_b64,
                "signatures": [
                    {"sig": self.signature_b64, "keyid": ""},
                ],
            },
            "verificationMaterial": {
                "publicKey": {"pem": self.pubkey_pem},
                "x509CertificateChain": {
                    "certificates": [{"pem": self.cert_pem}],
                },
            },
            "tlogEntries": [self.rekor_entry],
            "vos3_meta": {
                "stage": "11",
                "tier": "dev",
                "spec_v3_byte_compatibility": "pending — see docs/SIGSTORE_V3_GAP.md",
            },
        }

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.write_text(json.dumps(self.to_v3_dict(), indent=2), encoding="utf-8")
        return p

    @staticmethod
    def read(path: str | Path) -> "Bundle":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        env = d["dsseEnvelope"]
        return Bundle(
            artifact_path=d["artifact"]["path"],
            artifact_sha256_hex=d["artifact"]["sha256_hex"],
            payload_b64=env["payload"],
            payload_type=env["payloadType"],
            signature_b64=env["signatures"][0]["sig"],
            pubkey_pem=d["verificationMaterial"]["publicKey"]["pem"],
            cert_pem=d["verificationMaterial"]["x509CertificateChain"][
                "certificates"
            ][0]["pem"],
            rekor_entry=d["tlogEntries"][0],
        )


# ---------------------------------------------------------------------------
# Signer
# ---------------------------------------------------------------------------


@dataclass
class Signer:
    private_key: ec.EllipticCurvePrivateKey
    cert_pem: str

    @staticmethod
    def from_dev_key(path: str | Path) -> "Signer":
        """Load the dev-tier ECDSA P-256 private key from disk.

        The matching public key is at <path>.pub-equivalent (the
        VOS3-Cyber convention is sibling .pub file). The dev cert is
        self-signed and labelled CN=VOS3-DEV-NOT-FULCIO.
        """
        pem = Path(path).read_bytes()
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise SignError(
                f"key at {path} is not ECDSA — refusing to sign with non-EC key"
            )
        if key.curve.name != "secp256r1":
            raise SignError(
                f"key curve {key.curve.name} != secp256r1; v3 bundle requires P-256"
            )
        cert_pem = _build_dev_cert_pem(key)
        return Signer(private_key=key, cert_pem=cert_pem)

    def sign_artifact(
        self,
        artifact_path: str | Path,
        *,
        rekor: Optional[RekorV2Log] = None,
    ) -> Bundle:
        """Sign an artefact and append the bundle to the Rekor v2 log."""
        artifact_bytes = Path(artifact_path).read_bytes()
        artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()

        # DSSE payload — a JSON statement binding the artefact path, its
        # SHA-256, and the signing timestamp. This is what gets PAE-wrapped
        # and signed; verifiers re-derive PAE before signature check.
        statement = {
            "_type": "https://in-toto.io/Statement/v0.1",
            "predicateType": "https://vos3.security/sigstore-v3-shaped/v1",
            "subject": [
                {
                    "name": str(artifact_path),
                    "digest": {"sha256": artifact_sha256},
                }
            ],
            "predicate": {
                "signedAt": datetime.now(timezone.utc).isoformat(),
                "tier": "dev",
                "tool": "infra/security/sigstore_v3_bundle.py",
            },
        }
        payload_bytes = json.dumps(statement, sort_keys=True).encode("utf-8")
        pae = _pae(DSSE_PAYLOAD_TYPE, payload_bytes)

        # ECDSA P-256 + SHA-256 — standard Sigstore alg. DER-encoded sig.
        sig = self.private_key.sign(pae, ec.ECDSA(hashes.SHA256()))

        pub_pem = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

        bundle = Bundle(
            artifact_path=str(artifact_path),
            artifact_sha256_hex=artifact_sha256,
            payload_b64=_b64(payload_bytes),
            payload_type=DSSE_PAYLOAD_TYPE,
            signature_b64=_b64(sig),
            pubkey_pem=pub_pem,
            cert_pem=self.cert_pem,
            rekor_entry={},  # placeholder; populated below
        )

        if rekor is not None:
            # Append the canonical bundle bytes (without the rekor field
            # itself, to avoid circular dependency) into Rekor.
            stub_bytes = json.dumps(
                {
                    "artifact_sha256": artifact_sha256,
                    "payload_b64": bundle.payload_b64,
                    "signature_b64": bundle.signature_b64,
                    "pubkey_pem": bundle.pubkey_pem,
                },
                sort_keys=True,
            ).encode("utf-8")
            entry = rekor.append(kind="vos3.sigstore.v3+dsse", payload=stub_bytes)
            bundle.rekor_entry = entry.to_dict()
        else:
            bundle.rekor_entry = {
                "kind": "vos3.sigstore.v3+dsse",
                "skipped": True,
                "reason": "no rekor log provided to signer",
            }

        return bundle


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


@dataclass
class Verifier:
    public_key: ec.EllipticCurvePublicKey

    @staticmethod
    def from_dev_key(pub_pem_path: str | Path) -> "Verifier":
        pem = Path(pub_pem_path).read_bytes()
        key = serialization.load_pem_public_key(pem)
        if not isinstance(key, ec.EllipticCurvePublicKey):
            raise VerifyError(
                f"public key at {pub_pem_path} is not ECDSA"
            )
        return Verifier(public_key=key)

    def verify_artifact(
        self,
        artifact_path: str | Path,
        bundle_path: str | Path,
        *,
        expected_sha256_hex: Optional[str] = None,
    ) -> dict:
        """Three independent checks, fail-closed on any failure.

        (a) Recompute SHA-256 of the artefact, compare with bundle.
        (b) Verify the ECDSA signature over the DSSE PAE bytes.
        (c) Verify the Rekor inclusion proof against the entry's
            claimed root (if a rekor entry is present and not skipped).

        ``expected_sha256_hex`` (optional): if supplied, also check the
        claimed digest matches a caller-provided expectation — used by
        the Stage-11 release pipeline to assert the bundle binds the
        DETERMINISTIC kernel SHA, not just whatever the bundle says.
        """
        bundle = Bundle.read(bundle_path)

        # (a) re-hash the artefact
        artifact_bytes = Path(artifact_path).read_bytes()
        actual_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
        if actual_sha256 != bundle.artifact_sha256_hex:
            raise VerifyError(
                f"artefact SHA-256 mismatch: file={actual_sha256} "
                f"bundle_says={bundle.artifact_sha256_hex}"
            )
        if expected_sha256_hex is not None and actual_sha256 != expected_sha256_hex:
            raise VerifyError(
                f"artefact SHA-256 {actual_sha256} != caller-expected "
                f"{expected_sha256_hex}"
            )

        # (b) verify ECDSA over PAE
        payload = _b64d(bundle.payload_b64)
        sig = _b64d(bundle.signature_b64)
        pae = _pae(bundle.payload_type, payload)
        try:
            self.public_key.verify(sig, pae, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise VerifyError(f"DSSE signature failed: {exc}") from exc

        # (b.5) cross-check the embedded statement actually mentions our
        # artefact's SHA — defends against a swapped-payload attack.
        statement = json.loads(payload.decode("utf-8"))
        subj = statement["subject"][0]
        if subj["digest"]["sha256"] != actual_sha256:
            raise VerifyError(
                "DSSE payload subject SHA does not match artefact SHA"
            )

        # (c) rekor inclusion proof (optional but verified if present)
        rekor_check = "skipped"
        if bundle.rekor_entry and not bundle.rekor_entry.get("skipped"):
            stub_bytes = json.dumps(
                {
                    "artifact_sha256": bundle.artifact_sha256_hex,
                    "payload_b64": bundle.payload_b64,
                    "signature_b64": bundle.signature_b64,
                    "pubkey_pem": bundle.pubkey_pem,
                },
                sort_keys=True,
            ).encode("utf-8")
            if not verify_stored_entry(stub_bytes, bundle.rekor_entry):
                raise VerifyError(
                    "rekor inclusion proof failed for bundle stub"
                )
            rekor_check = "passed"

        return {
            "verified": True,
            "artifact_path": str(artifact_path),
            "computed_sha256_hex": actual_sha256,
            "bundle_sha256_hex": bundle.artifact_sha256_hex,
            "rekor_inclusion": rekor_check,
            "tier": "dev",
        }


# ---------------------------------------------------------------------------
# Internal helpers — dev-tier self-signed cert
# ---------------------------------------------------------------------------


def _build_dev_cert_pem(key: ec.EllipticCurvePrivateKey) -> str:
    """Build a self-signed dev cert clearly labelled NOT-FULCIO.

    The cert is for human readability of the bundle; cryptographic
    trust comes from the public-key match, not from a trust anchor
    chain. Production replaces this with a real Fulcio-issued
    short-lived cert.
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "VOS3-DEV-NOT-FULCIO"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "vOS-Cyber dev tier"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime(2099, 1, 1, tzinfo=timezone.utc))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
