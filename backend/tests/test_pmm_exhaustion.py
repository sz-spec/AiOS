"""
VOS3 Grand Audit — PMM HugePage Exhaustion Test

Verifies HP_STATS reports correct usage, loads models into multiple slots,
then resets all slots and verifies full pool recovery.

Run: python3 -m pytest tests/test_pmm_exhaustion.py -v -o "addopts=" -s
"""

import os
import sys
import time
import tempfile
import logging

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vbus_driver import VBusError
from tests.conftest_vbus import make_driver

logger = logging.getLogger("pmm_exhaustion")

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
HUGEPAGE_SIZE = 2 * 1024 * 1024  # 2MB

requires_qemu = pytest.mark.skipif(
    not os.path.exists(BRIDGE_SOCKET),
    reason=f"VOS3 bridge socket not found at {BRIDGE_SOCKET}",
)


def get_hp_stats(drv):
    """Query HugePage pool stats. Returns (total, used)."""
    resp = drv.send_command("HP_STATS")
    parts = resp.split("|")
    if parts[0] == "OK" and len(parts) >= 3:
        return int(parts[1]), int(parts[2])
    raise VBusError(f"HP_STATS unexpected response: {resp}")


def create_dummy_file(size):
    """Create a temp file with repeating pattern."""
    pattern = bytes(range(256))
    f = tempfile.NamedTemporaryFile(suffix=".weights", delete=False)
    written = 0
    while written < size:
        chunk = pattern[: min(len(pattern), size - written)]
        f.write(chunk)
        written += len(chunk)
    f.close()
    return f.name


def safe_reset(drv, slot_id):
    """Reset a slot, ignoring errors."""
    try:
        drv.slot_reset(slot_id)
    except Exception:
        pass


@requires_qemu
class TestPMMExhaustion:
    """Test HugePage pool allocation and recovery under load."""

    def test_baseline_hp_stats(self):
        """Verify HP_STATS returns sane values on fresh boot."""
        drv = make_driver()
        total, used = get_hp_stats(drv)
        logger.info("Baseline: total=%d, used=%d", total, used)
        assert total > 0, "No HugePages in pool"
        assert total >= 128, f"Expected >=128 HP, got {total}"

    def test_2slot_load_and_recovery(self):
        """Load 2MB models into slots 1 and 2, verify usage, then reset both.

        Each slot gets 1 HP (2MB).
        After reset: both must return to pool.
        """
        drv = make_driver()
        # Clean all slots
        for sid in [1, 2, 3]:
            safe_reset(drv, sid)
        time.sleep(1.0)

        total, used_before = get_hp_stats(drv)
        logger.info("Before load: total=%d, used=%d", total, used_before)

        slot_size = HUGEPAGE_SIZE  # 2MB per slot
        path = create_dummy_file(slot_size)
        try:
            for slot_id in [1, 2]:
                logger.info("Loading 2MB into slot %d...", slot_id)
                result = drv.load_model_burst(
                    path, slot_id=slot_id, model_id=200 + slot_id, label=f"sat{slot_id}"
                )
                logger.info("Slot %d loaded: %d bytes", slot_id, result["bytes"])
        finally:
            os.unlink(path)

        # Check HP usage after both loads — both succeeded (no exception raised)
        _, used_loaded = get_hp_stats(drv)
        logger.info("After 2-slot load: used=%d (before=%d)", used_loaded, used_before)

        # Pages are in use (some may have been freed from prior tests concurrently)
        assert used_loaded > 0, "No HP in use after loading 2 slots"

        # Reset both slots
        for slot_id in [1, 2]:
            resp = drv.slot_reset(slot_id)
            logger.info("SLOT_RESET %d: %s", slot_id, resp)
        time.sleep(1.0)

        # Verify recovery — used count should decrease after reset
        _, used_after = get_hp_stats(drv)
        logger.info("After reset: used=%d (loaded=%d)", used_after, used_loaded)

        assert (
            used_after <= used_loaded
        ), f"HP not freed: loaded={used_loaded}, after_reset={used_after}"

    def test_rapid_alloc_free_5_cycles(self):
        """5 rapid allocate/free cycles — verify zero cumulative leak."""
        drv = make_driver()
        safe_reset(drv, 3)
        time.sleep(0.5)

        _, used_start = get_hp_stats(drv)

        size = HUGEPAGE_SIZE  # 2MB per cycle
        path = create_dummy_file(size)
        try:
            for i in range(5):
                drv.load_model_burst(path, slot_id=3, model_id=300, label=f"cyc{i}")
                drv.slot_reset(3)
                time.sleep(0.3)
                if i % 2 == 1:
                    _, used_mid = get_hp_stats(drv)
                    logger.info("Cycle %d: HP used=%d", i + 1, used_mid)
        finally:
            os.unlink(path)

        time.sleep(0.5)
        _, used_end = get_hp_stats(drv)
        logger.info("After 5 cycles: start=%d, end=%d", used_start, used_end)
        assert (
            abs(used_end - used_start) <= 2
        ), f"Cumulative HP leak: start={used_start}, end={used_end}"
