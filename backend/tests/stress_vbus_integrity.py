"""
Phase 4.1 Destructive Stress Test — Binary Bridge Security Certification

Tests binary integrity, fuzzing, PTE injection security, and memory scrub
verification for the VOS3 VBus transport layer.

These tests use raw sockets to inject malformed frames — they do NOT use
the VBusDriver class, because the point is to test the kernel's resilience
to frames that violate the protocol.

Run: python3 -m pytest tests/stress_vbus_integrity.py -v --tb=short
"""

import os
import re
import socket
import struct
import time
import zlib

import pytest

# ============================================================================
# CONSTANTS (must match kernel/include/vos/virtio_vbus.h)
# ============================================================================

VBUS_TYPE_CMD = 0x01
VBUS_TYPE_RESP = 0x02
VBUS_TYPE_DATA = 0x03
VBUS_TYPE_HANDSHAKE = 0x04
VBUS_TYPE_PING = 0x05
VBUS_TYPE_EVENT = 0x06
VBUS_TYPE_NOTIFY = 0x0A

# v2 16-byte header: type(u8)+slot_id(u8)+tag(u16)+len(u32)+payload_crc(u32)+hdr_crc(u32)
FRAME_HDR_FMT = "<BBHIII"
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)  # 16 bytes
VBUS_MAX_PAYLOAD = 65520  # 64KB - 16-byte header

VBUS_TAG_ASYNC = 0xFFFF
VBUS_TAG_STREAM = 0x0000

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")

# ============================================================================
# PROJECT ROOT (for source-code static audits)
# ============================================================================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
KERNEL_SRC = os.path.join(PROJECT_ROOT, "kernel", "src")
KERNEL_INC = os.path.join(PROJECT_ROOT, "kernel", "include", "vos")


# ============================================================================
# FRAME BUILDING HELPERS
# ============================================================================


def compute_dual_crc(
    frame_type: int, slot_id: int, tag: int, length: int, payload: bytes
) -> tuple:
    """Compute dual CRC32 the same way the kernel does.

    Returns (payload_crc, hdr_crc).
    - hdr_crc: CRC32 over 8-byte header prefix (type+slot_id+tag+len)
    - payload_crc: CRC32 over 8-byte header prefix + payload
    """
    hdr_prefix = struct.pack("<BBHI", frame_type, slot_id, tag, length)
    hdr_crc = zlib.crc32(hdr_prefix) & 0xFFFFFFFF
    payload_crc = zlib.crc32(hdr_prefix + payload) & 0xFFFFFFFF
    return payload_crc, hdr_crc


def build_valid_frame(
    frame_type: int, payload: bytes, slot_id: int = 0xFF, tag: int = 0
) -> bytes:
    """Build a complete, valid v2 binary frame with correct dual-CRC."""
    length = len(payload)
    payload_crc, hdr_crc = compute_dual_crc(frame_type, slot_id, tag, length, payload)
    header = struct.pack(
        FRAME_HDR_FMT, frame_type, slot_id, tag, length, payload_crc, hdr_crc
    )
    return header + payload


def build_raw_frame(
    frame_type: int,
    payload: bytes,
    slot_id: int = 0xFF,
    tag: int = 0,
    payload_crc_override: int = None,
    hdr_crc_override: int = None,
    length_override: int = None,
) -> bytes:
    """Build a frame with optional CRC/length overrides for attack injection."""
    actual_len = len(payload)
    wire_len = length_override if length_override is not None else actual_len

    hdr_prefix = struct.pack("<BBHI", frame_type, slot_id, tag, wire_len)
    payload_crc = (
        payload_crc_override
        if payload_crc_override is not None
        else (zlib.crc32(hdr_prefix + payload) & 0xFFFFFFFF)
    )
    hdr_crc = (
        hdr_crc_override
        if hdr_crc_override is not None
        else (zlib.crc32(hdr_prefix) & 0xFFFFFFFF)
    )

    header = struct.pack(
        FRAME_HDR_FMT, frame_type, slot_id, tag, wire_len, payload_crc, hdr_crc
    )
    return header + payload


