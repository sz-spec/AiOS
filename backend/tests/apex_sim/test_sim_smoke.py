"""
backend/tests/apex_sim/test_sim_smoke.py — sanity tests of the sim itself.

If these fail, the harness is broken; the broader 40 Stage-10 tests that
depend on it cannot be trusted to be exercising real logic.
"""

from __future__ import annotations

from tests.apex_sim import ApexSimDriver


def test_ping():
    drv = ApexSimDriver()
    assert drv.send_command("PING") == "PONG"


def test_policy_override_roundtrip():
    drv = ApexSimDriver()
    assert drv.send_command("POLICY_OVERRIDE|0|750") == "POLICY_OK|slot=0|gate=750"
    status = drv.send_command("POLICY_STATUS")
    assert "force_permit=0" in status
    assert "0:750" in status


def test_force_permit_toggle():
    drv = ApexSimDriver()
    drv.send_command("POLICY_OVERRIDE|0|900")
    # Score below threshold but force_permit ON → CONF_OK
    drv.send_command("POLICY_FORCE_PERMIT|1")
    reply = drv.send_command("ACTION_CHECK_CONFIDENCE|0|100")
    assert reply.startswith("CONF_OK|") and "forced=1" in reply

    # force_permit OFF → BLOCK
    drv.send_command("POLICY_FORCE_PERMIT|0")
    reply2 = drv.send_command("ACTION_CHECK_CONFIDENCE|0|100")
    assert reply2.startswith("ERR 13 CONF_BLOCK|")


def test_audit_ring_inject_and_drain():
    drv = ApexSimDriver()
    seq1 = drv.inject_audit_event(category=0x10, rc=2, slot_id=0, digest="ab" * 8)
    seq2 = drv.inject_audit_event(category=0x10, rc=3, slot_id=1, digest="cd" * 8)
    assert seq1 == 1 and seq2 == 2

    reply = drv.send_command("AUDIT_FAIL_QUOTE")
    assert reply.startswith("AUDIT_FAIL|total=2|fill=2")
    assert "1:16:2:0:" in reply  # category 0x10 == 16 decimal
    assert "2:16:3:1:" in reply

    # Drain semantics: ring now empty
    reply2 = drv.send_command("AUDIT_FAIL_QUOTE")
    assert "fill=0" in reply2


def test_intent_submit_digest_shape():
    drv = ApexSimDriver()
    reply = drv.send_command("INTENT_SUBMIT|" + "ab" * 16)
    head, _, digest = reply.partition("|")
    assert head == "INTENT_OK"
    assert len(digest) == 96  # SHA-384 hex


def test_intent_submit_bound_when_slot_arg_present():
    drv = ApexSimDriver()
    reply = drv.send_command("INTENT_SUBMIT|" + "00" * 8 + "|2")
    assert reply.startswith("INTENT_BOUND|")


def test_tpm_seal_unseal_roundtrip():
    drv = ApexSimDriver()
    payload_hex = "deadbeef" * 8
    sealed = drv.send_command(f"TPM_SEAL|{payload_hex}")
    assert sealed.startswith("TPM_SEALED|")
    wrapped_hex = sealed.split("|", 1)[1]
    unsealed = drv.send_command(f"TPM_UNSEAL|{wrapped_hex}")
    assert unsealed == f"TPM_UNSEALED|{payload_hex}"


def test_unknown_command():
    drv = ApexSimDriver()
    reply = drv.send_command("NOPE_DOESNT_EXIST")
    assert reply.startswith("ERR 1 UNKNOWN_CMD")


def test_ring_buffer_integration(tmp_path, monkeypatch):
    """Wire the sim driver through the userspace ring buffer + store."""
    from services.compliance_store import ComplianceStore
    from services.vbus_ring_buffer import VBusRingBuffer

    db_path = str(tmp_path / "compliance.db")
    monkeypatch.setenv("VOS3_COMPLIANCE_DB_PATH", db_path)
    store = ComplianceStore(db_path=db_path)

    drv = ApexSimDriver()
    for i in range(5):
        drv.inject_audit_event(category=0x10, rc=i, slot_id=i % 4, digest=f"{i:016x}")

    ring = VBusRingBuffer(drv, store)
    result = ring.drain_once()
    assert result["new"] == 5
    assert result["fill"] == 5
    assert result["total"] == 5
    assert result["highest_seq_seen"] == 5

    # Second drain — nothing new
    result2 = ring.drain_once()
    assert result2["new"] == 0
    assert result2["fill"] == 0
