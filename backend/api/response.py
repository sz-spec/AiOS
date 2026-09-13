"""Standardized API response envelope."""

from typing import Any, Optional
from pydantic import BaseModel


class ApiResponse(BaseModel):
    """Standard response wrapper for all API endpoints.

    Usage:
        return ApiResponse(data={"user": user_dict})
        return ApiResponse(error="Not found", status="error")
    """

    status: str = "ok"
    data: Optional[Any] = None
    error: Optional[str] = None

    @classmethod
    def success(cls, data: Any = None) -> "ApiResponse":
        return cls(status="ok", data=data)

    @classmethod
    def fail(cls, error: str, status: str = "error") -> "ApiResponse":
        return cls(status=status, error=error)
