"""
Memory Leak Detection Tests
============================
Verifies that key subsystems do not leak memory under sustained use.

Uses tracemalloc to measure heap growth; asserts bounded deltas.

Run:
    pytest tests/perf/test_memory_leaks.py -p no:xdist -v
"""

import gc
import importlib
import time
import tracemalloc

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _measure_delta(func, *, rounds=1):
    """Run *func* (optionally multiple *rounds*) and return the tracemalloc
    memory delta in bytes.  GC is forced before each snapshot so that only
    true leaks are counted."""
    gc.collect()
    tracemalloc.start()
    snap_before = tracemalloc.take_snapshot()

    for _ in range(rounds):
        func()

    gc.collect()
    snap_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    stats = snap_after.compare_to(snap_before, "lineno")
    return sum(s.size_diff for s in stats)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.performance
@pytest.mark.slow
class TestMemoryLeaks:
    """Memory-leak detection suite."""

    # 1. Sequential health requests -----------------------------------------
    def test_health_endpoint_no_leak(self, client):
        """1000 sequential GET /health requests must not grow heap >5 MB."""
        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        for _ in range(1000):
            resp = client.get("/health")
            assert resp.status_code == 200

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snap2.compare_to(snap1, "lineno")
        total_delta = sum(s.size_diff for s in stats)
        assert (
            total_delta < 5 * 1024 * 1024
        ), f"Health endpoint leaked {total_delta / 1024 / 1024:.2f} MB over 1000 requests"

    # 2. TokenBucket with 10K unique IPs ------------------------------------
    def test_token_bucket_cleanup_returns_memory(self):
        """10K unique IPs: after cleanup(), memory returns to baseline."""
        from middleware.rate_limit import TokenBucket

        bucket = TokenBucket(rate=10.0, burst=100)

        gc.collect()
        tracemalloc.start()
        snap_baseline = tracemalloc.take_snapshot()

        for i in range(10_000):
            bucket.allow(f"10.0.{i // 256}.{i % 256}")

        # Memory grew — that is expected
        gc.collect()
        snap_peak = tracemalloc.take_snapshot()
        peak_stats = snap_peak.compare_to(snap_baseline, "lineno")
        peak_delta = sum(s.size_diff for s in peak_stats)
        assert peak_delta > 0, "Expected memory to grow with 10K entries"

        # Now cleanup and verify memory returns close to baseline
        bucket.cleanup(max_age=0)
        gc.collect()
        snap_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        after_stats = snap_after.compare_to(snap_baseline, "lineno")
        after_delta = sum(s.size_diff for s in after_stats)
        # After cleanup the residual should be much smaller than peak
        assert (
            after_delta < peak_delta * 0.5
        ), f"cleanup() did not reclaim memory: peak={peak_delta}, after={after_delta}"

    # 3. Rate limiter bucket cleanup with max_age=0 -------------------------
    def test_rate_limiter_cleanup_removes_all(self):
        """Add 1000 entries, cleanup(max_age=0) removes every one."""
        from middleware.rate_limit import TokenBucket

        bucket = TokenBucket(rate=1.0, burst=10)
        for i in range(1000):
            bucket.allow(f"key_{i}")

        assert len(bucket._buckets) == 1000

        bucket.cleanup(max_age=0)
        assert len(bucket._buckets) == 0

    # 4. Service singleton re-import ----------------------------------------
    def test_service_singleton_reimport(self):
        """Importing the main module 100 times yields the same app object."""
        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        first_app = None
        for _ in range(100):
            mod = importlib.import_module("main")
            if first_app is None:
                first_app = mod.app
            assert mod.app is first_app, "App singleton changed on re-import"

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snap2.compare_to(snap1, "lineno")
        total_delta = sum(s.size_diff for s in stats)
        assert (
            total_delta < 5 * 1024 * 1024
        ), f"Re-importing main leaked {total_delta / 1024 / 1024:.2f} MB"

    # 5. Create and destroy 100 TestClient instances -------------------------
    def test_testclient_lifecycle(self, _app):
        """Creating and destroying 100 TestClient instances leaks < 10 MB."""
        from fastapi.testclient import TestClient

        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        for _ in range(100):
            c = TestClient(_app)
            c.get("/health")
            del c

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snap2.compare_to(snap1, "lineno")
        total_delta = sum(s.size_diff for s in stats)
        assert (
            total_delta < 10 * 1024 * 1024
        ), f"TestClient lifecycle leaked {total_delta / 1024 / 1024:.2f} MB"

    # 6. Auth _user_id_cache bounded ----------------------------------------
    def test_user_id_cache_bounded(self):
        """Adding 1000 entries to _user_id_cache keeps memory bounded."""
        from middleware.auth import _user_id_cache

        original = dict(_user_id_cache)
        try:
            gc.collect()
            tracemalloc.start()
            snap1 = tracemalloc.take_snapshot()

            for i in range(1000):
                _user_id_cache[f"clerk_user_{i}"] = f"convex_id_{i}"

            gc.collect()
            snap2 = tracemalloc.take_snapshot()
            tracemalloc.stop()

            stats = snap2.compare_to(snap1, "lineno")
            total_delta = sum(s.size_diff for s in stats)
            # 1000 small string pairs should be well under 1 MB
            assert (
                total_delta < 1 * 1024 * 1024
            ), f"_user_id_cache with 1000 entries used {total_delta / 1024:.1f} KB"
        finally:
            _user_id_cache.clear()
            _user_id_cache.update(original)

    # 7. Repeated JSON serialization -----------------------------------------
    def test_json_serialization_no_accumulation(self):
        """Serializing a 1 MB JSON response 100 times does not accumulate."""
        import json

        payload = {"data": "x" * (1024 * 1024)}  # ~1 MB string

        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        for _ in range(100):
            serialized = json.dumps(payload)
            del serialized

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snap2.compare_to(snap1, "lineno")
        total_delta = sum(s.size_diff for s in stats)
        assert (
            total_delta < 5 * 1024 * 1024
        ), f"JSON serialization leaked {total_delta / 1024 / 1024:.2f} MB"

    # 8. Request/response cycle leak check -----------------------------------
    def test_post_request_cycle_no_leak(self, client):
        """500 POST requests with body, memory delta < 5 MB."""
        body = {"message": "a" * 1024, "model": "default"}

        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        for _ in range(500):
            # POST to health (will 405 or similar) — we only care about memory
            client.post("/health", json=body)

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snap2.compare_to(snap1, "lineno")
        total_delta = sum(s.size_diff for s in stats)
        assert (
            total_delta < 5 * 1024 * 1024
        ), f"POST cycle leaked {total_delta / 1024 / 1024:.2f} MB over 500 requests"

    # 9. TokenBucket stale entry cleanup -------------------------------------
    def test_token_bucket_stale_cleanup(self):
        """Entries with old timestamps are removed by cleanup()."""
        from middleware.rate_limit import TokenBucket

        bucket = TokenBucket(rate=1.0, burst=10)

        # Manually insert stale entries with old timestamps
        now = time.monotonic()
        for i in range(100):
            bucket._buckets[f"stale_{i}"] = (5.0, now - 600)  # 10 min old

        # Also add fresh entries
        for i in range(10):
            bucket._buckets[f"fresh_{i}"] = (5.0, now)

        assert len(bucket._buckets) == 110

        bucket.cleanup(max_age=300)  # Remove entries older than 5 min

        assert len(bucket._buckets) == 10
        # Verify only fresh entries remain
        for i in range(10):
            assert f"fresh_{i}" in bucket._buckets
        for i in range(100):
            assert f"stale_{i}" not in bucket._buckets

    # 10. AuthenticatedUser GC collection -----------------------------------
    def test_authenticated_user_gc_collected(self):
        """10K AuthenticatedUser instances are all collected by GC."""
        from middleware.auth import AuthenticatedUser

        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        for i in range(10_000):
            user = AuthenticatedUser(
                id=f"user_{i}",
                email=f"user_{i}@test.com",
                permissions=["read"],
            )
            # dataclass instances cannot use weakref by default,
            # so we just verify memory is reclaimed after deletion
            del user

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snap2.compare_to(snap1, "lineno")
        total_delta = sum(s.size_diff for s in stats)
        # 10K small objects created and immediately deleted should leave <1 MB
        assert (
            total_delta < 1 * 1024 * 1024
        ), f"AuthenticatedUser GC failed: {total_delta / 1024:.1f} KB residual"

    # 11. Import stability ---------------------------------------------------
    def test_middleware_import_stability(self):
        """Importing all middleware modules 50 times causes no leaks."""
        modules = [
            "middleware.rate_limit",
            "middleware.auth",
            "middleware.team_auth",
            "middleware.content_size",
            "middleware.timing",
            "middleware.request_id",
        ]

        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        for _ in range(50):
            for mod_name in modules:
                importlib.import_module(mod_name)

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snap2.compare_to(snap1, "lineno")
        total_delta = sum(s.size_diff for s in stats)
        assert (
            total_delta < 2 * 1024 * 1024
        ), f"Middleware re-import leaked {total_delta / 1024 / 1024:.2f} MB"

    # 12. Large request body handling ----------------------------------------
    def test_large_body_memory_returns(self, client):
        """100 requests with 1 MB body each: memory returns to baseline."""
        large_body = {"payload": "x" * (1024 * 1024)}

        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        for _ in range(100):
            # POST to any endpoint; response code doesn't matter
            client.post("/health", json=large_body)

        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snap2.compare_to(snap1, "lineno")
        total_delta = sum(s.size_diff for s in stats)
        assert (
            total_delta < 5 * 1024 * 1024
        ), f"Large body handling leaked {total_delta / 1024 / 1024:.2f} MB"
