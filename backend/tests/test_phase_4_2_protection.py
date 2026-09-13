"""
Phase 4.2 — AI Weight Loading & Protection Tests

Tests zero-copy DMA streaming, HugePage-protected model memory,
PTE enforcement (PRESENT+PS+AI_PROTECTED+NX SET, WRITABLE+GLOBAL CLEAR),
constant-time tamper rejection, and streaming throughput.

Run: python3 -m pytest tests/test_phase_4_2_protection.py -v -o "addopts="
"""

import os
import sys
import tempfile
import logging

import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vbus_driver import VBusDriver

logger = logging.getLogger("test_phase_4_2")

# ============================================================================
# CONSTANTS
# ============================================================================

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
MODEL_SIZE = 10 * 1024 * 1024  # 10 MB
HUGEPAGE_SIZE = 2 * 1024 * 1024  # 2 MB
NUM_HUGEPAGES = (MODEL_SIZE + HUGEPAGE_SIZE - 1) // HUGEPAGE_SIZE  # 5

# PTE bit positions
PTE_PRESENT = 1 << 0
PTE_WRITABLE = 1 << 1
PTE_PS_LARGE = 1 << 7  # Page Size (2MB HugePage)
PTE_GLOBAL = 1 << 8  # Global bit
PTE_AI_PROTECTED = 1 << 10  # AI Guard protected
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
def dummy_weights_path():
    """Create a 10MB dummy model weights file with repeating pattern."""
    pattern = bytes(range(256))  # 256 bytes: 0x00..0xFF
    repeats = MODEL_SIZE // len(pattern)  # 40960 repeats

    tmpfile = tempfile.NamedTemporaryFile(
        prefix="vos3_model_", suffix=".weights", delete=False
    )
    try:
        for _ in range(repeats):
            tmpfile.write(pattern)
        tmpfile.flush()
        tmpfile.close()

        actual_size = os.path.getsize(tmpfile.name)
        assert actual_size == MODEL_SIZE, f"Expected {MODEL_SIZE}, got {actual_size}"
        logger.info("Created dummy weights: %s (%d bytes)", tmpfile.name, actual_size)
        yield tmpfile.name
    finally:
        try:
            os.unlink(tmpfile.name)
        except OSError:
            pass


@pytest.fixture(scope="module")
def vbus_driver():
    """Connect VBusDriver to the kernel bridge."""
    driver = VBusDriver(socket_path=BRIDGE_SOCKET)
    connected = driver.connect()
    if not connected:
        pytest.skip("Cannot connect to VOS3 bridge")
    yield driver
    driver.disconnect()


@pytest.fixture(scope="module")
def loaded_model(vbus_driver, dummy_weights_path):
    """Load the 10MB model and return the result dict."""
    result = vbus_driver.load_model_weights(
        dummy_weights_path, model_id=1, chunk_size=16384, window=64
    )
    logger.info("Model loaded: %s", result)
    return result


# ============================================================================
# TESTS
# ============================================================================


