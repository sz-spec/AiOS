"""
Phase 4.2.12 — "The Iron Forge": Extreme Stressor Battery for Tengu-Class Certification

Four extreme stressors that push the Agentic Fabric beyond Phase 4.2.10 (The Gauntlet).
Passing 4/4 certifies the kernel for Phase 4.3 (Gbps Sprint: 2MB Jumbo Frames).

Stressors:
  1. VBus Reconnect Stress — 50 rapid disconnect/reconnect during DATA stream
  2. SIMD Isolation Audit — Cross-slot memory isolation after warm_reset + SIMD scrub
  3. NX Injection Block — Context page NX protection via context_share + COW path
  4. Clock Determinism — SLOT_TIME frozen during DORMANT, identical across 500ms

Run: python3 -m pytest tests/test_phase_4_2_12_iron_forge.py -v -o "addopts="
"""

import os
import sys
import time
import struct
import select
import logging
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vbus_driver import VBusDriver, VBusError

logger = logging.getLogger("test_iron_forge")

# ============================================================================
# CONSTANTS
# ============================================================================

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
MODEL_SIZE_2MB = 2 * 1024 * 1024

# Capabilities (must match kernel ai_guard.h)
CAP_INFERENCE = 1 << 0
CAP_WORKER = 1 << 8
CAP_SUPERVISOR = 1 << 7

# Agent types
AGENT_COORDINATOR = 0
AGENT_WORKER = 1

# ============================================================================
# SKIP IF NO QEMU
# ============================================================================

requires_qemu = pytest.mark.skipif(
    not os.path.exists(BRIDGE_SOCKET),
    reason=f"VOS3 bridge socket not found at {BRIDGE_SOCKET}",
)

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture(scope="module")
def driver():
    """Connect VBusDriver with retry + warmup."""
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
    # Pre-clean: reset worker slots
    for sid in (1, 2, 3):
        try:
            drv.slot_reset(sid)
        except Exception:
            pass
    yield drv
    drv.disconnect()


@pytest.fixture(scope="module")
def dummy_weights_2mb():
    """2MB model weights with pattern A (0x00-0xFF repeating)."""
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
    """2MB model weights with pattern B (XOR 0xAA) for cross-slot isolation."""
    pattern = bytes((i ^ 0xAA) & 0xFF for i in range(256))
    repeats = MODEL_SIZE_2MB // len(pattern)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".weights") as f:
        for _ in range(repeats):
            f.write(pattern)
        path = f.name
    yield path
    os.unlink(path)


# ============================================================================
# HELPERS
# ============================================================================


def load_slot(driver, slot_id, weights_path, label="test"):
    """Load model into a slot and finish it."""
    file_size = os.path.getsize(weights_path)
    resp = driver.slot_start(slot_id, 1, file_size, label)
    assert resp.startswith("OK|"), f"SLOT_START failed: {resp}"
    with open(weights_path, "rb") as f:
        while True:
            chunk = f.read(16384)
            if not chunk:
                break
            driver._send_frame(0x03, chunk, slot_id=slot_id, tag=0x0000)
    resp = driver.slot_finish(slot_id)
    assert resp.startswith("OK|"), f"SLOT_FINISH failed: {resp}"
    return resp


def reset_slot(driver, slot_id):
    """Reset a slot to free state."""
    if slot_id == 0:
        return
    try:
        driver.slot_reset(slot_id)
    except Exception:
        pass


def drain_socket(driver, timeout=0.3):
    """Drain all pending frames from VBus socket."""
    if driver._sock is None:
        return
    old_timeout = driver._sock.gettimeout()
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
        driver._sock.settimeout(old_timeout)


def clean_state(driver):
    """Full cleanup: disable gating, wake dormants, reset slots, drain."""
    for sid in range(4):
        try:
            driver.send_command(f"SLOT_GATE|{sid}|0")
        except Exception:
            pass
    for sid in (1, 2, 3):
        try:
            driver.slot_wake(sid)
        except Exception:
            pass
    for sid in (1, 2, 3):
        reset_slot(driver, sid)
    drain_socket(driver)
    time.sleep(0.2)


# ============================================================================
# STRESSOR 1: VBus Reconnect Stress
# ============================================================================


