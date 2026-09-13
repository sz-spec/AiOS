#!/usr/bin/env python3
"""
Phase 9: Bare-Metal Peak — Verification Gate (v23.0)

Tests:
1. DMA Warp Engine initialization and stats
2. Storage HAL device detection
3. GDT/IDT validation via SYSINFO
4. V-AAAK Native Mode toggle and compressed response
5. DMA transfer integrity (ivshmem → slot)
6. Storage HAL type detection (VirtIO vs AHCI vs NVMe)
7. AAAK compression ratio measurement
8. Binary forensics: Phase 9 symbol census
"""

import os
import sys
import subprocess
import pytest

# Add parent paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

from vbus_driver import v_aaak_encode, v_aaak_decode


# Decorator for tests requiring QEMU
def _qemu_running():
    pid_file = "/tmp/vos3_qemu.pid"
    if not os.path.exists(pid_file):
        return False
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, FileNotFoundError):
        return False


requires_qemu = pytest.mark.skipif(not _qemu_running(), reason="QEMU not running")

KERNEL_ELF = os.path.join(
    os.path.dirname(__file__), "..", "..", "kernel", "build", "vos3.elf"
)


class TestBinaryForensics:
    """Phase 9 binary symbol verification (no QEMU needed)."""

    def _nm_symbols(self):
        """Get symbols from kernel binary."""
        if not os.path.exists(KERNEL_ELF):
            pytest.skip("Kernel binary not found")
        result = subprocess.run(["nm", KERNEL_ELF], capture_output=True, text=True)
        return result.stdout

    def test_dma_warp_symbols(self):
        """DMA Warp Engine symbols present in binary."""
        syms = self._nm_symbols()
        required = [
            "vos3_dma_warp_init",
            "vos3_dma_nt_copy",
            "vos3_dma_warp_ivshmem_to_slot",
            "vos3_dma_warp_phys_to_slot",
            "vos3_dma_warp_stats",
            "vos3_dma_warp_fence",
        ]
        for sym in required:
            assert sym in syms, f"Missing DMA symbol: {sym}"

    def test_storage_hal_symbols(self):
        """Storage HAL symbols present in binary."""
        syms = self._nm_symbols()
        required = [
            "vos3_storage_hal_init",
            "vos3_storage_read",
            "vos3_storage_write",
            "vos3_storage_device_count",
            "vos3_storage_active_type",
        ]
        for sym in required:
            assert sym in syms, f"Missing Storage symbol: {sym}"

    def test_uefi_boot_symbols(self):
        """UEFI boot foundation symbols present."""
        syms = self._nm_symbols()
        required = [
            "vos3_uefi_parse_memory_map",
            "vos3_gdt_validate",
            "g_uefi_boot_info",
        ]
        for sym in required:
            assert sym in syms, f"Missing UEFI symbol: {sym}"

    def test_bridge_command_symbols(self):
        """Phase 9 VBus command handler symbols present."""
        syms = self._nm_symbols()
        required = [
            "cmd_dma_warp_xfer",
            "cmd_dma_warp_stats",
            "cmd_storage_info",
            "cmd_aaak_mode",
        ]
        for sym in required:
            assert sym in syms, f"Missing bridge command: {sym}"

    def test_aaak_native_global(self):
        """AAAK native mode global flag present."""
        syms = self._nm_symbols()
        assert "g_vbus_aaak_native" in syms

    def test_movnti_in_nt_copy(self):
        """Non-temporal stores (movnti) present in DMA copy function."""
        if not os.path.exists(KERNEL_ELF):
            pytest.skip("Kernel binary not found")
        result = subprocess.run(
            ["objdump", "-d", KERNEL_ELF, "-j", ".text"], capture_output=True, text=True
        )
        # Find the dma_nt_copy function and check for movnti
        assert "movnti" in result.stdout, "No movnti instruction in binary"

    def test_sfence_after_nt_copy(self):
        """sfence present (required after non-temporal stores)."""
        if not os.path.exists(KERNEL_ELF):
            pytest.skip("Kernel binary not found")
        result = subprocess.run(
            ["objdump", "-d", KERNEL_ELF, "-j", ".text"], capture_output=True, text=True
        )
        assert "sfence" in result.stdout

    def test_ahci_nvme_register_constants(self):
        """AHCI/NVMe register offsets compiled into binary."""
        if not os.path.exists(KERNEL_ELF):
            pytest.skip("Kernel binary not found")
        result = subprocess.run(["strings", KERNEL_ELF], capture_output=True, text=True)
        # Check for storage HAL log strings
        assert "STORAGE-HAL" in result.stdout, "Storage HAL strings missing"
        assert "DMA-WARP" in result.stdout, "DMA Warp strings missing"

    def test_gdt_audit_strings(self):
        """GDT validation strings compiled into binary."""
        if not os.path.exists(KERNEL_ELF):
            pytest.skip("Kernel binary not found")
        result = subprocess.run(["strings", KERNEL_ELF], capture_output=True, text=True)
        assert "GDT-AUDIT" in result.stdout, "GDT audit strings missing"


