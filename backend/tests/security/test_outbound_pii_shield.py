"""
Tests for O3 — Context-Aware Outbound-PII Shield
(backend/security/outbound_pii_shield.py).

The shield is a fail-closed, context-aware PII egress gate: the SAME
payload is allowed to a trusted destination or with an explicit
kind-scoped release, but refused to an arbitrary destination. These
tests pin that behaviour as the contract for the honest 40/80 closure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from security.outbound_pii_shield import (  # noqa: E402
    EgressMode,
    OutboundPiiBlocked,
    OutboundPiiShield,
    PiiKind,
    ReleaseContext,
)

EXTERNAL = "https://hooks.partner-saas.example/ingest"
CLEAN = "build complete; 0 warnings; deploy id 8f3a2c"
EMAIL_PAYLOAD = "user record: alice@aidg.com placed an order"
SSN_PAYLOAD = "applicant ssn=123-45-6789 pending review"
CARD_PAYLOAD = "charge token 4242424242424242 settled"


# --------------------------------------------------------------------------
# Clean payloads always pass.
# --------------------------------------------------------------------------


def test_clean_payload_allowed_even_to_external():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    d = shield.inspect(CLEAN, destination=EXTERNAL)
    assert d.allowed is True
    assert d.has_pii is False
    assert d.findings == ()


def test_clean_payload_require_clean_egress_does_not_raise():
    shield = OutboundPiiShield()
    d = shield.require_clean_egress(CLEAN, destination=EXTERNAL)
    assert d.allowed is True


# --------------------------------------------------------------------------
# PII to an untrusted destination is fail-closed.
# --------------------------------------------------------------------------


def test_email_to_untrusted_is_blocked():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    d = shield.inspect(EMAIL_PAYLOAD, destination=EXTERNAL)
    assert d.allowed is False
    assert PiiKind.EMAIL in d.kinds


def test_require_clean_egress_raises_on_untrusted_pii():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    with pytest.raises(OutboundPiiBlocked) as ei:
        shield.require_clean_egress(SSN_PAYLOAD, destination=EXTERNAL)
    assert ei.value.decision.allowed is False
    assert PiiKind.SSN in ei.value.decision.kinds


def test_credit_card_detected_via_luhn():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    d = shield.inspect(CARD_PAYLOAD, destination=EXTERNAL)
    assert d.allowed is False
    assert PiiKind.CREDIT_CARD in d.kinds


def test_non_luhn_long_number_not_flagged_as_card():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    d = shield.inspect("trace id 1234567890123456 logged", destination=EXTERNAL)
    # 1234567890123456 fails Luhn -> not a card -> clean.
    assert PiiKind.CREDIT_CARD not in d.kinds


# --------------------------------------------------------------------------
# Context-aware ALLOW paths — same payload, permitted.
# --------------------------------------------------------------------------


def test_pii_to_trusted_destination_allowed():
    shield = OutboundPiiShield(trusted_destinations=frozenset({"sink.internal"}))
    d = shield.inspect(EMAIL_PAYLOAD, destination="https://sink.internal/log")
    assert d.allowed is True
    assert d.trusted_destination is True
    assert d.has_pii is True  # PII present but permitted by trust


def test_trusted_suffix_match():
    shield = OutboundPiiShield(trusted_destinations=frozenset({".internal"}))
    d = shield.inspect(EMAIL_PAYLOAD, destination="https://a.b.internal/x")
    assert d.allowed is True
    assert d.trusted_destination is True


def test_release_context_clears_matching_kind():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    ctx = ReleaseContext(
        reviewed=True,
        cleared_kinds=frozenset({PiiKind.EMAIL}),
        justification="DPA-signed marketing export",
        reviewer="dpo@aidg.com",
    )
    d = shield.require_clean_egress(EMAIL_PAYLOAD, destination=EXTERNAL, context=ctx)
    assert d.allowed is True
    assert d.released_by_context is True


def test_release_context_does_not_clear_other_kinds():
    # Cleared EMAIL, but payload has an SSN -> still fail-closed.
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    ctx = ReleaseContext(reviewed=True, cleared_kinds=frozenset({PiiKind.EMAIL}))
    mixed = EMAIL_PAYLOAD + " ; ssn=123-45-6789"
    with pytest.raises(OutboundPiiBlocked):
        shield.require_clean_egress(mixed, destination=EXTERNAL, context=ctx)


def test_unreviewed_context_does_not_clear():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    ctx = ReleaseContext(reviewed=False, cleared_kinds=frozenset({PiiKind.EMAIL}))
    d = shield.inspect(EMAIL_PAYLOAD, destination=EXTERNAL, context=ctx)
    assert d.allowed is False


# --------------------------------------------------------------------------
# Modes.
# --------------------------------------------------------------------------


def test_redact_mode_masks_pii_and_allows():
    shield = OutboundPiiShield(
        trusted_destinations=frozenset(), default_mode=EgressMode.REDACT
    )
    d = shield.inspect(EMAIL_PAYLOAD, destination=EXTERNAL)
    assert d.allowed is True
    assert d.redacted_payload is not None
    assert "alice@aidg.com" not in d.redacted_payload
    assert "[REDACTED:email]" in d.redacted_payload
    assert d.effective_payload(EMAIL_PAYLOAD) == d.redacted_payload


def test_audit_mode_allows_but_flags():
    shield = OutboundPiiShield(
        trusted_destinations=frozenset(), default_mode=EgressMode.AUDIT
    )
    d = shield.inspect(SSN_PAYLOAD, destination=EXTERNAL)
    assert d.allowed is True
    assert d.has_pii is True


def test_block_is_default_mode():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    assert shield.default_mode == EgressMode.BLOCK


# --------------------------------------------------------------------------
# Allowlist + bytes + env config.
# --------------------------------------------------------------------------


def test_allowlisted_value_not_flagged():
    shield = OutboundPiiShield(
        trusted_destinations=frozenset(),
        allowlist=frozenset({"alice@aidg.com"}),
    )
    d = shield.inspect(EMAIL_PAYLOAD, destination=EXTERNAL)
    assert d.allowed is True
    assert d.has_pii is False


def test_example_com_email_is_skipped():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    d = shield.inspect("contact test@example.com please", destination=EXTERNAL)
    assert d.has_pii is False


def test_bytes_payload_supported():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    d = shield.inspect(EMAIL_PAYLOAD.encode(), destination=EXTERNAL)
    assert d.allowed is False
    assert PiiKind.EMAIL in d.kinds


def test_dev_override_allows_with_flag(monkeypatch):
    monkeypatch.setenv("VOS3_PII_SHIELD_DEV_OVERRIDE", "1")
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    d = shield.inspect(SSN_PAYLOAD, destination=EXTERNAL)
    assert d.allowed is True
    assert d.dev_override_used is True


def test_trusted_destinations_loaded_from_env(monkeypatch):
    monkeypatch.setenv("VOS3_PII_TRUSTED_DESTINATIONS", "sink.internal, .corp.example")
    shield = OutboundPiiShield()
    d = shield.inspect(EMAIL_PAYLOAD, destination="https://x.corp.example/y")
    assert d.allowed is True
    assert d.trusted_destination is True


def test_stats_accumulate():
    shield = OutboundPiiShield(trusted_destinations=frozenset())
    shield.inspect(CLEAN, destination=EXTERNAL)
    shield.inspect(EMAIL_PAYLOAD, destination=EXTERNAL)
    assert shield.stats.inspections == 2
    assert shield.stats.clean == 1
    assert shield.stats.blocked == 1
