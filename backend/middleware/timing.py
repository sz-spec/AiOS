"""
Response Timing Middleware
==========================
Logs slow requests (>1s) and adds Server-Timing header.
"""

import time
import logging

from fastapi import Request

logger = logging.getLogger("vos3.timing")

SLOW_REQUEST_THRESHOLD_S = 1.0


async def timing_middleware(request: Request, call_next):
    """Add timing header and log slow requests."""
    start = time.monotonic()

    response = await call_next(request)

    duration = time.monotonic() - start
    duration_ms = duration * 1000

    # Add Server-Timing header
    response.headers["Server-Timing"] = f"total;dur={duration_ms:.1f}"

    # Log slow requests
    if duration > SLOW_REQUEST_THRESHOLD_S:
        logger.warning(
            "Slow request: %s %s took %.1fms",
            request.method,
            request.url.path,
            duration_ms,
        )

    return response