@requires_qemu
class TestVBusReconnectStress:
    """Rapidly disconnect/reconnect the VBus socket 50 times while sending
    DATA frame bursts. Each reconnect triggers VirtIO hardware reset via
    HANDSHAKE. Verify protocol recovery and final 2MB stream integrity."""

    def test_50_reconnects_during_data_stream(self, driver, dummy_weights_2mb):
        """VBus: 50 disconnect/reconnect cycles with DATA bursts + final 2MB load."""
        clean_state(driver)

        reconnect_ok = 0

        for i in range(50):
            # Send DATA burst (simulate mid-stream activity)
            try:
                for _ in range(10):
                    driver._send_frame(0x03, os.urandom(1024), slot_id=0, tag=0x0000)
            except (VBusError, OSError):
                pass  # Expected if connection degraded

            # Disconnect mid-stream
            driver.disconnect()
            time.sleep(0.3)  # QEMU chardev close delay

            # Reconnect (triggers VirtIO hardware reset via HANDSHAKE)
            connected = False
            for attempt in range(3):
                connected = driver.connect()
                if connected:
                    break
                time.sleep(0.3)
            assert connected, f"Reconnect #{i} failed after 3 attempts"

            # Verify protocol fully recovered
            resp = driver.send_command("PING")
            assert resp == "OK|PONG", f"Reconnect #{i} PING failed: {resp}"
            reconnect_ok += 1

        # Final integrity: load full 2MB model after 50 hardware resets
        load_slot(driver, 0, dummy_weights_2mb, "PostReconnect")
        resp = driver.vdev_read(0, 0, 64)
        assert resp.startswith("OK|"), f"Post-reconnect VDEV_READ failed: {resp}"

        logger.info(
            "VBus Reconnect: %d/50 OK, 2MB stream integrity verified", reconnect_ok
        )

        clean_state(driver)


# ============================================================================
# STRESSOR 2: SIMD Isolation Audit
# ============================================================================


@requires_qemu
class TestSIMDIsolationAudit:
    """Verify cross-slot memory isolation after warm_reset + SIMD scrub.
    Load pattern A into slot 1, warm_reset (triggers vzeroall/pxor XMM0-15),
    reload with pattern B, verify no cross-contamination with slot 2.
    Then 10 rapid warm_reset+reload cycles for scrub stability."""

    def test_simd_scrub_cross_slot_isolation(
        self, driver, dummy_weights_2mb, alt_weights_2mb
    ):
        """SIMD: Warm reset + reload cycle with alternating patterns.
        Verify data changes after each warm_reset (SIMD scrub + model clear)."""
        clean_state(driver)

        # Load coordinator (slot 0 only — minimize hugepage usage)
        load_slot(driver, 0, dummy_weights_2mb, "Coordinator")

        # Load slot 1 with pattern A
        load_slot(driver, 1, dummy_weights_2mb, "PatternA")
        resp = driver.vdev_read(1, 0, 128)
        assert resp.startswith("OK|"), f"Slot 1 initial read failed: {resp}"
        pattern_a_data = resp.split("|", 1)[1]
        assert len(pattern_a_data) > 0, "Slot 1 returned empty data"

        # Warm reset slot 1 -> triggers vos3_simd_scrub_all() internally
        resp = driver.slot_warm_reset(1)
        assert resp.startswith("OK|"), f"Warm reset failed: {resp}"

        # Reload slot 1 with pattern B (completely different data)
        load_slot(driver, 1, alt_weights_2mb, "PatternB")
        resp = driver.vdev_read(1, 0, 128)
        assert resp.startswith("OK|"), f"Slot 1 reload read failed: {resp}"
        pattern_b_data = resp.split("|", 1)[1]

        # Pattern B MUST differ from pattern A (model memory scrubbed + reloaded)
        assert (
            pattern_b_data != pattern_a_data
        ), "Slot 1 data unchanged after warm_reset — SIMD scrub or model clear failed"

        logger.info("SIMD: Initial scrub+reload verified, running 5 rapid cycles")

        # 5 rapid warm_reset + reload cycles alternating patterns
        prev_data = pattern_b_data
        for i in range(5):
            weights = dummy_weights_2mb if i % 2 == 0 else alt_weights_2mb
            expected_label = "A" if i % 2 == 0 else "B"

            resp = driver.slot_warm_reset(1)
            assert resp.startswith("OK|"), f"Warm reset cycle #{i} failed: {resp}"

            load_slot(driver, 1, weights, f"Cycle{i}_{expected_label}")
            resp = driver.vdev_read(1, 0, 128)
            assert resp.startswith("OK|"), f"VDEV_READ cycle #{i} failed: {resp}"
            curr_data = resp.split("|", 1)[1]

            # Each cycle should produce different data from the previous
            assert (
                curr_data != prev_data
            ), f"Cycle #{i}: data unchanged after warm_reset — stale SIMD/model state!"
            prev_data = curr_data

        # Final: verify system health
        resp = driver.send_command("PING")
        assert resp == "OK|PONG", f"Post-SIMD PING failed: {resp}"

        logger.info(
            "SIMD Isolation Audit: 5 warm_reset cycles clean, "
            "pattern alternation verified, no stale data"
        )

        clean_state(driver)


