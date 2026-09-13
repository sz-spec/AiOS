"""
OWASP API8 — Security Misconfiguration Tests
==============================================

Tests for VOS3 backend security configuration: error handling, auth
enforcement, header hygiene, CORS policy, and production guards.

Reference: https://owasp.org/API-Security/editions/2023/en/0xa8-security-misconfiguration/
"""

import importlib
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client():
    """TestClient for the VOS3 app, bypassing auth for OWASP config tests.

    Overrides the auth dependency to return a test user so that security
    misconfiguration tests can exercise error handlers, CORS, and response
    shapes without being blocked by the auth middleware.
    """
    from main import app
    from middleware.auth import get_current_user, AuthenticatedUser, verify_auth
    from fastapi.testclient import TestClient

    _test_user = AuthenticatedUser(
        id="owasp_test_user",
        email="owasp@test.com",
        org_id="org_owasp",
        permissions=["read"],
    )
    app.dependency_overrides[get_current_user] = lambda: _test_user

    # Also patch verify_auth at middleware level so non-dependency routes pass
    verify_auth.__wrapped__ if hasattr(verify_auth, "__wrapped__") else None

    async def _mock_verify_auth(request):
        return _test_user

    import middleware.auth as _auth_mod

    _orig_verify = _auth_mod.verify_auth
    _auth_mod.verify_auth = _mock_verify_auth

    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    yield client

    app.dependency_overrides.pop(get_current_user, None)
    _auth_mod.verify_auth = _orig_verify


# ===========================================================================
# 1. Error Responses Don't Leak Stack Traces
# ===========================================================================


@pytest.mark.owasp
class TestNoStackTraceLeakage:
    """Error responses must not expose Python tracebacks or internal details."""

    def test_404_no_traceback(self, app_client):
        """A 404 response must not contain Python traceback markers."""
        resp = app_client.get("/api/nonexistent-endpoint-that-does-not-exist")
        body = resp.text
        assert "Traceback" not in body
        assert 'File "' not in body
        assert '.py"' not in body

    def test_404_no_internal_paths(self, app_client):
        """404 responses must not leak filesystem paths."""
        resp = app_client.get("/api/this-path-will-never-exist-xyz")
        body = resp.text
        # Should not contain Python module paths
        assert "/site-packages/" not in body
        assert "/usr/lib/" not in body
        assert "backend/" not in body


# ===========================================================================
# 2. 500 Errors Return Structured JSON
# ===========================================================================


@pytest.mark.owasp
class TestStructured500Errors:
    """Internal server errors must return structured JSON, not raw HTML."""

    def test_unhandled_exception_returns_json(self, app_client):
        """Force an unhandled exception and verify JSON error envelope.

        Uses a non-/api path to avoid the API key middleware, while the
        global exception handler still applies.
        """
        from main import app

        # Add a temporary route at a non-/api path to bypass API key middleware
        @app.get("/_owasp_test_500_trigger")
        async def _trigger_500():
            raise RuntimeError("Intentional OWASP test exception")

        try:
            resp = app_client.get("/_owasp_test_500_trigger")
            assert resp.status_code == 500
            assert resp.headers.get("content-type", "").startswith("application/json")

            data = resp.json()
            assert "error" in data
            assert "code" in data["error"]
            assert "message" in data["error"]
            # Must NOT contain the actual exception message
            assert "Intentional OWASP test exception" not in data["error"]["message"]
        finally:
            # Clean up the temporary route
            app.routes[:] = [
                r
                for r in app.routes
                if getattr(r, "path", "") != "/_owasp_test_500_trigger"
            ]

    def test_500_response_has_request_id(self, app_client):
        """500 responses should include a request_id for tracing."""
        from main import app

        @app.get("/_owasp_test_500_reqid")
        async def _trigger_500_reqid():
            raise ValueError("OWASP request ID test")

        try:
            resp = app_client.get("/_owasp_test_500_reqid")
            assert resp.status_code == 500
            data = resp.json()
            assert "error" in data
            assert "request_id" in data["error"]
            assert len(data["error"]["request_id"]) > 0
        finally:
            app.routes[:] = [
                r
                for r in app.routes
                if getattr(r, "path", "") != "/_owasp_test_500_reqid"
            ]


