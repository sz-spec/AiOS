"""
Observability Module for Router & Efficiency Tracking
======================================================
Tracks model selections, costs, response times, and usage patterns.
Persists data to DevMemory for survival across restarts.
"""

import time
import json
import logging
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)


# Memory persistence helper
def _get_memory():
    """Get DevMemory instance for persistence."""
    try:
        from memory.dev_memory import get_dev_memory

        return get_dev_memory()
    except Exception as e:
        logger.warning(f"Could not get DevMemory: {e}")
        return None


# Cost estimates per 1K tokens (input/output)
MODEL_COSTS = {
    "claude-opus": {"input": 0.015, "output": 0.075},
    "claude-sonnet": {"input": 0.003, "output": 0.015},
    "gpt": {"input": 0.005, "output": 0.015},
    "gemini": {"input": 0.00025, "output": 0.0005},
}


@dataclass
class RequestMetric:
    """Single request metric."""

    timestamp: datetime
    role: str
    complexity: int
    model_selected: str
    response_time_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    success: bool = True
    fallback_used: bool = False


@dataclass
class ErrorRecord:
    """Tracked error/bug record."""

    timestamp: datetime
    error_type: str
    message: str
    source: str  # route/module that caused the error
    severity: str  # critical, error, warning
    stack_trace: Optional[str] = None
    resolved: bool = False


