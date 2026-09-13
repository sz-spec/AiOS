"""
OWASP API4:2023 -- Unrestricted Resource Consumption
=====================================================

Tests that rate limiting is properly enforced to prevent resource exhaustion.

The rate limiter lives in ``middleware/rate_limit.py``:

- ``TokenBucket(rate, burst)`` -- per-IP token bucket
- Global ``_limiter`` (rate=1.0 tok/s, burst=120) for standard API paths
- Auth ``_auth_limiter`` (rate=5/60 tok/s, burst=5) for identity-sensitive paths
- ``_get_client_ip(request)`` uses ``request.client.host`` (ignores X-Forwarded-For)
- ``_is_auth_path(path)`` checks prefixes: /api/auth/, /api/clerk/, etc.
- Health endpoints ``/health`` and ``/`` are exempt
- Periodic cleanup every 1000 requests

Unit tests import ``TokenBucket`` and ``_is_auth_path`` directly.
Integration tests use ``TestClient`` to exercise the middleware stack.
"""

import time
import pytest
from unittest.mock import MagicMock, AsyncMock

from middleware.rate_limit import (
    TokenBucket,
    _is_auth_path,
    _get_client_ip,
    rate_limit_middleware,
)

# ---------------------------------------------------------------------------
# Marker applied to every test in this module
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.owasp


# =========================================================================
# Unit Tests: TokenBucket
# =========================================================================


class TestTokenBucketUnit:
    """Direct unit tests for the TokenBucket class."""

    def test_allow_returns_true_until_burst_exhausted(self):
        """allow() returns True for exactly ``burst`` calls, then False."""
        bucket = TokenBucket(rate=0, burst=5)
        results = [bucket.allow("ip") for _ in range(6)]
        assert results[:5] == [True] * 5
        assert results[5] is False

    def test_burst_exhaustion_boundary(self):
        """Exactly burst+1 calls: first ``burst`` True, burst+1 False."""
        bucket = TokenBucket(rate=0, burst=3)
        for i in range(3):
            assert bucket.allow("key") is True, f"Call {i} should be True"
        assert bucket.allow("key") is False, "Call 3 should be False (exhausted)"

    def test_burst_of_one(self):
        """Burst=1 allows exactly one request."""
        bucket = TokenBucket(rate=0, burst=1)
        assert bucket.allow("x") is True
        assert bucket.allow("x") is False
        assert bucket.allow("x") is False

    def test_zero_burst_denies_all(self):
        """Burst=0 should deny every request immediately."""
        bucket = TokenBucket(rate=0, burst=0)
        assert bucket.allow("x") is False
        assert bucket.allow("x") is False

    def test_token_replenishment(self):
        """After exhaustion, waiting allows tokens to replenish."""
        bucket = TokenBucket(rate=100.0, burst=2)
        # Exhaust
        assert bucket.allow("ip") is True
        assert bucket.allow("ip") is True
        assert bucket.allow("ip") is False
        # Wait for replenishment (rate=100/s, need 1 token -> 10ms)
        time.sleep(0.05)
        assert bucket.allow("ip") is True

    def test_replenishment_caps_at_burst(self):
        """Tokens never exceed burst even after long idle period."""
        bucket = TokenBucket(rate=1000.0, burst=3)
        assert bucket.allow("ip") is True  # 3 -> 2
        time.sleep(0.1)  # would add 100 tokens, but capped at burst=3
        # Should have at most 3 tokens (burst cap)
        results = [bucket.allow("ip") for _ in range(4)]
        assert results[:3] == [True] * 3
        assert results[3] is False

    def test_different_keys_independent(self):
        """Each key has its own bucket; exhausting one does not affect another."""
        bucket = TokenBucket(rate=0, burst=1)
        assert bucket.allow("alpha") is True
        assert bucket.allow("alpha") is False
        # Beta is independent
        assert bucket.allow("beta") is True
        assert bucket.allow("beta") is False

    def test_cleanup_removes_stale_entries(self):
        """cleanup() removes entries older than max_age."""
        bucket = TokenBucket(rate=10.0, burst=10)
        bucket.allow("ip1")
        bucket.allow("ip2")
        # Manually backdate ip1
        tokens, _ = bucket._buckets["ip1"]
        bucket._buckets["ip1"] = (tokens, 0)  # epoch = very old
        bucket.cleanup(max_age=1.0)
        assert "ip1" not in bucket._buckets
        assert "ip2" in bucket._buckets

    def test_cleanup_preserves_fresh_entries(self):
        """cleanup() does not remove entries within max_age."""
        bucket = TokenBucket(rate=10.0, burst=10)
        bucket.allow("fresh_ip")
        bucket.cleanup(max_age=300.0)
        assert "fresh_ip" in bucket._buckets

    def test_high_rate_sustained(self):
        """With very high rate, requests should almost always be allowed."""
        bucket = TokenBucket(rate=10000.0, burst=10)
        # Exhaust burst first
        for _ in range(10):
            bucket.allow("fast")
        # Even after exhaust, high rate should replenish fast
        time.sleep(0.01)
        assert bucket.allow("fast") is True