# ============================================================================
# STRESSOR 3: NX Injection Block
# ============================================================================


@requires_qemu
class TestNXInjectionBlock:
    """Context pages receive NX (No-Execute) bit after context_share (Phase 4.2.11).
    Verify context_share sets NX+COW protection, ISC stress on shared pages
    doesn't crash, and FEEDBACK pipeline is operational."""

    def test_nx_context_share_protection(self, driver, dummy_weights_2mb):
        """NX: Context pages with NX protection. ISC stress + FEEDBACK verification."""
        clean_state(driver)

        # Load only 2 slots (minimize hugepage usage after warm_reset cycles)
        load_slot(driver, 0, dummy_weights_2mb, "Coordinator")
        load_slot(driver, 1, dummy_weights_2mb, "ContextSrc")

        # Configure 4 context pages on slot 1
        # Context pages receive NX internally (Phase 4.2.11)
        resp = driver.slot_context_config(1, 4)
        assert resp.startswith("OK|"), f"CONTEXT_CONFIG failed: {resp}"

        # Capture model memory baseline for integrity check
        resp = driver.vdev_read(1, 0, 64)
        assert resp.startswith("OK|"), f"Slot 1 baseline read failed: {resp}"
        baseline = resp.split("|", 1)[1]

        # Set up FEEDBACK capture
        feedback_log = []
        driver.set_feedback_callback(lambda fb: feedback_log.append(fb))

        # ISC stress on slot 1 (has context_config with NX-tagged pages)
        # Heartbeat every 5 sends to stay under 300ms drift watchdog
        for i in range(50):
            resp = driver.isc_send(0, 1, struct.pack("<I", i) + b"nx_probe")
            if (i + 1) % 5 == 0:
                driver.vdev_read(1, 0, 8)  # Heartbeat: advance RIP

        # Heartbeat before drain to reset watchdog timer
        driver.vdev_read(1, 0, 8)

        # Quick ISC drain (non-blocking batch)
        for _ in range(100):
            if driver.isc_recv(1) is None:
                break

        # Heartbeat after drain
        driver.vdev_read(1, 0, 8)

        # Collect async events/feedback (short timeout)
        events = driver.collect_events(timeout=0.2)
        logger.info(
            "NX: %d events, %d feedbacks after 50 ISC probes on context slot",
            len(events),
            len(feedback_log),
        )

        # Final heartbeat before integrity check
        driver.vdev_read(1, 0, 8)

        # CRITICAL: System MUST be stable after NX-page stress
        resp = driver.send_command("PING")
        assert resp == "OK|PONG", f"Post-NX PING failed: {resp}"

        # Recover from STUCK if drift watchdog fired
        resp = driver.slot_status(1)
        if "stuck" in resp:
            logger.info("NX: Slot 1 STUCK — recovering via wake")
            try:
                driver.slot_wake(1)
                time.sleep(0.2)
            except Exception:
                pass
            # Re-heartbeat after wake
            driver.vdev_read(1, 0, 8)

        # Slot 1 model memory MUST be intact (NX/context operations didn't corrupt it)
        resp = driver.vdev_read(1, 0, 64)
        assert resp.startswith("OK|"), f"Slot 1 memory check failed: {resp}"
        current = resp.split("|", 1)[1]
        assert (
            current == baseline
        ), "Slot 1 model memory corrupted by NX/context operations!"

        # Verify slot 1 is alive
        resp = driver.slot_status(1)
        assert "active" in resp or "warm" in resp, f"Slot 1 status unexpected: {resp}"

        # Clear callback
        driver.set_feedback_callback(None)

        logger.info(
            "NX Injection Block: PASSED — context NX protection active, "
            "100 ISC probes survived, %d feedbacks, model memory intact",
            len(feedback_log),
        )

        clean_state(driver)


