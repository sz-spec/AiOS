"""
Tests for Observability Module
===============================

Covers: RequestMetric, ObservabilityTracker, track_request context manager,
        get_metrics, get_cost_breakdown, record_error, get_errors,
        get_health, resolve_error
"""

import pytest
from unittest.mock import patch
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tracker():
    """Fresh ObservabilityTracker with no memory persistence."""
    with patch("src.observability._get_memory", return_value=None):
        from src.observability import ObservabilityTracker

        t = ObservabilityTracker(max_history=100)
    return t


@pytest.fixture
def metric():
    """A sample RequestMetric."""
    from src.observability import RequestMetric

    return RequestMetric(
        timestamp=datetime.now(),
        role="coding",
        complexity=5,
        model_selected="claude-sonnet",
        response_time_ms=150.0,
        tokens_in=100,
        tokens_out=200,
    )


# ---------------------------------------------------------------------------
# RequestMetric
# ---------------------------------------------------------------------------


class TestRequestMetric:
    def test_metric_creation(self, metric):
        assert metric.role == "coding"
        assert metric.complexity == 5
        assert metric.model_selected == "claude-sonnet"

    def test_metric_defaults(self):
        from src.observability import RequestMetric

        m = RequestMetric(
            timestamp=datetime.now(),
            role="test",
            complexity=1,
            model_selected="gpt",
            response_time_ms=10.0,
        )
        assert m.tokens_in == 0
        assert m.tokens_out == 0
        assert m.cost == 0.0
        assert m.success is True
        assert m.fallback_used is False

    def test_metric_cost_calculation(self, tracker, metric):
        """Cost should be calculated when recorded."""
        tracker.record(metric)
        # claude-sonnet: input=0.003, output=0.015 per 1K
        expected_cost = (100 / 1000) * 0.003 + (200 / 1000) * 0.015
        assert metric.cost == pytest.approx(expected_cost, abs=0.0001)

    def test_metric_cost_unknown_model(self, tracker):
        from src.observability import RequestMetric

        m = RequestMetric(
            timestamp=datetime.now(),
            role="test",
            complexity=1,
            model_selected="unknown-model",
            response_time_ms=10.0,
            tokens_in=1000,
            tokens_out=1000,
        )
        tracker.record(m)
        assert m.cost == 0.0  # Unknown model has zero cost


# ---------------------------------------------------------------------------
# track_request context manager
# ---------------------------------------------------------------------------


class TestTrackRequest:
    def test_track_request_success(self, tracker):
        with tracker.track_request("coding", 5, "claude-sonnet") as req:
            req.tokens_in = 50
            req.tokens_out = 100
        assert len(tracker._requests) == 1
        assert tracker._requests[0].success is True

    def test_track_request_measures_time(self, tracker):
        import time

        with tracker.track_request("coding", 3, "gpt"):
            time.sleep(0.01)
        assert tracker._requests[0].response_time_ms >= 5  # At least 5ms

    def test_track_request_failure(self, tracker):
        with pytest.raises(ValueError):
            with tracker.track_request("coding", 5, "claude-sonnet"):
                raise ValueError("test error")
        assert len(tracker._requests) == 1
        assert tracker._requests[0].success is False

    def test_track_request_increments_model_count(self, tracker):
        with tracker.track_request("coding", 3, "gpt"):
            pass
        assert tracker._model_counts["gpt"] == 1

    def test_track_request_increments_role_count(self, tracker):
        with tracker.track_request("architect", 7, "gpt"):
            pass
        assert tracker._role_counts["architect"] == 1

    def test_track_fallback(self, tracker):
        with tracker.track_request("coding", 5, "gpt") as req:
            req.fallback_used = True
        assert tracker._fallback_count == 1


# ---------------------------------------------------------------------------
# get_metrics
# ---------------------------------------------------------------------------


class TestGetMetrics:
    def test_empty_metrics(self, tracker):
        metrics = tracker.get_metrics()
        assert metrics["summary"]["total_requests"] == 0
        assert metrics["summary"]["total_cost"] == 0.0

    def test_metrics_after_requests(self, tracker, metric):
        tracker.record(metric)
        metrics = tracker.get_metrics()
        assert metrics["summary"]["total_requests"] == 1
        assert metrics["summary"]["total_cost"] > 0

    def test_metrics_by_model(self, tracker, metric):
        tracker.record(metric)
        metrics = tracker.get_metrics()
        assert "claude-sonnet" in metrics["by_model"]
        assert metrics["by_model"]["claude-sonnet"] == 1

    def test_metrics_by_role(self, tracker, metric):
        tracker.record(metric)
        metrics = tracker.get_metrics()
        assert "coding" in metrics["by_role"]

    def test_metrics_avg_response_time(self, tracker):
        from src.observability import RequestMetric

        for ms in [100, 200, 300]:
            m = RequestMetric(
                timestamp=datetime.now(),
                role="test",
                complexity=1,
                model_selected="gpt",
                response_time_ms=ms,
            )
            tracker.record(m)
        metrics = tracker.get_metrics()
        assert metrics["summary"]["avg_response_time_ms"] == pytest.approx(200.0, abs=1)

    def test_metrics_recent_requests_capped_at_20(self, tracker):
        from src.observability import RequestMetric

        for i in range(25):
            m = RequestMetric(
                timestamp=datetime.now(),
                role="test",
                complexity=1,
                model_selected="gpt",
                response_time_ms=10.0,
            )
            tracker.record(m)
        metrics = tracker.get_metrics()
        assert len(metrics["recent_requests"]) == 20

    def test_metrics_last_hour(self, tracker):
        from src.observability import RequestMetric

        # Add a recent request
        m = RequestMetric(
            timestamp=datetime.now(),
            role="test",
            complexity=1,
            model_selected="gpt",
            response_time_ms=10.0,
            tokens_in=100,
            tokens_out=100,
        )
        tracker.record(m)
        metrics = tracker.get_metrics()
        assert metrics["last_hour"]["requests"] == 1


