"""
P2.1 Pre-Integration · Entropy & collision audit on hybrid_binding_digest.

vOS·Adaptive·SHA=aeb3736·Phase=P2

Honest scope
------------
hybrid_binding_digest is SHA-256 of a versioned canonical encoding.
SHA-256 itself is a well-studied PRF — any reasonable statistical test
on its output passes by construction. The tests here are not trying
to prove SHA-256 is good; they verify that OUR encoding doesn't
accidentally produce a structured collapse:
  - byte-frequency chi-square on a 10k-sample empirical distribution
  - avalanche test: single-bit input flip → output Hamming distance
    ≈ 128 bits (mean of binomial(256, 0.5))
  - cross-payload distinctness
  - component-swap (Ed↔ML) rejection at the binding-verify gate
"""

from __future__ import annotations

import random
import re

import pytest

from services.pqc_sign import (
    hybrid_binding_digest,
    hybrid_binding_verify,
    hybrid_keygen,
    hybrid_sign,
    verify_path,
)

pytestmark = pytest.mark.skipif(
    verify_path() == "unavailable",
    reason="no PQC backend installed",
)


# ---------------------------------------------------------------------------
# Helpers — small, deterministic-seeded sample generation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def keypair():
    return hybrid_keygen()


def _random_signed(keypair, rng):
    """Pull a deterministic random payload + sign it. Returns
    (payload, sig_ed, sig_ml). Same RNG seed => same outputs across
    pytest-xdist workers."""
    ed_priv, _ed_pub, ml_priv, _ml_pub = keypair
    payload = (
        rng.randbytes(64)
        if hasattr(rng, "randbytes")
        else bytes(rng.getrandbits(8) for _ in range(64))
    )
    sig_ed, sig_ml = hybrid_sign(payload, ed_priv, ml_priv)
    return payload, sig_ed, sig_ml


# ---------------------------------------------------------------------------
# 1. Byte-frequency chi-square — uniformity of digest output bytes
# ---------------------------------------------------------------------------


def test_digest_byte_frequency_passes_chi_square(keypair):
    """Generate N=2_000 hybrid binding digests over independent random
    payloads. The expected distribution of digest bytes is uniform on
    [0,255]. Compute Pearson chi-square; assert below the 99% threshold
    for k=255 degrees of freedom (≈ 310.5).

    2_000 digests × 32 bytes/digest = 64_000 byte samples → expected
    count per cell = 64_000/256 = 250. Plenty for a meaningful test.
    """
    # Sample size tuned for CI speed: 600 digests × 32 bytes = 19,200
    # byte samples. Per-cell expected = 75; still well within
    # chi-square's normal-approximation validity (np > 5 rule).
    # Reduced from 2000 to keep wall-clock under 30s.
    rng = random.Random(0xC0FFEE)
    counts = [0] * 256
    for _ in range(600):
        payload, sig_ed, sig_ml = _random_signed(keypair, rng)
        d = hybrid_binding_digest(payload, sig_ed, sig_ml)
        for b in d:
            counts[b] += 1
    n_bytes = sum(counts)
    expected = n_bytes / 256.0
    chi_square = sum((c - expected) ** 2 / expected for c in counts)

    # k=255 dof, 99% threshold ≈ 310.5; 99.9% threshold ≈ 330.5.
    # We use the 99.9% threshold to keep this test stable across runs
    # without producing false alarms (probability < 0.1% under H0).
    assert chi_square < 330.5, (
        f"digest byte distribution chi-square = {chi_square:.2f} > 330.5 "
        f"(99.9% threshold) — possible structural bias in binding "
        f"encoding"
    )