def recv_frame(sock: socket.socket, timeout: float = 2.0):
    """Receive and parse a v2 binary frame from the socket.

    Returns (frame_type, slot_id, tag, payload) or None on timeout/error.
    Transparently skips EVENT/NOTIFY frames.
    """
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(max(remaining, 0.1))
            header = _recv_exact(sock, FRAME_HDR_SIZE)
            if header is None:
                return None
            frame_type, slot_id, tag, length, expected_pcrc, expected_hcrc = (
                struct.unpack(FRAME_HDR_FMT, header)
            )
            if length > VBUS_MAX_PAYLOAD:
                return None
            payload = _recv_exact(sock, length) if length > 0 else b""
            if payload is None:
                return None
            # Skip EVENT/NOTIFY frames (heartbeats, slot notifications)
            if frame_type in (VBUS_TYPE_EVENT, VBUS_TYPE_NOTIFY):
                continue
            return (frame_type, slot_id, tag, payload)
    except (socket.timeout, OSError):
        return None


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (socket.timeout, OSError):
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def send_valid_ping(sock: socket.socket, timeout: float = 2.0) -> bool:
    """Send a valid PING frame and verify PONG response. Returns True if bridge is alive."""
    frame = build_valid_frame(VBUS_TYPE_PING, b"PING", tag=1)
    try:
        sock.sendall(frame)
        result = recv_frame(sock, timeout=timeout)
        if result is None:
            return False
        ftype, _sid, _tag, payload = result
        return ftype == VBUS_TYPE_PING and payload == b"PONG"
    except OSError:
        return False


def connect_bridge(retries: int = 5, delay: float = 0.5) -> socket.socket:
    """Connect to the bridge socket with retry (QEMU chardev re-listen delay)."""
    for attempt in range(retries):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect(BRIDGE_SOCKET)
            time.sleep(0.2)  # Let virtio settle after connect
            return sock
        except ConnectionRefusedError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise


# ============================================================================
# SKIP MARKER — skip runtime tests if QEMU is not running
# ============================================================================

requires_qemu = pytest.mark.skipif(
    not os.path.exists(BRIDGE_SOCKET),
    reason=f"QEMU not running (no socket at {BRIDGE_SOCKET})",
)


# ============================================================================
# AREA 1: BINARY INTEGRITY & FUZZING (virtio_vbus.c)
# ============================================================================


@requires_qemu
class TestCRC32Fuzzing:
    """CRC32 bitflip injection — verify kernel rejects corrupted frames."""

    def test_payload_crc_bitflip_rejected(self):
        """Flip each of 32 payload_crc bits individually. All must be rejected."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not responding before test"

            payload = b"PING"
            correct_pcrc, correct_hcrc = compute_dual_crc(
                VBUS_TYPE_PING, 0xFF, 1, len(payload), payload
            )
            rejected = 0

            for bit in range(32):
                corrupted_pcrc = correct_pcrc ^ (1 << bit)
                frame = build_raw_frame(
                    VBUS_TYPE_PING, payload, tag=1, payload_crc_override=corrupted_pcrc
                )
                sock.sendall(frame)

                result = recv_frame(sock, timeout=0.3)
                if result is None:
                    rejected += 1
                elif result[0] == VBUS_TYPE_PING and result[3] == b"PONG":
                    pytest.fail(f"Bit {bit}: payload_crc-corrupted frame ACCEPTED")
                else:
                    rejected += 1

            assert (
                rejected == 32
            ), f"Only {rejected}/32 payload_crc-corrupted frames rejected"
            assert send_valid_ping(sock), "Bridge died after payload_crc fuzzing"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_hdr_crc_bitflip_rejected(self):
        """Flip each of 32 hdr_crc bits individually. All must be rejected."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not responding before test"

            payload = b"PING"
            correct_pcrc, correct_hcrc = compute_dual_crc(
                VBUS_TYPE_PING, 0xFF, 1, len(payload), payload
            )
            rejected = 0

            for bit in range(32):
                corrupted_hcrc = correct_hcrc ^ (1 << bit)
                frame = build_raw_frame(
                    VBUS_TYPE_PING, payload, tag=1, hdr_crc_override=corrupted_hcrc
                )
                sock.sendall(frame)

                result = recv_frame(sock, timeout=0.3)
                if result is None:
                    rejected += 1
                elif result[0] == VBUS_TYPE_PING and result[3] == b"PONG":
                    pytest.fail(f"Bit {bit}: hdr_crc-corrupted frame ACCEPTED")
                else:
                    rejected += 1

            assert (
                rejected == 32
            ), f"Only {rejected}/32 hdr_crc-corrupted frames rejected"
            assert send_valid_ping(sock), "Bridge died after hdr_crc fuzzing"
        finally:
            sock.close()
            time.sleep(0.3)