# =========================================================================
# Unit Tests: _is_auth_path
# =========================================================================


class TestIsAuthPath:
    """Tests for auth path detection logic."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/auth/login",
            "/api/auth/register",
            "/api/auth/callback",
            "/api/clerk/webhook",
            "/api/clerk/sessions",
            "/api/webhooks/clerk",
            "/api/webhooks/clerk/events",
            "/api/apps/authorize",
            "/api/apps/authorize/callback",
            "/api/apps/register",
            "/api/apps/register/submit",
            "/api/terminal/connect",
            "/api/terminal/session",
        ],
    )
    def test_auth_path_returns_true(self, path):
        """Known auth prefixes must be recognized."""
        assert _is_auth_path(path) is True, f"{path} should be an auth path"

    @pytest.mark.parametrize(
        "path",
        [
            "/api/projects",
            "/api/agents/",
            "/api/memory/add",
            "/api/settings/status",
            "/api/v-core/organizations",
            "/api/billing/checkout",
            "/api/chat/send",
            "/health",
            "/",
        ],
    )
    def test_non_auth_path_returns_false(self, path):
        """Non-auth paths must NOT trigger the auth rate limiter."""
        assert _is_auth_path(path) is False, f"{path} should NOT be an auth path"


# =========================================================================
# Unit Tests: _get_client_ip
# =========================================================================


class TestGetClientIp:
    """Tests that _get_client_ip ignores X-Forwarded-For spoofing."""

    def test_returns_client_host(self):
        """Should return request.client.host."""
        request = MagicMock()
        request.client.host = "10.20.30.40"
        assert _get_client_ip(request) == "10.20.30.40"

    def test_ignores_xff_header(self):
        """X-Forwarded-For must be ignored to prevent IP spoofing."""
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers = {"x-forwarded-for": "8.8.8.8, 1.2.3.4"}
        assert _get_client_ip(request) == "10.0.0.1"

    def test_no_client_returns_unknown(self):
        """If request.client is None, return 'unknown'."""
        request = MagicMock()
        request.client = None
        assert _get_client_ip(request) == "unknown"


# =========================================================================
# Middleware Integration Tests (via raw middleware function)
# =========================================================================


class TestRateLimitMiddleware:
    """Integration tests calling rate_limit_middleware directly."""

    def _make_request(self, path="/api/test", host="127.0.0.1"):
        """Create a mock Request object."""
        request = MagicMock()
        request.url.path = path
        request.client.host = host
        request.headers = {}
        return request

    @pytest.mark.asyncio
    async def test_health_endpoint_exempt(self):
        """GET /health should bypass rate limiting entirely."""
        call_next = AsyncMock(return_value="ok")
        request = self._make_request(path="/health")
        result = await rate_limit_middleware(request, call_next)
        assert result == "ok"
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_root_endpoint_exempt(self):
        """GET / should bypass rate limiting entirely."""
        call_next = AsyncMock(return_value="ok")
        request = self._make_request(path="/")
        result = await rate_limit_middleware(request, call_next)
        assert result == "ok"
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_normal_request_passes_within_limit(self):
        """A single standard API request within burst should pass."""
        call_next = AsyncMock(return_value="ok")
        request = self._make_request(path="/api/test", host="normal_test_ip")
        result = await rate_limit_middleware(request, call_next)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_xff_spoofing_does_not_bypass_limit(self):
        """Spoofing X-Forwarded-For must not create a separate bucket."""
        from middleware import rate_limit as rl_mod

        # Create a dedicated bucket for this test
        original_limiter = rl_mod._limiter
        rl_mod._limiter = TokenBucket(rate=0, burst=2)
        try:
            call_next = AsyncMock(return_value="ok")

            # Same real IP, different XFF
            for xff_ip in ["1.1.1.1", "2.2.2.2"]:
                req = self._make_request(path="/api/data", host="xff_test_ip")
                req.headers = {"x-forwarded-for": xff_ip}
                await rate_limit_middleware(req, call_next)

            # Third request from same real IP should be blocked
            req = self._make_request(path="/api/data", host="xff_test_ip")
            req.headers = {"x-forwarded-for": "3.3.3.3"}
            result = await rate_limit_middleware(req, call_next)
            assert result.status_code == 429
        finally:
            rl_mod._limiter = original_limiter


# =========================================================================
# Middleware Integration Tests: Standard limiter burst exhaustion
# =========================================================================


class TestStandardLimiterExhaustion:
    """Tests that the global limiter returns 429 after burst exhaustion."""

    @pytest.mark.asyncio
    async def test_standard_burst_exhaustion_returns_429(self):
        """After 120 requests (burst), the 121st must return 429."""
        from middleware import rate_limit as rl_mod

        original_limiter = rl_mod._limiter
        # Use a small burst for test speed
        rl_mod._limiter = TokenBucket(rate=0, burst=5)
        try:
            call_next = AsyncMock(return_value="ok")
            ip = "burst_test_ip"

            for i in range(5):
                req = MagicMock()
                req.url.path = "/api/some-endpoint"
                req.client.host = ip
                req.headers = {}
                result = await rate_limit_middleware(req, call_next)
                assert result == "ok", f"Request {i} should pass"

            # 6th request -- should be 429
            req = MagicMock()
            req.url.path = "/api/some-endpoint"
            req.client.host = ip
            req.headers = {}
            result = await rate_limit_middleware(req, call_next)
            assert result.status_code == 429
        finally:
            rl_mod._limiter = original_limiter

    @pytest.mark.asyncio
    async def test_429_includes_retry_after_header(self):
        """429 response must include Retry-After header."""
        from middleware import rate_limit as rl_mod

        original_limiter = rl_mod._limiter
        rl_mod._limiter = TokenBucket(rate=0, burst=1)
        try:
            call_next = AsyncMock(return_value="ok")
            ip = "retry_after_test_ip"

            req = MagicMock()
            req.url.path = "/api/test"
            req.client.host = ip
            req.headers = {}
            await rate_limit_middleware(req, call_next)

            # Second request triggers 429
            req2 = MagicMock()
            req2.url.path = "/api/test"
            req2.client.host = ip
            req2.headers = {}
            result = await rate_limit_middleware(req2, call_next)
            assert result.status_code == 429
            assert "retry-after" in {k.lower() for k in result.headers.keys()}
        finally:
            rl_mod._limiter = original_limiter

    @pytest.mark.asyncio
    async def test_different_ips_have_independent_buckets(self):
        """Two different IPs should have separate rate limit counters."""
        from middleware import rate_limit as rl_mod

        original_limiter = rl_mod._limiter
        rl_mod._limiter = TokenBucket(rate=0, burst=2)
        try:
            call_next = AsyncMock(return_value="ok")

            # Exhaust IP-A
            for _ in range(2):
                req = MagicMock()
                req.url.path = "/api/test"
                req.client.host = "ip_a_independent"
                req.headers = {}
                await rate_limit_middleware(req, call_next)

            # IP-A exhausted
            req = MagicMock()
            req.url.path = "/api/test"
            req.client.host = "ip_a_independent"
            req.headers = {}
            result = await rate_limit_middleware(req, call_next)
            assert result.status_code == 429

            # IP-B should still have full burst
            req = MagicMock()
            req.url.path = "/api/test"
            req.client.host = "ip_b_independent"
            req.headers = {}
            result = await rate_limit_middleware(req, call_next)
            assert result == "ok"
        finally:
            rl_mod._limiter = original_limiter


# =========================================================================
# Middleware Integration Tests: Auth limiter
# =========================================================================


class TestAuthLimiterExhaustion:
    """Tests the stricter auth rate limiter (burst=5, rate=5/60)."""

    @pytest.mark.asyncio
    async def test_auth_path_blocks_after_5_requests(self):
        """Auth paths have burst=5; 6th request must return 429."""
        from middleware import rate_limit as rl_mod

        original_auth_limiter = rl_mod._auth_limiter
        rl_mod._auth_limiter = TokenBucket(rate=0, burst=5)
        try:
            call_next = AsyncMock(return_value="ok")
            ip = "auth_burst_ip"

            for i in range(5):
                req = MagicMock()
                req.url.path = "/api/auth/login"
                req.client.host = ip
                req.headers = {}
                result = await rate_limit_middleware(req, call_next)
                assert result == "ok", f"Auth request {i} should pass"

            # 6th auth request -- 429
            req = MagicMock()
            req.url.path = "/api/auth/login"
            req.client.host = ip
            req.headers = {}
            result = await rate_limit_middleware(req, call_next)
            assert result.status_code == 429
        finally:
            rl_mod._auth_limiter = original_auth_limiter

    @pytest.mark.asyncio
    async def test_auth_429_has_retry_after_60(self):
        """Auth 429 must include Retry-After: 60 header."""
        from middleware import rate_limit as rl_mod

        original_auth_limiter = rl_mod._auth_limiter
        rl_mod._auth_limiter = TokenBucket(rate=0, burst=1)
        try:
            call_next = AsyncMock(return_value="ok")
            ip = "auth_retry_ip"

            req = MagicMock()
            req.url.path = "/api/auth/login"
            req.client.host = ip
            req.headers = {}
            await rate_limit_middleware(req, call_next)

            req2 = MagicMock()
            req2.url.path = "/api/auth/login"
            req2.client.host = ip
            req2.headers = {}
            result = await rate_limit_middleware(req2, call_next)
            assert result.status_code == 429
            assert result.headers.get("retry-after") == "60"
        finally:
            rl_mod._auth_limiter = original_auth_limiter

    @pytest.mark.asyncio
    async def test_auth_and_standard_limiters_are_independent(self):
        """Exhausting the auth limiter must not affect the standard limiter."""
        from middleware import rate_limit as rl_mod

        orig_limiter = rl_mod._limiter
        orig_auth = rl_mod._auth_limiter
        rl_mod._limiter = TokenBucket(rate=0, burst=5)
        rl_mod._auth_limiter = TokenBucket(rate=0, burst=1)
        try:
            call_next = AsyncMock(return_value="ok")
            ip = "cross_limiter_ip"

            # Exhaust auth limiter
            req = MagicMock()
            req.url.path = "/api/auth/login"
            req.client.host = ip
            req.headers = {}
            await rate_limit_middleware(req, call_next)

            req2 = MagicMock()
            req2.url.path = "/api/auth/login"
            req2.client.host = ip
            req2.headers = {}
            result = await rate_limit_middleware(req2, call_next)
            assert result.status_code == 429

            # Standard endpoint from same IP should still work
            req3 = MagicMock()
            req3.url.path = "/api/projects"
            req3.client.host = ip
            req3.headers = {}
            result = await rate_limit_middleware(req3, call_next)
            assert result == "ok"
        finally:
            rl_mod._limiter = orig_limiter
            rl_mod._auth_limiter = orig_auth


# =========================================================================
# Cleanup logic tests
# =========================================================================


class TestCleanupIntegration:
    """Test the periodic cleanup mechanism."""

    @pytest.mark.asyncio
    async def test_cleanup_runs_after_1000_requests(self):
        """After 1000 requests, _limiter.cleanup() should be triggered."""
        from middleware import rate_limit as rl_mod

        orig_counter = rl_mod._cleanup_counter
        orig_limiter = rl_mod._limiter
        # Use high burst so no 429s interfere
        rl_mod._limiter = TokenBucket(rate=10000, burst=2000)
        rl_mod._cleanup_counter = 998  # 2 more to trigger
        try:
            call_next = AsyncMock(return_value="ok")

            for i in range(3):
                req = MagicMock()
                req.url.path = "/api/test"
                req.client.host = f"cleanup_ip_{i}"
                req.headers = {}
                await rate_limit_middleware(req, call_next)

            # After 1000th request, counter resets to 0 (then increments)
            # At 998 + 3 = 1001, wraps to 1 after reset
            assert (
                rl_mod._cleanup_counter < 998
            ), f"Counter should have reset; got {rl_mod._cleanup_counter}"
        finally:
            rl_mod._cleanup_counter = orig_counter
            rl_mod._limiter = orig_limiter

    def test_cleanup_removes_stale_buckets(self):
        """TokenBucket.cleanup() should remove entries older than max_age."""
        bucket = TokenBucket(rate=1.0, burst=10)
        bucket.allow("stale_ip")
        bucket.allow("fresh_ip")
        # Backdate stale_ip
        tokens, _ = bucket._buckets["stale_ip"]
        bucket._buckets["stale_ip"] = (tokens, 0)
        bucket.cleanup(max_age=1.0)
        assert "stale_ip" not in bucket._buckets
        assert "fresh_ip" in bucket._buckets


# =========================================================================
# Concurrent rapid-fire from same IP
# =========================================================================


class TestConcurrentRapidFire:
    """Simulates rapid concurrent requests from a single IP."""

    @pytest.mark.asyncio
    async def test_rapid_fire_respects_burst(self):
        """Firing many requests rapidly must not exceed burst allowance."""
        bucket = TokenBucket(rate=0, burst=10)
        results = [bucket.allow("rapid_ip") for _ in range(20)]
        allowed = sum(1 for r in results if r)
        denied = sum(1 for r in results if not r)
        assert allowed == 10
        assert denied == 10

    @pytest.mark.asyncio
    async def test_rapid_fire_auth_path(self):
        """Rapid auth requests respect the tighter burst=5 limit."""
        from middleware import rate_limit as rl_mod

        orig_auth = rl_mod._auth_limiter
        rl_mod._auth_limiter = TokenBucket(rate=0, burst=5)
        try:
            call_next = AsyncMock(return_value="ok")
            ip = "rapid_auth_ip"
            results = []

            for _ in range(10):
                req = MagicMock()
                req.url.path = "/api/auth/login"
                req.client.host = ip
                req.headers = {}
                result = await rate_limit_middleware(req, call_next)
                results.append(result)

            ok_count = sum(1 for r in results if r == "ok")
            blocked_count = sum(
                1 for r in results if hasattr(r, "status_code") and r.status_code == 429
            )
            assert ok_count == 5
            assert blocked_count == 5
        finally:
            rl_mod._auth_limiter = orig_auth
