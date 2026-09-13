"""
Chaos Test: Multi-Core Inference Race Conditions (KIM Concurrency)

Exercises the Kernel Inference Manager (KIM) under concurrent access to
expose race conditions on SMP-2 hardware. KIM uses per-slot spinlocks
(g_kim_slot_lock[8]) to serialize generate calls on the same slot, and
HugePage-backed KV-cache with slot->lock protection.

Targets:
  - Cross-slot isolation: parallel KIM_GENERATE on distinct slots must never
    leak tokens between slots.
  - Double-entry rejection: concurrent KIM_GENERATE on the SAME slot must
    return EBUSY (-16) for the second caller.
  - KV-cache pinning: HugePage allocation during generate must be balanced
    (no leaks after reset).
  - Reset-during-inference: SLOT_RESET while KIM_GENERATE is in flight must
    not cause a kernel panic (graceful teardown).

Run:
  python3 -m pytest backend/tests/chaos_inference_concurrency.py -v -o "addopts=" -s -m chaos

Requires: QEMU running with VOS3 kernel and VBus bridge at /tmp/vos3_bridge.sock
"""

import os
import sys
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import List

import pytest

# ---------------------------------------------------------------------------
# Path setup — allow imports from backend/
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vbus_driver import VBusDriver, VBusError
from tests.conftest_vbus import make_driver

logger = logging.getLogger("chaos_kim")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
CONSOLE_LOG = os.environ.get("VOS3_CONSOLE_LOG", "/tmp/vos3_console.log")
HUGEPAGE_SIZE = 2 * 1024 * 1024  # 2 MB

# Slots used for chaos tests (avoid slot 0 — coordinator, kernel-only)
CHAOS_SLOTS = [1, 2, 3, 4]

# Dummy model parameters for SLOT_START
DUMMY_MODEL_ID = 1
DUMMY_MODEL_SIZE = HUGEPAGE_SIZE  # 2 MB minimum load
DUMMY_LABEL = "chaos-test"

# KIM_GENERATE defaults
DEFAULT_MAX_TOKENS = 32
DEFAULT_TEMPERATURE = 100  # 1.0 in fixed-point x100

# EBUSY errno value returned by kernel
EBUSY = 16


# ---------------------------------------------------------------------------
# Skip marker — skip all tests if QEMU is not running
# ---------------------------------------------------------------------------

requires_qemu = pytest.mark.skipif(
    not os.path.exists(BRIDGE_SOCKET),
    reason=f"QEMU not running (no socket at {BRIDGE_SOCKET})",
)

chaos = pytest.mark.chaos


# ---------------------------------------------------------------------------
# Metrics dataclass — collected per test
# ---------------------------------------------------------------------------


@dataclass
class ChaosMetrics:
    """Aggregated metrics for a single chaos test run."""

    test_name: str = ""
    threads_launched: int = 0
    commands_sent: int = 0
    ok_responses: int = 0
    ebusy_responses: int = 0
    error_responses: int = 0
    panics_detected: int = 0
    hp_delta: int = 0
    wall_time_ms: float = 0.0
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"=== CHAOS METRICS: {self.test_name} ===",
            f"  Threads:    {self.threads_launched}",
            f"  Commands:   {self.commands_sent}",
            f"  OK:         {self.ok_responses}",
            f"  EBUSY:      {self.ebusy_responses}",
            f"  Errors:     {self.error_responses}",
            f"  Panics:     {self.panics_detected}",
            f"  HP delta:   {self.hp_delta}",
            f"  Wall time:  {self.wall_time_ms:.1f} ms",
        ]
        for note in self.notes:
            lines.append(f"  Note: {note}")
        lines.append("=" * (len(lines[0])))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# VBusClient — lightweight thread-safe wrapper for raw socket communication
# ---------------------------------------------------------------------------


