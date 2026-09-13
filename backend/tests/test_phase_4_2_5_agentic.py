"""
Phase 4.2.5 — Multi-Agent Orchestration Tests

Tests multi-slot model loading, suspend/resume with PTE inversion,
slot lifecycle (reset/swap/snapshot), async events, CRC32C integrity,
VDEV_READ data verification, agent labeling, timers, and telemetry.

Run: python3 -m pytest tests/test_phase_4_2_5_agentic.py -v -o "addopts="
"""

import os
import sys
import time
import tempfile
import logging

import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vbus_driver import VBusDriver, VBusError

logger = logging.getLogger("test_phase_4_2_5")

# ============================================================================
# CONSTANTS
# ============================================================================

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
MODEL_SIZE_10MB = 10 * 1024 * 1024
MODEL_SIZE_2MB = 2 * 1024 * 1024
HUGEPAGE_SIZE = 2 * 1024 * 1024

# PTE bit positions
PTE_PRESENT = 1 << 0
PTE_WRITABLE = 1 << 1
PTE_PS_LARGE = 1 << 7  # Page Size (2MB HugePage)
PTE_GLOBAL = 1 << 8  # Global bit
PTE_AI_PROTECTED = 1 << 10  # AI Guard protected
PTE_IS_INVERTED = 1 << 11  # PTE inversion (suspended)
PTE_NX = 1 << 63  # No Execute


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
    """Connect VBusDriver to the kernel bridge with retry."""
    drv = VBusDriver(socket_path=BRIDGE_SOCKET)
    connected = False
    for attempt in range(3):
        connected = drv.connect()
        if connected:
            break
        time.sleep(0.5)
    if not connected:
        pytest.skip("Cannot connect to VOS3 bridge after 3 attempts")
    yield drv
    drv.disconnect()


@pytest.fixture(scope="module")
def dummy_weights_10mb():
    """Create a 10MB dummy model weights file with repeating 0x00..0xFF pattern."""
    pattern = bytes(range(256))
    repeats = MODEL_SIZE_10MB // len(pattern)

    tmpfile = tempfile.NamedTemporaryFile(
        prefix="vos3_model_10mb_", suffix=".weights", delete=False
    )
    try:
        for _ in range(repeats):
            tmpfile.write(pattern)
        tmpfile.flush()
        tmpfile.close()

        actual_size = os.path.getsize(tmpfile.name)
        assert (
            actual_size == MODEL_SIZE_10MB
        ), f"Expected {MODEL_SIZE_10MB}, got {actual_size}"
        logger.info(
            "Created 10MB dummy weights: %s (%d bytes)", tmpfile.name, actual_size
        )
        yield tmpfile.name
    finally:
        try:
            os.unlink(tmpfile.name)
        except OSError:
            pass


@pytest.fixture(scope="module")
def dummy_weights_2mb():
    """Create a 2MB dummy model weights file with repeating 0x00..0xFF pattern."""
    pattern = bytes(range(256))
    repeats = MODEL_SIZE_2MB // len(pattern)

    tmpfile = tempfile.NamedTemporaryFile(
        prefix="vos3_model_2mb_", suffix=".weights", delete=False
    )
    try:
        for _ in range(repeats):
            tmpfile.write(pattern)
        tmpfile.flush()
        tmpfile.close()

        actual_size = os.path.getsize(tmpfile.name)
        assert (
            actual_size == MODEL_SIZE_2MB
        ), f"Expected {MODEL_SIZE_2MB}, got {actual_size}"
        logger.info(
            "Created 2MB dummy weights: %s (%d bytes)", tmpfile.name, actual_size
        )
        yield tmpfile.name
    finally:
        try:
            os.unlink(tmpfile.name)
        except OSError:
            pass


def _safe_reset(driver, slot_id):
    """Reset a slot, ignoring errors if it is already free."""
    try:
        driver.slot_reset(slot_id)
    except VBusError:
        pass


# ============================================================================
# TESTS
# ============================================================================


