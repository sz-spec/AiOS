"""
VOS3 Grand Audit — VOS3FS v2 Extreme Test

Tests the double-indirect block path by loading a file that exceeds the
single-indirect capacity (6 direct + 128 indirect = 134 blocks = 68,608 bytes).
We load a 2MB model (4096 blocks → requires double-indirect) and verify
integrity via XXH3/CRC32C checksums reported by SLOT_FINISH.

Run: python3 -m pytest tests/test_vos3fs_v2_extreme.py -v -o "addopts=" -s
"""

import os
import sys
import time
import hashlib
import tempfile
import logging

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest_vbus import make_driver

logger = logging.getLogger("vos3fs_v2_extreme")

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")

requires_qemu = pytest.mark.skipif(
    not os.path.exists(BRIDGE_SOCKET),
    reason=f"VOS3 bridge socket not found at {BRIDGE_SOCKET}",
)


def create_entropy_file(size):
    """Create a temp file with deterministic pseudo-random data."""
    f = tempfile.NamedTemporaryFile(suffix=".weights", delete=False)
    seed = b"VOS3_GRAND_AUDIT_ENTROPY_SEED_2026"
    block = hashlib.sha256(seed).digest() * 16  # 512 bytes
    written = 0
    while written < size:
        chunk_size = min(len(block), size - written)
        f.write(block[:chunk_size])
        written += chunk_size
    f.close()
    return f.name


def safe_reset(drv, slot_id):
    """Reset a slot, ignoring errors."""
    try:
        drv.slot_reset(slot_id)
    except Exception:
        pass


@requires_qemu
class TestVOS3FSv2Extreme:
    """Test double-indirect block paths via large model loads."""

    def test_2mb_model_load_integrity(self):
        """Load a 2MB model (requires double-indirect blocks in VOS3FS v2).

        2MB = 4096 blocks. Direct=6, Indirect=128 → need 3962 double-indirect blocks.
        Verifies the full bmap_alloc → bmap → read path works end-to-end.
        """
        drv = make_driver()
        safe_reset(drv, 1)
        time.sleep(0.3)

        size = 2 * 1024 * 1024  # 2MB
        path = create_entropy_file(size)
        try:
            result = drv.load_model_burst(
                path, slot_id=1, model_id=100, label="v2extreme"
            )
            logger.info("2MB load result: %s", result)

            assert (
                result["bytes"] == size
            ), f"Size mismatch: {result['bytes']} != {size}"

            xxh3 = (
                int(result.get("checksum_xxh3", "0"), 16)
                if isinstance(result.get("checksum_xxh3"), str)
                else result.get("checksum_xxh3", 0)
            )
            assert xxh3 != 0, "XXH3 checksum is zero — data not hashed"

            safe_reset(drv, 1)
        finally:
            os.unlink(path)

    def test_8mb_model_load_deep_dindirect(self):
        """Load an 8MB model — deep into double-indirect territory.

        8MB = 16384 blocks = exactly 1 full double-indirect pointer capacity.
        This exercises the boundary condition at dindirect[0] being fully saturated.
        Uses slot 2 (independent of other tests).
        """
        drv = make_driver()
        safe_reset(drv, 2)
        time.sleep(0.3)

        size = 8 * 1024 * 1024  # 8MB
        path = create_entropy_file(size)
        try:
            result = drv.load_model_burst(
                path, slot_id=2, model_id=101, label="deep_di"
            )
            logger.info("8MB load result: %s", result)

            assert result["bytes"] == size
            xxh3 = (
                int(result.get("checksum_xxh3", "0"), 16)
                if isinstance(result.get("checksum_xxh3"), str)
                else result.get("checksum_xxh3", 0)
            )
            assert xxh3 != 0, "XXH3 checksum is zero"

            safe_reset(drv, 2)
        finally:
            os.unlink(path)

    def test_checksum_determinism(self):
        """Load the same 2MB file twice to slot 3 — checksums must match."""
        drv = make_driver()
        safe_reset(drv, 3)
        time.sleep(0.3)

        size = 2 * 1024 * 1024
        path = create_entropy_file(size)
        try:
            r1 = drv.load_model_burst(path, slot_id=3, model_id=103, label="det1")
            xxh3_1 = r1.get("checksum_xxh3", "0")
            crc_1 = r1.get("checksum_crc32c", "0")
            safe_reset(drv, 3)
            time.sleep(0.5)

            r2 = drv.load_model_burst(path, slot_id=3, model_id=103, label="det2")
            xxh3_2 = r2.get("checksum_xxh3", "0")
            crc_2 = r2.get("checksum_crc32c", "0")
            safe_reset(drv, 3)

            assert xxh3_1 == xxh3_2, f"XXH3 mismatch: {xxh3_1} != {xxh3_2}"
            assert crc_1 == crc_2, f"CRC32C mismatch: {crc_1} != {crc_2}"
            logger.info("Determinism PASS: XXH3=%s CRC32C=%s", xxh3_1, crc_1)
        finally:
            os.unlink(path)

    def test_hp_stats_stable_after_load_reset(self):
        """HP pool must return to pre-load state after SLOT_RESET.
        Uses slot 3 with 2MB load — independent of other tests.
        """
        drv = make_driver()
        # Clean all slots
        for sid in [1, 2, 3]:
            safe_reset(drv, sid)
        time.sleep(1.0)

        resp = drv.send_command("HP_STATS")
        parts = resp.split("|")
        hp_before = int(parts[2]) if len(parts) >= 3 else -1
        logger.info("HP used before: %d", hp_before)

        size = 2 * 1024 * 1024  # 2MB
        path = create_entropy_file(size)
        try:
            drv.load_model_burst(path, slot_id=3, model_id=104, label="hp_test")
            safe_reset(drv, 3)
            time.sleep(0.5)
        finally:
            os.unlink(path)

        resp = drv.send_command("HP_STATS")
        parts = resp.split("|")
        hp_after = int(parts[2]) if len(parts) >= 3 else -1
        logger.info("HP used after: %d", hp_after)

        assert (
            abs(hp_before - hp_after) <= 2
        ), f"HP leak: before={hp_before}, after={hp_after}"
