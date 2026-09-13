"""
backend/services/bbs_selective_disclosure.py
============================================

Sprint 20 / Primitive (a) — BBS+ selective-disclosure proofs for
PII-safe cross-domain token exchange.

What this closes
----------------

Two standing scope ceilings, both DEPTH deepenings of already-counted
moat rows (NOT new rows — see docs/V1.3_BACKLOG_MASTER_PLAN.md §4):

  - Cluster B.3 — "BBS+ selective disclosure" ceiling on the identity
    federation bridge (PII-safe partial-envelope disclosure across
    trust domains).
  - Cluster C-3 BBS+ — the cryptographic upgrade path for O3's
    in-process ``ReleaseContext``. Today O3 (outbound_pii_shield.py)
    permits a PII send under a Python ``ReleaseContext(reviewed=True)``
    — a boolean a bug could forge. This module replaces it with a
    cryptographic, auditor-verifiable release: an outbound PII send is
    permitted iff the payload's PII kinds ⊆ the kinds proven by a
    VALID, UNEXPIRED derived proof bound to *this* destination's trust
    domain.

The W3C three-operation model (vc-di-bbs)
-----------------------------------------

    Issuer (vOS attestation service, Trust Domain A):
        base = BbsIssuer(sk).sign(statements, holder_pub)
        # mandatory statements: schema/@type, tenant trust-domain, epoch
        # selective statements:  one canonicalized statement per PII kind

    Holder (the agent / federation bridge):
        derived = BbsHolder(holder_sk).prove(
            base, reveal=<indices of cleared kinds>,
            verifier_id=<destination trust domain>, ttl_seconds=...)
        # 'pseudonym' is verifier-scoped + unlinkable across verifiers

    Verifier (vOS instance, Trust Domain B):
        result = BbsVerifier().verify(derived, issuer_pub)
        # learns ONLY the revealed statements; over-broad / forged /
        # expired / wrong-holder proofs are rejected

Backends — ``VOS3_BBS_BACKEND``
-------------------------------

  - ``stub`` (default) — a REAL-crypto stand-in built from a per-
    statement Merkle commitment tree (SHA-256) + Ed25519 issuer and
    holder signatures + HMAC-SHA256 verifier-scoped pseudonyms. It
    enforces every property the enforcement contract depends on
    (selective reveal, over-broad rejection, holder binding, expiry,
    per-verifier pseudonym unlinkability) using primitives already in
    the tree (``cryptography``), with **zero** new dependencies.
  - ``blst`` — the production BLS12-381 BBS+ path. NOT shipped here:
    requires a vetted constant-time BBS library (operator decision §10
    of the V1.3 plan). Selecting it raises ``BbsBackendUnavailable``
    so a misconfiguration fails loud rather than silently downgrading.

Honest scope ceiling (read before trusting this for production)
---------------------------------------------------------------

  The ``stub`` backend is cryptographically sound for the properties
  it claims, but it is NOT real BBS+: the Merkle root is a per-credential
  CONSTANT, so two derived proofs minted from the SAME base credential
  are correlatable by their shared root. Real BBS+ ProofGen re-randomizes
  the signature so even same-credential proofs are unlinkable. The
  per-verifier PSEUDONYM here is unlinkable (HMAC-scoped), but the
  credential root is not. This is acceptable for a fail-closed egress
  gate (the alternative is a forgeable boolean) but it is NOT a
  privacy-complete BBS+ deployment. Flip ``VOS3_BBS_BACKEND=blst`` once
  a vetted lib lands.

References
----------
  - W3C Data Integrity BBS Cryptosuites v1.0 — https://www.w3.org/TR/vc-di-bbs/
  - Selective disclosure of VCs — arXiv 2401.08196
  - MATTR, BBS signatures: privacy-by-design
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

ENV_BACKEND = "VOS3_BBS_BACKEND"

# A derived proof older than this (when no explicit ttl is given) is
# rejected. Mirrors the C-3 DeclassEvidence 5-minute window.
DEFAULT_TTL_SECONDS = 300


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BbsError(Exception):
    """Base class for all BBS selective-disclosure failures."""


class BbsBackendUnavailable(BbsError):
    """Raised when the configured backend cannot be used (e.g. blst not
    installed). Fail-loud — never silently downgrade a privacy backend."""


class BbsVerifyError(BbsError):
    """Raised by the verifier when a derived proof does not verify
    (bad issuer sig, broken Merkle path, forged holder binding, expired,
    over-broad reveal). Fail-closed."""


# ---------------------------------------------------------------------------
# Clock — lets tests pin time (same pattern as identity_federation_bridge)
# ---------------------------------------------------------------------------


class ClockProtocol(Protocol):
    def time(self) -> float: ...


class SystemClock:
    def time(self) -> float:
        return time.time()


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


class BbsBackend(str, enum.Enum):
    STUB = "stub"
    BLST = "blst"


def _selected_backend() -> BbsBackend:
    raw = os.environ.get(ENV_BACKEND, "").strip().lower()
    if raw == BbsBackend.BLST.value:
        return BbsBackend.BLST
    return BbsBackend.STUB


def _require_stub_backend() -> None:
    if _selected_backend() is BbsBackend.BLST:
        raise BbsBackendUnavailable(
            "VOS3_BBS_BACKEND=blst selected but the production BLS12-381 "
            "BBS+ backend is not bundled (operator decision §10 of the "
            "V1.3 plan). Install a vetted constant-time BBS library and "
            "wire it here, or unset VOS3_BBS_BACKEND to use the stub."
        )


# ---------------------------------------------------------------------------
# Crypto helpers (stub backend — real primitives from `cryptography`)
# ---------------------------------------------------------------------------


def _sha256(*chunks: bytes) -> bytes:
    """Length-prefixed SHA-256 over the chunks → unambiguous framing."""
    h = hashlib.sha256()
    for c in chunks:
        h.update(len(c).to_bytes(8, "big"))
        h.update(c)
    return h.digest()


def _leaf(index: int, statement: str, nonce: bytes) -> bytes:
    return _sha256(
        b"vos3-bbs-leaf", index.to_bytes(4, "big"), statement.encode("utf-8"), nonce
    )


def _merkle_root(leaves: Sequence[bytes]) -> bytes:
    """Binary Merkle root with domain-separated node hashing. Odd nodes
    are promoted (duplicated) — RFC-6962-style padding."""
    if not leaves:
        return _sha256(b"vos3-bbs-empty")
    level = list(leaves)
    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            nxt.append(_sha256(b"vos3-bbs-node", left, right))
        level = nxt
    return level[0]


def _merkle_path(leaves: Sequence[bytes], index: int) -> tuple[tuple[str, bytes], ...]:
    """Inclusion path for `index`: (side, sibling_hash) from leaf to root.
    side ∈ {"L","R"} says which side the sibling sits on."""
    path: list[tuple[str, bytes]] = []
    level = list(leaves)
    idx = index
    while len(level) > 1:
        sib_index = idx ^ 1
        if sib_index >= len(level):
            sib_index = idx  # promoted odd node — sibling is self
        side = "R" if (idx % 2 == 0) else "L"
        path.append((side, level[sib_index]))
        nxt: list[bytes] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else level[i]
            nxt.append(_sha256(b"vos3-bbs-node", left, right))
        idx //= 2
        level = nxt
    return tuple(path)


def _merkle_verify(leaf: bytes, path: Iterable[tuple[str, bytes]], root: bytes) -> bool:
    cur = leaf
    for side, sib in path:
        if side == "R":
            cur = _sha256(b"vos3-bbs-node", cur, sib)
        else:
            cur = _sha256(b"vos3-bbs-node", sib, cur)
    return hmac.compare_digest(cur, root)


def _ed25519_sign(private_bytes: bytes, message: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    sk = Ed25519PrivateKey.from_private_bytes(private_bytes)
    return sk.sign(message)


def _ed25519_verify(public_bytes: bytes, signature: bytes, message: bytes) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )
    from cryptography.exceptions import InvalidSignature

    try:
        pk = Ed25519PublicKey.from_public_bytes(public_bytes)
        pk.verify(signature, message)
        return True
    except InvalidSignature:
        return False
    except Exception as exc:  # noqa: BLE001
        raise BbsVerifyError(f"ed25519 verify error: {exc}") from exc


def generate_keypair() -> tuple[bytes, bytes]:
    """Return (private_bytes, public_bytes) for a fresh Ed25519 key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives import serialization

    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, pub


