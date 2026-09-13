"""
Performance Benchmark Regression Tests
========================================
Micro-benchmarks for hot-path functions.  Uses pytest-benchmark.

Run (benchmarks require sequential execution):
    pytest tests/perf/test_benchmark_regression.py -p no:xdist --benchmark-enable -v
"""

import json
import time

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bucket(rate=1000.0, burst=10_000):
    from middleware.rate_limit import TokenBucket

    return TokenBucket(rate=rate, burst=burst)


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
class TestBenchmarkRegression:
    """pytest-benchmark regression tests for critical-path functions."""

    # 1. TokenBucket.allow() throughput -------------------------------------
    def test_token_bucket_allow_throughput(self, benchmark):
        """Single TokenBucket.allow() call latency."""
        bucket = _make_bucket()
        benchmark(bucket.allow, "bench_ip")

    # 2. TokenBucket with 1000 pre-existing buckets -------------------------
    def test_token_bucket_allow_with_many_buckets(self, benchmark):
        """allow() remains fast with 1000 existing buckets."""
        bucket = _make_bucket()
        # Pre-populate
        for i in range(1000):
            bucket.allow(f"10.0.{i // 256}.{i % 256}")

        benchmark(bucket.allow, "target_ip")

    # 3. _is_auth_path — positive match -------------------------------------
    def test_is_auth_path_positive(self, benchmark):
        """_is_auth_path() for an auth prefix path."""
        from middleware.rate_limit import _is_auth_path

        benchmark(_is_auth_path, "/api/auth/login")

    # 4. _is_auth_path — negative match -------------------------------------
    def test_is_auth_path_negative(self, benchmark):
        """_is_auth_path() for a non-auth path."""
        from middleware.rate_limit import _is_auth_path

        benchmark(_is_auth_path, "/api/agents/list")

    # 5. _get_client_ip extraction ------------------------------------------
    def test_get_client_ip_speed(self, benchmark):
        """_get_client_ip() extraction speed with mock request."""
        from middleware.rate_limit import _get_client_ip
        from unittest.mock import MagicMock

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.100"

        benchmark(_get_client_ip, mock_request)

    # 6. AuthenticatedUser creation -----------------------------------------
    def test_authenticated_user_creation(self, benchmark):
        """AuthenticatedUser dataclass instantiation."""
        from middleware.auth import AuthenticatedUser

        def create_user():
            return AuthenticatedUser(
                id="user_bench",
                email="bench@test.com",
                org_id="org_1",
                org_role="member",
                permissions=["read", "write"],
                metadata={"session_id": "sess_123"},
            )

        benchmark(create_user)

    # 7. AuthenticatedUser.has_permission() ---------------------------------
    def test_authenticated_user_has_permission(self, benchmark):
        """has_permission() lookup speed."""
        from middleware.auth import AuthenticatedUser

        user = AuthenticatedUser(
            id="user_bench",
            permissions=["read", "write", "deploy", "manage_team"],
        )

        benchmark(user.has_permission, "deploy")

    # 8. JSON response serialization ----------------------------------------
    def test_json_serialization(self, benchmark):
        """JSON serialization of a typical API response payload."""
        payload = {
            "status": "healthy",
            "version": "3.1.0",
            "services": {
                "control_plane": True,
                "business_core": True,
                "workflow_engine": False,
                "mission_control": True,
            },
            "metrics": {
                "requests_total": 123456,
                "latency_p99_ms": 42.5,
                "error_rate": 0.001,
            },
            "agents": [
                {"id": f"agent_{i}", "status": "active", "tasks": i * 10}
                for i in range(20)
            ],
        }

        benchmark(json.dumps, payload)

    # 9. Health endpoint response time --------------------------------------
    def test_health_endpoint_latency(self, benchmark, client):
        """GET /health end-to-end latency through TestClient."""

        def hit_health():
            resp = client.get("/health")
            assert resp.status_code == 200

        benchmark(hit_health)

    # 10. Rate limiter cleanup() with 10K entries ---------------------------
    def test_rate_limiter_cleanup_10k(self, benchmark):
        """cleanup() performance with 10K stale entries."""
        from middleware.rate_limit import TokenBucket

        def setup_and_cleanup():
            bucket = TokenBucket(rate=1.0, burst=10)
            now = time.monotonic()
            # All entries are stale (10 min old)
            for i in range(10_000):
                bucket._buckets[f"ip_{i}"] = (0.0, now - 600)
            bucket.cleanup(max_age=300)
            assert len(bucket._buckets) == 0

        benchmark(setup_and_cleanup)
