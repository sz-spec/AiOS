"""
Circuit Breaker for Kernel Bridge
==================================

Prevents cascading failures by tracking bridge communication errors
and temporarily disabling calls when the failure threshold is exceeded.

States:
  CLOSED    - Normal operation, calls pass through
  OPEN      - Failures exceeded threshold, calls rejected immediately
  HALF_OPEN - Recovery window, limited calls to test if bridge is back

Usage:
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
    result = await cb.call(bridge._send_command, "PING")
"""

import time
import logging

logger = logging.getLogger("vos3.bridge.circuit_breaker")


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open."""

    pass


class CircuitBreaker:
    """Circuit breaker for async bridge calls.

    After `failure_threshold` consecutive failures, transitions to OPEN
    state and rejects all calls for `recovery_timeout` seconds. Then
    transitions to HALF_OPEN and allows limited calls to test recovery.
    Two consecutive successes in HALF_OPEN close the circuit.
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 30):
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.failure_count = 0
        self.last_failure_time: float | None = None
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_successes = 0

    async def call(self, func, *args, **kwargs):
        """Execute a function through the circuit breaker.

        Args:
            func: Async callable to execute.
            *args, **kwargs: Passed to func.

        Returns:
            Result of func(*args, **kwargs).

        Raises:
            CircuitOpenError: If circuit is OPEN and recovery timeout
                has not elapsed.
        """
        if self.state == "OPEN":
            if (
                self.last_failure_time is not None
                and time.time() - self.last_failure_time > self.recovery_timeout
            ):
                logger.info("Circuit breaker transitioning to HALF_OPEN")
                self.state = "HALF_OPEN"
                self.half_open_successes = 0
            else:
                raise CircuitOpenError(
                    "Bridge circuit open — kernel bridge temporarily unavailable"
                )

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except CircuitOpenError:
            raise
        except Exception:
            self._on_failure()
            raise

    def _on_success(self):
        """Record a successful call."""
        if self.state == "HALF_OPEN":
            self.half_open_successes += 1
            if self.half_open_successes >= 2:
                logger.info("Circuit breaker CLOSED (recovered)")
                self.state = "CLOSED"
                self.failure_count = 0
        else:
            self.failure_count = 0

    def _on_failure(self):
        """Record a failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            logger.warning(f"Circuit breaker OPEN after {self.failure_count} failures")
            self.state = "OPEN"

    @property
    def is_open(self) -> bool:
        """Whether the circuit breaker is in OPEN state."""
        return self.state == "OPEN"

    @property
    def is_closed(self) -> bool:
        """Whether the circuit breaker is in CLOSED state."""
        return self.state == "CLOSED"

    def reset(self):
        """Manually reset the circuit breaker to CLOSED."""
        self.state = "CLOSED"
        self.failure_count = 0
        self.last_failure_time = None
        self.half_open_successes = 0
        logger.info("Circuit breaker manually reset to CLOSED")