class ObservabilityTracker:
    """
    Tracks and aggregates metrics for model routing decisions.

    Usage:
        tracker = ObservabilityTracker()

        # Track a request
        with tracker.track_request("coding", 5, "claude-sonnet") as req:
            response = call_model(...)
            req.tokens_in = 100
            req.tokens_out = 200

        # Get metrics
        metrics = tracker.get_metrics()

        # Track an error
        tracker.record_error("ValueError", "Invalid input", "chat_routes", "error")
    """

    def __init__(self, max_history: int = 10000):
        self.max_history = max_history
        self._requests: list[RequestMetric] = []
        self._errors: list[ErrorRecord] = []
        self._lock = Lock()

        # Aggregated counters
        self._model_counts: dict[str, int] = defaultdict(int)
        self._role_counts: dict[str, int] = defaultdict(int)
        self._total_cost: float = 0.0
        self._total_tokens_in: int = 0
        self._total_tokens_out: int = 0
        self._fallback_count: int = 0
        self._error_count: int = 0

        # Service health tracking
        self._service_status: dict[str, dict] = {
            "backend": {"status": "healthy", "last_check": datetime.now().isoformat()},
            "memory": {"status": "unknown", "last_check": None},
            "router": {"status": "unknown", "last_check": None},
        }

        # Load persisted data from memory
        self._load_from_memory()

    def _load_from_memory(self):
        """Load persisted metrics from DevMemory."""
        try:
            memory = _get_memory()
            if not memory:
                return

            # Load metrics summary
            results = memory.query(
                "observability metrics summary", top_k=1, memory_type="context"
            )
            if results:
                for r in results:
                    if "metrics_data" in r.get("content", ""):
                        try:
                            data = json.loads(r["metadata"].get("data", "{}"))
                            self._total_cost = data.get("total_cost", 0.0)
                            self._total_tokens_in = data.get("total_tokens_in", 0)
                            self._total_tokens_out = data.get("total_tokens_out", 0)
                            self._fallback_count = data.get("fallback_count", 0)
                            self._error_count = data.get("error_count", 0)
                            self._model_counts = defaultdict(
                                int, data.get("model_counts", {})
                            )
                            self._role_counts = defaultdict(
                                int, data.get("role_counts", {})
                            )
                            logger.info(
                                f"Loaded metrics from memory: {data.get('total_requests', 0)} requests"
                            )
                        except json.JSONDecodeError:
                            pass

            # Load recent requests
            results = memory.query(
                "observability request metric", top_k=100, memory_type="context"
            )
            for r in results:
                if "request_metric" in r.get("content", ""):
                    try:
                        data = json.loads(r["metadata"].get("data", "{}"))
                        metric = RequestMetric(
                            timestamp=datetime.fromisoformat(data["timestamp"]),
                            role=data["role"],
                            complexity=data["complexity"],
                            model_selected=data["model_selected"],
                            response_time_ms=data["response_time_ms"],
                            tokens_in=data.get("tokens_in", 0),
                            tokens_out=data.get("tokens_out", 0),
                            cost=data.get("cost", 0.0),
                            success=data.get("success", True),
                            fallback_used=data.get("fallback_used", False),
                        )
                        self._requests.append(metric)
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass

            # Load errors
            results = memory.query(
                "observability error record", top_k=100, memory_type="error"
            )
            for r in results:
                if "error_record" in r.get("content", ""):
                    try:
                        data = json.loads(r["metadata"].get("data", "{}"))
                        error = ErrorRecord(
                            timestamp=datetime.fromisoformat(data["timestamp"]),
                            error_type=data["error_type"],
                            message=data["message"],
                            source=data["source"],
                            severity=data.get("severity", "error"),
                            stack_trace=data.get("stack_trace"),
                            resolved=data.get("resolved", False),
                        )
                        self._errors.append(error)
                    except (json.JSONDecodeError, KeyError, ValueError):
                        pass

            if self._requests:
                logger.info(f"Restored {len(self._requests)} requests from memory")
            if self._errors:
                logger.info(f"Restored {len(self._errors)} errors from memory")

        except Exception as e:
            logger.warning(f"Failed to load from memory: {e}")

    def _save_metrics_summary(self):
        """Save aggregated metrics to memory."""
        try:
            memory = _get_memory()
            if not memory:
                return

            data = {
                "total_requests": len(self._requests),
                "total_cost": self._total_cost,
                "total_tokens_in": self._total_tokens_in,
                "total_tokens_out": self._total_tokens_out,
                "fallback_count": self._fallback_count,
                "error_count": self._error_count,
                "model_counts": dict(self._model_counts),
                "role_counts": dict(self._role_counts),
            }

            memory.add(
                content="observability metrics_data summary",
                memory_type="context",
                metadata={"data": json.dumps(data), "type": "metrics_summary"},
            )
        except Exception as e:
            logger.warning(f"Failed to save metrics summary: {e}")

    def _save_request_to_memory(self, metric: RequestMetric):
        """Save a single request metric to memory."""
        try:
            memory = _get_memory()
            if not memory:
                return

            data = {
                "timestamp": metric.timestamp.isoformat(),
                "role": metric.role,
                "complexity": metric.complexity,
                "model_selected": metric.model_selected,
                "response_time_ms": metric.response_time_ms,
                "tokens_in": metric.tokens_in,
                "tokens_out": metric.tokens_out,
                "cost": metric.cost,
                "success": metric.success,
                "fallback_used": metric.fallback_used,
            }

            memory.add(
                content=f"observability request_metric {metric.role} {metric.model_selected}",
                memory_type="context",
                metadata={"data": json.dumps(data), "type": "request_metric"},
            )
        except Exception as e:
            logger.warning(f"Failed to save request to memory: {e}")

    def _save_error_to_memory(self, error: ErrorRecord):
        """Save an error record to memory."""
        try:
            memory = _get_memory()
            if not memory:
                return

            data = {
                "timestamp": error.timestamp.isoformat(),
                "error_type": error.error_type,
                "message": error.message,
                "source": error.source,
                "severity": error.severity,
                "stack_trace": error.stack_trace,
                "resolved": error.resolved,
            }

            memory.add(
                content=f"observability error_record {error.severity} {error.error_type} in {error.source}",
                memory_type="error",
                metadata={"data": json.dumps(data), "type": "error_record"},
            )
        except Exception as e:
            logger.warning(f"Failed to save error to memory: {e}")

    def track_request(self, role: str, complexity: int, model: str):
        """Context manager for tracking a request."""
        return _RequestTracker(self, role, complexity, model)

    def record(self, metric: RequestMetric):
        """Record a completed request metric."""
        with self._lock:
            # Calculate cost
            costs = MODEL_COSTS.get(metric.model_selected, {"input": 0, "output": 0})
            metric.cost = (metric.tokens_in / 1000) * costs["input"] + (
                metric.tokens_out / 1000
            ) * costs["output"]

            # Update aggregates
            self._model_counts[metric.model_selected] += 1
            self._role_counts[metric.role] += 1
            self._total_cost += metric.cost
            self._total_tokens_in += metric.tokens_in
            self._total_tokens_out += metric.tokens_out

            if metric.fallback_used:
                self._fallback_count += 1
            if not metric.success:
                self._error_count += 1

            # Store request
            self._requests.append(metric)

            # Trim history if needed
            if len(self._requests) > self.max_history:
                self._requests = self._requests[-self.max_history :]

            # Log the request
            logger.info(
                f"[Observability] {metric.model_selected} | "
                f"role={metric.role} complexity={metric.complexity} | "
                f"{metric.response_time_ms:.0f}ms | "
                f"${metric.cost:.4f}"
            )

            # Persist to memory
            self._save_request_to_memory(metric)
            self._save_metrics_summary()

    def get_metrics(self) -> dict:
        """Get aggregated metrics."""
        with self._lock:
            total_requests = len(self._requests)

            # Calculate averages
            avg_response_time = 0.0
            avg_complexity = 0.0
            if total_requests > 0:
                avg_response_time = (
                    sum(r.response_time_ms for r in self._requests) / total_requests
                )
                avg_complexity = (
                    sum(r.complexity for r in self._requests) / total_requests
                )

            # Recent requests (last hour)
            one_hour_ago = datetime.now() - timedelta(hours=1)
            recent = [r for r in self._requests if r.timestamp > one_hour_ago]

            return {
                "summary": {
                    "total_requests": total_requests,
                    "total_cost": round(self._total_cost, 4),
                    "total_tokens_in": self._total_tokens_in,
                    "total_tokens_out": self._total_tokens_out,
                    "avg_response_time_ms": round(avg_response_time, 2),
                    "avg_complexity": round(avg_complexity, 2),
                    "error_rate": round(
                        self._error_count / max(total_requests, 1) * 100, 2
                    ),
                    "fallback_rate": round(
                        self._fallback_count / max(total_requests, 1) * 100, 2
                    ),
                },
                "by_model": dict(self._model_counts),
                "by_role": dict(self._role_counts),
                "last_hour": {
                    "requests": len(recent),
                    "cost": round(sum(r.cost for r in recent), 4),
                },
                "recent_requests": [
                    {
                        "timestamp": r.timestamp.isoformat(),
                        "role": r.role,
                        "complexity": r.complexity,
                        "model": r.model_selected,
                        "response_time_ms": r.response_time_ms,
                        "cost": round(r.cost, 4),
                        "success": r.success,
                    }
                    for r in self._requests[-20:]  # Last 20 requests
                ],
            }

    def get_cost_breakdown(self) -> dict:
        """Get cost breakdown by model and role."""
        with self._lock:
            by_model = defaultdict(float)
            by_role = defaultdict(float)

            for r in self._requests:
                by_model[r.model_selected] += r.cost
                by_role[r.role] += r.cost

            return {
                "by_model": {k: round(v, 4) for k, v in by_model.items()},
                "by_role": {k: round(v, 4) for k, v in by_role.items()},
                "total": round(self._total_cost, 4),
            }

    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self._requests.clear()
            self._model_counts.clear()
            self._role_counts.clear()
            self._total_cost = 0.0
            self._total_tokens_in = 0
            self._total_tokens_out = 0
            self._fallback_count = 0
            self._error_count = 0

    def record_error(
        self,
        error_type: str,
        message: str,
        source: str,
        severity: str = "error",
        stack_trace: Optional[str] = None,
    ):
        """Record an error/bug."""
        with self._lock:
            error = ErrorRecord(
                timestamp=datetime.now(),
                error_type=error_type,
                message=message,
                source=source,
                severity=severity,
                stack_trace=stack_trace,
            )
            self._errors.append(error)

            # Trim history
            if len(self._errors) > self.max_history:
                self._errors = self._errors[-self.max_history :]

            logger.error(
                f"[Bug Tracked] {severity.upper()}: {error_type} in {source} - {message}"
            )

            # Persist to memory
            self._save_error_to_memory(error)

    def get_errors(self, limit: int = 50, include_resolved: bool = False) -> list:
        """Get recent errors."""
        with self._lock:
            errors = (
                self._errors
                if include_resolved
                else [e for e in self._errors if not e.resolved]
            )
            return [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "error_type": e.error_type,
                    "message": e.message,
                    "source": e.source,
                    "severity": e.severity,
                    "stack_trace": e.stack_trace,
                    "resolved": e.resolved,
                }
                for e in errors[-limit:]
            ]

    def update_service_status(
        self, service: str, status: str, details: Optional[dict] = None
    ):
        """Update service health status."""
        with self._lock:
            self._service_status[service] = {
                "status": status,
                "last_check": datetime.now().isoformat(),
                **(details or {}),
            }

    def get_health(self) -> dict:
        """Get system health status."""
        with self._lock:
            # Count errors by severity in last hour
            one_hour_ago = datetime.now() - timedelta(hours=1)
            recent_errors = [e for e in self._errors if e.timestamp > one_hour_ago]

            error_counts = {"critical": 0, "error": 0, "warning": 0}
            for e in recent_errors:
                if e.severity in error_counts:
                    error_counts[e.severity] += 1

            # Determine overall health
            if error_counts["critical"] > 0:
                overall_status = "critical"
            elif error_counts["error"] > 5:
                overall_status = "degraded"
            elif error_counts["warning"] > 10:
                overall_status = "warning"
            else:
                overall_status = "healthy"

            return {
                "status": overall_status,
                "timestamp": datetime.now().isoformat(),
                "services": self._service_status,
                "errors_last_hour": error_counts,
                "total_errors": len(self._errors),
                "unresolved_errors": len([e for e in self._errors if not e.resolved]),
            }

    def resolve_error(self, index: int) -> bool:
        """Mark an error as resolved."""
        with self._lock:
            if 0 <= index < len(self._errors):
                self._errors[index].resolved = True
                return True
            return False

    # ------------------------------------------------------------------
    # Spending Caps (Phase I2)
    # ------------------------------------------------------------------

    def check_budget(self, model: str, estimated_tokens: int) -> bool:
        """Check if a request is within spending budget.

        Uses sliding window tracking for per-request, per-minute, and
        per-hour caps.

        Args:
            model: Model name (e.g. 'claude-opus').
            estimated_tokens: Estimated total tokens (in + out).

        Returns:
            True if within budget, False if budget exceeded.
        """
        # Caps (configurable via class attributes)
        per_request_max_usd = getattr(self, "per_request_max_usd", 0.50)
        per_minute_max_usd = getattr(self, "per_minute_max_usd", 5.00)
        per_hour_max_usd = getattr(self, "per_hour_max_usd", 50.00)

        # Estimate cost for this request
        costs = MODEL_COSTS.get(model, {"input": 0.003, "output": 0.015})
        # Assume 40% input, 60% output split
        est_cost = (estimated_tokens * 0.4 / 1000) * costs["input"] + (
            estimated_tokens * 0.6 / 1000
        ) * costs["output"]

        # Per-request check
        if est_cost > per_request_max_usd:
            logger.warning(
                f"[Budget] Request rejected: estimated ${est_cost:.4f} > "
                f"per_request cap ${per_request_max_usd:.2f}"
            )
            return False

        with self._lock:
            now = datetime.now()

            # Per-minute sliding window
            one_min_ago = now - timedelta(minutes=1)
            minute_cost = sum(
                r.cost for r in self._requests if r.timestamp > one_min_ago
            )
            if minute_cost + est_cost > per_minute_max_usd:
                logger.warning(
                    f"[Budget] Minute cap exceeded: ${minute_cost:.4f} + "
                    f"${est_cost:.4f} > ${per_minute_max_usd:.2f}"
                )
                return False

            # Per-hour sliding window
            one_hour_ago = now - timedelta(hours=1)
            hour_cost = sum(
                r.cost for r in self._requests if r.timestamp > one_hour_ago
            )
            if hour_cost + est_cost > per_hour_max_usd:
                logger.warning(
                    f"[Budget] Hour cap exceeded: ${hour_cost:.4f} + "
                    f"${est_cost:.4f} > ${per_hour_max_usd:.2f}"
                )
                return False

        return True

    # ------------------------------------------------------------------
    # Audit Trail (Phase I9)
    # ------------------------------------------------------------------

    def log_tool_call(
        self,
        tool_name: str,
        agent_role: str,
        args: dict | None = None,
        result: str | None = None,
    ):
        """Log a tool call for audit trail.

        Also performs anomaly detection: alerts if >30 calls/min from
        same agent role.
        """
        now = datetime.now()
        entry = {
            "timestamp": now.isoformat(),
            "tool": tool_name,
            "agent_role": agent_role,
            "args_summary": str(args)[:200] if args else "",
            "result_summary": str(result)[:200] if result else "",
        }
        logger.info(f"[ToolAudit] {agent_role} -> {tool_name}")

        # Anomaly detection: count recent calls from this role
        with self._lock:
            if not hasattr(self, "_tool_calls"):
                self._tool_calls: list[dict] = []
            self._tool_calls.append(entry)

            # Keep only last 5 minutes
            cutoff = now - timedelta(minutes=5)
            self._tool_calls = [
                c
                for c in self._tool_calls
                if datetime.fromisoformat(c["timestamp"]) > cutoff
            ]

            # Check rate: >30 calls/min from same role
            one_min_ago = now - timedelta(minutes=1)
            recent_from_role = sum(
                1
                for c in self._tool_calls
                if c["agent_role"] == agent_role
                and datetime.fromisoformat(c["timestamp"]) > one_min_ago
            )
            if recent_from_role > 30:
                logger.warning(
                    f"[Anomaly] Agent '{agent_role}' made {recent_from_role} "
                    f"tool calls in the last minute (threshold: 30)"
                )


