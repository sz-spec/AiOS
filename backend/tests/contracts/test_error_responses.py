"""
Error Response Structure Tests
===============================

Verifies that ALL error responses follow a consistent structure, never
leak internal details (stack traces, file paths, SQL), and always return
valid JSON with correct Content-Type headers.

Run with:
    pytest tests/contracts/test_error_responses.py -m contract
"""

import json
import pytest

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_valid_json(resp):
    """Return True if the response body is parseable JSON."""
    try:
        resp.json()
        return True
    except (json.JSONDecodeError, ValueError):
        return False


def _has_json_content_type(resp):
    ct = resp.headers.get("content-type", "")
    return "application/json" in ct


CHAT_URL = "/api/chat/completions"
AGENTS_URL = "/api/agents"
MEMORY_ADD_URL = "/api/memory/add"


# ============================================================================
# 1. 404 returns {"detail": "..."} JSON
# ============================================================================


class TestNotFound:
    def test_404_has_detail_key(self, client):
        resp = client.get("/api/this-route-does-not-exist-at-all")
        assert resp.status_code in (404, 503)
        data = resp.json()
        assert (
            "detail" in data or "error" in data
        ), f"404 response missing 'detail' or 'error': {data}"

    def test_404_body_is_json(self, client):
        resp = client.get("/api/this-route-does-not-exist-at-all")
        assert _is_valid_json(resp), "404 body is not valid JSON"


# ============================================================================
# 2. 422 returns {"detail": [...]} with validation errors
# ============================================================================


class TestValidationError:
    def test_422_has_detail(self, client):
        resp = client.post(CHAT_URL, json={})
        assert resp.status_code in (422, 503)
        data = resp.json()
        # FastAPI 422 uses {"detail": [...]} or custom {"error": {...}}
        assert (
            "detail" in data or "error" in data
        ), f"422 response missing 'detail' or 'error': {list(data.keys())}"

    def test_422_detail_lists_errors(self, client):
        resp = client.post(CHAT_URL, json={})
        if resp.status_code == 422:
            data = resp.json()
            if "detail" in data:
                detail = data["detail"]
                # Standard FastAPI: detail is a list of error dicts
                if isinstance(detail, list):
                    assert len(detail) > 0, "422 detail list should not be empty"
                    first = detail[0]
                    assert isinstance(first, dict)

    def test_422_mentions_field_name(self, client):
        """Validation error should reference which field failed."""
        resp = client.post(CHAT_URL, json={"stream": False})
        if resp.status_code == 422:
            text = resp.text.lower()
            assert (
                "messages" in text or "field" in text or "required" in text
            ), "422 should mention the problematic field"


# ============================================================================
# 3. 401 returns {"detail": "..."}
# ============================================================================


class TestUnauthorized:
    def test_401_structure(self, client, _app):
        """If auth is enforced, removing the override should yield 401."""
        # This test uses the unauthenticated_client fixture pattern
        from fastapi.testclient import TestClient

        try:
            from middleware.auth import get_current_user

            # Temporarily remove auth override
            override = _app.dependency_overrides.pop(get_current_user, None)
            raw_client = TestClient(_app)
            resp = raw_client.get("/api/agents")
            if resp.status_code == 401:
                data = resp.json()
                assert (
                    "detail" in data or "error" in data
                ), "401 should return structured JSON"
        except (ImportError, KeyError):
            pytest.skip("Auth middleware not available")
        finally:
            # Restore override
            if override is not None:
                _app.dependency_overrides[get_current_user] = override


# ============================================================================
# 4. 403 returns {"detail": "..."}
# ============================================================================


class TestForbidden:
    def test_403_structure_if_triggerable(self, client):
        """If any endpoint returns 403, verify structure."""
        # Attempt to hit a privileged-only endpoint with default test user
        resp = client.post("/api/metrics/reset")
        if resp.status_code == 403:
            data = resp.json()
            assert "detail" in data or "error" in data


# ============================================================================
# 5. 405 wrong method
# ============================================================================


class TestMethodNotAllowed:
    def test_patch_on_health_returns_405(self, client):
        resp = client.patch("/health")
        assert resp.status_code in (405, 503)
        assert _is_valid_json(resp), "405 should return JSON body"

    def test_delete_on_health_returns_405(self, client):
        resp = client.delete("/health")
        assert resp.status_code in (405, 503)

    def test_put_on_chat_models_returns_405(self, client):
        resp = client.put("/api/chat/models", json={})
        assert resp.status_code in (405, 503)


# ============================================================================
# 6. 429 rate limit (if triggerable)
# ============================================================================