@requires_qemu
class TestLengthMismatch:
    """Length field attacks — verify no hangs or crashes."""

    def test_length_mismatch_no_hang(self):
        """Header claims 4KB but only 1KB sent. Bridge must not hang."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            # Build a frame claiming 4096 bytes of payload but only send 1024
            payload_1k = b"\x41" * 1024
            # Note: In VirtIO-serial, the kernel receives a complete buffer
            # per descriptor. The kernel sees total_len=1033 (9 hdr + 1024)
            # but header says length=4096. parse_frame_header checks:
            #   VBUS_FRAME_HDR_SIZE + payload_len_out > total_len → -1
            # So this is a malformed frame, not a "waiting for more bytes" situation.
            frame = build_raw_frame(VBUS_TYPE_CMD, payload_1k, length_override=4096)
            sock.sendall(frame)

            # Wait briefly, then verify bridge is still alive
            time.sleep(0.5)
            assert send_valid_ping(sock), "Bridge hung on length-mismatch frame"
        finally:
            sock.close()
            time.sleep(0.3)  # Allow QEMU chardev to re-listen


@requires_qemu
class TestOversizedFrame:
    """Oversized payload — verify safe drop."""

    def test_oversized_frame_dropped(self):
        """Payload exceeding VBUS_MAX_PAYLOAD must be dropped without crash."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            # Build a frame with length > VBUS_MAX_PAYLOAD
            # We send the header claiming 65528 bytes but only send minimal data
            # The kernel's bounce buffer is 64KB and handles this safely
            small_payload = b"\x00" * 64
            frame = build_raw_frame(
                VBUS_TYPE_CMD, small_payload, length_override=VBUS_MAX_PAYLOAD + 1
            )
            sock.sendall(frame)

            time.sleep(0.3)
            assert send_valid_ping(sock), "Bridge crashed on oversized frame"
        finally:
            sock.close()
            time.sleep(0.3)  # Allow QEMU chardev to re-listen


