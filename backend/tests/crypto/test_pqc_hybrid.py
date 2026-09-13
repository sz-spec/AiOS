"""
P2.1 · Hybrid Ed25519 + ML-DSA-65 round-trip / tamper / perf tests.

Honest scope
------------
* Backend on this dev host: pure_python (dilithium-py). The 2 ms /
  4 ms verify/sign budget the directive named is for a 2010-era
  Westmere CPU with liboqs C+AVX2 — physically unreachable from a
  Python backend on any host. We assert a generous wall-clock
  ceiling here that the pure_python path meets on this host, and
  the certification report names the real budget + backend gap.
* "Constant-time" claims for hybrid_verify: tests verify the
  STRUCTURAL invariant (both component verifies run regardless of
  intermediate results). True CPU-cycle constant-time can't be
  measured from Python — that's a property of the underlying
  liboqs build, NOT of this dispatcher.
"""

from __future__ import annotations

import os

import pytest

from services.pqc_sign import (
    hybrid_keygen,
    hybrid_sign,
    hybrid_verify,
    hybrid_binding_digest,
    hybrid_binding_verify,
    verify_path,
    benchmark,
)

# ---------------------------------------------------------------------------
# Backend availability — gate the entire suite
# ---------------------------------------------------------------------------

_BACKEND = verify_path()
pytestmark = pytest.mark.skipif(
    _BACKEND == "unavailable",
    reason="no PQC backend installed (install dilithium-py or oqs-python)",
)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def keypair():
    return hybrid_keygen()


@pytest.mark.parametrize("payload_size", [1, 16, 256, 1024, 4096])
def test_hybrid_roundtrip(keypair, payload_size):
    ed_priv, ed_pub, ml_priv, ml_pub = keypair
    payload = os.urandom(payload_size)
    sig_ed, sig_ml = hybrid_sign(payload, ed_priv, ml_priv)
    assert hybrid_verify(payload, ed_pub, ml_pub, sig_ed, sig_ml) is True


def test_signature_sizes_match_spec(keypair):
    """FIPS 204 ML-DSA-65: 3309-byte signature, 1952-byte pubkey,
    4032-byte privkey. Ed25519: 64-byte signature, 32-byte pubkey,
    32-byte privkey. Total hybrid sig = 3373 bytes (matches the AAA
    plan's reservation for the VBus LARGE_SIG extension)."""
    ed_priv, ed_pub, ml_priv, ml_pub = keypair
    assert len(ed_priv) == 32
    assert len(ed_pub) == 32
    assert len(ml_pub) == 1952
    assert len(ml_priv) == 4032
    payload = b"transcript anchor"
    sig_ed, sig_ml = hybrid_sign(payload, ed_priv, ml_priv)
    assert len(sig_ed) == 64
    assert len(sig_ml) == 3309
    assert len(sig_ed) + len(sig_ml) == 3373


# ---------------------------------------------------------------------------
# Tamper rejection — each axis must independently fail
# ---------------------------------------------------------------------------


def _signed(keypair):
    ed_priv, ed_pub, ml_priv, ml_pub = keypair
    payload = b"sovereign attestation v1"
    sig_ed, sig_ml = hybrid_sign(payload, ed_priv, ml_priv)
    return payload, ed_pub, ml_pub, sig_ed, sig_ml


def test_payload_tamper_rejected(keypair):
    payload, ed_pub, ml_pub, sig_ed, sig_ml = _signed(keypair)
    assert not hybrid_verify(payload + b"x", ed_pub, ml_pub, sig_ed, sig_ml)