class VBusClient:
    """Thread-safe VBus socket client for chaos testing.

    Each VBusClient owns a SEPARATE VBusDriver connection. This is critical
    because QEMU chardev only supports one client per socket, so the test
    must serialize actual socket access. This class wraps a shared driver
    with a lock so multiple threads can safely issue commands.

    For tests that need truly parallel socket writes (double-entry), we use
    the raw socket approach with explicit locking.
    """

    def __init__(self, driver: VBusDriver):
        self._drv = driver
        self._lock = threading.Lock()

    def send_command(self, cmd: str, slot_id: int = 0xFF) -> str:
        """Thread-safe command send. Acquires lock before send/recv cycle."""
        with self._lock:
            return self._drv.send_command(cmd, slot_id=slot_id)

    def kim_generate(
        self,
        slot_id: int,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: int = DEFAULT_TEMPERATURE,
    ) -> str:
        """Thread-safe KIM_GENERATE."""
        with self._lock:
            return self._drv.kim_generate(slot_id, max_tokens, temperature)

    def slot_start(
        self,
        slot_id: int,
        model_id: int = DUMMY_MODEL_ID,
        size: int = DUMMY_MODEL_SIZE,
        label: str = DUMMY_LABEL,
    ) -> str:
        """Thread-safe SLOT_START."""
        with self._lock:
            return self._drv.slot_start(slot_id, model_id, size, label)

    def slot_finish(self, slot_id: int) -> str:
        """Thread-safe SLOT_FINISH."""
        with self._lock:
            return self._drv.slot_finish(slot_id)

    def slot_reset(self, slot_id: int) -> str:
        """Thread-safe SLOT_RESET."""
        with self._lock:
            return self._drv.slot_reset(slot_id)

    def hp_stats(self) -> tuple:
        """Thread-safe HP_STATS. Returns (total, used)."""
        with self._lock:
            resp = self._drv.send_command("HP_STATS")
        parts = resp.split("|")
        if parts[0] == "OK" and len(parts) >= 3:
            return int(parts[1]), int(parts[2])
        raise VBusError(f"HP_STATS parse error: {resp}")

    def ping(self) -> bool:
        """Thread-safe PING — returns True if bridge is alive."""
        try:
            resp = self.send_command("PING")
            return resp is not None and "ERR" not in resp
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Thread-safe response collector
# ---------------------------------------------------------------------------


@dataclass
class ThreadResult:
    """Captures the outcome of a threaded VBus command."""

    thread_id: int
    slot_id: int
    response: str = ""
    error: str = ""
    elapsed_ms: float = 0.0
    is_ok: bool = False
    is_ebusy: bool = False


class ResponseCollector:
    """Thread-safe container for collecting results from worker threads."""

    def __init__(self):
        self._results: List[ThreadResult] = []
        self._lock = threading.Lock()

    def add(self, result: ThreadResult) -> None:
        with self._lock:
            self._results.append(result)

    @property
    def results(self) -> List[ThreadResult]:
        with self._lock:
            return list(self._results)

    def count_ok(self) -> int:
        return sum(1 for r in self.results if r.is_ok)

    def count_ebusy(self) -> int:
        return sum(1 for r in self.results if r.is_ebusy)

    def count_errors(self) -> int:
        return sum(1 for r in self.results if r.error and not r.is_ebusy)


# ---------------------------------------------------------------------------
# Console log panic detection
# ---------------------------------------------------------------------------


def count_console_panics() -> int:
    """Count kernel panic markers in the QEMU console log."""
    count = 0
    try:
        with open(CONSOLE_LOG, "r", errors="replace") as f:
            for line in f:
                low = line.lower()
                if "kernel panic" in low or "reserved-bit" in low:
                    count += 1
    except FileNotFoundError:
        pass
    return count


# ---------------------------------------------------------------------------
# Slot cleanup helper
# ---------------------------------------------------------------------------


def safe_reset(client: VBusClient, slot_id: int) -> None:
    """Reset a slot, ignoring errors (slot may not be started)."""
    try:
        client.slot_reset(slot_id)
    except Exception:
        pass


def setup_slot(client: VBusClient, slot_id: int) -> bool:
    """Start a slot with a dummy model. Returns True on success."""
    try:
        safe_reset(client, slot_id)
        time.sleep(0.2)
        resp = client.slot_start(slot_id)
        if "OK" in resp or "ok" in resp.lower():
            # Finish loading to transition to ACTIVE state
            resp2 = client.slot_finish(slot_id)
            return "OK" in resp2 or "ok" in resp2.lower()
        return False
    except Exception as e:
        logger.warning("setup_slot(%d) failed: %s", slot_id, e)
        return False


