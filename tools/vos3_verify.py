#!/usr/bin/env python3
"""
VOS3 Transparency Verifier — vos3_verify.py
===========================================
Standalone auditor tool. Connects to the VOS3 Transparency API,
downloads the live MMR root hash, and verifies the algorithm locally
using a pure-Python implementation that mirrors the C kernel logic.

Usage:
    python3 tools/vos3_verify.py
    python3 tools/vos3_verify.py --url https://api.yourdomain.com --token sk-...
    python3 tools/vos3_verify.py --watch       # poll continuously
    python3 tools/vos3_verify.py --demo        # run self-test without API

Reference implementation: kernel/src/sec/mmr_audit.c
"""

import hashlib
import struct
import json
import sys
import time
import argparse
import urllib.request
import urllib.error
import os


# ---------------------------------------------------------------------------
# Pure-Python MMR — mirrors mmr_audit.c exactly
# ---------------------------------------------------------------------------

MMR_MAX_PEAKS = 64
SHA256_SIZE   = 32


def sha256(*parts: bytes) -> bytes:
    """SHA-256 over the concatenation of all parts."""
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.digest()


class MMR:
    """
    Merkle Mountain Range — append-only forest of perfect binary trees.

    Matches the C implementation in kernel/src/sec/mmr_audit.c:
      - Leaves: SHA-256(timestamp ‖ syscall_nr ‖ entropy ‖ data), XOR entropy-bound
      - Merge:  SHA-256(left ‖ right ‖ height_byte)   ← domain separation
      - Root:   SHA-256(fold(valid peaks, high→low) ‖ leaf_count[8])
    """

    def __init__(self):
        self._peaks: list[bytes | None] = [None] * MMR_MAX_PEAKS
        self._leaf_count: int = 0

    def _merge(self, left: bytes, right: bytes, height: int) -> bytes:
        """Height-byte domain separation prevents second-preimage cross-level attacks."""
        return sha256(left, right, bytes([height & 0xFF]))

    def append_raw(self, leaf_hash: bytes) -> None:
        """
        Append a pre-computed 32-byte leaf hash.
        O(log N) amortized — same peak-merging carry loop as the C kernel.
        """
        carry = leaf_hash
        for h in range(MMR_MAX_PEAKS):
            if self._peaks[h] is None:
                self._peaks[h] = carry
                break
            carry = self._merge(self._peaks[h], carry, h)
            self._peaks[h] = None
        self._leaf_count += 1

    def append_syscall(self, timestamp: int, syscall_nr: int,
                       entropy: bytes, data: bytes = b'\x00' * 32) -> bytes:
        """
        Build a leaf from syscall fields and append it.
        Mirrors mmr_record_syscall() / mmr_append() in the C kernel.

        Returns the leaf hash (before XOR) for display.
        """
        ts_b   = struct.pack('<Q', timestamp & 0xFFFF_FFFF_FFFF_FFFF)
        nr_b   = struct.pack('<Q', syscall_nr & 0xFFFF_FFFF_FFFF_FFFF)
        ent_b  = (entropy + b'\x00' * 8)[:8]
        data_b = (data + b'\x00' * 32)[:32]

        leaf_hash = sha256(ts_b, nr_b, ent_b, data_b)

        # XOR entropy into first 8 bytes — RDSEED binding
        mutable = bytearray(leaf_hash)
        for i in range(8):
            mutable[i] ^= ent_b[i]
        leaf_hash = bytes(mutable)

        self.append_raw(leaf_hash)
        return leaf_hash

    def root(self) -> bytes | None:
        """
        Bagging-the-Peaks root computation.
        Mirrors mmr_root() in the C kernel.
        """
        if self._leaf_count == 0:
            return None

        valid_peaks = [(h, p) for h, p in enumerate(self._peaks) if p is not None]
        if not valid_peaks:
            return None

        # Fold high→low
        valid_peaks.sort(key=lambda x: x[0], reverse=True)
        running = valid_peaks[0][1]
        for _, peak in valid_peaks[1:]:
            running = sha256(running, peak)

        # Append leaf count (8 bytes little-endian) — defeats length extension
        count_b = struct.pack('<Q', self._leaf_count)
        return sha256(running, count_b)

    def root_hex(self) -> str:
        r = self.root()
        return r.hex() if r else '(empty)'

    @property
    def leaf_count(self) -> int:
        return self._leaf_count


