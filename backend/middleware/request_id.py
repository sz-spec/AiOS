"""
Request ID Middleware
=====================
Assigns a unique request ID to each request for tracing and correlation.
Reuses X-Request-ID from incoming headers if present.
"""

import uuid
import logging

from fastapi import Request

logger = logging.getLogger("vos3.request_id")


async def request_id_middleware(request: Request, call_next):
    """Attach a unique request ID and propagate it in the response."""
    # Reuse caller-provided ID for distributed tracing, otherwise generate one
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    # Store on request.state so error handlers and routes can access it
    request.state.request_id = request_id

    response = await call_next(request)

    # Propagate to response for client-side correlation
    response.headers["X-Request-ID"] = request_id

    return response
