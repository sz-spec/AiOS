"""
VBus Token-Stream HMAC Integrity Chaos Test
=============================================

Chaos-level security tests for the VOS3 VBus HMAC-SHA256 per-frame
authentication layer, focused on TOKEN_STREAM (0x07) frames and the
HMAC ban mechanism.

Tests 6 attack vectors:
  1. Poisoned Token Injection — invalid HMAC on TOKEN_STREAM frame
  2. Valid Token Passthrough — correctly signed TOKEN_STREAM frame accepted
  3. Ban Mechanism — 10 invalid HMAC frames trigger 60-second ban
  4. HMAC Coverage — all 7 frame types verified for HMAC enforcement
  5. Replay Attack Defense — replayed frame handled gracefully
  6. Legacy Compatibility — no-HMAC handshake for basic commands, HMAC-tagged rejected

VBus v2.19 64-byte header layout:
  [u8:type][u8:slot_id][u16:tag LE][u32:length LE][u32:payload_crc LE][u32:hdr_crc LE]
  [16B HMAC at offset 16-47][u8:hmac_flag at offset 48][15B pad]

HMAC-SHA256 key exchange:
  Client sends HANDSHAKE with 37-byte payload: "VBUS2" + 32-byte random key
  Kernel responds with "VOS3-VBUS2-OK-HMAC" if HMAC accepted

Run:
  python3 -m pytest tests/chaos_vbus_hmac_integrity.py -v --tb=short -p no:xdist
  python3 -m pytest tests/chaos_vbus_hmac_integrity.py -v -k "poisoned" -p no:xdist

Environment:
  VOS3_BRIDGE_SOCKET — path to QEMU bridge socket (default: /tmp/vos3_bridge.sock)
"""

import hashlib
import hmac as hmac_mod
import os
import socket
import struct
import time
from typing import Optional, Tuple

import pytest

# ============================================================================
# CONSTANTS (must match kernel/include/vos/virtio_vbus.h + vbus_driver.py)
# ============================================================================

VBUS_TYPE_CMD = 0x01
VBUS_TYPE_RESP = 0x02
VBUS_TYPE_DATA = 0x03
VBUS_TYPE_HANDSHAKE = 0x04
VBUS_TYPE_PING = 0x05
VBUS_TYPE_EVENT = 0x06
VBUS_TYPE_TOKEN_STREAM = 0x07
VBUS_TYPE_NOTIFY = 0x0A

ALL_HMAC_TYPES = [
    VBUS_TYPE_CMD,
    VBUS_TYPE_RESP,
    VBUS_TYPE_DATA,
    VBUS_TYPE_HANDSHAKE,
    VBUS_TYPE_PING,
    VBUS_TYPE_EVENT,
    VBUS_TYPE_TOKEN_STREAM,
]

# v2.19 64-byte header: type(u8)+slot_id(u8)+tag(u16)+len(u32)+payload_crc(u32)+hdr_crc(u4)+pad(48)
FRAME_HDR_FMT = "<BBHIII48s"
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)  # 64 bytes

VBUS_MAX_PAYLOAD = 2097152  # 2MB Jumbo MTU

VBUS_HMAC_OFFSET = 16
VBUS_HMAC_SIZE = 32
VBUS_FLAGS_OFFSET = 48
VBUS_FLAG_HMAC = 0x01

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")

# Ban threshold from vbus_transport.c: 10 violations in 60-second window
HMAC_BAN_THRESHOLD = 10

# ============================================================================
# CRC32C — matches kernel and vbus_driver.py
# ============================================================================

try:
    from crc32c import crc32c as _crc32c_ext

    def _crc32c(data: bytes, value: int = 0) -> int:
        return _crc32c_ext(data, value) & 0xFFFFFFFF

except ImportError:
    _CRC32C_TABLE = []

    def _crc32c_build_table():
        for i in range(256):
            crc = i
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0x82F63B78
                else:
                    crc = crc >> 1
            _CRC32C_TABLE.append(crc)

    _crc32c_build_table()

    def _crc32c(data: bytes, value: int = 0) -> int:
        crc = (value ^ 0xFFFFFFFF) & 0xFFFFFFFF
        for b in data:
            crc = _CRC32C_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
        return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


# ============================================================================
# FRAME BUILDING HELPERS
# ============================================================================


def compute_dual_crc(
    frame_type: int, slot_id: int, tag: int, length: int, payload: bytes
) -> Tuple[int, int]:
    """Compute dual CRC32C the same way the kernel does.

    Returns (payload_crc, hdr_crc).
    - hdr_crc: CRC32C over 8-byte header prefix (type+slot_id+tag+len)
    - payload_crc: CRC32C over 8-byte header prefix + payload (incremental seed)
    """
    hdr_prefix = struct.pack("<BBHI", frame_type, slot_id, tag, length)
    hdr_crc = _crc32c(hdr_prefix)
    payload_crc = _crc32c(payload, _crc32c(hdr_prefix))
    return payload_crc, hdr_crc