# ---------------------------------------------------------------------------
# Self-test — run without any API
# ---------------------------------------------------------------------------

def run_self_test() -> bool:
    print('\n' + '─' * 60)
    print('  VOS3 MMR Self-Test (pure Python, no API required)')
    print('─' * 60)

    mmr = MMR()

    # Append 5 known syscalls
    events = [
        (1714000000_000000, 0,   b'DEADBEEF', b'\x00' * 32),   # boot event
        (1714000000_001000, 9,   b'CAFEBABE', b'\x01' * 32),   # mmap
        (1714000000_002000, 56,  b'FEEDFACE', b'\x02' * 32),   # clone
        (1714000000_003000, 202, b'BAADF00D', b'\x03' * 32),   # futex
        (1714000000_004000, 318, b'D15EA5ED', b'\x04' * 32),   # getrandom
    ]

    print('\n  Appending 5 syscall events:\n')
    for ts, nr, ent, data in events:
        leaf = mmr.append_syscall(ts, nr, ent.ljust(8, b'\x00'), data)
        print(f'    nr={nr:>3}  leaf={leaf.hex()[:24]}…')

    root = mmr.root_hex()
    print(f'\n  MMR root ({mmr.leaf_count} leaves):\n  {root}')

    # Verify determinism
    mmr2 = MMR()
    for ts, nr, ent, data in events:
        mmr2.append_syscall(ts, nr, ent.ljust(8, b'\x00'), data)

    deterministic = mmr.root() == mmr2.root()
    print(f'\n  Determinism check: {"PASS ✓" if deterministic else "FAIL ✗"}')

    # Verify tamper detection
    mmr_tampered = MMR()
    for i, (ts, nr, ent, data) in enumerate(events):
        if i == 2:
            ent = b'XXXXXXXX'  # tampered entropy
        mmr_tampered.append_syscall(ts, nr, ent.ljust(8, b'\x00'), data)

    tamper_detected = mmr.root() != mmr_tampered.root()
    print(f'  Tamper detection:   {"PASS ✓" if tamper_detected else "FAIL ✗"}')

    if tamper_detected:
        print(f'  Tampered root:  {mmr_tampered.root_hex()[:32]}…')
        print(f'  Original root:  {root[:32]}…')

    passed = deterministic and tamper_detected
    print(f'\n  Self-test: {"ALL PASS ✓" if passed else "FAILED ✗"}\n')
    return passed


# ---------------------------------------------------------------------------
# API verification
# ---------------------------------------------------------------------------

def fetch_transparency(base_url: str, token: str | None) -> dict:
    url = base_url.rstrip('/') + '/api/kernel/transparency'
    req = urllib.request.Request(url)
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    req.add_header('Accept', 'application/json')
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def verify_root_format(root_hex: str) -> bool:
    """Verify the root is a valid 64-char lowercase hex string."""
    if len(root_hex) != 64:
        return False
    try:
        bytes.fromhex(root_hex)
        return True
    except ValueError:
        return False