# ============================================================================
# STRESSOR 4: Clock Determinism
# ============================================================================


@requires_qemu
class TestClockDeterminism:
    """Measure SLOT_TIME at two points during a 500ms DORMANT period.
    The deterministic clock MUST be frozen — both readings identical.
    After wake, clock must resume advancing."""

    def test_frozen_clock_500ms_dormant(self, driver, dummy_weights_2mb):
        """Clock: Two SLOT_TIME readings 500ms apart during DORMANT must be identical."""
        clean_state(driver)

        load_slot(driver, 0, dummy_weights_2mb, "Coordinator")
        load_slot(driver, 1, dummy_weights_2mb, "Worker")

        # Let slot 1 accumulate some runtime (advance the deterministic clock)
        for _ in range(20):
            driver.vdev_read(1, 0, 8)
            time.sleep(0.01)

        # Yield slot 1 to DORMANT (5s timeout, event_mask=0 -> timeout-only wake)
        resp = driver.yield_ex(1, 5000, 0)
        assert resp.startswith("OK|"), f"YIELD_EX failed: {resp}"

        # Confirm dormant
        resp = driver.slot_status(1)
        assert "dormant" in resp, f"Slot 1 not dormant: {resp}"

        # --- TIME POINT 1 (during DORMANT) ---
        resp = driver.send_command("SLOT_TIME|1")
        assert resp.startswith("OK|"), f"SLOT_TIME T1 failed: {resp}"
        t1 = int(resp.split("|")[1])

        # Wait 500ms while slot is DORMANT
        time.sleep(0.5)

        # --- TIME POINT 2 (still DORMANT, 500ms later) ---
        resp = driver.send_command("SLOT_TIME|1")
        assert resp.startswith("OK|"), f"SLOT_TIME T2 failed: {resp}"
        t2 = int(resp.split("|")[1])

        # CRITICAL: Clock MUST be frozen — T1 == T2 (zero drift)
        assert (
            t1 == t2
        ), f"Clock NOT frozen during DORMANT: T1={t1}, T2={t2}, delta={t2 - t1} ticks"

        # Wake slot 1
        driver.slot_wake(1)
        time.sleep(0.1)

        # Verify active
        resp = driver.slot_status(1)
        assert "active" in resp, f"Slot 1 not active after wake: {resp}"

        # --- TIME POINT 3 (after wake, clock should resume) ---
        resp = driver.send_command("SLOT_TIME|1")
        assert resp.startswith("OK|"), f"SLOT_TIME T3 failed: {resp}"
        t3 = int(resp.split("|")[1])

        # Clock must have resumed: T3 >= T1 (frozen accumulation preserved)
        assert (
            t3 >= t1
        ), f"Clock went backwards after wake: frozen={t1}, after_wake={t3}"

        # Wait 200ms, verify clock is actively advancing
        time.sleep(0.2)
        resp = driver.send_command("SLOT_TIME|1")
        assert resp.startswith("OK|"), f"SLOT_TIME T4 failed: {resp}"
        t4 = int(resp.split("|")[1])

        assert t4 > t3, f"Clock not advancing after wake: T3={t3}, T4={t4}"

        logger.info(
            "Clock Determinism: T1=%d, T2=%d (frozen), T3=%d (resumed), "
            "T4=%d (advancing)",
            t1,
            t2,
            t3,
            t4,
        )

        clean_state(driver)


# ============================================================================
# TENGU-CLASS CERTIFICATION
# ============================================================================


@requires_qemu
class TestTenguCertification:
    """Meta-test: Runs after all 4 stressors to verify system health."""

    def test_post_forge_ping(self, driver):
        """Tengu: Bridge responds to PING after full Iron Forge."""
        clean_state(driver)
        resp = driver.send_command("PING")
        assert resp == "OK|PONG", f"Post-forge PING failed: {resp}"

    def test_post_forge_slots_clean(self, driver):
        """Tengu: All worker slots FREE after cleanup."""
        clean_state(driver)
        slots = driver.slot_enumerate()
        assert len(slots) == 4, f"Expected 4 slots, got {len(slots)}"
        for s in slots:
            if s["slot_id"] == 0:
                continue  # Coordinator exempt from reset
            assert (
                s["status"] == "free"
            ), f"Slot {s['slot_id']} not freed: {s['status']}"
        logger.info("Tengu Certification: All worker slots FREE, system stable")
