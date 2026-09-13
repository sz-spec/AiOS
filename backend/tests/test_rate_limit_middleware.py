"""Tests for middleware/rate_limit.py — Token bucket rate limiter."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from middleware.rate_limit import TokenBucket, rate_limit_middleware, _get_client_ip


class TestTokenBucket:
    def test_allow_within_limit(self):
        bucket = TokenBucket(rate=10.0, burst=10)
        assert bucket.allow("192.168.1.1") is True

    def test_burst_capacity(self):
        bucket = TokenBucket(rate=1.0, burst=5)
        for _ in range(5):
            assert bucket.allow("ip1") is True
        # 6th request should fail (no time for replenish)
        assert bucket.allow("ip1") is False

    def test_different_ips_separate_buckets(self):
        bucket = TokenBucket(rate=1.0, burst=1)
        assert bucket.allow("ip1") is True
        assert bucket.allow("ip2") is True
        # ip1 exhausted, ip2 still fine
        assert bucket.allow("ip1") is False

    def test_cleanup_removes_stale(self):
        bucket = TokenBucket(rate=10.0, burst=10)
        bucket.allow("ip1")
        # Manually set old timestamp
        bucket._buckets["ip1"] = (10, 0)  # tokens, time=0 (very old)
        bucket.cleanup(max_age=0.001)
        assert "ip1" not in bucket._buckets


class TestGetClientIp:
    def test_direct_client(self):
        request = MagicMock()
        request.headers = {}
        request.client.host = "10.0.0.1"
        assert _get_client_ip(request) == "10.0.0.1"

    def test_x_forwarded_for(self):
        """_get_client_ip ignores XFF and uses TCP peer address (request.client.host)."""
        request = MagicMock()
        request.headers = {"x-forwarded-for": "203.0.113.5, 10.0.0.1"}
        request.client.host = "10.0.0.1"
        assert _get_client_ip(request) == "10.0.0.1"


class TestRateLimitMiddleware:
    @pytest.mark.asyncio
    async def test_health_bypasses(self):
        request = MagicMock()
        request.url.path = "/health"
        call_next = AsyncMock(return_value="ok")
        result = await rate_limit_middleware(request, call_next)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_root_bypasses(self):
        request = MagicMock()
        request.url.path = "/"
        call_next = AsyncMock(return_value="ok")
        result = await rate_limit_middleware(request, call_next)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_normal_request_passes(self):
        request = MagicMock()
        request.url.path = "/api/test"
        request.headers = {}
        request.client.host = "1.2.3.4"
        call_next = AsyncMock(return_value="ok")
        result = await rate_limit_middleware(request, call_next)
        assert result == "ok"