class _RequestTracker:
    """Context manager for tracking individual requests."""

    def __init__(
        self, tracker: ObservabilityTracker, role: str, complexity: int, model: str
    ):
        self.tracker = tracker
        self.metric = RequestMetric(
            timestamp=datetime.now(),
            role=role,
            complexity=complexity,
            model_selected=model,
            response_time_ms=0.0,
        )
        self._start_time: float = 0.0

    def __enter__(self):
        self._start_time = time.perf_counter()
        return self.metric

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.metric.response_time_ms = (time.perf_counter() - self._start_time) * 1000
        if exc_type is not None:
            self.metric.success = False
        self.tracker.record(self.metric)
        return False  # Don't suppress exceptions


# Global tracker instance
tracker = ObservabilityTracker()


# Convenience functions
def track_request(role: str, complexity: int, model: str):
    """Track a model request."""
    return tracker.track_request(role, complexity, model)


def get_metrics() -> dict:
    """Get current metrics."""
    return tracker.get_metrics()


def get_cost_breakdown() -> dict:
    """Get cost breakdown."""
    return tracker.get_cost_breakdown()


def reset_metrics():
    """Reset all metrics."""
    tracker.reset()


def record_error(
    error_type: str,
    message: str,
    source: str,
    severity: str = "error",
    stack_trace: Optional[str] = None,
):
    """Record an error/bug."""
    tracker.record_error(error_type, message, source, severity, stack_trace)


def get_errors(limit: int = 50, include_resolved: bool = False) -> list:
    """Get recent errors."""
    return tracker.get_errors(limit, include_resolved)


def get_health() -> dict:
    """Get system health status."""
    return tracker.get_health()


def update_service_status(service: str, status: str, details: Optional[dict] = None):
    """Update service health status."""
    tracker.update_service_status(service, status, details)


def check_budget(model: str, estimated_tokens: int) -> bool:
    """Check if a request is within spending budget."""
    return tracker.check_budget(model, estimated_tokens)


def log_tool_call(
    tool_name: str, agent_role: str, args: dict | None = None, result: str | None = None
):
    """Log a tool call for audit trail."""
    tracker.log_tool_call(tool_name, agent_role, args, result)


__all__ = [
    "ObservabilityTracker",
    "RequestMetric",
    "ErrorRecord",
    "tracker",
    "track_request",
    "get_metrics",
    "get_cost_breakdown",
    "reset_metrics",
    "record_error",
    "get_errors",
    "get_health",
    "update_service_status",
    "check_budget",
    "log_tool_call",
    "MODEL_COSTS",
]