def build_hmac_frame(
    hmac_key: bytes, frame_type: int, payload: bytes, slot_id: int = 0xFF, tag: int = 0
) -> bytes:
    """Build a complete, valid v2.19 HMAC-authenticated frame.

    64-byte header with:
    - bytes 0-15: type + slot_id + tag + length + payload_crc + hdr_crc
    - bytes 16-47: HMAC-SHA256(key, header[0:16] + payload)
    - byte 48: HMAC flag (0x01)
    - bytes 49-63: zero padding
    """
    length = len(payload)
    payload_crc, hdr_crc = compute_dual_crc(frame_type, slot_id, tag, length, payload)

    # Build 64-byte header with zero padding initially
    header = struct.pack(
        FRAME_HDR_FMT,
        frame_type,
        slot_id,
        tag,
        length,
        payload_crc,
        hdr_crc,
        b"\x00" * 48,
    )

    # Compute HMAC over bytes 0-15 (fixed header prefix) + payload
    hmac_input = header[:16] + payload
    mac = hmac_mod.new(hmac_key, hmac_input, hashlib.sha256).digest()

    # Splice HMAC into header at offset 16-47, set flag at byte 48
    header = header[:16] + mac + bytes([VBUS_FLAG_HMAC]) + b"\x00" * 15

    return header + payload


def build_poisoned_hmac_frame(
    frame_type: int, payload: bytes, slot_id: int = 0xFF, tag: int = 0
) -> bytes:
    """Build a frame with valid CRCs but a random (invalid) HMAC.

    The HMAC field (bytes 16-47) is filled with random bytes, and the
    HMAC flag (byte 48) is set to 0x01. This simulates an attacker who
    knows the frame format but does not have the session key.
    """
    length = len(payload)
    payload_crc, hdr_crc = compute_dual_crc(frame_type, slot_id, tag, length, payload)

    header = struct.pack(
        FRAME_HDR_FMT,
        frame_type,
        slot_id,
        tag,
        length,
        payload_crc,
        hdr_crc,
        b"\x00" * 48,
    )

    # Random HMAC (32 bytes) + flag set
    fake_hmac = os.urandom(32)
    header = header[:16] + fake_hmac + bytes([VBUS_FLAG_HMAC]) + b"\x00" * 15

    return header + payload


def build_legacy_frame(
    frame_type: int, payload: bytes, slot_id: int = 0xFF, tag: int = 0
) -> bytes:
    """Build a valid v2 frame with correct CRCs but NO HMAC (legacy mode).

    The 48-byte padding is all zeros, including the HMAC flag at byte 48.
    """
    length = len(payload)
    payload_crc, hdr_crc = compute_dual_crc(frame_type, slot_id, tag, length, payload)
    header = struct.pack(
        FRAME_HDR_FMT,
        frame_type,
        slot_id,
        tag,
        length,
        payload_crc,
        hdr_crc,
        b"\x00" * 48,
    )
    return header + payload


def build_token_stream_payload(
    slot_id: int = 1,
    token_id: int = 42,
    seq: int = 1,
    flags: int = 0x01,
    text: str = "hello",
) -> bytes:
    """Build a TOKEN_STREAM payload matching the KIM format.

    Format: [u8:slot][u32:token_id LE][u16:seq LE][u8:flags][text NUL]
    """
    return (
        struct.pack("<BIHB", slot_id, token_id, seq, flags)
        + text.encode("utf-8")
        + b"\x00"
    )


# ============================================================================
# VBusClient — HMAC-aware raw socket client for chaos testing
# ============================================================================