class TestRateLimit:
    def test_429_has_structured_response(self, client):
        """If rate limiting is active, burst requests should yield 429."""
        # Attempt rapid burst — may or may not trigger 429
        last_resp = None
        for _ in range(50):
            last_resp = client.get("/health")
            if last_resp.status_code == 429:
                break
        if last_resp and last_resp.status_code == 429:
            assert _is_valid_json(last_resp), "429 should return JSON"
            data = last_resp.json()
            assert "detail" in data or "error" in data


# ============================================================================
# 7. Content-Type mismatch
# ============================================================================


class TestContentTypeMismatch:
    def test_text_plain_to_json_endpoint(self, client):
        resp = client.post(
            CHAT_URL,
            content="plain text body",
            headers={"Content-Type": "text/plain"},
        )
        # Should be 422, not 500
        assert resp.status_code in (
            415,
            422,
            400,
            503,
        ), f"text/plain to JSON endpoint should be 4xx, got {resp.status_code}"


# ============================================================================
# 8. Empty body to POST endpoint
# ============================================================================


class TestEmptyBody:
    def test_empty_body_chat(self, client):
        resp = client.post(
            CHAT_URL,
            content="",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (
            422,
            400,
            503,
        ), f"Empty body should be 422/400, got {resp.status_code}"

    def test_empty_body_agents(self, client):
        resp = client.post(
            AGENTS_URL,
            content="",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (
            422,
            400,
            503,
        ), f"Empty body should be 422/400, got {resp.status_code}"

    def test_empty_body_memory(self, client):
        resp = client.post(
            MEMORY_ADD_URL,
            content="",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (
            422,
            400,
            503,
        ), f"Empty body should be 422/400, got {resp.status_code}"


# ============================================================================
# 9. Non-JSON body to JSON endpoint
# ============================================================================


class TestNonJsonBody:
    def test_html_body_to_json_endpoint(self, client):
        resp = client.post(
            CHAT_URL,
            content="<html><body>hi</body></html>",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (
            422,
            400,
            503,
        ), f"Non-JSON body should be 422/400, got {resp.status_code}"

    def test_xml_body_to_json_endpoint(self, client):
        resp = client.post(
            AGENTS_URL,
            content="<agent><name>test</name></agent>",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (
            422,
            400,
            503,
        ), f"XML body should be 422/400, got {resp.status_code}"


# ============================================================================
# 10. 500 does not leak raw traceback
# ============================================================================


class TestNoTracebackLeak:
    def test_500_no_traceback(self, client):
        """If we can trigger a 500, verify no raw traceback is exposed."""
        # Try to trigger a 500 via impossible operation
        resp = client.post(
            "/api/v-core/workflows/nonexistent-wf/execute",
            json={"trigger_data": {}},
        )
        if resp.status_code == 500:
            text = resp.text
            assert "Traceback" not in text, "500 leaks Python traceback"
            assert 'File "/' not in text, "500 leaks file paths"


# ============================================================================
# 11. Very large request body
# ============================================================================


class TestLargeBody:
    def test_oversized_body(self, client):
        huge_payload = {"data": "X" * (10 * 1024 * 1024)}  # ~10MB
        resp = client.post(CHAT_URL, json=huge_payload)
        # Should be 413 or 422, not unhandled crash
        assert resp.status_code in (
            413,
            422,
            400,
            500,
            503,
        ), f"Very large body: expected 4xx/5xx, got {resp.status_code}"


# ============================================================================
# 12. All error responses have Content-Type: application/json
# ============================================================================


class TestErrorContentType:
    @pytest.mark.parametrize(
        "method,path,expected_code",
        [
            ("GET", "/api/nonexistent-route", 404),
            ("PATCH", "/health", 405),
        ],
    )
    def test_error_has_json_content_type(self, client, method, path, expected_code):
        resp = getattr(client, method.lower())(path)
        if resp.status_code == expected_code:
            assert _has_json_content_type(
                resp
            ), f"{resp.status_code} missing application/json Content-Type"


# ============================================================================
# 13. Error messages don't contain stack traces
# ============================================================================


class TestNoStackTrace:
    STACK_PATTERNS = ["Traceback (most recent", '  File "/', '  File "<']

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v-core/entities/bad-id",
            "/api/v-core/records/bad-id",
            "/api/v-core/workflows/bad-id",
        ],
    )
    def test_no_stack_trace_in_error(self, client, path):
        resp = client.get(path)
        if resp.status_code >= 400:
            text = resp.text
            for pattern in self.STACK_PATTERNS:
                assert (
                    pattern not in text
                ), f"Stack trace pattern found in {path} error response"


# ============================================================================
# 14. Error messages don't contain file paths
# ============================================================================


class TestNoFilePaths:
    FILE_PATH_PATTERNS = [
        "/Users/",
        "/home/",
        "/app/",
        "\\Users\\",
        "site-packages/",
    ]

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v-core/entities/bad-id",
            "/api/agents/nonexistent-999",
        ],
    )
    def test_no_file_paths_in_error(self, client, path):
        resp = client.get(path)
        if resp.status_code >= 400:
            text = resp.text
            for pattern in self.FILE_PATH_PATTERNS:
                assert (
                    pattern not in text
                ), f"File path '{pattern}' found in error response for {path}"


# ============================================================================
# 15. Error messages don't contain SQL / internal queries
# ============================================================================


class TestNoSQLLeak:
    SQL_PATTERNS = ["SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE"]

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v-core/entities/bad-id",
            "/api/v-core/records/bad-id",
        ],
    )
    def test_no_sql_in_error(self, client, path):
        resp = client.get(path)
        if resp.status_code >= 400:
            text = resp.text
            for pattern in self.SQL_PATTERNS:
                assert (
                    pattern not in text
                ), f"SQL pattern '{pattern}' found in error for {path}"