def holder_commitment(holder_public_bytes: bytes) -> bytes:
    """The value an issuer binds a base proof to. The holder proves
    ownership by signing the derived proof with the matching secret."""
    return _sha256(b"vos3-bbs-holder", holder_public_bytes)


def _issuer_root_payload(
    issuer_id: str, merkle_root: bytes, commitment: bytes
) -> bytes:
    """The exact bytes the issuer signs. Count-independent so the verifier
    — who never learns how many statements the credential held — can
    re-derive and check it from only the root + holder commitment."""
    return _sha256(
        b"vos3-bbs-issuer-root",
        issuer_id.encode("utf-8"),
        merkle_root,
        commitment,
    )


# ---------------------------------------------------------------------------
# Statement model — PII-kind helpers
# ---------------------------------------------------------------------------

MANDATORY_PREFIX = "mandatory:"
PII_CLEARED_PREFIX = "pii-cleared:"


def build_pii_statements(
    *,
    trust_domain: str,
    epoch: int,
    cleared_kinds: Iterable[str],
    schema: str = "vos3/pii-release/v1",
) -> tuple[list[str], frozenset[int]]:
    """Build the statement list + the set of mandatory indices for a PII
    release credential.

    Layout (index order is the credential's canonical order):
        [0] mandatory: schema
        [1] mandatory: trust-domain
        [2] mandatory: epoch
        [3..] pii-cleared:<kind>   (one selective statement per kind)

    Mandatory statements are always revealed by ProofGen; selective ones
    are revealed on request.
    """
    statements = [
        f"{MANDATORY_PREFIX}schema={schema}",
        f"{MANDATORY_PREFIX}trust-domain={trust_domain}",
        f"{MANDATORY_PREFIX}epoch={int(epoch)}",
    ]
    for kind in sorted({str(k) for k in cleared_kinds}):
        statements.append(f"{PII_CLEARED_PREFIX}{kind}")
    return statements, frozenset({0, 1, 2})


