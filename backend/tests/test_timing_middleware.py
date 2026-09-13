"""Tests for middleware/timing.py — Response timing middleware."""

import pytest
import logging
from unittest.mock import MagicMock, AsyncMock

from middleware.timing import timing_middleware, SLOW_REQUEST_THRESHOLD_S


class TestTimingMiddleware:
    @pytest.mark.asyncio
    async def test_adds_server_timing_header(self):
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/test"
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await timing_middleware(request, call_next)
        assert "Server-Timing" in result.headers
        assert result.headers["Server-Timing"].startswith("total;dur=")

    @pytest.mark.asyncio
    async def test_valid_duration_format(self):
        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/test"
        response = MagicMock()
        response.headers = {}
        call_next = AsyncMock(return_value=response)

        result = await timing_middleware(request, call_next)
        timing = result.headers["Server-Timing"]
        # Parse "total;dur=123.4"
        dur_str = timing.split("dur=")[1]
        dur = float(dur_str)
        assert dur >= 0

    @pytest.mark.asyncio
    async def test_slow_request_logged(self, caplog):
        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/slow"
        response = MagicMock()
        response.headers = {}

        import time

        async def slow_next(req):
            time.sleep(1.1)
            return response

        with caplog.at_level(logging.WARNING, logger="vos3.timing"):
            await timing_middleware(request, slow_next)
        assert any("Slow request" in r.message for r in caplog.records)

    def test_threshold_constant(self):
        assert SLOW_REQUEST_THRESHOLD_S == 1.0
