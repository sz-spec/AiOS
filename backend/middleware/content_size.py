"""
Content Size Limit Middleware
==============================
Rejects requests whose Content-Length exceeds a configurable maximum.
Requests without a Content-Length header (e.g. streaming) are allowed through.
"""

from fastapi import Request
from fastapi.responses import JSONResponse

MAX_CONTENT_SIZE = 10 * 1024 * 1024  # 10 MB


async def content_size_middleware(request: Request, call_next):
    """Return 413 if Content-Length exceeds MAX_CONTENT_SIZE."""
    content_length = request.headers.get("content-length")

    if content_length is not None:
        try:
            if int(content_length) > MAX_CONTENT_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": "Request body too large",
                        "max_bytes": MAX_CONTENT_SIZE,
                    },
                )
        except ValueError:
            pass  # Malformed header — let downstream handle it

    return await call_next(request)