def revealed_pii_kinds(revealed_statements: Iterable[str]) -> frozenset[str]:
    """Extract the cleared PII kinds from a verified proof's revealed
    statements."""
    return frozenset(
        s[len(PII_CLEARED_PREFIX) :]
        for s in revealed_statements
        if s.startswith(PII_CLEARED_PREFIX)
    )


def revealed_trust_domain(revealed_statements: Iterable[str]) -> Optional[str]:
    needle = f"{MANDATORY_PREFIX}trust-domain="
    for s in revealed_statements:
        if s.startswith(needle):
            return s[len(needle) :]
    return None


# ---------------------------------------------------------------------------
# Proof data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BaseProof:
    """The issued credential, held by the holder. Carries everything the
    holder needs to derive selective proofs."""

    issuer_id: str
    issuer_pub: bytes
    holder_commitment: bytes
    merkle_root: bytes
    issuer_signature: bytes
    statements: tuple[str, ...]
    nonces: tuple[bytes, ...]
    mandatory_indices: frozenset[int]


@dataclass(frozen=True)
class DerivedProof:
    """A verifier-scoped, selectively-disclosed proof. Safe to send to a
    verifier — reveals only ``revealed`` statements."""

    issuer_id: str
    issuer_pub: bytes
    merkle_root: bytes
    issuer_signature: bytes
    holder_pub: bytes
    verifier_id: str
    issued_at: float
    expires_at: float
    pseudonym: str
    # index -> (statement, nonce, merkle path)
    revealed: tuple[tuple[int, str, bytes, tuple[tuple[str, bytes], ...]], ...]
    holder_signature: bytes

    @property
    def revealed_statements(self) -> tuple[str, ...]:
        return tuple(stmt for _, stmt, _, _ in self.revealed)

    def holder_signed_payload(self) -> bytes:
        """Bind the holder signature to: the credential root, the verifier
        scope, the time window, the pseudonym, and the exact revealed set.
        A stolen base proof cannot reproduce this without the holder
        secret → forgery rejected."""
        revealed_digest = _sha256(
            *[
                _sha256(idx.to_bytes(4, "big"), stmt.encode("utf-8"), nonce)
                for idx, stmt, nonce, _ in self.revealed
            ]
        )
        return _sha256(
            b"vos3-bbs-derived",
            self.issuer_id.encode("utf-8"),
            self.merkle_root,
            self.holder_pub,
            self.verifier_id.encode("utf-8"),
            repr(self.issued_at).encode("utf-8"),
            repr(self.expires_at).encode("utf-8"),
            self.pseudonym.encode("utf-8"),
            revealed_digest,
        )


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    revealed_statements: tuple[str, ...]
    pseudonym: str
    verifier_id: str
    reason: str = ""


# ---------------------------------------------------------------------------
# Issuer / Holder / Verifier (stub backend)
# ---------------------------------------------------------------------------


