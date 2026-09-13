"""
VBus Binary Frame Driver for VOS3 Phase 4.2 (Tagged-Frame v2).

Binary frames over Unix socket connected to QEMU virtio-serial-pci device.

Frame format (16-byte header + payload, AVX-512 aligned):
  [u8:type][u8:slot_id][u16:tag LE][u32:len LE][u32:payload_crc LE][u32:hdr_crc LE][payload...]

payload_crc: CRC32C over type+slot_id+tag+len+payload (full integrity)
hdr_crc:     CRC32C over type+slot_id+tag+len (8 bytes only — Length-Corruption defense)

Phase 4.2.18: Switched from IEEE 802.3 CRC32 to CRC32C (Castagnoli) for
hardware SSE4.2 acceleration in the kernel.
"""

import asyncio
import hashlib
import hmac as hmac_mod
import os
import select
import socket
import struct
import logging
from typing import Optional, Tuple

# Phase 4.2.18: CRC32C (Castagnoli) — try hardware-accelerated package first
# Phase 4.3.1: Added `value` param for incremental CRC (avoids 2MB concatenation)
try:
    from crc32c import crc32c as _crc32c_ext

    def _crc32c(data: bytes, value: int = 0) -> int:
        return _crc32c_ext(data, value) & 0xFFFFFFFF

except ImportError:
    # Pure Python CRC32C fallback (Castagnoli polynomial 0x82F63B78)
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


logger = logging.getLogger("vbus_driver")

# Frame types — must match kernel/include/vos/virtio_vbus.h
VBUS_TYPE_CMD = 0x01
VBUS_TYPE_RESP = 0x02
VBUS_TYPE_DATA = 0x03
VBUS_TYPE_HANDSHAKE = 0x04
VBUS_TYPE_PING = 0x05
VBUS_TYPE_EVENT = 0x06
VBUS_TYPE_TOKEN_STREAM = 0x07  # Phase 6: KIM inference token frames
VBUS_TYPE_FEEDBACK = 0x08
VBUS_TYPE_BATCH = 0x09
VBUS_TYPE_NOTIFY = 0x0A
VBUS_TYPE_INTERRUPT = 0x0B
VBUS_TYPE_SUBSCRIBE = 0x0C
VBUS_TYPE_V_AAAK = 0x0D  # Phase 8: Token-Aware Shorthand frame

# Phase 4.2.19: 64-byte header for 512-bit payload alignment
# type(u8)+slot_id(u8)+tag(u16)+len(u32)+payload_crc(u32)+hdr_crc(u32)+padding(48)
FRAME_HDR_FMT = "<BBHIII48s"
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)  # 64 bytes

VBUS_MAX_PAYLOAD = 2097152  # Phase 4.3: 2MB Jumbo MTU
VBUS_TIMEOUT_S = 10.0

# Tag constants
VBUS_TAG_ASYNC = 0xFFFF  # Unsolicited EVENT/FEEDBACK/NOTIFY
VBUS_TAG_STREAM = 0x0000  # DATA frames (no response expected)

# Phase 8: V-AAAK Token Dictionary (must match kernel g_v_aaak_dict)
V_AAAK_ESCAPE = 0xFF
V_AAAK_DICT = [
    " the",
    " a",
    " is",
    " of",
    " and",
    " to",
    " in",
    " it",
    " that",
    " for",
    " was",
    " on",
    " are",
    " with",
    " as",
    " this",
    " be",
    " at",
    " have",
    " from",
    " or",
    " by",
    " not",
    " but",
    " what",
    " all",
    " were",
    " when",
    " we",
    " there",
    " can",
    " an",
    " your",
    " which",
    " their",
    " if",
    " do",
    " will",
    " each",
    " how",
    " them",
    " then",
    " he",
    " she",
    " my",
    " no",
    " more",
    " so",
    "the",
    "and",
    "ing",
    "tion",
    "ed ",
    "er ",
    "es ",
    "re ",
    "\n",
    "  ",
    ", ",
    ". ",
    ": ",
    ";\n",
    "}\n",
    "{\n",
]


# Pre-compute encoded token bytes (avoid .encode() per comparison).
_V_AAAK_DICT_BYTES: list[bytes] = [t.encode("utf-8") for t in V_AAAK_DICT]
_V_AAAK_DICT_LENS: list[int] = [len(b) for b in _V_AAAK_DICT_BYTES]

# Group tokens by first byte, sorted longest-first within each group.
# Key = first byte value, Value = list of (token_bytes, dict_index, token_len)
# sorted by token_len descending so we can break on first match (greedy longest).
_V_AAAK_BY_FIRST: dict[int, list[tuple[bytes, int, int]]] = {}
for _i, _tb in enumerate(_V_AAAK_DICT_BYTES):
    _fb = _tb[0]
    if _fb not in _V_AAAK_BY_FIRST:
        _V_AAAK_BY_FIRST[_fb] = []
    _V_AAAK_BY_FIRST[_fb].append((_tb, _i, len(_tb)))
for _fb in _V_AAAK_BY_FIRST:
    _V_AAAK_BY_FIRST[_fb].sort(key=lambda x: x[2], reverse=True)

# Maximum token length (for slice window).
_V_AAAK_MAX_TLEN: int = max(_V_AAAK_DICT_LENS)


def v_aaak_encode(data: bytes, salt: Optional[bytes] = None) -> bytes:
    """Encode data using V-AAAK token dictionary (Python-side, matches kernel).

    Optimized: first-byte dispatch table eliminates ~90% of comparisons per
    position. Within each bucket, tokens are sorted longest-first so the first
    match found is always the greedy longest match.

    Phase 6.6-U: If ``salt`` is provided (16 bytes), each dictionary code byte
    is XOR'd with ``salt[code % 16]`` before emission. The kernel applies the
    same XOR in reverse during decode, making the wire encoding session-specific.
    """
    by_first = _V_AAAK_BY_FIRST
    max_tlen = _V_AAAK_MAX_TLEN
    escape = V_AAAK_ESCAPE
    salted = salt is not None and len(salt) >= 16
    result = bytearray()
    n = len(data)
    i = 0
    while i < n:
        b = data[i]
        candidates = by_first.get(b)
        if candidates is not None:
            best_idx = -1
            best_len = 0
            window = data[i : i + max_tlen]
            for tbytes, tidx, tlen in candidates:
                if tlen > len(window):
                    continue
                if window[:tlen] == tbytes:
                    best_idx = tidx
                    best_len = tlen
                    break  # longest-first: first match IS the best
            if best_idx >= 0:
                result.append(escape)
                code = best_idx
                if salted:
                    # Salt index = position of code byte in encoded stream
                    # Modular addition keeps codes in [0,63], avoids 0xFF escape collision
                    code = (code + salt[len(result) % 16]) % 64
                result.append(code)
                i += best_len
                continue
        if b == escape:
            result.append(escape)
            result.append(escape)
        else:
            result.append(b)
        i += 1
    return bytes(result)


def v_aaak_decode(data: bytes, salt: Optional[bytes] = None) -> bytes:
    """Decode V-AAAK compressed data (Python-side, matches kernel).

    Phase 6.6-U: If ``salt`` is provided (16 bytes), each dictionary code byte
    is de-salted via modular subtraction ``(code + 64 - salt[pos % 16]) % 64``
    before dictionary lookup. Keeps codes in [0,63], avoids 0xFF escape collision.
    """
    dict_bytes = _V_AAAK_DICT_BYTES
    dict_len = len(dict_bytes)
    escape = V_AAAK_ESCAPE
    salted = salt is not None and len(salt) >= 16
    result = bytearray()
    n = len(data)
    i = 0
    while i < n:
        if data[i] == escape:
            i += 1
            if i >= n:
                raise ValueError("Truncated V-AAAK data")
            code = data[i]
            if code == escape:
                result.append(escape)
            else:
                if salted:
                    # Salt index = position of code byte in encoded stream (i)
                    # Modular subtraction reverses the addition in encode
                    code = (code + 64 - salt[i % 16]) % 64
                if code >= dict_len:
                    raise ValueError(f"Invalid V-AAAK code: {code}")
                result.extend(dict_bytes[code])
            i += 1
        else:
            result.append(data[i])
            i += 1
    return bytes(result)


class VBusError(Exception):
    """VBus transport error."""

    pass


class VBusSecurityError(VBusError):
    """VBus security violation — HMAC negotiation failed."""

    pass


