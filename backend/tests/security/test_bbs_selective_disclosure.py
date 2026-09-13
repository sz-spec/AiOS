"""
Tests for Sprint 20 / Primitive (a) — BBS+ selective-disclosure proofs
(backend/services/bbs_selective_disclosure.py) and their O3 integration
(backend/security/outbound_pii_shield.py :: require_bbs_release).

The stub backend uses real Merkle + Ed25519 + HMAC primitives, so these
tests pin the genuine security properties the fail-closed contract
depends on: selective reveal, over-broad rejection, holder binding,
verifier-scoped unlinkable pseudonyms, expiry, and the upgrade of O3's
boolean ReleaseContext to a cryptographic release.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.bbs_selective_disclosure import (  # noqa: E402
    BbsBackendUnavailable,
    BbsError,
    BbsHolder,
    BbsIssuer,
    BbsVerifier,
    ENV_BACKEND,
    _ed25519_sign,
    build_pii_statements,
    generate_keypair,
    revealed_pii_kinds,
    revealed_trust_domain,
)
from security.outbound_pii_shield import (  # noqa: E402
    OutboundPiiBlocked,
    OutboundPiiShield,
)


class FakeClock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


TD_A = "spiffe://issuer.example"
TD_B = "spiffe://verifier.example"


def _issue(clock=None, kinds=("email", "phone"), trust_domain=TD_B):
    """Issue a PII-release credential cleared for `kinds`, bound to a
    fresh holder, returning (issuer, holder, base)."""
    issuer = BbsIssuer.generate(TD_A)
    holder = BbsHolder.generate(clock=clock)
    statements, mandatory = build_pii_statements(
        trust_domain=trust_domain,
        epoch=7,
        cleared_kinds=kinds,
    )
    base = issuer.sign(
        statements,
        holder.public_bytes,
        mandatory_indices=mandatory,
        nonce_seed=b"test-seed",
    )
    return issuer, holder, base


# --------------------------------------------------------------------------
# Core BBS properties
# --------------------------------------------------------------------------


def test_reveal_subset_verifies_and_hides_unrevealed():
    issuer, holder, base = _issue(kinds=("email", "phone", "ssn"))
    # statements: [schema, td, epoch, email, phone, ssn] -> reveal only email
    email_idx = base.statements.index("pii-cleared:email")
    derived = holder.prove(base, reveal=[email_idx], verifier_id=TD_B)

    result = BbsVerifier().verify(derived, issuer.public_bytes)
    assert result.ok, result.reason
    cleared = revealed_pii_kinds(result.revealed_statements)
    assert cleared == frozenset({"email"})
    # phone/ssn statements are NOT in the revealed set
    assert "pii-cleared:phone" not in result.revealed_statements
    assert "pii-cleared:ssn" not in result.revealed_statements


def test_mandatory_statements_always_revealed():
    issuer, holder, base = _issue(kinds=("email",))
    derived = holder.prove(base, reveal=[], verifier_id=TD_B)
    result = BbsVerifier().verify(derived, issuer.public_bytes)
    assert result.ok
    # trust-domain (mandatory) is present even though we revealed nothing
    assert revealed_trust_domain(result.revealed_statements) == TD_B


def test_cross_verifier_pseudonyms_are_unlinkable():
    issuer, holder, base = _issue()
    d_a = holder.prove(base, reveal=[], verifier_id="verifier-A")
    d_b = holder.prove(base, reveal=[], verifier_id="verifier-B")
    assert d_a.pseudonym != d_b.pseudonym
    # same verifier id -> stable pseudonym (correlatable within a verifier)
    d_a2 = holder.prove(base, reveal=[], verifier_id="verifier-A")
    assert d_a.pseudonym == d_a2.pseudonym


def test_expired_proof_rejected():
    clock = FakeClock()
    issuer, holder, base = _issue(clock=clock)
    derived = holder.prove(base, reveal=[], verifier_id=TD_B, ttl_seconds=60)
    verifier = BbsVerifier(clock=clock)
    assert verifier.verify(derived, issuer.public_bytes).ok
    clock.advance(61)
    result = verifier.verify(derived, issuer.public_bytes)
    assert not result.ok
    assert "expired" in result.reason


def test_holder_binding_forgery_rejected():
    """A stolen base proof cannot mint a verifiable derived proof: the
    derived proof's holder signature must verify under the bound key."""
    issuer, holder, base = _issue()
    derived = holder.prove(base, reveal=[], verifier_id=TD_B)
    # Attacker tampers the holder signature.
    forged = type(derived)(**{**derived.__dict__, "holder_signature": b"\x00" * 64})
    result = BbsVerifier().verify(forged, issuer.public_bytes)
    assert not result.ok
    assert "holder binding" in result.reason


def test_wrong_holder_cannot_derive():
    issuer, _holder, base = _issue()
    attacker = BbsHolder.generate()  # different key, not bound to base
    with pytest.raises(BbsError):
        attacker.prove(base, reveal=[], verifier_id=TD_B)


def test_wrong_issuer_key_rejected():
    issuer, holder, base = _issue()
    derived = holder.prove(base, reveal=[], verifier_id=TD_B)
    _, other_pub = generate_keypair()
    result = BbsVerifier().verify(derived, other_pub)
    assert not result.ok
    assert "issuer public key mismatch" in result.reason


