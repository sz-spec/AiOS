"""
backend/services/hybrid_kex.py
================================

Sprint 14.1 / Gap 2 — userspace Hybrid X25519 + ML-KEM-768 key exchange.

What this is
------------

A Python-side implementation of the same hybrid KEX the kernel TLS 1.3
stack offers in supported_groups (TLS_GROUP_X25519MLKEM768 = 0x11EC).
Userspace needs the operation for:

  • Sigstore-v3 bundle signing where the artifact's symmetric key is
    transported alongside the signature (kept hybrid even though the
    signature itself is ECDSA).
  • IntegrityCertificate envelopes shipped to AI-SPM connectors.
  • Sovereign-tier P2P sync (services/p2p_sync.py) where peers exchange
    short-lived inference-session keys.

Wire format — IETF draft-ietf-tls-ecdhe-mlkem-04
------------------------------------------------

Client → server:
    encap_key:   1216 bytes = X25519_pub (32) || ML-KEM_pub (1184)

Server → client:
    cipher:      1120 bytes = X25519_pub (32) || ML-KEM_ct (1088)

Shared secret on both sides:
    ss = ML-KEM_ss (32) || X25519_ss (32)        = 64 bytes

The ordering — ML-KEM first, classical second — matches the IETF draft.
We expose that 64-byte concatenation as the OUTPUT; callers feed it into
their own KDF (HKDF for TLS, BLAKE3 / SHA-256 for non-TLS use).

Backend dispatch — same shape as services.pqc_sign
--------------------------------------------------

ML-KEM-768 has no pure-Python pinning we can ship with constant-time
guarantees today. Userland inherits the kernel's honest-scope ceiling:

  oqs_avx2 / oqs_scalar   — liboqs via ``pip install oqs``; constant-time
  unavailable             — no PQ library on the host; hybrid KEX raises
                            HybridKexUnavailable

When the chosen backend is ``unavailable``, callers MUST decide between
(a) refusing the connection (sovereign profile), (b) classical X25519 only
(default profile w/ explicit operator opt-out).

X25519 is **always** available via ``cryptography`` (already a transitive
dep across the project — see services/crypto_keyring.py and
services/app_crypto.py).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger("vos.hybrid_kex")


# ---------------------------------------------------------------------------
# Sizes — match kernel/include/vos/mlkem768.h + tls.h
# ---------------------------------------------------------------------------

X25519_PUB_BYTES = 32
X25519_PRIV_BYTES = 32
X25519_SS_BYTES = 32

MLKEM768_PK_BYTES = 1184
MLKEM768_SK_BYTES = 2400
MLKEM768_CT_BYTES = 1088
MLKEM768_SS_BYTES = 32

HYBRID_ENCAP_KEY_BYTES = X25519_PUB_BYTES + MLKEM768_PK_BYTES  # 1216
HYBRID_CIPHER_BYTES = X25519_PUB_BYTES + MLKEM768_CT_BYTES  # 1120
HYBRID_SS_BYTES = MLKEM768_SS_BYTES + X25519_SS_BYTES  # 64


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HybridKexUnavailable(RuntimeError):
    """ML-KEM-768 backend is not installed on this host."""


class HybridKexDecodeError(ValueError):
    """Wire-format mismatch (wrong length, malformed key share, etc.)."""


# ---------------------------------------------------------------------------
# Backend probe — cached after first call
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BackendInfo:
    name: str  # "oqs_avx2" | "oqs_scalar" | "unavailable"
    available: bool


_backend_cache: Optional[_BackendInfo] = None
_backend_lock = threading.Lock()


def _probe_backend() -> _BackendInfo:
    global _backend_cache
    with _backend_lock:
        if _backend_cache is not None:
            return _backend_cache
        try:
            import oqs  # type: ignore[import-not-found]
        except ImportError:
            _backend_cache = _BackendInfo(name="unavailable", available=False)
            return _backend_cache
        try:
            kems = set(oqs.get_enabled_kem_mechanisms())
        except Exception:  # noqa: BLE001
            kems = set()
        if "ML-KEM-768" not in kems:
            _backend_cache = _BackendInfo(name="unavailable", available=False)
            return _backend_cache
        # liboqs doesn't tell us via Python whether it was AVX2-built;
        # honest: report scalar unless an env hint says otherwise.
        is_avx2 = os.environ.get("VOS3_OQS_BUILD", "").lower() == "avx2"
        _backend_cache = _BackendInfo(
            name="oqs_avx2" if is_avx2 else "oqs_scalar",
            available=True,
        )
        return _backend_cache


def backend_name() -> str:
    return _probe_backend().name


def is_available() -> bool:
    return _probe_backend().available


def _reset_probe_for_tests() -> None:
    global _backend_cache
    with _backend_lock:
        _backend_cache = None


# ---------------------------------------------------------------------------
# X25519 (always available via cryptography)
# ---------------------------------------------------------------------------


def _x25519_keygen() -> Tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PrivateFormat,
        NoEncryption,
        PublicFormat,
    )

    priv = X25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )
    pub_bytes = priv.public_key().public_bytes(
        encoding=Encoding.Raw, format=PublicFormat.Raw
    )
    return priv_bytes, pub_bytes


def _x25519_shared(priv_bytes: bytes, peer_pub_bytes: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )

    priv = X25519PrivateKey.from_private_bytes(priv_bytes)
    peer = X25519PublicKey.from_public_bytes(peer_pub_bytes)
    return priv.exchange(peer)


# ---------------------------------------------------------------------------
# ML-KEM-768 via liboqs (oqs-python)
# ---------------------------------------------------------------------------


def _mlkem_keygen() -> Tuple[bytes, bytes]:
    """Returns (public_key, secret_key) for ML-KEM-768."""
    info = _probe_backend()
    if not info.available:
        raise HybridKexUnavailable(
            "oqs-python with ML-KEM-768 enabled is required for hybrid KEX. "
            "Install with: pip install oqs"
        )
    import oqs  # type: ignore[import-not-found]

    with oqs.KeyEncapsulation("ML-KEM-768") as kem:
        pk = kem.generate_keypair()
        sk = kem.export_secret_key()
    if len(pk) != MLKEM768_PK_BYTES:
        raise HybridKexDecodeError(
            f"unexpected ML-KEM-768 pk size: got {len(pk)} expected {MLKEM768_PK_BYTES}"
        )
    if len(sk) != MLKEM768_SK_BYTES:
        raise HybridKexDecodeError(
            f"unexpected ML-KEM-768 sk size: got {len(sk)} expected {MLKEM768_SK_BYTES}"
        )
    return pk, sk


def _mlkem_encaps(peer_pk: bytes) -> Tuple[bytes, bytes]:
    """Returns (ciphertext, shared_secret)."""
    info = _probe_backend()
    if not info.available:
        raise HybridKexUnavailable("oqs ML-KEM-768 not available")
    if len(peer_pk) != MLKEM768_PK_BYTES:
        raise HybridKexDecodeError(
            f"peer_pk size {len(peer_pk)} != expected {MLKEM768_PK_BYTES}"
        )
    import oqs  # type: ignore[import-not-found]

    with oqs.KeyEncapsulation("ML-KEM-768") as kem:
        ct, ss = kem.encap_secret(peer_pk)
    return ct, ss


def _mlkem_decaps(our_sk: bytes, ct: bytes) -> bytes:
    info = _probe_backend()
    if not info.available:
        raise HybridKexUnavailable("oqs ML-KEM-768 not available")
    if len(ct) != MLKEM768_CT_BYTES:
        raise HybridKexDecodeError(f"ct size {len(ct)} != expected {MLKEM768_CT_BYTES}")
    import oqs  # type: ignore[import-not-found]

    with oqs.KeyEncapsulation("ML-KEM-768", secret_key=our_sk) as kem:
        ss = kem.decap_secret(ct)
    return ss


# ---------------------------------------------------------------------------
# Public hybrid surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HybridKeypair:
    """Caller-side (initiator) keypair — both halves needed for decaps."""

    x25519_priv: bytes
    x25519_pub: bytes
    mlkem_pk: bytes
    mlkem_sk: bytes

    def encap_key(self) -> bytes:
        """1216-byte wire-format public material to send to peer."""
        return self.x25519_pub + self.mlkem_pk


@dataclass(frozen=True)
class HybridCipher:
    """Peer (responder) → initiator response material."""

    x25519_peer_pub: bytes
    mlkem_ct: bytes

    @classmethod
    def from_wire(cls, blob: bytes) -> "HybridCipher":
        if len(blob) != HYBRID_CIPHER_BYTES:
            raise HybridKexDecodeError(
                f"hybrid cipher size {len(blob)} != {HYBRID_CIPHER_BYTES}"
            )
        return cls(
            x25519_peer_pub=blob[:X25519_PUB_BYTES],
            mlkem_ct=blob[X25519_PUB_BYTES:],
        )

    def to_wire(self) -> bytes:
        return self.x25519_peer_pub + self.mlkem_ct


def hybrid_keygen() -> HybridKeypair:
    """Initiator generates X25519 + ML-KEM keypair. Raises if PQ unavailable."""
    x_priv, x_pub = _x25519_keygen()
    pk, sk = _mlkem_keygen()
    return HybridKeypair(x25519_priv=x_priv, x25519_pub=x_pub, mlkem_pk=pk, mlkem_sk=sk)


def hybrid_encap(peer_encap_key: bytes) -> Tuple[bytes, HybridCipher]:
    """Responder side: derive shared secret AND produce cipher for initiator.

    Returns (shared_secret_64_bytes, cipher_to_send_back).
    """
    if len(peer_encap_key) != HYBRID_ENCAP_KEY_BYTES:
        raise HybridKexDecodeError(
            f"encap_key size {len(peer_encap_key)} != {HYBRID_ENCAP_KEY_BYTES}"
        )
    peer_x_pub = peer_encap_key[:X25519_PUB_BYTES]
    peer_mlkem_pk = peer_encap_key[X25519_PUB_BYTES:]

    # X25519 leg — responder generates its own ephemeral
    our_x_priv, our_x_pub = _x25519_keygen()
    x_ss = _x25519_shared(our_x_priv, peer_x_pub)

    # ML-KEM leg — encapsulate to the initiator's public key
    mlkem_ct, mlkem_ss = _mlkem_encaps(peer_mlkem_pk)

    shared = mlkem_ss + x_ss
    if len(shared) != HYBRID_SS_BYTES:
        raise HybridKexDecodeError(
            f"shared secret size {len(shared)} != {HYBRID_SS_BYTES}"
        )

    cipher = HybridCipher(x25519_peer_pub=our_x_pub, mlkem_ct=mlkem_ct)
    return shared, cipher


def hybrid_decap(our_keys: HybridKeypair, cipher: HybridCipher) -> bytes:
    """Initiator side: combine our keypair + peer's cipher into 64-byte secret."""
    x_ss = _x25519_shared(our_keys.x25519_priv, cipher.x25519_peer_pub)
    mlkem_ss = _mlkem_decaps(our_keys.mlkem_sk, cipher.mlkem_ct)
    shared = mlkem_ss + x_ss
    if len(shared) != HYBRID_SS_BYTES:
        raise HybridKexDecodeError(
            f"shared secret size {len(shared)} != {HYBRID_SS_BYTES}"
        )
    return shared