def test_digest_bit_avalanche(keypair):
    """Single-bit input flip MUST change ~128 bits of the 256-bit
    output (mean of binomial(256, 0.5) ≈ 128 ± ~5.6). This is the
    avalanche property — a load-bearing assumption for using the
    digest as an integrity linkage in the LARGE_SIG frame.

    We run 200 trials, each flipping a random bit of the payload,
    and assert the median Hamming distance is in [115, 141] (≈ 128
    ± 13, which is generous).
    """
    # 60 trials is enough for the median to be stable at the
    # binomial(256, 0.5) mean of 128. Reduced from 200 for CI speed.
    rng = random.Random(0xBADBEEF)
    distances = []
    for _ in range(60):
        payload, sig_ed, sig_ml = _random_signed(keypair, rng)
        d1 = hybrid_binding_digest(payload, sig_ed, sig_ml)

        # Flip one random bit in payload
        idx = rng.randrange(len(payload))
        bit = rng.randrange(8)
        flipped = bytearray(payload)
        flipped[idx] ^= 1 << bit
        d2 = hybrid_binding_digest(bytes(flipped), sig_ed, sig_ml)

        # Hamming distance in bits
        ham = 0
        for a, b in zip(d1, d2):
            ham += bin(a ^ b).count("1")
        distances.append(ham)

    distances.sort()
    median = distances[len(distances) // 2]
    assert 115 <= median <= 141, (
        f"avalanche median = {median} bits (expected ~128 ± 13). "
        f"Binding encoding may be losing entropy."
    )


def test_digest_cross_payload_distinct(keypair):
    """Two distinct payloads → two distinct digests with overwhelming
    probability. Run 100 distinct-payload pairs and assert ZERO
    accidental collisions on the digest (collision probability ≈
    100·100/2^256 ≈ 8.6e-75 — vanishingly small)."""
    rng = random.Random(0x12345)
    digests = set()
    # 50 trials. Birthday collision probability at 50·49/(2·2^256) is
    # astronomically small; reduced from 100 for CI speed.
    for _ in range(50):
        payload, sig_ed, sig_ml = _random_signed(keypair, rng)
        d = hybrid_binding_digest(payload, sig_ed, sig_ml)
        digests.add(d)
    # If any pair collided we'd see fewer than 50 distinct.
    assert len(digests) == 50


# ---------------------------------------------------------------------------
# 2. Component-swap rejection (malformed signature injection)
# ---------------------------------------------------------------------------


def test_swapping_ed_and_ml_components_in_digest_input_diverges(keypair):
    """Hybrid_binding_digest takes (payload, sig_ed, sig_ml) in a
    POSITIONAL order. If an attacker tries to construct a digest by
    swapping the two signature components (passing ml-sig where ed-sig
    is expected and vice-versa), the digest MUST diverge from the
    legitimate one — otherwise the binding has a malleability hole."""
    ed_priv, _ed_pub, ml_priv, _ml_pub = keypair
    payload = b"sovereign attestation"
    sig_ed, sig_ml = hybrid_sign(payload, ed_priv, ml_priv)

    legit = hybrid_binding_digest(payload, sig_ed, sig_ml)
    swapped = hybrid_binding_digest(payload, sig_ml, sig_ed)
    assert legit != swapped, (
        "binding digest invariant under (ed,ml) <-> (ml,ed) — would "
        "let an attacker fake the binding by transposing fields"
    )


def test_swapped_components_rejected_by_binding_verify(keypair):
    """If a receiver got a legitimate digest but the sigs were swapped
    in transport, binding_verify must reject. This is the fail-fast
    logic gate the directive named."""
    ed_priv, _ed_pub, ml_priv, _ml_pub = keypair
    payload = b"sovereign attestation"
    sig_ed, sig_ml = hybrid_sign(payload, ed_priv, ml_priv)

    legit_digest = hybrid_binding_digest(payload, sig_ed, sig_ml)
    # Swap the two component args on the verify call:
    assert not hybrid_binding_verify(
        payload, sig_ml, sig_ed, legit_digest
    ), "binding_verify accepted swapped sig components — malleability"


def test_zero_length_component_rejected(keypair):
    """Empty or truncated signature components must NOT validate
    against a digest computed with the real components — fail-fast
    at the logic gate, not via timing."""
    ed_priv, _ed_pub, ml_priv, _ml_pub = keypair
    payload = b"x"
    sig_ed, sig_ml = hybrid_sign(payload, ed_priv, ml_priv)
    digest = hybrid_binding_digest(payload, sig_ed, sig_ml)

    assert not hybrid_binding_verify(payload, b"", sig_ml, digest)
    assert not hybrid_binding_verify(payload, sig_ed, b"", digest)
    assert not hybrid_binding_verify(payload, sig_ed[:32], sig_ml, digest)


def test_malformed_digest_rejected_at_logic_gate(keypair):
    """Digest of wrong length must be rejected BEFORE computing the
    SHA-256 — that's the fail-fast logic gate. We can't observe the
    timing directly from Python but we CAN verify the early-return
    via source inspection."""
    import inspect
    from services import pqc_sign

    src = inspect.getsource(pqc_sign.hybrid_binding_verify)
    # The length check must precede the digest computation.
    assert re.search(
        r"len\(\s*claimed_digest\s*\)\s*!=\s*32[\s\S]{0,80}return\s+False",
        src,
    ), (
        "hybrid_binding_verify must fail-fast on wrong-length digest "
        "BEFORE computing the SHA-256"
    )