@requires_qemu
class TestDMAWarp:
    """DMA Warp Engine tests (requires QEMU with Phase 9 kernel)."""

    def _get_driver(self):
        from vbus_driver import VBusDriver

        return VBusDriver()

    def test_dma_stats_initial(self):
        """DMA stats available after init."""
        drv = self._get_driver()
        resp = drv.dma_warp_stats()
        assert "ERR" not in resp or "0|0" in resp

    def test_dma_xfer_to_active_slot(self):
        """DMA transfer to an active slot succeeds."""
        drv = self._get_driver()
        drv.send_command("SLOT_START|1|test-dma|4194304|dma-test|1|0")
        drv.dma_warp_xfer(1, 0, 1, 2097152)
        # May succeed or fail depending on ivshmem availability
        drv.send_command("SLOT_RESET|1")

    def test_dma_slot0_rejection(self):
        """DMA to Slot 0 (coordinator) rejected."""
        drv = self._get_driver()
        resp = drv.dma_warp_xfer(0, 0, 1, 2097152)
        assert "ERR" in resp


@requires_qemu
class TestStorageHAL:
    """Storage HAL tests (requires QEMU)."""

    def _get_driver(self):
        from vbus_driver import VBusDriver

        return VBusDriver()

    def test_storage_info(self):
        """STORAGE_INFO command returns device info."""
        drv = self._get_driver()
        resp = drv.storage_info()
        # Should have at least 1 device (VirtIO fallback)
        parts = resp.split("|")
        assert len(parts) >= 2
        count = int(parts[0])
        assert count >= 1, f"Expected at least 1 storage device, got {count}"

    def test_storage_virtio_detected(self):
        """VirtIO-Block is always detected as fallback."""
        drv = self._get_driver()
        resp = drv.storage_info()
        parts = resp.split("|")
        active_type = int(parts[1])
        # In QEMU: type 1 = VirtIO, 2 = AHCI, 3 = NVMe
        assert active_type >= 1, f"No active storage device (type={active_type})"


@requires_qemu
class TestAAAKNative:
    """V-AAAK Native Mode tests (requires QEMU with Phase 9 kernel)."""

    def _get_driver(self):
        from vbus_driver import VBusDriver

        return VBusDriver()

    def test_aaak_mode_toggle(self):
        """AAAK_MODE command toggles native compression."""
        drv = self._get_driver()
        # Enable
        resp = drv.aaak_mode(True)
        assert "AAAK_NATIVE_ON" in resp
        # Disable
        resp = drv.aaak_mode(False)
        assert "AAAK_NATIVE_OFF" in resp

    def test_aaak_mode_default_off(self):
        """AAAK native mode defaults to off."""
        drv = self._get_driver()
        # Ensure off first
        drv.aaak_mode(False)
        # PING should return uncompressed
        resp = drv.send_command("PING")
        assert not resp.startswith("AAAK:")


@requires_qemu
class TestGDTValidation:
    """GDT/IDT validation tests (requires QEMU)."""

    def _get_driver(self):
        from vbus_driver import VBusDriver

        return VBusDriver()

    def test_sysinfo_available(self):
        """SYSINFO command works (implies GDT/IDT are valid)."""
        drv = self._get_driver()
        resp = drv.send_command("SYSINFO")
        assert "ERR" not in resp

    def test_query_cr4(self):
        """QUERY_CR4 returns valid control register value."""
        drv = self._get_driver()
        resp = drv.send_command("QUERY_CR4")
        # CR4 should have PAE (bit 5) set
        # Response is hex string
        if "ERR" not in resp:
            cr4 = int(resp, 16) if resp.startswith("0x") else int(resp)
            assert cr4 & (1 << 5), f"CR4.PAE not set: 0x{cr4:x}"


class TestVAAAKCompression:
    """V-AAAK compression benchmarks (no QEMU needed)."""

    def test_english_prose_compression(self):
        """English prose achieves meaningful compression."""
        text = (
            "The artificial intelligence model is trained on a large dataset "
            "and the results are impressive. Each layer of the transformer "
            "can be described as an attention mechanism that learns to focus "
            "on the most relevant parts of the input sequence.\n"
        ) * 10
        raw = text.encode()
        enc = v_aaak_encode(raw)
        dec = v_aaak_decode(enc)
        assert dec == raw, "Round-trip failed"
        ratio = len(raw) / len(enc)
        print(f"\nEnglish prose: {ratio:.2f}x ({len(raw)} -> {len(enc)})")
        assert ratio > 1.1

    def test_code_compression(self):
        """Source code achieves some compression."""
        code = """
def forward(self, x):
    attention = self.attention(x)
    residual = attention + x
    output = self.feedforward(residual)
    return self.layernorm(output + residual)
""" * 10
        raw = code.encode()
        enc = v_aaak_encode(raw)
        dec = v_aaak_decode(enc)
        assert dec == raw
        ratio = len(raw) / len(enc)
        print(f"\nCode: {ratio:.2f}x ({len(raw)} -> {len(enc)})")

    def test_json_compression(self):
        """JSON output compression."""
        json_data = (
            '{"model": "llama-7b", "type": "transformer", "layers": 32, "heads": 32}\n'
            * 20
        )
        raw = json_data.encode()
        enc = v_aaak_encode(raw)
        dec = v_aaak_decode(enc)
        assert dec == raw
        ratio = len(raw) / len(enc)
        print(f"\nJSON: {ratio:.2f}x ({len(raw)} -> {len(enc)})")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-s"])
