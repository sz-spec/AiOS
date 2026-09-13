"""Structured error responses for VOS3 API.

Provides a consistent error format across all endpoints:

    {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Human-readable description",
            "details": { ... optional context ... },
            "request_id": "uuid"
        }
    }

Usage:
    from core.errors import VOS3Error, ErrorCode, vos3_error_response

    # Raise structured errors
    raise VOS3Error(ErrorCode.NOT_FOUND, "Agent not found", {"agent_id": agent_id})

    # Or use helper
    raise not_found("Agent", agent_id)
"""

from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorCode(str, Enum):
    """Standardized error codes for the VOS3 API."""

    # Client errors (4xx)
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNPROCESSABLE = "UNPROCESSABLE"

    # Server errors (5xx)
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    TIMEOUT = "TIMEOUT"

    # Domain-specific
    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    AGENT_ERROR = "AGENT_ERROR"
    KERNEL_ERROR = "KERNEL_ERROR"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"


# Map error codes to HTTP status codes
_CODE_TO_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.UNPROCESSABLE: 422,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.UPSTREAM_ERROR: 502,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.MODEL_NOT_AVAILABLE: 503,
    ErrorCode.QUOTA_EXCEEDED: 429,
    ErrorCode.AGENT_ERROR: 500,
    ErrorCode.KERNEL_ERROR: 502,
    ErrorCode.CIRCUIT_OPEN: 503,
}


class ErrorDetail(BaseModel):
    """Structured error response body."""

    code: str
    message: str
    details: Optional[dict[str, Any]] = None
    request_id: str


class ErrorResponse(BaseModel):
    """Top-level error envelope."""

    error: ErrorDetail


class VOS3Error(HTTPException):
    """Structured API error that produces a consistent JSON response."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ):
        self.error_code = code
        self.error_message = message
        self.error_details = details
        self.request_id = request_id or str(uuid4())
        status = _CODE_TO_STATUS.get(code, 500)
        super().__init__(status_code=status, detail=message)


def vos3_error_response(exc: VOS3Error) -> JSONResponse:
    """Convert a VOS3Error into a structured JSONResponse."""
    body = {
        "error": {
            "code": exc.error_code.value,
            "message": exc.error_message,
            "request_id": exc.request_id,
        }
    }
    if exc.error_details:
        body["error"]["details"] = exc.error_details
    return JSONResponse(status_code=exc.status_code, content=body)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def not_found(resource: str, identifier: Any = None) -> VOS3Error:
    """Raise a NOT_FOUND error for a resource."""
    details = {"resource": resource}
    if identifier is not None:
        details["id"] = str(identifier)
    return VOS3Error(ErrorCode.NOT_FOUND, f"{resource} not found", details)


def validation_error(message: str, field: Optional[str] = None) -> VOS3Error:
    """Raise a VALIDATION_ERROR."""
    details = {}
    if field:
        details["field"] = field
    return VOS3Error(ErrorCode.VALIDATION_ERROR, message, details or None)


def forbidden(message: str = "Insufficient permissions") -> VOS3Error:
    """Raise a FORBIDDEN error."""
    return VOS3Error(ErrorCode.FORBIDDEN, message)


def upstream_error(service: str, message: str) -> VOS3Error:
    """Raise an UPSTREAM_ERROR for a failed external service call."""
    return VOS3Error(ErrorCode.UPSTREAM_ERROR, message, {"service": service})


def rate_limited(retry_after: Optional[int] = None) -> VOS3Error:
    """Raise a RATE_LIMITED error."""
    details = {}
    if retry_after is not None:
        details["retry_after_seconds"] = retry_after
    return VOS3Error(ErrorCode.RATE_LIMITED, "Rate limit exceeded", details or None)