def test_over_broad_reveal_rejected():
    """A MALICIOUS holder (who holds a legitimately-issued credential and
    its key) tries to reveal a statement that is NOT in their credential
    and re-signs the tampered set with their own valid holder key. The
    holder-binding check passes (they signed it), so the Merkle inclusion
    check is the backstop that catches the over-broad reveal."""
    issuer, holder, base = _issue(kinds=("email",))
    email_idx = base.statements.index("pii-cleared:email")
    derived = holder.prove(base, reveal=[email_idx], verifier_id=TD_B)
    # Rewrite the revealed email statement to claim 'ssn' instead.
    tampered_revealed = tuple(
        (idx, "pii-cleared:ssn" if stmt == "pii-cleared:email" else stmt, nonce, path)
        for idx, stmt, nonce, path in derived.revealed
    )
    forged = type(derived)(
        **{**derived.__dict__, "revealed": tampered_revealed, "holder_signature": b""}
    )
    # Malicious holder re-signs the tampered payload with their real key.
    valid_sig = _ed25519_sign(holder.private_bytes, forged.holder_signed_payload())
    forged = type(forged)(**{**forged.__dict__, "holder_signature": valid_sig})
    result = BbsVerifier().verify(forged, issuer.public_bytes)
    assert not result.ok
    assert "Merkle path" in result.reason


def test_verifier_scope_pinning_rejects_mismatch():
    issuer, holder, base = _issue()
    derived = holder.prove(base, reveal=[], verifier_id="verifier-A")
    result = BbsVerifier().verify(
        derived, issuer.public_bytes, expected_verifier_id="verifier-B"
    )
    assert not result.ok
    assert "scoped to verifier" in result.reason


def test_blst_backend_fails_loud(monkeypatch):
    monkeypatch.setenv(ENV_BACKEND, "blst")
    issuer = BbsIssuer.generate(TD_A)
    _, holder_pub = generate_keypair()
    with pytest.raises(BbsBackendUnavailable):
        issuer.sign(["mandatory:x"], holder_pub)


# --------------------------------------------------------------------------
# O3 integration — require_bbs_release upgrades the boolean ReleaseContext
# --------------------------------------------------------------------------

EXTERNAL = "https://partner.example/ingest"
EMAIL_PAYLOAD = "record: alice@aidg.com placed an order"


def test_o3_blocks_pii_without_proof():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    issuer = BbsIssuer.generate(TD_A)
    with pytest.raises(OutboundPiiBlocked):
        shield.require_bbs_release(
            EMAIL_PAYLOAD,
            EXTERNAL,
            derived_proof=None,
            issuer_public_bytes=issuer.public_bytes,
            destination_trust_domain=TD_B,
        )


def test_o3_allows_pii_with_valid_proof():
    clock = FakeClock()
    issuer, holder, base = _issue(clock=clock, kinds=("email",), trust_domain=TD_B)
    email_idx = base.statements.index("pii-cleared:email")
    derived = holder.prove(base, reveal=[email_idx], verifier_id=TD_B)

    shield = OutboundPiiShield(trusted_destinations=frozenset())
    decision = shield.require_bbs_release(
        EMAIL_PAYLOAD,
        EXTERNAL,
        derived_proof=derived,
        issuer_public_bytes=issuer.public_bytes,
        destination_trust_domain=TD_B,
        verifier=BbsVerifier(clock=clock),
    )
    assert decision.allowed
    assert decision.released_by_bbs
    assert shield.stats.allowed_bbs_released == 1


def test_o3_blocks_when_proof_clears_wrong_kind():
    """Proof clears 'phone' but payload carries an email → fail-closed."""
    issuer, holder, base = _issue(kinds=("phone",), trust_domain=TD_B)
    phone_idx = base.statements.index("pii-cleared:phone")
    derived = holder.prove(base, reveal=[phone_idx], verifier_id=TD_B)

    shield = OutboundPiiShield(trusted_destinations=frozenset())
    with pytest.raises(OutboundPiiBlocked) as exc:
        shield.require_bbs_release(
            EMAIL_PAYLOAD,
            EXTERNAL,
            derived_proof=derived,
            issuer_public_bytes=issuer.public_bytes,
            destination_trust_domain=TD_B,
        )
    assert "does not clear PII kinds" in exc.value.decision.reason


def test_o3_blocks_on_wrong_trust_domain():
    issuer, holder, base = _issue(kinds=("email",), trust_domain="other-td")
    email_idx = base.statements.index("pii-cleared:email")
    derived = holder.prove(base, reveal=[email_idx], verifier_id="other-td")

    shield = OutboundPiiShield(trusted_destinations=frozenset())
    with pytest.raises(OutboundPiiBlocked):
        shield.require_bbs_release(
            EMAIL_PAYLOAD,
            EXTERNAL,
            derived_proof=derived,
            issuer_public_bytes=issuer.public_bytes,
            destination_trust_domain=TD_B,  # mismatch vs proof's other-td
        )


def test_o3_blocks_on_expired_proof():
    clock = FakeClock()
    issuer, holder, base = _issue(clock=clock, kinds=("email",), trust_domain=TD_B)
    email_idx = base.statements.index("pii-cleared:email")
    derived = holder.prove(base, reveal=[email_idx], verifier_id=TD_B, ttl_seconds=30)
    clock.advance(31)
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    with pytest.raises(OutboundPiiBlocked):
        shield.require_bbs_release(
            EMAIL_PAYLOAD,
            EXTERNAL,
            derived_proof=derived,
            issuer_public_bytes=issuer.public_bytes,
            destination_trust_domain=TD_B,
            verifier=BbsVerifier(clock=clock),
        )


def test_o3_clean_payload_allowed_without_proof():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    issuer = BbsIssuer.generate(TD_A)
    decision = shield.require_bbs_release(
        "build complete; 0 warnings",
        EXTERNAL,
        derived_proof=None,
        issuer_public_bytes=issuer.public_bytes,
        destination_trust_domain=TD_B,
    )
    assert decision.allowed
    assert not decision.has_pii
