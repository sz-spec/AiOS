"""
FastAPI TestClient — concurrent request fan-out.

Verifies the app routes the spec calls "100 concurrent API requests".
TestClient is single-threaded by default; we drive it from
ThreadPoolExecutor so the WSGI middleware stack gets exercised under
the kind of contention a real ASGI deployment sees on burst traffic.

NOTE: `auth_middleware` in `app.py` runs as a `BaseHTTPMiddleware`,
which means HTTPExceptions it raises propagate OUT of the call stack
instead of being caught by FastAPI's per-route exception handler.
With the default `raise_server_exceptions=True`, TestClient surfaces
those as Python exceptions, killing the test even when the response
*would* have been a clean 401. We therefore build a permissive client
with `raise_server_exceptions=False`: the middleware's 401 becomes
an actual 401 response, and our "no 5xx crash" invariant holds.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import median

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def permissive_client():
    """TestClient that doesn't re-raise middleware exceptions."""
    from main import app

    return TestClient(app, base_url="http://localhost", raise_server_exceptions=False)


_PROBE_ROUTES = [
    "/api/health",
    "/api/system/health",
    "/api/metrics/health",
]


def _probe(client, route: str) -> tuple[int, float]:
    t0 = time.perf_counter()
    r = client.get(route)
    return r.status_code, time.perf_counter() - t0


# ---------------------------------------------------------------------------
# Route reachability — never 5xx (4xx auth/not-found is acceptable)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", _PROBE_ROUTES)
def test_probe_route_reachable(permissive_client, route):
    status, _ = _probe(permissive_client, route)
    assert status < 500, f"{route} returned {status}"


@pytest.mark.parametrize("route", _PROBE_ROUTES)
def test_probe_under_500ms_warm(permissive_client, route):
    _probe(permissive_client, route)  # prime
    durations = []
    for _ in range(5):
        s, d = _probe(permissive_client, route)
        assert s < 500
        durations.append(d)
    assert (
        median(durations) < 0.500
    ), f"{route} median {median(durations)*1e3:.0f}ms > 500ms"


# ---------------------------------------------------------------------------
# Concurrent fan-out — 10, 25 workers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "workers,route",
    [
        (10, _PROBE_ROUTES[0]),
        (25, _PROBE_ROUTES[0]),
        (10, _PROBE_ROUTES[1]),
        (25, _PROBE_ROUTES[1]),
    ],
)
def test_concurrent_burst_no_5xx(permissive_client, workers, route):
    _probe(permissive_client, route)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_probe, permissive_client, route) for _ in range(workers)]
        results = [f.result() for f in as_completed(futs)]
    statuses = [r[0] for r in results]
    durations = [r[1] for r in results]
    assert all(s < 500 for s in statuses), f"5xx in burst: {statuses}"
    p95 = sorted(durations)[int(len(durations) * 0.95) - 1]
    assert p95 < 2.0, f"{workers}-worker p95 {p95*1e3:.0f}ms > 2s ceiling"


@pytest.mark.slow
@pytest.mark.parametrize("workers", [50, 100])
def test_concurrent_burst_heavy(permissive_client, workers):
    route = _PROBE_ROUTES[0]
    _probe(permissive_client, route)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_probe, permissive_client, route) for _ in range(workers)]
        results = [f.result() for f in as_completed(futs)]
    statuses = [r[0] for r in results]
    durations = [r[1] for r in results]
    assert all(s < 500 for s in statuses)
    p95 = sorted(durations)[int(len(durations) * 0.95) - 1]
    assert p95 < 5.0


# ---------------------------------------------------------------------------
# JSON payload — POST with heavy body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload_kb", [1, 10, 50, 100])
def test_post_heavy_json_does_not_500(permissive_client, payload_kb):
    """Endpoint must either accept or reject — never crash on a heavy POST."""
    blob = "x" * (payload_kb * 1024)
    r = permissive_client.post(
        "/api/codegen/",
        json={"prompt": blob, "language": "python"},
    )
    assert (
        r.status_code < 500
    ), f"{payload_kb}KB POST crashed with {r.status_code}: {r.text[:200]}"


# ---------------------------------------------------------------------------
# Diverse routes — fan out across health/metrics surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route",
    [
        "/api/health",
        "/api/system/health",
        "/api/metrics/health",
        "/api/system/status",
        "/api/v1/system/version",
    ],
)
def test_route_does_not_500(permissive_client, route):
    r = permissive_client.get(route)
    assert r.status_code < 500, f"{route} → {r.status_code}: {r.text[:200]}"


@pytest.mark.parametrize(
    "route,workers",
    [
        ("/api/health", 8),
        ("/api/health", 16),
        ("/api/system/health", 8),
        ("/api/metrics/health", 8),
    ],
)
def test_concurrent_diverse_routes(permissive_client, route, workers):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_probe, permissive_client, route) for _ in range(workers * 2)]
        results = [f.result() for f in as_completed(futs)]
    statuses = [r[0] for r in results]
    assert all(s < 500 for s in statuses)