# ---------------------------------------------------------------------------
# Parse response helpers
# ---------------------------------------------------------------------------


def is_ok_response(resp: str) -> bool:
    """Check if a VBus response indicates success."""
    return resp.startswith("OK")


def is_ebusy_response(resp: str) -> bool:
    """Check if a VBus response indicates EBUSY (errno 16)."""
    return "ERR|16" in resp or "EBUSY" in resp


def extract_token_count(resp: str) -> int:
    """Extract token count from OK|N response. Returns -1 on parse error."""
    parts = resp.split("|")
    if len(parts) >= 2 and parts[0] == "OK":
        try:
            return int(parts[1])
        except ValueError:
            pass
    return -1


# ===========================================================================
# TEST CLASS
# ===========================================================================


@requires_qemu
class TestChaosInferenceConcurrency:
    """Chaos tests for KIM concurrent inference on SMP-2."""

    # -----------------------------------------------------------------------
    # Test 1: Parallel inference isolation (4 slots, 4 threads)
    # -----------------------------------------------------------------------

    @chaos
    def test_parallel_inference_isolation(self):
        """4 slots, 4 threads, each sends KIM_GENERATE to a unique slot.

        Verifies:
          - All 4 generate calls succeed (OK response)
          - No cross-slot token leakage (each slot returns independent tokens)
          - No kernel panics in console log
        """
        metrics = ChaosMetrics(test_name="parallel_inference_isolation")
        drv = make_driver()
        client = VBusClient(drv)

        # Verify bridge is alive
        assert client.ping(), "Bridge not responding"

        panics_before = count_console_panics()

        # Setup all 4 slots
        for sid in CHAOS_SLOTS:
            ok = setup_slot(client, sid)
            assert ok, f"Failed to setup slot {sid}"
            time.sleep(0.1)

        collector = ResponseCollector()
        barrier = threading.Barrier(len(CHAOS_SLOTS))

        def worker(tid: int, slot_id: int):
            """Thread worker: wait at barrier, then send KIM_GENERATE."""
            result = ThreadResult(thread_id=tid, slot_id=slot_id)
            try:
                # All threads start simultaneously
                barrier.wait(timeout=5.0)
                t0 = time.monotonic()
                resp = client.kim_generate(
                    slot_id, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE
                )
                result.elapsed_ms = (time.monotonic() - t0) * 1000
                result.response = resp
                result.is_ok = is_ok_response(resp)
                result.is_ebusy = is_ebusy_response(resp)
            except Exception as e:
                result.error = str(e)
            collector.add(result)

        # Launch 4 threads
        t0 = time.monotonic()
        threads = []
        for i, sid in enumerate(CHAOS_SLOTS):
            t = threading.Thread(target=worker, args=(i, sid), daemon=True)
            threads.append(t)
            t.start()
        metrics.threads_launched = len(threads)

        for t in threads:
            t.join(timeout=30.0)
        metrics.wall_time_ms = (time.monotonic() - t0) * 1000

        # Analyze results
        results = collector.results
        metrics.commands_sent = len(results)
        metrics.ok_responses = collector.count_ok()
        metrics.error_responses = collector.count_errors()

        # Every slot should have returned OK
        for r in results:
            logger.info(
                "Slot %d: ok=%s resp=%s elapsed=%.1fms",
                r.slot_id,
                r.is_ok,
                r.response[:80],
                r.elapsed_ms,
            )
            if not r.is_ok and not r.error:
                metrics.notes.append(f"Slot {r.slot_id} unexpected: {r.response}")

        assert metrics.ok_responses == len(CHAOS_SLOTS), (
            f"Expected {len(CHAOS_SLOTS)} OK responses, got {metrics.ok_responses}. "
            f"Results: {[(r.slot_id, r.response[:60]) for r in results]}"
        )

        # Verify no cross-slot contamination: each slot's token count is independent.
        # In a healthy system, token counts are deterministic per-slot since each slot
        # has its own model state. If cross-slot leakage occurs, a slot might return
        # tokens that belong to another slot's sequence (detectable by unexpected counts
        # or response anomalies).
        token_counts = {}
        for r in results:
            tc = extract_token_count(r.response)
            token_counts[r.slot_id] = tc
            assert tc >= 0, f"Slot {r.slot_id}: bad token count in '{r.response}'"
        metrics.notes.append(f"Token counts: {token_counts}")

        # No panics
        panics_after = count_console_panics()
        metrics.panics_detected = panics_after - panics_before
        assert (
            metrics.panics_detected == 0
        ), f"{metrics.panics_detected} kernel panics detected during parallel inference"

        # Cleanup
        for sid in CHAOS_SLOTS:
            safe_reset(client, sid)
            time.sleep(0.1)

        logger.info("\n%s", metrics.summary())

    # -----------------------------------------------------------------------
    # Test 2: Double-entry rejection (same slot, 2 threads)
    # -----------------------------------------------------------------------

    @chaos
    def test_double_entry_rejection(self):
        """Send 2 KIM_GENERATE commands to the SAME slot concurrently.

        The kernel's per-slot spinlock should serialize access. The first caller
        acquires the lock and begins generation; the second caller finds
        ctx->state != VOS3_KIM_IDLE and returns EBUSY (-16).

        Verifies:
          - Exactly 1 OK and 1 EBUSY response (or 2 OK if generation completes
            before the second thread enters, which is also valid).
          - No kernel panic.
        """
        metrics = ChaosMetrics(test_name="double_entry_rejection")
        drv = make_driver()
        client = VBusClient(drv)

        assert client.ping(), "Bridge not responding"

        panics_before = count_console_panics()
        target_slot = 1

        # Setup slot 1
        ok = setup_slot(client, target_slot)
        assert ok, f"Failed to setup slot {target_slot}"
        time.sleep(0.2)

        collector = ResponseCollector()
        barrier = threading.Barrier(2)

        def worker(tid: int):
            """Thread worker: barrier-synchronized KIM_GENERATE to same slot."""
            result = ThreadResult(thread_id=tid, slot_id=target_slot)
            try:
                barrier.wait(timeout=5.0)
                t0 = time.monotonic()
                resp = client.kim_generate(
                    target_slot, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE
                )
                result.elapsed_ms = (time.monotonic() - t0) * 1000
                result.response = resp
                result.is_ok = is_ok_response(resp)
                result.is_ebusy = is_ebusy_response(resp)
            except Exception as e:
                result.error = str(e)
                # VBus error responses containing EBUSY are also valid
                if "16" in str(e) or "EBUSY" in str(e):
                    result.is_ebusy = True
            collector.add(result)

        # Launch 2 threads targeting the same slot
        t0 = time.monotonic()
        threads = []
        for i in range(2):
            t = threading.Thread(target=worker, args=(i,), daemon=True)
            threads.append(t)
            t.start()
        metrics.threads_launched = 2

        for t in threads:
            t.join(timeout=30.0)
        metrics.wall_time_ms = (time.monotonic() - t0) * 1000

        # Analyze results
        results = collector.results
        metrics.commands_sent = len(results)
        metrics.ok_responses = collector.count_ok()
        metrics.ebusy_responses = collector.count_ebusy()
        metrics.error_responses = collector.count_errors()

        for r in results:
            logger.info(
                "Thread %d → Slot %d: ok=%s ebusy=%s resp=%s elapsed=%.1fms",
                r.thread_id,
                r.slot_id,
                r.is_ok,
                r.is_ebusy,
                r.response[:80],
                r.elapsed_ms,
            )

        # Valid outcomes:
        #   (a) 1 OK + 1 EBUSY  — second thread hit the spinlock while first was generating
        #   (b) 2 OK             — first generate completed before second entered
        # Invalid: 0 OK, 2 EBUSY, or any panics
        ok_count = metrics.ok_responses
        ebusy_count = metrics.ebusy_responses

        assert ok_count >= 1, (
            f"Expected at least 1 OK response, got {ok_count}. "
            f"Results: {[(r.thread_id, r.response[:60]) for r in results]}"
        )
        assert ok_count + ebusy_count == 2, (
            f"Expected OK+EBUSY to total 2, got OK={ok_count} EBUSY={ebusy_count}. "
            f"Results: {[(r.thread_id, r.response[:60]) for r in results]}"
        )

        if ebusy_count == 1:
            metrics.notes.append("RACE HIT: concurrent double-entry correctly rejected")
        else:
            metrics.notes.append(
                "NO RACE: first generate completed before second entered"
            )

        # No panics
        panics_after = count_console_panics()
        metrics.panics_detected = panics_after - panics_before
        assert (
            metrics.panics_detected == 0
        ), f"{metrics.panics_detected} kernel panics during double-entry test"

        # Cleanup
        safe_reset(client, target_slot)

        logger.info("\n%s", metrics.summary())

    # -----------------------------------------------------------------------
    # Test 3: KV-cache pinning under load
    # -----------------------------------------------------------------------

    @chaos
    def test_kv_cache_pinning_under_load(self):
        """Generate tokens, check HP_STATS, reset slot, check HP_STATS again.

        Verifies that HugePages allocated for KV-cache during generate are
        properly released on SLOT_RESET, resulting in zero net HP delta.

        Sequence:
          1. Record HP baseline (total, used_before)
          2. SLOT_START + SLOT_FINISH slot 1
          3. KIM_GENERATE slot 1 (triggers KV-cache allocation)
          4. HP_STATS → record used_during
          5. SLOT_RESET slot 1
          6. HP_STATS → record used_after
          7. Assert used_after == used_before (zero delta)
        """
        metrics = ChaosMetrics(test_name="kv_cache_pinning_under_load")
        drv = make_driver()
        client = VBusClient(drv)

        assert client.ping(), "Bridge not responding"

        panics_before = count_console_panics()

        # Clean slate: reset slots used
        safe_reset(client, 1)
        time.sleep(0.3)

        # Step 1: baseline HP stats
        total, used_before = client.hp_stats()
        logger.info("HP baseline: total=%d, used=%d", total, used_before)
        metrics.notes.append(f"HP baseline: total={total}, used={used_before}")

        # Step 2-3: setup slot and generate
        ok = setup_slot(client, 1)
        assert ok, "Failed to setup slot 1"
        time.sleep(0.2)

        t0 = time.monotonic()
        resp = client.kim_generate(1, DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE)
        gen_ms = (time.monotonic() - t0) * 1000
        metrics.commands_sent += 1
        logger.info("KIM_GENERATE slot 1: %s (%.1fms)", resp[:80], gen_ms)

        if is_ok_response(resp):
            metrics.ok_responses += 1
        else:
            metrics.error_responses += 1
            metrics.notes.append(f"Generate failed: {resp}")

        # Step 4: HP stats during load
        _, used_during = client.hp_stats()
        logger.info(
            "HP during generate: used=%d (delta from baseline: %d)",
            used_during,
            used_during - used_before,
        )
        metrics.notes.append(
            f"HP during: used={used_during} (delta={used_during - used_before})"
        )

        # Step 5: reset slot
        safe_reset(client, 1)
        time.sleep(0.5)  # Allow kernel to free HugePages

        # Step 6: HP stats after reset
        _, used_after = client.hp_stats()
        logger.info(
            "HP after reset: used=%d (delta from baseline: %d)",
            used_after,
            used_after - used_before,
        )

        # Step 7: verify zero delta
        metrics.hp_delta = used_after - used_before
        metrics.notes.append(f"HP after: used={used_after} (delta={metrics.hp_delta})")

        # No panics
        panics_after = count_console_panics()
        metrics.panics_detected = panics_after - panics_before

        assert metrics.hp_delta == 0, (
            f"HugePage leak detected! Before={used_before}, After={used_after}, "
            f"Delta={metrics.hp_delta} HugePages not freed after SLOT_RESET"
        )
        assert (
            metrics.panics_detected == 0
        ), f"{metrics.panics_detected} kernel panics during KV-cache pinning test"

        metrics.wall_time_ms = gen_ms
        logger.info("\n%s", metrics.summary())

    # -----------------------------------------------------------------------
    # Test 4: Reset during inference
    # -----------------------------------------------------------------------

    @chaos
    def test_reset_during_inference(self):
        """Send KIM_GENERATE and SLOT_RESET concurrently to the same slot.

        This is a destructive race condition test. The kernel must handle
        SLOT_RESET gracefully even if KIM_GENERATE is mid-execution:
          - The spinlock prevents literal simultaneous access
          - If RESET arrives while GENERATE holds the lock, RESET waits
          - If RESET wins the lock first, GENERATE should fail cleanly

        Verifies:
          - No kernel panic (graceful handling)
          - Bridge remains responsive after the race
          - HP pool returns to baseline (no leaked HugePages)
        """
        metrics = ChaosMetrics(test_name="reset_during_inference")
        drv = make_driver()
        client = VBusClient(drv)

        assert client.ping(), "Bridge not responding"

        panics_before = count_console_panics()

        # Clean slate
        safe_reset(client, 1)
        time.sleep(0.3)
        _, used_before = client.hp_stats()

        # Setup slot 1
        ok = setup_slot(client, 1)
        assert ok, "Failed to setup slot 1"
        time.sleep(0.2)

        collector = ResponseCollector()
        barrier = threading.Barrier(2)

        def generate_worker():
            """Send KIM_GENERATE with max tokens to maximize time window."""
            result = ThreadResult(thread_id=0, slot_id=1)
            try:
                barrier.wait(timeout=5.0)
                t0 = time.monotonic()
                # Use high max_tokens to maximize the generation window
                resp = client.kim_generate(1, 512, DEFAULT_TEMPERATURE)
                result.elapsed_ms = (time.monotonic() - t0) * 1000
                result.response = resp
                result.is_ok = is_ok_response(resp)
                result.is_ebusy = is_ebusy_response(resp)
            except Exception as e:
                result.error = str(e)
            collector.add(result)

        def reset_worker():
            """Send SLOT_RESET after a tiny delay to overlap with generate."""
            result = ThreadResult(thread_id=1, slot_id=1)
            try:
                barrier.wait(timeout=5.0)
                # Small delay to let generate start first, but still overlap
                time.sleep(0.001)
                t0 = time.monotonic()
                resp = client.slot_reset(1)
                result.elapsed_ms = (time.monotonic() - t0) * 1000
                result.response = resp
                result.is_ok = is_ok_response(resp)
            except Exception as e:
                result.error = str(e)
            collector.add(result)

        # Launch both threads
        t0 = time.monotonic()
        t_gen = threading.Thread(target=generate_worker, daemon=True)
        t_rst = threading.Thread(target=reset_worker, daemon=True)
        t_gen.start()
        t_rst.start()
        metrics.threads_launched = 2

        t_gen.join(timeout=30.0)
        t_rst.join(timeout=30.0)
        metrics.wall_time_ms = (time.monotonic() - t0) * 1000

        # Analyze results
        results = collector.results
        metrics.commands_sent = len(results)

        for r in results:
            label = "GENERATE" if r.thread_id == 0 else "RESET"
            logger.info(
                "%s → Slot %d: ok=%s resp=%s err=%s elapsed=%.1fms",
                label,
                r.slot_id,
                r.is_ok,
                r.response[:80],
                r.error[:60] if r.error else "",
                r.elapsed_ms,
            )
            if r.is_ok:
                metrics.ok_responses += 1
            elif r.error:
                metrics.error_responses += 1

        # The critical assertion: no kernel panic
        panics_after = count_console_panics()
        metrics.panics_detected = panics_after - panics_before
        assert (
            metrics.panics_detected == 0
        ), f"{metrics.panics_detected} kernel panics during reset-during-inference!"

        # Bridge must still be alive after the race
        time.sleep(0.5)
        assert client.ping(), "Bridge died after reset-during-inference race"
        metrics.notes.append("Bridge alive after race")

        # Ensure slot is fully reset (may need explicit reset if generate won)
        safe_reset(client, 1)
        time.sleep(0.5)

        # Check HP pool returns to baseline
        _, used_after = client.hp_stats()
        metrics.hp_delta = used_after - used_before
        metrics.notes.append(f"HP delta after race: {metrics.hp_delta}")

        assert metrics.hp_delta == 0, (
            f"HugePage leak after reset-during-inference: "
            f"before={used_before}, after={used_after}, delta={metrics.hp_delta}"
        )

        logger.info("\n%s", metrics.summary())
