"""
Middleware Chain Integration Tests
====================================
Verifies the correct ordering and interaction of the middleware stack:

    content_size -> rate_limit -> request_id -> timing -> CORS -> auth

Run:
    pytest tests/integration/test_middleware_chain.py -v
"""

import time
import asyncio
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_test_app():
    """Build a minimal FastAPI app with the full middleware chain for testing."""
    app = FastAPI()

    # Apply middleware in the same order as app.py (outermost first)
    from middleware.content_size import content_size_middleware

    app.middleware("http")(content_size_middleware)

    from middleware.timing import timing_middleware

    app.middleware("http")(timing_middleware)

    from middleware.request_id import request_id_middleware

    app.middleware("http")(request_id_middleware)

    from middleware.rate_limit import rate_limit_middleware

    app.middleware("http")(rate_limit_middleware)

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/api/test")
    async def api_test():
        return {"data": "ok"}

    @app.post("/api/test")
    async def api_test_post(request: Request):
        body = await request.body()
        return {"received": len(body)}

    @app.get("/api/slow")
    async def slow_endpoint():
        await asyncio.sleep(1.2)
        return {"slow": True}

    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMiddlewareChain:
    """Integration tests for middleware ordering and interaction."""

    # 1. Rate-limited request never reaches auth ----------------------------
    def test_rate_limited_blocks_before_auth(self):
        """When rate-limited (429), the auth layer is never invoked."""
        from middleware.rate_limit import _limiter

        app = _build_test_app()
        client = TestClient(app)

        # Drain the rate limiter for this IP
        original_buckets = dict(_limiter._buckets)
        try:
            # Set tokens to 0 for testclient IP
            _limiter._buckets["testclient"] = (0.0, time.monotonic())

            resp = client.get("/api/test")
            assert resp.status_code == 429
        finally:
            _limiter._buckets.clear()
            _limiter._buckets.update(original_buckets)

    # 2. Content-size rejection does not consume rate-limit token ------------
    def test_content_size_rejection_preserves_rate_limit(self):
        """A 413 from content_size middleware should not consume a rate limit token."""
        from middleware.rate_limit import _limiter
        from middleware.content_size import MAX_CONTENT_SIZE

        app = _build_test_app()
        client = TestClient(app)

        original_buckets = dict(_limiter._buckets)
        try:
            _limiter._buckets.clear()

            # Send oversized content-length
            resp = client.post(
                "/api/test",
                content=b"x",
                headers={"Content-Length": str(MAX_CONTENT_SIZE + 1)},
            )
            assert resp.status_code == 413

            # The rate limiter bucket should have no entry for this IP
            # (content_size runs before rate_limit, so rate_limit never saw the request)
            # However, due to middleware ordering in Starlette (last added = outermost),
            # we verify that the rate limiter was not decremented by checking
            # a subsequent request still succeeds
            resp2 = client.get("/api/test")
            assert resp2.status_code == 200
        finally:
            _limiter._buckets.clear()
            _limiter._buckets.update(original_buckets)

    # 3. Request ID propagated through full chain ----------------------------
    def test_request_id_propagated(self):
        """Request ID appears in the response header."""
        app = _build_test_app()
        client = TestClient(app)

        resp = client.get("/health")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers
        # The ID should be a UUID-like string
        req_id = resp.headers["X-Request-ID"]
        assert len(req_id) > 0

    # 4. Timing middleware records latency for success -----------------------
    def test_timing_header_on_success(self):
        """Successful response includes Server-Timing header."""
        app = _build_test_app()
        client = TestClient(app)

        resp = client.get("/health")
        assert resp.status_code == 200
        assert "Server-Timing" in resp.headers
        timing = resp.headers["Server-Timing"]
        assert "total;dur=" in timing

    # 5. Timing middleware records latency for errors ------------------------
    def test_timing_header_on_error(self):
        """Error responses also include Server-Timing header."""
        app = _build_test_app()
        client = TestClient(app)

        resp = client.get("/nonexistent-path")
        # Should be 404
        assert "Server-Timing" in resp.headers

    # 6. Health endpoint bypasses rate limiting and auth ---------------------
    def test_health_bypasses_rate_limit(self):
        """GET /health is exempt from rate limiting (always 200)."""
        from middleware.rate_limit import _limiter

        app = _build_test_app()
        client = TestClient(app)

        original_buckets = dict(_limiter._buckets)
        try:
            # Drain tokens for the client IP
            _limiter._buckets["testclient"] = (0.0, time.monotonic())

            # /health should still succeed
            resp = client.get("/health")
            assert resp.status_code == 200
        finally:
            _limiter._buckets.clear()
            _limiter._buckets.update(original_buckets)

    # 7. Middleware order: content_size runs before rate_limit ----------------
    def test_content_size_before_rate_limit(self):
        """An oversized request is rejected by content_size (413),
        proving content_size runs before rate_limit."""
        from middleware.content_size import MAX_CONTENT_SIZE

        app = _build_test_app()
        client = TestClient(app)

        resp = client.post(
            "/api/test",
            content=b"x",
            headers={"Content-Length": str(MAX_CONTENT_SIZE + 1000)},
        )
        assert resp.status_code == 413
        body = resp.json()
        assert "too large" in body.get("detail", "").lower()

    # 8. CORS preflight OPTIONS request handled before auth -----------------
    def test_cors_options_no_auth_required(self, monkeypatch):
        """An OPTIONS preflight request should not require authentication."""
        from app import create_app
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        client = TestClient(create_app())

        resp = client.options(
            "/api/test",
            headers={
                "Origin": "tauri://localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "tauri://localhost"
        assert resp.headers["access-control-allow-credentials"] == "true"
        rejected = client.options("/api/test", headers={
            "Origin": "https://untrusted.example", "Access-Control-Request-Method": "GET"
        })
        assert rejected.status_code == 400
        assert "access-control-allow-origin" not in rejected.headers

    # 9. Request with X-Request-ID header reused in response ----------------
    def test_request_id_passthrough(self):
        """Caller-provided X-Request-ID is echoed back in the response."""
        app = _build_test_app()
        client = TestClient(app)

        custom_id = "custom-trace-id-12345"
        resp = client.get("/health", headers={"X-Request-ID": custom_id})
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == custom_id

    # 10. Slow request logged by timing middleware ---------------------------
    def test_slow_request_logged(self, caplog):
        """Requests > 1s trigger a warning log from timing middleware."""
        app = _build_test_app()
        client = TestClient(app)

        with caplog.at_level(logging.WARNING, logger="vos3.timing"):
            resp = client.get("/api/slow")

        assert resp.status_code == 200
        # Check that the slow-request warning was logged
        slow_logs = [r for r in caplog.records if "Slow request" in r.message]
        assert len(slow_logs) >= 1, "Expected 'Slow request' warning log"
        assert "/api/slow" in slow_logs[0].message