@dataclass
class BbsIssuer:
    """vOS attestation service (Trust Domain A) that issues PII-release
    credentials."""

    issuer_id: str
    private_bytes: bytes
    public_bytes: bytes

    def sign(
        self,
        statements: Sequence[str],
        holder_public_bytes: bytes,
        *,
        mandatory_indices: Iterable[int] = (),
        nonce_seed: Optional[bytes] = None,
    ) -> BaseProof:
        _require_stub_backend()
        if not statements:
            raise BbsError("cannot issue a credential with zero statements")
        # Per-statement nonces blind each leaf so a low-entropy statement
        # can't be brute-forced from its Merkle leaf. Seeded → reproducible
        # for tests; random otherwise.
        nonces: list[bytes] = []
        for i in range(len(statements)):
            if nonce_seed is not None:
                nonces.append(
                    _sha256(b"vos3-bbs-nonce", nonce_seed, i.to_bytes(4, "big"))
                )
            else:
                nonces.append(os.urandom(16))
        leaves = [_leaf(i, s, nonces[i]) for i, s in enumerate(statements)]
        root = _merkle_root(leaves)
        commitment = holder_commitment(holder_public_bytes)
        sig = _ed25519_sign(
            self.private_bytes,
            _issuer_root_payload(self.issuer_id, root, commitment),
        )
        return BaseProof(
            issuer_id=self.issuer_id,
            issuer_pub=self.public_bytes,
            holder_commitment=commitment,
            merkle_root=root,
            issuer_signature=sig,
            statements=tuple(statements),
            nonces=tuple(nonces),
            mandatory_indices=frozenset(mandatory_indices),
        )

    @classmethod
    def generate(cls, issuer_id: str) -> "BbsIssuer":
        priv, pub = generate_keypair()
        return cls(issuer_id=issuer_id, private_bytes=priv, public_bytes=pub)


