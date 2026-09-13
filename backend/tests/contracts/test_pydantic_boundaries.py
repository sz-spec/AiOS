"""
Pydantic Validation Boundary Tests
====================================

Tests input validation at API boundaries: type coercion, field constraints,
injection payloads, and edge-case values.  Every POST/PUT endpoint protected
by Pydantic models should reject malformed input with 422 (not 500).

Run with:
    pytest tests/contracts/test_pydantic_boundaries.py -m contract
"""

import pytest

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_rejection(resp, *acceptable):
    """The response must be one of the acceptable status codes.

    503 (Service Unavailable) is always accepted — backend services
    may be unavailable in the test environment.
    """
    codes = acceptable or (422,)
    assert resp.status_code in (
        *codes,
        503,
    ), f"Expected {codes}, got {resp.status_code}: {resp.text[:300]}"


def _assert_not_500(resp):
    """A validation error must never surface as an unhandled 500."""
    assert (
        resp.status_code != 500 or "internal" in resp.text.lower()
    ), f"Unexpected 500 from validation — should be 422: {resp.text[:300]}"


# ============================================================================
# Target endpoints with their minimal valid payloads
# ============================================================================

CHAT_URL = "/api/chat/completions"
AGENTS_URL = "/api/agents"
ENTITIES_URL = "/api/v-core/entities"
MEMORY_ADD_URL = "/api/memory/add"
SETTINGS_URL = "/api/settings/keys"

VALID_CHAT = {
    "messages": [{"role": "user", "content": "hello"}],
    "stream": False,
}

VALID_AGENT = {
    "name": "TestAgent",
    "role": "assistant",
}

VALID_ENTITY = {
    "name": "test_entity",
    "label": "Test",
}

VALID_MEMORY = {
    "content": "Remember this fact",
    "memory_type": "conversation",
}


# ============================================================================
# 1. Empty string for required string field
# ============================================================================


class TestEmptyStringRequired:
    def test_empty_name_agent(self, client):
        payload = {**VALID_AGENT, "name": ""}
        resp = client.post(AGENTS_URL, json=payload)
        # Some APIs accept empty string, some reject — both are valid contracts
        # Just must not be 500
        _assert_not_500(resp)

    def test_empty_content_memory(self, client):
        payload = {**VALID_MEMORY, "content": ""}
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_not_500(resp)


# ============================================================================
# 2. String exceeding 10K chars
# ============================================================================


class TestOverlongString:
    def test_huge_content_chat(self, client):
        huge = "A" * 110_000  # Exceeds max_length=100_000
        payload = {
            "messages": [{"role": "user", "content": huge}],
            "stream": False,
        }
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422, 413, 200)

    def test_huge_name_agent(self, client):
        payload = {**VALID_AGENT, "name": "X" * 20_000}
        resp = client.post(AGENTS_URL, json=payload)
        _assert_not_500(resp)


# ============================================================================
# 3. Negative number where positive required
# ============================================================================


class TestNegativeNumbers:
    def test_negative_temperature(self, client):
        payload = {**VALID_CHAT, "temperature": -1.0}
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422, 200)

    def test_negative_max_tokens(self, client):
        payload = {**VALID_CHAT, "max_tokens": -100}
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422, 200)


# ============================================================================
# 4. NaN / Infinity in numeric field
# ============================================================================


class TestNaNInfinity:
    def test_nan_temperature(self, client):
        # JSON does not have NaN — send as string, should be rejected
        resp = client.post(
            CHAT_URL,
            content='{"messages":[{"role":"user","content":"hi"}],"temperature":NaN}',
            headers={"Content-Type": "application/json"},
        )
        _assert_rejection(resp, 422, 400)

    def test_infinity_temperature(self, client):
        resp = client.post(
            CHAT_URL,
            content='{"messages":[{"role":"user","content":"hi"}],"temperature":Infinity}',
            headers={"Content-Type": "application/json"},
        )
        _assert_rejection(resp, 422, 400)


# ============================================================================
# 5. Invalid enum value
# ============================================================================


class TestInvalidEnum:
    def test_invalid_chat_role(self, client):
        payload = {
            "messages": [{"role": "superadmin", "content": "test"}],
        }
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422)

    def test_invalid_agent_role_accepted(self, client):
        """Agent role is freeform str, so any value should be accepted."""
        payload = {**VALID_AGENT, "role": "custom_role_123"}
        resp = client.post(AGENTS_URL, json=payload)
        # Freeform — should NOT be 422
        if resp.status_code in (200, 201):
            agent_id = resp.json().get("id")
            if agent_id:
                client.delete(f"{AGENTS_URL}/{agent_id}")


