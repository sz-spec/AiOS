#!/usr/bin/env python3
"""
backend/scripts/perf/mlkem_latency_test.py

Sprint 17 / Prototype 3 — ML-KEM-768 latency micro-benchmark.

What this measures
------------------

Per-operation wall-clock latency for the three primitive operations of
the Sprint 15 / N1 hybrid post-quantum TLS path:

  * X25519 keygen + DH      (classical baseline)
  * ML-KEM-768 keygen        (kyber-py pure Python implementation)
  * ML-KEM-768 encaps + decaps
  * Hybrid (X25519 || ML-KEM-768) end-to-end

The benchmark fills a gap in the public literature: per the WebSearch
on May-24 2026 horizon, no published source measures microkernel-side
ML-KEM-768 latency on commodity hardware (arxiv 2404.13544 measures
on AVX-512 servers; IACR ePrint 2026/959 measures on financial-
infra TLS terminators). vOS-side numbers ground the Sprint 17 /
Cluster A "PQ-TLS overhead at the microkernel boundary" claim.

Usage
-----

  python backend/scripts/perf/mlkem_latency_test.py
  python backend/scripts/perf/mlkem_latency_test.py --iterations 2000
  python backend/scripts/perf/mlkem_latency_test.py --output results.json

Honest scope ceiling
--------------------

  * kyber-py is a pure-Python reference implementation by GiacomoPope.
    Per the author + pq-crystals.org, it is NOT constant-time and NOT
    secure against side-channel attacks. Use for BENCHMARKING + dev
    only. Production deployments use the kernel ML-KEM-768 path
    (kernel/include/vos/mlkem768.h) which is constant-time C.
  * X25519 uses the in-tree `cryptography` library; numbers reflect
    its rust backend (constant-time, production-grade).
  * Pure-Python ML-KEM-768 is ~60-70× slower than the C extension
    variant (per the mlkem package benchmark on PyPI: 1000 keygen+
    encaps+decaps in ~51 seconds on a modern x86). These numbers are
    a CEILING for the C-side performance; treat them as conservative.
  * Results are emitted as a JSON document so future runs can be
    diffed against the committed baseline at
    `infra/benchmarks/results/pq_tls_baseline_2026-05-25.json`.

References
----------

  * draft-ietf-tls-mlkem-07 (current ML-KEM for TLS 1.3)
  * arxiv 2404.13544 — Faster Post-Quantum TLS 1.3 Based on ML-KEM
    (AVX-512 1.64× speedup; batch keygen 3.5-4.9×)
  * IACR ePrint 2026/959 — Operationalising Post-Quantum TLS at
    financial infrastructure
  * github.com/GiacomoPope/kyber-py — pure-Python ML-KEM (FIPS 203)
  * NIST FIPS 203 Module-Lattice-Based KEM Standard
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Backend probes
# ---------------------------------------------------------------------------


_KYBER_AVAILABLE = False
_CRYPTOGRAPHY_AVAILABLE = False
_X25519_BACKEND = None
_ML_KEM_768 = None

try:
    from cryptography.hazmat.primitives.asymmetric import x25519 as _x25519

    _CRYPTOGRAPHY_AVAILABLE = True
    _X25519_BACKEND = _x25519
except ImportError:
    pass

try:
    from kyber_py.ml_kem import ML_KEM_768 as _ML_KEM_768  # type: ignore

    _KYBER_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Bench result types
# ---------------------------------------------------------------------------


@dataclass
class OperationStats:
    """Wall-clock latency stats for a single primitive operation."""

    operation: str
    backend: str
    iterations: int
    min_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    mean_ms: float
    stddev_ms: float


@dataclass
class BenchmarkReport:
    """Top-level report bundle."""

    schema_version: str
    timestamp_utc: str
    host: dict
    backends: dict
    operations: list[OperationStats] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bench harness
# ---------------------------------------------------------------------------


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    idx = int(q * (len(sorted_vals) - 1))
    return sorted_vals[idx]


def _time_operation(
    name: str, backend: str, fn: Callable[[], None], iterations: int
) -> OperationStats:
    """Run `fn` `iterations` times, returning latency stats in milliseconds.

    Single-thread, no JIT warmup beyond a 10-call discard at the start.
    Uses time.perf_counter for sub-microsecond resolution.
    """
    # Warmup — discard the first 10 calls' timing (caches + import-time
    # lazy init).
    for _ in range(min(10, iterations)):
        fn()

    timings_ns: list[int] = []
    for _ in range(iterations):
        t0 = time.perf_counter_ns()
        fn()
        t1 = time.perf_counter_ns()
        timings_ns.append(t1 - t0)

    timings_ms = sorted(t / 1_000_000 for t in timings_ns)
    return OperationStats(
        operation=name,
        backend=backend,
        iterations=iterations,
        min_ms=round(timings_ms[0], 4),
        p50_ms=round(_percentile(timings_ms, 0.50), 4),
        p95_ms=round(_percentile(timings_ms, 0.95), 4),
        p99_ms=round(_percentile(timings_ms, 0.99), 4),
        max_ms=round(timings_ms[-1], 4),
        mean_ms=round(statistics.fmean(timings_ms), 4),
        stddev_ms=(
            round(statistics.stdev(timings_ms), 4) if len(timings_ms) > 1 else 0.0
        ),
    )


# ---------------------------------------------------------------------------
# Operation factories — produce closures the harness can call repeatedly
# ---------------------------------------------------------------------------


def _x25519_keygen_op() -> Callable[[], None]:
    if not _CRYPTOGRAPHY_AVAILABLE:
        raise RuntimeError("cryptography library not available")
    return lambda: _X25519_BACKEND.X25519PrivateKey.generate()


def _x25519_dh_op() -> Callable[[], None]:
    """Generate a fresh peer + do one DH each call."""
    if not _CRYPTOGRAPHY_AVAILABLE:
        raise RuntimeError("cryptography library not available")
    our_sk = _X25519_BACKEND.X25519PrivateKey.generate()
    peer_pk = _X25519_BACKEND.X25519PrivateKey.generate().public_key()
    return lambda: our_sk.exchange(peer_pk)


def _mlkem_keygen_op() -> Callable[[], None]:
    if not _KYBER_AVAILABLE:
        raise RuntimeError("kyber-py library not available")
    return lambda: _ML_KEM_768.keygen()


def _mlkem_encaps_op() -> Callable[[], None]:
    if not _KYBER_AVAILABLE:
        raise RuntimeError("kyber-py library not available")
    pk, _sk = _ML_KEM_768.keygen()
    return lambda: _ML_KEM_768.encaps(pk)


def _mlkem_decaps_op() -> Callable[[], None]:
    if not _KYBER_AVAILABLE:
        raise RuntimeError("kyber-py library not available")
    pk, sk = _ML_KEM_768.keygen()
    _ss, ct = _ML_KEM_768.encaps(pk)
    return lambda: _ML_KEM_768.decaps(sk, ct)


def _hybrid_keygen_op() -> Callable[[], None]:
    """X25519 keygen + ML-KEM-768 keygen back-to-back."""
    if not (_CRYPTOGRAPHY_AVAILABLE and _KYBER_AVAILABLE):
        raise RuntimeError("hybrid needs both cryptography and kyber-py")

    def _op() -> None:
        _X25519_BACKEND.X25519PrivateKey.generate()
        _ML_KEM_768.keygen()

    return _op


def _hybrid_encap_op() -> Callable[[], None]:
    """Full hybrid encap on a fresh peer keypair each call."""
    if not (_CRYPTOGRAPHY_AVAILABLE and _KYBER_AVAILABLE):
        raise RuntimeError("hybrid needs both cryptography and kyber-py")
    peer_sk = _X25519_BACKEND.X25519PrivateKey.generate()
    peer_pk = peer_sk.public_key()
    mlkem_pk, _mlkem_sk = _ML_KEM_768.keygen()

    def _op() -> None:
        # Classical half
        our_sk = _X25519_BACKEND.X25519PrivateKey.generate()
        our_sk.exchange(peer_pk)
        # PQ half
        _ML_KEM_768.encaps(mlkem_pk)

    return _op


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_benchmark(
    iterations: int = 200, output_path: Optional[Path] = None
) -> BenchmarkReport:
    report = BenchmarkReport(
        schema_version="vos3.pq_tls_bench.v1",
        timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        host={
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "arch": platform.machine(),
        },
        backends={
            "cryptography": _CRYPTOGRAPHY_AVAILABLE,
            "kyber_py": _KYBER_AVAILABLE,
        },
    )

    # Honest-scope notes upfront.
    report.notes.append(
        "kyber-py is a pure-Python reference implementation — NOT "
        "constant-time, NOT side-channel safe. Use for benchmarking "
        "+ educational purposes only. Production deployments use the "
        "kernel ML-KEM-768 C path."
    )
    report.notes.append(
        f"Per-operation iterations={iterations} (after a 10-call "
        f"warmup discard). Wall-clock via time.perf_counter_ns()."
    )

    # X25519 — always available if cryptography is present.
    if _CRYPTOGRAPHY_AVAILABLE:
        report.operations.append(
            _time_operation(
                "x25519_keygen",
                "cryptography(rust)",
                _x25519_keygen_op(),
                iterations,
            )
        )
        report.operations.append(
            _time_operation(
                "x25519_dh",
                "cryptography(rust)",
                _x25519_dh_op(),
                iterations,
            )
        )
    else:
        report.notes.append("SKIP: x25519 (cryptography library missing)")

    # ML-KEM-768.
    if _KYBER_AVAILABLE:
        # kyber-py is slow in pure-Python — cut iteration count for
        # encaps/decaps which dominate; keygen is the cheapest.
        mlkem_iter = max(20, iterations // 10)
        report.operations.append(
            _time_operation(
                "mlkem_768_keygen",
                "kyber-py(pure-python)",
                _mlkem_keygen_op(),
                mlkem_iter,
            )
        )
        report.operations.append(
            _time_operation(
                "mlkem_768_encaps",
                "kyber-py(pure-python)",
                _mlkem_encaps_op(),
                mlkem_iter,
            )
        )
        report.operations.append(
            _time_operation(
                "mlkem_768_decaps",
                "kyber-py(pure-python)",
                _mlkem_decaps_op(),
                mlkem_iter,
            )
        )
    else:
        report.notes.append(
            "SKIP: ML-KEM-768 (kyber-py library missing — install via "
            "`pip install kyber-py`)"
        )

    # Hybrid path.
    if _CRYPTOGRAPHY_AVAILABLE and _KYBER_AVAILABLE:
        hybrid_iter = max(20, iterations // 10)
        report.operations.append(
            _time_operation(
                "hybrid_keygen",
                "x25519+mlkem768",
                _hybrid_keygen_op(),
                hybrid_iter,
            )
        )
        report.operations.append(
            _time_operation(
                "hybrid_encap",
                "x25519+mlkem768",
                _hybrid_encap_op(),
                hybrid_iter,
            )
        )
    else:
        report.notes.append("SKIP: hybrid (need both cryptography + kyber-py)")

    # Emit.
    payload = {
        "schema_version": report.schema_version,
        "timestamp_utc": report.timestamp_utc,
        "host": report.host,
        "backends": report.backends,
        "operations": [asdict(op) for op in report.operations],
        "notes": report.notes,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2))

    return report


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--iterations",
        type=int,
        default=200,
        help="Iterations per operation (default 200; ML-KEM uses "
        "iterations/10 due to pure-Python slowness)",
    )
    ap.add_argument(
        "--output", type=Path, default=None, help="Write JSON results to this path"
    )
    args = ap.parse_args()

    if args.iterations < 1:
        print("error: --iterations must be >= 1", file=sys.stderr)
        return 2

    print("=== ML-KEM-768 latency micro-benchmark (Sprint 17 Prototype 3) ===")
    print(
        f"backends: cryptography={_CRYPTOGRAPHY_AVAILABLE} "
        f"kyber-py={_KYBER_AVAILABLE}"
    )
    print(
        f"iterations={args.iterations} (ML-KEM scaled down to "
        f"{max(20, args.iterations // 10)})"
    )
    print()

    report = run_benchmark(iterations=args.iterations, output_path=args.output)

    print(
        f"{'operation':<24s} {'backend':<28s} {'iter':>6s} "
        f"{'p50':>10s} {'p95':>10s} {'p99':>10s} {'mean':>10s}"
    )
    print("-" * 100)
    for op in report.operations:
        print(
            f"{op.operation:<24s} {op.backend:<28s} {op.iterations:>6d} "
            f"{op.p50_ms:>9.4f}ms {op.p95_ms:>9.4f}ms "
            f"{op.p99_ms:>9.4f}ms {op.mean_ms:>9.4f}ms"
        )
    if report.notes:
        print()
        print("Notes:")
        for n in report.notes:
            print(f"  - {n}")
    if args.output is not None:
        print(f"\nJSON written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