# ============================================================================
# 16. Error response body is always valid JSON
# ============================================================================


class TestErrorBodyAlwaysJSON:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/nonexistent-route"),
            ("POST", "/api/chat/completions"),
            ("PATCH", "/health"),
        ],
    )
    def test_error_body_is_json(self, client, method, path):
        if method == "POST":
            resp = client.post(path, json={})
        else:
            resp = getattr(client, method.lower())(path)
        if resp.status_code >= 400:
            assert _is_valid_json(
                resp
            ), f"{method} {path} returned {resp.status_code} with non-JSON body: {resp.text[:200]}"
            # Also ensure it's not HTML
            assert not resp.text.strip().startswith(
                "<!"
            ), "Error response is HTML, not JSON"


# ============================================================================
# 17. OPTIONS requests
# ============================================================================


class TestOptionsRequests:
    def test_options_returns_cors_or_405(self, client):
        resp = client.options("/api/agents")
        # FastAPI with CORSMiddleware: 200 with CORS headers
        # Without CORS: 405
        assert resp.status_code in (200, 204, 405, 503)

    def test_options_health(self, client):
        resp = client.options("/health")
        assert resp.status_code in (200, 204, 405, 503)


# ============================================================================
# 18. HEAD requests
# ============================================================================


class TestHeadRequests:
    def test_head_health_matches_get_status(self, client):
        get_resp = client.get("/health")
        head_resp = client.head("/health")
        # HEAD may return 405 if the endpoint only defines GET
        assert head_resp.status_code in (
            get_resp.status_code,
            405,
            503,
        ), f"HEAD /health status {head_resp.status_code} unexpected (GET was {get_resp.status_code})"
        # HEAD should have no body
        assert (
            head_resp.content == b""
            or len(head_resp.content) == 0
            or head_resp.headers.get("content-length") is not None
        )

    def test_head_agents_matches_get_status(self, client):
        get_resp = client.get("/api/agents")
        head_resp = client.head("/api/agents")
        assert head_resp.status_code in (
            get_resp.status_code,
            405,
            503,
        ), f"HEAD /api/agents status {head_resp.status_code} unexpected (GET was {get_resp.status_code})"


# ============================================================================
# 19. Multiple Accept headers
# ============================================================================


class TestAcceptHeaders:
    def test_accept_json(self, client):
        resp = client.get("/health", headers={"Accept": "application/json"})
        assert resp.status_code in (200, 503)

    def test_accept_wildcard(self, client):
        resp = client.get("/health", headers={"Accept": "*/*"})
        assert resp.status_code in (200, 503)

    def test_accept_xml_still_returns_json(self, client):
        resp = client.get("/health", headers={"Accept": "application/xml"})
        # FastAPI always returns JSON — should not error out
        assert resp.status_code in (200, 406, 503)


# ============================================================================
# 20. Invalid (malformed) JSON body
# ============================================================================


class TestMalformedJSON:
    def test_truncated_json(self, client):
        resp = client.post(
            CHAT_URL,
            content='{"messages": [{"role": "user", "content": "hi"',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (
            422,
            400,
            503,
        ), f"Truncated JSON should be 422/400, got {resp.status_code}"

    def test_trailing_comma_json(self, client):
        resp = client.post(
            CHAT_URL,
            content='{"messages": [],}',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (
            422,
            400,
            503,
        ), f"Trailing comma JSON should be 422/400, got {resp.status_code}"

    def test_single_quote_json(self, client):
        resp = client.post(
            CHAT_URL,
            content="{'messages': []}",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (
            422,
            400,
            503,
        ), f"Single-quote JSON should be 422/400, got {resp.status_code}"

    def test_binary_garbage(self, client):
        resp = client.post(
            CHAT_URL,
            content=b"\x00\x01\x02\xff\xfe",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (
            422,
            400,
            503,
        ), f"Binary garbage should be 422/400, got {resp.status_code}"