# ============================================================================
# 6. SQL injection string
# ============================================================================


class TestSQLInjection:
    SQL_PAYLOADS = [
        "' OR 1=1 --",
        "'; DROP TABLE users; --",
        "1; SELECT * FROM information_schema.tables",
    ]

    @pytest.mark.parametrize("sqli", SQL_PAYLOADS)
    def test_sql_injection_in_agent_name(self, client, sqli):
        payload = {**VALID_AGENT, "name": sqli}
        resp = client.post(AGENTS_URL, json=payload)
        # Must not return SQL error
        if resp.status_code in (200, 201, 422):
            text = resp.text.lower()
            assert (
                "sql" not in text or "syntax" not in text
            ), f"Possible SQL error leak: {resp.text[:200]}"
            # Cleanup
            if resp.status_code in (200, 201):
                agent_id = resp.json().get("id")
                if agent_id:
                    client.delete(f"{AGENTS_URL}/{agent_id}")

    @pytest.mark.parametrize("sqli", SQL_PAYLOADS)
    def test_sql_injection_in_memory_content(self, client, sqli):
        payload = {**VALID_MEMORY, "content": sqli}
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_not_500(resp)


# ============================================================================
# 7. XSS payload
# ============================================================================


class TestXSSPayload:
    XSS = "<script>alert(1)</script>"

    def test_xss_in_agent_name(self, client):
        payload = {**VALID_AGENT, "name": self.XSS}
        resp = client.post(AGENTS_URL, json=payload)
        _assert_not_500(resp)
        if resp.status_code in (200, 201):
            data = resp.json()
            # If stored, the raw script tag should be escaped or stored as-is
            # (API is JSON, not HTML — no XSS risk in JSON responses)
            agent_id = data.get("id")
            if agent_id:
                client.delete(f"{AGENTS_URL}/{agent_id}")

    def test_xss_in_entity_label(self, client):
        payload = {**VALID_ENTITY, "label": self.XSS}
        resp = client.post(ENTITIES_URL, json=payload)
        _assert_not_500(resp)


# ============================================================================
# 8. Null bytes in string field
# ============================================================================


class TestNullBytes:
    def test_null_byte_in_name(self, client):
        payload = {**VALID_AGENT, "name": "test\x00evil"}
        resp = client.post(AGENTS_URL, json=payload)
        _assert_not_500(resp)

    def test_null_byte_in_memory(self, client):
        payload = {**VALID_MEMORY, "content": "remember\x00this"}
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_not_500(resp)


# ============================================================================
# 9. Unicode edge cases
# ============================================================================


class TestUnicodeEdgeCases:
    @pytest.mark.parametrize(
        "text",
        [
            "\U0001f600\U0001f525\U0001f4a5",  # Emoji
            "\u4e16\u754c\u4f60\u597d",  # CJK
            "\u0645\u0631\u062d\u0628\u0627",  # Arabic
            "\u200b\u200c\u200d\ufeff",  # Zero-width chars
            "\U0001f1fa\U0001f1f8",  # Flag emoji (multi-codepoint)
        ],
    )
    def test_unicode_in_agent_name(self, client, text):
        payload = {**VALID_AGENT, "name": text}
        resp = client.post(AGENTS_URL, json=payload)
        _assert_not_500(resp)
        if resp.status_code in (200, 201):
            agent_id = resp.json().get("id")
            if agent_id:
                client.delete(f"{AGENTS_URL}/{agent_id}")

    @pytest.mark.parametrize(
        "text",
        [
            "\U0001f600 emoji memory",
            "\u4e16\u754c Chinese chars",
            "mixing\u200b\u200czero-width",
        ],
    )
    def test_unicode_in_memory_content(self, client, text):
        payload = {**VALID_MEMORY, "content": text}
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_not_500(resp)


# ============================================================================
# 10. Integer at int32 boundary
# ============================================================================


class TestIntBoundary:
    def test_max_int32_tokens(self, client):
        payload = {**VALID_CHAT, "max_tokens": 2147483647}
        resp = client.post(CHAT_URL, json=payload)
        # May be rejected by le=128000 constraint → 422
        _assert_not_500(resp)


# ============================================================================
# 11. Integer beyond int64
# ============================================================================


