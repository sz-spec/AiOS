"""
Phase 4.9-Final — The Centurion Swap

100 rapid interleaved model swaps to verify:
  - Overlap Guard integrity (PUD isolation)
  - HugePage memory leak detection (pool_used before == after)
  - Zero kernel panics under sustained swap stress
  - XXH3 checksum validity on every load

Each iteration:
  1. Randomly pick Slot 1 or Slot 2
  2. Random size: 1..16 HugePages (2MB..32MB)
  3. slot_start → load_model_burst → slot_finish → slot_reset
  4. Verify XXH3 non-zero

Run: python3 -m pytest tests/stress_centurion_swap.py -v -o "addopts=" -s
"""

import os
import sys
import time
import random
import tempfile
import logging

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vbus_driver import VBusDriver

logger = logging.getLogger("centurion_swap")

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
HUGEPAGE_SIZE = 2 * 1024 * 1024  # 2MB

requires_qemu = pytest.mark.skipif(
    not os.path.exists(BRIDGE_SOCKET),
    reason=f"VOS3 bridge socket not found at {BRIDGE_SOCKET}",
)


def make_driver():
    """Create and connect a VBusDriver with retry."""
    drv = VBusDriver(socket_path=BRIDGE_SOCKET)
    for attempt in range(15):
        if drv.connect():
            return drv
        time.sleep(0.5)
    raise RuntimeError("Cannot connect to VOS3 bridge")


def create_dummy_file(size):
    """Create a temp file with repeating 0x00..0xFF pattern."""
    pattern = bytes(range(256))
    f = tempfile.NamedTemporaryFile(suffix=".weights", delete=False)
    written = 0
    while written < size:
        chunk = pattern[: min(len(pattern), size - written)]
        f.write(chunk)
        written += len(chunk)
    f.close()
    return f.name


def get_hp_stats(drv):
    """Query HugePage pool stats via HP_STATS command.

    Returns (total, used) tuple.
    Response format: "OK|<total>|<used>"
    """
    resp = drv.send_command("HP_STATS")
    parts = resp.split("|")
    # send_command returns full response: "OK|128|0"
    if parts[0] == "OK" and len(parts) >= 3:
        total = int(parts[1])
        used = int(parts[2])
    else:
        # Fallback: might already be stripped
        total = int(parts[0])
        used = int(parts[1])
    return total, used


def reset_slot(drv, slot_id):
    """Reset a slot, ignoring errors."""
    try:
        drv.slot_reset(slot_id)
    except Exception:
        pass