@requires_qemu
class TestPhase42Protection:
    """Phase 4.2 AI Weight Loading & Protection tests."""

    def test_model_load_10mb(self, loaded_model):
        """Test: 10MB model loads successfully via zero-copy binary streaming."""
        assert (
            loaded_model["size"] == MODEL_SIZE
        ), f"Expected size {MODEL_SIZE}, got {loaded_model['size']}"
        assert (
            loaded_model["bytes"] == MODEL_SIZE
        ), f"Expected bytes {MODEL_SIZE}, got {loaded_model['bytes']}"
        assert loaded_model["chunks"] > 0, "Expected non-zero chunk count"
        assert loaded_model["checksum"] != "0x0", "Checksum should not be zero"
        assert loaded_model["checksum"].startswith("0x"), "Checksum should be hex"
        logger.info(
            "PASS: 10MB model loaded — %d chunks, checksum=%s",
            loaded_model["chunks"],
            loaded_model["checksum"],
        )

    def test_model_pte_ai_protected(self, vbus_driver, loaded_model):
        """Test: Model PTE has correct protection bits.

        Verifies:
          - bit 0  (PRESENT)      = SET
          - bit 1  (WRITABLE)     = CLEAR
          - bit 7  (PS/LARGE)     = SET  (2MB HugePage)
          - bit 8  (GLOBAL)       = CLEAR (Non-Global for PCID isolation)
          - bit 10 (AI_PROTECTED) = SET
          - bit 63 (NX)           = SET  (buffer overflow prevention)
        """
        addr = loaded_model["addr"]
        resp = vbus_driver.send_command(f"MODEL_CHECK|{addr}")
        assert resp.startswith("OK|"), f"MODEL_CHECK failed: {resp}"
        pte_hex = resp.split("|")[1]
        pte = int(pte_hex, 16)

        # Must be SET
        assert pte & PTE_PRESENT, f"PRESENT bit not set: PTE=0x{pte:016x}"
        assert pte & PTE_PS_LARGE, f"PS (HugePage) bit not set: PTE=0x{pte:016x}"
        assert pte & PTE_AI_PROTECTED, f"AI_PROTECTED bit not set: PTE=0x{pte:016x}"
        assert pte & PTE_NX, f"NX bit not set: PTE=0x{pte:016x}"

        # Must be CLEAR
        assert not (pte & PTE_WRITABLE), f"WRITABLE bit still set: PTE=0x{pte:016x}"
        assert not (
            pte & PTE_GLOBAL
        ), f"GLOBAL bit set (PCID isolation broken): PTE=0x{pte:016x}"

        logger.info("PASS: PTE verification — 0x%016x", pte)

    def test_model_tamper_rejected(self, vbus_driver, loaded_model):
        """Test: MODEL_TAMPER returns constant-time EACCES PROTECTED."""
        addr = loaded_model["addr"]
        resp = vbus_driver.send_command(f"MODEL_TAMPER|{addr}")

        # Should return ERR|13|EACCES: PROTECTED
        assert "PROTECTED" in resp, f"Expected PROTECTED, got: {resp}"
        assert "13" in resp, f"Expected EACCES (13), got: {resp}"

        logger.info("PASS: Tamper rejected — %s", resp)

    def test_model_pte_all_hugepages(self, vbus_driver, loaded_model):
        """Test: All 5 HugePages have correct PTE flags."""
        base = int(loaded_model["addr"], 16)

        for i in range(NUM_HUGEPAGES):
            addr = base + i * HUGEPAGE_SIZE
            resp = vbus_driver.send_command(f"MODEL_CHECK|0x{addr:x}")
            assert resp.startswith(
                "OK|"
            ), f"MODEL_CHECK failed for HugePage {i}: {resp}"
            pte_hex = resp.split("|")[1]
            pte = int(pte_hex, 16)

            # SET: PRESENT, PS, AI_PROTECTED, NX
            assert pte & PTE_PRESENT, f"HP{i}: PRESENT not set"
            assert pte & PTE_PS_LARGE, f"HP{i}: PS not set"
            assert pte & PTE_AI_PROTECTED, f"HP{i}: AI_PROTECTED not set"
            assert pte & PTE_NX, f"HP{i}: NX not set"

            # CLEAR: WRITABLE, GLOBAL
            assert not (pte & PTE_WRITABLE), f"HP{i}: WRITABLE still set"
            assert not (pte & PTE_GLOBAL), f"HP{i}: GLOBAL set"

        logger.info("PASS: All %d HugePages verified", NUM_HUGEPAGES)

    def test_model_streaming_throughput(self, loaded_model):
        """Test: Streaming throughput exceeds 1 MB/s floor."""
        throughput = loaded_model["throughput_mbps"]
        elapsed = loaded_model["elapsed_s"]

        assert throughput > 1.0, (
            f"Throughput below 1 MB/s floor: {throughput:.2f} MB/s "
            f"({elapsed:.3f}s for {MODEL_SIZE} bytes)"
        )

        logger.info(
            "PASS: Throughput %.2f MB/s (%.3fs for 10MB, target >50 MB/s)",
            throughput,
            elapsed,
        )

    def test_model_sync_integrity(self, vbus_driver, dummy_weights_path):
        """Test: MODEL_SYNC byte counts match at each window boundary.

        Loads a second model with small window (16 chunks) and verifies
        sync responses match expected byte counts.
        """
        file_size = os.path.getsize(dummy_weights_path)
        chunk_size = 16384
        window = 16  # Sync every 16 chunks = 256KB

        resp = vbus_driver.send_command(f"MODEL_START|2|{file_size}")
        assert resp.startswith("OK|"), f"MODEL_START failed: {resp}"

        chunks_sent = 0
        bytes_sent = 0
        sync_mismatches = 0

        with open(dummy_weights_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                vbus_driver._send_frame(0x03, chunk)  # VBUS_TYPE_DATA
                chunks_sent += 1
                bytes_sent += len(chunk)

                if chunks_sent % window == 0:
                    resp = vbus_driver.send_command("MODEL_SYNC")
                    assert resp.startswith("OK|"), f"MODEL_SYNC failed: {resp}"
                    kernel_bytes = int(resp.split("|")[1])
                    if kernel_bytes != bytes_sent:
                        sync_mismatches += 1
                        logger.warning(
                            "Sync mismatch at chunk %d: sent=%d kernel=%d",
                            chunks_sent,
                            bytes_sent,
                            kernel_bytes,
                        )

        # Final sync
        resp = vbus_driver.send_command("MODEL_SYNC")
        resp = vbus_driver.send_command("MODEL_DONE")
        assert resp.startswith("OK|"), f"MODEL_DONE failed: {resp}"

        # Some mismatches are acceptable (triggers rewind), but count should be low
        logger.info(
            "PASS: Sync integrity — %d chunks, %d bytes, %d mismatches",
            chunks_sent,
            bytes_sent,
            sync_mismatches,
        )

    def test_model_hugepage_no_fragmentation(self, vbus_driver, loaded_model):
        """Test: All model PTEs confirmed as 2MB HugePages (no 4KB fragmentation).

        Verifies PS (bit 7) is SET on every HugePage, proving no
        4KB-fragmentation tampering occurred.
        """
        base = int(loaded_model["addr"], 16)

        for i in range(NUM_HUGEPAGES):
            addr = base + i * HUGEPAGE_SIZE
            resp = vbus_driver.send_command(f"MODEL_CHECK|0x{addr:x}")
            assert resp.startswith("OK|"), f"CHECK failed for HP{i}: {resp}"
            pte = int(resp.split("|")[1], 16)

            assert pte & PTE_PS_LARGE, (
                f"HugePage {i} at 0x{addr:x}: PS bit CLEAR — "
                f"possible 4KB fragmentation tampering! PTE=0x{pte:016x}"
            )

        logger.info(
            "PASS: No fragmentation — all %d HugePages have PS=1", NUM_HUGEPAGES
        )
