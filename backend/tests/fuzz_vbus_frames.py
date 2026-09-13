"""
VBus Binary Frame Fuzzer — Comprehensive Protocol Resilience Testing

Tests the VOS3 kernel's VBus binary bridge (v2.19) against millions of
malformed, mutated, and adversarial frames to verify that the kernel never
crashes, hangs, or leaks state regardless of input.

This fuzzer goes far beyond the basic integrity tests in stress_vbus_integrity.py:
  1. Fully random frame generation (configurable, default 100K per run)
  2. Structured mutation of valid frames (byte, bit, truncation, extension)
  3. Protocol state machine violations (out-of-order, role-reversal, etc.)
  4. Boundary value analysis on every header field
  5. Endurance / sustained barrage testing

All tests use raw sockets — no VBusDriver — because the goal is to test the
kernel's parser resilience, not the Python driver.

Reproducibility: Every random test uses a fixed seed derived from the test name.
Override with VOS3_FUZZ_SEED=<int> environment variable.

Run:
  python3 -m pytest tests/fuzz_vbus_frames.py -v --tb=short
  python3 -m pytest tests/fuzz_vbus_frames.py -v -k "random_frames" --tb=short

Environment variables:
  VOS3_BRIDGE_SOCKET  — path to QEMU bridge socket (default: /tmp/vos3_bridge.sock)
  VOS3_FUZZ_SEED      — RNG seed for reproducibility (default: 0xDEADBEEF)
  VOS3_FUZZ_COUNT     — number of frames for random generation tests (default: 100000)
  VOS3_FUZZ_ENDURANCE — seconds for endurance barrage (default: 60)
"""

import os
import random
import socket
import struct
import time
import zlib
from typing import Optional, Tuple

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
VBUS_TYPE_BATCH = 0x09
VBUS_TYPE_NOTIFY = 0x0A

KNOWN_TYPES = {
    VBUS_TYPE_CMD,
    VBUS_TYPE_RESP,
    VBUS_TYPE_DATA,
    VBUS_TYPE_HANDSHAKE,
    VBUS_TYPE_PING,
    VBUS_TYPE_EVENT,
    VBUS_TYPE_BATCH,
    VBUS_TYPE_NOTIFY,
}

# v2 16-byte header: type(u8)+slot_id(u8)+tag(u16)+len(u32)+payload_crc(u32)+hdr_crc(u32)
FRAME_HDR_FMT = "<BBHIII"
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)  # 16 bytes
VBUS_MAX_PAYLOAD = 65520  # 64KB - 16-byte header

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
FUZZ_SEED = int(os.environ.get("VOS3_FUZZ_SEED", "0xDEADBEEF"), 0)
FUZZ_COUNT = int(os.environ.get("VOS3_FUZZ_COUNT", "100000"))
FUZZ_ENDURANCE_SECS = int(os.environ.get("VOS3_FUZZ_ENDURANCE", "60"))


# ============================================================================
# FRAME BUILDING HELPERS (mirrors stress_vbus_integrity.py)
# ============================================================================


