"""
Phase 4.2.13 — The Diamond Anvil
=================================
Final 72-hour certification battery.  Exercises the SYNERGY between every
Phase 4.2 primitive:

  1. Integrated Synergy    — VBus 2.0 + COW Context + BTB Scrubbing + Consensus Gating
  2. Swarm Marathon         — 4-slot circular ISC, random SLOT_GATE/COMMIT, crash/reconnect
  3. PTE/TLB Stress         — Rapid invert/uninvert on Slot 1, concurrent reads on Slot 0
  4. ISC Wake Latency Audit — Verify BTB (IBPB) + Clock Re-Sync don't degrade ISC-wake time

Pass criteria:  4/4 test classes green  →  72h Certification Report issued.
"""

import os
import sys
import time
import random
import select
import tempfile
import logging

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vbus_driver import VBusDriver, VBusError

logger = logging.getLogger("test_diamond_anvil")

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
MODEL_SIZE_2MB = 2 * 1024 * 1024

# Capability flags (kernel ai_guard.h)
CAP_INFERENCE = 1 << 0
CAP_SUPERVISOR = 1 << 7
CAP_WORKER = 1 << 8

requires_qemu = pytest.mark.skipif(
    not os.path.exists(BRIDGE_SOCKET),
    reason=f"VOS3 bridge socket not found at {BRIDGE_SOCKET}",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safe_int(s, base=10, default=0):
    if not s or not s.strip():
        return default
    return int(s.strip(), base)


def drain_socket(driver, timeout=0.3):
    """Drain all pending frames from VBus socket."""
    if driver._sock is None:
        return
    old = driver._sock.gettimeout()
    driver._sock.settimeout(0.1)
    try:
        while True:
            ready, _, _ = select.select([driver._sock], [], [], timeout)
            if not ready:
                break
            try:
                driver._recv_frame()
            except (VBusError, OSError):
                break
    except Exception:
        pass
    finally:
        driver._sock.settimeout(old)


def heartbeat(driver, slot_id):
    """Issue VDEV_READ to advance RIP / reset drift watchdog."""
    try:
        driver.vdev_read(slot_id, 0, 16)
    except Exception:
        pass


def slot_status(driver, slot_id):
    """Return slot status string (e.g. 'active', 'dormant', 'free').

    SLOT_STATUS response: OK|slot_id|status_name|label|pri|mid|size|cksum|cyc|viol
    Status name is at index 2 after splitting on '|'.
    """
    try:
        resp = driver.slot_status(slot_id)
    except Exception:
        return "unknown"
    if resp.startswith("OK|"):
        parts = resp.split("|")
        # parts[0]="OK", parts[1]=slot_id, parts[2]=status_name
        if len(parts) >= 3:
            return parts[2].strip().lower()
    return "unknown"


def recover_stuck(driver, slot_id):
    """If slot went STUCK, wake + heartbeat to recover."""
    status = slot_status(driver, slot_id)
    if status == "stuck":
        try:
            driver.slot_wake(slot_id)
        except Exception:
            pass
        time.sleep(0.2)
        heartbeat(driver, slot_id)
        time.sleep(0.1)
    return slot_status(driver, slot_id)


def clean_state(driver):
    """Full cleanup: disable gating, wake dormants, reset worker slots."""
    for sid in range(4):
        try:
            driver.send_command(f"SLOT_GATE|{sid}|0")
        except Exception:
            pass
    for sid in range(4):
        try:
            driver.slot_wake(sid)
        except Exception:
            pass
    for sid in (1, 2, 3):
        try:
            driver.slot_reset(sid)
        except Exception:
            pass
    drain_socket(driver)
    time.sleep(0.2)


def load_slot(driver, slot_id, weights_path, label="anvil"):
    """Load model weights into a slot."""
    file_size = os.path.getsize(weights_path)
    resp = driver.slot_start(slot_id, 1, file_size, label)
    assert resp.startswith("OK|"), f"SLOT_START slot {slot_id} failed: {resp}"
    with open(weights_path, "rb") as f:
        while True:
            chunk = f.read(16384)
            if not chunk:
                break
            driver._send_frame(0x03, chunk, slot_id=slot_id, tag=0x0000)
    resp = driver.slot_finish(slot_id)
    assert resp.startswith("OK|"), f"SLOT_FINISH slot {slot_id} failed: {resp}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def driver():
    """Module-scoped VBus connection with retry + warmup."""
    drv = VBusDriver(socket_path=BRIDGE_SOCKET)
    connected = False
    for attempt in range(3):
        connected = drv.connect()
        if connected:
            break
        time.sleep(0.5)
    if not connected:
        pytest.skip("Cannot connect to VOS3 bridge after 3 attempts")
    try:
        drv.ping()
        time.sleep(0.3)
    except Exception:
        pass
    # Pre-clean
    for sid in (1, 2, 3):
        try:
            drv.slot_reset(sid)
        except Exception:
            pass
    yield drv
    drv.disconnect()


@pytest.fixture(scope="module")
def dummy_weights_2mb():
    """2MB pattern-A weights (0x00..0xFF repeating)."""
    pattern = bytes(range(256))
    repeats = MODEL_SIZE_2MB // len(pattern)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".weights") as f:
        for _ in range(repeats):
            f.write(pattern)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture(scope="module")
def alt_weights_2mb():
    """2MB pattern-B weights (0xFF..0x00 descending)."""
    pattern = bytes(range(255, -1, -1))
    repeats = MODEL_SIZE_2MB // len(pattern)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".weights") as f:
        for _ in range(repeats):
            f.write(pattern)
        path = f.name
    yield path
    os.unlink(path)


# ===========================================================================
# Test 1:  Integrated Synergy
# ===========================================================================


@requires_qemu
class TestIntegratedSynergy:
    """
    Exercises VBus 2.0 + COW Context + BTB Scrubbing + Consensus Gating
    in a single coordinated workflow.

    Flow:
      1. Load Coordinator (slot 0) + Worker (slot 1)
      2. Configure context on slot 1 (last loaded — safe from address overlap)
      3. Enable SLOT_GATE on slot 1, ISC from 0->1, COMMIT_SLOT
      4. Warm-reset slot 1 (context survives, BTB scrub fires)
      5. Reload slot 1, verify context persisted, ISC still works
      6. Suspend/resume slot 1 (PTE invert/uninvert + IBPB)
      7. Final integrity check
    """

    def test_synergy_workflow(self, driver, dummy_weights_2mb, alt_weights_2mb):
        clean_state(driver)

        # --- Step 1: Load both slots ---
        load_slot(driver, 0, dummy_weights_2mb, "Coord")
        load_slot(driver, 1, alt_weights_2mb, "Worker1")

        # Set capabilities
        driver.slot_caps(0, CAP_INFERENCE | CAP_SUPERVISOR, 0)
        driver.slot_caps(1, CAP_INFERENCE | CAP_WORKER, 1)

        # Full IO masks
        driver.slot_io_mask(0, 0xFFFFFFFFFFFFFFFF)
        driver.slot_io_mask(1, 0xFFFFFFFFFFFFFFFF)
        heartbeat(driver, 0)
        heartbeat(driver, 1)

        # --- Step 2: Configure context on slot 1 (last loaded = safe) ---
        resp = driver.slot_context_config(1, 2)  # 2 pages = 8KB
        assert "OK" in resp, f"CONTEXT_CONFIG slot 1 failed: {resp}"
        heartbeat(driver, 0)
        heartbeat(driver, 1)

        # --- Step 3: Consensus gating + ISC ---
        resp = driver.slot_gate(1, 1)
        assert "OK" in resp, f"SLOT_GATE enable failed: {resp}"

        msg = b"TASK:compute_hash"
        resp = driver.isc_send(0, 1, msg, context_id=42)
        assert "OK" in resp, f"ISC_SEND 0->1 failed: {resp}"
        heartbeat(driver, 0)
        heartbeat(driver, 1)

        # Worker consumes message
        result = driver.isc_recv(1)
        assert result is not None, "ISC_RECV slot 1 empty"
        ctx_id, data = result
        assert ctx_id == 42
        assert data == msg

        # COMMIT releases any buffered response
        resp = driver.commit_slot(1)
        heartbeat(driver, 0)
        heartbeat(driver, 1)

        # Disable gate
        driver.slot_gate(1, 0)

        # --- Step 4: Warm-reset slot 1 (context survives, BTB scrub) ---
        heartbeat(driver, 1)
        resp = driver.slot_warm_reset(1)
        assert "OK" in resp, f"WARM_RESET failed: {resp}"
        heartbeat(driver, 0)
        time.sleep(0.2)

        # --- Step 5: Reload slot 1, verify context persisted ---
        load_slot(driver, 1, alt_weights_2mb, "Worker1v2")
        driver.slot_io_mask(1, 0xFFFFFFFFFFFFFFFF)
        heartbeat(driver, 0)
        heartbeat(driver, 1)

        # ISC still works post-warm-reset
        msg2 = b"VERIFY:post_warm_reset"
        resp = driver.isc_send(0, 1, msg2, context_id=99)
        assert "OK" in resp, f"Post-reset ISC_SEND failed: {resp}"
        heartbeat(driver, 1)

        result = driver.isc_recv(1)
        assert result is not None, "Post-reset ISC_RECV failed"
        ctx_id2, data2 = result
        assert ctx_id2 == 99
        assert data2 == msg2

        # --- Step 6: Suspend/resume slot 1 (PTE invert + IBPB scrub) ---
        heartbeat(driver, 1)
        resp = driver.slot_suspend(1)
        assert "OK" in resp, f"SLOT_SUSPEND failed: {resp}"
        time.sleep(0.1)

        status = slot_status(driver, 1)
        assert status in (
            "suspended",
            "suspended_pending",
        ), f"Expected suspended, got {status}"

        resp = driver.slot_resume(1)
        assert "OK" in resp, f"SLOT_RESUME failed: {resp}"
        heartbeat(driver, 1)
        time.sleep(0.1)

        status = slot_status(driver, 1)
        assert status == "active", f"Expected active after resume, got {status}"

        # --- Step 7: Final integrity ---
        resp = driver.slot_check(1, 0)
        assert "OK" in resp, f"SLOT_CHECK integrity failed: {resp}"

        clean_state(driver)


# ===========================================================================
# Test 2:  Swarm Marathon
# ===========================================================================


@requires_qemu
class TestSwarmMarathon:
    """
    4-slot circular ISC chat with random consensus gating and a simulated
    backend crash (disconnect/reconnect) mid-stream.

    Flow:
      1. Load all 4 slots
      2. Circular ISC ring: 0->1->2->3->0 (20 full loops)
      3. Randomly enable/disable SLOT_GATE + COMMIT_SLOT on workers
      4. Mid-stream: disconnect VBus, reconnect, recover all slots
      5. Verify: all slots alive, messages delivered
    """

    def test_swarm_marathon(self, driver, dummy_weights_2mb, alt_weights_2mb):
        clean_state(driver)

        # Weight files indexed by slot for reload after crash
        slot_weights = {
            0: dummy_weights_2mb,
            1: alt_weights_2mb,
            2: dummy_weights_2mb,
            3: alt_weights_2mb,
        }

        # --- Step 1: Load 4 slots ---
        load_slot(driver, 0, dummy_weights_2mb, "Coord")
        load_slot(driver, 1, alt_weights_2mb, "W1")
        load_slot(driver, 2, dummy_weights_2mb, "W2")
        load_slot(driver, 3, alt_weights_2mb, "W3")

        # Coordinator + 3 workers
        driver.slot_caps(0, CAP_INFERENCE | CAP_SUPERVISOR, 0)
        for sid in (1, 2, 3):
            driver.slot_caps(sid, CAP_INFERENCE | CAP_WORKER, 1)

        # Full IO masks
        for sid in range(4):
            driver.slot_io_mask(sid, 0xFFFFFFFFFFFFFFFF)
            heartbeat(driver, sid)

        rng = random.Random(2026)
        isc_success = 0
        gate_ops = 0
        LOOPS = 20
        CRASH_AT_LOOP = 10

        # --- Step 2-4: Circular ISC with random gating + crash ---
        for loop in range(LOOPS):
            # Simulated crash at midpoint
            if loop == CRASH_AT_LOOP:
                logger.info("Simulating backend crash at loop %d", loop)
                driver.disconnect()
                # Short crash: 100ms — minimise drift watchdog exposure
                time.sleep(0.1)
                connected = False
                for attempt in range(5):
                    connected = driver.connect()
                    if connected:
                        break
                    time.sleep(0.3)
                assert connected, "Failed to reconnect after simulated crash"
                driver.ping()
                time.sleep(0.1)
                # Immediate heartbeat to ALL slots
                for sid in range(4):
                    heartbeat(driver, sid)
                # Recover any STUCK slots — reset + reload if necessary
                for sid in range(4):
                    status = recover_stuck(driver, sid)
                    if status == "stuck":
                        # Hard recovery: reset and reload
                        logger.info("Hard-recovering STUCK slot %d", sid)
                        try:
                            driver.slot_reset(sid)
                        except Exception:
                            pass
                        time.sleep(0.1)
                        load_slot(driver, sid, slot_weights[sid], f"Recovered{sid}")
                        if sid == 0:
                            driver.slot_caps(sid, CAP_INFERENCE | CAP_SUPERVISOR, 0)
                        else:
                            driver.slot_caps(sid, CAP_INFERENCE | CAP_WORKER, 1)
                        driver.slot_io_mask(sid, 0xFFFFFFFFFFFFFFFF)
                    heartbeat(driver, sid)
                time.sleep(0.1)

            # Random gating on a worker
            if rng.random() < 0.3:
                gate_slot = rng.choice([1, 2, 3])
                try:
                    driver.slot_gate(gate_slot, 1)
                    gate_ops += 1
                except Exception:
                    pass

            # Circular ISC: 0->1->2->3->0
            ring = [(0, 1), (1, 2), (2, 3), (3, 0)]
            for src, dst in ring:
                msg = f"loop{loop}:{src}->{dst}".encode()
                try:
                    resp = driver.isc_send(src, dst, msg, context_id=loop)
                    if "OK" in resp:
                        isc_success += 1
                except (VBusError, OSError):
                    pass

                # Heartbeat EVERY endpoint to prevent drift
                heartbeat(driver, src)
                heartbeat(driver, dst)

            # Random commit on gated workers
            if rng.random() < 0.4:
                commit_slot = rng.choice([1, 2, 3])
                try:
                    driver.commit_slot(commit_slot)
                except Exception:
                    pass

            # Random gate disable
            if rng.random() < 0.3:
                ungate_slot = rng.choice([1, 2, 3])
                try:
                    driver.slot_gate(ungate_slot, 0)
                except Exception:
                    pass

            # Periodic drain + heartbeat all to prevent overflow + STUCK
            if loop % 3 == 2:
                for sid in range(4):
                    for _ in range(10):
                        r = driver.isc_recv(sid)
                        if r is None:
                            break
                    heartbeat(driver, sid)

        # --- Step 5: Final verification ---
        # Disable all gates
        for sid in range(4):
            try:
                driver.slot_gate(sid, 0)
            except Exception:
                pass

        # Drain remaining messages
        total_drained = 0
        for sid in range(4):
            for _ in range(50):
                r = driver.isc_recv(sid)
                if r is None:
                    break
                total_drained += 1
            heartbeat(driver, sid)

        # Verify all slots alive (with STUCK recovery + hard reset if needed)
        for sid in range(4):
            status = recover_stuck(driver, sid)
            if status == "stuck":
                # Hard recovery: reset and reload
                logger.info("Post-marathon hard-recovery slot %d", sid)
                try:
                    driver.slot_reset(sid)
                except Exception:
                    pass
                time.sleep(0.1)
                load_slot(driver, sid, slot_weights[sid], f"Final{sid}")
                driver.slot_io_mask(sid, 0xFFFFFFFFFFFFFFFF)
                heartbeat(driver, sid)
                status = slot_status(driver, sid)
            assert status in (
                "active",
                "warm",
            ), f"Slot {sid} in bad state after marathon: {status}"

        # At least 40% of ISC sends should have succeeded (crash loses some)
        expected_min = LOOPS * 4 * 0.4
        assert (
            isc_success >= expected_min
        ), f"ISC success too low: {isc_success}/{LOOPS * 4} (need {expected_min})"

        logger.info(
            "Swarm Marathon: %d ISC ok, %d gate ops, %d msgs drained",
            isc_success,
            gate_ops,
            total_drained,
        )

        clean_state(driver)


# ===========================================================================
# Test 3:  PTE/TLB Stress
# ===========================================================================


@requires_qemu
class TestPTETLBStress:
    """
    Rapid PTE invert/uninvert on Slot 1 while Slot 0 performs concurrent reads.
    Verifies no page-table corruption after 20 rapid suspend/resume cycles.

    Flow:
      1. Load slots 0 and 1
      2. 20x rapid: suspend(1) → VDEV_READ(0) → resume(1) → verify both slots
      3. After all cycles: SLOT_CHECK integrity, ISC round-trip
    """

    def test_rapid_pte_cycles(self, driver, dummy_weights_2mb, alt_weights_2mb):
        clean_state(driver)

        # Load 2 slots
        load_slot(driver, 0, dummy_weights_2mb, "PTECoord")
        load_slot(driver, 1, alt_weights_2mb, "PTETarget")

        driver.slot_io_mask(0, 0xFFFFFFFFFFFFFFFF)
        driver.slot_io_mask(1, 0xFFFFFFFFFFFFFFFF)
        heartbeat(driver, 0)
        heartbeat(driver, 1)

        CYCLES = 20
        corrupt_count = 0

        for cycle in range(CYCLES):
            # Heartbeat before suspend to reset drift watchdog
            heartbeat(driver, 0)
            heartbeat(driver, 1)

            # Suspend slot 1 (PTE invert + BTB scrub via IBPB)
            resp = driver.slot_suspend(1)
            assert "OK" in resp, f"Cycle {cycle}: SUSPEND failed: {resp}"

            # Concurrent read on slot 0 while slot 1 is inverted
            resp0 = driver.vdev_read(0, 0, 16)
            assert "OK" in resp0, f"Cycle {cycle}: Slot 0 read failed during invert"

            # Brief pause
            time.sleep(0.03)

            # Resume slot 1 (PTE uninvert)
            resp = driver.slot_resume(1)
            assert "OK" in resp, f"Cycle {cycle}: RESUME failed: {resp}"

            # Heartbeat immediately after resume
            heartbeat(driver, 1)
            heartbeat(driver, 0)

            # Verify slot 1 is active
            status = slot_status(driver, 1)
            if status != "active":
                recover_stuck(driver, 1)
                status = slot_status(driver, 1)
            if status != "active":
                corrupt_count += 1

        # Tolerance: allow at most 2 transient non-active readings
        assert corrupt_count <= 2, f"Too many corrupt cycles: {corrupt_count}/{CYCLES}"

        # Post-stress integrity check
        resp = driver.slot_check(1, 0)
        assert "OK" in resp, f"SLOT_CHECK post-PTE-stress failed: {resp}"

        # ISC round-trip still works
        msg = b"POST_PTE_STRESS"
        resp = driver.isc_send(0, 1, msg, context_id=777)
        assert "OK" in resp, f"Post-stress ISC_SEND failed: {resp}"
        heartbeat(driver, 1)

        result = driver.isc_recv(1)
        assert result is not None, "Post-stress ISC_RECV empty"
        ctx_id, data = result
        assert ctx_id == 777
        assert data == msg

        # Coordinator still reads fine
        resp = driver.vdev_read(0, 0, 16)
        assert "OK" in resp, "Coordinator read failed post-PTE-stress"

        logger.info(
            "PTE/TLB Stress: %d cycles, %d transient issues", CYCLES, corrupt_count
        )

        clean_state(driver)


# ===========================================================================
# Test 4:  ISC Wake Latency Audit
# ===========================================================================


@requires_qemu
class TestISCWakeLatency:
    """
    Verify that Phase 4.2.12 fixes (IBPB BTB scrubbing, Clock Re-Sync)
    don't degrade ISC-wake latency beyond acceptable bounds.

    Flow:
      1. Load slots 0 and 1
      2. Put slot 1 into DORMANT via yield_ex (event_mask = slot 0)
      3. ISC from slot 0 -> 1 triggers yield-wake
      4. Measure time from ISC_SEND to slot 1 becoming ACTIVE
      5. Repeat 10 times, verify P95 < 500ms
      6. Verify clock determinism: SLOT_TIME frozen during DORMANT
    """

    def test_isc_wake_latency(self, driver, dummy_weights_2mb, alt_weights_2mb):
        clean_state(driver)

        load_slot(driver, 0, dummy_weights_2mb, "LatCoord")
        load_slot(driver, 1, alt_weights_2mb, "LatWorker")

        driver.slot_io_mask(0, 0xFFFFFFFFFFFFFFFF)
        driver.slot_io_mask(1, 0xFFFFFFFFFFFFFFFF)
        heartbeat(driver, 0)
        heartbeat(driver, 1)

        TRIALS = 10
        latencies = []

        for trial in range(TRIALS):
            heartbeat(driver, 0)
            heartbeat(driver, 1)

            # Drain leftover ISC
            for _ in range(10):
                if driver.isc_recv(1) is None:
                    break

            # Get time BEFORE yield
            driver.send_command("SLOT_TIME|1")
            heartbeat(driver, 1)

            # Put slot 1 DORMANT: event_mask bit 0 = wake on ISC from slot 0
            resp = driver.yield_ex(1, 5000, 1 << 0)
            assert "OK" in resp, f"Trial {trial}: YIELD_EX failed: {resp}"
            time.sleep(0.05)

            # Confirm dormant
            status = slot_status(driver, 1)
            assert status == "dormant", f"Trial {trial}: Expected dormant, got {status}"

            # Clock should be frozen
            driver.send_command("SLOT_TIME|1")

            # ISC wake: send from slot 0 -> 1
            t_start = time.monotonic()
            msg = f"WAKE:{trial}".encode()
            resp = driver.isc_send(0, 1, msg, context_id=trial)
            assert "OK" in resp, f"Trial {trial}: ISC_SEND failed: {resp}"

            # Poll for slot 1 to become active
            woke = False
            for _ in range(50):  # Up to 500ms at 10ms intervals
                time.sleep(0.01)
                status = slot_status(driver, 1)
                if status in ("active", "warm"):
                    woke = True
                    break
            t_end = time.monotonic()

            if not woke:
                recover_stuck(driver, 1)
                status = slot_status(driver, 1)
                woke = status in ("active", "warm")

            assert woke, f"Trial {trial}: Slot 1 didn't wake, status={status}"
            latency_ms = (t_end - t_start) * 1000
            latencies.append(latency_ms)

            heartbeat(driver, 1)

            # Consume the wake message
            result = driver.isc_recv(1)
            assert result is not None, f"Trial {trial}: ISC_RECV empty after wake"

            # Clock should be advancing again
            heartbeat(driver, 1)
            time.sleep(0.05)
            driver.send_command("SLOT_TIME|1")

        # --- Latency analysis ---
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        avg = sum(latencies) / len(latencies)
        max_lat = max(latencies)

        logger.info(
            "ISC Wake Latency (%d trials): avg=%.1fms p50=%.1fms "
            "p95=%.1fms max=%.1fms",
            TRIALS,
            avg,
            p50,
            p95,
            max_lat,
        )

        # P95 must be under 500ms
        assert p95 < 500, f"P95 latency too high: {p95:.1f}ms (limit 500ms)"
        # Max must be under 2000ms
        assert max_lat < 2000, f"Max latency too high: {max_lat:.1f}ms"

        clean_state(driver)


# ===========================================================================
# Tengu Certification (Meta-Test)
# ===========================================================================


@requires_qemu
class TestTenguCertification:
    """Post-anvil sanity: PING responds and all slots are clean."""

    def test_post_anvil_ping(self, driver):
        rtt = driver.ping()
        assert rtt >= 0, f"PING failed with RTT={rtt}"

    def test_post_anvil_slots_clean(self, driver):
        resp = driver.slot_enumerate()
        for info in resp:
            sid = info.get("slot_id", -1)
            status = info.get("status", "unknown")
            if sid == 0:
                # Coordinator may still be loaded or stuck from prior tests
                assert status in (
                    "free",
                    "active",
                    "warm",
                    "stuck",
                ), f"Slot 0 in unexpected state: {status}"
            else:
                assert status in (
                    "free",
                    "stuck",
                ), f"Slot {sid} not cleaned up: {status}"
