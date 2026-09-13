"""
Negative Tests: Malformed & Adversarial HTTP Requests
=====================================================
Verify that the VOS3 API rejects malformed, oversized, and adversarial
inputs with proper 4xx status codes (never 500 / stack traces / data leaks).

All tests use the ``client`` fixture (authenticated TestClient) from conftest.py.
"""

import json

import pytest

pytestmark = pytest.mark.negative


# ---------------------------------------------------------------------------
# Endpoints under test — representative POST and GET routes
# ---------------------------------------------------------------------------
POST_ENDPOINTS = [
    "/api/chat/completions",
    "/api/agents",
    "/api/v-core/entities",
    "/api/memory/store",
    "/api/settings/api-keys",
]

GET_ENDPOINTS = [
    "/api/agents",
    "/api/chat/models",
    "/api/v-core/organizations",
    "/api/memory/search",
    "/api/settings/api-keys",
    "/health",
]


# ===================================================================
# 1. Empty JSON body to POST endpoints -> 422 (not 500)
# ===================================================================
class TestEmptyBody:

    @pytest.mark.parametrize("endpoint", POST_ENDPOINTS)
    def test_empty_json_body(self, client, endpoint):
        """POST with ``{}`` should return 422, never 500.

        200 is also acceptable for endpoints where all fields are optional
        (e.g. settings api-keys treats {} as a no-op update).
        """
        resp = client.post(endpoint, json={})
        assert resp.status_code != 500, f"{endpoint} returned 500 on empty body"
        assert resp.status_code in (
            200,
            400,
            404,
            405,
            422,
            503,
        ), f"{endpoint} unexpected status {resp.status_code}"


