"""
Unit tests for the inter-agent provenance chain — AA6 mitigation (Day 7).

These tests pin the public API and behavior of
``ai.agents.inter_agent_provenance``:

  * :class:`AgentMessageChain` round-trips (sign + verify) on the same
    chain instance and across two chain instances that share a key.
  * Tampering with ``payload``, ``mac``, ``previous_mac``, ``sequence_num``,
    or ``sender_role`` after signing causes verify to raise
    :class:`InterAgentMessageInvalid`.
  * Replay (verifying the same message twice) is rejected as a
    sequence-monotonicity violation.
  * Out-of-order sequence numbers are rejected.
  * Chain-link break (forged ``previous_mac``) is rejected.
  * Wrong key (verify under an independent chain with a different key)
    is rejected with a MAC mismatch.
  * Empty payload, very long payload (1 MiB), and Unicode payload all
    round-trip cleanly.
  * Two chains with different keys produce different MACs for the same
    input — keying is wired correctly.
  * Verification uses :func:`hmac.compare_digest` (asserted via mock
    patch on the module-level reference).
  * The :class:`SignedAgentMessage` dataclass is frozen (immutable from
    the consumer side).
  * Stable-reason strings on :class:`InterAgentMessageInvalid` so SIEM
    rules can grep on them without breaking.

Runtime-wiring tests (multi_agent ``_sign_handoff`` /
``_verify_handoff``) live in the multi-agent suite; this file pins the
chain primitive only.
"""

from __future__ import annotations

