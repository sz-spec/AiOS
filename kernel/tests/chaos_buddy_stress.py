"""
Chaos Buddy Allocator Stress Test -- VOS3 HugePage Pool Integrity

Connects to VOS3 over VBus and hammers the buddy allocator with 10,000+
rapid-fire alloc/dealloc cycles (SLOT_START/SLOT_RESET) across slots 1-7,
then verifies zero HugePage leaks via HP_STATS.

Run:
    python3 -m pytest kernel/tests/chaos_buddy_stress.py -v -m chaos --tb=short

Requires:
    - QEMU running VOS3 with VBus socket at /tmp/vos3_bridge.sock
    - crcmod or crc32c package (falls back to pure-Python CRC32C)

Frame format v2.19 (64-byte header):
    [u8:type][u8:slot_id][u16:tag LE][u32:length LE]
    [u32:payload_crc LE][u32:hdr_crc LE][48B pad]
"""

import os
import random
import socket
import struct
import threading
import time
from typing import Optional, Tuple

import pytest

# ---------------------------------------------------------------------------
# CRC32C (Castagnoli) -- hardware-accelerated or pure-Python fallback
# ---------------------------------------------------------------------------

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
                    crc >>= 1
            _CRC32C_TABLE.append(crc)

    _crc32c_build_table()

    def _crc32c(data: bytes, value: int = 0) -> int:
        crc = (value ^ 0xFFFFFFFF) & 0xFFFFFFFF
        for b in data:
            crc = _CRC32C_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
        return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Constants (must match kernel/include/vos/virtio_vbus.h)
# ---------------------------------------------------------------------------

VBUS_TYPE_CMD       = 0x01
VBUS_TYPE_RESP      = 0x02
VBUS_TYPE_HANDSHAKE = 0x04
VBUS_TYPE_PING      = 0x05
VBUS_TYPE_EVENT     = 0x06
VBUS_TYPE_NOTIFY    = 0x0A
VBUS_TYPE_TOKEN_STREAM = 0x07
VBUS_TYPE_FEEDBACK  = 0x08

# v2.19: 64-byte header with 48 bytes of zero padding
FRAME_HDR_FMT  = "<BBHIII48s"
FRAME_HDR_SIZE = struct.calcsize(FRAME_HDR_FMT)  # 64 bytes

VBUS_MAX_PAYLOAD = 2097152  # 2 MB jumbo MTU
VBUS_TIMEOUT_S   = 5.0

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")

# Slots available for model loading (slot 0 is Coordinator -- kernel-only)
ALLOCATABLE_SLOTS = list(range(1, 8))  # 1..7

HDR_PAD = b"\x00" * 48


# ---------------------------------------------------------------------------
# Skip marker -- skip all runtime tests if QEMU is not running
# ---------------------------------------------------------------------------

requires_qemu = pytest.mark.skipif(
    not os.path.exists(BRIDGE_SOCKET),
    reason=f"QEMU not running (no socket at {BRIDGE_SOCKET})",
)


# ---------------------------------------------------------------------------
# VBusClient helper class
# ---------------------------------------------------------------------------