@requires_qemu
class TestCRCStorm:
    """CRC corruption storm — DoS resilience (validates P2 fix)."""

    def test_rapid_crc_storm(self):
        """Send 100 frames with random dual-CRC. Bridge must survive and recover."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            import random

            random.seed(42)
            for i in range(100):
                bad_pcrc = random.randint(0, 0xFFFFFFFF)
                bad_hcrc = random.randint(0, 0xFFFFFFFF)
                frame = build_raw_frame(
                    VBUS_TYPE_CMD,
                    b"PING",
                    payload_crc_override=bad_pcrc,
                    hdr_crc_override=bad_hcrc,
                )
                try:
                    sock.sendall(frame)
                except OSError:
                    break

            # Small delay for kernel to process the storm
            time.sleep(0.5)

            # Verify bridge is still alive after the storm
            assert send_valid_ping(
                sock
            ), "Bridge died after 100-frame CRC storm (P2 fix may be missing)"
        finally:
            sock.close()
            time.sleep(0.3)  # Allow QEMU chardev to re-listen


@requires_qemu
class TestEdgeCases:
    """Edge case frames — zero length, unknown types."""

    def test_zero_length_frame(self):
        """Valid frame with empty payload should not crash."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            frame = build_valid_frame(VBUS_TYPE_CMD, b"")
            sock.sendall(frame)

            time.sleep(0.3)
            assert send_valid_ping(sock), "Bridge crashed on zero-length frame"
        finally:
            sock.close()
            time.sleep(0.3)  # Allow QEMU chardev to re-listen

    def test_unknown_frame_type(self):
        """Frame with type=0xFF should be silently dropped."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            frame = build_valid_frame(0xFF, b"ATTACK")
            sock.sendall(frame)

            time.sleep(0.3)
            assert send_valid_ping(sock), "Bridge crashed on unknown frame type"
        finally:
            sock.close()
            time.sleep(0.3)  # Allow QEMU chardev to re-listen


# ============================================================================
# AREA 2: PTE INJECTION SECURITY AUDIT (vmm.c) — STATIC ANALYSIS
# ============================================================================


class TestPTEFlagsAudit:
    """Static source-code audit of vos3_vmm_map_vbus_pages PTE flags."""

    def _read_source(self, relpath: str) -> str:
        """Read a kernel source file."""
        fullpath = os.path.join(PROJECT_ROOT, relpath)
        with open(fullpath, "r") as f:
            return f.read()

    def test_pte_flags_correct(self):
        """Verify PTE flags include NX, AI_PROTECTED, USER, PRESENT — NOT WRITABLE."""
        src = self._read_source("kernel/src/mm/vmm.c")

        # Extract the vos3_vmm_map_vbus_pages function
        match = re.search(
            r"int vos3_vmm_map_vbus_pages\(.*?\n\{(.*?)^}",
            src,
            re.MULTILINE | re.DOTALL,
        )
        assert match, "vos3_vmm_map_vbus_pages not found in vmm.c"
        func_body = match.group(1)

        # Find the pte_flags assignment
        assert "VOS3_PTE_PRESENT" in func_body, "Missing VOS3_PTE_PRESENT in pte_flags"
        assert "VOS3_PTE_USER" in func_body, "Missing VOS3_PTE_USER in pte_flags"
        assert (
            "VOS3_PTE_NO_EXECUTE" in func_body
        ), "Missing VOS3_PTE_NO_EXECUTE (NX bit)"
        assert (
            "VOS3_PTE_AI_PROTECTED" in func_body
        ), "Missing VOS3_PTE_AI_PROTECTED (bit 10)"

        # CRITICAL: Must NOT contain WRITABLE
        # Check the pte_flags line specifically (not comments)
        flags_lines = [
            l
            for l in func_body.splitlines()
            if "pte_flags" in l
            and "VOS3_PTE_" in l
            and "//" not in l.split("VOS3_PTE_")[0]
        ]
        for line in flags_lines:
            assert (
                "VOS3_PTE_WRITABLE" not in line
            ), f"SECURITY VIOLATION: WRITABLE flag found in pte_flags: {line.strip()}"

    def test_rollback_on_failure(self):
        """Verify rollback logic exists to unmap on partial failure."""
        src = self._read_source("kernel/src/mm/vmm.c")
        match = re.search(
            r"int vos3_vmm_map_vbus_pages\(.*?\n\{(.*?)^}",
            src,
            re.MULTILINE | re.DOTALL,
        )
        assert match, "vos3_vmm_map_vbus_pages not found"
        func_body = match.group(1)

        assert (
            "rollback" in func_body.lower() or "goto" in func_body
        ), "No rollback/goto pattern found — partial failure may leak mappings"

    def test_invlpg_after_map(self):
        """Verify TLB flush (invlpg) is called after each page mapping."""
        src = self._read_source("kernel/src/mm/vmm.c")
        match = re.search(
            r"int vos3_vmm_map_vbus_pages\(.*?\n\{(.*?)^}",
            src,
            re.MULTILINE | re.DOTALL,
        )
        assert match
        func_body = match.group(1)
        assert "invlpg" in func_body, "Missing TLB invalidation after PTE write"

    def test_nx_bit_definition(self):
        """Verify VOS3_PTE_NO_EXECUTE is bit 63 (NX bit on x86_64)."""
        hdr = self._read_source("kernel/include/vos/vmm.h")
        match = re.search(r"#define\s+VOS3_PTE_NO_EXECUTE\s+.*?(\d+)\s*\)", hdr)
        assert match, "VOS3_PTE_NO_EXECUTE not found in vmm.h"
        bit = int(match.group(1))
        assert bit == 63, f"NX bit should be 63, found {bit}"

    def test_ai_protected_bit_definition(self):
        """Verify VOS3_PTE_AI_PROTECTED is bit 10."""
        hdr = self._read_source("kernel/include/vos/ai_guard.h")
        match = re.search(r"#define\s+VOS3_PTE_AI_PROTECTED\s+.*?(\d+)\s*\)", hdr)
        assert match, "VOS3_PTE_AI_PROTECTED not found in ai_guard.h"
        bit = int(match.group(1))
        assert bit == 10, f"AI_PROTECTED bit should be 10, found {bit}"


# ============================================================================
# AREA 4: MEMORY SCRUBBING VERIFICATION (ai_guard.c) — STATIC ANALYSIS
# ============================================================================


class TestScrubAudit:
    """Static audit of vos3_ai_guard_scrub_model_regions implementation."""

    def _read_source(self, relpath: str) -> str:
        fullpath = os.path.join(PROJECT_ROOT, relpath)
        with open(fullpath, "r") as f:
            return f.read()

    def test_rep_stosq_zeroing(self):
        """Verify scrub uses rep stosq with zero value."""
        src = self._read_source("kernel/src/mm/ai_guard.c")

        # Find scrub_zero_fill function
        match = re.search(
            r"static void scrub_zero_fill\(.*?\n\{(.*?)\n}", src, re.DOTALL
        )
        assert match, "scrub_zero_fill not found in ai_guard.c"
        func_body = match.group(1)

        assert "rep stosq" in func_body, "Missing rep stosq instruction"
        assert (
            '"a"((uint64_t)0)' in func_body
            or '"a"(0ULL)' in func_body
            or '"a"((uint64_t)0)' in func_body.replace(" ", "")
        ), "rep stosq must write zeros via rax=0"

    def test_mfence_after_scrub(self):
        """Verify memory fence after zero-fill (ensures visibility before PTE change)."""
        src = self._read_source("kernel/src/mm/ai_guard.c")
        match = re.search(
            r"static void scrub_zero_fill\(.*?\n\{(.*?)\n}", src, re.DOTALL
        )
        assert match
        func_body = match.group(1)
        assert "mfence" in func_body, "Missing mfence after rep stosq"

    def test_pte_restored_after_scrub(self):
        """Verify PTEs are restored to read-only after scrub (no lingering WRITABLE)."""
        src = self._read_source("kernel/src/mm/ai_guard.c")

        # Find the main scrub function
        match = re.search(
            r"uint64_t vos3_ai_guard_scrub_model_regions\(.*?\n\{(.*?)^}",
            src,
            re.MULTILINE | re.DOTALL,
        )
        assert match, "vos3_ai_guard_scrub_model_regions not found"
        func_body = match.group(1)

        # Step 1: Temporarily makes writable
        assert (
            "VOS3_VMM_FLAG_WRITE" in func_body
        ), "Scrub must temporarily set WRITABLE before zeroing"

        # Step 3: Must restore to read-only (VOS3_VMM_FLAG_USER without WRITE)
        # The function call may span multiple lines, so join nearby lines
        # and look for the restore update_flags block after scrub_zero_fill
        lines = func_body.splitlines()
        step3_found = False
        for i, line in enumerate(lines):
            if (
                "Step 3" in line
                or "restore" in line.lower()
                or "read-only" in line.lower()
            ):
                # Join the next 8 lines into a block to handle multi-line calls
                block = " ".join(lines[i : min(i + 8, len(lines))])
                if "vos3_vmm_update_flags" in block:
                    # The restore block should have USER but NOT WRITE
                    if (
                        "VOS3_VMM_FLAG_USER" in block
                        and "VOS3_VMM_FLAG_WRITE" not in block
                    ):
                        step3_found = True
                break

        assert (
            step3_found
        ), "Post-scrub PTE restoration must use VOS3_VMM_FLAG_USER without VOS3_VMM_FLAG_WRITE"

    def test_invlpg_after_pte_restore(self):
        """Verify TLB flush after PTE restoration."""
        src = self._read_source("kernel/src/mm/ai_guard.c")
        match = re.search(
            r"uint64_t vos3_ai_guard_scrub_model_regions\(.*?\n\{(.*?)^}",
            src,
            re.MULTILINE | re.DOTALL,
        )
        assert match
        func_body = match.group(1)
        assert "invlpg" in func_body, "Missing TLB flush after PTE restoration in scrub"

    def test_ctx_lock_acquired(self):
        """Verify context lock is acquired during scrub (prevents race conditions)."""
        src = self._read_source("kernel/src/mm/ai_guard.c")
        match = re.search(
            r"uint64_t vos3_ai_guard_scrub_model_regions\(.*?\n\{(.*?)^}",
            src,
            re.MULTILINE | re.DOTALL,
        )
        assert match
        func_body = match.group(1)
        assert (
            "__sync_lock_test_and_set" in func_body
        ), "Missing lock acquisition in scrub (race condition with region list)"
        assert "__sync_lock_release" in func_body, "Missing lock release in scrub"


# ============================================================================
# AREA 5: FALLBACK & HANDSHAKE ROBUSTNESS
# ============================================================================


@requires_qemu
class TestHandshake:
    """Handshake protocol verification."""

    def test_handshake_v2(self):
        """Send HANDSHAKE v2 frame with 'VBUS2', expect 'VOS3-VBUS2-OK'."""
        sock = connect_bridge()
        try:
            frame = build_valid_frame(VBUS_TYPE_HANDSHAKE, b"VBUS2", tag=0)
            sock.sendall(frame)

            result = recv_frame(sock, timeout=3.0)
            assert result is not None, "No handshake response received"
            ftype, _sid, _tag, payload = result
            assert (
                ftype == VBUS_TYPE_HANDSHAKE
            ), f"Expected HANDSHAKE frame type, got 0x{ftype:02x}"
            assert (
                payload == b"VOS3-VBUS2-OK"
            ), f"Expected 'VOS3-VBUS2-OK', got {payload!r}"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 6: VBUS 2.0 TAG & DUAL-CRC VALIDATION
# ============================================================================


@requires_qemu
class TestVBus2Tags:
    """VBus 2.0 tagged-frame protocol tests."""

    def test_tag_echo(self):
        """CMD with tag=0x1234 → RESP echoes same tag."""
        sock = connect_bridge()
        try:
            # Handshake first
            hs = build_valid_frame(VBUS_TYPE_HANDSHAKE, b"VBUS2", tag=0)
            sock.sendall(hs)
            recv_frame(sock, timeout=2.0)

            tag = 0x1234
            frame = build_valid_frame(VBUS_TYPE_CMD, b"PING", tag=tag)
            sock.sendall(frame)

            result = recv_frame(sock, timeout=2.0)
            assert result is not None, "No response to tagged CMD"
            ftype, _sid, rtag, payload = result
            assert ftype == VBUS_TYPE_RESP, f"Expected RESP, got 0x{ftype:02x}"
            assert rtag == tag, f"Tag mismatch: sent 0x{tag:04x}, got 0x{rtag:04x}"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_batch_tag_grouping(self):
        """BATCH with tag=0x42, all N RESPs carry tag=0x42."""
        sock = connect_bridge()
        try:
            hs = build_valid_frame(VBUS_TYPE_HANDSHAKE, b"VBUS2", tag=0)
            sock.sendall(hs)
            recv_frame(sock, timeout=2.0)

            tag = 0x42
            cmds = "PING\nPING\nPING"
            frame = build_valid_frame(0x09, cmds.encode(), tag=tag)  # VBUS_TYPE_BATCH
            sock.sendall(frame)

            for i in range(3):
                result = recv_frame(sock, timeout=2.0)
                assert result is not None, f"Missing RESP #{i}"
                ftype, _sid, rtag, _payload = result
                assert ftype == VBUS_TYPE_RESP, f"RESP #{i}: got 0x{ftype:02x}"
                assert rtag == tag, f"RESP #{i}: tag 0x{rtag:04x} != 0x{tag:04x}"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_crc_v2_domain(self):
        """CRC covers slot_id+tag fields — bitflip in slot_id detected."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive"

            payload = b"PING"
            length = len(payload)
            # Build correct frame with slot_id=0xFF
            pcrc, hcrc = compute_dual_crc(VBUS_TYPE_PING, 0xFF, 1, length, payload)
            # Flip bit 0 of slot_id (0xFF → 0xFE) but keep CRCs unchanged
            corrupted = (
                struct.pack(FRAME_HDR_FMT, VBUS_TYPE_PING, 0xFE, 1, length, pcrc, hcrc)
                + payload
            )
            sock.sendall(corrupted)

            result = recv_frame(sock, timeout=0.3)
            # Should be rejected (CRC mismatch on slot_id change)
            if result is not None:
                ftype, _sid, _tag, rpayload = result
                assert not (
                    ftype == VBUS_TYPE_PING and rpayload == b"PONG"
                ), "Slot_id bitflip was NOT detected by dual-CRC"

            assert send_valid_ping(sock), "Bridge died after CRC domain test"
        finally:
            sock.close()
            time.sleep(0.3)
