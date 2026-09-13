"""
backend/tests/governance/test_telemetry_genai.py

Sprint 15 / Items G1 + G5 — OpenTelemetry GenAI emitter + PII redactor tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load services/telemetry_genai.py via explicit file path so it doesn't
# collide with any module aliasing in other tests.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TELEM_PATH = _REPO_ROOT / "backend" / "services" / "telemetry_genai.py"
_spec = importlib.util.spec_from_file_location(
    "vos3_telemetry_genai_under_test", _TELEM_PATH
)
tg = importlib.util.module_from_spec(_spec)
sys.modules["vos3_telemetry_genai_under_test"] = tg
_spec.loader.exec_module(tg)


# ---------------------------------------------------------------------------
# Redactor — each pattern class has a positive + negative test
# ---------------------------------------------------------------------------


def test_redact_email():
    r = tg.redact_pii("contact me at jane.doe@example.com please")
    assert "[REDACTED:EMAIL]" in r.redacted
    assert "jane.doe@example.com" not in r.redacted
    assert r.counts.get("EMAIL") == 1


def test_redact_openai_key():
    r = tg.redact_pii("OPENAI_API_KEY=sk-abcdefghijklmnop1234567890XYZ in env")
    assert "[REDACTED:SK_OPENAI]" in r.redacted
    assert "sk-abcdef" not in r.redacted


def test_redact_github_pat():
    r = tg.redact_pii(
        "git clone https://ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA@github.com/x/y"
    )
    assert "[REDACTED:PAT_GITHUB]" in r.redacted


def test_redact_aws_access_key():
    r = tg.redact_pii("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")
    assert "[REDACTED:AWS_KEY]" in r.redacted


def test_redact_jwt_token():
    fake_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjMifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    r = tg.redact_pii(f"Authorization: {fake_jwt}")
    assert "[REDACTED:JWT_TOKEN]" in r.redacted


def test_redact_bearer_header_preserves_prefix():
    r = tg.redact_pii("Authorization: Bearer abc123def456ghi789jkl")
    # The "Bearer " prefix is preserved so the trace still shows the
    # header was present; only the secret is redacted.
    assert "Bearer [REDACTED:BEARER_HEADER]" in r.redacted


def test_redact_us_ssn():
    r = tg.redact_pii("SSN 123-45-6789 on file")
    assert "[REDACTED:US_SSN]" in r.redacted


def test_redact_credit_card_shape():
    r = tg.redact_pii("card 4111111111111111 charged $20")
    assert "[REDACTED:CC_NUMBER]" in r.redacted


def test_redact_us_phone():
    r = tg.redact_pii("call (555) 123-4567 between 9 and 5")
    assert "[REDACTED:US_PHONE]" in r.redacted


def test_redact_ipv4():
    r = tg.redact_pii("remote=10.0.0.42 client")
    assert "[REDACTED:IPV4]" in r.redacted


def test_redact_keep_kinds_env_var_skips_class(monkeypatch):
    """Operator can opt OUT of redacting specific classes via env var
    (typically IPV4 for forensic correlation)."""
    monkeypatch.setenv("VOS3_PII_REDACTION_KEEP_KINDS", "IPV4,US_PHONE")
    r = tg.redact_pii("remote=10.0.0.42 phone 555-123-4567")
    # Both kept — should appear literally.
    assert "10.0.0.42" in r.redacted
    assert "555-123-4567" in r.redacted
    # Email still redacted (not in keep list).
    r2 = tg.redact_pii("email me@x.com from 10.0.0.42")
    assert "[REDACTED:EMAIL]" in r2.redacted
    assert "10.0.0.42" in r2.redacted


def test_redact_no_match_returns_unchanged():
    r = tg.redact_pii("the quick brown fox jumps over the lazy dog")
    assert r.redacted == "the quick brown fox jumps over the lazy dog"
    assert r.counts == {}


def test_redact_empty_string():
    r = tg.redact_pii("")
    assert r.redacted == ""
    assert r.counts == {}


def test_redact_non_string_input_safe():
    r = tg.redact_pii(None)
    assert r.redacted == ""


def test_redact_mode_count_does_not_substitute():
    r = tg.redact_pii("contact me@example.com today", mode="count")
    assert "me@example.com" in r.redacted
    assert r.counts.get("EMAIL") == 1


def test_redact_span_recurses_into_nested():
    raw = {
        "user": "jane.doe@example.com",
        "tool_args": {
            "prompt": "lookup SSN 123-45-6789",
            "context": ["call (555) 123-4567"],
        },
        "tokens_used": 42,
    }
    out = tg.redact_span(raw)
    assert "[REDACTED:EMAIL]" in out["user"]
    assert "[REDACTED:US_SSN]" in out["tool_args"]["prompt"]
    assert "[REDACTED:US_PHONE]" in out["tool_args"]["context"][0]
    assert out["tokens_used"] == 42


# ---------------------------------------------------------------------------
# Span emit
# ---------------------------------------------------------------------------


def _force_otel_unavailable(monkeypatch):
    """Make the OTel SDK import inside telemetry_genai.py fail, forcing
    the structured-log fallback path."""
    monkeypatch.setattr(tg, "_otel_emit_via_sdk", lambda *args, **kwargs: False)


def test_emit_agent_span_via_structured_log(caplog, monkeypatch):
    """When OTel SDK isn't reachable, we fall back to a JSON log line."""
    _force_otel_unavailable(monkeypatch)
    caplog.set_level("INFO", logger=tg.logger.name)
    span = tg.GenAISpanData(
        operation_name="chat",
        agent_name="frontend-agent",
        agent_id="agent-001",
        duration_ms=12.5,
        attributes={
            tg.ATTR_REQUEST_MODEL: "claude-sonnet-4-6",
            tg.ATTR_USAGE_INPUT_TOKENS: 1024,
            tg.ATTR_USAGE_OUTPUT_TOKENS: 256,
        },
        slot_id=2,
        intent_digest="abcdef0123456789",
    )
    tg.emit_agent_span(span)

    log_text = "\n".join(rec.message for rec in caplog.records)
    assert "gen_ai.agent.chat" in log_text


