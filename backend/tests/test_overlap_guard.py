"""
VOS3 Grand Audit — Overlap Guard Rapid Cycle Test

Rapid SLOT_START / SLOT_RESET cycles with 2MB loads to stress-test
the PUD-isolated slot bases. Verifies:
  - Zero "Reserved-bit PTE" panics in console log
  - Zero "OVERLAP" warnings in console log
  - HP pool returns to baseline after all cycles

Run: python3 -m pytest tests/test_overlap_guard.py -v -o "addopts=" -s
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

logger = logging.getLogger("overlap_guard")

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
CONSOLE_LOG = os.environ.get("VOS3_CONSOLE_LOG", "/tmp/vos3_console.log")
HUGEPAGE_SIZE = 2 * 1024 * 1024  # 2MB

requires_qemu = pytest.mark.skipif(
    not os.path.exists(BRIDGE_SOCKET),
    reason=f"VOS3 bridge socket not found at {BRIDGE_SOCKET}",
)


def get_hp_stats(drv):
    """Returns (total, used)."""
    resp = drv.send_command("HP_STATS")
    parts = resp.split("|")
    if parts[0] == "OK" and len(parts) >= 3:
        return int(parts[1]), int(parts[2])
    raise VBusError(f"HP_STATS parse error: {resp}")


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


def count_console_errors():
    """Count PTE panics and overlap warnings in console log."""
    panics = 0
    overlaps = 0
    try:
        with open(CONSOLE_LOG, "r", errors="replace") as f:
            for line in f:
                if "Reserved-bit" in line or "reserved bit" in line.lower():
                    panics += 1
                if "OVERLAP" in line:
                    overlaps += 1
    except FileNotFoundError:
        pass
    return panics, overlaps


@requires_qemu
class TestOverlapGuard:
    """Rapid SLOT_START/SLOT_RESET cycles with 2MB loads."""

    def test_10_rapid_cycles_zero_panics(self):
        """10 load/reset cycles rotating slots — zero PTE panics allowed."""
        drv = make_driver()
        # Clean all slots
        for sid in [1, 2, 3]:
            safe_reset(drv, sid)
        time.sleep(1.0)

        panics_before, overlaps_before = count_console_errors()
        _, used_before = get_hp_stats(drv)
        logger.info("Baseline: HP used=%d, panics=%d", used_before, panics_before)

        size = HUGEPAGE_SIZE  # 2MB per cycle
        path = create_dummy_file(size)
        successes = 0
        failures = 0

        try:
            for i in range(10):
                slot_id = (i % 3) + 1  # Rotate slots 1, 2, 3
                try:
                    drv.load_model_burst(
                        path, slot_id=slot_id, model_id=400 + i, label=f"og{i}"
                    )
                    safe_reset(drv, slot_id)
                    successes += 1
                    time.sleep(0.2)
                except Exception as e:
                    failures += 1
                    logger.warning("Cycle %d failed: %s", i + 1, e)
                    safe_reset(drv, slot_id)
                    time.sleep(0.5)

                if i % 5 == 4:
                    _, used_mid = get_hp_stats(drv)
                    logger.info("Cycle %d: HP used=%d", i + 1, used_mid)
        finally:
            os.unlink(path)

        # Final checks
        panics_after, overlaps_after = count_console_errors()
        _, used_after = get_hp_stats(drv)

        new_panics = panics_after - panics_before
        new_overlaps = overlaps_after - overlaps_before

        logger.info(
            "Results: %d/%d success, %d new panics, %d new overlaps",
            successes,
            10,
            new_panics,
            new_overlaps,
        )
        logger.info("HP: before=%d, after=%d", used_before, used_after)

        assert new_panics == 0, f"PTE panics detected: {new_panics}"
        assert new_overlaps == 0, f"Overlap warnings detected: {new_overlaps}"
        assert successes >= 8, f"Too many failures: {failures}/10"
        assert (
            abs(used_after - used_before) <= 2
        ), f"HP leak: before={used_before}, after={used_after}"

    def test_alternating_slot_sizes(self):
        """Alternate between small (2MB) loads on slot 1 and slot 2.

        Tests that the HugePage allocation/free path works correctly
        when rapidly switching between different slots.
        """
        drv = make_driver()
        for sid in [1, 2, 3]:
            safe_reset(drv, sid)
        time.sleep(1.0)

        _, used_before = get_hp_stats(drv)
        panics_before, _ = count_console_errors()

        path = create_dummy_file(HUGEPAGE_SIZE)  # 2MB
        successes = 0
        try:
            for i in range(6):
                slot_id = 1 if i % 2 == 0 else 2
                try:
                    drv.load_model_burst(
                        path, slot_id=slot_id, model_id=500 + i, label=f"alt{i}"
                    )
                    safe_reset(drv, slot_id)
                    successes += 1
                    time.sleep(0.2)
                except Exception as e:
                    logger.warning("Alt cycle %d failed: %s", i + 1, e)
                    safe_reset(drv, slot_id)
                    time.sleep(0.5)
        finally:
            os.unlink(path)

        panics_after, _ = count_console_errors()
        _, used_after = get_hp_stats(drv)

        assert panics_after - panics_before == 0, "PTE panics in alternating test"
        assert successes >= 4, f"Too many failures: {6 - successes}/6"
        assert (
            abs(used_after - used_before) <= 2
        ), f"HP leak in alternating: {used_before} -> {used_after}"