class VBusClient:
    """Lightweight VBus binary frame client for chaos testing.

    Handles socket connect, v2.19 64-byte header framing, dual-CRC32C
    integrity, HMAC-SHA256 handshake, and transparent EVENT/NOTIFY draining.
    """

    def __init__(self, socket_path: str = BRIDGE_SOCKET, timeout: float = VBUS_TIMEOUT_S):
        self.socket_path = socket_path
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._next_tag = 1
        self._hmac_key: Optional[bytes] = None

    # -- Connection lifecycle --

    def connect(self, retries: int = 5, delay: float = 0.5) -> None:
        """Connect to VBus and perform HANDSHAKE (with HMAC negotiation)."""
        for attempt in range(retries):
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                self._sock = sock
                break
            except ConnectionRefusedError:
                if attempt < retries - 1:
                    time.sleep(delay)
                else:
                    raise

        # Drain stale frames (kernel heartbeat PINGs)
        self._sock.setblocking(False)
        try:
            while True:
                stale = self._sock.recv(4096)
                if not stale:
                    break
        except BlockingIOError:
            pass
        self._sock.setblocking(True)
        self._sock.settimeout(self.timeout)

        # HANDSHAKE with HMAC-SHA256 key exchange
        hmac_key = os.urandom(32)
        self._send_frame(VBUS_TYPE_HANDSHAKE, b"VBUS2" + hmac_key,
                         slot_id=0xFF, tag=0)

        # Skip up to 5 non-HANDSHAKE frames (EVENT, PING, etc.)
        for _ in range(5):
            ftype, _sid, _tag, payload = self._recv_frame()
            if ftype == VBUS_TYPE_HANDSHAKE:
                resp_str = payload.decode("utf-8", errors="replace")
                if "HMAC" in resp_str:
                    self._hmac_key = hmac_key
                else:
                    # Accept non-HMAC handshake for testing flexibility
                    self._hmac_key = None
                return

        raise RuntimeError("HANDSHAKE failed: no response after 5 frames")

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()

    # -- Tag allocation --

    def _alloc_tag(self) -> int:
        tag = self._next_tag
        self._next_tag = (self._next_tag + 1) & 0xFFFF
        if self._next_tag in (0x0000, 0xFFFF):
            self._next_tag = 1
        return tag

    # -- Frame send/recv --

    def _send_frame(self, frame_type: int, payload: bytes,
                    slot_id: int = 0xFF, tag: int = 0) -> None:
        if self._sock is None:
            raise RuntimeError("Not connected")

        length = len(payload)
        hdr_prefix = struct.pack("<BBHI", frame_type, slot_id, tag, length)
        hdr_crc = _crc32c(hdr_prefix)
        payload_crc = _crc32c(payload, _crc32c(hdr_prefix))

        header = struct.pack(FRAME_HDR_FMT, frame_type, slot_id, tag,
                             length, payload_crc, hdr_crc, HDR_PAD)

        # HMAC-SHA256 authentication if negotiated
        if self._hmac_key:
            import hashlib
            import hmac as hmac_mod
            hmac_input = header[:16] + payload
            mac = hmac_mod.new(self._hmac_key, hmac_input, hashlib.sha256).digest()
            header = header[:16] + mac + bytes([0x01]) + b"\x00" * 15

        self._sock.sendall(header)
        if length > 0:
            self._sock.sendall(payload)

    def _recv_frame(self) -> Tuple[int, int, int, bytes]:
        """Receive a frame, transparently skipping EVENT/NOTIFY/FEEDBACK/TOKEN_STREAM."""
        while True:
            header = self._recv_exact(FRAME_HDR_SIZE)
            (frame_type, slot_id, tag, length,
             expected_pcrc, expected_hcrc, _pad) = struct.unpack(FRAME_HDR_FMT, header)

            hdr_prefix = header[:8]
            actual_hcrc = _crc32c(hdr_prefix)
            if actual_hcrc != expected_hcrc:
                raise RuntimeError(
                    f"Header CRC mismatch: expected 0x{expected_hcrc:08x}, "
                    f"got 0x{actual_hcrc:08x}")

            if length > VBUS_MAX_PAYLOAD:
                raise RuntimeError(f"Frame too large: {length}")

            payload = self._recv_exact(length) if length > 0 else b""

            actual_pcrc = _crc32c(payload, _crc32c(hdr_prefix))
            if actual_pcrc != expected_pcrc:
                raise RuntimeError(
                    f"Payload CRC mismatch: expected 0x{expected_pcrc:08x}, "
                    f"got 0x{actual_pcrc:08x}")

            # HMAC verification if negotiated
            if self._hmac_key:
                import hashlib
                import hmac as hmac_mod
                received_hmac = header[16:48]
                flag = header[48]
                if flag & 0x01:
                    expected_mac = hmac_mod.new(
                        self._hmac_key, header[:16] + payload, hashlib.sha256
                    ).digest()
                    if not hmac_mod.compare_digest(received_hmac, expected_mac):
                        raise RuntimeError("HMAC verification failed")

            # Transparently skip async frame types
            if frame_type in (VBUS_TYPE_EVENT, VBUS_TYPE_NOTIFY):
                continue
            if frame_type == VBUS_TYPE_TOKEN_STREAM:
                continue
            if frame_type == VBUS_TYPE_FEEDBACK:
                continue

            return frame_type, slot_id, tag, payload

    def _recv_exact(self, n: int) -> bytes:
        if self._sock is None:
            raise RuntimeError("Not connected")
        buf = bytearray(n)
        mv = memoryview(buf)
        pos = 0
        while pos < n:
            nbytes = self._sock.recv_into(mv[pos:], n - pos)
            if nbytes == 0:
                raise RuntimeError(f"Connection closed (needed {n} bytes, got {pos})")
            pos += nbytes
        return bytes(buf)

    # -- Command helpers --

    def send_command(self, command: str, slot_id: int = 0xFF) -> str:
        """Send a text command and return the response string."""
        payload = command.encode("utf-8")
        tag = self._alloc_tag()
        self._send_frame(VBUS_TYPE_CMD, payload, slot_id=slot_id, tag=tag)

        ftype, _sid, rtag, resp_payload = self._recv_frame()
        if ftype != VBUS_TYPE_RESP:
            raise RuntimeError(f"Expected RESP (0x02), got 0x{ftype:02x}")
        if rtag != tag:
            raise RuntimeError(f"Tag mismatch: sent 0x{tag:04x}, got 0x{rtag:04x}")

        return resp_payload.decode("utf-8", errors="replace")

    def hp_stats(self) -> dict:
        """Query HugePage pool stats. Returns dict with at least 'free' count."""
        resp = self.send_command("HP_STATS")
        # Expected format: OK|free=N|total=M|reserved=R  (or similar)
        result = {"raw": resp}
        if resp.startswith("OK"):
            for part in resp.split("|"):
                if "=" in part:
                    key, val = part.split("=", 1)
                    key = key.strip().lower()
                    try:
                        result[key] = int(val.strip())
                    except ValueError:
                        result[key] = val.strip()
        return result

    def slot_start(self, slot_id: int, model_id: int = 1,
                   size: int = 2097152, label: str = "chaos_test") -> str:
        """Send SLOT_START command to allocate HugePages for a slot."""
        return self.send_command(f"SLOT_START|{slot_id}|{model_id}|{size}|{label}")

    def slot_reset(self, slot_id: int) -> str:
        """Send SLOT_RESET command to deallocate a slot's HugePages."""
        return self.send_command(f"SLOT_RESET|{slot_id}")

    def ping(self) -> bool:
        """Send PING and verify PONG. Returns True if bridge is alive."""
        try:
            resp = self.send_command("PING")
            return "PONG" in resp or "OK" in resp
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_hp_free(stats: dict) -> int:
    """Extract the free HugePage count from HP_STATS response.

    Handles multiple possible response formats:
      OK|free=N|total=M|reserved=R
      OK|N free|M total
      OK|N
    """
    if "free" in stats:
        return stats["free"]

    raw = stats.get("raw", "")
    # Try to find a number after "free" or just the first number after OK
    for part in raw.split("|"):
        part = part.strip()
        if part.lower().startswith("free"):
            digits = "".join(c for c in part if c.isdigit())
            if digits:
                return int(digits)
        # Fallback: first pure number after OK
        if part.isdigit():
            return int(part)

    # Last resort: scan for any integer
    import re
    numbers = re.findall(r"\d+", raw)
    if numbers:
        return int(numbers[0])

    raise ValueError(f"Cannot parse free HugePages from HP_STATS: {raw}")


