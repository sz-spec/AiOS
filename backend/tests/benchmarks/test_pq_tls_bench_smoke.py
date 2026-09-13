"""
backend/tests/benchmarks/test_pq_tls_bench_smoke.py

Sprint 17 / Prototype 3 — Smoke test for the ML-KEM-768 latency
micro-benchmark harness. Confirms the script runs end-to-end on
hosts where the kyber-py + cryptography backends are available,
and skips gracefully on hosts where they're not.

This test does NOT assert on absolute performance — those numbers
vary by hardware. It only checks that the benchmark harness
produces a well-formed report.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BENCH_PATH = _REPO_ROOT / "backend" / "scripts" / "perf" / "mlkem_latency_test.py"
_spec = importlib.util.spec_from_file_location(
    "vos3_mlkem_bench_under_test", _BENCH_PATH
)
bench = importlib.util.module_from_spec(_spec)
sys.modules["vos3_mlkem_bench_under_test"] = bench
_spec.loader.exec_module(bench)


# ---------------------------------------------------------------------------
# Backend availability
# ---------------------------------------------------------------------------


def test_cryptography_backend_probed():
    """The bench module reports whether cryptography is available.
    On every project venv (cryptography is a direct dep), this MUST be True."""
    assert bench._CRYPTOGRAPHY_AVAILABLE is True


def test_kyber_py_probe_is_boolean():
    """kyber-py is OPTIONAL — the project doesn't ship it as a
    requirements.txt dep. Just confirm the probe returned a bool."""
    assert isinstance(bench._KYBER_AVAILABLE, bool)


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------


def test_percentile_empty_returns_zero():
    assert bench._percentile([], 0.5) == 0.0


def test_percentile_single_element():
    assert bench._percentile([1.5], 0.5) == 1.5
    assert bench._percentile([1.5], 0.99) == 1.5


def test_percentile_ten_elements():
    vals = [float(i) for i in range(10)]  # [0..9]
    assert bench._percentile(vals, 0.5) == 4.0  # index 4
    assert bench._percentile(vals, 0.95) == 8.0  # index 8
    assert bench._percentile(vals, 1.0) == 9.0


def test_percentile_zero_or_negative_q_returns_first():
    assert bench._percentile([1.0, 2.0, 3.0], 0.0) == 1.0
    assert bench._percentile([1.0, 2.0, 3.0], -0.5) == 1.0


# ---------------------------------------------------------------------------
# OperationStats shape
# ---------------------------------------------------------------------------


def test_x25519_keygen_op_produces_complete_stats():
    """X25519 must always be available; smoke-test the operation."""
    op = bench._x25519_keygen_op()
    stats = bench._time_operation("x25519_keygen", "cryptography(rust)", op, 50)
    assert stats.operation == "x25519_keygen"
    assert stats.iterations == 50
    assert stats.min_ms > 0
    assert stats.p50_ms >= stats.min_ms
    assert stats.p95_ms >= stats.p50_ms
    assert stats.p99_ms >= stats.p95_ms
    assert stats.max_ms >= stats.p99_ms
    assert stats.mean_ms > 0


def test_x25519_dh_op_produces_complete_stats():
    op = bench._x25519_dh_op()
    stats = bench._time_operation("x25519_dh", "cryptography(rust)", op, 30)
    assert stats.iterations == 30
    assert stats.min_ms > 0


# ---------------------------------------------------------------------------
# Full bench report shape
# ---------------------------------------------------------------------------


def test_run_benchmark_emits_report_with_required_fields(tmp_path):
    out = tmp_path / "bench.json"
    report = bench.run_benchmark(iterations=20, output_path=out)
    assert report.schema_version == "vos3.pq_tls_bench.v1"
    assert isinstance(report.host, dict)
    assert "python_version" in report.host
    assert "platform" in report.host
    assert isinstance(report.backends, dict)
    assert "cryptography" in report.backends
    assert "kyber_py" in report.backends
    # At minimum the two X25519 ops should be present.
    op_names = {op.operation for op in report.operations}
    assert "x25519_keygen" in op_names
    assert "x25519_dh" in op_names


def test_run_benchmark_writes_valid_json(tmp_path):
    out = tmp_path / "bench.json"
    bench.run_benchmark(iterations=20, output_path=out)
    assert out.exists()
    parsed = json.loads(out.read_text())
    assert parsed["schema_version"] == "vos3.pq_tls_bench.v1"
    assert isinstance(parsed["operations"], list)
    assert len(parsed["operations"]) >= 2  # at least the X25519 pair


def test_run_benchmark_records_notes_about_kyber():
    report = bench.run_benchmark(iterations=20)
    # Honest-scope ceiling note about kyber-py MUST be present so anyone
    # reading the JSON sees the constant-time disclaimer.
    notes_str = " ".join(report.notes).lower()
    assert "kyber-py" in notes_str or "kyber_py" in notes_str
    assert "constant-time" in notes_str or "side-channel" in notes_str


# ---------------------------------------------------------------------------
# Skip-on-missing-backend behavior
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not bench._KYBER_AVAILABLE,
    reason="kyber-py not installed; ML-KEM-768 ops cannot run",
)
def test_mlkem_keygen_op_produces_complete_stats():
    op = bench._mlkem_keygen_op()
    stats = bench._time_operation("mlkem_768_keygen", "kyber-py(pure-python)", op, 10)
    assert stats.iterations == 10
    # Pure-Python ML-KEM keygen on commodity hw is typically 1-5 ms.
    # The smoke test asserts only that it ran + produced positive timing.
    assert stats.p50_ms > 0


@pytest.mark.skipif(
    not bench._KYBER_AVAILABLE, reason="kyber-py not installed; hybrid path cannot run"
)
def test_hybrid_encap_op_produces_complete_stats():
    op = bench._hybrid_encap_op()
    stats = bench._time_operation("hybrid_encap", "x25519+mlkem768", op, 10)
    assert stats.p50_ms > 0


# ---------------------------------------------------------------------------
# Baseline file sanity (if committed)
# ---------------------------------------------------------------------------


def test_committed_baseline_if_present_is_valid_json():
    baseline_path = (
        _REPO_ROOT
        / "infra"
        / "benchmarks"
        / "results"
        / "pq_tls_baseline_2026-05-25.json"
    )
    if not baseline_path.exists():
        pytest.skip("baseline not committed yet")
    parsed = json.loads(baseline_path.read_text())
    assert parsed["schema_version"] == "vos3.pq_tls_bench.v1"
    # Baseline MUST include at minimum the X25519 ops (cryptography is
    # ubiquitously available on the dev host that produced the baseline).
    op_names = {op["operation"] for op in parsed["operations"]}
    assert "x25519_keygen" in op_names