class VBusDriver:
    """Binary frame driver for VOS3 VBus transport (v2 tagged-frame).

    Drop-in replacement for the serial text protocol. Commands are still
    text strings (e.g. "PING", "WRITE|/path|hex"), but they travel inside
    binary frames with dual-CRC32 integrity and tag-based correlation.
    """

    # Phase Omega: Local model registry cache — decouples kernel from Convex.
    # Populated once at startup or on-demand; keyed by model_id.
    _model_registry_cache: dict = {}

    def __init__(self, socket_path: str = "/tmp/vos3_bridge.sock"):
        self.socket_path = socket_path
        self._sock: Optional[socket.socket] = None
        self._event_queue = []
        self._event_callback = None
        self._feedback_callback = None
        self._last_feedback = None
        self._token_queue: dict[int, list] = (
            {}
        )  # Phase 6: per-slot KIM token stream buffer
        self._token_callback = None  # Phase 6: per-token callback
        self._next_tag = 1  # Monotonic u16, skip 0x0000 and 0xFFFF
        self._slot_seq = {}  # Phase 4.8-H: per-slot DATA frame sequence counters
        self._hmac_key: Optional[bytes] = None  # Phase 5: HMAC-SHA256 key
        self._aaak_salt: Optional[bytes] = None  # Phase 6.6-U: session AAAK salt
        # Phase 6.4 Forensic: Breach tracking (instance-level, not class-level)
        self._breach_count = {}
        self._breach_total = 0
        # B-HIGH-7 fix: Lazy-init lock — asyncio.Lock() requires a running
        # event loop in Python 3.12+, but __init__ is synchronous.
        self._token_chain_lock = None  # Created on first async access

    def _alloc_tag(self) -> int:
        """Allocate next correlation tag (skip 0x0000=STREAM, 0xFFFF=ASYNC)."""
        tag = self._next_tag
        self._next_tag = (self._next_tag + 1) & 0xFFFF
        if self._next_tag == 0x0000:
            self._next_tag = 1
        if self._next_tag == 0xFFFF:
            self._next_tag = 1
        return tag

    def _alloc_slot_seq(self, slot_id: int) -> int:
        """Per-slot DATA frame sequence tag (1..0xFFFE, wraps, skips 0)."""
        seq = self._slot_seq.get(slot_id, 0) + 1
        if seq > 0xFFFE:
            seq = 1
        self._slot_seq[slot_id] = seq
        return seq

    # Phase Omega-Ghost: Fixed metadata width defeats L3 side-channel
    # attacks that infer model type from cache-line access patterns on
    # variable-length label strings.
    _LABEL_PAD_WIDTH = 64

    @classmethod
    def _pad_label(cls, label: str) -> str:
        """Pad label to fixed 64-byte width with null bytes to defeat
        L3 side-channel probing attacks based on metadata string length.

        Phase v17: Truncate at 63 bytes (reserving 1 null terminator),
        then null-pad to exactly 64 bytes — one full cache line.
        """
        encoded = label.encode("utf-8")[:63]
        padded = encoded.ljust(cls._LABEL_PAD_WIDTH, b"\x00")
        return padded.decode("utf-8", errors="replace")

    @classmethod
    def populate_model_cache(cls, models: list) -> None:
        """Populate the local model registry cache.

        Called once at startup or on-demand. Each entry is a dict with keys:
        model_id (int), format (str), size (int), label (str).
        The kernel never needs to know about external databases — metadata
        flows through VBus commands populated from this cache.

        Phase Omega-Ghost: All labels are padded to exactly 64 bytes to
        prevent L3 side-channel attacks that guess model types based on
        metadata string length (cache-line occupancy analysis).
        """
        for m in models:
            mid = m.get("model_id")
            if mid is not None:
                cls._model_registry_cache[mid] = {
                    "format": m.get("format", "unknown"),
                    "size": m.get("size", 0),
                    "label": cls._pad_label(m.get("label", "")),
                }

    @classmethod
    def get_cached_model(cls, model_id: int) -> Optional[dict]:
        """Lookup model metadata from local cache. Returns None if not cached."""
        entry = cls._model_registry_cache.get(model_id)
        if entry is None:
            return None
        return dict(entry)

    def connect(self) -> bool:
        """Connect to VBus socket and perform handshake.

        Returns True if connected and handshake succeeded, False otherwise.
        """
        try:
            if self._sock is not None:
                return True
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(VBUS_TIMEOUT_S)
            sock.connect(self.socket_path)
            self._sock = sock

            # Drain stale frames (kernel heartbeat PINGs) before handshake
            sock.setblocking(False)
            try:
                while True:
                    stale = sock.recv(4096)
                    if not stale:
                        break
            except BlockingIOError:
                pass  # No more data — buffer is clean
            sock.setblocking(True)
            sock.settimeout(VBUS_TIMEOUT_S)

            # Phase 6.6-U: Handshake v3.1+HMAC: send "VBUS3" + 32-byte key.
            # Kernel responds with salt-bearing response and 16-byte salt suffix.
            # The kernel may send unsolicited EVENT/PING frames upon connection
            # that race with the HANDSHAKE response. Skip up to 5 non-HANDSHAKE
            # frames before giving up.
            hmac_key = os.urandom(32)
            self._send_frame(
                VBUS_TYPE_HANDSHAKE, b"VBUS3" + hmac_key, slot_id=0xFF, tag=0
            )
            for _attempt in range(5):
                ftype, _sid, _tag, payload = self._recv_frame()
                if ftype == VBUS_TYPE_HANDSHAKE:
                    resp_str = payload.decode("utf-8", errors="replace")
                    if "HMAC" in resp_str:
                        self._hmac_key = hmac_key
                        # Phase 6.6-U: Extract 16-byte salt from response suffix.
                        # v3.1 response: "VOS3-VBUS31-AAAK-HMAC-SALT\x00" + 16 bytes
                        # Salt starts at byte offset 27 (after NUL terminator).
                        if b"SALT" in payload and len(payload) >= 43:
                            self._aaak_salt = bytes(payload[27:43])
                            logger.info(
                                "VBus v3.1 handshake OK (HMAC+SALT): %s " "(salt=%s)",
                                resp_str[:27],
                                self._aaak_salt[:4].hex(),
                            )
                        else:
                            logger.info(
                                "VBus v3 handshake OK (HMAC, no salt): %s", resp_str
                            )
                        return True
                    elif b"SALT" in payload:
                        # Unauthenticated v3.1 with salt — extract salt
                        if len(payload) >= 40:
                            self._aaak_salt = bytes(payload[24:40])
                            logger.info(
                                "VBus v3.1 handshake OK (SALT, no HMAC): " "salt=%s",
                                self._aaak_salt[:4].hex(),
                            )
                        return True
                    else:
                        # HMAC mandatory — reject non-HMAC kernel connections
                        self.disconnect()
                        raise VBusSecurityError(
                            f"HMAC negotiation failed: kernel responded '{resp_str}' "
                            f"without HMAC — unauthenticated connections are prohibited"
                        )
                # Skip unsolicited frames (EVENT, PING, etc.) during handshake
                logger.debug("Skipping frame type 0x%02x during handshake", ftype)

            # If no HANDSHAKE response after 5 frames, handshake failed
            self.disconnect()
            raise VBusSecurityError(
                "HMAC negotiation failed: no HANDSHAKE response after 5 frames"
            )
        except VBusSecurityError:
            raise  # Never swallow security errors
        except (OSError, VBusError) as e:
            logger.debug("VBus connect failed: %s", e)
            self.disconnect()
            return False

    def disconnect(self) -> None:
        """Close the VBus connection."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._aaak_salt = None  # Phase 6.6-U: clear session salt on disconnect

    def _drain_pending_frames(self) -> None:
        """Non-blocking drain of pending EVENT/FEEDBACK/NOTIFY frames from RX socket.

        Uses MSG_PEEK to inspect the 16-byte frame header without consuming.
        Only consumes complete async frames whose full payload is already buffered.
        All other frame types are left untouched.
        """
        if self._sock is None:
            return
        while True:
            ready, _, _ = select.select([self._sock], [], [], 0)
            if not ready:
                break
            try:
                peeked = self._sock.recv(FRAME_HDR_SIZE, socket.MSG_PEEK)
                if len(peeked) < FRAME_HDR_SIZE:
                    break

                frame_type, slot_id, tag, length, _, _, _pad = struct.unpack(
                    FRAME_HDR_FMT, peeked
                )
                if frame_type not in (
                    VBUS_TYPE_EVENT,
                    VBUS_TYPE_FEEDBACK,
                    VBUS_TYPE_NOTIFY,
                ):
                    break
                if length > VBUS_MAX_PAYLOAD:
                    break

                total = FRAME_HDR_SIZE + length
                full_peek = self._sock.recv(total, socket.MSG_PEEK)
                if len(full_peek) < total:
                    break

                # Full frame confirmed present — consume
                header = self._recv_exact(FRAME_HDR_SIZE)
                ft, sid, tg, ln, expected_pcrc, expected_hcrc, _pad2 = struct.unpack(
                    FRAME_HDR_FMT, header
                )
                payload = self._recv_exact(ln) if ln > 0 else b""

                # Validate hdr_crc first
                hdr_prefix = struct.pack("<BBHI", ft, sid, tg, ln)
                actual_hcrc = _crc32c(hdr_prefix)
                if actual_hcrc != expected_hcrc:
                    continue

                # Validate payload_crc
                actual_pcrc = _crc32c(payload, _crc32c(hdr_prefix))
                if actual_pcrc != expected_pcrc:
                    continue

                if ft == VBUS_TYPE_EVENT and len(payload) >= 14:
                    event = self._parse_event(payload)
                    self._event_queue.append(event)
                    logger.debug(
                        "_drain: consumed EVENT code=%d", event.get("event_code", -1)
                    )
                    if self._event_callback:
                        self._event_callback(event)
                elif ft == VBUS_TYPE_FEEDBACK and len(payload) >= 27:
                    feedback = self._parse_feedback(payload)
                    self._last_feedback = feedback
                    if self._feedback_callback:
                        self._feedback_callback(feedback)
                elif ft == VBUS_TYPE_NOTIFY:
                    event = (
                        self._parse_event(payload)
                        if len(payload) >= 14
                        else {"raw": payload}
                    )
                    self._event_queue.append(event)
                    if self._event_callback:
                        self._event_callback(event)
            except (OSError, VBusError):
                break

    def send_command(self, command: str, slot_id: int = 0xFF) -> str:
        """Send a text command over VBus and return the response string.

        Uses tag-based correlation to match CMD→RESP, transparently skipping
        any interleaved async frames (EVENTs, FEEDBACK, NOTIFY).

        Returns:
            Response string (e.g. "OK|PONG" or "ERR|22|EINVAL: missing path")

        Raises:
            VBusError: On transport or CRC errors.
        """
        if self._sock is None:
            raise VBusError("Not connected")

        payload = command.encode("utf-8")
        if len(payload) > VBUS_MAX_PAYLOAD:
            raise VBusError(f"Command too large: {len(payload)} > {VBUS_MAX_PAYLOAD}")

        tag = self._alloc_tag()
        logger.debug("CMD_SEND [tag=0x%04x]: %s", tag, command[:60])
        self._send_frame(VBUS_TYPE_CMD, payload, slot_id=slot_id, tag=tag)

        # Wait for RESP with matching tag �� async frames are handled transparently
        ftype, _sid, rtag, resp_payload = self._recv_frame()

        if ftype != VBUS_TYPE_RESP:
            raise VBusError(f"Expected RESP frame (0x02), got 0x{ftype:02x}")
        if rtag != tag:
            raise VBusError(f"Tag mismatch: sent 0x{tag:04x}, received 0x{rtag:04x}")

        resp_str = resp_payload.decode("utf-8", errors="replace")
        logger.debug(
            "CMD_RESP [tag=0x%04x]: %s → %s", rtag, command.split("|")[0], resp_str[:60]
        )
        return resp_str

    def ping(self) -> float:
        """Send a PING frame and measure round-trip time in milliseconds."""
        import time

        if self._sock is None:
            raise VBusError("Not connected")

        tag = self._alloc_tag()
        start = time.monotonic()
        self._send_frame(VBUS_TYPE_PING, b"PING", tag=tag)
        ftype, _sid, _tag, _ = self._recv_frame()
        elapsed = (time.monotonic() - start) * 1000.0

        if ftype != VBUS_TYPE_PING:
            raise VBusError(f"Expected PING response, got 0x{ftype:02x}")
        return elapsed

    # ---- Phase 4.2: AI Model Weight Loading ----

    def load_model_weights(
        self,
        file_path: str,
        model_id: int = 1,
        chunk_size: int = 2097152,
        window: int = 64,
        max_rewinds: int = 3,
    ) -> dict:
        """Load AI model weights via zero-copy binary streaming.

        Sends MODEL_START, streams raw binary DATA frames, periodically
        syncs with MODEL_SYNC, uses Smart-Retry MODEL_REWIND on mismatch,
        then MODEL_DONE to finalize.

        Args:
            file_path: Path to model weights file.
            model_id: MCP model identifier.
            chunk_size: Bytes per DATA frame (default 8192).
            window: Sync every N chunks (default 64).
            max_rewinds: Maximum rewind retries before abort.

        Returns:
            dict with keys: addr, size, checksum, chunks, bytes, rewinds

        Raises:
            VBusError: On transport or protocol errors.
        """
        import os
        import time

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            raise VBusError("Empty model file")

        # Step 1: MODEL_START
        resp = self.send_command(f"MODEL_START|{model_id}|{file_size}")
        if not resp.startswith("OK|"):
            raise VBusError(f"MODEL_START failed: {resp}")
        resp.split("|")[1]

        start_time = time.monotonic()
        chunks_sent = 0
        bytes_sent = 0
        rewind_count = 0

        # Step 2: Stream raw binary DATA frames (tag=STREAM for fire-and-forget)
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self._send_frame(VBUS_TYPE_DATA, chunk, tag=VBUS_TAG_STREAM)
                chunks_sent += 1
                bytes_sent += len(chunk)

                # Periodic sync check
                if chunks_sent % window == 0:
                    resp = self.send_command("MODEL_SYNC")
                    if not resp.startswith("OK|"):
                        raise VBusError(f"MODEL_SYNC failed: {resp}")
                    kernel_bytes = int(resp.split("|")[1])
                    if kernel_bytes != bytes_sent:
                        # Smart-Retry: rewind to kernel's last known good offset
                        if rewind_count >= max_rewinds:
                            raise VBusError(
                                f"Max rewinds exceeded: sent={bytes_sent} "
                                f"kernel={kernel_bytes}"
                            )
                        resp = self.send_command(f"MODEL_REWIND|{kernel_bytes}")
                        if not resp.startswith("OK|"):
                            raise VBusError(f"MODEL_REWIND failed: {resp}")
                        rewound_to = int(resp.split("|")[1])
                        f.seek(rewound_to)
                        bytes_sent = rewound_to
                        chunks_sent = rewound_to // chunk_size
                        rewind_count += 1
                        logger.warning(
                            "Model rewind #%d to offset %d", rewind_count, rewound_to
                        )

        # Final sync if needed
        if chunks_sent % window != 0:
            self.send_command("MODEL_SYNC")

        # Step 3: MODEL_DONE
        resp = self.send_command("MODEL_DONE")
        if not resp.startswith("OK|"):
            raise VBusError(f"MODEL_DONE failed: {resp}")

        elapsed = time.monotonic() - start_time
        throughput = (bytes_sent / (1024 * 1024)) / elapsed if elapsed > 0 else 0

        parts = resp.split("|")
        result = {
            "addr": parts[1],
            "size": int(parts[2]),
            "checksum": parts[3],
            "chunks": chunks_sent,
            "bytes": bytes_sent,
            "rewinds": rewind_count,
            "elapsed_s": round(elapsed, 3),
            "throughput_mbps": round(throughput, 2),
        }
        logger.info(
            "Model loaded: %d bytes in %.3fs (%.2f MB/s), %d rewinds",
            bytes_sent,
            elapsed,
            throughput,
            rewind_count,
        )
        return result

    # ---- Phase 4.2.5: Event Handling ----

    def set_event_callback(self, fn):
        """Register callback for async events: fn(event_dict)."""
        self._event_callback = fn

    def collect_events(self, timeout: float = 0.5) -> list:
        """Drain all pending events, waiting up to timeout seconds for new ones.

        Handles EVENT, NOTIFY, and FEEDBACK frame types. All async frames
        carry tag=0xFFFF (VBUS_TAG_ASYNC).
        """
        import time

        events = list(self._event_queue)
        self._event_queue.clear()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._sock is None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([self._sock], [], [], min(remaining, 0.05))
            if not ready:
                continue
            try:
                header = self._recv_exact(FRAME_HDR_SIZE)
                frame_type, slot_id, tag, length, expected_pcrc, expected_hcrc, _pad = (
                    struct.unpack(FRAME_HDR_FMT, header)
                )
                payload = self._recv_exact(length) if length > 0 else b""

                # Validate hdr_crc
                hdr_prefix = struct.pack("<BBHI", frame_type, slot_id, tag, length)
                actual_hcrc = _crc32c(hdr_prefix)
                if actual_hcrc != expected_hcrc:
                    continue
                # Validate payload_crc
                actual_pcrc = _crc32c(payload, _crc32c(hdr_prefix))
                if actual_pcrc != expected_pcrc:
                    continue

                if (
                    frame_type in (VBUS_TYPE_EVENT, VBUS_TYPE_NOTIFY)
                    and len(payload) >= 14
                ):
                    event = self._parse_event(payload)
                    events.append(event)
                    if self._event_callback:
                        self._event_callback(event)
                elif frame_type == VBUS_TYPE_FEEDBACK and len(payload) >= 27:
                    feedback = self._parse_feedback(payload)
                    self._last_feedback = feedback
                    if self._feedback_callback:
                        self._feedback_callback(feedback)
            except (OSError, VBusError):
                break
        return events

    def _parse_event(self, payload: bytes) -> dict:
        """Parse 14-byte event payload."""
        slot_id = payload[0]
        event_code = payload[1]
        value = struct.unpack_from("<I", payload, 2)[0]
        timestamp = struct.unpack_from("<Q", payload, 6)[0]
        return {
            "slot_id": slot_id,
            "event_code": event_code,
            "value": value,
            "timestamp": timestamp,
        }

    # ---- Phase 6: KIM Token Stream ----

    # Phase 6.4.1-U: Per-slot running chain MAC for provenance verification.
    # chain_mac[n] = HMAC(session_key, payload || chain_mac[n-1]).
    # If a ghost token is injected, all subsequent chain MACs diverge.
    _token_chain_mac: dict[int, bytes] = {}
    _token_chain_lock: Optional[asyncio.Lock]  # lazy-init on first async access

    # Phase 6.4.2-H: Per-slot last-seen timestamp for monotonicity enforcement.
    _token_last_ts: dict[int, int] = {}

    # Phase 6.4.2-H: Staleness bound — reject tokens older than 500ms.
    _TOKEN_TS_STALE_MS = 500

    # Phase 6.4 Forensic: STREAM_PROVENANCE_BREACH tracking.
    # Consecutive breach count per slot. Resets on valid frame.
    _breach_count: dict[int, int] = {}
    _breach_total: int = 0  # Lifetime breach counter across all slots.
    _BREACH_KILL_THRESHOLD = 5  # Consecutive breaches before session kill.

    def _verify_token_chain(
        self, slot_id: int, core_payload: bytes, received_mac: bytes
    ) -> bool:
        """Verify chained-HMAC token provenance.

        Returns True if chain MAC matches (or HMAC not enabled).
        Updates running chain state for this slot on success.
        """
        if not self._hmac_key:
            return True  # No HMAC session — skip chain verification
        if len(received_mac) != 32:
            return False

        import hmac as _hmac
        import hashlib

        prev_mac = self._token_chain_mac.get(slot_id, b"\x00" * 32)
        expected = _hmac.new(
            self._hmac_key, core_payload + prev_mac, hashlib.sha256
        ).digest()

        if _hmac.compare_digest(expected, received_mac):
            self._token_chain_mac[slot_id] = expected
            return True
        return False

    def _parse_token_frame(self, payload: bytes) -> dict:
        """Parse TOKEN_STREAM payload (v6.4.2-H):
        [u8:slot][u32:token_id LE][u16:seq LE][u8:flags][u32:timestamp_ms LE]
        [text NUL][chain_mac[32]]

        Phase 6.4: If flags bit 0x80 (AAAK) is set, token text is V-AAAK compressed.
        Phase 6.4.1-U: 32-byte chained-HMAC provenance MAC appended after NUL.
        Phase 6.4.2-H: 32-bit timestamp at offset 8. Text starts at offset 12.
            Backend rejects frames > 500ms stale or out of monotonic order.
        """
        if len(payload) < 13:
            return {
                "slot_id": 0,
                "token_id": 0,
                "seq": 0,
                "flags": 0,
                "text": "",
                "is_first": False,
                "is_last": False,
                "is_error": True,
                "is_aaak": False,
                "is_batched": False,
                "chain_valid": False,
                "ts_valid": False,
                "timestamp_ms": 0,
            }

        slot_id = payload[0]
        token_id = struct.unpack_from("<I", payload, 1)[0]
        seq = struct.unpack_from("<H", payload, 5)[0]
        flags = payload[7]

        # Phase 6.4.2-H: 32-bit LE timestamp at offset 8-11 (ms since boot)
        timestamp_ms = struct.unpack_from("<I", payload, 8)[0]

        # Text region starts at offset 12 (after timestamp)
        text_region = payload[12:]
        nul_pos = text_region.find(b"\x00")
        if nul_pos < 0:
            nul_pos = len(text_region)
        raw_text = text_region[:nul_pos]

        # Phase 6.4.2-H: Time-lock validation.
        # 1. Monotonicity: timestamp must be >= last-seen for this slot.
        # 2. Staleness: reject frames older than 500ms relative to latest seen.
        ts_valid = True
        last_ts = self._token_last_ts.get(slot_id, 0)
        if timestamp_ms < last_ts:
            ts_valid = False  # Non-monotonic — possible replay/injection
        elif last_ts > 0 and (timestamp_ms - last_ts) > self._TOKEN_TS_STALE_MS:
            ts_valid = False  # Stale — too far ahead relative to stream
        if ts_valid:
            self._token_last_ts[slot_id] = timestamp_ms

        # Chain MAC is 32 bytes after the NUL terminator
        chain_mac_offset = nul_pos + 1
        chain_mac = b""
        chain_valid = False
        if chain_mac_offset + 32 <= len(text_region):
            chain_mac = text_region[chain_mac_offset : chain_mac_offset + 32]
            # core_payload includes header(8) + timestamp(4) + text + NUL
            core_payload = payload[: 12 + nul_pos + 1]
            chain_valid = self._verify_token_chain(slot_id, core_payload, chain_mac)

        # Phase 6.4: Transparent V-AAAK decompression
        is_aaak = bool(flags & 0x80)
        if is_aaak and raw_text:
            decoded = v_aaak_decode(raw_text)
            text = decoded.decode("utf-8", errors="replace")
        else:
            text = raw_text.decode("utf-8", errors="replace")

        return {
            "slot_id": slot_id,
            "token_id": token_id,
            "seq": seq,
            "flags": flags & 0x3F,  # Strip AAAK (0x80) + BATCHED (0x40)
            "text": text,
            "is_first": bool(flags & 0x01),
            "is_last": bool(flags & 0x02),
            "is_error": bool(flags & 0x04),
            "is_aaak": is_aaak,
            "is_batched": bool(flags & 0x40),
            "chain_valid": chain_valid,
            "ts_valid": ts_valid,
            "timestamp_ms": timestamp_ms,
        }

    def set_token_callback(self, fn):
        """Register callback for incoming TOKEN_STREAM frames: fn(token_dict)."""
        self._token_callback = fn

    def drain_tokens(self, slot_id: int | None = None) -> list:
        """Return and clear buffered tokens, optionally filtered by slot_id."""
        if slot_id is not None:
            q = self._token_queue.get(slot_id, [])
            out = list(q)
            if slot_id in self._token_queue:
                self._token_queue[slot_id] = []
            return out
        # Fallback: drain all slots
        out = []
        for sid in list(self._token_queue.keys()):
            out.extend(self._token_queue[sid])
            self._token_queue[sid] = []
        return out

    def kim_generate(
        self, slot_id: int, max_tokens: int = 256, temperature: int = 100
    ) -> str:
        """Send KIM_GENERATE command to kernel. Returns OK|tokens_generated.

        Args:
            slot_id: Model slot (0-7)
            max_tokens: Maximum tokens to generate (1-4096)
            temperature: Temperature in fixed-point x100 (100 = 1.0)
        """
        return self.send_command(
            f"KIM_GENERATE|{slot_id}|{max_tokens}|{temperature}",
            slot_id=slot_id,
        )

    # ---- Phase 4.2.5: Multi-Slot Methods ----

    def slot_start(
        self, slot_id: int, model_id: int, size: int, label: str = ""
    ) -> str:
        """Send SLOT_START command."""
        self._slot_seq[slot_id] = 0  # Phase 4.8-H: reset per-slot sequence
        self._token_chain_mac.pop(slot_id, None)  # Phase 6.4.1-U: reset chain MAC
        self._token_last_ts.pop(slot_id, None)  # Phase 6.4.2-H: reset timestamp state
        self._breach_count.pop(slot_id, None)  # Phase 6.4: reset breach counter
        padded = self._pad_label(
            label
        )  # Phase Omega-Ghost: fixed-width label to kernel
        return self.send_command(f"SLOT_START|{slot_id}|{model_id}|{size}|{padded}")

    def slot_finish(self, slot_id: int) -> str:
        """Send SLOT_FINISH command."""
        return self.send_command(f"SLOT_FINISH|{slot_id}")

    def slot_sync(self, slot_id: int) -> str:
        """Send SLOT_SYNC command."""
        return self.send_command(f"SLOT_SYNC|{slot_id}")

    def slot_rewind(self, slot_id: int, offset: int) -> str:
        """Send SLOT_REWIND command."""
        return self.send_command(f"SLOT_REWIND|{slot_id}|{offset}")

    def slot_suspend(self, slot_id: int) -> str:
        """Send SLOT_SUSPEND command."""
        return self.send_command(f"SLOT_SUSPEND|{slot_id}")

    def slot_resume(self, slot_id: int) -> str:
        """Send SLOT_RESUME command."""
        return self.send_command(f"SLOT_RESUME|{slot_id}")

    def slot_status(self, slot_id: int) -> str:
        """Send SLOT_STATUS command."""
        return self.send_command(f"SLOT_STATUS|{slot_id}")

    def slot_check(self, slot_id: int, addr: int) -> str:
        """Send SLOT_CHECK command."""
        return self.send_command(f"SLOT_CHECK|{slot_id}|{hex(addr)}")

    def slot_snapshot(self, slot_id: int) -> str:
        """Send SLOT_SNAPSHOT command."""
        return self.send_command(f"SLOT_SNAPSHOT|{slot_id}")

    def slot_timer(self, slot_id: int, ms: int) -> str:
        """Send SLOT_TIMER command."""
        return self.send_command(f"SLOT_TIMER|{slot_id}|{ms}")

    def slot_reset(self, slot_id: int) -> str:
        """Send SLOT_RESET command."""
        self._token_chain_mac.pop(slot_id, None)  # Phase 6.4.1-U: reset chain MAC
        self._token_last_ts.pop(slot_id, None)  # Phase 6.4.2-H: reset timestamp state
        self._breach_count.pop(slot_id, None)  # Phase 6.4: reset breach counter
        return self.send_command(f"SLOT_RESET|{slot_id}")

    def slot_swap(self, slot_a: int, slot_b: int) -> str:
        """Send SLOT_SWAP command."""
        return self.send_command(f"SLOT_SWAP|{slot_a}|{slot_b}")

    def vdev_read(self, slot_id: int, offset: int, length: int) -> str:
        """Send VDEV_READ command."""
        return self.send_command(f"VDEV_READ|{slot_id}|{offset}|{length}")

    def load_model_slot(
        self,
        file_path: str,
        slot_id: int,
        model_id: int = 1,
        label: str = "",
        chunk_size: int = 16384,
        window: int = 64,
        max_rewinds: int = 3,
    ) -> dict:
        """Load model weights into a specific slot.

        Uses SLOT_START/SLOT_SYNC/SLOT_FINISH for slot_id > 0.
        For slot_id == 0, falls back to MODEL_START/MODEL_DONE.
        """
        import os
        import time

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            raise VBusError("Empty model file")

        if slot_id == 0:
            return self.load_model_weights(
                file_path, model_id, chunk_size, window, max_rewinds
            )

        # SLOT_START
        resp = self.slot_start(slot_id, model_id, file_size, label)
        if not resp.startswith("OK|"):
            raise VBusError(f"SLOT_START failed: {resp}")

        start_time = time.monotonic()
        chunks_sent = 0
        bytes_sent = 0
        rewind_count = 0

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self._send_frame(
                    VBUS_TYPE_DATA,
                    chunk,
                    slot_id=slot_id,
                    tag=self._alloc_slot_seq(slot_id),
                )
                chunks_sent += 1
                bytes_sent += len(chunk)

                if chunks_sent % window == 0:
                    resp = self.slot_sync(slot_id)
                    if not resp.startswith("OK|"):
                        raise VBusError(f"SLOT_SYNC failed: {resp}")
                    kernel_bytes = int(resp.split("|")[1])
                    if kernel_bytes != bytes_sent:
                        if rewind_count >= max_rewinds:
                            raise VBusError("Max rewinds exceeded")
                        resp = self.slot_rewind(slot_id, kernel_bytes)
                        if not resp.startswith("OK|"):
                            raise VBusError(f"SLOT_REWIND failed: {resp}")
                        f.seek(kernel_bytes)
                        bytes_sent = kernel_bytes
                        chunks_sent = kernel_bytes // chunk_size
                        rewind_count += 1

        # SLOT_FINISH
        resp = self.slot_finish(slot_id)
        if not resp.startswith("OK|"):
            raise VBusError(f"SLOT_FINISH failed: {resp}")

        elapsed = time.monotonic() - start_time
        parts = resp.split("|")
        return {
            "addr": parts[1],
            "size": int(parts[2]),
            "checksum_xxh3": parts[3],
            "checksum_crc32c": parts[4] if len(parts) > 4 else "0",
            "chunks": chunks_sent,
            "bytes": bytes_sent,
            "rewinds": rewind_count,
            "elapsed_s": round(elapsed, 3),
        }

    # ---- Phase 4.6: Infinity Loop — SQ Burst Load (Fire-and-Forget) ----

    def load_model_burst(
        self,
        file_path: str,
        slot_id: int,
        model_id: int = 1,
        label: str = "",
        chunk_size: int = 49152,
    ) -> dict:
        """Load model weights using SQ fire-and-forget burst protocol.

        Phase 4.6 Infinity Loop: Sends all DATA frames without per-frame sync.
        The kernel bridge writes data to HugePages and posts SQ entries for
        deferred hash/CRC. SLOT_FINISH drains the SQ before finalizing.

        Uses memoryview for zero-copy buffer slicing — avoids Python-side
        copies during chunk iteration.

        Phase Omega: If label is empty, attempts to resolve it from
        _model_registry_cache (populated from Convex at startup).

        Args:
            file_path: Path to model weights file.
            slot_id: Target slot (1-3).
            model_id: MCP model identifier.
            label: Agent label (auto-resolved from cache if empty).
            chunk_size: Bytes per DATA frame (default 64KB for max throughput).

        Returns:
            dict with keys: addr, size, checksum_xxh3, checksum_crc32c,
                            chunks, bytes, elapsed_s, throughput_mbps
        """
        import os
        import time

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            raise VBusError("Empty model file")

        # Phase Omega: resolve label from local model registry cache if empty
        if not label:
            cached = self.get_cached_model(model_id)
            if cached:
                label = cached.get("label", "")

        # SLOT_START
        resp = self.slot_start(slot_id, model_id, file_size, label)
        if not resp.startswith("OK|"):
            raise VBusError(f"SLOT_START failed: {resp}")

        start_time = time.monotonic()

        # Increase socket timeout for burst — kernel must drain all DATA before FINISH
        old_timeout = self._sock.gettimeout()
        burst_timeout = max(
            60.0, file_size / (4 * 1024 * 1024)
        )  # ~4MB/s minimum estimate
        self._sock.settimeout(burst_timeout)

        try:
            # Read entire file into buffer, use memoryview for zero-copy slicing
            with open(file_path, "rb") as f:
                data = f.read()
            mv = memoryview(data)
            total = len(data)
            offset = 0
            chunks_sent = 0

            # Fire-and-forget burst: send all DATA frames with no sync
            while offset < total:
                end = min(offset + chunk_size, total)
                chunk = mv[offset:end]
                self._send_frame(
                    VBUS_TYPE_DATA,
                    chunk,
                    slot_id=slot_id,
                    tag=self._alloc_slot_seq(slot_id),
                )
                chunks_sent += 1
                offset = end

            del mv  # Release memoryview before finish

            # SLOT_FINISH — kernel drains SQ, finalizes hash/CRC
            resp = self.slot_finish(slot_id)
            if not resp.startswith("OK|"):
                raise VBusError(f"SLOT_FINISH failed: {resp}")
        finally:
            self._sock.settimeout(old_timeout)

        elapsed = time.monotonic() - start_time
        throughput = (total / (1024 * 1024)) / elapsed if elapsed > 0 else 0

        parts = resp.split("|")
        return {
            "addr": parts[1],
            "size": int(parts[2]),
            "checksum_xxh3": parts[3],
            "checksum_crc32c": parts[4] if len(parts) > 4 else "0",
            "chunks": chunks_sent,
            "bytes": total,
            "rewinds": 0,
            "elapsed_s": round(elapsed, 3),
            "throughput_mbps": round(throughput, 2),
        }

    def load_model_burst_concurrent(self, slots: list, chunk_size: int = 49152) -> list:
        """Load model weights into multiple slots concurrently via interleaved I/O.

        Phase 4.7.1: Single-threaded round-robin frame interleaving eliminates
        the threading.Lock serialization that caused 747% interference in the
        dual-slot audit. Uses select() for write readiness.

        Args:
            slots: List of dicts with keys: file_path, slot_id, model_id, label
            chunk_size: Bytes per DATA frame (default 64KB).

        Returns:
            List of result dicts (same format as load_model_burst), one per slot.
        """
        import os
        import time

        if not slots:
            return []

        # Phase 1: SLOT_START for each slot (sequential — CMD/RESP pairs)
        file_data = []
        for s in slots:
            file_size = os.path.getsize(s["file_path"])
            if file_size == 0:
                raise VBusError(f"Empty model file: {s['file_path']}")
            resp = self.slot_start(
                s["slot_id"], s.get("model_id", 1), file_size, s.get("label", "")
            )
            if not resp.startswith("OK|"):
                raise VBusError(f"SLOT_START slot {s['slot_id']} failed: {resp}")
            with open(s["file_path"], "rb") as f:
                data = f.read()
            file_data.append(
                {
                    "slot_id": s["slot_id"],
                    "data": memoryview(data),
                    "offset": 0,
                    "total": len(data),
                    "chunks": 0,
                }
            )

        start_time = time.monotonic()

        # Increase socket timeout for burst
        old_timeout = self._sock.gettimeout()
        max_size = max(fd["total"] for fd in file_data)
        burst_timeout = max(120.0, max_size / (2 * 1024 * 1024))
        self._sock.settimeout(burst_timeout)

        try:
            # Phase 2: Round-robin interleaved DATA frames (no lock, no threads)
            active = list(range(len(file_data)))
            while active:
                next_active = []
                for idx in active:
                    fd = file_data[idx]
                    if fd["offset"] < fd["total"]:
                        end = min(fd["offset"] + chunk_size, fd["total"])
                        chunk = fd["data"][fd["offset"] : end]
                        self._send_frame(
                            VBUS_TYPE_DATA,
                            chunk,
                            slot_id=fd["slot_id"],
                            tag=self._alloc_slot_seq(fd["slot_id"]),
                        )
                        fd["offset"] = end
                        fd["chunks"] += 1
                    if fd["offset"] < fd["total"]:
                        next_active.append(idx)
                active = next_active

            # Phase 3: SLOT_FINISH for each slot (sequential)
            results = []
            for i, fd in enumerate(file_data):
                del fd["data"]  # Release memoryview
                resp = self.slot_finish(slots[i]["slot_id"])
                if not resp.startswith("OK|"):
                    raise VBusError(
                        f"SLOT_FINISH slot {slots[i]['slot_id']} failed: {resp}"
                    )
                elapsed = time.monotonic() - start_time
                throughput = (
                    (fd["total"] / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                )
                parts = resp.split("|")
                results.append(
                    {
                        "addr": parts[1],
                        "size": int(parts[2]),
                        "checksum_xxh3": parts[3],
                        "checksum_crc32c": parts[4] if len(parts) > 4 else "0",
                        "chunks": fd["chunks"],
                        "bytes": fd["total"],
                        "rewinds": 0,
                        "elapsed_s": round(elapsed, 3),
                        "throughput_mbps": round(throughput, 2),
                    }
                )
        finally:
            self._sock.settimeout(old_timeout)

        return results

    # ---- Phase 4.9b: Warp Drive Methods ----

    def load_model_warp(
        self,
        file_path: str,
        slot_id: int,
        model_id: int = 1,
        label: str = "",
        priority: int = 128,
        chunk_size: int = 49152,
    ) -> dict:
        """Load model via ivshmem zero-copy warp path.

        Phase 4.9b: Instead of streaming data through VBus DATA frames,
        writes model data directly to the shared memory file (mmap) and
        notifies the kernel via WARP_POST commands.

        Falls back to VBus burst path if warp drive is unavailable.
        """
        import os
        import time
        import mmap as mmap_mod

        # Check warp availability
        try:
            resp = self.send_command("WARP_STATUS")
        except Exception as e:
            raise VBusError(f"WARP_STATUS probe failed for slot {slot_id}: {e}") from e
        if not resp or "WARP_ON" not in resp:
            return self.load_model_burst(
                file_path, slot_id, model_id, label, chunk_size
            )

        warp_size = int(resp.split()[-1]) if len(resp.split()) > 1 else 0
        zone_size = warp_size // 4  # 16MB per slot

        # B-M3: Guard against zero zone size
        if zone_size == 0:
            logger.warning("Warp zone_size=0, falling back to burst")
            return self.load_model_burst(
                file_path, slot_id, model_id, label, chunk_size
            )

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            raise VBusError("Empty model file")

        # B-M2: Zone boundary check
        zone_offset = slot_id * zone_size
        if zone_offset + zone_size > warp_size:
            raise VBusError(
                f"Slot {slot_id} zone exceeds warp boundary "
                f"({zone_offset}+{zone_size} > {warp_size})"
            )

        warp_path = "/tmp/vos3_warp.raw"

        # SLOT_START as usual
        try:
            start_resp = self.slot_start(slot_id, model_id, file_size, label)
        except VBusError:
            raise
        except Exception as e:
            raise VBusError(
                f"SLOT_START transport error (slot={slot_id}, size={file_size}): {e}"
            ) from e
        if not start_resp.startswith("OK|"):
            raise VBusError(f"SLOT_START failed: {start_resp}")

        start_time = time.monotonic()

        old_timeout = self._sock.gettimeout()
        burst_timeout = max(60.0, file_size / (4 * 1024 * 1024))
        self._sock.settimeout(burst_timeout)

        try:
            # B-H1/B-H2: mmap in try/finally for guaranteed cleanup on error
            with open(warp_path, "r+b") as f:
                mm = mmap_mod.mmap(f.fileno(), warp_size)
                try:
                    with open(file_path, "rb") as model_f:
                        offset = 0
                        chunks_sent = 0
                        while offset < file_size:
                            chunk = min(chunk_size, file_size - offset, zone_size)
                            data = model_f.read(chunk)
                            if not data:
                                break
                            # Write to warp zone (flush deferred to after all chunks)
                            mm[zone_offset : zone_offset + len(data)] = data
                            # B-M1: Notify kernel and validate response
                            try:
                                post_resp = self.send_command(
                                    f"WARP_POST|{slot_id}|{zone_offset}|{len(data)}"
                                )
                            except VBusError:
                                raise
                            except Exception as e:
                                raise VBusError(
                                    f"WARP_POST transport error (slot={slot_id}, "
                                    f"offset={offset}/{file_size}, chunk={chunks_sent}): {e}"
                                ) from e
                            if not post_resp.startswith("OK"):
                                raise VBusError(f"WARP_POST failed: {post_resp}")
                            offset += len(data)
                            chunks_sent += 1
                    # Single flush after all chunks written, before SLOT_FINISH
                    mm.flush()
                finally:
                    mm.close()

            # SLOT_FINISH
            try:
                resp = self.slot_finish(slot_id)
            except VBusError:
                raise
            except Exception as e:
                raise VBusError(
                    f"SLOT_FINISH transport error (slot={slot_id}, "
                    f"sent={chunks_sent} chunks, {file_size} bytes): {e}"
                ) from e
            if not resp.startswith("OK|"):
                raise VBusError(f"SLOT_FINISH failed: {resp}")
        finally:
            self._sock.settimeout(old_timeout)

        elapsed = time.monotonic() - start_time
        throughput = (file_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0

        parts = resp.split("|")
        return {
            "addr": parts[1],
            "size": int(parts[2]),
            "checksum_xxh3": parts[3],
            "checksum_crc32c": parts[4] if len(parts) > 4 else "0",
            "chunks": chunks_sent,
            "bytes": file_size,
            "rewinds": 0,
            "elapsed_s": round(elapsed, 3),
            "throughput_mbps": round(throughput, 2),
            "warp": True,
        }

    def load_model_warp_concurrent(self, slots: list, chunk_size: int = 49152) -> list:
        """Load models into multiple slots concurrently via warp drive.

        Phase 4.9b: Round-robin between slots, writing each chunk to its
        ivshmem warp zone and issuing WARP_POST. Falls back to
        load_model_burst_concurrent if warp is unavailable.
        """
        import os
        import time
        import mmap as mmap_mod

        if not slots:
            return []

        # Check warp availability
        try:
            resp = self.send_command("WARP_STATUS")
        except Exception as e:
            raise VBusError(
                f"WARP_STATUS probe failed (concurrent, {len(slots)} slots): {e}"
            ) from e
        if not resp or "WARP_ON" not in resp:
            return self.load_model_burst_concurrent(slots, chunk_size)

        warp_size = int(resp.split()[-1]) if len(resp.split()) > 1 else 0
        zone_size = warp_size // 4

        # B-M3: Guard against zero zone size
        if zone_size == 0:
            logger.warning("Warp zone_size=0, falling back to burst (concurrent)")
            return self.load_model_burst_concurrent(slots, chunk_size)

        warp_path = "/tmp/vos3_warp.raw"

        # Phase 1: SLOT_START for each slot
        file_data = []
        for s in slots:
            file_size = os.path.getsize(s["file_path"])
            if file_size == 0:
                raise VBusError(f"Empty model file: {s['file_path']}")
            try:
                start_resp = self.slot_start(
                    s["slot_id"], s.get("model_id", 1), file_size, s.get("label", "")
                )
            except VBusError:
                raise
            except Exception as e:
                raise VBusError(
                    f"SLOT_START transport error (slot={s['slot_id']}, "
                    f"size={file_size}): {e}"
                ) from e
            if not start_resp.startswith("OK|"):
                raise VBusError(f"SLOT_START slot {s['slot_id']} failed: {start_resp}")
            file_data.append(
                {
                    "slot_id": s["slot_id"],
                    "file_path": s["file_path"],
                    "total": file_size,
                    "offset": 0,
                    "chunks": 0,
                }
            )

        start_time = time.monotonic()

        old_timeout = self._sock.gettimeout()
        max_size = max(fd["total"] for fd in file_data)
        burst_timeout = max(120.0, max_size / (2 * 1024 * 1024))
        self._sock.settimeout(burst_timeout)

        try:
            # B-H1/B-H2: mmap in try/finally for guaranteed cleanup on error
            with open(warp_path, "r+b") as f:
                mm = mmap_mod.mmap(f.fileno(), warp_size)
                try:
                    # Open all model files
                    handles = []
                    for fd in file_data:
                        handles.append(open(fd["file_path"], "rb"))

                    try:
                        # Round-robin interleaved WARP_POST
                        active = list(range(len(file_data)))
                        while active:
                            next_active = []
                            for idx in active:
                                fd = file_data[idx]
                                if fd["offset"] < fd["total"]:
                                    chunk = min(
                                        chunk_size,
                                        fd["total"] - fd["offset"],
                                        zone_size,
                                    )
                                    data = handles[idx].read(chunk)
                                    if not data:
                                        continue
                                    zone_offset = fd["slot_id"] * zone_size
                                    # B-M2: Zone boundary check
                                    if zone_offset + len(data) > warp_size:
                                        raise VBusError(
                                            f"Slot {fd['slot_id']} warp write exceeds boundary"
                                        )
                                    mm[zone_offset : zone_offset + len(data)] = data
                                    # B-M1: Validate WARP_POST response
                                    try:
                                        post_resp = self.send_command(
                                            f"WARP_POST|{fd['slot_id']}|{zone_offset}|{len(data)}"
                                        )
                                    except VBusError:
                                        raise
                                    except Exception as e:
                                        raise VBusError(
                                            f"WARP_POST transport error (slot={fd['slot_id']}, "
                                            f"offset={fd['offset']}/{fd['total']}, "
                                            f"chunk={fd['chunks']}): {e}"
                                        ) from e
                                    if not post_resp.startswith("OK"):
                                        raise VBusError(
                                            f"WARP_POST failed: {post_resp}"
                                        )
                                    fd["offset"] += len(data)
                                    fd["chunks"] += 1
                                if fd["offset"] < fd["total"]:
                                    next_active.append(idx)
                            active = next_active
                    finally:
                        for h in handles:
                            h.close()
                    # Single flush after all chunks written
                    mm.flush()
                finally:
                    mm.close()

            # Phase 3: SLOT_FINISH for each slot
            results = []
            for i, fd in enumerate(file_data):
                try:
                    resp = self.slot_finish(slots[i]["slot_id"])
                except VBusError:
                    raise
                except Exception as e:
                    raise VBusError(
                        f"SLOT_FINISH transport error (slot={slots[i]['slot_id']}, "
                        f"sent={fd['chunks']} chunks, {fd['total']} bytes): {e}"
                    ) from e
                if not resp.startswith("OK|"):
                    raise VBusError(
                        f"SLOT_FINISH slot {slots[i]['slot_id']} failed: {resp}"
                    )
                elapsed = time.monotonic() - start_time
                throughput = (
                    (fd["total"] / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                )
                parts = resp.split("|")
                results.append(
                    {
                        "addr": parts[1],
                        "size": int(parts[2]),
                        "checksum_xxh3": parts[3],
                        "checksum_crc32c": parts[4] if len(parts) > 4 else "0",
                        "chunks": fd["chunks"],
                        "bytes": fd["total"],
                        "rewinds": 0,
                        "elapsed_s": round(elapsed, 3),
                        "throughput_mbps": round(throughput, 2),
                        "warp": True,
                    }
                )
        finally:
            self._sock.settimeout(old_timeout)

        return results

    def sq_status(self) -> str:
        """Query SQ head/tail/sentinel."""
        return self.send_command("SQ_STATUS")

    def doorbell_status(self) -> str:
        """Query doorbell matrix bits (Phase 4.7)."""
        return self.send_command("DOORBELL_STATUS")

    def smp_status(self) -> str:
        """Query SMP streaming mask."""
        return self.send_command("SMP_STATUS")

    def core_pin(self, slot_id: int) -> str:
        """Auto-pin slot to core."""
        return self.send_command(f"CORE_PIN|{slot_id}")

    # ---- Phase 4.2.7: Agentic Fabric Methods ----

    def _parse_feedback(self, payload: bytes) -> dict:
        """Parse deep diagnostic feedback payload."""
        slot_id = payload[0]
        fault_addr = struct.unpack_from("<Q", payload, 1)[0]
        fault_rip = struct.unpack_from("<Q", payload, 9)[0]
        fault_rsp = struct.unpack_from("<Q", payload, 17)[0]
        stack_len = payload[25]
        stack_capture = payload[26 : 26 + stack_len]
        tail_off = 26 + stack_len
        tail_len = payload[tail_off] if tail_off < len(payload) else 0
        rx_tail = (
            payload[tail_off + 1 : tail_off + 1 + tail_len] if tail_len > 0 else b""
        )
        return {
            "slot_id": slot_id,
            "fault_addr": fault_addr,
            "fault_rip": fault_rip,
            "fault_rsp": fault_rsp,
            "stack_len": stack_len,
            "stack_capture": stack_capture,
            "tail_len": tail_len,
            "rx_tail": rx_tail,
        }

    def set_feedback_callback(self, fn):
        """Register callback for deep diagnostic feedback: fn(feedback_dict)."""
        self._feedback_callback = fn

    def get_last_feedback(self) -> Optional[dict]:
        """Return last received feedback frame data, or None."""
        return self._last_feedback

    def get_driver_pressure(self) -> dict:
        """Query the kernel for AI hardware driver pressure metrics.

        Returns dict with keys: congested (bool), hp_used (int), hp_total (int),
        timeouts (int), pressure_ratio (float 0.0-1.0).

        Returns a safe "not congested" default if VBus is unavailable.
        """
        _DEFAULT = {
            "congested": False,
            "hp_used": 0,
            "hp_total": 1,
            "timeouts": 0,
            "pressure_ratio": 0.0,
            "available": False,
        }
        try:
            raw = self.send_command("DRIVER_PRESSURE")
            if not raw or not isinstance(raw, str):
                return _DEFAULT
            # Strip "OK|" prefix emitted by send_ok() in the kernel
            if raw.startswith("OK|"):
                raw = raw[3:]
            # Parse: "congested=N|hp_used=U|hp_total=T|timeouts=X"
            fields = {}
            for part in raw.split("|"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    fields[k.strip()] = v.strip()
            hp_used = int(fields.get("hp_used", 0))
            hp_total = max(1, int(fields.get("hp_total", 1)))
            pressure_ratio = hp_used / hp_total
            return {
                "congested": fields.get("congested", "0") == "1",
                "hp_used": hp_used,
                "hp_total": hp_total,
                "timeouts": int(fields.get("timeouts", 0)),
                "pressure_ratio": round(pressure_ratio, 3),
                "available": True,
            }
        except Exception as e:
            logger.debug("get_driver_pressure() unavailable: %s", e)
            return _DEFAULT

    def isc_send(
        self, from_slot: int, to_slot: int, data: bytes, context_id: int = 0
    ) -> str:
        """Send ISC_SEND command with optional context_id for session-aware routing."""
        hex_data = data.hex()
        return self.send_command(
            f"ISC_SEND|{from_slot}|{to_slot}|{context_id}|{hex_data}"
        )

    def isc_recv(self, slot_id: int) -> Optional[Tuple[int, bytes]]:
        """Send ISC_RECV command. Returns (context_id, message_bytes) or None if empty."""
        resp = self.send_command(f"ISC_RECV|{slot_id}")
        if resp.startswith("OK|"):
            # Format: OK|context_id|hex_data
            parts = resp.split("|", 2)
            if len(parts) < 3:
                # Backward compat: OK|hex_data (no context_id)
                hex_data = parts[1]
                context_id = 0
            else:
                context_id = int(parts[1])
                hex_data = parts[2]
            if len(hex_data) % 2 != 0:
                logger.error(
                    "ISC_RECV BAD HEX: full_resp='%s' hex_data='%s' len=%d",
                    resp,
                    hex_data,
                    len(hex_data),
                )
                return None
            return (context_id, bytes.fromhex(hex_data))
        return None

    def slot_dormant(self, slot_id: int) -> str:
        """Send SLOT_DORMANT command."""
        return self.send_command(f"SLOT_DORMANT|{slot_id}")

    def slot_wake(self, slot_id: int) -> str:
        """Send SLOT_WAKE command."""
        return self.send_command(f"SLOT_WAKE|{slot_id}")

    def slot_quota(self, slot_id: int, max_cycles: int) -> str:
        """Send SLOT_QUOTA command."""
        return self.send_command(f"SLOT_QUOTA|{slot_id}|{max_cycles}")

    def slot_caps(self, slot_id: int, capabilities: int, agent_type: int) -> str:
        """Send SLOT_CAPS command."""
        return self.send_command(f"SLOT_CAPS|{slot_id}|{capabilities}|{agent_type}")

    def slot_affinity(self, slot_id: int, cpu_id: int) -> str:
        """Send SLOT_AFFINITY command."""
        return self.send_command(f"SLOT_AFFINITY|{slot_id}|{cpu_id}")

    def slot_checkpoint(self, slot_id: int) -> str:
        """Send SLOT_CHECKPOINT command."""
        return self.send_command(f"SLOT_CHECKPOINT|{slot_id}")

    def slot_rollback(self, slot_id: int) -> str:
        """Send SLOT_ROLLBACK command."""
        return self.send_command(f"SLOT_ROLLBACK|{slot_id}")

    def slot_io_mask(self, slot_id: int, mask: int) -> str:
        """Send SLOT_IO_MASK command."""
        return self.send_command(f"SLOT_IO_MASK|{slot_id}|{mask}")

    def send_interrupt(self, slot_id: int) -> str:
        """Send INTERRUPT frame to wake a dormant slot via VBus.

        Uses VBUS_TYPE_INTERRUPT (0x0B) — bypasses the text command layer
        for lowest-latency slot wakeup (e.g., webhook-triggered inference).
        """
        tag = self._alloc_tag()
        self._send_frame(
            VBUS_TYPE_INTERRUPT, bytes([slot_id]), slot_id=slot_id, tag=tag
        )
        ftype, _sid, _tag, payload = self._recv_frame()
        return payload.decode("utf-8", errors="replace")

    # ---- Phase 4.2.7+: Context Persistence & Event Pub/Sub ----

    def slot_context_config(self, slot_id: int, page_count: int) -> str:
        """Send SLOT_CONTEXT_CONFIG command to allocate 4KB context pages."""
        return self.send_command(f"SLOT_CONTEXT_CONFIG|{slot_id}|{page_count}")

    def slot_warm_reset(self, slot_id: int) -> str:
        """Send SLOT_WARM_RESET command (reset model, preserve context pages)."""
        return self.send_command(f"SLOT_WARM_RESET|{slot_id}")

    def event_subscribe(self, event_type: int, slot_id: int) -> str:
        """Send EVENT_SUBSCRIBE text command."""
        return self.send_command(f"EVENT_SUBSCRIBE|{event_type}|{slot_id}")

    def event_unsubscribe(self, event_type: int) -> str:
        """Send EVENT_UNSUBSCRIBE text command."""
        return self.send_command(f"EVENT_UNSUBSCRIBE|{event_type}")

    def send_event_interrupt(self, event_type: int, payload: bytes = b"") -> str:
        """Send INTERRUPT frame with pub/sub routing (event_type + payload).

        Uses VBUS_TYPE_INTERRUPT with multi-byte payload:
        payload[0]=event_type, payload[1..]=event_data.
        Kernel routes to subscribed slot via event delivery pipeline.
        """
        data = bytes([event_type]) + payload
        tag = self._alloc_tag()
        self._send_frame(VBUS_TYPE_INTERRUPT, data, slot_id=0xFF, tag=tag)
        ftype, _sid, _tag, resp_payload = self._recv_frame()
        return resp_payload.decode("utf-8", errors="replace")

    def slot_enumerate(self) -> list:
        """Send SLOT_ENUMERATE command. Returns list of slot info dicts."""
        resp = self.send_command("SLOT_ENUMERATE")
        if not resp.startswith("OK|"):
            return []
        # Parse: OK|slot0_info|slot1_info|slot2_info|slot3_info
        raw = resp[3:]  # Strip "OK|"
        slots = []
        for slot_str in raw.split("|"):
            parts = slot_str.split(":")
            if len(parts) < 15:
                logger.debug(
                    "slot_enumerate: skipping segment with %d fields (need 15): %s",
                    len(parts),
                    slot_str[:80],
                )
                continue
            try:

                def safe_int(s, base=10, default=0):
                    """Parse int safely, returning default for empty strings."""
                    if not s or not s.strip():
                        return default
                    return int(s.strip(), base)

                slots.append(
                    {
                        "slot_id": safe_int(parts[0]),
                        "status": parts[1].strip(),
                        "label": parts[2].strip(),
                        "agent_type": safe_int(parts[3]),
                        "capabilities": safe_int(parts[4], 16),
                        "priority": safe_int(parts[5]),
                        "affinity": safe_int(parts[6]),
                        "model_id": safe_int(parts[7]),
                        "version": safe_int(parts[8]),
                        "epoch": safe_int(parts[9]),
                        "size": safe_int(parts[10]),
                        "cycles": safe_int(parts[11]),
                        "violations": safe_int(parts[12]),
                        "io_mask": safe_int(parts[13], 16),
                        "has_checkpoint": safe_int(parts[14]),
                    }
                )
            except (ValueError, IndexError) as e:
                logger.warning(
                    "slot_enumerate: parse error on '%s': %s", slot_str[:80], e
                )
                continue
        return slots

    def send_batch(self, commands: list) -> list:
        """Send multiple commands atomically as a BATCH frame.

        All N RESPs share the same tag for grouping.
        Returns a list of response strings, one per command.
        """
        tag = self._alloc_tag()
        payload = "\n".join(commands).encode("utf-8")
        self._send_frame(VBUS_TYPE_BATCH, payload, tag=tag)
        responses = []
        for _ in commands:
            frame_type, _sid, _tag, data = self._recv_frame()
            responses.append(data.decode("utf-8", errors="replace"))
        return responses

    # ---- Phase 4.2.8: Swarm Governance Primitives ----

    def slot_shared_map(self, target_slot: int, source_phys: int) -> str:
        """Send SLOT_SHARED_MAP command to map a shared hugepage into target slot."""
        return self.send_command(f"SLOT_SHARED_MAP|{target_slot}|{source_phys}")

    def slot_barrier_wait(self, slot_id: int, mask: int) -> str:
        """Send SLOT_BARRIER_WAIT command to register slot in barrier with mask."""
        return self.send_command(f"SLOT_BARRIER_WAIT|{slot_id}|{mask}")

    # ---- Phase 4.2.9: Autonomous Agentic Primitives ----

    def yield_ex(self, slot_id: int, timeout_ms: int, event_mask: int) -> str:
        """Send YIELD_EX command: voluntary DORMANT with timed/event wake."""
        return self.send_command(f"YIELD_EX|{slot_id}|{timeout_ms}|{event_mask}")

    def commit_slot(self, slot_id: int) -> str:
        """Send COMMIT_SLOT command: flush consensus-gated RESP buffer."""
        return self.send_command(f"COMMIT_SLOT|{slot_id}")

    def slot_gate(self, slot_id: int, enable: int) -> str:
        """Send SLOT_GATE command: enable/disable consensus gating on slot."""
        return self.send_command(f"SLOT_GATE|{slot_id}|{enable}")

    def context_share(self, src_slot: int, dst_slot: int) -> str:
        """Send CONTEXT_SHARE command: COW-share src context pages into dst."""
        return self.send_command(f"CONTEXT_SHARE|{src_slot}|{dst_slot}")

    # ---- Phase 5: Context Management (Freeze/Thaw) — April 2026 Hardened ----

    def ctx_freeze(self, slot_id: int, scrub: bool = True) -> dict:
        """Differential freeze: checkpoint + scrub + read only dirty pages."""
        import time as _time

        resp = self.send_command(f"CTX_FREEZE|{slot_id}")
        parts = resp.split("|")
        # OK|freeze_id|page_count|dirty_count|bitmap_hex|hash|crc|epoch|offset|session_epoch
        freeze_id = (
            int(parts[1], 16)
            if parts[1].startswith(
                (
                    "0",
                    "1",
                    "2",
                    "3",
                    "4",
                    "5",
                    "6",
                    "7",
                    "8",
                    "9",
                    "a",
                    "b",
                    "c",
                    "d",
                    "e",
                    "f",
                )
            )
            else int(parts[1])
        )
        page_count = int(parts[2])
        dirty_count = int(parts[3])
        dirty_bitmap = int(parts[4], 16)
        hash_val = parts[5]
        crc = parts[6]
        epoch = parts[7]
        offset = parts[8]
        session_epoch = int(parts[9])

        if scrub:
            self.send_command(f"CTX_SCRUB|{slot_id}")

        pages = {}
        for i in range(page_count):
            if dirty_bitmap & (1 << i):
                rd_resp = self.send_command(f"CTX_READ|{slot_id}|{i}")
                hex_data = rd_resp.split("|", 1)[1]
                pages[i] = bytes.fromhex(hex_data)

        return {
            "freeze_id": freeze_id,
            "slot_id": slot_id,
            "page_count": page_count,
            "dirty_count": dirty_count,
            "dirty_bitmap": dirty_bitmap,
            "pages": pages,
            "hash": hash_val,
            "crc": crc,
            "epoch": epoch,
            "offset": offset,
            "session_epoch": session_epoch,
            "created_at": _time.time(),
        }

    def _compute_thaw_crc(self, segment: dict) -> int:
        """Compute CRC32C matching kernel's vos3_ai_ctx_compute_crc()."""
        crc = _crc32c(struct.pack("<Q", segment["freeze_id"]))
        bitmap_lo = segment["dirty_bitmap"] & 0xFFFFFFFFFFFFFFFF
        bitmap_hi = (segment["dirty_bitmap"] >> 64) & 0xFFFFFFFFFFFFFFFF
        bitmap_bytes = struct.pack("<QQ", bitmap_lo, bitmap_hi)
        crc = _crc32c(bitmap_bytes, crc)
        for i in range(segment["page_count"]):
            if i in segment["pages"]:
                crc = _crc32c(segment["pages"][i], crc)
        return crc

    def ctx_thaw(self, slot_id: int, segment: dict, cold: bool = False) -> str:
        """Thaw context with atomic commit. CRC verified before activation."""
        pc = segment["page_count"]
        fid = segment["freeze_id"]
        se = segment["session_epoch"]
        cmd = "CTX_THAW_COLD" if cold else "CTX_THAW"
        self.send_command(f"{cmd}|{slot_id}|{pc}|{fid}|{se}")

        for page_idx, page_data in segment["pages"].items():
            self.send_command(f"CTX_WRITE|{slot_id}|{page_idx}|{page_data.hex()}")

        expected_crc = self._compute_thaw_crc(segment)
        return self.send_command(f"CTX_THAW_DONE|{slot_id}|{expected_crc}")

    async def ctx_thaw_async(self, slot_id: int, segment: dict, fetch_fn=None) -> str:
        """Cold-thaw with async L3 fetch. Non-blocking."""
        pc = segment["page_count"]
        fid = segment["freeze_id"]
        se = segment["session_epoch"]
        self.send_command(f"CTX_THAW_COLD|{slot_id}|{pc}|{fid}|{se}")

        for page_idx in sorted(segment["pages"].keys()):
            if fetch_fn:
                page_data = await fetch_fn(segment["freeze_id"], page_idx)
            else:
                page_data = segment["pages"][page_idx]
            self.send_command(f"CTX_WRITE|{slot_id}|{page_idx}|{page_data.hex()}")

        expected_crc = self._compute_thaw_crc(segment)
        return self.send_command(f"CTX_THAW_DONE|{slot_id}|{expected_crc}")

    def ctx_hint_thaw(
        self, slot_id: int, page_count: int, freeze_id: int, session_epoch: int
    ) -> str:
        """Predictive Paging: pre-allocate + zero-fill pages before task starts."""
        return self.send_command(
            f"CTX_THAW_COLD|{slot_id}|{page_count}|{freeze_id}|{session_epoch}"
        )

    def ctx_get_latency(self, slot_id: int) -> int:
        """Query context access latency (ticks from thaw start to thaw_done)."""
        resp = self.send_command(f"CTX_LATENCY|{slot_id}")
        return int(resp.split("|")[1])

    # ---- Phase 8: Sovereign Intelligence & V-Palace ----

    def lock_prefix(self, slot_id: int, count: int = 4) -> str:
        """Lock first N HugePages as immutable prefix."""
        return self.send_command(f"LOCK_PREFIX|{slot_id}|{count}")

    def unlock_prefix(self, slot_id: int) -> str:
        """Unlock prefix HugePages."""
        return self.send_command(f"UNLOCK_PREFIX|{slot_id}")

    def palace_assign(self, slot_id: int, hp_index: int, hall: int) -> str:
        """Assign room to hall (0=WEIGHT, 1=PERSISTENT, 2=TRANSIENT)."""
        return self.send_command(f"PALACE_ASSIGN|{slot_id}|{hp_index}|{hall}")

    def palace_query(self, slot_id: int, hp_index: int) -> str:
        """Query room metadata. Returns 'hall|locked|access_count|last_tick'."""
        return self.send_command(f"PALACE_QUERY|{slot_id}|{hp_index}")

    def palace_stats(self, slot_id: int) -> str:
        """Query per-hall counts. Returns 'weight|persistent|transient'."""
        return self.send_command(f"PALACE_STATS|{slot_id}")

    def palace_touch(self, slot_id: int, hp_index: int) -> str:
        """Touch room to update access recency."""
        return self.send_command(f"PALACE_TOUCH|{slot_id}|{hp_index}")

    def v_aaak_encode_remote(self, data: bytes) -> bytes:
        """Encode via kernel V-AAAK (round-trip through VBus)."""
        hex_data = data.hex()
        resp = self.send_command(f"V_AAAK_ENCODE|{hex_data}")
        return bytes.fromhex(resp)

    def v_aaak_decode_remote(self, data: bytes) -> bytes:
        """Decode via kernel V-AAAK (round-trip through VBus)."""
        hex_data = data.hex()
        resp = self.send_command(f"V_AAAK_DECODE|{hex_data}")
        return bytes.fromhex(resp)

    # ---- Phase 9: Bare-Metal Peak ----

    def dma_warp_xfer(
        self, slot_id: int, hp_start: int = 0, hp_count: int = 1, length: int = 2097152
    ) -> str:
        """Initiate DMA warp transfer from ivshmem to slot."""
        return self.send_command(
            f"DMA_WARP_XFER|{slot_id}|{hp_start}|{hp_count}|{length}"
        )

    def dma_warp_stats(self) -> str:
        """Get DMA warp engine statistics."""
        return self.send_command("DMA_WARP_STATS")

    def storage_info(self) -> str:
        """Get storage HAL device info."""
        return self.send_command("STORAGE_INFO")

    def aaak_mode(self, enable: bool = True) -> str:
        """Enable/disable V-AAAK native compression mode."""
        return self.send_command(f"AAAK_MODE|{1 if enable else 0}")

    # ---- Internal frame I/O ----

    # Phase 4.3.1: Pre-allocated zero padding for header (avoids per-frame alloc)
    _HDR_PAD = b"\x00" * 48

    def _send_frame(
        self, frame_type: int, payload: bytes, slot_id: int = 0xFF, tag: int = 0
    ) -> None:
        """Build and send a v2 binary frame with dual-CRC.

        Phase 4.3.1: Uses incremental CRC and scatter send to eliminate
        2MB byte-string concatenations for Jumbo Frames.
        """
        if self._sock is None:
            raise VBusError("Not connected")

        length = len(payload)
        # 8-byte header prefix: type + slot_id + tag + len
        hdr_prefix = struct.pack("<BBHI", frame_type, slot_id, tag, length)
        # hdr_crc: CRC32C over prefix(8) only — Length-Corruption defense
        hdr_crc = _crc32c(hdr_prefix)
        # payload_crc: CRC32C over prefix(8) + payload — incremental, no concatenation
        payload_crc = _crc32c(payload, _crc32c(hdr_prefix))
        # Phase 4.2.19: 64-byte header with 48 bytes zero padding
        header = struct.pack(
            FRAME_HDR_FMT,
            frame_type,
            slot_id,
            tag,
            length,
            payload_crc,
            hdr_crc,
            self._HDR_PAD,
        )
        # Phase 5: HMAC-SHA256 frame authentication
        if self._hmac_key:
            hmac_input = header[:16] + payload
            mac = hmac_mod.new(self._hmac_key, hmac_input, hashlib.sha256).digest()
            # Overwrite padding bytes 16-47 with HMAC, set flag at byte 48
            header = header[:16] + mac + bytes([0x01]) + b"\x00" * 15
        # Scatter send: header then payload — avoids 2MB+ concatenation
        self._sock.sendall(header)
        if length > 0:
            self._sock.sendall(payload)

    def _recv_frame(self) -> Tuple[int, int, int, bytes]:
        """Receive and validate a v2 binary frame with dual-CRC.

        Returns (frame_type, slot_id, tag, payload).
        Transparently handles EVENT/FEEDBACK/NOTIFY frames by queuing them.
        """
        while True:
            header = self._recv_exact(FRAME_HDR_SIZE)
            frame_type, slot_id, tag, length, expected_pcrc, expected_hcrc, _padding = (
                struct.unpack(FRAME_HDR_FMT, header)
            )

            # Validate hdr_crc FIRST (fast reject on length corruption)
            # Slice first 8 bytes from received header instead of re-packing —
            # header[:8] == struct.pack("<BBHI", type, slot_id, tag, length)
            hdr_prefix = header[:8]
            actual_hcrc = _crc32c(hdr_prefix)
            if actual_hcrc != expected_hcrc:
                raise VBusError(
                    f"Header CRC mismatch: expected 0x{expected_hcrc:08x}, "
                    f"got 0x{actual_hcrc:08x} — Length-Corruption blocked"
                )

            if length > VBUS_MAX_PAYLOAD:
                raise VBusError(f"Frame too large: {length} > {VBUS_MAX_PAYLOAD}")

            payload = self._recv_exact(length) if length > 0 else b""
            logger.debug(
                "_recv_frame: type=0x%02x slot=%d tag=0x%04x len=%d",
                frame_type,
                slot_id,
                tag,
                length,
            )

            # Validate payload_crc — incremental CRC, no concatenation
            actual_pcrc = _crc32c(payload, _crc32c(hdr_prefix))
            if actual_pcrc != expected_pcrc:
                raise VBusError(
                    f"Payload CRC mismatch: expected 0x{expected_pcrc:08x}, "
                    f"got 0x{actual_pcrc:08x} (len={length})"
                )

            # Phase 5: HMAC-SHA256 authentication check
            if self._hmac_key:
                received_hmac = header[16:48]
                flag = header[48]
                if not (flag & 0x01):
                    raise VBusError("HMAC required but flag not set")
                expected_mac = hmac_mod.new(
                    self._hmac_key, header[:16] + payload, hashlib.sha256
                ).digest()
                if not hmac_mod.compare_digest(received_hmac, expected_mac):
                    raise VBusError("HMAC verification failed")

            # Transparently handle EVENT/NOTIFY frames
            if frame_type in (VBUS_TYPE_EVENT, VBUS_TYPE_NOTIFY) and len(payload) >= 14:
                event = self._parse_event(payload)
                self._event_queue.append(event)
                if self._event_callback:
                    self._event_callback(event)
                continue

            # Transparently handle TOKEN_STREAM frames (Phase 6: KIM)
            if frame_type == VBUS_TYPE_TOKEN_STREAM and len(payload) >= 13:
                token = self._parse_token_frame(payload)
                sid = token.get("slot_id", 0)

                # Phase 6.4 Forensic: STREAM_PROVENANCE_BREACH detection.
                # If chain_valid or ts_valid is False, a provenance breach
                # has occurred — log at ERROR level and track consecutive count.
                chain_ok = token.get("chain_valid", True)
                ts_ok = token.get("ts_valid", True)
                if not chain_ok or not ts_ok:
                    self._breach_count[sid] = self._breach_count.get(sid, 0) + 1
                    self._breach_total += 1
                    reasons = []
                    if not chain_ok:
                        reasons.append("CHAIN_MAC_MISMATCH")
                    if not ts_ok:
                        reasons.append("TIMESTAMP_VIOLATION")
                    logger.error(
                        "STREAM_PROVENANCE_BREACH slot=%d seq=%d "
                        "token_id=%d reason=%s consecutive=%d total=%d",
                        sid,
                        token.get("seq", 0),
                        token.get("token_id", 0),
                        "+".join(reasons),
                        self._breach_count[sid],
                        self._breach_total,
                    )
                    # Kill session after N consecutive breaches on same slot
                    if self._breach_count[sid] >= self._BREACH_KILL_THRESHOLD:
                        logger.critical(
                            "STREAM_PROVENANCE_BREACH: %d consecutive on slot %d "
                            "— session terminated for safety",
                            self._breach_count[sid],
                            sid,
                        )
                        self.disconnect()
                        raise VBusError(
                            f"STREAM_PROVENANCE_BREACH: {self._breach_count[sid]} "
                            f"consecutive breaches on slot {sid}"
                        )
                else:
                    # Valid frame — reset consecutive breach counter for this slot
                    self._breach_count[sid] = 0

                self._token_queue.setdefault(sid, []).append(token)
                if self._token_callback:
                    self._token_callback(token)
                continue

            # Transparently handle FEEDBACK frames
            if frame_type == VBUS_TYPE_FEEDBACK and len(payload) >= 27:
                feedback = self._parse_feedback(payload)
                self._last_feedback = feedback
                if self._feedback_callback:
                    self._feedback_callback(feedback)
                continue

            return frame_type, slot_id, tag, payload

    def _recv_exact(self, n: int) -> bytes:
        """Read exactly n bytes from the socket.

        Pre-allocates buffer of exact size and uses recv_into with a
        memoryview to avoid intermediate bytearray resizing and the
        extra copy from extend().
        """
        if self._sock is None:
            raise VBusError("Not connected")

        buf = bytearray(n)
        mv = memoryview(buf)
        pos = 0
        while pos < n:
            nbytes = self._sock.recv_into(mv[pos:], n - pos)
            if nbytes == 0:
                raise VBusError(f"Connection closed (needed {n} bytes, got {pos})")
            pos += nbytes
        return bytes(buf)