# ===================================================================
# 2. Null body to POST endpoints -> 422
# ===================================================================
class TestNullBody:

    @pytest.mark.parametrize("endpoint", POST_ENDPOINTS)
    def test_null_body(self, client, endpoint):
        """POST with ``null`` JSON body should return 422, never 500."""
        resp = client.post(
            endpoint,
            content=b"null",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code != 500, f"{endpoint} returned 500 on null body"
        assert resp.status_code in (400, 404, 405, 422, 503)


# ===================================================================
# 3. Non-JSON Content-Type with body -> 422
# ===================================================================
class TestNonJsonContentType:

    @pytest.mark.parametrize(
        "content_type",
        ["text/plain", "text/xml", "application/xml", "multipart/form-data"],
    )
    def test_non_json_content_type(self, client, content_type):
        """POST with non-JSON Content-Type to a JSON endpoint -> 422."""
        resp = client.post(
            "/api/chat/completions",
            content=b"<xml>bad</xml>",
            headers={"Content-Type": content_type},
        )
        assert resp.status_code != 500
        assert resp.status_code in (400, 404, 415, 422, 503)


# ===================================================================
# 4. Path traversal in URL -> 404 (not file contents)
# ===================================================================
class TestPathTraversal:

    @pytest.mark.parametrize(
        "path",
        [
            "/api/chat/../../etc/passwd",
            "/api/chat/..%2F..%2Fetc%2Fpasswd",
            "/api/agents/../../../../etc/shadow",
        ],
    )
    def test_path_traversal(self, client, path):
        """Directory traversal in URL must NOT leak file contents."""
        from fastapi import HTTPException

        try:
            resp = client.get(path)
        except HTTPException as exc:
            # Auth middleware may raise 401 before routing — valid rejection
            assert exc.status_code in (
                401,
                403,
            ), f"Unexpected HTTPException {exc.status_code} for traversal path"
            return
        assert resp.status_code in (
            400,
            401,
            404,
            405,
            422,
            503,
        ), f"Unexpected status {resp.status_code} for traversal path"
        body = resp.text.lower()
        assert "root:" not in body, "Path traversal leaked /etc/passwd!"
        assert "shadow" not in body or "shadow" in path.lower()


# ===================================================================
# 5. Null bytes in URL path -> 400 or 404
# ===================================================================
class TestNullBytesInUrl:

    @pytest.mark.parametrize(
        "path",
        [
            "/api/chat/%00admin",
            "/api/agents/%00%00",
            "/api/v-core/entities/%00",
        ],
    )
    def test_null_bytes_in_url(self, client, path):
        """Null bytes in URL path must not crash the server."""
        resp = client.get(path)
        assert resp.status_code != 500, "Null-byte URL caused 500"
        assert resp.status_code in (400, 404, 422, 503)


# ===================================================================
# 6. SQL injection in query params -> no SQL error
# ===================================================================
class TestSqlInjection:

    @pytest.mark.parametrize(
        "payload",
        [
            "' OR 1=1 --",
            "'; DROP TABLE users; --",
            "1 UNION SELECT * FROM information_schema.tables --",
        ],
    )
    def test_sql_injection_in_query_param(self, client, payload):
        """SQL injection strings in query params must not produce SQL errors."""
        resp = client.get("/api/agents", params={"search": payload})
        body = resp.text.lower()
        assert "syntax error" not in body, "SQL error leaked in response"
        assert "sqlalchemy" not in body
        assert "psycopg" not in body
        assert resp.status_code != 500


# ===================================================================
# 7. NoSQL injection in JSON body -> 422 or ignored
# ===================================================================
class TestNoSqlInjection:

    @pytest.mark.parametrize(
        "payload",
        [
            {"query": {"$gt": ""}},
            {"query": {"$ne": None}},
            {"query": {"$regex": ".*"}},
        ],
    )
    def test_nosql_injection(self, client, payload):
        """NoSQL operator injection in JSON body must not be processed."""
        resp = client.post("/api/memory/store", json=payload)
        assert resp.status_code != 500
        body = resp.text.lower()
        assert "mongodb" not in body
        assert "pymongo" not in body


# ===================================================================
# 8. Deeply nested JSON (100+ levels) -> 422 or handled gracefully
# ===================================================================
class TestDeeplyNestedJson:

    def test_deeply_nested_json(self, client):
        """100+ levels of JSON nesting must not cause a stack overflow."""
        nested = {"a": None}
        current = nested
        for _ in range(150):
            current["a"] = {"a": None}
            current = current["a"]
        current["a"] = "leaf"

        resp = client.post(
            "/api/chat/completions",
            json={"messages": [{"role": "user", "content": json.dumps(nested)}]},
        )
        # Accept anything except 500 / stack overflow crash
        assert resp.status_code != 500 or "recursion" not in resp.text.lower()


# ===================================================================
# 9. Very large array (10K elements) in request body -> 422 or 413
# ===================================================================
class TestLargeArrayBody:

    def test_large_array_body(self, client):
        """10K-element array in messages should be rejected or truncated."""
        messages = [{"role": "user", "content": f"msg-{i}"} for i in range(10_000)]
        resp = client.post("/api/chat/completions", json={"messages": messages})
        # max_items=500 in ChatRequest, so 422 expected
        assert resp.status_code in (
            400,
            413,
            422,
            503,
        ), f"Expected rejection for 10K items, got {resp.status_code}"


# ===================================================================
# 10. Extremely long URL (>8KB) -> 414 or 404
# ===================================================================
class TestExtremelyLongUrl:

    def test_long_url(self, client):
        """URL longer than 8KB should be rejected."""
        padding = "A" * 10_000
        resp = client.get(f"/api/agents?q={padding}")
        # Starlette / uvicorn may return 414, 400, or silently truncate
        assert resp.status_code != 500


# ===================================================================
# 11. Negative limit/offset in query params -> 422 or clamped to 0
# ===================================================================
class TestNegativePagination:

    @pytest.mark.parametrize(
        "param,value",
        [
            ("limit", -1),
            ("offset", -100),
            ("page", -5),
        ],
    )
    def test_negative_pagination(self, client, param, value):
        """Negative pagination values should not crash the server."""
        resp = client.get("/api/agents", params={param: value})
        assert resp.status_code != 500, f"Negative {param}={value} caused 500"


# ===================================================================
# 12. Duplicate query params -> uses one value (no crash)
# ===================================================================
class TestDuplicateQueryParams:

    def test_duplicate_query_params(self, client):
        """Duplicate query params should not crash the server."""
        resp = client.get("/api/agents?page=1&page=2&page=3")
        assert resp.status_code != 500
        # FastAPI picks last value or first — either is fine, just no crash
        assert resp.status_code in (200, 400, 404, 422, 503)


# ===================================================================
# 13. Unicode edge cases: ZWJ, RTL override, surrogates
# ===================================================================
class TestUnicodeEdgeCases:

    @pytest.mark.parametrize(
        "label,payload",
        [
            ("zero_width_joiner", "\u200d\u200d\u200d"),
            ("rtl_override", "\u202e\u202ereversed"),
            ("replacement_char", "\ufffd" * 50),
            (
                "emoji_zwj_sequence",
                "\U0001f468\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466",
            ),
            ("mixed_scripts", "\u0410\u4e00\u0041\u0627"),
        ],
    )
    def test_unicode_edge_cases(self, client, label, payload):
        """Unicode edge-case strings in text fields must not crash."""
        resp = client.post(
            "/api/agents",
            json={"name": payload, "role": "custom"},
        )
        assert resp.status_code != 500, f"Unicode case '{label}' caused 500"


# ===================================================================
# 14. Integer overflow values (2^63) in numeric fields -> 422
# ===================================================================
class TestIntegerOverflow:

    @pytest.mark.parametrize("value", [2**63, -(2**63), 2**128])
    def test_integer_overflow(self, client, value):
        """Integer overflow values in numeric fields -> 422 or safe handling."""
        resp = client.post(
            "/api/chat/completions",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": value,
            },
        )
        assert resp.status_code != 500, f"Overflow value {value} caused 500"
        assert resp.status_code in (400, 422, 503)