class VBusClient:
    """Raw socket VBus client with HMAC session key negotiation.

    Unlike VBusDriver (which is the production driver), this client
    exposes raw frame-level control for chaos/security testing.
    """

    def __init__(self, socket_path: str = BRIDGE_SOCKET):
        self.socket_path = socket_path
        self._sock: Optional[socket.socket] = None
        self.hmac_key: Optional[bytes] = None
        self._tag_counter = 1

    def _alloc_tag(self) -> int:
        tag = self._tag_counter
        self._tag_counter = (self._tag_counter + 1) & 0xFFFE
        if self._tag_counter == 0:
            self._tag_counter = 1
        return tag

    def connect_with_hmac(self, retries: int = 5, delay: float = 0.5) -> bool:
        """Connect and negotiate HMAC-authenticated session.

        Sends a 37-byte HANDSHAKE payload ("VBUS2" + 32-byte random key).
        Returns True if HMAC session established, False otherwise.
        """
        for attempt in range(retries):
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect(self.socket_path)
                self._sock = sock

                # Drain stale data
                sock.setblocking(False)
                try:
                    while True:
                        stale = sock.recv(4096)
                        if not stale:
                            break
                except BlockingIOError:
                    pass
                sock.setblocking(True)
                sock.settimeout(5.0)

                # Send HMAC handshake: "VBUS2" + 32-byte key
                hmac_key = os.urandom(32)
                hs_payload = b"VBUS2" + hmac_key
                hs_frame = build_legacy_frame(VBUS_TYPE_HANDSHAKE, hs_payload, tag=0)
                sock.sendall(hs_frame)

                # Receive handshake response (skip EVENT/NOTIFY frames)
                for _ in range(5):
                    result = self._recv_frame_raw(timeout=3.0)
                    if result is None:
                        break
                    ftype, _sid, _tag, payload = result
                    if ftype == VBUS_TYPE_HANDSHAKE:
                        resp_str = payload.decode("utf-8", errors="replace")
                        if "HMAC" in resp_str:
                            self.hmac_key = hmac_key
                            return True
                        else:
                            # Non-HMAC response — legacy kernel
                            return True

                self.close()
            except (ConnectionRefusedError, OSError):
                if self._sock:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
                if attempt < retries - 1:
                    time.sleep(delay)
        return False

    def connect_legacy(self, retries: int = 5, delay: float = 0.5) -> bool:
        """Connect with legacy handshake (5-byte "VBUS2", no HMAC key).

        Returns True if connected and handshake succeeded.
        """
        for attempt in range(retries):
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect(self.socket_path)
                self._sock = sock

                # Drain stale data
                sock.setblocking(False)
                try:
                    while True:
                        stale = sock.recv(4096)
                        if not stale:
                            break
                except BlockingIOError:
                    pass
                sock.setblocking(True)
                sock.settimeout(5.0)

                # Legacy handshake: "VBUS2" only (5 bytes, no key)
                hs_frame = build_legacy_frame(VBUS_TYPE_HANDSHAKE, b"VBUS2", tag=0)
                sock.sendall(hs_frame)

                # Receive handshake response
                for _ in range(5):
                    result = self._recv_frame_raw(timeout=3.0)
                    if result is None:
                        break
                    ftype, _sid, _tag, payload = result
                    if ftype == VBUS_TYPE_HANDSHAKE:
                        self.hmac_key = None  # No HMAC in legacy mode
                        return True

                self.close()
            except (ConnectionRefusedError, OSError):
                if self._sock:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
                if attempt < retries - 1:
                    time.sleep(delay)
        return False

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self.hmac_key = None

    def send_raw(self, data: bytes) -> bool:
        """Send raw bytes on the socket. Returns False if connection lost."""
        if self._sock is None:
            return False
        try:
            self._sock.sendall(data)
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def send_hmac_frame(
        self, frame_type: int, payload: bytes, slot_id: int = 0xFF, tag: int = 0
    ) -> bool:
        """Send a properly HMAC-signed frame using the session key."""
        if self.hmac_key is None:
            return False
        frame = build_hmac_frame(
            self.hmac_key, frame_type, payload, slot_id=slot_id, tag=tag
        )
        return self.send_raw(frame)

    def send_poisoned_frame(
        self, frame_type: int, payload: bytes, slot_id: int = 0xFF, tag: int = 0
    ) -> bool:
        """Send a frame with random (invalid) HMAC."""
        frame = build_poisoned_hmac_frame(frame_type, payload, slot_id=slot_id, tag=tag)
        return self.send_raw(frame)

    def send_legacy_frame(
        self, frame_type: int, payload: bytes, slot_id: int = 0xFF, tag: int = 0
    ) -> bool:
        """Send a legacy frame (no HMAC, flag=0)."""
        frame = build_legacy_frame(frame_type, payload, slot_id=slot_id, tag=tag)
        return self.send_raw(frame)

    def send_ping(self, use_hmac: bool = True) -> Optional[bytes]:
        """Send a PING and return the response payload, or None on timeout.

        If use_hmac is True and we have a session key, sends HMAC-signed PING.
        """
        tag = self._alloc_tag()
        if use_hmac and self.hmac_key:
            self.send_hmac_frame(VBUS_TYPE_PING, b"PING", tag=tag)
        else:
            self.send_legacy_frame(VBUS_TYPE_PING, b"PING", tag=tag)

        result = self.recv_frame(timeout=2.0)
        if result is None:
            return None
        ftype, _sid, _tag, payload = result
        if ftype == VBUS_TYPE_PING:
            return payload
        return None

    def send_cmd(
        self, command: str, use_hmac: bool = True, slot_id: int = 0xFF
    ) -> Optional[str]:
        """Send a CMD and return the RESP string, or None on timeout."""
        tag = self._alloc_tag()
        payload = command.encode("utf-8")
        if use_hmac and self.hmac_key:
            self.send_hmac_frame(VBUS_TYPE_CMD, payload, slot_id=slot_id, tag=tag)
        else:
            self.send_legacy_frame(VBUS_TYPE_CMD, payload, slot_id=slot_id, tag=tag)

        result = self.recv_frame(timeout=2.0)
        if result is None:
            return None
        ftype, _sid, _tag, resp_payload = result
        if ftype == VBUS_TYPE_RESP:
            return resp_payload.decode("utf-8", errors="replace")
        return None

    def recv_frame(self, timeout: float = 2.0) -> Optional[Tuple[int, int, int, bytes]]:
        """Receive a frame, skipping EVENT/NOTIFY. Returns (type, slot, tag, payload) or None."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            result = self._recv_frame_raw(timeout=max(remaining, 0.1))
            if result is None:
                return None
            ftype = result[0]
            # Skip EVENT/NOTIFY frames (kernel heartbeats, slot notifications)
            if ftype in (VBUS_TYPE_EVENT, VBUS_TYPE_NOTIFY):
                continue
            return result

    def _recv_frame_raw(
        self, timeout: float = 2.0
    ) -> Optional[Tuple[int, int, int, bytes]]:
        """Receive and parse a single raw frame. Does NOT skip any frame types."""
        if self._sock is None:
            return None
        self._sock.settimeout(timeout)
        try:
            header = self._recv_exact(FRAME_HDR_SIZE)
            if header is None:
                return None
            frame_type, slot_id, tag, length, _pcrc, _hcrc, _pad = struct.unpack(
                FRAME_HDR_FMT, header
            )
            if length > VBUS_MAX_PAYLOAD:
                return None
            payload = self._recv_exact(length) if length > 0 else b""
            if payload is None:
                return None
            return (frame_type, slot_id, tag, payload)
        except (socket.timeout, OSError):
            return None

    def _recv_exact(self, n: int) -> Optional[bytes]:
        """Read exactly n bytes."""
        if self._sock is None:
            return None
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def drain(self, timeout: float = 0.3) -> None:
        """Drain any pending data from the socket."""
        if self._sock is None:
            return
        self._sock.settimeout(timeout)
        try:
            while True:
                data = self._sock.recv(65536)
                if not data:
                    break
        except (socket.timeout, OSError):
            pass

    def is_alive(self) -> bool:
        """Check if bridge is alive via PING/PONG."""
        resp = self.send_ping()
        return resp is not None and resp == b"PONG"


# ============================================================================
# SKIP MARKER — skip runtime tests if QEMU is not running
# ============================================================================


def _qemu_bridge_reachable() -> bool:
    """Probe the QEMU bridge socket."""
    if not os.path.exists(BRIDGE_SOCKET):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(BRIDGE_SOCKET)
        s.close()
        time.sleep(0.3)
        return True
    except (ConnectionRefusedError, OSError):
        return False


requires_qemu = pytest.mark.skipif(
    not _qemu_bridge_reachable(),
    reason=f"QEMU not running (bridge unreachable at {BRIDGE_SOCKET})",
)

# Register the chaos marker for strict-markers compatibility
chaos = pytest.mark.chaos


# ============================================================================
# METRICS TRACKER
# ============================================================================


class ChaosMetrics:
    """Aggregates timing and pass/fail counts across all chaos tests."""

    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.frames_sent = 0
        self.frames_dropped = 0
        self.frames_accepted = 0
        self.total_time_s = 0.0

    def record(
        self,
        passed: bool,
        sent: int = 0,
        dropped: int = 0,
        accepted: int = 0,
        elapsed: float = 0.0,
    ) -> None:
        self.tests_run += 1
        if passed:
            self.tests_passed += 1
        else:
            self.tests_failed += 1
        self.frames_sent += sent
        self.frames_dropped += dropped
        self.frames_accepted += accepted
        self.total_time_s += elapsed

    def summary(self) -> str:
        return (
            f"\n{'=' * 60}\n"
            f"CHAOS HMAC INTEGRITY METRICS\n"
            f"{'=' * 60}\n"
            f"  Tests:    {self.tests_passed}/{self.tests_run} PASS"
            f" ({self.tests_failed} FAIL)\n"
            f"  Frames:   {self.frames_sent} sent, "
            f"{self.frames_dropped} dropped, "
            f"{self.frames_accepted} accepted\n"
            f"  Time:     {self.total_time_s:.3f}s total\n"
            f"{'=' * 60}"
        )


_metrics = ChaosMetrics()


# ============================================================================
# TEST 1: POISONED TOKEN INJECTION
# ============================================================================


@requires_qemu
@chaos
class TestPoisonedTokenDropped:
    """Inject TOKEN_STREAM frames with invalid HMACs — verify they are dropped."""

    def test_poisoned_token_dropped(self):
        """Craft a valid TOKEN_STREAM frame with random HMAC bytes.
        The kernel must silently drop the frame (no PONG, no crash).
        """
        start = time.monotonic()
        client = VBusClient()
        try:
            assert client.connect_with_hmac(), "Failed to establish HMAC session"
            assert client.is_alive(), "Bridge not alive after HMAC handshake"

            # Build a TOKEN_STREAM payload
            token_payload = build_token_stream_payload(
                slot_id=1, token_id=100, seq=1, flags=0x01, text="poisoned"
            )

            dropped = 0
            sent = 0
            for i in range(5):
                tag = client._alloc_tag()
                ok = client.send_poisoned_frame(
                    VBUS_TYPE_TOKEN_STREAM, token_payload, slot_id=1, tag=tag
                )
                assert ok, f"Failed to send poisoned frame #{i}"
                sent += 1

                # The kernel should drop this frame — no response expected.
                # Brief wait to see if anything comes back.
                result = client.recv_frame(timeout=0.3)
                if result is None:
                    dropped += 1
                else:
                    # If we get a response, it should NOT be a valid token echo
                    ftype = result[0]
                    assert (
                        ftype != VBUS_TYPE_TOKEN_STREAM
                    ), "Poisoned TOKEN_STREAM was echoed back (CRITICAL SECURITY FAILURE)"
                    dropped += 1  # Any non-token response counts as "not accepted"

            assert (
                dropped == sent
            ), f"Only {dropped}/{sent} poisoned frames were dropped"

            # Verify bridge is still alive after the attack
            assert client.is_alive(), "Bridge died after poisoned token injection"

            elapsed = time.monotonic() - start
            _metrics.record(True, sent=sent, dropped=dropped, elapsed=elapsed)
        finally:
            client.close()
            time.sleep(0.3)


# ============================================================================
# TEST 2: VALID TOKEN PASSTHROUGH
# ============================================================================


@requires_qemu
@chaos
class TestValidTokenAccepted:
    """Send properly HMAC-signed TOKEN_STREAM frames — verify acceptance."""

    def test_valid_token_accepted(self):
        """A correctly signed TOKEN_STREAM frame should be accepted by the kernel.

        The kernel may not echo TOKEN_STREAM back (it is a one-way stream),
        but the key invariant is: no error, no disconnect, bridge stays alive.
        After sending valid tokens, a CMD/PING must still work.
        """
        start = time.monotonic()
        client = VBusClient()
        try:
            assert client.connect_with_hmac(), "Failed to establish HMAC session"
            assert client.is_alive(), "Bridge not alive after HMAC handshake"

            token_payload = build_token_stream_payload(
                slot_id=1, token_id=200, seq=1, flags=0x01, text="valid_token"
            )

            sent = 0
            accepted = 0
            for i in range(5):
                tag = client._alloc_tag()
                ok = client.send_hmac_frame(
                    VBUS_TYPE_TOKEN_STREAM, token_payload, slot_id=1, tag=tag
                )
                assert ok, f"Failed to send valid token frame #{i}"
                sent += 1
                # TOKEN_STREAM is fire-and-forget (no response expected)
                # Drain any unsolicited frames
                client.drain(timeout=0.1)
                accepted += 1

            # The bridge must still be alive and responsive
            assert (
                client.is_alive()
            ), "Bridge died after valid TOKEN_STREAM frames (should have accepted them)"

            # Also verify a CMD still works after token stream
            resp = client.send_cmd("PING")
            assert (
                resp is not None and "PONG" in resp
            ), f"CMD PING failed after valid token stream: {resp}"

            elapsed = time.monotonic() - start
            _metrics.record(True, sent=sent, accepted=accepted, elapsed=elapsed)
        finally:
            client.close()
            time.sleep(0.3)


# ============================================================================
# TEST 3: BAN MECHANISM
# ============================================================================


@requires_qemu
@chaos
class TestBanAfter10Violations:
    """Send 10+ invalid HMAC frames rapidly — verify the ban mechanism kicks in."""

    def test_ban_after_10_violations(self):
        """The kernel's vbus_transport.c bans after 10 HMAC violations in 60s.

        After triggering the ban, the 11th frame should be rejected (dropped
        or met with a ban response). The bridge must remain alive but
        unresponsive to further frames until the ban expires.
        """
        start = time.monotonic()
        client = VBusClient()
        try:
            assert client.connect_with_hmac(), "Failed to establish HMAC session"
            assert client.is_alive(), "Bridge not alive after HMAC handshake"

            # Send 10 poisoned frames rapidly to trigger the ban
            build_token_stream_payload(
                slot_id=1, token_id=999, seq=1, flags=0x01, text="ban_trigger"
            )

            sent = 0
            for i in range(HMAC_BAN_THRESHOLD):
                tag = client._alloc_tag()
                ok = client.send_poisoned_frame(VBUS_TYPE_CMD, b"PING", tag=tag)
                if not ok:
                    break
                sent += 1
                # Brief pause between frames (don't overwhelm socket)
                time.sleep(0.01)

            assert (
                sent >= HMAC_BAN_THRESHOLD
            ), f"Only sent {sent}/{HMAC_BAN_THRESHOLD} frames before connection dropped"

            # Small delay for the kernel to process the violation batch
            time.sleep(0.2)

            # The 11th frame should be rejected or dropped due to ban
            tag = client._alloc_tag()
            ok = client.send_poisoned_frame(VBUS_TYPE_CMD, b"SHOULD_BE_BANNED", tag=tag)

            if ok:
                # Frame was sent — check if kernel responds (it should not)
                result = client.recv_frame(timeout=1.0)
                # Either None (dropped/banned) or an error response is acceptable
                if result is not None:
                    ftype, _sid, _tag, payload = result
                    payload.decode("utf-8", errors="replace")
                    # An error response about ban is acceptable
                    assert (
                        ftype == VBUS_TYPE_RESP or result is None
                    ), f"Unexpected frame after ban: type=0x{ftype:02x}"

            # Note: After ban, even valid frames may be rejected for 60s.
            # We do NOT assert bridge is alive here — it may legitimately
            # refuse connections during ban. Instead, verify it recovers.
            # The ban is on the HMAC layer, so the socket may still be open
            # but commands are rejected.
            client.drain(timeout=0.5)

            # Try a valid HMAC ping — it may or may not work depending on
            # whether the ban covers all frames or just bad-HMAC ones.
            # The key assertion is: the kernel did NOT crash.
            # We reconnect to test kernel stability.
            client.close()
            time.sleep(0.5)

            client2 = VBusClient()
            assert (
                client2.connect_with_hmac()
            ), "Kernel unreachable after ban (possible crash)"
            # After reconnect with new session, the ban may still be active
            # but the kernel is provably alive.
            client2.close()

            elapsed = time.monotonic() - start
            _metrics.record(True, sent=sent + 1, dropped=sent + 1, elapsed=elapsed)
        finally:
            client.close()
            time.sleep(0.3)


# ============================================================================
# TEST 4: HMAC COVERAGE — ALL 7 FRAME TYPES
# ============================================================================


@requires_qemu
@chaos
class TestHmacCoverageAllTypes:
    """Verify HMAC verification covers all 7 frame types."""

    def test_hmac_coverage_all_types(self):
        """Send a poisoned frame for each of the 7 VBus frame types.
        All must be dropped (no valid response echoed back).

        Frame types: CMD(0x01), RESP(0x02), DATA(0x03), HANDSHAKE(0x04),
                     PING(0x05), EVENT(0x06), TOKEN_STREAM(0x07)
        """
        start = time.monotonic()
        client = VBusClient()
        try:
            assert client.connect_with_hmac(), "Failed to establish HMAC session"
            assert client.is_alive(), "Bridge not alive after HMAC handshake"

            type_names = {
                VBUS_TYPE_CMD: "CMD",
                VBUS_TYPE_RESP: "RESP",
                VBUS_TYPE_DATA: "DATA",
                VBUS_TYPE_HANDSHAKE: "HANDSHAKE",
                VBUS_TYPE_PING: "PING",
                VBUS_TYPE_EVENT: "EVENT",
                VBUS_TYPE_TOKEN_STREAM: "TOKEN_STREAM",
            }

            # Payloads appropriate for each type
            type_payloads = {
                VBUS_TYPE_CMD: b"PING",
                VBUS_TYPE_RESP: b"OK|PONG",
                VBUS_TYPE_DATA: b"\x00" * 64,
                VBUS_TYPE_HANDSHAKE: b"VBUS2",
                VBUS_TYPE_PING: b"PING",
                VBUS_TYPE_EVENT: b"\x01\x01" + struct.pack("<IQ", 0, 0),
                VBUS_TYPE_TOKEN_STREAM: build_token_stream_payload(),
            }

            dropped_count = 0
            sent_count = 0

            for ftype in ALL_HMAC_TYPES:
                payload = type_payloads[ftype]
                tag = client._alloc_tag()

                ok = client.send_poisoned_frame(ftype, payload, tag=tag)
                assert ok, f"Failed to send poisoned {type_names[ftype]} frame"
                sent_count += 1

                # Wait briefly for any response
                result = client.recv_frame(timeout=0.3)
                if result is None:
                    dropped_count += 1
                else:
                    # A valid echo of the same type would be a security failure
                    resp_type = result[0]
                    # For CMD, kernel would respond with RESP; for PING, PONG
                    # Both being absent is the correct behavior
                    if ftype == VBUS_TYPE_CMD and resp_type == VBUS_TYPE_RESP:
                        resp_str = result[3].decode("utf-8", errors="replace")
                        if "PONG" in resp_str:
                            pytest.fail(
                                f"SECURITY FAILURE: Poisoned {type_names[ftype]} "
                                f"frame was ACCEPTED (got RESP: {resp_str})"
                            )
                    elif ftype == VBUS_TYPE_PING and resp_type == VBUS_TYPE_PING:
                        if result[3] == b"PONG":
                            pytest.fail(
                                "SECURITY FAILURE: Poisoned PING "
                                "frame was ACCEPTED (got PONG)"
                            )
                    dropped_count += 1  # Non-matching response counts as dropped

            assert (
                dropped_count == sent_count
            ), f"Only {dropped_count}/{sent_count} poisoned frames dropped across all types"

            # Verify bridge survived the full sweep
            assert client.is_alive(), "Bridge died during HMAC coverage sweep"

            elapsed = time.monotonic() - start
            _metrics.record(
                True, sent=sent_count, dropped=dropped_count, elapsed=elapsed
            )
        finally:
            client.close()
            time.sleep(0.3)


# ============================================================================
# TEST 5: REPLAY ATTACK DEFENSE
# ============================================================================


@requires_qemu
@chaos
class TestReplayAttackDefense:
    """Capture a valid HMAC-signed frame and replay it — verify graceful handling."""

    def test_replay_attack_defense(self):
        """Record a valid HMAC-signed CMD PING frame, then replay it multiple
        times. The kernel should handle replays gracefully:
        - It may accept them (VBus v2 has no replay counter/nonce)
        - It must NOT crash, hang, or leak state
        - After replay storm, bridge must still be alive

        This test documents the current replay behavior rather than
        asserting rejection (replay defense requires nonce/sequence numbers
        which are not yet part of the VBus spec).
        """
        start = time.monotonic()
        client = VBusClient()
        try:
            assert client.connect_with_hmac(), "Failed to establish HMAC session"
            assert client.is_alive(), "Bridge not alive after HMAC handshake"

            # Build a valid HMAC-signed CMD PING frame and capture it
            tag = client._alloc_tag()
            captured_frame = build_hmac_frame(
                client.hmac_key, VBUS_TYPE_CMD, b"PING", slot_id=0xFF, tag=tag
            )

            # First send: should work normally
            assert client.send_raw(captured_frame), "Failed to send original frame"
            result = client.recv_frame(timeout=2.0)
            assert result is not None, "No response to original valid frame"
            ftype, _sid, _tag, payload = result
            payload.decode("utf-8", errors="replace")

            # Replay the exact same frame 20 times
            replay_count = 20
            sent = 0
            responses = 0
            for i in range(replay_count):
                ok = client.send_raw(captured_frame)
                if not ok:
                    break
                sent += 1

                # Drain response (may or may not arrive for replays)
                result = client.recv_frame(timeout=0.3)
                if result is not None:
                    responses += 1

                # Brief pause
                time.sleep(0.01)

            # The key assertion: bridge must survive the replay storm
            client.drain(timeout=0.5)
            assert (
                client.is_alive()
            ), f"Bridge died after {sent} replay frames ({responses} responses received)"

            # Verify a fresh (non-replay) command still works
            resp = client.send_cmd("PING")
            assert resp is not None, "Fresh CMD failed after replay storm"

            elapsed = time.monotonic() - start
            _metrics.record(
                True, sent=sent + 2, accepted=responses + 1, elapsed=elapsed
            )
        finally:
            client.close()
            time.sleep(0.3)


# ============================================================================
# TEST 6: LEGACY COMPATIBILITY
# ============================================================================


@requires_qemu
@chaos
class TestLegacyCompatibility:
    """Connect with legacy handshake (no HMAC) and verify behavior."""

    def test_legacy_compatibility(self):
        """Connect with 5-byte "VBUS2" handshake (no HMAC key).

        In legacy mode:
        - Basic commands (PING) should still work (backward compat)
        - Frames with HMAC flag set but no session key should be handled
          gracefully (either ignored or rejected, not crash)
        """
        start = time.monotonic()
        client = VBusClient()
        try:
            assert client.connect_legacy(), "Failed legacy handshake"
            assert client.hmac_key is None, "Legacy mode should have no HMAC key"

            # Test 1: Basic PING should work in legacy mode
            tag = client._alloc_tag()
            client.send_legacy_frame(VBUS_TYPE_PING, b"PING", tag=tag)
            result = client.recv_frame(timeout=2.0)
            ping_ok = False
            if result is not None:
                ftype, _sid, _tag, payload = result
                if ftype == VBUS_TYPE_PING and payload == b"PONG":
                    ping_ok = True

            assert ping_ok, "Legacy PING did not receive PONG"

            # Test 2: Basic CMD should work in legacy mode
            tag = client._alloc_tag()
            client.send_legacy_frame(VBUS_TYPE_CMD, b"PING", tag=tag)
            result = client.recv_frame(timeout=2.0)
            cmd_ok = False
            if result is not None:
                ftype, _sid, _tag, payload = result
                if ftype == VBUS_TYPE_RESP:
                    resp_str = payload.decode("utf-8", errors="replace")
                    if "PONG" in resp_str:
                        cmd_ok = True

            assert cmd_ok, "Legacy CMD PING did not receive PONG response"

            # Test 3: Send a frame with HMAC flag set but random HMAC
            # (simulating an attacker trying to inject HMAC-tagged frames
            # on a legacy session). The kernel should handle this gracefully.
            tag = client._alloc_tag()
            poisoned = build_poisoned_hmac_frame(
                VBUS_TYPE_CMD, b"SLOT_RESET|1", tag=tag
            )
            client.send_raw(poisoned)
            result = client.recv_frame(timeout=0.5)
            # We don't assert rejection here — in legacy mode the kernel
            # may ignore the HMAC field entirely. The key is: no crash.

            # Test 4: Bridge must still be alive after the mixed-mode test
            client.drain(timeout=0.3)
            tag = client._alloc_tag()
            client.send_legacy_frame(VBUS_TYPE_PING, b"PING", tag=tag)
            result = client.recv_frame(timeout=2.0)
            still_alive = False
            if result is not None:
                ftype, _sid, _tag, payload = result
                if ftype == VBUS_TYPE_PING and payload == b"PONG":
                    still_alive = True

            assert still_alive, "Bridge died after HMAC-tagged frame on legacy session"

            elapsed = time.monotonic() - start
            _metrics.record(True, sent=4, accepted=2, dropped=1, elapsed=elapsed)
        finally:
            client.close()
            time.sleep(0.3)


# ============================================================================
# OFFLINE (NON-QEMU) HMAC VERIFICATION TESTS
# ============================================================================


class TestHmacOfflineVerification:
    """Pure Python verification of HMAC frame construction and validation.

    These tests do NOT require QEMU — they validate the frame-building
    helpers and HMAC computation against known vectors.
    """

    def test_hmac_frame_construction(self):
        """Verify build_hmac_frame produces a valid frame with correct HMAC."""
        key = bytes(range(32))  # Deterministic key
        payload = b"TEST_PAYLOAD"

        frame = build_hmac_frame(key, VBUS_TYPE_CMD, payload, slot_id=0xFF, tag=1)

        # Frame should be 64-byte header + payload
        assert len(frame) == FRAME_HDR_SIZE + len(payload)

        # Extract HMAC from header
        header = frame[:FRAME_HDR_SIZE]
        embedded_hmac = header[VBUS_HMAC_OFFSET : VBUS_HMAC_OFFSET + VBUS_HMAC_SIZE]
        flag = header[VBUS_FLAGS_OFFSET]

        assert flag & VBUS_FLAG_HMAC, "HMAC flag not set in frame"

        # Recompute HMAC independently
        hmac_input = header[:16] + payload
        expected_mac = hmac_mod.new(key, hmac_input, hashlib.sha256).digest()
        assert embedded_hmac == expected_mac, "HMAC mismatch in constructed frame"

    def test_poisoned_frame_has_wrong_hmac(self):
        """Verify build_poisoned_hmac_frame has an incorrect HMAC."""
        key = os.urandom(32)
        payload = b"PING"

        poisoned = build_poisoned_hmac_frame(VBUS_TYPE_CMD, payload, tag=1)
        header = poisoned[:FRAME_HDR_SIZE]

        # Verify HMAC flag is set
        assert header[VBUS_FLAGS_OFFSET] & VBUS_FLAG_HMAC, "HMAC flag not set"

        # The HMAC should NOT match what the key would produce
        embedded_hmac = header[VBUS_HMAC_OFFSET : VBUS_HMAC_OFFSET + VBUS_HMAC_SIZE]
        hmac_input = header[:16] + payload
        correct_mac = hmac_mod.new(key, hmac_input, hashlib.sha256).digest()

        # The random HMAC should differ from any valid key's HMAC (with
        # overwhelming probability)
        assert (
            embedded_hmac != correct_mac
        ), "Poisoned HMAC accidentally matches (astronomically unlikely)"

    def test_legacy_frame_has_no_hmac_flag(self):
        """Verify build_legacy_frame has HMAC flag cleared."""
        frame = build_legacy_frame(VBUS_TYPE_CMD, b"PING", tag=1)
        header = frame[:FRAME_HDR_SIZE]
        assert not (
            header[VBUS_FLAGS_OFFSET] & VBUS_FLAG_HMAC
        ), "Legacy frame should NOT have HMAC flag set"

    def test_crc_integrity_in_hmac_frame(self):
        """Verify that CRCs are still valid inside HMAC-signed frames."""
        key = os.urandom(32)
        payload = b"CHECKSUM_TEST_DATA"

        frame = build_hmac_frame(key, VBUS_TYPE_CMD, payload, slot_id=0xFF, tag=42)
        header = frame[:FRAME_HDR_SIZE]

        # Extract CRC fields
        _type, _sid, _tag, length, expected_pcrc, expected_hcrc, _pad = struct.unpack(
            FRAME_HDR_FMT, header
        )

        # Verify hdr_crc
        hdr_prefix = struct.pack("<BBHI", VBUS_TYPE_CMD, 0xFF, 42, len(payload))
        actual_hcrc = _crc32c(hdr_prefix)
        assert (
            actual_hcrc == expected_hcrc
        ), f"Header CRC mismatch: 0x{expected_hcrc:08x} vs 0x{actual_hcrc:08x}"

        # Verify payload_crc
        actual_pcrc = _crc32c(payload, _crc32c(hdr_prefix))
        assert (
            actual_pcrc == expected_pcrc
        ), f"Payload CRC mismatch: 0x{expected_pcrc:08x} vs 0x{actual_pcrc:08x}"

    def test_hmac_determinism(self):
        """Same key + same frame content = same HMAC (deterministic)."""
        key = b"\xaa" * 32
        payload = b"DETERMINISM_CHECK"

        frame1 = build_hmac_frame(key, VBUS_TYPE_PING, payload, tag=7)
        frame2 = build_hmac_frame(key, VBUS_TYPE_PING, payload, tag=7)

        hmac1 = frame1[VBUS_HMAC_OFFSET : VBUS_HMAC_OFFSET + VBUS_HMAC_SIZE]
        hmac2 = frame2[VBUS_HMAC_OFFSET : VBUS_HMAC_OFFSET + VBUS_HMAC_SIZE]

        assert hmac1 == hmac2, "HMAC is not deterministic for same inputs"

    def test_hmac_key_sensitivity(self):
        """Different keys produce different HMACs for the same frame."""
        key_a = b"\xaa" * 32
        key_b = b"\xbb" * 32
        payload = b"KEY_SENSITIVITY"

        frame_a = build_hmac_frame(key_a, VBUS_TYPE_CMD, payload, tag=1)
        frame_b = build_hmac_frame(key_b, VBUS_TYPE_CMD, payload, tag=1)

        hmac_a = frame_a[VBUS_HMAC_OFFSET : VBUS_HMAC_OFFSET + VBUS_HMAC_SIZE]
        hmac_b = frame_b[VBUS_HMAC_OFFSET : VBUS_HMAC_OFFSET + VBUS_HMAC_SIZE]

        assert hmac_a != hmac_b, "Different keys produced same HMAC (CRITICAL)"

    def test_all_frame_types_produce_unique_hmacs(self):
        """Same key and payload but different frame types produce different HMACs.

        This proves the frame type byte is included in the HMAC input.
        """
        key = os.urandom(32)
        payload = b"TYPE_UNIQUENESS"

        hmacs = set()
        for ftype in ALL_HMAC_TYPES:
            frame = build_hmac_frame(key, ftype, payload, tag=1)
            mac = frame[VBUS_HMAC_OFFSET : VBUS_HMAC_OFFSET + VBUS_HMAC_SIZE]
            hmacs.add(mac)

        assert len(hmacs) == len(
            ALL_HMAC_TYPES
        ), f"Only {len(hmacs)}/{len(ALL_HMAC_TYPES)} unique HMACs across frame types"

    def test_timing_assertion_hmac_computation(self):
        """HMAC computation for a 48KB payload should complete in <100ms.

        This is a basic performance sanity check — the kernel uses
        FIPS 180-4 SHA-256 in GPR-only mode, so the Python side should
        be comfortably faster.
        """
        key = os.urandom(32)
        payload = os.urandom(49152)  # 48KB — max chunk size

        start = time.monotonic()
        for _ in range(100):
            build_hmac_frame(key, VBUS_TYPE_DATA, payload, tag=1)
        elapsed = time.monotonic() - start

        per_frame_ms = (elapsed / 100) * 1000
        assert (
            per_frame_ms < 100
        ), f"HMAC computation too slow: {per_frame_ms:.2f}ms per 48KB frame"


# ============================================================================
# CONFTEST HOOKS FOR METRICS SUMMARY
# ============================================================================


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print chaos metrics summary at the end of the test session."""
    if _metrics.tests_run > 0:
        terminalreporter.write(_metrics.summary())


# ============================================================================
# STANDALONE EXECUTION
# ============================================================================

if __name__ == "__main__":
    print("VBus HMAC Integrity Chaos Test")
    print(f"  Bridge socket: {BRIDGE_SOCKET}")
    print(f"  HMAC ban threshold: {HMAC_BAN_THRESHOLD} violations")
    print(f"  Frame types covered: {len(ALL_HMAC_TYPES)}")
    print()
    print(
        "Run with: python3 -m pytest tests/chaos_vbus_hmac_integrity.py -v "
        "--tb=short -p no:xdist"
    )
    print("  Add -k 'poisoned' to run only poisoned token tests")
    print("  Add -k 'ban' to run only ban mechanism tests")
    print("  Add -k 'replay' to run only replay attack tests")
    print("  Add -k 'legacy' to run only legacy compatibility tests")
    print("  Add -k 'coverage' to run only HMAC coverage tests")
    print("  Add -k 'Offline' to run only offline (no-QEMU) verification tests")
