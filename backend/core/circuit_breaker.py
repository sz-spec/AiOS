"""
Circuit Breaker for External Services
======================================

Prevents cascading failures during LLM provider or database outages.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Service is down, fail fast for cooldown_seconds
- HALF_OPEN: After cooldown, allow one probe request to test recovery

Usage:
    breaker = CircuitBreaker("openai", failure_threshold=5, cooldown_seconds=30)

    async def call_openai():
        if not breaker.allow_request():
            raise ServiceUnavailableError("openai circuit open")
        try:
            result = await openai_client.chat(...)
            breaker.record_success()
            return result
        except Exception as e:
            breaker.record_failure()
            raise
"""

import time
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when a circuit breaker is OPEN and fast-failing a request.

    Carries enough metadata for the API layer to return a structured 503
    with Retry-After header and a feature-scoped error body.
    """

    def __init__(self, service: str, retry_after: float, function_name: str = ""):
        self.service = service
        self.retry_after = retry_after  # seconds until HALF_OPEN probe
        self.function_name = function_name
        super().__init__(
            f"Circuit breaker OPEN for {service!r} — retry in {retry_after:.0f}s"
        )


class CircuitBreaker:
    """Simple circuit breaker: N consecutive failures -> cooldown -> half-open probe."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time: Optional[float] = None
        self._total_trips = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if self._last_failure_time is not None:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.cooldown_seconds:
                    self._state = CircuitState.HALF_OPEN
        return self._state

    def allow_request(self) -> bool:
        """Returns True if a request should be allowed through."""
        current = self.state
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.HALF_OPEN:
            return True  # Allow one probe
        return False  # OPEN -> fast-fail

    def retry_after_seconds(self) -> float:
        """Seconds until the breaker enters HALF_OPEN (may probe again)."""
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            elapsed = time.monotonic() - self._last_failure_time
            return max(0.0, self.cooldown_seconds - elapsed)
        return 0.0

    def record_success(self) -> None:
        """Record a successful request. Resets the breaker to CLOSED."""
        if self._state != CircuitState.CLOSED:
            logger.info("CircuitBreaker[%s]: recovered, closing circuit", self.name)
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """Record a failed request. May trip the breaker to OPEN."""
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            # Probe failed, reopen
            self._state = CircuitState.OPEN
            logger.warning(
                "CircuitBreaker[%s]: half-open probe failed, reopening", self.name
            )
        elif self._consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._total_trips += 1
            logger.warning(
                "CircuitBreaker[%s]: tripped after %d failures (trip #%d)",
                self.name,
                self._consecutive_failures,
                self._total_trips,
            )

    def reset(self) -> None:
        """Manually reset the breaker to CLOSED."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time = None

    def status(self) -> dict:
        """Return breaker status for monitoring."""
        return {
            "name": self.name,
            "state": self.state.value,
            "consecutive_failures": self._consecutive_failures,
            "total_trips": self._total_trips,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }


# ---------------------------------------------------------------------------
# Global breaker registry
# ---------------------------------------------------------------------------

_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    failure_threshold: int = 5,
    cooldown_seconds: float = 30.0,
) -> CircuitBreaker:
    """Get or create a named circuit breaker."""
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )
    return _breakers[name]


def all_breaker_statuses() -> list[dict]:
    """Return status of all registered breakers."""
    return [b.status() for b in _breakers.values()]


__all__ = [
    "CircuitState",
    "CircuitBreakerOpenError",
    "CircuitBreaker",
    "get_breaker",
    "all_breaker_statuses",
]