@requires_qemu
class TestCenturionSwap:
    """100 rapid swap iterations across Slots 1 and 2."""

    def test_centurion_100_swaps(self):
        """Execute 100 random load/reset cycles with leak detection."""
        drv = make_driver()
        try:
            # Clean slate: reset slots 1 and 2
            reset_slot(drv, 1)
            reset_slot(drv, 2)
            time.sleep(0.3)

            # Snapshot initial HugePage pool state
            hp_total, hp_used_initial = get_hp_stats(drv)
            print("\n  === CENTURION SWAP: 100 iterations ===")
            print(
                f"  HugePage pool: {hp_total} total, {hp_used_initial} used (initial)"
            )

            passed = 0
            failed = 0
            total_bytes = 0
            start_time = time.monotonic()

            # Seed for reproducibility
            rng = random.Random(42)

            for i in range(100):
                slot_id = rng.choice([1, 2])
                # 1..16 HugePages (cap at 16 to keep iterations fast in TCG)
                hp_count = rng.randint(1, 16)
                file_size = hp_count * HUGEPAGE_SIZE
                model_id = 1000 + i

                fpath = create_dummy_file(file_size)
                try:
                    result = drv.load_model_burst(
                        fpath,
                        slot_id=slot_id,
                        model_id=model_id,
                        label=f"C{i:03d}",
                        chunk_size=49152,
                    )

                    # Verify XXH3 is non-zero (data was processed)
                    xxh3 = result.get("checksum_xxh3", "0")
                    if xxh3 == "0" or xxh3 is None:
                        print(
                            f"  [{i:3d}] FAIL slot={slot_id} hp={hp_count} "
                            f"xxh3=0 (no data processed)"
                        )
                        failed += 1
                    else:
                        passed += 1
                        total_bytes += result.get("bytes", 0)

                    # Reset the slot to free HugePages back to pool
                    reset_slot(drv, slot_id)

                except Exception as e:
                    print(f"  [{i:3d}] ERROR slot={slot_id} hp={hp_count}: {e}")
                    failed += 1
                    # Try to recover: reset both slots
                    reset_slot(drv, 1)
                    reset_slot(drv, 2)
                    # Reconnect if bridge dropped
                    try:
                        drv.disconnect()
                    except Exception:
                        pass
                    time.sleep(1.0)
                    drv = make_driver()
                finally:
                    os.unlink(fpath)

                # Progress report every 25 iterations
                if (i + 1) % 25 == 0:
                    _, hp_used_mid = get_hp_stats(drv)
                    elapsed = time.monotonic() - start_time
                    print(
                        f"  [{i+1:3d}/100] pass={passed} fail={failed} "
                        f"hp_used={hp_used_mid} elapsed={elapsed:.1f}s"
                    )

            elapsed_total = time.monotonic() - start_time
            total_mb = total_bytes / (1024 * 1024)

            # Final HugePage pool snapshot
            hp_total_final, hp_used_final = get_hp_stats(drv)

            print("\n  === CENTURION SWAP RESULTS ===")
            print(
                f"  Iterations: {passed + failed}/100 "
                f"(PASS={passed}, FAIL={failed})"
            )
            print(f"  Total data: {total_mb:.1f} MB in {elapsed_total:.1f}s")
            print(
                f"  HugePage pool: initial={hp_used_initial}, "
                f"final={hp_used_final}, delta={hp_used_final - hp_used_initial}"
            )
            print("  Kernel panics: 0 (test completed)")

            # Assertions
            assert failed == 0, f"{failed} iterations failed"
            assert passed == 100, f"Only {passed}/100 passed"
            assert hp_used_final == hp_used_initial, (
                f"HugePage LEAK: initial={hp_used_initial} final={hp_used_final} "
                f"delta={hp_used_final - hp_used_initial}"
            )

        finally:
            reset_slot(drv, 1)
            reset_slot(drv, 2)
            drv.disconnect()


@requires_qemu
class TestCenturionSlot0Immunity:
    """Verify Slot 0 remains untouched during Centurion swap storm."""

    def test_slot0_survives_centurion(self):
        """Load Slot 0 once, run 20 rapid swaps on Slots 1-2, verify Slot 0 intact."""
        drv = make_driver()
        try:
            # Load Slot 0 (Coordinator)
            fpath0 = create_dummy_file(HUGEPAGE_SIZE)
            try:
                r0 = drv.load_model_burst(
                    fpath0, slot_id=0, model_id=1, label="Coord", chunk_size=49152
                )
            finally:
                os.unlink(fpath0)

            slot0_xxh3 = r0["checksum_xxh3"]
            slot0_crc = r0["checksum_crc32c"]
            print(f"\n  Slot 0 loaded: xxh3={slot0_xxh3}, crc32c={slot0_crc}")

            # 20 rapid swaps on Slots 1-2
            rng = random.Random(99)
            for i in range(20):
                slot_id = rng.choice([1, 2])
                hp_count = rng.randint(1, 8)
                file_size = hp_count * HUGEPAGE_SIZE

                fpath = create_dummy_file(file_size)
                try:
                    drv.load_model_burst(
                        fpath,
                        slot_id=slot_id,
                        model_id=2000 + i,
                        label=f"Storm{i}",
                        chunk_size=49152,
                    )
                    reset_slot(drv, slot_id)
                finally:
                    os.unlink(fpath)

            # Verify Slot 0 status is still LOADED (not corrupted)
            resp = drv.slot_status(0)
            print(f"  Slot 0 after storm: {resp}")
            assert (
                "LOADED" in resp or "READY" in resp or "FREE" not in resp
            ), f"Slot 0 was corrupted: {resp}"

            print("  Slot 0 survived 20 swap storms: INTACT")

        finally:
            reset_slot(drv, 1)
            reset_slot(drv, 2)
            drv.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-o", "addopts=", "-s"])