# ---------------------------------------------------------------------------
# get_cost_breakdown
# ---------------------------------------------------------------------------


class TestCostBreakdown:
    def test_empty_breakdown(self, tracker):
        breakdown = tracker.get_cost_breakdown()
        assert breakdown["total"] == 0.0

    def test_breakdown_by_model(self, tracker, metric):
        tracker.record(metric)
        breakdown = tracker.get_cost_breakdown()
        assert "claude-sonnet" in breakdown["by_model"]
        assert breakdown["by_model"]["claude-sonnet"] > 0

    def test_breakdown_by_role(self, tracker, metric):
        tracker.record(metric)
        breakdown = tracker.get_cost_breakdown()
        assert "coding" in breakdown["by_role"]


# ---------------------------------------------------------------------------
# record_error / get_errors
# ---------------------------------------------------------------------------


class TestErrorTracking:
    def test_record_error(self, tracker):
        tracker.record_error("ValueError", "bad input", "chat_routes", "error")
        assert len(tracker._errors) == 1

    def test_get_errors_excludes_resolved(self, tracker):
        tracker.record_error("E1", "msg1", "src1", "error")
        tracker.record_error("E2", "msg2", "src2", "warning")
        tracker._errors[0].resolved = True
        errors = tracker.get_errors()
        assert len(errors) == 1
        assert errors[0]["error_type"] == "E2"

    def test_get_errors_include_resolved(self, tracker):
        tracker.record_error("E1", "msg1", "src1", "error")
        tracker._errors[0].resolved = True
        errors = tracker.get_errors(include_resolved=True)
        assert len(errors) == 1

    def test_get_errors_limit(self, tracker):
        for i in range(10):
            tracker.record_error(f"E{i}", f"msg{i}", "src", "error")
        errors = tracker.get_errors(limit=5)
        assert len(errors) == 5

    def test_resolve_error(self, tracker):
        tracker.record_error("E1", "msg1", "src1", "error")
        assert tracker.resolve_error(0) is True
        assert tracker._errors[0].resolved is True

    def test_resolve_error_invalid_index(self, tracker):
        assert tracker.resolve_error(999) is False

    def test_error_has_stack_trace(self, tracker):
        tracker.record_error("E1", "msg", "src", "critical", stack_trace="line 42")
        errors = tracker.get_errors()
        assert errors[0]["stack_trace"] == "line 42"


# ---------------------------------------------------------------------------
# get_health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_healthy_status(self, tracker):
        health = tracker.get_health()
        assert health["status"] == "healthy"

    def test_critical_status(self, tracker):
        tracker.record_error("E1", "crash", "core", "critical")
        health = tracker.get_health()
        assert health["status"] == "critical"

    def test_degraded_status(self, tracker):
        for i in range(6):
            tracker.record_error(f"E{i}", f"err{i}", "src", "error")
        health = tracker.get_health()
        assert health["status"] == "degraded"

    def test_health_services(self, tracker):
        health = tracker.get_health()
        assert "backend" in health["services"]

    def test_health_unresolved_count(self, tracker):
        tracker.record_error("E1", "msg", "src", "error")
        tracker.record_error("E2", "msg", "src", "error")
        tracker.resolve_error(0)
        health = tracker.get_health()
        assert health["unresolved_errors"] == 1


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_everything(self, tracker, metric):
        tracker.record(metric)
        tracker.record_error("E1", "msg", "src", "error")
        tracker.reset()
        metrics = tracker.get_metrics()
        assert metrics["summary"]["total_requests"] == 0
        assert metrics["summary"]["total_cost"] == 0.0


# ---------------------------------------------------------------------------
# History trimming
# ---------------------------------------------------------------------------


class TestHistoryTrimming:
    def test_requests_trimmed_at_max(self):
        with patch("src.observability._get_memory", return_value=None):
            from src.observability import ObservabilityTracker, RequestMetric

            t = ObservabilityTracker(max_history=5)
        for i in range(10):
            m = RequestMetric(
                timestamp=datetime.now(),
                role="test",
                complexity=1,
                model_selected="gpt",
                response_time_ms=10.0,
            )
            t.record(m)
        assert len(t._requests) == 5
