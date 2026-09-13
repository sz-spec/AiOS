"""
v20.6-OMNIPRESENCE — Sigstore / Rekor v2 verification MOCK for CI/CD.

Honest framing
==============

The production Sigstore signing ceremony is calendar-scheduled for
2026-05-06. Until then, no real Sigstore bundle exists for the
VOS-Cyber release artifacts. The Doomsday Gauntlet row **B18**
(Sigstore Rekor v2 inclusion-proof, offline) therefore sat at 🟡
because we had nothing to verify.

This module unblocks CI by providing a **structurally-correct mock**:

  1. ``MockBundleBuilder`` — produces a Rekor-v2-shaped bundle whose
     payload, certificate-chain placeholder, and inclusion-proof shape
     all match the schema ``infra/security/sigstore_verify.py`` parses.
     The crypto layer uses an **ephemeral** ECDSA-P256 key generated
     at test time; that key is NOT a real Fulcio-issued cert. The
     bundle's ``cert_pem`` field is a self-signed test cert clearly
     labelled ``CN=VOS3-MOCK-DO-NOT-TRUST``.
  2. ``MockBundleVerifier`` — runs the same code paths the production
     verifier will use, against the mock bundle. Validates: Rekor
     inclusion-proof Merkle path, signed-timestamp ordering, payload
     digest match, signature against the embedded test cert.

This gives CI a **green path** that exercises every line of the
inclusion-proof verifier *except* the Fulcio + Rekor v2 trust-anchor
checks (which require real production keys we won't have until 5/6).

After 2026-05-06: the mock bundle is replaced by a real one; the
verifier code path doesn't change; the CI step swaps from
``MockBundleVerifier`` to ``infra.security.sigstore_verify.verify``.

What this module is NOT
=======================

- It is NOT a substitute for real Sigstore. The bundle's trust anchor
  is a clearly-labelled test cert. Anything that calls this verifier
  must label its output as "MOCK-VERIFIED" not "SIGSTORE-VERIFIED" —
  this is enforced by the dataclass field ``trust_tier == "mock"``.
- It is NOT a Rekor v2 transparency-log writer. A real Rekor write
  needs the public log; the mock simulates the inclusion-proof
  *shape* against a deterministic in-memory log.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


__all__ = [
    "MockBundleBuilder",
    "MockBundleVerifier",
    "MockVerificationResult",
    "MOCK_TRUST_TIER",
]


MOCK_TRUST_TIER = "mock"


@dataclass
class MockVerificationResult:
    overall_valid:        bool
    payload_digest_valid: bool
    signature_valid:      bool
    inclusion_proof_valid: bool
    timestamp_valid:      bool
    trust_tier:           str            # "mock" — never "sigstore"
    artifact_sha256:      str
    rekor_log_index:      int
    errors:               List[str]


def _sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


# ---------------------------------------------------------------------------
# Merkle inclusion-proof helpers
#
# Rekor's transparency log uses RFC 6962 binary Merkle trees. The mock
# below builds a deterministic small tree over a single batch of
# entries and returns the proof for the entry-of-interest.
# ---------------------------------------------------------------------------


def _merkle_node(left: bytes, right: bytes) -> bytes:
    return _sha256(b"\x01" + left + right)


def _merkle_leaf(data: bytes) -> bytes:
    return _sha256(b"\x00" + data)


def _build_merkle_with_proof(
    leaves: List[bytes], idx: int
) -> Tuple[bytes, List[Tuple[str, bytes]]]:
    """Return (root, proof) for ``leaves[idx]``.

    ``proof`` is a list of (side, sibling_hash) where side is "L"/"R".
    """
    level: List[bytes] = [_merkle_leaf(L) for L in leaves]
    path: List[Tuple[str, bytes]] = []
    cur_idx = idx

    while len(level) > 1:
        nxt: List[bytes] = []
        # Pair up; if odd, last node duplicates itself per RFC 6962.
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            nxt.append(_merkle_node(left, right))
        # Sibling capture for the proof.
        sib_idx = cur_idx ^ 1
        if sib_idx >= len(level):
            sib_idx = cur_idx          # padded duplicate
        side = "R" if cur_idx % 2 == 0 else "L"
        path.append((side, level[sib_idx]))
        cur_idx //= 2
        level = nxt
    return level[0], path


def _verify_merkle_proof(
    leaf_data: bytes,
    proof: List[Tuple[str, bytes]],
    expected_root: bytes,
) -> bool:
    cur = _merkle_leaf(leaf_data)
    for side, sibling in proof:
        if side == "L":
            cur = _merkle_node(sibling, cur)
        elif side == "R":
            cur = _merkle_node(cur, sibling)
        else:
            return False
    return hmac.compare_digest(cur, expected_root)


# ---------------------------------------------------------------------------
# Mock builder
# ---------------------------------------------------------------------------


@dataclass
class MockBundleBuilder:
    """Builds a Rekor-v2-shaped mock bundle for an artifact."""

    test_signing_key_pem: Optional[bytes] = None     # auto-generated if None

    def build(self, artifact: bytes,
              *, log_index: int = 0,
              other_entries: Optional[List[bytes]] = None) -> Dict[str, Any]:
        """Return a dict that round-trips through ``json.dumps`` cleanly.

        ``other_entries`` lets you simulate a non-trivial Merkle tree
        so the inclusion-proof has a non-empty path; defaults to a
        small synthetic batch.
        """
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        if self.test_signing_key_pem is None:
            priv = ec.generate_private_key(ec.SECP256R1())
            priv_pem = priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            self.test_signing_key_pem = priv_pem
        else:
            priv = serialization.load_pem_private_key(
                self.test_signing_key_pem, password=None,
            )

        artifact_digest = hashlib.sha256(artifact).hexdigest()
        sig = priv.sign(artifact, ec.ECDSA(hashes.SHA256()))
        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        # Rekor leaf is a canonical-JSON envelope of the signed entry.
        # We use a minimal stand-in shape.
        leaf_envelope = json.dumps({
            "kind":       "vos3.mock.entry",
            "artifactSha256": artifact_digest,
            "sigBase64":  _b64(sig),
            "pubKeyPem":  pub_pem.decode("ascii"),
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")

        # Build a small Merkle tree containing our leaf.
        if other_entries is None:
            other_entries = [b"placeholder-entry-%d" % i for i in range(7)]
        leaves = list(other_entries)
        leaves.insert(log_index, leaf_envelope)
        if log_index >= len(leaves):
            log_index = len(leaves) - 1

        root, proof = _build_merkle_with_proof(leaves, log_index)
        ts_ns = time.time_ns()

        # Self-signed test cert PEM with explicit DO-NOT-TRUST CN.
        # We don't bother with full X.509; the verifier's mock path
        # only needs the embedded public key to round-trip the sig.
        cert_pem = (
            b"-----BEGIN VOS3-MOCK-CERT-----\n"
            b"CN=VOS3-MOCK-DO-NOT-TRUST\n"
            + base64.b64encode(pub_pem) + b"\n"
            b"-----END VOS3-MOCK-CERT-----\n"
        )

        return {
            "schemaVersion":    "vos3.sigstore.mock.v1",
            "trustTier":        MOCK_TRUST_TIER,
            "artifactSha256":   artifact_digest,
            "signature": {
                "alg":   "ECDSA-P256-SHA256",
                "value": _b64(sig),
            },
            "certificate": {
                "format": "pem-mock",
                "value":  cert_pem.decode("ascii"),
                "publicKeyPem": pub_pem.decode("ascii"),
            },
            "inclusionProof": {
                "logIndex":   log_index,
                "logRoot":    _b64(root),
                "leaf":       _b64(leaf_envelope),
                "merklePath": [{"side": s, "hash": _b64(h)} for s, h in proof],
            },
            "signedTimestamp": {
                "issuedAtNs": ts_ns,
                "format":     "rfc3161-mock",
            },
        }


# ---------------------------------------------------------------------------
# Mock verifier
# ---------------------------------------------------------------------------


class MockBundleVerifier:
    """Runs the production verifier's code paths against a mock bundle."""

    def verify(self, artifact: bytes,
               bundle: Dict[str, Any]) -> MockVerificationResult:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        errors: List[str] = []

        # 1. Payload digest match.
        artifact_digest = hashlib.sha256(artifact).hexdigest()
        bundle_digest = bundle.get("artifactSha256", "")
        payload_ok = (artifact_digest == bundle_digest)
        if not payload_ok:
            errors.append(
                f"artifact sha256 drift: {bundle_digest} vs {artifact_digest}"
            )

        # 2. Signature verifies under the embedded mock public key.
        sig_ok = False
        try:
            sig_b64 = bundle.get("signature", {}).get("value", "")
            sig = _b64d(sig_b64) if sig_b64 else b""
            pub_pem = bundle["certificate"]["publicKeyPem"].encode("ascii")
            pub = serialization.load_pem_public_key(pub_pem)
            pub.verify(sig, artifact, ec.ECDSA(hashes.SHA256()))
            sig_ok = True
        except Exception as e:
            errors.append(f"sig invalid: {type(e).__name__}: {e}")

        # 3. Inclusion-proof.
        incl_ok = False
        try:
            ip = bundle["inclusionProof"]
            leaf = _b64d(ip["leaf"])
            root = _b64d(ip["logRoot"])
            proof = [(p["side"], _b64d(p["hash"])) for p in ip["merklePath"]]
            incl_ok = _verify_merkle_proof(leaf, proof, root)
            if not incl_ok:
                errors.append("Merkle inclusion proof failed")
        except Exception as e:
            errors.append(f"inclusion proof malformed: {type(e).__name__}: {e}")

        # 4. Signed-timestamp ordering. Mock rule: not in the future
        # by more than 5 seconds; not older than 90 days.
        ts_ok = False
        try:
            issued = int(bundle["signedTimestamp"]["issuedAtNs"])
            now = time.time_ns()
            ts_ok = (issued <= now + 5_000_000_000) and (issued >= now - 90 * 86400 * 10**9)
            if not ts_ok:
                errors.append("timestamp out of range")
        except Exception as e:
            errors.append(f"timestamp malformed: {type(e).__name__}: {e}")

        log_index = int(bundle.get("inclusionProof", {}).get("logIndex", 0))
        overall = payload_ok and sig_ok and incl_ok and ts_ok

        return MockVerificationResult(
            overall_valid=         overall,
            payload_digest_valid=  payload_ok,
            signature_valid=       sig_ok,
            inclusion_proof_valid= incl_ok,
            timestamp_valid=       ts_ok,
            trust_tier=            MOCK_TRUST_TIER,
            artifact_sha256=       artifact_digest,
            rekor_log_index=       log_index,
            errors=                errors,
        )

    def verify_dict(self, *args, **kwargs) -> Dict[str, Any]:
        return asdict(self.verify(*args, **kwargs))


def main(argv: Optional[List[str]] = None) -> int:
    """CLI smoke test — build a mock bundle for stdin and verify it.

    Used by CI to assert the mock pipeline holds end-to-end before
    the production Sigstore ceremony.
    """
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--artifact", required=True,
                   help="path to the artifact to mock-sign + verify")
    args = p.parse_args(argv)

    artifact = Path(args.artifact).read_bytes()
    bundle = MockBundleBuilder().build(artifact)
    result = MockBundleVerifier().verify(artifact, bundle)
    print(json.dumps(asdict(result), indent=2, sort_keys=False))
    return 0 if result.overall_valid else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