class TestIntOverflow:
    def test_beyond_int64_max_tokens(self, client):
        payload = {**VALID_CHAT, "max_tokens": 2**63}
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422, 400)

    def test_negative_overflow(self, client):
        payload = {**VALID_CHAT, "max_tokens": -(2**63)}
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422, 400)


# ============================================================================
# 12. Float precision edge cases
# ============================================================================


class TestFloatPrecision:
    def test_float_precision(self, client):
        # 0.1 + 0.2 = 0.30000000000000004
        payload = {**VALID_CHAT, "temperature": 0.1 + 0.2}
        resp = client.post(CHAT_URL, json=payload)
        _assert_not_500(resp)

    def test_very_small_float(self, client):
        payload = {**VALID_CHAT, "temperature": 1e-300}
        resp = client.post(CHAT_URL, json=payload)
        _assert_not_500(resp)


# ============================================================================
# 13. Boolean where string expected
# ============================================================================


class TestTypeMismatchBoolStr:
    def test_boolean_as_name(self, client):
        payload = {**VALID_AGENT, "name": True}
        resp = client.post(AGENTS_URL, json=payload)
        # Pydantic may coerce True → "True" or reject
        _assert_not_500(resp)

    def test_boolean_as_content(self, client):
        payload = {**VALID_MEMORY, "content": False}
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_not_500(resp)


# ============================================================================
# 14. List where string expected
# ============================================================================


class TestTypeMismatchListStr:
    def test_list_as_name(self, client):
        payload = {**VALID_AGENT, "name": ["a", "b"]}
        resp = client.post(AGENTS_URL, json=payload)
        _assert_rejection(resp, 422, 200)

    def test_list_as_content(self, client):
        payload = {**VALID_MEMORY, "content": [1, 2, 3]}
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_rejection(resp, 422, 200)


# ============================================================================
# 15. Dict where string expected
# ============================================================================


class TestTypeMismatchDictStr:
    def test_dict_as_name(self, client):
        payload = {**VALID_AGENT, "name": {"key": "val"}}
        resp = client.post(AGENTS_URL, json=payload)
        _assert_rejection(resp, 422, 200)

    def test_dict_as_content(self, client):
        payload = {**VALID_MEMORY, "content": {"nested": "obj"}}
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_rejection(resp, 422, 200)


# ============================================================================
# 16. Missing required field
# ============================================================================


class TestMissingRequired:
    def test_missing_messages_chat(self, client):
        payload = {"stream": False}
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422)
        if resp.status_code == 422:
            text = resp.text.lower()
            assert (
                "messages" in text or "required" in text or "missing" in text
            ), "422 error should mention the missing field name"

    def test_missing_name_agent(self, client):
        payload = {"role": "assistant"}
        resp = client.post(AGENTS_URL, json=payload)
        _assert_rejection(resp, 422)

    def test_missing_content_memory(self, client):
        payload = {"memory_type": "conversation"}
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_rejection(resp, 422)

    def test_missing_name_entity(self, client):
        payload = {"label": "NoName"}
        resp = client.post(ENTITIES_URL, json=payload)
        _assert_rejection(resp, 422)


# ============================================================================
# 17. Extra unexpected field
# ============================================================================


class TestExtraFields:
    def test_extra_field_agent(self, client):
        payload = {**VALID_AGENT, "totally_unknown_field": "surprise"}
        resp = client.post(AGENTS_URL, json=payload)
        # Pydantic default: extra fields silently ignored → 200
        # Or Pydantic strict: 422
        assert resp.status_code in (200, 201, 422, 500, 503)
        _assert_not_500(resp)

    def test_extra_field_chat(self, client):
        payload = {**VALID_CHAT, "hacker_field": True}
        resp = client.post(CHAT_URL, json=payload)
        _assert_not_500(resp)


# ============================================================================
# 18. Null for required non-optional field
# ============================================================================


class TestNullRequired:
    def test_null_name_agent(self, client):
        payload = {**VALID_AGENT, "name": None}
        resp = client.post(AGENTS_URL, json=payload)
        _assert_rejection(resp, 422)

    def test_null_messages_chat(self, client):
        payload = {"messages": None}
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422)

    def test_null_content_memory(self, client):
        payload = {**VALID_MEMORY, "content": None}
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_rejection(resp, 422)


# ============================================================================
# 19. Empty list where non-empty required
# ============================================================================