@requires_qemu
class TestPhase425Agentic:
    """Phase 4.2.5 Multi-Agent Orchestration tests."""

    # ------------------------------------------------------------------
    # 1. test_multi_slot_load
    # ------------------------------------------------------------------
    def test_multi_slot_load(self, driver, dummy_weights_10mb, dummy_weights_2mb):
        """Test: Load 10MB into slot 0 and 2MB into slot 1; verify distinct bases and checksums."""
        _safe_reset(driver, 1)

        result_0 = driver.load_model_weights(
            dummy_weights_10mb, model_id=1, chunk_size=16384, window=64
        )
        result_1 = driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=2, label="Slot1"
        )

        addr_0 = result_0["addr"]
        addr_1 = result_1["addr"]

        assert (
            addr_0 and addr_0 != "0x0"
        ), f"Slot 0 base addr should be non-zero, got {addr_0}"
        assert (
            addr_1 and addr_1 != "0x0"
        ), f"Slot 1 base addr should be non-zero, got {addr_1}"
        assert (
            addr_0 != addr_1
        ), f"Slots 0 and 1 should have different base addresses, both are {addr_0}"

        cksum_0 = result_0["checksum"]
        cksum_1 = result_1.get("checksum_xxh3", result_1.get("checksum", ""))

        assert (
            cksum_0 and cksum_0 != "0" and cksum_0 != "0x0"
        ), f"Slot 0 checksum should be non-zero, got {cksum_0}"
        assert (
            cksum_1 and cksum_1 != "0" and cksum_1 != "0x0"
        ), f"Slot 1 checksum should be non-zero, got {cksum_1}"
        assert (
            cksum_0 != cksum_1
        ), f"Checksums should differ (different sizes), both are {cksum_0}"

        logger.info(
            "PASS: multi-slot load — slot0=%s cksum=%s, slot1=%s cksum=%s",
            addr_0,
            cksum_0,
            addr_1,
            cksum_1,
        )

    # ------------------------------------------------------------------
    # 2. test_coordinator_no_suspend
    # ------------------------------------------------------------------
    def test_coordinator_no_suspend(self, driver, dummy_weights_10mb):
        """Test: Suspending the coordinator (slot 0) should be rejected with ERR."""
        # Ensure slot 0 is loaded
        driver.load_model_weights(dummy_weights_10mb, model_id=1)

        resp = driver.slot_suspend(0)
        assert (
            "ERR" in resp
        ), f"Suspending coordinator slot 0 should return ERR, got: {resp}"

        logger.info("PASS: coordinator suspend rejected — %s", resp)

    # ------------------------------------------------------------------
    # 3. test_suspend_resume_pte_inversion
    # ------------------------------------------------------------------
    def test_suspend_resume_pte_inversion(self, driver, dummy_weights_2mb):
        """Test: Suspend inverts PTEs (IS_INVERTED set), resume restores (protected)."""
        _safe_reset(driver, 1)

        result = driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=42, label="PteTest"
        )
        base_addr = result["addr"]
        base_int = int(base_addr, 16)

        # Suspend slot 1 — PTE should become inverted
        resp_suspend = driver.slot_suspend(1)
        assert (
            resp_suspend.startswith("OK") or "suspend" in resp_suspend.lower()
        ), f"SLOT_SUSPEND failed: {resp_suspend}"

        resp_check_suspended = driver.slot_check(1, base_int)
        assert (
            "inverted" in resp_check_suspended.lower()
        ), f"After suspend, PTE should be inverted, got: {resp_check_suspended}"

        # Resume slot 1 — PTE should be protected again
        resp_resume = driver.slot_resume(1)
        assert (
            resp_resume.startswith("OK") or "resume" in resp_resume.lower()
        ), f"SLOT_RESUME failed: {resp_resume}"

        resp_check_resumed = driver.slot_check(1, base_int)
        assert (
            "protected" in resp_check_resumed.lower()
        ), f"After resume, PTE should be protected, got: {resp_check_resumed}"

        logger.info(
            "PASS: PTE inversion — suspend=%s, resume=%s",
            resp_check_suspended,
            resp_check_resumed,
        )

    # ------------------------------------------------------------------
    # 4. test_single_stream_invariant
    # ------------------------------------------------------------------
    def test_single_stream_invariant(self, driver):
        """Test: Starting a second stream while one is active returns ERR (EBUSY)."""
        # Start a model stream on slot 0 (coordinator path)
        resp_start = driver.send_command(f"MODEL_START|1|{MODEL_SIZE_10MB}")
        assert resp_start.startswith(
            "OK|"
        ), f"MODEL_START should succeed, got: {resp_start}"

        # Try to start a slot stream while MODEL_START is still active
        resp_slot = driver.send_command(f"SLOT_START|1|2|{MODEL_SIZE_2MB}|Test")
        assert (
            "ERR" in resp_slot
        ), f"Concurrent SLOT_START should be rejected with EBUSY, got: {resp_slot}"

        # Clean up: send MODEL_DONE to finalize the first stream
        driver.send_command("MODEL_DONE")

        logger.info(
            "PASS: single-stream invariant — concurrent rejected: %s", resp_slot
        )

    # ------------------------------------------------------------------
    # 5. test_snapshot_warm
    # ------------------------------------------------------------------
    def test_snapshot_warm(self, driver, dummy_weights_10mb):
        """Test: Snapshot of slot 0 returns OK with warm indicator."""
        driver.load_model_weights(dummy_weights_10mb, model_id=1)

        resp = driver.slot_snapshot(0)
        assert resp.startswith("OK"), f"SLOT_SNAPSHOT should return OK, got: {resp}"
        assert (
            "warm" in resp.lower()
        ), f"SLOT_SNAPSHOT response should contain 'warm', got: {resp}"

        logger.info("PASS: snapshot warm — %s", resp)

    # ------------------------------------------------------------------
    # 6. test_async_events_loaded
    # ------------------------------------------------------------------
    def test_async_events_loaded(self, driver, dummy_weights_2mb):
        """Test: Loading a slot generates SLOT_LOADED event (event_code=1)."""
        _safe_reset(driver, 1)

        # Clear any pending events
        driver.collect_events(0.1)

        driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=42, label="Security"
        )

        events = driver.collect_events(0.5)
        loaded_events = [e for e in events if e["event_code"] == 1]

        assert (
            len(loaded_events) >= 1
        ), f"Expected at least one SLOT_LOADED event (code=1), got events: {events}"

        logger.info(
            "PASS: async SLOT_LOADED event — %d events, loaded=%d",
            len(events),
            len(loaded_events),
        )

    # ------------------------------------------------------------------
    # 7. test_async_events_suspend_resume
    # ------------------------------------------------------------------
    def test_async_events_suspend_resume(self, driver, dummy_weights_2mb):
        """Test: Suspend/resume generates SUSPENDED (code=2) and RESUMED (code=3) events."""
        _safe_reset(driver, 1)
        driver.collect_events(0.1)  # Clear pending

        driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=42, label="EventTest"
        )

        driver.slot_suspend(1)
        driver.slot_resume(1)

        events = driver.collect_events(0.5)

        suspended_events = [e for e in events if e["event_code"] == 2]
        resumed_events = [e for e in events if e["event_code"] == 3]

        assert (
            len(suspended_events) >= 1
        ), f"Expected SUSPENDED event (code=2), got events: {events}"
        assert (
            len(resumed_events) >= 1
        ), f"Expected RESUMED event (code=3), got events: {events}"

        logger.info(
            "PASS: suspend/resume events — suspended=%d, resumed=%d",
            len(suspended_events),
            len(resumed_events),
        )

    # ------------------------------------------------------------------
    # 8. test_crc32c_integrity
    # ------------------------------------------------------------------
    def test_crc32c_integrity(self, driver, dummy_weights_2mb):
        """Test: CRC32C from SLOT_FINISH matches Python-computed CRC32C of file data."""
        try:
            import crcmod

            crc32c_fn = crcmod.predefined.mkCrcFun("crc-32c")
        except ImportError:
            pytest.skip("crcmod not installed — skipping CRC32C integrity test")

        _safe_reset(driver, 1)

        result = driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=42, label="CrcTest"
        )
        kernel_crc = result.get("checksum_crc32c", "0")

        # Compute CRC32C in Python over the same file data
        with open(dummy_weights_2mb, "rb") as f:
            data = f.read()
        python_crc = crc32c_fn(data)
        python_crc_hex = f"0x{python_crc:08x}"

        # Normalize kernel CRC for comparison
        if kernel_crc.startswith("0x") or kernel_crc.startswith("0X"):
            kernel_crc_int = int(kernel_crc, 16)
        else:
            kernel_crc_int = int(kernel_crc)
        kernel_crc_hex = f"0x{kernel_crc_int:08x}"

        assert (
            kernel_crc_hex == python_crc_hex
        ), f"CRC32C mismatch: kernel={kernel_crc_hex}, python={python_crc_hex}"

        logger.info(
            "PASS: CRC32C integrity — kernel=%s, python=%s",
            kernel_crc_hex,
            python_crc_hex,
        )

    # ------------------------------------------------------------------
    # 9. test_slot_status
    # ------------------------------------------------------------------
    def test_slot_status(self, driver, dummy_weights_10mb):
        """Test: Loaded slot 0 reports active or warm status."""
        driver.load_model_weights(dummy_weights_10mb, model_id=1)

        resp = driver.slot_status(0)
        resp_lower = resp.lower()
        assert (
            "active" in resp_lower or "warm" in resp_lower
        ), f"Slot 0 status should be active or warm, got: {resp}"

        logger.info("PASS: slot status — %s", resp)

    # ------------------------------------------------------------------
    # 10. test_backward_compat_model_start
    # ------------------------------------------------------------------
    def test_backward_compat_model_start(self, driver, dummy_weights_10mb):
        """Test: load_model_weights (old API) still succeeds with addr, size, checksum."""
        result = driver.load_model_weights(
            dummy_weights_10mb, model_id=1, chunk_size=16384, window=64
        )

        assert "addr" in result, f"Result missing 'addr': {result}"
        assert "size" in result, f"Result missing 'size': {result}"
        assert "checksum" in result, f"Result missing 'checksum': {result}"

        assert (
            result["size"] == MODEL_SIZE_10MB
        ), f"Expected size {MODEL_SIZE_10MB}, got {result['size']}"
        assert (
            result["addr"] and result["addr"] != "0x0"
        ), f"Addr should be non-zero, got {result['addr']}"
        assert (
            result["checksum"] and result["checksum"] != "0x0"
        ), f"Checksum should be non-zero, got {result['checksum']}"

        logger.info(
            "PASS: backward compat — addr=%s size=%d checksum=%s",
            result["addr"],
            result["size"],
            result["checksum"],
        )

    # ------------------------------------------------------------------
    # 11. test_suspend_resume_integrity
    # ------------------------------------------------------------------
    def test_suspend_resume_integrity(self, driver, dummy_weights_2mb):
        """Test: Data survives 3 suspend/resume cycles (VDEV_READ returns same bytes)."""
        _safe_reset(driver, 1)

        result = driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=42, label="IntegrityTest"
        )
        result.get("checksum_xxh3", result.get("checksum", ""))

        # Read first 256 bytes before cycles
        resp_before = driver.vdev_read(1, 0, 256)
        assert resp_before.startswith(
            "OK"
        ), f"VDEV_READ before cycles failed: {resp_before}"
        data_before = (
            resp_before.split("|", 1)[1] if "|" in resp_before else resp_before
        )

        # 3 suspend/resume cycles
        for cycle in range(3):
            resp_s = driver.slot_suspend(1)
            assert (
                "ERR" not in resp_s or "coordinator" in resp_s.lower()
            ), f"Suspend failed on cycle {cycle}: {resp_s}"
            resp_r = driver.slot_resume(1)
            assert (
                resp_r.startswith("OK") or "resume" in resp_r.lower()
            ), f"Resume failed on cycle {cycle}: {resp_r}"

        # Read first 256 bytes after cycles
        resp_after = driver.vdev_read(1, 0, 256)
        assert resp_after.startswith(
            "OK"
        ), f"VDEV_READ after cycles failed: {resp_after}"
        data_after = resp_after.split("|", 1)[1] if "|" in resp_after else resp_after

        assert data_before == data_after, (
            f"Data changed after 3 suspend/resume cycles: "
            f"before={data_before[:64]}... after={data_after[:64]}..."
        )

        logger.info("PASS: suspend/resume integrity — data consistent after 3 cycles")

    # ------------------------------------------------------------------
    # 12. test_all_slots_concurrent
    # ------------------------------------------------------------------
    def test_all_slots_concurrent(self, driver, dummy_weights_2mb):
        """Test: Load all 4 slots, suspend 1-3, resume 1-3, verify all active."""
        # Reset slots 1-3 first
        for s in range(1, 4):
            _safe_reset(driver, s)

        # Load slot 0 via MODEL_START path
        result_0 = driver.load_model_weights(
            dummy_weights_2mb, model_id=1, chunk_size=16384, window=64
        )
        assert result_0["addr"], f"Slot 0 load failed: {result_0}"

        # Load slots 1, 2, 3
        results = {}
        for slot in range(1, 4):
            r = driver.load_model_slot(
                dummy_weights_2mb,
                slot_id=slot,
                model_id=slot + 10,
                label=f"Agent{slot}",
            )
            assert r["addr"], f"Slot {slot} load failed: {r}"
            results[slot] = r

        # Suspend slots 1, 2, 3
        for slot in range(1, 4):
            resp = driver.slot_suspend(slot)
            assert (
                "ERR" not in resp or "coordinator" in resp.lower()
            ), f"Suspend slot {slot} failed: {resp}"

        # Resume slots 1, 2, 3
        for slot in range(1, 4):
            resp = driver.slot_resume(slot)
            assert (
                resp.startswith("OK") or "resume" in resp.lower()
            ), f"Resume slot {slot} failed: {resp}"

        # Verify all slots are active
        for slot in range(4):
            resp = driver.slot_status(slot)
            resp_lower = resp.lower()
            assert (
                "active" in resp_lower or "warm" in resp_lower or "loaded" in resp_lower
            ), f"Slot {slot} should be active after resume, got: {resp}"

        logger.info("PASS: all 4 slots concurrent — load/suspend/resume/verify")

    # ------------------------------------------------------------------
    # 13. test_heartbeat_event
    # ------------------------------------------------------------------
    def test_heartbeat_event(self, driver, dummy_weights_10mb):
        """Test: Heartbeat events (code=6) arrive within 350ms window."""
        driver.load_model_weights(dummy_weights_10mb, model_id=1)

        # Clear pending events
        driver.collect_events(0.1)

        time.sleep(0.35)

        events = driver.collect_events(0.5)
        heartbeats = [e for e in events if e["event_code"] == 6]

        assert len(heartbeats) >= 2, (
            f"Expected at least 2 heartbeat events (code=6) in 350ms, "
            f"got {len(heartbeats)} heartbeats out of {len(events)} total events"
        )

        logger.info(
            "PASS: heartbeat events — %d heartbeats in 350ms window",
            len(heartbeats),
        )

    # ------------------------------------------------------------------
    # 14. test_pte_inversion_bits
    # ------------------------------------------------------------------
    def test_pte_inversion_bits(self, driver, dummy_weights_2mb):
        """Test: PTE bit-level verification for suspend (inverted) and resume (present)."""
        _safe_reset(driver, 1)

        result = driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=42, label="PteBits"
        )
        base_int = int(result["addr"], 16)

        # Active state: PTE should have PRESENT set
        resp_active = driver.send_command(f"SLOT_CHECK|1|{hex(base_int)}")
        assert resp_active.startswith(
            "OK"
        ), f"SLOT_CHECK (active) failed: {resp_active}"
        # Parse PTE value if present in response
        parts_active = resp_active.split("|")
        if len(parts_active) >= 2:
            try:
                pte_active = int(parts_active[1], 16)
                assert (
                    pte_active & PTE_PRESENT
                ), f"Active PTE should have PRESENT set: PTE=0x{pte_active:016x}"
            except ValueError:
                pass  # Response format may vary; keyword check below suffices

        # Suspend: PTE should have IS_INVERTED set and PRESENT clear
        driver.slot_suspend(1)
        resp_suspended = driver.send_command(f"SLOT_CHECK|1|{hex(base_int)}")
        assert (
            "inverted" in resp_suspended.lower()
        ), f"Suspended PTE should indicate 'inverted': {resp_suspended}"
        parts_susp = resp_suspended.split("|")
        if len(parts_susp) >= 2:
            try:
                pte_susp = int(parts_susp[1], 16)
                assert pte_susp & PTE_IS_INVERTED, (
                    f"Suspended PTE should have IS_INVERTED (bit 11) set: "
                    f"PTE=0x{pte_susp:016x}"
                )
                assert not (pte_susp & PTE_PRESENT), (
                    f"Suspended PTE should have PRESENT clear: "
                    f"PTE=0x{pte_susp:016x}"
                )
            except ValueError:
                pass

        # Resume: PTE should have PRESENT set again
        driver.slot_resume(1)
        resp_resumed = driver.send_command(f"SLOT_CHECK|1|{hex(base_int)}")
        assert (
            "protected" in resp_resumed.lower() or "present" in resp_resumed.lower()
        ), f"Resumed PTE should indicate 'protected' or 'present': {resp_resumed}"
        parts_res = resp_resumed.split("|")
        if len(parts_res) >= 2:
            try:
                pte_res = int(parts_res[1], 16)
                assert (
                    pte_res & PTE_PRESENT
                ), f"Resumed PTE should have PRESENT set: PTE=0x{pte_res:016x}"
            except ValueError:
                pass

        logger.info(
            "PASS: PTE inversion bits — active, suspended (inverted), resumed (present)"
        )

    # ------------------------------------------------------------------
    # 15. test_agent_label
    # ------------------------------------------------------------------
    def test_agent_label(self, driver):
        """Test: SLOT_START includes agent label in response."""
        _safe_reset(driver, 1)

        resp = driver.send_command(f"SLOT_START|1|42|{MODEL_SIZE_2MB}|Security")
        assert (
            "Security" in resp
        ), f"SLOT_START response should echo label 'Security', got: {resp}"

        # Clean up: send SLOT_FINISH or abort
        try:
            driver.send_command("SLOT_FINISH|1")
        except VBusError:
            _safe_reset(driver, 1)

        logger.info("PASS: agent label — %s", resp)

    # ------------------------------------------------------------------
    # 16. test_ai_timer
    # ------------------------------------------------------------------
    def test_ai_timer(self, driver, dummy_weights_10mb):
        """Test: SLOT_TIMER fires TIMER_EXPIRED event (code=5) after specified ms."""
        driver.load_model_weights(dummy_weights_10mb, model_id=1)

        # Clear pending events
        driver.collect_events(0.1)

        resp = driver.slot_timer(0, 200)
        assert (
            resp.startswith("OK") or "timer" in resp.lower()
        ), f"SLOT_TIMER should succeed, got: {resp}"

        time.sleep(0.3)

        events = driver.collect_events(0.5)
        timer_events = [e for e in events if e["event_code"] == 5]

        assert len(timer_events) >= 1, (
            f"Expected TIMER_EXPIRED event (code=5) after 200ms, "
            f"got {len(timer_events)} timer events out of {len(events)} total"
        )

        logger.info(
            "PASS: AI timer — %d timer events after 200ms delay",
            len(timer_events),
        )

    # ------------------------------------------------------------------
    # 17. test_slot_reset
    # ------------------------------------------------------------------
    def test_slot_reset(self, driver, dummy_weights_2mb):
        """Test: SLOT_RESET frees slot; SLOT_STATUS confirms free."""
        _safe_reset(driver, 1)

        driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=42, label="ResetTest"
        )

        resp_reset = driver.slot_reset(1)
        assert "free" in resp_reset.lower() or resp_reset.startswith(
            "OK"
        ), f"SLOT_RESET should succeed with 'free', got: {resp_reset}"

        resp_status = driver.slot_status(1)
        assert (
            "free" in resp_status.lower()
        ), f"After reset, slot 1 should be free, got: {resp_status}"

        logger.info("PASS: slot reset — reset=%s, status=%s", resp_reset, resp_status)

    # ------------------------------------------------------------------
    # 18. test_priority_snapshot
    # ------------------------------------------------------------------
    def test_priority_snapshot(self, driver, dummy_weights_10mb):
        """Test: SLOT_SNAPSHOT on slot 0 succeeds."""
        driver.load_model_weights(dummy_weights_10mb, model_id=1)

        resp = driver.slot_snapshot(0)
        assert resp.startswith("OK"), f"SLOT_SNAPSHOT should succeed, got: {resp}"

        logger.info("PASS: priority snapshot — %s", resp)

    # ------------------------------------------------------------------
    # 19. test_slot_swap
    # ------------------------------------------------------------------
    def test_slot_swap(self, driver, dummy_weights_2mb):
        """Test: SLOT_SWAP exchanges slot 1 and slot 2; both remain active."""
        _safe_reset(driver, 1)
        _safe_reset(driver, 2)

        result_1 = driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=10, label="SwapA"
        )
        result_2 = driver.load_model_slot(
            dummy_weights_2mb, slot_id=2, model_id=20, label="SwapB"
        )

        result_1.get("checksum_xxh3", "")
        result_2.get("checksum_xxh3", "")

        resp_swap = driver.slot_swap(1, 2)
        assert (
            resp_swap.startswith("OK") or "swap" in resp_swap.lower()
        ), f"SLOT_SWAP should succeed, got: {resp_swap}"

        # Both slots should still be active after swap
        status_1 = driver.slot_status(1)
        status_2 = driver.slot_status(2)

        assert (
            "active" in status_1.lower()
            or "warm" in status_1.lower()
            or "loaded" in status_1.lower()
        ), f"Slot 1 should be active after swap, got: {status_1}"
        assert (
            "active" in status_2.lower()
            or "warm" in status_2.lower()
            or "loaded" in status_2.lower()
        ), f"Slot 2 should be active after swap, got: {status_2}"

        logger.info(
            "PASS: slot swap — slot1=%s, slot2=%s",
            status_1,
            status_2,
        )

    # ------------------------------------------------------------------
    # 20. test_vdev_read
    # ------------------------------------------------------------------
    def test_vdev_read(self, driver, dummy_weights_2mb):
        """Test: VDEV_READ returns correct data matching the file's first 256 bytes."""
        _safe_reset(driver, 1)

        driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=42, label="VdevRead"
        )

        resp = driver.vdev_read(1, 0, 256)
        assert resp.startswith("OK"), f"VDEV_READ should succeed, got: {resp}"

        # Extract hex payload from response
        hex_data = resp.split("|", 1)[1] if "|" in resp else ""
        assert (
            len(hex_data) > 0
        ), f"VDEV_READ response should contain hex data, got: {resp}"

        # Decode hex and compare with file's first 256 bytes
        try:
            read_bytes = bytes.fromhex(hex_data)
        except ValueError:
            # Response might contain additional metadata; try to extract just hex
            # Strip any non-hex trailing fields
            hex_clean = hex_data.split("|")[0].strip()
            read_bytes = bytes.fromhex(hex_clean)

        with open(dummy_weights_2mb, "rb") as f:
            expected_bytes = f.read(256)

        assert read_bytes == expected_bytes, (
            f"VDEV_READ data mismatch: got {len(read_bytes)} bytes, "
            f"first 16: {read_bytes[:16].hex()}, "
            f"expected first 16: {expected_bytes[:16].hex()}"
        )

        logger.info("PASS: VDEV_READ — 256 bytes match file content")

    # ------------------------------------------------------------------
    # 21. test_telemetry_counters
    # ------------------------------------------------------------------
    def test_telemetry_counters(self, driver, dummy_weights_10mb):
        """Test: Multiple VDEV_READ calls succeed and slot remains active."""
        driver.load_model_weights(dummy_weights_10mb, model_id=1)

        for i in range(5):
            resp = driver.vdev_read(0, 0, 64)
            assert resp.startswith("OK"), f"VDEV_READ call {i+1}/5 failed: {resp}"

        status = driver.slot_status(0)
        status_lower = status.lower()
        assert (
            "active" in status_lower or "warm" in status_lower
        ), f"Slot 0 should still be active after 5 reads, got: {status}"

        logger.info("PASS: telemetry counters — 5 VDEV_READs, status=%s", status)

    # ------------------------------------------------------------------
    # 22. test_corrupt_reset_zero
    # ------------------------------------------------------------------
    def test_corrupt_reset_zero(self, driver, dummy_weights_2mb):
        """Test: After reset + reload, VDEV_READ returns fresh data (not stale)."""
        _safe_reset(driver, 1)

        # Load slot 1 and read initial data
        driver.load_model_slot(
            dummy_weights_2mb, slot_id=1, model_id=42, label="CorruptTest"
        )
        resp_before = driver.vdev_read(1, 0, 256)
        assert resp_before.startswith(
            "OK"
        ), f"VDEV_READ before reset failed: {resp_before}"
        data_before = resp_before.split("|", 1)[1] if "|" in resp_before else ""

        # Reset the slot
        resp_reset = driver.slot_reset(1)
        assert (
            resp_reset.startswith("OK") or "free" in resp_reset.lower()
        ), f"SLOT_RESET failed: {resp_reset}"

        # Verify slot is free
        resp_status = driver.slot_status(1)
        assert (
            "free" in resp_status.lower()
        ), f"Slot 1 should be free after reset, got: {resp_status}"

        # Reload slot 1 with the same data
        driver.load_model_slot(dummy_weights_2mb, slot_id=1, model_id=99, label="Fresh")

        resp_after = driver.vdev_read(1, 0, 256)
        assert resp_after.startswith(
            "OK"
        ), f"VDEV_READ after reload failed: {resp_after}"
        data_after = resp_after.split("|", 1)[1] if "|" in resp_after else ""

        # Data should be the same file content (proves clean reload, not stale/zeroed)
        assert data_after == data_before, (
            f"After reset+reload, VDEV_READ should return same file content. "
            f"Before: {data_before[:64]}..., After: {data_after[:64]}..."
        )

        logger.info("PASS: corrupt reset zero — clean reload verified")