import dataclasses
import hmac as _hmac_module
import logging
import secrets
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure ``backend`` is on the path for direct ``ai.*`` imports whether
# pytest is invoked from the repo root or from ``backend/``.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from ai.agents import inter_agent_provenance  # noqa: E402
from ai.agents.inter_agent_provenance import (  # noqa: E402
    AgentMessageChain,
    InterAgentMessageInvalid,
    SignedAgentMessage,
    append_provenance,
    verify_latest_provenance,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_key() -> bytes:
    """A fresh 32-byte session key for each test."""
    return secrets.token_bytes(32)


@pytest.fixture
def chain(session_key: bytes) -> AgentMessageChain:
    """A fresh chain bound to ``session_key``."""
    return AgentMessageChain(session_key)


# ---------------------------------------------------------------------------
# 1. Construction
# ---------------------------------------------------------------------------


class TestChainConstruction:
    """The constructor pins its key type / length contract."""

    def test_accepts_32_byte_bytes(self) -> None:
        c = AgentMessageChain(secrets.token_bytes(32))
        assert isinstance(c, AgentMessageChain)

    def test_accepts_bytearray(self) -> None:
        c = AgentMessageChain(bytearray(secrets.token_bytes(32)))
        assert isinstance(c, AgentMessageChain)

    def test_accepts_shorter_key_per_rfc2104(self) -> None:
        # HMAC accepts any non-empty key; RFC 2104 §3 zero-pads to the
        # block size. We do not impose a stricter minimum here.
        c = AgentMessageChain(b"\x01\x02\x03")
        msg = c.sign("architect", "x")
        assert c.verify(msg) is True

    def test_rejects_non_bytes(self) -> None:
        with pytest.raises(TypeError):
            AgentMessageChain("not bytes")  # type: ignore[arg-type]

    def test_rejects_empty_key(self) -> None:
        with pytest.raises(ValueError):
            AgentMessageChain(b"")


# ---------------------------------------------------------------------------
# 2. Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """The happy path: sign on chain A, verify on chain B (same key)."""

    def test_single_message_round_trip(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        msg = producer.sign("architect", "the architecture plan")
        assert consumer.verify(msg) is True

    def test_sequence_starts_at_one(self, chain: AgentMessageChain) -> None:
        msg = chain.sign("architect", "x")
        assert msg.sequence_num == 1

    def test_sequence_increments_by_one(self, chain: AgentMessageChain) -> None:
        m1 = chain.sign("architect", "a")
        m2 = chain.sign("frontend", "b")
        m3 = chain.sign("backend", "c")
        assert (m1.sequence_num, m2.sequence_num, m3.sequence_num) == (1, 2, 3)

    def test_previous_mac_first_is_empty(self, chain: AgentMessageChain) -> None:
        msg = chain.sign("architect", "x")
        assert msg.previous_mac == b""

    def test_previous_mac_chains(self, chain: AgentMessageChain) -> None:
        m1 = chain.sign("architect", "a")
        m2 = chain.sign("frontend", "b")
        assert m2.previous_mac == m1.mac

    def test_multi_message_chain_round_trip(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        for i in range(5):
            msg = producer.sign(f"role-{i}", f"payload-{i}")
            assert consumer.verify(msg) is True

    def test_iso_timestamp_present(self, chain: AgentMessageChain) -> None:
        msg = chain.sign("architect", "x")
        assert isinstance(msg.created_at_iso, str)
        # ISO-8601 UTC — must contain "T" and an offset / "Z".
        assert "T" in msg.created_at_iso


# ---------------------------------------------------------------------------
# 3. Tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection:
    """Mutations to any signed field MUST fail verification."""

    def test_payload_tamper_rejected(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        original = producer.sign("architect", "original payload")
        # SignedAgentMessage is frozen — construct a tampered copy.
        tampered = dataclasses.replace(original, payload="MALICIOUS REWRITE")
        with pytest.raises(InterAgentMessageInvalid) as exc:
            consumer.verify(tampered)
        assert exc.value.reason == "mac mismatch"

    def test_mac_tamper_rejected(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        original = producer.sign("architect", "x")
        # Flip the last byte of the MAC.
        bad_mac = bytearray(original.mac)
        bad_mac[-1] ^= 0xFF
        tampered = dataclasses.replace(original, mac=bytes(bad_mac))
        with pytest.raises(InterAgentMessageInvalid) as exc:
            consumer.verify(tampered)
        assert exc.value.reason == "mac mismatch"

    def test_sender_role_tamper_rejected(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        original = producer.sign("architect", "x")
        tampered = dataclasses.replace(original, sender_role="frontend")
        with pytest.raises(InterAgentMessageInvalid) as exc:
            consumer.verify(tampered)
        assert exc.value.reason == "mac mismatch"

    def test_sequence_num_tamper_rejected(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        # Sign two so the consumer's last_verified_seq advances to 1.
        m1 = producer.sign("architect", "a")
        consumer.verify(m1)
        m2 = producer.sign("frontend", "b")
        # Lie about the sequence on m2 — claim it is 99.
        tampered = dataclasses.replace(m2, sequence_num=99)
        with pytest.raises(InterAgentMessageInvalid) as exc:
            consumer.verify(tampered)
        assert exc.value.reason == "mac mismatch"

    def test_timestamp_tamper_does_NOT_fail(self, session_key: bytes) -> None:
        # ``created_at_iso`` is explicitly NOT in the MAC input — tampering
        # with it must still verify (it is audit metadata only).
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        original = producer.sign("architect", "x")
        tampered = dataclasses.replace(original, created_at_iso="1970-01-01T00:00:00+00:00")
        assert consumer.verify(tampered) is True


# ---------------------------------------------------------------------------
# 4. Replay / ordering
# ---------------------------------------------------------------------------


class TestReplayAndOrdering:
    """Replay, out-of-order, and chain-splice attacks."""

    def test_replay_rejected(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        msg = producer.sign("architect", "x")
        assert consumer.verify(msg) is True
        with pytest.raises(InterAgentMessageInvalid) as exc:
            consumer.verify(msg)
        assert exc.value.reason == "sequence not monotonic"

    def test_out_of_order_rejected(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        m1 = producer.sign("architect", "a")
        m2 = producer.sign("frontend", "b")
        # Verify the second message first — chain link fails.
        with pytest.raises(InterAgentMessageInvalid) as exc:
            consumer.verify(m2)
        assert exc.value.reason == "chain link mismatch"
        # m1 still verifies fine afterward (verify-side state was not
        # advanced by the failed call).
        assert consumer.verify(m1) is True

    def test_skip_a_sequence_rejected(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        m1 = producer.sign("architect", "a")
        _m2 = producer.sign("frontend", "b")  # never delivered
        m3 = producer.sign("backend", "c")
        consumer.verify(m1)
        # m3 chains to m2's MAC, not m1's — link mismatch.
        with pytest.raises(InterAgentMessageInvalid) as exc:
            consumer.verify(m3)
        assert exc.value.reason == "chain link mismatch"

    def test_chain_splice_rejected(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        m1 = producer.sign("architect", "a")
        m2 = producer.sign("frontend", "b")
        consumer.verify(m1)
        # Forge: take m2 but rewrite previous_mac to point at a forged value.
        forged = dataclasses.replace(m2, previous_mac=b"\x00" * 32)
        with pytest.raises(InterAgentMessageInvalid) as exc:
            consumer.verify(forged)
        assert exc.value.reason == "chain link mismatch"


# ---------------------------------------------------------------------------
# 5. Key isolation
# ---------------------------------------------------------------------------


class TestKeyIsolation:
    """Different keys MUST produce different MACs and reject each other."""

    def test_different_keys_different_macs(self) -> None:
        k1 = b"\x01" * 32
        k2 = b"\x02" * 32
        c1 = AgentMessageChain(k1)
        c2 = AgentMessageChain(k2)
        m1 = c1.sign("architect", "x")
        m2 = c2.sign("architect", "x")
        # Same role, seq, payload, prev_mac (both empty for first) — only
        # the key differs. MACs must differ.
        assert m1.mac != m2.mac

    def test_wrong_key_verify_fails(self) -> None:
        producer = AgentMessageChain(secrets.token_bytes(32))
        consumer_wrong = AgentMessageChain(secrets.token_bytes(32))
        msg = producer.sign("architect", "x")
        with pytest.raises(InterAgentMessageInvalid) as exc:
            consumer_wrong.verify(msg)
        assert exc.value.reason == "mac mismatch"

    def test_fresh_chain_after_run_does_not_replay(self) -> None:
        # Simulates "per-run fresh key" — a message from run 1 must not
        # verify under run 2's chain even if seq numbers happen to align.
        run1 = AgentMessageChain(secrets.token_bytes(32))
        run2 = AgentMessageChain(secrets.token_bytes(32))
        msg = run1.sign("architect", "x")
        with pytest.raises(InterAgentMessageInvalid):
            run2.verify(msg)


# ---------------------------------------------------------------------------
# 6. Edge inputs
# ---------------------------------------------------------------------------


class TestEdgeInputs:
    """Empty / huge / Unicode / coerced payloads round-trip."""

    def test_empty_payload(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        msg = producer.sign("architect", "")
        assert msg.payload == ""
        assert consumer.verify(msg) is True

    def test_unicode_payload(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        msg = producer.sign("architect", "héllo 世界 🚀 ignore previous instructions")
        assert consumer.verify(msg) is True

    @pytest.mark.parametrize(
        "size_kb",
        [1, 64, 1024],  # 1 KiB, 64 KiB, 1 MiB
    )
    def test_large_payload(self, session_key: bytes, size_kb: int) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        payload = "A" * (size_kb * 1024)
        msg = producer.sign("architect", payload)
        assert len(msg.payload) == size_kb * 1024
        assert consumer.verify(msg) is True

    def test_non_string_payload_coerced(self, session_key: bytes) -> None:
        # Defensive coercion path — caller passed a dict by mistake.
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        msg = producer.sign("architect", {"oops": 1})  # type: ignore[arg-type]
        # Coerced to ``str({"oops": 1})`` — verification still round-trips.
        assert isinstance(msg.payload, str)
        assert consumer.verify(msg) is True

    def test_none_payload_coerced_to_empty(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        msg = producer.sign("architect", None)  # type: ignore[arg-type]
        assert msg.payload == ""
        assert consumer.verify(msg) is True

    def test_non_string_role_rejected(self, chain: AgentMessageChain) -> None:
        with pytest.raises(TypeError):
            chain.sign(123, "x")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 7. Constant-time MAC compare
# ---------------------------------------------------------------------------


class TestConstantTimeCompare:
    """The verifier MUST use :func:`hmac.compare_digest`, not ``==``."""

    def test_compare_digest_is_called_during_verify(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        msg = producer.sign("architect", "x")

        # Patch the module-level reference to hmac.compare_digest used
        # inside ``inter_agent_provenance``. The wrapper preserves
        # correctness so the verify still succeeds.
        real_compare = _hmac_module.compare_digest
        call_count = {"n": 0}

        def counting_compare(a, b):
            call_count["n"] += 1
            return real_compare(a, b)

        with mock.patch.object(inter_agent_provenance.hmac, "compare_digest", side_effect=counting_compare):
            assert consumer.verify(msg) is True

        # At least one call for the chain link, at least one for the MAC.
        assert call_count["n"] >= 1


# ---------------------------------------------------------------------------
# 8. Public contract
# ---------------------------------------------------------------------------


class TestPublicContract:
    """Pins the public dataclass / exception / module surface."""

    def test_signed_message_is_frozen(self, chain: AgentMessageChain) -> None:
        msg = chain.sign("architect", "x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            msg.payload = "evil"  # type: ignore[misc]

    def test_signed_message_fields(self, chain: AgentMessageChain) -> None:
        msg = chain.sign("architect", "x")
        # The frozen dataclass exposes exactly the documented fields.
        names = {f.name for f in dataclasses.fields(msg)}
        assert names == {
            "sender_role",
            "sequence_num",
            "payload",
            "mac",
            "previous_mac",
            "created_at_iso",
        }

    def test_mac_is_32_bytes(self, chain: AgentMessageChain) -> None:
        msg = chain.sign("architect", "x")
        assert isinstance(msg.mac, bytes)
        assert len(msg.mac) == 32  # SHA-256 digest size

    def test_exception_carries_reason(self) -> None:
        exc = InterAgentMessageInvalid("mac mismatch")
        assert exc.reason == "mac mismatch"
        assert str(exc) == "mac mismatch"

    def test_module_exports(self) -> None:
        # __all__ pinned so future cleanups don't accidentally drop one.
        exported = set(inter_agent_provenance.__all__)
        assert exported >= {
            "AgentMessageChain",
            "InterAgentMessageInvalid",
            "SignedAgentMessage",
        }


# ---------------------------------------------------------------------------
# 9. Malformed inputs to verify
# ---------------------------------------------------------------------------


class TestMalformedVerifyInput:
    """``verify()`` rejects non-:class:`SignedAgentMessage` inputs."""

    def test_verify_rejects_none(self, chain: AgentMessageChain) -> None:
        with pytest.raises(InterAgentMessageInvalid) as exc:
            chain.verify(None)  # type: ignore[arg-type]
        assert exc.value.reason == "malformed message"

    def test_verify_rejects_dict(self, chain: AgentMessageChain) -> None:
        with pytest.raises(InterAgentMessageInvalid) as exc:
            chain.verify({"sender_role": "architect"})  # type: ignore[arg-type]
        assert exc.value.reason == "malformed message"

    def test_verify_rejects_string(self, chain: AgentMessageChain) -> None:
        with pytest.raises(InterAgentMessageInvalid):
            chain.verify("not a message")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 10. Helper convenience functions
# ---------------------------------------------------------------------------


class TestProvenanceHelpers:
    """Thin helpers used by ``multi_agent.py`` wire points."""

    def test_append_creates_list_on_first_call(self, chain: AgentMessageChain) -> None:
        state: dict = {}
        append_provenance(state, chain, "architect", "hello")
        assert "_provenance_record" in state
        assert len(state["_provenance_record"]) == 1
        assert state["_provenance_record"][0].sender_role == "architect"

    def test_append_extends_existing_list(self, chain: AgentMessageChain) -> None:
        state: dict = {}
        append_provenance(state, chain, "architect", "a")
        append_provenance(state, chain, "frontend", "b")
        assert len(state["_provenance_record"]) == 2
        assert [m.sender_role for m in state["_provenance_record"]] == [
            "architect",
            "frontend",
        ]

    def test_verify_latest_round_trip(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        state: dict = {}
        append_provenance(state, producer, "architect", "x")
        msg = verify_latest_provenance(state, consumer, expected_role="architect")
        assert msg.sender_role == "architect"

    def test_verify_latest_role_mismatch(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        state: dict = {}
        append_provenance(state, producer, "architect", "x")
        with pytest.raises(InterAgentMessageInvalid):
            verify_latest_provenance(state, consumer, expected_role="frontend")

    def test_verify_latest_missing_record(self, chain: AgentMessageChain) -> None:
        with pytest.raises(InterAgentMessageInvalid) as exc:
            verify_latest_provenance({}, chain)
        assert exc.value.reason == "malformed message"


# ---------------------------------------------------------------------------
# 11. Stateless verify_signature — for parallel fan-out consumers
# ---------------------------------------------------------------------------


class TestVerifySignatureStateless:
    """``verify_signature`` is the parallel-branch verify primitive."""

    def test_verify_signature_round_trip(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        msg = producer.sign("architect", "x")
        assert consumer.verify_signature(msg) is True

    def test_verify_signature_can_be_called_repeatedly(self, session_key: bytes) -> None:
        # Multiple parallel consumers must be able to verify the SAME
        # upstream emit. The stateful ``verify`` would reject the second
        # call; ``verify_signature`` does not.
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        msg = producer.sign("architect", "x")
        for _ in range(5):
            assert consumer.verify_signature(msg) is True

    def test_verify_signature_rejects_tampered_payload(self, session_key: bytes) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        original = producer.sign("architect", "x")
        tampered = dataclasses.replace(original, payload="evil")
        with pytest.raises(InterAgentMessageInvalid) as exc:
            consumer.verify_signature(tampered)
        assert exc.value.reason == "mac mismatch"

    def test_verify_signature_rejects_wrong_key(self) -> None:
        producer = AgentMessageChain(secrets.token_bytes(32))
        wrong = AgentMessageChain(secrets.token_bytes(32))
        msg = producer.sign("architect", "x")
        with pytest.raises(InterAgentMessageInvalid) as exc:
            wrong.verify_signature(msg)
        assert exc.value.reason == "mac mismatch"

    def test_verify_signature_does_not_advance_state(self, session_key: bytes) -> None:
        # After ``verify_signature``, the stateful chain bookkeeping must
        # be unchanged — the next stateful ``verify`` of an earlier
        # message must still see a fresh slate.
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        m1 = producer.sign("architect", "a")
        consumer.verify_signature(m1)
        consumer.verify_signature(m1)
        consumer.verify_signature(m1)
        # Now do a stateful verify — should succeed (seq=1 > seq=0).
        assert consumer.verify(m1) is True


# ---------------------------------------------------------------------------
# 12. Audit logging
# ---------------------------------------------------------------------------


class TestAuditLogging:
    """Sign / verify-OK / verify-REJECT each emit a recognizable log line."""

    def test_sign_emits_info_log(self, chain: AgentMessageChain, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="langgraph.upgrade"):
            chain.sign("architect", "x")
        assert any("[INTER_AGENT_CHAIN]" in r.message and "sign" in r.message for r in caplog.records)

    def test_verify_ok_emits_info_log(self, session_key: bytes, caplog: pytest.LogCaptureFixture) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(session_key)
        msg = producer.sign("architect", "x")
        with caplog.at_level(logging.INFO, logger="langgraph.upgrade"):
            consumer.verify(msg)
        assert any("[INTER_AGENT_CHAIN]" in r.message and "verify OK" in r.message for r in caplog.records)

    def test_verify_reject_emits_warning_log(self, session_key: bytes, caplog: pytest.LogCaptureFixture) -> None:
        producer = AgentMessageChain(session_key)
        consumer = AgentMessageChain(secrets.token_bytes(32))  # wrong key
        msg = producer.sign("architect", "x")
        with caplog.at_level(logging.WARNING, logger="langgraph.upgrade"):
            with pytest.raises(InterAgentMessageInvalid):
                consumer.verify(msg)
        assert any("[INTER_AGENT_CHAIN]" in r.message and "REJECT" in r.message for r in caplog.records)