@pytest.mark.parametrize("bit_index", [0, 7, 63, 100, 511])
def test_ed25519_signature_bitflip_rejected(keypair, bit_index):
    """A single-bit flip on the Ed25519 signature MUST cause hybrid
    verify to fail — the AND ensures both components must validate."""
    payload, ed_pub, ml_pub, sig_ed, sig_ml = _signed(keypair)
    if bit_index >= len(sig_ed) * 8:
        pytest.skip("bit index outside Ed25519 sig length")
    flipped = bytearray(sig_ed)
    flipped[bit_index // 8] ^= 1 << (bit_index % 8)
    assert not hybrid_verify(payload, ed_pub, ml_pub, bytes(flipped), sig_ml)


@pytest.mark.parametrize("bit_index", [0, 7, 1000, 10000, 26471])
def test_mldsa_signature_bitflip_rejected(keypair, bit_index):
    """A single-bit flip on the ML-DSA signature MUST cause hybrid
    verify to fail. 26471 ≈ last bit of 3309-byte sig."""
    payload, ed_pub, ml_pub, sig_ed, sig_ml = _signed(keypair)
    if bit_index >= len(sig_ml) * 8:
        pytest.skip("bit index outside ML-DSA sig length")
    flipped = bytearray(sig_ml)
    flipped[bit_index // 8] ^= 1 << (bit_index % 8)
    assert not hybrid_verify(payload, ed_pub, ml_pub, sig_ed, bytes(flipped))


def test_forge_with_only_ed25519_rejected(keypair):
    """An attacker who can forge an Ed25519 sig but not ML-DSA — the
    hybrid scheme must STILL reject."""
    payload, ed_pub, ml_pub, sig_ed, _sig_ml = _signed(keypair)
    fake_ml = b"\x00" * 3309
    assert not hybrid_verify(payload, ed_pub, ml_pub, sig_ed, fake_ml)


def test_forge_with_only_mldsa_rejected(keypair):
    """Symmetric: ML-DSA-only forgery (post-quantum break of Ed25519)
    must still be rejected by the hybrid scheme."""
    payload, ed_pub, ml_pub, _sig_ed, sig_ml = _signed(keypair)
    fake_ed = b"\x00" * 64
    assert not hybrid_verify(payload, ed_pub, ml_pub, fake_ed, sig_ml)


def test_wrong_ed25519_pubkey_rejected(keypair):
    payload, _ed_pub, ml_pub, sig_ed, sig_ml = _signed(keypair)
    other_keypair = hybrid_keygen()
    wrong_ed_pub = other_keypair[1]  # ed_pub of another keypair
    assert not hybrid_verify(payload, wrong_ed_pub, ml_pub, sig_ed, sig_ml)


def test_wrong_mldsa_pubkey_rejected(keypair):
    payload, ed_pub, _ml_pub, sig_ed, sig_ml = _signed(keypair)
    other_keypair = hybrid_keygen()
    wrong_ml_pub = other_keypair[3]  # ml_pub of another keypair
    assert not hybrid_verify(payload, ed_pub, wrong_ml_pub, sig_ed, sig_ml)


# ---------------------------------------------------------------------------
# Constant-evaluation invariant — hybrid_verify must NOT short-circuit
# ---------------------------------------------------------------------------


def test_hybrid_verify_runs_both_components_on_ed_fail(keypair, monkeypatch):
    """When Ed25519 verify fails, hybrid_verify must STILL call the
    ML-DSA verify. Mock _mldsa_verify with a counter and assert it
    was invoked exactly once even though the result was doomed."""
    from services import pqc_sign

    payload, ed_pub, ml_pub, _sig_ed, sig_ml = _signed(keypair)
    bad_sig_ed = b"\x00" * 64  # invalid Ed25519 sig

    counter = {"called": 0}
    real = pqc_sign._mldsa_verify

    def counting(*a, **kw):
        counter["called"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(pqc_sign, "_mldsa_verify", counting)
    assert not hybrid_verify(payload, ed_pub, ml_pub, bad_sig_ed, sig_ml)
    assert counter["called"] == 1, (
        "hybrid_verify short-circuited on Ed25519 failure — would leak "
        "timing info about which component failed"
    )


def test_hybrid_verify_runs_both_components_on_ml_fail(keypair, monkeypatch):
    """Symmetric: when ML-DSA verify fails, hybrid_verify must STILL
    have called the Ed25519 verify."""
    from services import pqc_sign

    payload, ed_pub, ml_pub, sig_ed, _sig_ml = _signed(keypair)
    bad_sig_ml = b"\x00" * 3309

    counter = {"called": 0}
    # Patch the cryptography Ed25519 entry point: count from_public_bytes.
    real_from_pub = pqc_sign._ed25519.Ed25519PublicKey.from_public_bytes

    @staticmethod
    def counting_from_pub(b):
        counter["called"] += 1
        return real_from_pub(b)

    monkeypatch.setattr(
        pqc_sign._ed25519.Ed25519PublicKey,
        "from_public_bytes",
        counting_from_pub,
    )
    assert not hybrid_verify(payload, ed_pub, ml_pub, sig_ed, bad_sig_ml)
    assert counter["called"] == 1, (
        "ed25519 verify was skipped after ML-DSA failure path — "
        "structural constant-time invariant broken"
    )


# ---------------------------------------------------------------------------
# Binding digest — VBus LARGE_SIG frame integrity linkage
# ---------------------------------------------------------------------------


def test_binding_digest_deterministic(keypair):
    """Same inputs → same digest. Required for the LARGE_SIG frame
    receiver to recompute and compare."""
    payload, _ed_pub, _ml_pub, sig_ed, sig_ml = _signed(keypair)
    d1 = hybrid_binding_digest(payload, sig_ed, sig_ml)
    d2 = hybrid_binding_digest(payload, sig_ed, sig_ml)
    assert d1 == d2
    assert len(d1) == 32


def test_binding_digest_verify_roundtrip(keypair):
    payload, _ed_pub, _ml_pub, sig_ed, sig_ml = _signed(keypair)
    digest = hybrid_binding_digest(payload, sig_ed, sig_ml)
    assert hybrid_binding_verify(payload, sig_ed, sig_ml, digest) is True


def test_binding_digest_payload_tamper_rejected(keypair):
    payload, _ed_pub, _ml_pub, sig_ed, sig_ml = _signed(keypair)
    digest = hybrid_binding_digest(payload, sig_ed, sig_ml)
    assert not hybrid_binding_verify(payload + b"x", sig_ed, sig_ml, digest)


def test_binding_digest_sig_tamper_rejected(keypair):
    payload, _ed_pub, _ml_pub, sig_ed, sig_ml = _signed(keypair)
    digest = hybrid_binding_digest(payload, sig_ed, sig_ml)
    flipped_ed = bytearray(sig_ed)
    flipped_ed[0] ^= 0x01
    assert not hybrid_binding_verify(payload, bytes(flipped_ed), sig_ml, digest)
    flipped_ml = bytearray(sig_ml)
    flipped_ml[0] ^= 0x01
    assert not hybrid_binding_verify(payload, sig_ed, bytes(flipped_ml), digest)


def test_binding_digest_truncated_rejected(keypair):
    payload, _ed_pub, _ml_pub, sig_ed, sig_ml = _signed(keypair)
    digest = hybrid_binding_digest(payload, sig_ed, sig_ml)
    assert not hybrid_binding_verify(payload, sig_ed, sig_ml, digest[:31])
    assert not hybrid_binding_verify(payload, sig_ed, sig_ml, digest + b"\x00")


def test_binding_digest_is_versioned():
    """The binding digest construction MUST include a version tag so
    a future v2 binding scheme can co-exist without forge-via-prefix
    attacks. Currently 'vOS3.HYBRID.BIND.v1' — pin the literal."""
    from services import pqc_sign
    import inspect

    src = inspect.getsource(pqc_sign.hybrid_binding_digest)
    assert (
        "vOS3.HYBRID.BIND.v1" in src
    ), "binding digest must carry an explicit version tag"


# ---------------------------------------------------------------------------
# Perf — wall-clock budget on the current host
# ---------------------------------------------------------------------------


def test_perf_budget_on_current_host():
    """Measure sign+verify on this host. Assert a GENEROUS ceiling
    (pure-Python verify on Apple Silicon ≈ 5 ms; we cap at 50 ms to
    keep CI green on shared/slow runners).

    The DIRECTIVE'S 2 ms target is for 2010-Westmere + liboqs+AVX2 —
    physically unreachable from Python, documented in pqc_sign.py and
    the certification report. This test enforces an honest ceiling
    appropriate to the active backend."""
    b = benchmark(n_iterations=30, payload_size=1024)
    backend = b["backend"]

    # Different ceilings per backend — honest scope per path.
    if backend == "pure_python":
        verify_ceiling_ms = 50.0
        sign_ceiling_ms = 100.0
    elif backend.startswith("oqs"):
        verify_ceiling_ms = 5.0  # 300 µs typical AVX2; 5 ms conservative
        sign_ceiling_ms = 5.0
    else:
        pytest.skip(f"no backend ({backend})")

    assert b["verify_median_ms"] < verify_ceiling_ms, (
        f"verify {b['verify_median_ms']:.2f}ms exceeds {verify_ceiling_ms}ms "
        f"ceiling for backend={backend} on host {b.get('host_label')}"
    )
    assert b["sign_median_ms"] < sign_ceiling_ms, (
        f"sign {b['sign_median_ms']:.2f}ms exceeds {sign_ceiling_ms}ms "
        f"ceiling for backend={backend}"
    )


def test_benchmark_returns_expected_shape():
    """The certification-report consumer needs these fields. Pin them."""
    b = benchmark(n_iterations=5, payload_size=128)
    for k in (
        "backend",
        "n_iterations",
        "payload_size_bytes",
        "sign_median_ms",
        "verify_median_ms",
        "host_label",
    ):
        assert k in b


# ---------------------------------------------------------------------------
# Honest-scope marker
# ---------------------------------------------------------------------------


def test_pqc_sign_module_carries_phase_marker():
    """Every NEW file in this engagement must say so — auditor can
    grep `git grep vOS.Adaptive.Phase=P2` to recover the perimeter."""
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "services"
        / "pqc_sign.py"
    ).read_text()
    import re

    assert re.search(r"vOS.Adaptive.SHA=[0-9a-f]{7,40}.Phase=P2", src)


def test_pqc_sign_module_documents_honest_scope():
    """The directive's "bit-sliced parallelism in Python" and "thermal
    neutrality" demands cannot be honored. The module's docstring must
    say so out loud."""
    import pathlib

    src = (
        (
            pathlib.Path(__file__).resolve().parent.parent.parent
            / "services"
            / "pqc_sign.py"
        )
        .read_text()
        .lower()
    )
    # Bit-slicing impossibility named
    assert "bit-sliced" in src or "bit-slic" in src
    # Constant-time partial-ness disclosed
    assert "constant-time" in src or "constant time" in src
    # The 2010-Westmere reference vs current-host distinction
    assert "westmere" in src or "2010" in src