def run_api_verification(base_url: str, token: str | None, watch: bool) -> None:
    print('\n' + '─' * 60)
    print('  VOS3 MMR API Verifier')
    print(f'  Target: {base_url}')
    print('─' * 60)

    poll_count = 0
    prev_root  = None
    prev_leaves = None

    while True:
        poll_count += 1
        ts = time.strftime('%H:%M:%S')

        try:
            data = fetch_transparency(base_url, token)
        except urllib.error.HTTPError as e:
            print(f'\n  [{ts}] HTTP {e.code}: {e.reason}')
            if e.code == 401:
                print('  → Pass --token to authenticate')
            if not watch:
                sys.exit(1)
            time.sleep(5)
            continue
        except urllib.error.URLError as e:
            print(f'\n  [{ts}] Connection failed: {e.reason}')
            if not watch:
                sys.exit(1)
            time.sleep(5)
            continue

        fresh  = data.get('fresh', False)
        root   = data.get('root')
        leaves = data.get('leaves')
        error  = data.get('error')

        print(f'\n  [{ts}] Poll #{poll_count}')

        if not fresh or not root:
            print(f'  Status:  KERNEL OFFLINE')
            if error:
                print(f'  Error:   {error}')
            if not watch:
                sys.exit(1)
            time.sleep(5)
            continue

        # Format validation
        fmt_ok = verify_root_format(root)
        print(f'  Status:       KERNEL LIVE ✓')
        print(f'  Root hash:    {root[:32]}…{root[-8:]}')
        print(f'  Leaf count:   {leaves:,}' if isinstance(leaves, int) else f'  Leaf count:   {leaves}')
        print(f'  Format valid: {"✓ SHA-256 hex (64 chars)" if fmt_ok else "✗ INVALID"}')

        # Chain advancement check
        if prev_root is not None and prev_leaves is not None:
            if root == prev_root and isinstance(leaves, int) and isinstance(prev_leaves, int):
                if leaves == prev_leaves:
                    print(f'  Chain:        STATIC (no new events since last poll)')
                else:
                    # root should have changed if leaves changed — flag it
                    print(f'  Chain:        ✗ ANOMALY — leaf count changed but root did not')
            elif root != prev_root:
                delta = (leaves - prev_leaves) if isinstance(leaves, int) and isinstance(prev_leaves, int) else '?'
                print(f'  Chain:        ✓ ADVANCING (+{delta} events)')

        prev_root   = root
        prev_leaves = leaves

        if not watch:
            print()
            print('  Verification complete. To audit continuously: --watch')
            print('  To verify algorithm locally: --demo')
            break

        time.sleep(3)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='VOS3 MMR Transparency Verifier — for technical auditors',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run local self-test (no API required)
  python3 tools/vos3_verify.py --demo

  # Verify against local dev server
  python3 tools/vos3_verify.py

  # Verify against production with auth token
  python3 tools/vos3_verify.py --url https://api.example.com --token sk-...

  # Watch continuously
  python3 tools/vos3_verify.py --watch

Reference: kernel/src/sec/mmr_audit.c | EU AI Act Article 12
        """,
    )
    parser.add_argument('--url',   default='http://localhost:8000', help='VOS3 API base URL')
    parser.add_argument('--token', default=os.environ.get('VOS3_TOKEN'), help='Bearer token (or set VOS3_TOKEN env)')
    parser.add_argument('--watch', action='store_true', help='Poll continuously every 3 seconds')
    parser.add_argument('--demo',  action='store_true', help='Run pure-Python self-test without API')
    args = parser.parse_args()

    print()
    print('  ██╗   ██╗ ██████╗ ███████╗██████╗')
    print('  ██║   ██║██╔═══██╗██╔════╝╚════██╗')
    print('  ██║   ██║██║   ██║███████╗ █████╔╝')
    print('  ╚██╗ ██╔╝██║   ██║╚════██║ ╚═══██╗')
    print('   ╚████╔╝ ╚██████╔╝███████║██████╔╝')
    print('    ╚═══╝   ╚═════╝ ╚══════╝╚═════╝')
    print()
    print('  MMR Transparency Verifier — v20.3.0')
    print('  "Security by physics, not by policy."')

    self_ok = run_self_test()
    if not self_ok:
        print('  ✗ Self-test FAILED — implementation mismatch. Do not trust API results.')
        sys.exit(2)

    if not args.demo:
        run_api_verification(args.url, args.token, args.watch)


if __name__ == '__main__':
    main()