# ---------------------------------------------------------------------------
# Convenience for symmetric tests / classical fallback
# ---------------------------------------------------------------------------


def x25519_only_kex() -> Tuple[bytes, bytes, bytes]:
    """Classical-only round-trip — useful when callers detect
    ``is_available() == False`` and choose to proceed without PQ.

    Returns (alice_priv, alice_pub, shared_secret_with_self).
    """
    a_priv, a_pub = _x25519_keygen()
    b_priv, b_pub = _x25519_keygen()
    ss_a = _x25519_shared(a_priv, b_pub)
    ss_b = _x25519_shared(b_priv, a_pub)
    assert ss_a == ss_b, "X25519 round-trip mismatch — toolchain bug"
    return a_priv, a_pub, ss_a


def status() -> dict:
    info = _probe_backend()
    return {
        "backend": info.name,
        "available": info.available,
        "sizes": {
            "x25519_pub": X25519_PUB_BYTES,
            "mlkem_pk": MLKEM768_PK_BYTES,
            "mlkem_sk": MLKEM768_SK_BYTES,
            "mlkem_ct": MLKEM768_CT_BYTES,
            "hybrid_encap_key": HYBRID_ENCAP_KEY_BYTES,
            "hybrid_cipher": HYBRID_CIPHER_BYTES,
            "hybrid_shared_secret": HYBRID_SS_BYTES,
        },
        "ietf_codepoint": 0x11EC,
        "spec": "draft-ietf-tls-ecdhe-mlkem-04",
    }


__all__ = [
    "HybridKeypair",
    "HybridCipher",
    "HybridKexUnavailable",
    "HybridKexDecodeError",
    "hybrid_keygen",
    "hybrid_encap",
    "hybrid_decap",
    "x25519_only_kex",
    "is_available",
    "backend_name",
    "status",
    "X25519_PUB_BYTES",
    "MLKEM768_PK_BYTES",
    "MLKEM768_SK_BYTES",
    "MLKEM768_CT_BYTES",
    "HYBRID_ENCAP_KEY_BYTES",
    "HYBRID_CIPHER_BYTES",
    "HYBRID_SS_BYTES",
]