@dataclass
class BbsHolder:
    """The agent / federation bridge that derives selective proofs from a
    base credential bound to its key."""

    private_bytes: bytes
    public_bytes: bytes
    clock: ClockProtocol = field(default_factory=SystemClock)

    def prove(
        self,
        base: BaseProof,
        *,
        reveal: Iterable[int],
        verifier_id: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> DerivedProof:
        _require_stub_backend()
        # Holder must own the key the credential is bound to.
        if not hmac.compare_digest(
            base.holder_commitment, holder_commitment(self.public_bytes)
        ):
            raise BbsError(
                "this holder key does not match the credential's "
                "holder_commitment — cannot derive a proof"
            )
        if not verifier_id:
            raise BbsError("verifier_id is required (proof must be scoped)")

        reveal_set = {int(i) for i in reveal} | set(base.mandatory_indices)
        n = len(base.statements)
        for i in reveal_set:
            if i < 0 or i >= n:
                raise BbsError(f"reveal index {i} out of range [0,{n})")

        leaves = [_leaf(i, s, base.nonces[i]) for i, s in enumerate(base.statements)]
        revealed = [
            (i, base.statements[i], base.nonces[i], _merkle_path(leaves, i))
            for i in sorted(reveal_set)
        ]

        now = self.clock.time()
        expires = now + max(1, int(ttl_seconds))
        # Verifier-scoped pseudonym: requires the holder secret + the
        # verifier id → unlinkable across verifiers, unforgeable without
        # the holder key.
        nym = hmac.new(
            self.private_bytes,
            b"vos3-bbs-nym|" + verifier_id.encode("utf-8") + b"|" + base.merkle_root,
            hashlib.sha256,
        ).hexdigest()

        draft = DerivedProof(
            issuer_id=base.issuer_id,
            issuer_pub=base.issuer_pub,
            merkle_root=base.merkle_root,
            issuer_signature=base.issuer_signature,
            holder_pub=self.public_bytes,
            verifier_id=verifier_id,
            issued_at=now,
            expires_at=expires,
            pseudonym=nym,
            revealed=tuple(revealed),
            holder_signature=b"",
        )
        holder_sig = _ed25519_sign(self.private_bytes, draft.holder_signed_payload())
        return DerivedProof(
            issuer_id=draft.issuer_id,
            issuer_pub=draft.issuer_pub,
            merkle_root=draft.merkle_root,
            issuer_signature=draft.issuer_signature,
            holder_pub=draft.holder_pub,
            verifier_id=draft.verifier_id,
            issued_at=draft.issued_at,
            expires_at=draft.expires_at,
            pseudonym=draft.pseudonym,
            revealed=draft.revealed,
            holder_signature=holder_sig,
        )

    @classmethod
    def generate(cls, clock: Optional[ClockProtocol] = None) -> "BbsHolder":
        priv, pub = generate_keypair()
        return cls(private_bytes=priv, public_bytes=pub, clock=clock or SystemClock())


@dataclass
class BbsVerifier:
    """vOS instance (Trust Domain B) that verifies a derived proof. Learns
    ONLY the revealed statements."""

    clock: ClockProtocol = field(default_factory=SystemClock)

    def verify(
        self,
        derived: DerivedProof,
        issuer_public_bytes: bytes,
        *,
        expected_verifier_id: Optional[str] = None,
    ) -> VerifyResult:
        """Returns a VerifyResult. ok=False (with reason) on any failure;
        never trusts an unverified proof. Fail-closed: callers gate on
        ``result.ok``."""
        _require_stub_backend()
        try:
            return self._verify(derived, issuer_public_bytes, expected_verifier_id)
        except BbsVerifyError as exc:
            return VerifyResult(
                ok=False,
                revealed_statements=(),
                pseudonym="",
                verifier_id=derived.verifier_id,
                reason=str(exc),
            )

    def _verify(
        self,
        derived: DerivedProof,
        issuer_public_bytes: bytes,
        expected_verifier_id: Optional[str],
    ) -> VerifyResult:
        # 1. Verifier-scope binding (if the caller pins it).
        if (
            expected_verifier_id is not None
            and derived.verifier_id != expected_verifier_id
        ):
            raise BbsVerifyError(
                f"proof scoped to verifier {derived.verifier_id!r}, "
                f"expected {expected_verifier_id!r}"
            )

        # 2. Issuer pubkey pinning + issuer signature over the root.
        if not hmac.compare_digest(derived.issuer_pub, issuer_public_bytes):
            raise BbsVerifyError("issuer public key mismatch")
        root_payload = _issuer_root_payload(
            derived.issuer_id,
            derived.merkle_root,
            holder_commitment(derived.holder_pub),
        )
        if not _ed25519_verify(
            issuer_public_bytes, derived.issuer_signature, root_payload
        ):
            raise BbsVerifyError("issuer signature does not verify")

        # 3. Holder binding — derived proof must be signed by the holder
        #    key the credential is bound to. Stolen base proof → no secret
        #    → fails here.
        if not _ed25519_verify(
            derived.holder_pub,
            derived.holder_signature,
            derived.holder_signed_payload(),
        ):
            raise BbsVerifyError("holder binding signature does not verify")

        # 4. Time window.
        now = self.clock.time()
        if now > derived.expires_at:
            raise BbsVerifyError(f"proof expired at {derived.expires_at} (now {now})")
        if now + 1.0 < derived.issued_at:
            raise BbsVerifyError(
                f"proof issued in the future ({derived.issued_at} > {now})"
            )

        # 5. Every revealed statement needs a valid Merkle inclusion path
        #    to the signed root. A statement not in the credential has no
        #    path → over-broad / forged reveal is impossible.
        seen = set()
        for idx, stmt, nonce, path in derived.revealed:
            if idx in seen:
                raise BbsVerifyError(f"duplicate revealed index {idx}")
            seen.add(idx)
            leaf = _leaf(idx, stmt, nonce)
            if not _merkle_verify(leaf, path, derived.merkle_root):
                raise BbsVerifyError(
                    f"revealed statement #{idx} {stmt!r} has no valid "
                    f"Merkle path to the signed root (forged/over-broad)"
                )

        return VerifyResult(
            ok=True,
            revealed_statements=derived.revealed_statements,
            pseudonym=derived.pseudonym,
            verifier_id=derived.verifier_id,
            reason="verified",
        )


__all__ = [
    "BbsBackend",
    "BbsError",
    "BbsBackendUnavailable",
    "BbsVerifyError",
    "BaseProof",
    "DerivedProof",
    "VerifyResult",
    "BbsIssuer",
    "BbsHolder",
    "BbsVerifier",
    "ClockProtocol",
    "SystemClock",
    "build_pii_statements",
    "revealed_pii_kinds",
    "revealed_trust_domain",
    "generate_keypair",
    "holder_commitment",
    "DEFAULT_TTL_SECONDS",
    "ENV_BACKEND",
]