# ---------------------------------------------------------------------------
# Pytest markers
# ---------------------------------------------------------------------------

chaos = pytest.mark.chaos


# ===========================================================================
# TEST 1: Zero-Leak 10,000-Cycle Stress
# ===========================================================================

@requires_qemu
@chaos
def test_buddy_zero_leak_10k():
    """10,000 rapid alloc/dealloc cycles across slots 1-7.

    Strategy:
        - Randomly pick a slot, SLOT_START it (if not already started),
          or SLOT_RESET it (if already started).
        - After all 10,000 ops, reset any remaining active slots.
        - Assert HP_STATS free count returns to baseline (zero leak).
    """
    rng = random.Random(42)  # Deterministic seed for reproducibility

    with VBusClient() as client:
        assert client.ping(), "VBus bridge not responding before test"

        # Baseline HugePage state
        baseline_stats = client.hp_stats()
        baseline_free = parse_hp_free(baseline_stats)
        print(f"\n[BASELINE] HP free: {baseline_free} | raw: {baseline_stats['raw']}")

        active_slots = set()  # Track which slots currently have allocations
        alloc_count = 0
        dealloc_count = 0
        errors = 0

        t_start = time.monotonic()

        for cycle in range(10_000):
            slot = rng.choice(ALLOCATABLE_SLOTS)

            if slot in active_slots:
                # Deallocate
                try:
                    resp = client.slot_reset(slot)
                    active_slots.discard(slot)
                    dealloc_count += 1
                except Exception as e:
                    errors += 1
                    if errors > 50:
                        pytest.fail(
                            f"Too many errors ({errors}) during dealloc at cycle {cycle}: {e}")
            else:
                # Allocate (2MB model size -- triggers HugePage allocation)
                try:
                    resp = client.slot_start(slot, model_id=1, size=2097152,
                                             label=f"chaos_{cycle}")
                    if resp.startswith("OK"):
                        active_slots.add(slot)
                    alloc_count += 1
                except Exception as e:
                    errors += 1
                    if errors > 50:
                        pytest.fail(
                            f"Too many errors ({errors}) during alloc at cycle {cycle}: {e}")

            # Periodic progress (every 2000 cycles)
            if (cycle + 1) % 2000 == 0:
                mid_stats = client.hp_stats()
                mid_free = parse_hp_free(mid_stats)
                elapsed = time.monotonic() - t_start
                print(f"  [cycle {cycle + 1:>5}] allocs={alloc_count} deallocs={dealloc_count} "
                      f"active={len(active_slots)} free_hp={mid_free} "
                      f"errors={errors} elapsed={elapsed:.1f}s")

        # Drain: reset any slots still active
        for slot in sorted(active_slots):
            try:
                client.slot_reset(slot)
                dealloc_count += 1
            except Exception:
                pass
        active_slots.clear()

        # Brief settle time for kernel to complete deferred frees
        time.sleep(0.5)

        # Final HugePage state
        final_stats = client.hp_stats()
        final_free = parse_hp_free(final_stats)
        elapsed = time.monotonic() - t_start

        leak = baseline_free - final_free
        ops_per_sec = 10_000 / elapsed if elapsed > 0 else 0

        print(f"\n{'=' * 60}")
        print(f"  BUDDY ZERO-LEAK 10K SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Total cycles:    10,000")
        print(f"  Allocs:          {alloc_count}")
        print(f"  Deallocs:        {dealloc_count}")
        print(f"  Errors:          {errors}")
        print(f"  Baseline free:   {baseline_free}")
        print(f"  Final free:      {final_free}")
        print(f"  Leak (pages):    {leak}")
        print(f"  Elapsed:         {elapsed:.2f}s")
        print(f"  Ops/sec:         {ops_per_sec:.0f}")
        print(f"{'=' * 60}")

        assert leak == 0, (
            f"HugePage LEAK detected: baseline_free={baseline_free}, "
            f"final_free={final_free}, leak={leak} pages"
        )
        assert errors < 50, f"Too many VBus errors during stress: {errors}"
        assert client.ping(), "VBus bridge died during 10K stress test"


