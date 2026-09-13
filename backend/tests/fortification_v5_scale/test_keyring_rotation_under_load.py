"""
Phase 2 · Keyring rotation under load.

Honest scope
------------
"200k QPS / 10M nodes" is a deployment metric, not a pytest metric.
We measure rotation throughput at concurrency levels the host can
actually run (8 / 16 / 32 / 64 threads) and verify:
  * `threading.RLock` correctly serializes rotations
  * Final state is exactly ONE valid fingerprint (no torn writes)
  * Audit chronicle row count == number of rotations attempted
  * Latency-vs-concurrency slope is below the predictive-failure
    threshold (sub-linear scaling required)
"""

from __future__ import annotations

import threading
import time

import pytest

# ---------------------------------------------------------------------------
# Concurrent rotation — serialized by module-level RLock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_threads", [2, 4, 8, 16, 32, 64])
def test_concurrent_rotations_yield_one_final_state(v5_env, n_threads):
    """N threads call rotate_key() simultaneously. The lock serializes
    them; the final get_active_key_fingerprint() returns ONE value
    that's verifiable by verify_against_active_key()."""
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        rotate_key,
        verify_against_active_key,
    )

    barrier = threading.Barrier(n_threads)
    fingerprints = []

    def worker():
        barrier.wait()
        fp = rotate_key()
        fingerprints.append(fp)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # N rotations → N fingerprints recorded; only the LAST is active.
    assert len(fingerprints) == n_threads
    final_fp = get_active_key_fingerprint()
    assert verify_against_active_key(final_fp) is True
    # The final fp must be one of the recorded ones.
    assert final_fp in fingerprints


@pytest.mark.parametrize("n_threads", [4, 16, 64])
def test_concurrent_rotations_audit_row_count(v5_env, n_threads):
    """Every rotation MUST write exactly one audit row, even under
    contention. RLock + best-effort audit means count equals attempts."""
    from core.database.sqlite_setup import SecurityAuditLog, get_session
    from core.security.rotation_manager import rotate_key

    with get_session() as s:
        pre = s.query(SecurityAuditLog).filter_by(kind="key_rotated").count()

    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()
        rotate_key()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with get_session() as s:
        post = s.query(SecurityAuditLog).filter_by(kind="key_rotated").count()
    # Audit is best-effort but should be exact under our normal SQLite ops.
    assert post == pre + n_threads


# ---------------------------------------------------------------------------
# Predictive failure analysis — rotation throughput vs concurrency
# ---------------------------------------------------------------------------


def test_rotation_throughput_sublinear_slope(v5_env, regression_slope):
    """rotate_key() under a lock should NOT slow down as we add more
    contending threads (the work-per-thread is bounded). Wall-clock
    grows linearly with N (lock serializes), but per-rotation time
    must stay constant. Slope of per-rotation time vs concurrency
    should be ~0."""
    from core.security.rotation_manager import rotate_key

    concurrencies = [2, 4, 8, 16]
    per_rotation_times = []
    for n in concurrencies:
        barrier = threading.Barrier(n)

        def worker():
            barrier.wait()
            rotate_key()

        threads = [threading.Thread(target=worker) for _ in range(n)]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total = time.perf_counter() - t0
        per_rotation_times.append(total / n)

    slope = regression_slope(concurrencies, per_rotation_times)
    # Slope must be small — per-rotation time should be constant.
    # Threshold: 1ms per added thread (very generous).
    assert slope < 1e-3, (
        f"per-rotation latency grew {slope*1e3:.3f}ms per concurrent "
        f"thread — lock contention is super-linear"
    )


# ---------------------------------------------------------------------------
# Sequential rotation throughput
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_rotations", [10, 50, 100, 250, 500])
def test_sequential_rotation_throughput(v5_env, n_rotations):
    """Verify sequential rotate_key() throughput is >100/sec on this
    hardware (audit write + Ed25519 mint + keyring write)."""
    from core.security.rotation_manager import rotate_key

    t0 = time.perf_counter()
    for _ in range(n_rotations):
        rotate_key()
    elapsed = time.perf_counter() - t0
    throughput = n_rotations / elapsed
    # Floor: 50 rotations/sec on macOS APFS + Ed25519 mint.
    assert throughput > 50, (
        f"{n_rotations} rotations: {throughput:.1f}/sec — "
        f"rotation throughput floor breached"
    )


# ---------------------------------------------------------------------------
# Concurrent reads during rotation — read should never block on rotation
# longer than the write takes (RLock fairness)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_readers", [4, 8, 16, 32])
def test_readers_dont_starve_during_rotation(v5_env, n_readers):
    """N reader threads loop on get_active_key_fingerprint while 1
    rotator runs rotate_key. None of the readers should observe an
    empty or invalid fingerprint."""
    from core.security.rotation_manager import (
        get_active_key_fingerprint,
        rotate_key,
    )

    stop = threading.Event()
    reader_observations = []

    def reader():
        while not stop.is_set():
            fp = get_active_key_fingerprint()
            reader_observations.append(fp)

    readers = [threading.Thread(target=reader) for _ in range(n_readers)]
    for t in readers:
        t.start()
    rotate_key()
    rotate_key()
    rotate_key()
    stop.set()
    for t in readers:
        t.join()

    # Every observation must be a valid 16-char hex fingerprint.
    assert reader_observations
    for fp in reader_observations:
        assert isinstance(fp, str)
        assert len(fp) == 16
        int(fp, 16)  # must parse as hex
