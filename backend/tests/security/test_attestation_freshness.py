"""
backend/tests/security/test_attestation_freshness.py

Sprint 16 / Item D5 — Anti-replay nonce-gate tests for TEE attestation.

Covers the per-request nonce binding per draft-ietf-rats-ar4si-09:
- issue_nonce + verify_quote happy path.
- Replay attack: second verify with same nonce → NonceMissingError.
- Expired nonce: verify after TTL → NonceExpiredError.
- Audience mismatch: nonce issued for service-A, verified for service-B
  → NonceAudienceMismatchError.
- Binding mismatch: quote REPORTDATA does NOT contain SHA-256(nonce)
  → NonceBindingMismatchError.
- Quote shorter than 32 bytes → NonceBindingMismatchError.
- Issue / verify input validation (empty audience, empty nonce, non-bytes
  quote, non-positive TTL).
- Stats counters track all rejection classes.
- Expired nonces are reaped opportunistically.
- Concurrent issue + verify don't race.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NG_PATH = _REPO_ROOT / "backend" / "services" / "attestation_nonce_gate.py"
_spec = importlib.util.spec_from_file_location("vos3_nonce_gate_under_test", _NG_PATH)
ng = importlib.util.module_from_spec(_spec)
sys.modules["vos3_nonce_gate_under_test"] = ng
_spec.loader.exec_module(ng)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_issue_then_verify_happy_path():
    gate = ng.NonceGate(default_ttl_seconds=30)
    nonce = gate.issue_nonce(audience="svc-A")
    quote = ng.make_quote_with_nonce(b"quote-body-bytes", nonce)
    verified = gate.verify_quote(quote, nonce, audience="svc-A")
    assert verified.nonce == nonce
    assert verified.audience == "svc-A"
    assert len(verified.quote_sha256) == 64
    # SHA-256 actually matches the quote bytes.
    assert verified.quote_sha256 == hashlib.sha256(quote).hexdigest()


def test_nonce_is_hex_encoded_and_random():
    gate = ng.NonceGate()
    a = gate.issue_nonce(audience="svc")
    b = gate.issue_nonce(audience="svc")
    assert a != b
    # 32 bytes → 64 hex chars.
    assert len(a) == 64
    int(a, 16)  # raises if not hex


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def test_replay_blocked_second_verify_fails():
    gate = ng.NonceGate()
    nonce = gate.issue_nonce(audience="svc")
    quote = ng.make_quote_with_nonce(b"body", nonce)
    gate.verify_quote(quote, nonce, audience="svc")
    # Replay the SAME quote — the nonce was consumed.
    with pytest.raises(ng.NonceMissingError):
        gate.verify_quote(quote, nonce, audience="svc")


def test_replay_blocked_with_never_issued_nonce():
    gate = ng.NonceGate()
    fake_nonce = "00" * 32
    quote = ng.make_quote_with_nonce(b"body", fake_nonce)
    with pytest.raises(ng.NonceMissingError):
        gate.verify_quote(quote, fake_nonce, audience="svc")
    stats = gate.snapshot_stats()
    assert stats.replays_blocked == 1


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_expired_nonce_rejected():
    gate = ng.NonceGate(default_ttl_seconds=1)
    nonce = gate.issue_nonce(audience="svc")
    quote = ng.make_quote_with_nonce(b"body", nonce)
    # Sleep past TTL.
    time.sleep(1.1)
    with pytest.raises(ng.NonceExpiredError):
        gate.verify_quote(quote, nonce, audience="svc")


def test_custom_ttl_per_issue():
    gate = ng.NonceGate(default_ttl_seconds=300)
    nonce = gate.issue_nonce(audience="svc", ttl_seconds=1)
    time.sleep(1.1)
    quote = ng.make_quote_with_nonce(b"body", nonce)
    with pytest.raises(ng.NonceExpiredError):
        gate.verify_quote(quote, nonce, audience="svc")


def test_issue_rejects_non_positive_ttl():
    gate = ng.NonceGate()
    with pytest.raises(ValueError):
        gate.issue_nonce(audience="svc", ttl_seconds=0)
    with pytest.raises(ValueError):
        gate.issue_nonce(audience="svc", ttl_seconds=-5)


# ---------------------------------------------------------------------------
# Audience binding
# ---------------------------------------------------------------------------


def test_audience_mismatch_rejected():
    gate = ng.NonceGate()
    nonce = gate.issue_nonce(audience="svc-A")
    quote = ng.make_quote_with_nonce(b"body", nonce)
    with pytest.raises(ng.NonceAudienceMismatchError):
        gate.verify_quote(quote, nonce, audience="svc-B")


def test_issue_rejects_empty_audience():
    gate = ng.NonceGate()
    with pytest.raises(ValueError):
        gate.issue_nonce(audience="")
    with pytest.raises(ValueError):
        gate.issue_nonce(audience=None)  # type: ignore[arg-type]


def test_verify_rejects_empty_audience():
    gate = ng.NonceGate()
    nonce = gate.issue_nonce(audience="svc-A")
    quote = ng.make_quote_with_nonce(b"body", nonce)
    with pytest.raises(ValueError):
        gate.verify_quote(quote, nonce, audience="")


# ---------------------------------------------------------------------------
# Binding (REPORTDATA contains SHA-256(nonce))
# ---------------------------------------------------------------------------


def test_binding_mismatch_rejected():
    """Attester returns a quote whose REPORTDATA is NOT the SHA-256 of
    the nonce — should be rejected even though the nonce itself is in
    the issued set."""
    gate = ng.NonceGate()
    nonce = gate.issue_nonce(audience="svc")
    # Wrong binding: REPORTDATA = all-zeros, not SHA256(nonce).
    bad_quote = b"body" + b"\x00" * 32
    with pytest.raises(ng.NonceBindingMismatchError):
        gate.verify_quote(bad_quote, nonce, audience="svc")
    stats = gate.snapshot_stats()
    assert stats.binding_blocked == 1


def test_quote_too_short_rejected():
    gate = ng.NonceGate()
    nonce = gate.issue_nonce(audience="svc")
    with pytest.raises(ng.NonceBindingMismatchError):
        gate.verify_quote(b"short", nonce, audience="svc")


def test_verify_rejects_non_bytes_quote():
    gate = ng.NonceGate()
    nonce = gate.issue_nonce(audience="svc")
    with pytest.raises(TypeError):
        gate.verify_quote("not-bytes", nonce, audience="svc")  # type: ignore[arg-type]


def test_verify_rejects_empty_claimed_nonce():
    gate = ng.NonceGate()
    with pytest.raises(ValueError):
        gate.verify_quote(b"body" + b"\x00" * 32, "", audience="svc")


# ---------------------------------------------------------------------------
# Stats + reaping
# ---------------------------------------------------------------------------


def test_stats_counters():
    gate = ng.NonceGate(default_ttl_seconds=1)
    # 2 issued, 1 verified.
    n1 = gate.issue_nonce(audience="svc")
    n2 = gate.issue_nonce(audience="svc")
    quote1 = ng.make_quote_with_nonce(b"body", n1)
    gate.verify_quote(quote1, n1, audience="svc")
    # Replay attempt on n1.
    with pytest.raises(ng.NonceMissingError):
        gate.verify_quote(quote1, n1, audience="svc")
    # Audience mismatch attempt on n2.
    quote2 = ng.make_quote_with_nonce(b"body", n2)
    with pytest.raises(ng.NonceAudienceMismatchError):
        gate.verify_quote(quote2, n2, audience="OTHER")
    snap = gate.snapshot_stats()
    assert snap.nonces_issued == 2
    assert snap.quotes_verified == 1
    assert snap.replays_blocked == 1
    assert snap.audience_blocked == 1


def test_snapshot_returns_copy():
    gate = ng.NonceGate()
    snap1 = gate.snapshot_stats()
    gate.issue_nonce(audience="svc")
    snap2 = gate.snapshot_stats()
    assert snap1.nonces_issued == 0
    assert snap2.nonces_issued == 1


def test_expired_nonces_reaped_on_issue():
    gate = ng.NonceGate(default_ttl_seconds=1)
    n = gate.issue_nonce(audience="svc")
    time.sleep(1.1)
    # Issuing a new nonce should reap the expired one.
    gate.issue_nonce(audience="svc")
    # The expired one should no longer be verifiable.
    quote = ng.make_quote_with_nonce(b"body", n)
    with pytest.raises(ng.NonceMissingError):
        gate.verify_quote(quote, n, audience="svc")


# ---------------------------------------------------------------------------
# Concurrency smoke
# ---------------------------------------------------------------------------


def test_concurrent_issue_and_verify_no_corruption():
    gate = ng.NonceGate(default_ttl_seconds=30)
    errors = []

    def issue_and_verify():
        try:
            for _ in range(50):
                n = gate.issue_nonce(audience="svc")
                q = ng.make_quote_with_nonce(b"body", n)
                gate.verify_quote(q, n, audience="svc")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=issue_and_verify) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        if t.is_alive():
            pytest.fail("thread did not terminate — possible deadlock")
    assert not errors, f"unexpected exceptions: {errors}"
    snap = gate.snapshot_stats()
    assert snap.nonces_issued == 200
    assert snap.quotes_verified == 200
    assert snap.replays_blocked == 0