# ===========================================================================
# TEST 2: Concurrent Multi-Slot Alloc/Dealloc
# ===========================================================================

@requires_qemu
@chaos
def test_buddy_concurrent_slots():
    """Rapidly alloc/dealloc across all 7 slots simultaneously using threads.

    Each thread owns one slot and runs 200 alloc/reset cycles independently.
    Tests the lock ordering fix: concurrent commands to different slots must
    not deadlock or corrupt the buddy allocator.
    """
    NUM_CYCLES_PER_SLOT = 200
    slot_results = {}  # slot_id -> {"allocs": N, "deallocs": N, "errors": N}
    barrier = threading.Barrier(len(ALLOCATABLE_SLOTS), timeout=30)

    def worker(slot_id: int):
        """Per-slot worker: connect independently, run alloc/reset cycles."""
        result = {"allocs": 0, "deallocs": 0, "errors": 0}
        client = VBusClient()
        try:
            client.connect()

            # Synchronize all threads to start simultaneously
            barrier.wait()

            for cycle in range(NUM_CYCLES_PER_SLOT):
                try:
                    # Allocate
                    resp = client.slot_start(slot_id, model_id=1, size=2097152,
                                             label=f"conc_s{slot_id}_c{cycle}")
                    if resp.startswith("OK"):
                        result["allocs"] += 1

                    # Small random jitter to increase interleaving
                    time.sleep(random.uniform(0.001, 0.005))

                    # Deallocate
                    resp = client.slot_reset(slot_id)
                    result["deallocs"] += 1

                except Exception:
                    result["errors"] += 1
                    # Try to clean up slot on error before next cycle
                    try:
                        client.slot_reset(slot_id)
                    except Exception:
                        pass
                    time.sleep(0.05)

        except Exception as e:
            result["errors"] += 1
            result["connect_error"] = str(e)
        finally:
            # Ensure slot is clean before disconnect
            try:
                client.slot_reset(slot_id)
            except Exception:
                pass
            client.disconnect()
            time.sleep(0.3)  # QEMU chardev re-listen delay

        slot_results[slot_id] = result

    # Get baseline from a dedicated connection
    with VBusClient() as baseline_client:
        assert baseline_client.ping(), "VBus bridge not responding before concurrent test"
        baseline_stats = baseline_client.hp_stats()
        baseline_free = parse_hp_free(baseline_stats)
        print(f"\n[BASELINE] HP free: {baseline_free}")

    t_start = time.monotonic()

    # Launch one thread per slot
    threads = []
    for slot_id in ALLOCATABLE_SLOTS:
        t = threading.Thread(target=worker, args=(slot_id,), daemon=True)
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    elapsed = time.monotonic() - t_start

    # Brief settle time
    time.sleep(1.0)

    # Check final state from a fresh connection
    with VBusClient() as final_client:
        final_stats = final_client.hp_stats()
        final_free = parse_hp_free(final_stats)

    leak = baseline_free - final_free
    total_allocs = sum(r.get("allocs", 0) for r in slot_results.values())
    total_deallocs = sum(r.get("deallocs", 0) for r in slot_results.values())
    total_errors = sum(r.get("errors", 0) for r in slot_results.values())

    print(f"\n{'=' * 60}")
    print(f"  BUDDY CONCURRENT SLOTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Slots tested:    {len(ALLOCATABLE_SLOTS)} (slots {ALLOCATABLE_SLOTS})")
    print(f"  Cycles/slot:     {NUM_CYCLES_PER_SLOT}")
    print(f"  Total allocs:    {total_allocs}")
    print(f"  Total deallocs:  {total_deallocs}")
    print(f"  Total errors:    {total_errors}")
    for sid in sorted(slot_results):
        r = slot_results[sid]
        print(f"    Slot {sid}: allocs={r['allocs']} deallocs={r['deallocs']} "
              f"errors={r['errors']}"
              + (f" connect_error={r['connect_error']}" if "connect_error" in r else ""))
    print(f"  Baseline free:   {baseline_free}")
    print(f"  Final free:      {final_free}")
    print(f"  Leak (pages):    {leak}")
    print(f"  Elapsed:         {elapsed:.2f}s")
    print(f"{'=' * 60}")

    # All threads must have run
    assert len(slot_results) == len(ALLOCATABLE_SLOTS), (
        f"Only {len(slot_results)}/{len(ALLOCATABLE_SLOTS)} slots completed"
    )

    # Zero HugePage leak
    assert leak == 0, (
        f"HugePage LEAK in concurrent test: baseline={baseline_free}, "
        f"final={final_free}, leak={leak}"
    )

    # Error rate must be acceptable (some transient errors are OK under contention)
    max_acceptable_errors = NUM_CYCLES_PER_SLOT * len(ALLOCATABLE_SLOTS) * 0.10
    assert total_errors < max_acceptable_errors, (
        f"Excessive errors in concurrent test: {total_errors} "
        f"(max acceptable: {max_acceptable_errors:.0f})"
    )