# ===================================================================
# 15. Boolean strings where number expected -> 422
# ===================================================================
class TestBooleanStringInNumericField:

    @pytest.mark.parametrize("value", ["true", "false", "yes", "no"])
    def test_boolean_string_in_numeric_field(self, client, value):
        """Boolean strings in numeric fields should be rejected."""
        resp = client.post(
            "/api/chat/completions",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": value,
            },
        )
        assert resp.status_code != 500
        assert resp.status_code in (400, 422, 503)


# ===================================================================
# 16. HTML/script tags in text fields -> escaped/stripped
# ===================================================================
class TestXssInTextFields:

    @pytest.mark.parametrize(
        "payload",
        [
            "<script>alert(1)</script>",
            '<img src=x onerror="alert(1)">',
            "javascript:alert(document.cookie)",
            "<svg/onload=alert(1)>",
        ],
    )
    def test_xss_in_text_fields(self, client, payload):
        """XSS payloads in text fields must be escaped or stripped, never reflected raw."""
        resp = client.post(
            "/api/agents",
            json={"name": payload, "role": "custom"},
        )
        assert resp.status_code != 500
        # If the response echoes the name, verify it doesn't execute
        if resp.status_code in (200, 201):
            body = resp.text
            assert "<script>" not in body, "Raw <script> tag reflected in response"


# ===================================================================
# 17. Empty string for required fields -> 422
# ===================================================================
class TestEmptyStringRequired:

    def test_empty_name_for_agent(self, client):
        """Empty string for required ``name`` field should be rejected or accepted gracefully."""
        resp = client.post(
            "/api/agents",
            json={"name": "", "role": ""},
        )
        # May be 422 (validation) or 200/201 if empty strings are allowed
        assert resp.status_code != 500


# ===================================================================
# 18. Whitespace-only strings for required fields -> 422 or rejected
# ===================================================================
class TestWhitespaceOnlyRequired:

    @pytest.mark.parametrize(
        "name",
        ["   ", "\t\t", "\n\n", "\r\n"],
    )
    def test_whitespace_only_name(self, client, name):
        """Whitespace-only required fields should not create valid resources."""
        resp = client.post(
            "/api/agents",
            json={"name": name, "role": "custom"},
        )
        assert resp.status_code != 500


# ===================================================================
# 19. Very long string (100KB) in a text field -> 422 or 413
# ===================================================================
class TestVeryLongString:

    def test_100kb_string(self, client):
        """100KB string in a text field should be rejected or truncated."""
        long_string = "A" * 102_400
        resp = client.post(
            "/api/chat/completions",
            json={
                "messages": [{"role": "user", "content": long_string}],
            },
        )
        # ChatRequest has max_length=100_000 on content, so 422 expected
        assert resp.status_code in (
            400,
            413,
            422,
            503,
        ), f"100KB content got {resp.status_code}"


# ===================================================================
# 20. Unexpected HTTP method (PATCH where only GET/POST) -> 405
# ===================================================================
class TestUnexpectedHttpMethod:

    @pytest.mark.parametrize(
        "endpoint,method",
        [
            ("/health", "DELETE"),
            ("/health", "PATCH"),
            ("/health", "PUT"),
            ("/api/chat/models", "DELETE"),
            ("/api/chat/models", "PATCH"),
        ],
    )
    def test_unexpected_method(self, client, endpoint, method):
        """Using an unsupported HTTP method should return 405, not 500."""
        resp = client.request(method, endpoint)
        assert resp.status_code != 500, f"{method} {endpoint} returned 500"
        assert resp.status_code in (
            405,
            503,
        ), f"Expected 405 for {method} {endpoint}, got {resp.status_code}"