def compute_dual_crc(
    frame_type: int, slot_id: int, tag: int, length: int, payload: bytes
) -> Tuple[int, int]:
    """Compute dual CRC32 the same way the kernel does.

    Returns (payload_crc, hdr_crc).
    - hdr_crc: CRC32 over 8-byte header prefix (type+slot_id+tag+len)
    - payload_crc: CRC32 over 8-byte header prefix + payload (incremental seed)
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
    payload_crc_override: Optional[int] = None,
    hdr_crc_override: Optional[int] = None,
    length_override: Optional[int] = None,
) -> bytes:
    """Build a frame with optional CRC/length overrides for attack injection."""
    wire_len = length_override if length_override is not None else len(payload)

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


def _recv_exact(sock: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly n bytes from the socket."""
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
            frame_type, slot_id, tag, length, _, _ = struct.unpack(
                FRAME_HDR_FMT, header
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


def send_valid_ping(sock: socket.socket, timeout: float = 2.0) -> bool:
    """Send a valid PING frame and verify PONG response. Returns True if alive."""
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


def drain_socket(sock: socket.socket, timeout: float = 0.3):
    """Drain any pending data from the socket (discard responses to fuzz frames)."""
    sock.settimeout(timeout)
    try:
        while True:
            data = sock.recv(65536)
            if not data:
                break
    except (socket.timeout, OSError):
        pass


def safe_send(sock: socket.socket, data: bytes) -> bool:
    """Send data, returning False if the connection was reset."""
    try:
        sock.sendall(data)
        return True
    except (BrokenPipeError, ConnectionResetError, OSError):
        return False


def reconnect_and_verify(retries: int = 3) -> Tuple[socket.socket, bool]:
    """Reconnect to the bridge and verify it is alive via PING.

    Returns (socket, alive). Socket may be None if connection failed entirely.
    """
    for _ in range(retries):
        try:
            sock = connect_bridge(retries=3, delay=0.5)
            alive = send_valid_ping(sock, timeout=3.0)
            if alive:
                return sock, True
            sock.close()
            time.sleep(0.3)
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return None, False


# ============================================================================
# SKIP MARKER -- skip runtime tests if QEMU is not running
# ============================================================================


def _qemu_bridge_reachable() -> bool:
    """Probe the QEMU bridge socket — returns True only if a live QEMU is listening."""
    if not os.path.exists(BRIDGE_SOCKET):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(BRIDGE_SOCKET)
        s.close()
        time.sleep(0.3)  # let QEMU re-listen
        return True
    except (ConnectionRefusedError, OSError):
        return False


requires_qemu = pytest.mark.skipif(
    not _qemu_bridge_reachable(),
    reason=f"QEMU not running (bridge unreachable at {BRIDGE_SOCKET})",
)


# ============================================================================
# HELPER: Seeded RNG per test
# ============================================================================


def make_rng(test_name: str) -> random.Random:
    """Create a seeded Random instance for reproducible fuzzing.

    Combines the global FUZZ_SEED with a hash of the test name so each test
    gets a unique but reproducible stream.
    """
    combined = FUZZ_SEED ^ hash(test_name)
    rng = random.Random(combined)
    return rng


# ============================================================================
# AREA 1: FULLY RANDOM FRAME GENERATION
# ============================================================================


@requires_qemu
class TestFullyRandomFrames:
    """Generate completely random frames -- every byte is unconstrained.

    Goal: Prove the kernel parser never crashes, hangs, or enters an
    unrecoverable state regardless of what bytes arrive on the wire.
    """

    def test_random_frames_small_payloads(self):
        """Send FUZZ_COUNT frames with random headers and 0-256 byte payloads.

        This is the high-volume test. Payloads are small to maximize frame
        throughput and parser-path coverage.
        """
        rng = make_rng("test_random_frames_small_payloads")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before fuzz"

            sent = 0
            errors = 0
            batch_size = 500  # Frames per send batch before a brief pause
            check_interval = 10000  # PING liveness check every N frames

            for i in range(FUZZ_COUNT):
                frame_type = rng.randint(0, 0xFF)
                slot_id = rng.randint(0, 0xFF)
                tag = rng.randint(0, 0xFFFF)
                payload_len = rng.randint(0, 256)
                payload = rng.randbytes(payload_len)
                # Randomly choose: valid CRCs, garbage CRCs, or partial garbage
                crc_mode = rng.randint(0, 2)
                if crc_mode == 0:
                    # Valid CRCs for the actual content
                    pcrc, hcrc = compute_dual_crc(
                        frame_type, slot_id, tag, payload_len, payload
                    )
                elif crc_mode == 1:
                    # Completely random CRCs
                    pcrc = rng.randint(0, 0xFFFFFFFF)
                    hcrc = rng.randint(0, 0xFFFFFFFF)
                else:
                    # Valid hdr_crc but garbage payload_crc
                    _, hcrc = compute_dual_crc(
                        frame_type, slot_id, tag, payload_len, payload
                    )
                    pcrc = rng.randint(0, 0xFFFFFFFF)

                header = struct.pack(
                    FRAME_HDR_FMT, frame_type, slot_id, tag, payload_len, pcrc, hcrc
                )
                frame = header + payload

                if not safe_send(sock, frame):
                    errors += 1
                    # Connection lost -- reconnect
                    sock.close()
                    time.sleep(0.3)
                    sock = connect_bridge(retries=5, delay=0.5)
                    continue

                sent += 1

                # Periodic micro-pause to avoid overwhelming virtio queue
                if sent % batch_size == 0:
                    time.sleep(0.01)

                # Periodic drain to prevent kernel TX buffer backup
                if sent % (batch_size * 4) == 0:
                    drain_socket(sock, timeout=0.05)

                # Periodic liveness check
                if sent % check_interval == 0:
                    drain_socket(sock, timeout=0.1)
                    alive = send_valid_ping(sock, timeout=3.0)
                    assert alive, (
                        f"Bridge died after {sent} random frames "
                        f"(seed={FUZZ_SEED:#x}, frame #{i})"
                    )

            # Final liveness check
            drain_socket(sock, timeout=0.3)
            time.sleep(0.2)
            assert send_valid_ping(
                sock, timeout=3.0
            ), f"Bridge dead after {sent} random small-payload frames"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_random_frames_large_payloads(self):
        """Send 1000 frames with random payloads up to 128KB.

        Tests the kernel's handling of oversized and near-boundary payloads.
        Fewer frames because each is large.
        """
        rng = make_rng("test_random_frames_large_payloads")
        count = 1000
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before fuzz"

            sent = 0
            for i in range(count):
                frame_type = rng.randint(0, 0xFF)
                slot_id = rng.randint(0, 0xFF)
                tag = rng.randint(0, 0xFFFF)
                # Payload sizes: heavily weight toward boundaries
                size_choice = rng.randint(0, 4)
                if size_choice == 0:
                    payload_len = rng.randint(0, 64)
                elif size_choice == 1:
                    payload_len = rng.randint(
                        VBUS_MAX_PAYLOAD - 16, VBUS_MAX_PAYLOAD + 16
                    )
                elif size_choice == 2:
                    payload_len = rng.randint(VBUS_MAX_PAYLOAD, 131072)  # Up to 128KB
                elif size_choice == 3:
                    payload_len = VBUS_MAX_PAYLOAD
                else:
                    payload_len = rng.randint(0, 131072)

                payload = rng.randbytes(min(payload_len, 131072))
                # Length field may differ from actual payload size
                length_field = rng.choice(
                    [
                        len(payload),
                        payload_len,
                        rng.randint(0, 0xFFFFFFFF),
                    ]
                )

                pcrc = rng.randint(0, 0xFFFFFFFF)
                hcrc = rng.randint(0, 0xFFFFFFFF)

                header = struct.pack(
                    FRAME_HDR_FMT,
                    frame_type,
                    slot_id,
                    tag,
                    length_field & 0xFFFFFFFF,
                    pcrc,
                    hcrc,
                )
                frame = header + payload

                if not safe_send(sock, frame):
                    sock.close()
                    time.sleep(0.3)
                    sock = connect_bridge(retries=5, delay=0.5)
                    continue

                sent += 1
                # Brief pause after large frames
                time.sleep(0.005)

                if sent % 100 == 0:
                    drain_socket(sock, timeout=0.1)

            drain_socket(sock, timeout=0.5)
            time.sleep(0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), f"Bridge dead after {sent} random large-payload frames"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_pure_random_bytes(self):
        """Send completely unstructured random byte streams.

        No attempt to form frames at all -- pure chaos. The kernel must
        either parse what it can or discard without crashing.
        """
        rng = make_rng("test_pure_random_bytes")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before fuzz"

            total_bytes = 0
            target_bytes = 1024 * 1024  # 1 MB of random garbage
            chunk_sizes = [
                1,
                7,
                15,
                16,
                17,
                63,
                64,
                65,
                128,
                256,
                512,
                1024,
                4096,
                8192,
            ]

            while total_bytes < target_bytes:
                chunk_size = rng.choice(chunk_sizes)
                data = rng.randbytes(chunk_size)
                if not safe_send(sock, data):
                    sock.close()
                    time.sleep(0.3)
                    sock = connect_bridge(retries=5, delay=0.5)
                total_bytes += chunk_size

                if total_bytes % 65536 == 0:
                    drain_socket(sock, timeout=0.05)

            drain_socket(sock, timeout=0.5)
            time.sleep(0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), f"Bridge dead after {total_bytes} bytes of random garbage"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 2: STRUCTURED MUTATION TESTING
# ============================================================================


@requires_qemu
class TestStructuredMutation:
    """Take valid frames and apply targeted mutations.

    More likely to reach deep parser paths than pure random, because
    the frame structure is mostly correct with surgical corruption.
    """

    def _make_valid_ping(self) -> bytes:
        """Build a known-good PING frame for mutation."""
        return build_valid_frame(VBUS_TYPE_PING, b"PING", tag=1)

    def _make_valid_cmd(self, payload: bytes = b"PING") -> bytes:
        """Build a known-good CMD frame for mutation."""
        return build_valid_frame(VBUS_TYPE_CMD, payload, tag=0x42)

    def test_single_byte_mutations(self):
        """Mutate each byte position in a valid frame one at a time.

        For a 20-byte PING frame (16 header + 4 payload), that is 20 x 255
        = 5100 mutations. Every one must be safely rejected or processed.
        """
        rng = make_rng("test_single_byte_mutations")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            valid = self._make_valid_ping()
            mutations_sent = 0

            for pos in range(len(valid)):
                original_byte = valid[pos]
                # Try a few random replacement values for each position
                for _ in range(8):
                    new_byte = rng.randint(0, 255)
                    if new_byte == original_byte:
                        continue
                    mutated = bytearray(valid)
                    mutated[pos] = new_byte
                    if not safe_send(sock, bytes(mutated)):
                        sock.close()
                        time.sleep(0.3)
                        sock = connect_bridge(retries=5, delay=0.5)
                    mutations_sent += 1

                # Drain periodically
                if pos % 4 == 0:
                    drain_socket(sock, timeout=0.05)

            drain_socket(sock, timeout=0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), f"Bridge dead after {mutations_sent} single-byte mutations"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_bitflip_mutations(self):
        """Flip each bit in a valid frame individually.

        For a 20-byte frame, that is 160 bit-flips. Tests single-bit
        sensitivity of the CRC validation.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            valid = self._make_valid_ping()
            flips_sent = 0

            for byte_pos in range(len(valid)):
                for bit_pos in range(8):
                    mutated = bytearray(valid)
                    mutated[byte_pos] ^= 1 << bit_pos
                    if not safe_send(sock, bytes(mutated)):
                        sock.close()
                        time.sleep(0.3)
                        sock = connect_bridge(retries=5, delay=0.5)
                    flips_sent += 1

                if byte_pos % 4 == 0:
                    drain_socket(sock, timeout=0.05)

            drain_socket(sock, timeout=0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), f"Bridge dead after {flips_sent} bit-flip mutations"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_truncation_mutations(self):
        """Send partial headers: 1 byte, 2 bytes, ..., 15 bytes.

        The kernel must handle incomplete frames without blocking or crashing.
        Also tests truncated payloads (header complete but payload short).
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            valid = self._make_valid_ping()  # 20 bytes total

            # Truncated headers (1 to 15 bytes)
            for length in range(1, FRAME_HDR_SIZE):
                truncated = valid[:length]
                safe_send(sock, truncated)
                time.sleep(0.02)  # Small gap so kernel can detect truncation

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after truncated header mutations"

            # Truncated payloads: send full header but fewer payload bytes
            # Build a CMD with a 100-byte payload
            payload = b"A" * 100
            valid_cmd = build_valid_frame(VBUS_TYPE_CMD, payload, tag=2)
            for cut in [1, 10, 50, 99]:
                truncated = valid_cmd[: FRAME_HDR_SIZE + cut]
                safe_send(sock, truncated)
                time.sleep(0.02)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after truncated payload mutations"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_extension_mutations(self):
        """Append garbage bytes after a valid frame.

        The extra bytes may be misinterpreted as the start of the next frame.
        The kernel must handle this gracefully (discard or re-sync).
        """
        rng = make_rng("test_extension_mutations")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            valid = self._make_valid_ping()

            garbage_sizes = [1, 2, 7, 15, 16, 17, 32, 64, 128, 255, 512, 1024]
            for gsize in garbage_sizes:
                garbage = rng.randbytes(gsize)
                extended = valid + garbage
                safe_send(sock, extended)
                time.sleep(0.02)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after extension mutations"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_field_swap_mutations(self):
        """Swap header fields within a valid frame.

        E.g., put the hdr_crc where the length goes, put the tag where the
        type goes, etc. Tests that the kernel validates fields in-place.
        """
        make_rng("test_field_swap_mutations")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            payload = b"PING"
            pcrc, hcrc = compute_dual_crc(
                VBUS_TYPE_PING, 0xFF, 1, len(payload), payload
            )

            # Original field values
            fields = [VBUS_TYPE_PING, 0xFF, 1, len(payload), pcrc, hcrc]
            field_offsets = [0, 1, 2, 4, 8, 12]
            field_sizes = [1, 1, 2, 4, 4, 4]

            # Generate all pairwise swaps
            for i in range(len(fields)):
                for j in range(i + 1, len(fields)):
                    header = bytearray(struct.pack(FRAME_HDR_FMT, *fields))
                    # Read field i and j values as raw bytes
                    raw_i = header[field_offsets[i] : field_offsets[i] + field_sizes[i]]
                    raw_j = header[field_offsets[j] : field_offsets[j] + field_sizes[j]]
                    # Swap (truncate or pad to fit)
                    swap_len = min(field_sizes[i], field_sizes[j])
                    header[field_offsets[i] : field_offsets[i] + swap_len] = raw_j[
                        :swap_len
                    ]
                    header[field_offsets[j] : field_offsets[j] + swap_len] = raw_i[
                        :swap_len
                    ]

                    frame = bytes(header) + payload
                    safe_send(sock, frame)

                drain_socket(sock, timeout=0.05)

            drain_socket(sock, timeout=0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after field-swap mutations"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_rapid_duplicate_frames(self):
        """Send the same valid frame N times in rapid succession.

        Tests idempotency and tag/sequence-number handling under duplication.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            valid = self._make_valid_cmd(b"PING")
            duplicate_counts = [10, 100, 1000]

            for count in duplicate_counts:
                for _ in range(count):
                    if not safe_send(sock, valid):
                        sock.close()
                        time.sleep(0.3)
                        sock = connect_bridge(retries=5, delay=0.5)
                        break

                drain_socket(sock, timeout=0.5)
                assert send_valid_ping(
                    sock, timeout=3.0
                ), f"Bridge dead after {count} rapid duplicate frames"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_multi_byte_corruption(self):
        """Corrupt multiple random bytes simultaneously in a valid frame.

        Tests that the CRC catches multi-bit errors, not just single-bit.
        """
        rng = make_rng("test_multi_byte_corruption")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            valid = self._make_valid_cmd(b"HELLO FUZZ TEST")
            corruption_counts = [2, 3, 4, 8, 12]

            for num_corrupt in corruption_counts:
                for _ in range(200):
                    mutated = bytearray(valid)
                    positions = rng.sample(range(len(valid)), num_corrupt)
                    for pos in positions:
                        mutated[pos] = rng.randint(0, 255)
                    safe_send(sock, bytes(mutated))

                drain_socket(sock, timeout=0.2)

            drain_socket(sock, timeout=0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after multi-byte corruption"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 3: PROTOCOL STATE FUZZING
# ============================================================================


@requires_qemu
class TestProtocolStateFuzzing:
    """Violate the expected protocol state machine.

    The VBus protocol has implicit ordering assumptions (HANDSHAKE first,
    CMD before DATA, RESP is kernel-to-host only, etc.). This area tests
    what happens when those assumptions are violated.
    """

    def test_data_without_cmd(self):
        """Send DATA frames without a preceding CMD.

        DATA frames are normally sent as part of a multi-frame transfer
        initiated by CMD. Orphan DATA frames must be safely discarded.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            for slot_id in [0, 1, 2, 0xFF]:
                for tag in [0, 1, 0xFFFF]:
                    frame = build_valid_frame(
                        VBUS_TYPE_DATA, b"ORPHAN DATA", slot_id=slot_id, tag=tag
                    )
                    safe_send(sock, frame)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after orphan DATA frames"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_resp_from_host(self):
        """Send RESP frames from host to kernel (role reversal).

        RESP frames are normally kernel-to-host only. Sending them in the
        reverse direction should be safely ignored.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            payloads = [b"OK", b"ERR", b"PONG", b"", b"A" * 1000]
            for payload in payloads:
                for tag in [0, 1, 0x1234, 0xFFFF]:
                    frame = build_valid_frame(VBUS_TYPE_RESP, payload, tag=tag)
                    safe_send(sock, frame)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after host-sent RESP frames"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_interleaved_handshakes(self):
        """Send HANDSHAKE frames mid-conversation.

        After initial handshake, sending additional handshakes should either
        be processed (re-handshake) or safely ignored, never crash.
        """
        sock = connect_bridge()
        try:
            # Initial handshake
            hs = build_valid_frame(VBUS_TYPE_HANDSHAKE, b"VBUS2", tag=0)
            sock.sendall(hs)
            recv_frame(sock, timeout=2.0)  # Consume response

            assert send_valid_ping(sock), "Bridge not alive after initial handshake"

            # Send additional handshakes with various payloads
            handshake_payloads = [
                b"VBUS2",
                b"VBUS1",  # Old version
                b"VBUS3",  # Future version
                b"",  # Empty
                b"VBUS2" + b"\x00" * 32,  # HMAC-style (37 bytes)
                b"VBUS2" + os.urandom(32),  # Random HMAC key
                b"\x00" * 64,  # All zeros
                b"\xff" * 64,  # All ones
            ]

            for payload in handshake_payloads:
                frame = build_valid_frame(VBUS_TYPE_HANDSHAKE, payload, tag=0)
                safe_send(sock, frame)
                drain_socket(sock, timeout=0.3)

            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after interleaved handshakes"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_rapid_connect_disconnect(self):
        """Rapidly connect and disconnect without sending any data.

        Tests QEMU chardev socket handling under rapid client churn.
        """
        alive_at_end = False
        for cycle in range(20):
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                sock.connect(BRIDGE_SOCKET)

                # Randomly: send nothing, send partial, or send valid
                action = cycle % 4
                if action == 0:
                    pass  # Connect and immediately disconnect
                elif action == 1:
                    sock.sendall(b"\x00" * 3)  # Partial header
                elif action == 2:
                    frame = build_valid_frame(VBUS_TYPE_PING, b"PING", tag=1)
                    sock.sendall(frame)
                elif action == 3:
                    sock.sendall(os.urandom(7))  # Random garbage

                sock.close()
                time.sleep(0.3)  # QEMU chardev re-listen delay
            except (ConnectionRefusedError, OSError):
                time.sleep(0.5)

        # Final verification: kernel must still be reachable
        time.sleep(0.5)
        sock, alive_at_end = reconnect_and_verify(retries=5)
        if sock:
            sock.close()
        assert alive_at_end, "Bridge dead after rapid connect/disconnect cycles"

    def test_multiple_handshake_attempts(self):
        """Send many HANDSHAKE frames in quick succession on one connection.

        Tests that the kernel handles handshake floods without state corruption.
        """
        sock = connect_bridge()
        try:
            for i in range(50):
                hs = build_valid_frame(VBUS_TYPE_HANDSHAKE, b"VBUS2", tag=0)
                if not safe_send(sock, hs):
                    sock.close()
                    time.sleep(0.3)
                    sock = connect_bridge(retries=5, delay=0.5)
                    continue

                # Drain responses to avoid buffer backup
                if i % 5 == 0:
                    drain_socket(sock, timeout=0.1)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after 50 rapid handshake attempts"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_event_frame_from_host(self):
        """Send EVENT frames from host to kernel.

        EVENT frames are normally kernel-to-host (slot notifications,
        heartbeats). Sending them host-to-kernel should be safely dropped.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            event_payloads = [
                b"SLOT_READY|0",
                b"HEARTBEAT",
                b"",
                b"\x00" * 256,
                b"FAKE_EVENT|MALICIOUS",
            ]
            for payload in event_payloads:
                frame = build_valid_frame(VBUS_TYPE_EVENT, payload, tag=0)
                safe_send(sock, frame)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after host-sent EVENT frames"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_interleaved_types(self):
        """Rapidly alternate between all frame types in random order.

        Tests that the kernel's per-type dispatch is stateless and robust
        when types arrive in unexpected sequences.
        """
        rng = make_rng("test_interleaved_types")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            all_types = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x09, 0x0A]
            payloads = [b"PING", b"HELLO", b"DATA", b"VBUS2", b"", b"X" * 512]

            for _ in range(2000):
                ftype = rng.choice(all_types)
                payload = rng.choice(payloads)
                tag = rng.randint(0, 0xFFFF)
                slot_id = rng.choice([0, 1, 7, 0xFF])
                frame = build_valid_frame(ftype, payload, slot_id=slot_id, tag=tag)
                if not safe_send(sock, frame):
                    sock.close()
                    time.sleep(0.3)
                    sock = connect_bridge(retries=5, delay=0.5)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after 2000 interleaved-type frames"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 4: BOUNDARY VALUE TESTING
# ============================================================================


@requires_qemu
class TestBoundaryValues:
    """Exhaustive boundary testing on every header field.

    Tests exact boundary values, off-by-one values, and special sentinel
    values for each of the 6 header fields.
    """

    def test_length_boundaries(self):
        """Test length field boundary values: 0, 1, max-1, max, max+1, 0xFFFFFFFF.

        For lengths <= VBUS_MAX_PAYLOAD with matching actual payload, the frame
        is valid (CRCs correct). For lengths > VBUS_MAX_PAYLOAD, the kernel
        should reject without crash.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            boundary_lengths = [
                0,  # Zero payload
                1,  # Minimal payload
                15,  # One less than header size
                16,  # Exactly header size
                17,  # One more than header size
                VBUS_MAX_PAYLOAD - 1,  # 65519
                VBUS_MAX_PAYLOAD,  # 65520 (max valid)
                VBUS_MAX_PAYLOAD + 1,  # 65521 (first invalid)
                VBUS_MAX_PAYLOAD + 16,  # Slightly over
                0x0000FFFF,  # 16-bit max
                0x00010000,  # 16-bit overflow
                0x7FFFFFFF,  # INT32_MAX
                0x80000000,  # INT32_MIN as unsigned
                0xFFFFFFFF,  # UINT32_MAX
            ]

            for length_val in boundary_lengths:
                # Build frame with actual payload matching length (capped)
                actual_len = min(length_val, 1024)  # Cap actual data sent
                payload = b"\xaa" * actual_len

                if length_val <= VBUS_MAX_PAYLOAD and length_val == actual_len:
                    # Can build a valid frame
                    frame = build_valid_frame(VBUS_TYPE_CMD, payload, tag=1)
                else:
                    # Build with length override (CRCs will be wrong for the
                    # declared length, but that is part of the test)
                    frame = build_raw_frame(
                        VBUS_TYPE_CMD, payload, length_override=length_val, tag=1
                    )

                safe_send(sock, frame)
                time.sleep(0.02)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after length boundary tests"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_type_boundaries(self):
        """Test every possible type byte value (0x00-0xFF).

        Known types should be processed; unknown types should be safely dropped.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            for type_byte in range(256):
                payload = b"TYPE_FUZZ"
                frame = build_valid_frame(type_byte, payload, tag=1)
                safe_send(sock, frame)

                # Brief drain every 32 types
                if type_byte % 32 == 0:
                    drain_socket(sock, timeout=0.05)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after type boundary sweep (0x00-0xFF)"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_tag_boundaries(self):
        """Test tag field boundary values."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            boundary_tags = [
                0x0000,  # Stream tag / minimum
                0x0001,  # First valid numbered tag
                0x00FF,  # 8-bit max
                0x0100,  # 8-bit overflow
                0x7FFF,  # 15-bit max
                0x8000,  # Sign bit
                0xFFFE,  # Max - 1
                0xFFFF,  # Async tag / maximum
            ]

            for tag_val in boundary_tags:
                # Send as CMD and as PING to test different dispatch paths
                for ftype in [VBUS_TYPE_CMD, VBUS_TYPE_PING]:
                    payload = b"PING" if ftype == VBUS_TYPE_PING else b"TAG_TEST"
                    frame = build_valid_frame(ftype, payload, tag=tag_val)
                    safe_send(sock, frame)

                drain_socket(sock, timeout=0.1)

            drain_socket(sock, timeout=0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after tag boundary tests"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_slot_id_boundaries(self):
        """Test slot_id field boundary values.

        Slot 0 is the coordinator (kernel-only). Slots 1-7 are model slots.
        Slot 8+ and 0xFF are special/invalid.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            boundary_slots = [
                0,  # Coordinator slot (kernel-only, should reject non-kernel)
                1,  # First model slot
                2,  # Second model slot
                7,  # Last standard model slot
                8,  # First invalid slot
                63,  # Registry max - 1
                64,  # Registry max
                127,  # Mid-range
                128,  # High mid-range
                254,  # Max - 1
                255,  # Broadcast / wildcard (0xFF)
            ]

            for slot_id in boundary_slots:
                # Try CMD with PING payload (tests dispatch with slot context)
                frame = build_valid_frame(
                    VBUS_TYPE_CMD, b"PING", slot_id=slot_id, tag=1
                )
                safe_send(sock, frame)

                # Try DATA with slot context
                frame = build_valid_frame(
                    VBUS_TYPE_DATA, b"SLOT_FUZZ", slot_id=slot_id, tag=2
                )
                safe_send(sock, frame)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after slot_id boundary tests"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_crc_boundary_values(self):
        """Test extreme CRC field values: 0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF.

        These are combined with both valid and invalid header/payload content.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            crc_values = [0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFE, 0xFFFFFFFF]
            payload = b"PING"

            for pcrc_val in crc_values:
                for hcrc_val in crc_values:
                    frame = build_raw_frame(
                        VBUS_TYPE_PING,
                        payload,
                        tag=1,
                        payload_crc_override=pcrc_val,
                        hdr_crc_override=hcrc_val,
                    )
                    safe_send(sock, frame)

                drain_socket(sock, timeout=0.05)

            drain_socket(sock, timeout=0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after CRC boundary value tests"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_payload_content_boundaries(self):
        """Test payloads with adversarial content patterns.

        Null bytes, 0xFF fills, alternating patterns, embedded frame headers,
        and control characters that might confuse string-based parsers.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            adversarial_payloads = [
                b"\x00" * 64,  # All nulls
                b"\xff" * 64,  # All ones
                b"\x00\xff" * 32,  # Alternating
                b"\xaa\x55" * 32,  # Checkerboard
                # Embedded frame header (parser confusion)
                struct.pack(FRAME_HDR_FMT, 0x01, 0xFF, 0, 4, 0, 0) + b"PING",
                # Nested PING frame as payload
                build_valid_frame(VBUS_TYPE_PING, b"PING", tag=1),
                # Newlines and control characters
                b"LINE1\nLINE2\r\nLINE3\x00LINE4",
                # Pipe delimiters (used in VBus command parsing)
                b"CMD|ARG1|ARG2|ARG3",
                b"|" * 256,
                # Very long single "word" (no delimiters)
                b"A" * VBUS_MAX_PAYLOAD,
                # UTF-8 multi-byte sequences
                b"\xc0\x80" * 32,  # Overlong null
                b"\xed\xa0\x80" * 16,  # Surrogate half
                b"\xf4\x90\x80\x80" * 8,  # Above U+10FFFF
            ]

            for payload in adversarial_payloads:
                frame = build_valid_frame(VBUS_TYPE_CMD, payload, tag=3)
                safe_send(sock, frame)
                drain_socket(sock, timeout=0.1)

            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after adversarial payload tests"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_combined_boundary_corners(self):
        """Test corner-case combinations: type=0 + length=0, max type + max tag, etc."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            corners = [
                # (type, slot_id, tag, payload)
                (0x00, 0x00, 0x0000, b""),  # All minimums
                (0xFF, 0xFF, 0xFFFF, b""),  # All maximums, no payload
                (0x00, 0xFF, 0xFFFF, b"\xff" * 16),  # Min type, max rest
                (0xFF, 0x00, 0x0000, b"\x00" * 16),  # Max type, min rest
                (VBUS_TYPE_PING, 0, 0, b"PING"),  # Valid type, slot 0
                (VBUS_TYPE_CMD, 0, 0xFFFF, b""),  # CMD, slot 0, async tag, empty
                (VBUS_TYPE_HANDSHAKE, 0xFF, 0xFFFF, b"VBUS2"),  # HS with max tag
            ]

            for ftype, slot_id, tag, payload in corners:
                frame = build_valid_frame(ftype, payload, slot_id=slot_id, tag=tag)
                safe_send(sock, frame)
                drain_socket(sock, timeout=0.05)

            drain_socket(sock, timeout=0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after combined boundary corner tests"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 5: ENDURANCE / SUSTAINED BARRAGE TESTING
# ============================================================================


@requires_qemu
class TestEndurance:
    """Long-running sustained attack tests.

    These tests send large volumes of fuzz traffic over extended periods
    to surface memory leaks, buffer exhaustion, and gradual state corruption
    that short tests miss.
    """

    def test_10k_random_then_ping(self):
        """Send 10,000 random frames, then verify PING still works.

        This is a quick smoke test for the endurance category.
        """
        rng = make_rng("test_10k_random_then_ping")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            for i in range(10000):
                frame_type = rng.randint(0, 0xFF)
                slot_id = rng.randint(0, 0xFF)
                tag = rng.randint(0, 0xFFFF)
                payload_len = rng.randint(0, 128)
                payload = rng.randbytes(payload_len)
                pcrc = rng.randint(0, 0xFFFFFFFF)
                hcrc = rng.randint(0, 0xFFFFFFFF)

                header = struct.pack(
                    FRAME_HDR_FMT, frame_type, slot_id, tag, payload_len, pcrc, hcrc
                )
                safe_send(sock, header + payload)

                if i % 1000 == 0:
                    drain_socket(sock, timeout=0.05)

            drain_socket(sock, timeout=0.5)
            time.sleep(0.2)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after 10,000 random frames"
        finally:
            sock.close()
            time.sleep(0.3)

    @pytest.mark.slow
    def test_sustained_barrage(self):
        """Sustained FUZZ_ENDURANCE_SECS-second barrage with periodic liveness checks.

        Default: 60 seconds of continuous random frame injection.
        Checks PING every 5 seconds to detect gradual degradation.
        """
        rng = make_rng("test_sustained_barrage")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before barrage"

            start = time.monotonic()
            deadline = start + FUZZ_ENDURANCE_SECS
            check_interval = 5.0  # PING check every 5 seconds
            next_check = start + check_interval
            frames_sent = 0
            bytes_sent = 0
            reconnects = 0

            while time.monotonic() < deadline:
                # Generate a random frame
                frame_type = rng.randint(0, 0xFF)
                slot_id = rng.randint(0, 0xFF)
                tag = rng.randint(0, 0xFFFF)
                payload_len = rng.randint(0, 512)
                payload = rng.randbytes(payload_len)

                # Mix of valid and invalid CRCs
                if rng.random() < 0.3:
                    pcrc, hcrc = compute_dual_crc(
                        frame_type, slot_id, tag, payload_len, payload
                    )
                else:
                    pcrc = rng.randint(0, 0xFFFFFFFF)
                    hcrc = rng.randint(0, 0xFFFFFFFF)

                header = struct.pack(
                    FRAME_HDR_FMT, frame_type, slot_id, tag, payload_len, pcrc, hcrc
                )
                frame = header + payload

                if not safe_send(sock, frame):
                    sock.close()
                    time.sleep(0.3)
                    sock = connect_bridge(retries=5, delay=0.5)
                    reconnects += 1
                    next_check = time.monotonic() + check_interval
                    continue

                frames_sent += 1
                bytes_sent += len(frame)

                # Micro-pause every 500 frames
                if frames_sent % 500 == 0:
                    drain_socket(sock, timeout=0.01)

                # Periodic liveness check
                now = time.monotonic()
                if now >= next_check:
                    drain_socket(sock, timeout=0.2)
                    alive = send_valid_ping(sock, timeout=3.0)
                    elapsed = now - start
                    assert alive, (
                        f"Bridge died during sustained barrage at t={elapsed:.1f}s "
                        f"after {frames_sent} frames ({bytes_sent} bytes, "
                        f"{reconnects} reconnects)"
                    )
                    next_check = now + check_interval

            # Final check
            drain_socket(sock, timeout=0.5)
            time.sleep(0.3)
            elapsed = time.monotonic() - start
            alive = send_valid_ping(sock, timeout=3.0)
            assert alive, (
                f"Bridge dead after {elapsed:.1f}s barrage: "
                f"{frames_sent} frames, {bytes_sent} bytes, "
                f"{reconnects} reconnects"
            )
        finally:
            sock.close()
            time.sleep(0.3)

    def test_valid_invalid_alternating(self):
        """Alternate between valid and invalid frames 5000 times each.

        Tests that the kernel correctly resets parser state between frames
        and does not let a bad frame corrupt processing of the next good one.
        """
        rng = make_rng("test_valid_invalid_alternating")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            valid_responses = 0
            total_pairs = 5000

            for i in range(total_pairs):
                # Send a bad frame
                bad_type = rng.randint(0, 0xFF)
                bad_payload = rng.randbytes(rng.randint(0, 64))
                bad_frame = build_raw_frame(
                    bad_type,
                    bad_payload,
                    payload_crc_override=rng.randint(0, 0xFFFFFFFF),
                    hdr_crc_override=rng.randint(0, 0xFFFFFFFF),
                )
                safe_send(sock, bad_frame)

                # Send a valid PING immediately after
                ping_frame = build_valid_frame(VBUS_TYPE_PING, b"PING", tag=1)
                if not safe_send(sock, ping_frame):
                    sock.close()
                    time.sleep(0.3)
                    sock = connect_bridge(retries=5, delay=0.5)
                    continue

                # Check for PONG response (non-blocking, brief timeout)
                result = recv_frame(sock, timeout=0.5)
                if result is not None:
                    ftype, _, _, payload = result
                    if ftype == VBUS_TYPE_PING and payload == b"PONG":
                        valid_responses += 1

                # Drain any remaining responses
                if i % 100 == 0:
                    drain_socket(sock, timeout=0.05)

            # We expect a significant fraction of valid PINGs to get PONG
            # (some may be dropped due to socket buffering, but most should work)
            response_rate = valid_responses / total_pairs
            assert response_rate > 0.1, (
                f"PING response rate too low: {valid_responses}/{total_pairs} "
                f"({response_rate:.1%}) -- kernel may be dropping valid frames "
                f"after invalid ones"
            )

            drain_socket(sock, timeout=0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after alternating valid/invalid test"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_escalating_payload_sizes(self):
        """Send frames with exponentially growing payload sizes.

        1, 2, 4, 8, ..., 65536, 131072 bytes. Tests buffer allocation
        paths at every power-of-2 boundary.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            size = 1
            while size <= 131072:
                payload = b"\xcc" * size

                if size <= VBUS_MAX_PAYLOAD:
                    # Valid frame with correct CRCs
                    frame = build_valid_frame(VBUS_TYPE_CMD, payload, tag=1)
                else:
                    # Oversized: use actual payload but declare full length
                    frame = build_raw_frame(
                        VBUS_TYPE_CMD, payload, length_override=size, tag=1
                    )

                safe_send(sock, frame)
                drain_socket(sock, timeout=0.1)
                time.sleep(0.05)

                # Verify alive at each step
                assert send_valid_ping(
                    sock, timeout=3.0
                ), f"Bridge dead after payload size {size}"

                size *= 2

        finally:
            sock.close()
            time.sleep(0.3)

    def test_concurrent_frame_types_burst(self):
        """Burst 100 frames of each known type, then verify PING.

        Tests the kernel's per-type handler isolation under burst load.
        """
        rng = make_rng("test_concurrent_frame_types_burst")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            all_types = list(KNOWN_TYPES)
            frames_per_type = 100

            for ftype in all_types:
                for _ in range(frames_per_type):
                    payload_len = rng.randint(0, 128)
                    payload = rng.randbytes(payload_len)
                    tag = rng.randint(0, 0xFFFF)
                    slot_id = rng.choice([0, 1, 7, 0xFF])
                    frame = build_valid_frame(ftype, payload, slot_id=slot_id, tag=tag)
                    safe_send(sock, frame)

                drain_socket(sock, timeout=0.1)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after per-type burst test"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_zero_byte_payload_flood(self):
        """Send 10,000 zero-payload frames with valid CRCs.

        Tests the fast path for empty-payload frames under sustained load.
        Empty frames exercise header-only CRC validation.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            for i in range(10000):
                ftype = [VBUS_TYPE_CMD, VBUS_TYPE_PING, VBUS_TYPE_DATA][i % 3]
                frame = build_valid_frame(ftype, b"", tag=i & 0xFFFF)
                if not safe_send(sock, frame):
                    sock.close()
                    time.sleep(0.3)
                    sock = connect_bridge(retries=5, delay=0.5)

                if i % 1000 == 0:
                    drain_socket(sock, timeout=0.05)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after 10,000 zero-payload frames"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 6: REGRESSION / TARGETED ATTACK PATTERNS
# ============================================================================


@requires_qemu
class TestTargetedAttacks:
    """Targeted attack patterns derived from common parser vulnerabilities.

    These are not random -- they target specific classes of bugs:
    integer overflow, off-by-one, null pointer, format string, etc.
    """

    def test_integer_overflow_length(self):
        """Length values that trigger integer overflow when added to header size.

        FRAME_HDR_SIZE (16) + length should not overflow a 32-bit integer.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            overflow_lengths = [
                0xFFFFFFFF,  # Max u32
                0xFFFFFFFF - FRAME_HDR_SIZE,  # Wraps to 0 with header
                0xFFFFFFFF - FRAME_HDR_SIZE + 1,  # Wraps to 1 with header
                0x80000000,  # INT32_MIN
                0x80000000 - FRAME_HDR_SIZE,  # Wraps around INT32 boundary
                0x7FFFFFFF,  # INT32_MAX
                0x7FFFFFF0,  # Near INT32_MAX, aligned
            ]

            for length_val in overflow_lengths:
                # Send header only (don't actually send gigabytes of payload)
                frame = build_raw_frame(
                    VBUS_TYPE_CMD, b"", length_override=length_val, tag=1
                )
                safe_send(sock, frame)
                time.sleep(0.02)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after integer overflow length attacks"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_format_string_payloads(self):
        """Payloads containing format string specifiers.

        If the kernel ever passes payload content to printf-family functions,
        these would trigger crashes or information disclosure.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            fmt_payloads = [
                b"%s%s%s%s%s%s%s%s%s%s",
                b"%n%n%n%n%n%n%n%n",
                b"%x" * 100,
                b"%p" * 100,
                b"%.99999d",
                b"%1$s" * 20,
                b"AAAA" + b"%08x." * 20,
                b"%s" * 1000,
                b"\x00%n\x00%n\x00",
            ]

            for payload in fmt_payloads:
                frame = build_valid_frame(VBUS_TYPE_CMD, payload, tag=1)
                safe_send(sock, frame)
                drain_socket(sock, timeout=0.1)

            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after format string injection"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_null_byte_injection(self):
        """Payloads with embedded null bytes at various positions.

        Tests that the kernel uses length-based parsing (not C string functions).
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            null_payloads = [
                b"\x00",  # Single null
                b"\x00" * 256,  # All nulls
                b"PING\x00HIDDEN",  # Null in middle
                b"\x00PING",  # Null prefix
                b"PING\x00",  # Null suffix
                b"A\x00B\x00C\x00D\x00",  # Interleaved nulls
                b"\x00" * 16 + b"PING",  # Null header-sized prefix
            ]

            for payload in null_payloads:
                frame = build_valid_frame(VBUS_TYPE_CMD, payload, tag=1)
                safe_send(sock, frame)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after null byte injection"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_vbus_command_injection(self):
        """Payloads crafted to exploit VBus command parsing.

        The kernel parses CMD payloads as pipe-delimited commands. These
        test for injection, overflow, and edge cases in that parser.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            cmd_payloads = [
                # Normal commands with extra delimiters
                b"PING|extra|args|that|should|be|ignored",
                b"|||||||",
                b"|PING|",
                # Long command names
                b"A" * 4096,
                b"PING" + b" " * 4096,
                # Embedded newlines
                b"PING\nSLOT_RESET|1",
                b"PING\r\nSLOT_RESET|1",
                # Attempt to inject multiple commands
                b"PING\x00SLOT_RESET|0",
                # Special VBus commands with bad arguments
                b"SLOT_START|999",
                b"SLOT_RESET|-1",
                b"SLOT_START|",
                b"SLOT_START|0|extra",
                b"WARP_DATA|0|99999999",
                b"HP_STATS|",
                b"AGENT_KILL_ALL|",
                # Unicode in commands
                b"PING\xc0\xaf",
                b"\xef\xbb\xbfPING",  # BOM prefix
            ]

            for payload in cmd_payloads:
                frame = build_valid_frame(VBUS_TYPE_CMD, payload, tag=1)
                safe_send(sock, frame)
                drain_socket(sock, timeout=0.1)

            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after command injection attacks"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_header_as_payload(self):
        """Payload that is exactly a valid frame header.

        Tests that the parser does not get confused by header-like data
        appearing in the payload section.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            # Build a valid PING frame header (without payload) as the payload
            inner_header = struct.pack(
                FRAME_HDR_FMT, VBUS_TYPE_PING, 0xFF, 1, 4, 0xDEADBEEF, 0xCAFEBABE
            )
            frame = build_valid_frame(VBUS_TYPE_CMD, inner_header, tag=2)
            safe_send(sock, frame)

            # Multiple nested headers
            nested = inner_header * 10
            frame = build_valid_frame(VBUS_TYPE_CMD, nested, tag=3)
            safe_send(sock, frame)

            # Valid frame inside valid frame
            inner_frame = build_valid_frame(VBUS_TYPE_PING, b"PING", tag=99)
            frame = build_valid_frame(VBUS_TYPE_CMD, inner_frame, tag=4)
            safe_send(sock, frame)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after header-as-payload attack"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_alignment_attacks(self):
        """Payloads designed to break alignment assumptions.

        If the kernel assumes payload alignment (e.g., 4-byte or 8-byte),
        odd-sized payloads could trigger misaligned access faults.
        """
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            # Payloads of every size from 0 to 33 (covers all alignment cases
            # for 1, 2, 4, 8, 16, 32 byte alignments)
            for size in range(34):
                payload = b"\xbb" * size
                frame = build_valid_frame(VBUS_TYPE_CMD, payload, tag=1)
                safe_send(sock, frame)

            # Specific problematic sizes
            for size in [
                63,
                65,
                127,
                129,
                255,
                257,
                511,
                513,
                1023,
                1025,
                2047,
                2049,
                4095,
                4097,
                8191,
                8193,
            ]:
                payload = b"\xcc" * size
                frame = build_valid_frame(VBUS_TYPE_CMD, payload, tag=1)
                safe_send(sock, frame)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after alignment attack"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_repeated_pattern_payloads(self):
        """Payloads with repeating byte patterns that might confuse ring buffers.

        The kernel uses a 4MB ring buffer for RX reassembly. Repeating patterns
        could cause false sync if the parser relies on content matching.
        """
        make_rng("test_repeated_pattern_payloads")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            patterns = [
                bytes([0x01, 0xFF, 0x00, 0x04]),  # Looks like a frame start
                bytes([0x05, 0xFF, 0x01, 0x00]),  # PING-like pattern
                b"\x04\x00" * 512,  # HANDSHAKE type repeated
                bytes(range(256)),  # All byte values sequential
                bytes(range(255, -1, -1)),  # Reverse sequential
            ]

            for pattern in patterns:
                # Repeat pattern to fill various sizes
                for multiplier in [1, 10, 100, 256]:
                    payload = (pattern * multiplier)[:VBUS_MAX_PAYLOAD]
                    frame = build_valid_frame(VBUS_TYPE_CMD, payload, tag=1)
                    safe_send(sock, frame)

                drain_socket(sock, timeout=0.1)

            drain_socket(sock, timeout=0.3)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after repeated pattern attacks"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# AREA 7: BATCH FRAME FUZZING
# ============================================================================


@requires_qemu
class TestBatchFrameFuzzing:
    """Fuzz the BATCH (0x09) frame type specifically.

    BATCH frames contain newline-separated commands. The kernel splits
    these and processes each sub-command. This area tests malformed
    batch payloads.
    """

    def test_empty_batch(self):
        """BATCH frame with empty payload."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            frame = build_valid_frame(VBUS_TYPE_BATCH, b"", tag=1)
            safe_send(sock, frame)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after empty BATCH frame"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_batch_many_commands(self):
        """BATCH frame with hundreds of sub-commands."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            # 500 PING commands in one batch
            batch_payload = b"\n".join([b"PING"] * 500)
            frame = build_valid_frame(VBUS_TYPE_BATCH, batch_payload, tag=1)
            safe_send(sock, frame)

            drain_socket(sock, timeout=2.0)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after 500-command BATCH"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_batch_malformed_subcommands(self):
        """BATCH frame with a mix of valid and malformed sub-commands."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            subcommands = [
                b"PING",
                b"",  # Empty line
                b"NONEXISTENT_CMD",
                b"PING",
                b"\x00\x00\x00",  # Null bytes
                b"PING",
                b"A" * 4096,  # Very long command
                b"PING",
                b"|||||",  # Only delimiters
                b"PING",
            ]
            batch_payload = b"\n".join(subcommands)
            frame = build_valid_frame(VBUS_TYPE_BATCH, batch_payload, tag=1)
            safe_send(sock, frame)

            drain_socket(sock, timeout=1.0)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after malformed BATCH sub-commands"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_batch_only_newlines(self):
        """BATCH frame containing only newline characters."""
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            for count in [1, 10, 100, 1000]:
                payload = b"\n" * count
                frame = build_valid_frame(VBUS_TYPE_BATCH, payload, tag=1)
                safe_send(sock, frame)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after newline-only BATCH frames"
        finally:
            sock.close()
            time.sleep(0.3)

    def test_batch_binary_payload(self):
        """BATCH frame with binary (non-text) payload."""
        rng = make_rng("test_batch_binary_payload")
        sock = connect_bridge()
        try:
            assert send_valid_ping(sock), "Bridge not alive before test"

            for _ in range(20):
                payload = rng.randbytes(rng.randint(1, 4096))
                frame = build_valid_frame(VBUS_TYPE_BATCH, payload, tag=1)
                safe_send(sock, frame)

            drain_socket(sock, timeout=0.5)
            assert send_valid_ping(
                sock, timeout=3.0
            ), "Bridge dead after binary BATCH payloads"
        finally:
            sock.close()
            time.sleep(0.3)


# ============================================================================
# SUMMARY REPORTING (optional, for standalone execution)
# ============================================================================

if __name__ == "__main__":
    print("VBus Frame Fuzzer — Comprehensive Protocol Resilience Testing")
    print(f"  Bridge socket: {BRIDGE_SOCKET}")
    print(f"  Fuzz seed:     {FUZZ_SEED:#x}")
    print(f"  Fuzz count:    {FUZZ_COUNT}")
    print(f"  Endurance:     {FUZZ_ENDURANCE_SECS}s")
    print()
    print("Run with: python3 -m pytest tests/fuzz_vbus_frames.py -v --tb=short")
    print("  Add -k 'random' to run only random generation tests")
    print("  Add -k 'mutation' to run only mutation tests")
    print("  Add -k 'state' to run only protocol state tests")
    print("  Add -k 'boundary' to run only boundary value tests")
    print("  Add -k 'endurance' to run only endurance tests")
    print("  Add -k 'attack' to run only targeted attack tests")
    print("  Add -k 'batch' to run only batch frame tests")
    print("  Add -m 'slow' to include the sustained barrage test")