# ===========================================================================
# TEST 3: Fragmentation Resistance
# ===========================================================================

@requires_qemu
@chaos
def test_buddy_fragmentation_resistance():
    """Alternating alloc/dealloc pattern designed to induce fragmentation.

    Pattern per round (50 rounds total):
        1. Alloc slots 1, 3, 5, 7   (odd slots)
        2. Free slots 1, 3          (partial free -- creates holes)
        3. Alloc slots 2, 4         (fill holes with even slots)
        4. Free ALL active slots    (full drain)
        5. Verify HP_STATS unchanged

    This pattern exercises the buddy allocator's coalescing logic by
    creating and destroying fragmentation patterns repeatedly.
    """
    NUM_ROUNDS = 50

    with VBusClient() as client:
        assert client.ping(), "VBus bridge not responding before fragmentation test"

        baseline_stats = client.hp_stats()
        baseline_free = parse_hp_free(baseline_stats)
        print(f"\n[BASELINE] HP free: {baseline_free}")

        total_allocs = 0
        total_deallocs = 0
        errors = 0

        t_start = time.monotonic()

        for rnd in range(NUM_ROUNDS):
            active = set()

            # Step 1: Alloc odd slots (1, 3, 5, 7)
            for slot in [1, 3, 5, 7]:
                try:
                    resp = client.slot_start(slot, model_id=1, size=2097152,
                                             label=f"frag_r{rnd}_s{slot}")
                    if resp.startswith("OK"):
                        active.add(slot)
                    total_allocs += 1
                except Exception:
                    errors += 1

            # Step 2: Free slots 1, 3 (creates fragmentation holes)
            for slot in [1, 3]:
                if slot in active:
                    try:
                        client.slot_reset(slot)
                        active.discard(slot)
                        total_deallocs += 1
                    except Exception:
                        errors += 1

            # Step 3: Alloc even slots (2, 4) into the holes
            for slot in [2, 4]:
                try:
                    resp = client.slot_start(slot, model_id=1, size=2097152,
                                             label=f"frag_r{rnd}_s{slot}")
                    if resp.startswith("OK"):
                        active.add(slot)
                    total_allocs += 1
                except Exception:
                    errors += 1

            # Step 4: Free ALL remaining active slots
            for slot in sorted(active):
                try:
                    client.slot_reset(slot)
                    total_deallocs += 1
                except Exception:
                    errors += 1
            active.clear()

            # Step 5: Periodic HP check (every 10 rounds)
            if (rnd + 1) % 10 == 0:
                mid_stats = client.hp_stats()
                mid_free = parse_hp_free(mid_stats)
                mid_leak = baseline_free - mid_free
                elapsed = time.monotonic() - t_start
                print(f"  [round {rnd + 1:>3}] allocs={total_allocs} "
                      f"deallocs={total_deallocs} free_hp={mid_free} "
                      f"leak={mid_leak} errors={errors} elapsed={elapsed:.1f}s")
                if mid_leak > 0:
                    print(f"    WARNING: mid-test leak of {mid_leak} pages detected")

        # Brief settle time
        time.sleep(0.5)

        final_stats = client.hp_stats()
        final_free = parse_hp_free(final_stats)
        elapsed = time.monotonic() - t_start
        leak = baseline_free - final_free
        ops_total = total_allocs + total_deallocs

        print(f"\n{'=' * 60}")
        print(f"  BUDDY FRAGMENTATION RESISTANCE SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Rounds:          {NUM_ROUNDS}")
        print(f"  Pattern:         alloc(1,3,5,7) -> free(1,3) -> alloc(2,4) -> free(all)")
        print(f"  Total allocs:    {total_allocs}")
        print(f"  Total deallocs:  {total_deallocs}")
        print(f"  Total ops:       {ops_total}")
        print(f"  Errors:          {errors}")
        print(f"  Baseline free:   {baseline_free}")
        print(f"  Final free:      {final_free}")
        print(f"  Leak (pages):    {leak}")
        print(f"  Elapsed:         {elapsed:.2f}s")
        if elapsed > 0:
            print(f"  Ops/sec:         {ops_total / elapsed:.0f}")
        print(f"{'=' * 60}")

        assert leak == 0, (
            f"Fragmentation LEAK detected: baseline_free={baseline_free}, "
            f"final_free={final_free}, leak={leak} pages. "
            f"Buddy allocator may not be coalescing freed blocks properly."
        )
        assert errors < NUM_ROUNDS, (
            f"Too many errors during fragmentation test: {errors}/{NUM_ROUNDS} rounds"
        )
        assert client.ping(), "VBus bridge died during fragmentation test"