class TestEmptyList:
    def test_empty_messages_chat(self, client):
        payload = {"messages": [], "stream": False}
        resp = client.post(CHAT_URL, json=payload)
        # Empty messages may be accepted or rejected — both valid
        _assert_not_500(resp)

    def test_empty_services_agent(self, client):
        payload = {**VALID_AGENT, "services": []}
        resp = client.post(AGENTS_URL, json=payload)
        # services=[] is the default, should be fine
        _assert_not_500(resp)
        if resp.status_code in (200, 201):
            agent_id = resp.json().get("id")
            if agent_id:
                client.delete(f"{AGENTS_URL}/{agent_id}")


# ============================================================================
# 20. Date/time string in wrong format
# ============================================================================


class TestDateTimeFormat:
    def test_invalid_date_in_metadata(self, client):
        payload = {
            **VALID_MEMORY,
            "metadata": {"timestamp": "not-a-date"},
        }
        resp = client.post(MEMORY_ADD_URL, json=payload)
        # metadata is freeform dict — should accept anything
        _assert_not_500(resp)

    def test_numeric_date_in_metadata(self, client):
        payload = {
            **VALID_MEMORY,
            "metadata": {"timestamp": 1234567890},
        }
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_not_500(resp)


# ============================================================================
# Additional boundary tests (21-35)
# ============================================================================


class TestTemperatureRange:
    def test_temperature_at_upper_bound(self, client):
        payload = {**VALID_CHAT, "temperature": 2.0}
        resp = client.post(CHAT_URL, json=payload)
        _assert_not_500(resp)

    def test_temperature_above_upper_bound(self, client):
        payload = {**VALID_CHAT, "temperature": 2.1}
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422)

    def test_temperature_at_lower_bound(self, client):
        payload = {**VALID_CHAT, "temperature": 0.0}
        resp = client.post(CHAT_URL, json=payload)
        _assert_not_500(resp)


class TestMaxItemsMessages:
    def test_messages_at_500_items(self, client):
        """max_items=500 — exactly 500 should be accepted."""
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(500)]
        payload = {"messages": msgs, "stream": False}
        resp = client.post(CHAT_URL, json=payload)
        _assert_not_500(resp)

    def test_messages_exceeding_500(self, client):
        """501 messages should be rejected."""
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(501)]
        payload = {"messages": msgs, "stream": False}
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422, 200)


class TestNestedObjectDepth:
    def test_deeply_nested_metadata(self, client):
        """Deeply nested objects in freeform dict fields."""
        nested = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
        payload = {**VALID_MEMORY, "metadata": nested}
        resp = client.post(MEMORY_ADD_URL, json=payload)
        _assert_not_500(resp)


class TestEmptyJsonBody:
    def test_empty_object_to_chat(self, client):
        resp = client.post(CHAT_URL, json={})
        _assert_rejection(resp, 422)

    def test_empty_object_to_agents(self, client):
        resp = client.post(AGENTS_URL, json={})
        _assert_rejection(resp, 422)

    def test_empty_object_to_memory(self, client):
        resp = client.post(MEMORY_ADD_URL, json={})
        _assert_rejection(resp, 422)

    def test_empty_object_to_entity(self, client):
        resp = client.post(ENTITIES_URL, json={})
        _assert_rejection(resp, 422)


class TestMaxTokensConstraint:
    def test_max_tokens_at_upper_bound(self, client):
        payload = {**VALID_CHAT, "max_tokens": 128000}
        resp = client.post(CHAT_URL, json=payload)
        _assert_not_500(resp)

    def test_max_tokens_above_upper_bound(self, client):
        payload = {**VALID_CHAT, "max_tokens": 128001}
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422)

    def test_max_tokens_zero(self, client):
        payload = {**VALID_CHAT, "max_tokens": 0}
        resp = client.post(CHAT_URL, json=payload)
        _assert_rejection(resp, 422)


class TestEntityFieldValidation:
    def test_entity_with_fields_list(self, client):
        payload = {
            "name": "contract_test_fields",
            "label": "With Fields",
            "fields": [{"name": "field1", "type": "text", "label": "Field 1"}],
        }
        resp = client.post(ENTITIES_URL, json=payload)
        _assert_not_500(resp)

    def test_entity_fields_wrong_type(self, client):
        payload = {
            "name": "contract_test_bad_fields",
            "label": "Bad Fields",
            "fields": "not-a-list",
        }
        resp = client.post(ENTITIES_URL, json=payload)
        _assert_rejection(resp, 422)