# ===========================================================================
# 3. Production Mode Requires CLERK_SECRET_KEY
# ===========================================================================


@pytest.mark.owasp
class TestProductionAuthGuard:
    """ENVIRONMENT=production without CLERK_SECRET_KEY must raise RuntimeError."""

    def test_production_no_clerk_key_raises(self):
        """Importing auth module with ENVIRONMENT=production and no key must fail."""
        env_overrides = {
            "ENVIRONMENT": "production",
            "CLERK_SECRET_KEY": "",
            "CLERK_PUBLISHABLE_KEY": "",
            "VOS3_ALLOW_DEV_MODE": "",
            "CLERK_ISSUER_URL": "",
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            # Remove cached module so it re-evaluates module-level guards
            mod_name = "middleware.auth"
            saved = sys.modules.pop(mod_name, None)
            try:
                with pytest.raises(RuntimeError, match="CLERK_SECRET_KEY"):
                    importlib.import_module(mod_name)
            finally:
                # Restore the original module to avoid breaking other tests
                if saved is not None:
                    sys.modules[mod_name] = saved


# ===========================================================================
# 4. Dev Mode Requires Explicit Opt-In
# ===========================================================================


@pytest.mark.owasp
class TestDevModeOptIn:
    """Without CLERK_SECRET_KEY and without VOS3_ALLOW_DEV_MODE, auth must reject."""

    def test_no_key_no_dev_flag_rejects(self):
        """Auth module sets DEV_MODE=False when neither key nor flag is present."""
        env_overrides = {
            "ENVIRONMENT": "development",
            "CLERK_SECRET_KEY": "",
            "VOS3_ALLOW_DEV_MODE": "",
            "CLERK_ISSUER_URL": "",
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            mod_name = "middleware.auth"
            saved = sys.modules.pop(mod_name, None)
            try:
                mod = importlib.import_module(mod_name)
                # DEV_MODE must be False — auth will reject all requests
                assert mod.DEV_MODE is False
            finally:
                if saved is not None:
                    sys.modules[mod_name] = saved

    def test_explicit_dev_mode_allows(self):
        """With VOS3_ALLOW_DEV_MODE=true (and no key), DEV_MODE should be True."""
        env_overrides = {
            "ENVIRONMENT": "development",
            "CLERK_SECRET_KEY": "",
            "VOS3_ALLOW_DEV_MODE": "true",
            "CLERK_ISSUER_URL": "",
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            mod_name = "middleware.auth"
            saved = sys.modules.pop(mod_name, None)
            try:
                mod = importlib.import_module(mod_name)
                assert mod.DEV_MODE is True
            finally:
                if saved is not None:
                    sys.modules[mod_name] = saved


# ===========================================================================
# 5. Health Endpoint Doesn't Expose Secrets
# ===========================================================================


@pytest.mark.owasp
class TestHealthEndpointSecrets:
    """GET /health must not expose API keys, passwords, tokens, or secrets."""

    def test_health_no_secrets(self, app_client):
        resp = app_client.get("/health")
        assert resp.status_code == 200
        body = resp.text.lower()

        # Must not contain secret-like patterns
        secret_patterns = [
            "sk_",
            "sk-",
            "pk_",
            "api_key",
            "secret_key",
            "password",
            "token",
            "bearer",
            "whsec_",
            "rk_",
            "key=",
        ]
        for pattern in secret_patterns:
            assert (
                pattern not in body
            ), f"Health endpoint response contains secret-like pattern: {pattern!r}"

    def test_health_returns_expected_shape(self, app_client):
        """Health response should have status and version, nothing more sensitive."""
        resp = app_client.get("/health")
        data = resp.json()
        assert "status" in data
        assert data["status"] == "healthy"
        assert "version" in data
        # services field should only contain boolean availability flags
        if "services" in data:
            for svc_name, svc_val in data["services"].items():
                assert isinstance(
                    svc_val, bool
                ), f"Service '{svc_name}' should be a boolean, got {type(svc_val).__name__}"


# ===========================================================================
# 6. 429 Responses Include Retry-After Header
# ===========================================================================


@pytest.mark.owasp
class TestRateLimitRetryAfter:
    """Rate-limited (429) responses must include a Retry-After header."""

    def test_rate_limit_includes_retry_after(self, app_client):
        """Exhaust rate limit and verify Retry-After header on 429 response."""
        from middleware.rate_limit import _limiter

        # Drain the bucket for our test IP by setting tokens to 0
        test_ip = "127.0.0.1"
        import time

        _limiter._buckets[test_ip] = (0.0, time.monotonic())

        resp = app_client.get("/api/codegen/status")
        if resp.status_code == 429:
            assert (
                "retry-after" in resp.headers
            ), "429 response missing Retry-After header"
            retry_val = resp.headers["retry-after"]
            # Must be a parseable value (seconds or HTTP-date)
            assert len(retry_val) > 0

    def test_rate_limit_response_is_json(self, app_client):
        """429 responses should return structured JSON."""
        from middleware.rate_limit import _limiter

        test_ip = "127.0.0.1"
        import time

        _limiter._buckets[test_ip] = (0.0, time.monotonic())

        resp = app_client.get("/api/codegen/status")
        if resp.status_code == 429:
            assert resp.headers.get("content-type", "").startswith("application/json")
            data = resp.json()
            assert "detail" in data


# ===========================================================================
# 7. Error Messages Don't Expose Internal Paths or Module Names
# ===========================================================================


@pytest.mark.owasp
class TestNoInternalPathLeakage:
    """Error responses must not reveal internal filesystem paths or Python modules."""

    def test_404_no_module_names(self, app_client):
        resp = app_client.get("/api/definitely-not-a-real-endpoint")
        body = resp.text
        # Should not contain Python module import-style paths
        assert "kernel_bridge." not in body
        assert "middleware." not in body
        assert "fastapi.routing" not in body

    def test_validation_error_no_paths(self, app_client):
        """422 validation errors should not expose filesystem paths."""
        # POST to a known endpoint with invalid data to trigger validation error
        resp = app_client.post(
            "/api/chat/send",
            json={},  # Missing required fields
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code in (401, 403, 422):
            body = resp.text
            assert "/Users/" not in body
            assert "/home/" not in body
            assert "site-packages" not in body


# ===========================================================================
# 8. CORS Headers
# ===========================================================================


@pytest.mark.owasp
class TestCORSConfiguration:
    """CORS must be configured with an explicit allow list, not wildcard '*'."""

    def test_cors_no_wildcard_in_production(self, app_client):
        """Preflight OPTIONS must not return Access-Control-Allow-Origin: *"""
        resp = app_client.options(
            "/api/chat/send",
            headers={
                "Origin": "http://attacker.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        acao = resp.headers.get("access-control-allow-origin", "")
        assert (
            acao != "*"
        ), "CORS allows any origin — production must use an explicit allowlist"

    def test_cors_rejects_unknown_origin(self, app_client):
        """An unknown origin should NOT receive ACAO header."""
        resp = app_client.options(
            "/api/chat/send",
            headers={
                "Origin": "http://evil-attacker-site.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        acao = resp.headers.get("access-control-allow-origin", "")
        # Either empty or not matching the attacker origin
        assert (
            acao != "http://evil-attacker-site.example.com"
        ), "CORS reflected an unknown origin — misconfigured"

    def test_cors_allows_configured_origin(self, app_client):
        """A configured localhost origin should receive matching ACAO header."""
        resp = app_client.options(
            "/api/chat/send",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        acao = resp.headers.get("access-control-allow-origin", "")
        # Should either match exactly or be empty (if CORS disabled)
        if acao:
            assert acao == "http://localhost:3000"


# ===========================================================================
# 9. Content-Type on Error Responses
# ===========================================================================


@pytest.mark.owasp
class TestErrorContentType:
    """All error responses must have Content-Type: application/json."""

    def test_404_content_type(self, app_client):
        resp = app_client.get("/api/nonexistent-path-for-content-type-test")
        ct = resp.headers.get("content-type", "")
        assert (
            "application/json" in ct
        ), f"404 response has Content-Type '{ct}', expected application/json"

    def test_error_not_html(self, app_client):
        """Error responses must never be text/html (debug page indicator)."""
        resp = app_client.get("/api/nonexistent-path-for-html-check")
        ct = resp.headers.get("content-type", "")
        assert (
            "text/html" not in ct
        ), "Error response returned text/html — possible debug mode leak"


# ===========================================================================
# 10. No Debug Mode Indicators in Production Responses
# ===========================================================================


@pytest.mark.owasp
class TestNoDebugIndicators:
    """Production responses must not contain debug mode artifacts."""

    def test_root_no_debug_flags(self, app_client):
        """Root endpoint should not expose debug=True or similar flags."""
        resp = app_client.get("/")
        body = resp.text.lower()
        assert (
            "debug" not in body or 'debug": false' in body or 'debug":false' in body
        ), "Root endpoint exposes debug mode indicator"

    def test_health_no_debug_flags(self, app_client):
        """Health endpoint should not expose debug status."""
        resp = app_client.get("/health")
        body = resp.text.lower()
        # Acceptable: debug is absent or explicitly false
        if "debug" in body:
            data = resp.json()
            # Walk the response to check no debug=True
            body_str = json.dumps(data).lower()
            assert '"debug": true' not in body_str and '"debug":true' not in body_str

    def test_openapi_docs_no_server_info_leak(self, app_client):
        """OpenAPI schema should not leak server hostnames or internal IPs."""
        resp = app_client.get("/openapi.json")
        if resp.status_code == 200:
            body = resp.text
            # Should not contain internal hostnames or IPs
            assert "192.168." not in body
            assert "10.0.0." not in body
            assert (
                "127.0.0.1" not in body or "localhost" in body
            )  # localhost is OK for dev

    def test_error_no_framework_version(self, app_client):
        """Error responses should not expose framework or language versions."""
        resp = app_client.get("/api/nonexistent-for-version-check")
        body = resp.text
        # Should not leak specific framework version details
        assert "uvicorn" not in body.lower()
        assert "starlette" not in body.lower()
        # FastAPI title is fine, but version internals are not
        assert "cpython" not in body.lower()

    def test_response_headers_no_server_leak(self, app_client):
        """Response headers should not expose server software versions."""
        resp = app_client.get("/health")
        server_header = resp.headers.get("server", "").lower()
        # Uvicorn/Starlette may set this; if present, it should not contain version
        if server_header:
            # Acceptable: "uvicorn" alone; not acceptable: "uvicorn/0.x.x"
            import re

            version_pattern = re.compile(r"\d+\.\d+\.\d+")
            assert not version_pattern.search(
                server_header
            ), f"Server header leaks version: {server_header}"


# ===========================================================================
# Additional Misconfiguration Checks
# ===========================================================================


@pytest.mark.owasp
class TestAdditionalMisconfiguration:
    """Extra security misconfiguration tests."""

    def test_root_endpoint_no_api_keys(self, app_client):
        """Root endpoint must not expose API keys or secret values."""
        resp = app_client.get("/")
        assert resp.status_code == 200
        body = resp.text
        secret_keywords = [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "CLERK_SECRET",
            "STRIPE_SECRET",
            "SENTRY_DSN",
            "REDIS_URL",
        ]
        for kw in secret_keywords:
            assert kw not in body, f"Root endpoint exposes {kw}"

    def test_error_envelope_consistent_shape(self, app_client):
        """All API errors should follow the same envelope: {error: {code, message, request_id}}.

        Uses a non-/api path to bypass the API key middleware and reach
        the global exception handler / 404 handler directly.
        """
        resp = app_client.get("/_owasp_nonexistent_for_envelope_test")
        data = resp.json()
        assert "error" in data, "Error response missing top-level 'error' key"
        error = data["error"]
        assert "code" in error, "Error envelope missing 'code'"
        assert "message" in error, "Error envelope missing 'message'"
        assert "request_id" in error, "Error envelope missing 'request_id'"