def test_emit_tool_span_via_structured_log(caplog, monkeypatch):
    _force_otel_unavailable(monkeypatch)
    caplog.set_level("INFO", logger=tg.logger.name)
    span = tg.GenAISpanData(
        operation_name="tool",
        agent_name="codegen-agent",
        agent_id="agent-002",
        duration_ms=33.0,
        attributes={
            tg.ATTR_TOOL_NAME: "read_kernel_file",
            tg.ATTR_TOOL_ARGUMENTS: '{"path": "/etc/secret"}',
        },
    )
    tg.emit_tool_span(span)
    log_text = "\n".join(rec.message for rec in caplog.records)
    assert "gen_ai.tool.execute" in log_text


def test_emit_via_otel_sdk_path(monkeypatch):
    """When OTel SDK IS available, the emit path returns through it
    without hitting the structured-log fallback."""
    called = {"sdk": 0, "log": 0}

    def _fake_sdk(name, attrs, duration_ms):
        called["sdk"] += 1
        return True  # claim success — fallback should NOT fire

    def _fake_log(name, attrs, duration_ms):
        called["log"] += 1

    monkeypatch.setattr(tg, "_otel_emit_via_sdk", _fake_sdk)
    monkeypatch.setattr(tg, "_structured_log_emit", _fake_log)

    span = tg.GenAISpanData(
        operation_name="chat",
        agent_name="frontend-agent",
        agent_id="agent-001",
        duration_ms=12.5,
    )
    tg.emit_agent_span(span)

    assert called["sdk"] == 1
    assert called["log"] == 0


# ---------------------------------------------------------------------------
# MAIF audit wrapping (Item G5)
# ---------------------------------------------------------------------------


def test_wrap_for_audit_redacts_string_values():
    raw_attrs = {
        tg.ATTR_AGENT_NAME: "frontend-agent",
        tg.ATTR_TOOL_ARGUMENTS: "user_email=jane@example.com api_key=sk-abc1234567890def1234567890ghi",
        tg.ATTR_USAGE_INPUT_TOKENS: 100,
    }
    blob = tg.wrap_for_audit("gen_ai.tool.execute", raw_attrs)
    assert isinstance(blob, tg.MAIFRedactedBlob)
    assert "[REDACTED:EMAIL]" in blob.redacted_attrs[tg.ATTR_TOOL_ARGUMENTS]
    assert "[REDACTED:SK_OPENAI]" in blob.redacted_attrs[tg.ATTR_TOOL_ARGUMENTS]
    # Non-string attrs pass through unchanged.
    assert blob.redacted_attrs[tg.ATTR_USAGE_INPUT_TOKENS] == 100
    # Envelope SHA-256 is 64 hex chars.
    assert len(blob.envelope_sha256) == 64
    # Counts populated.
    assert blob.redaction_counts.get("EMAIL", 0) >= 1
    assert blob.redaction_counts.get("SK_OPENAI", 0) >= 1


def test_wrap_for_audit_envelope_digest_is_pre_redaction():
    """The envelope_sha256 must hash the ORIGINAL attrs, not the redacted
    version — otherwise an auditor can't cross-reference back to the
    unredacted source."""
    raw_a = {"prompt": "my email is jane@example.com"}
    raw_b = {"prompt": "my email is [REDACTED:EMAIL]"}
    blob_a = tg.wrap_for_audit("test", raw_a)
    blob_b = tg.wrap_for_audit("test", raw_b)
    # Different originals → different envelope digests.
    assert blob_a.envelope_sha256 != blob_b.envelope_sha256


def test_wrap_for_audit_handles_nested_dict():
    raw = {
        "tool": {
            "name": "fetch_url",
            "args": "https://api.example.com?token=Bearer abc123def456ghi789jkl",
        },
    }
    blob = tg.wrap_for_audit("gen_ai.tool.execute", raw)
    nested = blob.redacted_attrs["tool"]
    assert "[REDACTED:BEARER_HEADER]" in nested["args"]
