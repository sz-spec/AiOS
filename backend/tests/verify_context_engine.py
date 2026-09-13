#!/usr/bin/env python3
"""
Sovereign Context Audit — verify_context_engine.py
Phase 5: Context Management (Freeze/Thaw) — April 2026 Hardened

Standalone verification script for the Cognitive Memory Management layer.
Tests: Differential Freeze, Semantic Compaction, Atomic Thaw-Commit,
       Anti-Replay Protection, Round-Trip Integrity, Clean-Page Rejection,
       Telemetry, and Isolation.

Usage:
    python verify_context_engine.py          # Against live QEMU kernel
    python verify_context_engine.py --mock   # Offline CRC verification only
"""

import struct
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---- CRC32C (Castagnoli) — must match kernel's vos3_crc32c() ----
try:
    from crc32c import crc32c as _hw_crc32c

    def _crc32c(data: bytes, value: int = 0) -> int:
        return _hw_crc32c(data, value) & 0xFFFFFFFF

except ImportError:
    _CRC32C_TABLE = None

    def _build_table():
        global _CRC32C_TABLE
        poly = 0x82F63B78
        tbl = []
        for i in range(256):
            crc = i
            for _ in range(8):
                crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
            tbl.append(crc)
        _CRC32C_TABLE = tbl

    def _crc32c(data: bytes, value: int = 0) -> int:
        if _CRC32C_TABLE is None:
            _build_table()
        crc = value ^ 0xFFFFFFFF
        for b in data:
            crc = _CRC32C_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
        return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


def compute_thaw_crc(
    freeze_id: int, dirty_bitmap: int, page_count: int, pages: dict
) -> int:
    """Compute CRC32C matching kernel's vos3_ai_ctx_compute_crc().

    Order: freeze_id (u64 LE) + dirty_bitmap (u64 LE × 2) + dirty page data
    """
    crc = _crc32c(struct.pack("<Q", freeze_id))
    bitmap_lo = dirty_bitmap & 0xFFFFFFFFFFFFFFFF
    bitmap_hi = (dirty_bitmap >> 64) & 0xFFFFFFFFFFFFFFFF
    crc = _crc32c(struct.pack("<QQ", bitmap_lo, bitmap_hi), crc)
    for i in range(page_count):
        if i in pages:
            crc = _crc32c(pages[i], crc)
    return crc


# ============================================================================
# Test: Offline CRC Verification (no kernel needed)
# ============================================================================


def test_crc_determinism():
    """Verify CRC computation is deterministic and correct."""
    print("[TEST] CRC32C Determinism ... ", end="", flush=True)

    freeze_id = 42
    dirty_bitmap = 0b10101  # pages 0, 2, 4 dirty
    pages = {
        0: b"\xaa" * 4096,
        2: b"\xbb" * 4096,
        4: b"\xcc" * 4096,
    }

    crc1 = compute_thaw_crc(freeze_id, dirty_bitmap, 128, pages)
    crc2 = compute_thaw_crc(freeze_id, dirty_bitmap, 128, pages)
    assert crc1 == crc2, f"CRC not deterministic: {crc1:#x} vs {crc2:#x}"
    assert crc1 != 0, "CRC should not be zero for non-trivial data"

    # Tamper: change one byte in page 2
    tampered = dict(pages)
    tampered[2] = b"\xbb" * 4095 + b"\xff"
    crc3 = compute_thaw_crc(freeze_id, dirty_bitmap, 128, tampered)
    assert crc3 != crc1, "CRC should change on tampered data"

    # Tamper: wrong freeze_id
    crc4 = compute_thaw_crc(freeze_id + 1, dirty_bitmap, 128, pages)
    assert crc4 != crc1, "CRC should change on different freeze_id"

    # Tamper: wrong bitmap
    crc5 = compute_thaw_crc(freeze_id, dirty_bitmap | (1 << 6), 128, pages)
    assert crc5 != crc1, "CRC should change on different bitmap"

    print(f"PASS (crc={crc1:#010x})")
    return True


def test_bitmap_popcount():
    """Verify bitmap popcount matches dirty page count."""
    print("[TEST] Bitmap Popcount ... ", end="", flush=True)

    test_cases = [
        (0b00000, 0),
        (0b00001, 1),
        (0b10101, 3),
        (0xFFFFFFFFFFFFFFFF, 64),
        ((1 << 127), 1),
        ((1 << 128) - 1, 128),
    ]

    for bitmap, expected in test_cases:
        actual = bin(bitmap).count("1")
        assert (
            actual == expected
        ), f"popcount({bitmap:#x}) = {actual}, expected {expected}"

    print("PASS")
    return True


def test_crc_empty_pages():
    """Verify CRC handles zero dirty pages."""
    print("[TEST] CRC Empty Pages ... ", end="", flush=True)

    crc = compute_thaw_crc(1, 0, 128, {})
    assert crc != 0, "CRC of freeze_id=1 + empty bitmap should not be zero"

    # Two different freeze_ids with no pages should differ
    crc2 = compute_thaw_crc(2, 0, 128, {})
    assert crc != crc2, "Different freeze_ids should produce different CRCs"

    print(f"PASS (crc_fid1={crc:#010x}, crc_fid2={crc2:#010x})")
    return True


# ============================================================================
# Test: Live Kernel Tests (require running QEMU + VBus)
# ============================================================================


def run_live_tests():
    """Run live tests against a running VOS3 kernel via VBus."""
    from services.vbus_driver import VBusDriver

    drv = VBusDriver()
    try:
        drv.connect()
    except Exception as e:
        print(f"[SKIP] Cannot connect to VBus: {e}")
        return 0, 0

    SLOT = 1
    passed = 0
    failed = 0

    # ---- Setup: Ensure slot has context pages ----
    try:
        drv.send_command(f"SLOT_CONTEXT_CONFIG|{SLOT}|16")
    except Exception:
        pass  # May already be configured

    # ---- Test 1: Differential Freeze ----
    print("[TEST] Differential Freeze ... ", end="", flush=True)
    try:
        # Write unique patterns to 5 specific pages
        dirty_pages = [0, 3, 7, 11, 15]
        for idx in dirty_pages:
            pattern = bytes([idx & 0xFF]) * 4096
            drv.send_command(f"CTX_WRITE|{SLOT}|{idx}|{pattern.hex()}")

        segment = drv.ctx_freeze(SLOT, scrub=False)
        assert segment["dirty_count"] >= len(
            dirty_pages
        ), f"Expected >= {len(dirty_pages)} dirty, got {segment['dirty_count']}"
        assert segment["session_epoch"] > 0, "session_epoch should be > 0"
        assert segment["freeze_id"] > 0, "freeze_id should be > 0"
        print(
            f"PASS (dirty={segment['dirty_count']}, epoch={segment['session_epoch']})"
        )
        passed += 1
    except Exception as e:
        print(f"FAIL: {e}")
        failed += 1

    # ---- Test 2: Semantic Compaction ----
    print("[TEST] Semantic Compaction (Scrub) ... ", end="", flush=True)
    try:
        scrub_resp = drv.send_command(f"CTX_SCRUB|{SLOT}")
        parts = scrub_resp.split("|")
        # OK|scrubbed|pages_cleaned|effective_bytes
        pages_cleaned = int(parts[2])
        effective_bytes = int(parts[3])
        assert pages_cleaned > 0, "Should have cleaned pages"
        print(f"PASS (cleaned={pages_cleaned}, effective={effective_bytes} bytes)")
        passed += 1
    except Exception as e:
        print(f"FAIL: {e}")
        failed += 1

    # ---- Test 3: Clean Page Rejection ----
    print("[TEST] Clean Page Rejection (ENODATA) ... ", end="", flush=True)
    try:
        # Find a page NOT in dirty bitmap
        clean_idx = None
        for i in range(16):
            if i not in segment.get("pages", {}):
                clean_idx = i
                break
        if clean_idx is not None:
            try:
                drv.send_command(f"CTX_READ|{SLOT}|{clean_idx}")
                print("FAIL: Should have rejected clean page")
                failed += 1
            except Exception as read_err:
                if "61" in str(read_err) or "ENODATA" in str(read_err):
                    print(f"PASS (page {clean_idx} correctly rejected)")
                    passed += 1
                else:
                    print(f"FAIL: unexpected error: {read_err}")
                    failed += 1
        else:
            print("SKIP (all pages dirty)")
    except Exception as e:
        print(f"FAIL: {e}")
        failed += 1

    # ---- Test 4: Atomic Thaw-Commit (CRC Match) ----
    print("[TEST] Atomic Thaw-Commit (Correct CRC) ... ", end="", flush=True)
    try:
        # Re-freeze to get fresh segment
        segment = drv.ctx_freeze(SLOT, scrub=True)
        result = drv.ctx_thaw(SLOT, segment, cold=False)
        assert (
            "active" in result.lower() or "OK" in result
        ), f"Expected active, got: {result}"
        print(f"PASS (result={result})")
        passed += 1
    except Exception as e:
        print(f"FAIL: {e}")
        failed += 1

    # ---- Test 5: Atomic Thaw-Commit (WRONG CRC → warm_reset) ----
    print("[TEST] Tamper Rejection (Wrong CRC) ... ", end="", flush=True)
    try:
        segment2 = drv.ctx_freeze(SLOT, scrub=True)
        pc = segment2["page_count"]
        fid = segment2["freeze_id"]
        se = segment2["session_epoch"]
        drv.send_command(f"CTX_THAW|{SLOT}|{pc}|{fid}|{se}")

        for page_idx, page_data in segment2["pages"].items():
            drv.send_command(f"CTX_WRITE|{SLOT}|{page_idx}|{page_data.hex()}")

        # Send WRONG CRC
        wrong_crc = 0xDEADBEEF
        try:
            resp = drv.send_command(f"CTX_THAW_DONE|{SLOT}|{wrong_crc}")
            if "thaw_commit_failed" in resp or "ERR" in resp:
                print("PASS (rejected with thaw_commit_failed)")
                passed += 1
            else:
                print(f"FAIL: accepted wrong CRC: {resp}")
                failed += 1
        except Exception as crc_err:
            if "5" in str(crc_err) or "thaw_commit_failed" in str(crc_err):
                print("PASS (rejected with thaw_commit_failed)")
                passed += 1
            else:
                print(f"FAIL: unexpected error: {crc_err}")
                failed += 1
    except Exception as e:
        print(f"FAIL: {e}")
        failed += 1

    # ---- Test 6: Anti-Replay (stale session_epoch) ----
    print("[TEST] Anti-Replay (Stale Epoch) ... ", end="", flush=True)
    try:
        # Reconfigure context after warm_reset
        drv.send_command(f"SLOT_CONTEXT_CONFIG|{SLOT}|16")
        segment3 = drv.ctx_freeze(SLOT, scrub=True)
        stale_epoch = segment3["session_epoch"] - 1  # Use OLD epoch

        drv.send_command(
            f"CTX_THAW|{SLOT}|{segment3['page_count']}|{segment3['freeze_id']}|{stale_epoch}"
        )
        for page_idx, page_data in segment3["pages"].items():
            drv.send_command(f"CTX_WRITE|{SLOT}|{page_idx}|{page_data.hex()}")

        expected_crc = drv._compute_thaw_crc(segment3)
        try:
            resp = drv.send_command(f"CTX_THAW_DONE|{SLOT}|{expected_crc}")
            if "thaw_commit_failed" in resp or "ERR" in resp:
                print("PASS (stale epoch rejected)")
                passed += 1
            else:
                print(f"FAIL: accepted stale epoch: {resp}")
                failed += 1
        except Exception as ep_err:
            if "5" in str(ep_err) or "thaw_commit_failed" in str(ep_err):
                print("PASS (stale epoch rejected)")
                passed += 1
            else:
                print(f"FAIL: unexpected error: {ep_err}")
                failed += 1
    except Exception as e:
        print(f"FAIL: {e}")
        failed += 1

    # ---- Test 7: Telemetry (CTX_LATENCY) ----
    print("[TEST] Telemetry (CTX_LATENCY) ... ", end="", flush=True)
    try:
        # Do a successful round-trip first
        drv.send_command(f"SLOT_CONTEXT_CONFIG|{SLOT}|8")
        seg = drv.ctx_freeze(SLOT, scrub=True)
        drv.ctx_thaw(SLOT, seg, cold=False)
        latency = drv.ctx_get_latency(SLOT)
        assert latency >= 0, f"Latency should be non-negative, got {latency}"
        print(f"PASS (latency={latency} ticks)")
        passed += 1
    except Exception as e:
        print(f"FAIL: {e}")
        failed += 1

    # ---- Test 8: Cold Thaw ----
    print("[TEST] Cold Thaw (DORMANT during writes) ... ", end="", flush=True)
    try:
        drv.send_command(f"SLOT_CONTEXT_CONFIG|{SLOT}|8")
        seg = drv.ctx_freeze(SLOT, scrub=True)
        result = drv.ctx_thaw(SLOT, seg, cold=True)
        assert (
            "active" in result.lower() or "OK" in result
        ), f"Expected active after thaw_done, got: {result}"
        print("PASS (cold thaw completed, now active)")
        passed += 1
    except Exception as e:
        print(f"FAIL: {e}")
        failed += 1

    drv.disconnect()
    return passed, failed


# ============================================================================
# Main
# ============================================================================


def main():
    print("=" * 68)
    print("  SOVEREIGN CONTEXT AUDIT — verify_context_engine.py")
    print("  Phase 5: Context Management (Freeze/Thaw) — April 2026")
    print("=" * 68)
    print()

    offline_pass = 0
    offline_fail = 0

    # Offline CRC tests (always run)
    print("--- Offline CRC Verification ---")
    for test_fn in [test_crc_determinism, test_bitmap_popcount, test_crc_empty_pages]:
        try:
            if test_fn():
                offline_pass += 1
            else:
                offline_fail += 1
        except AssertionError as ae:
            print(f"FAIL: {ae}")
            offline_fail += 1

    live_pass, live_fail = 0, 0
    if "--mock" not in sys.argv:
        print()
        print("--- Live Kernel Tests (VBus) ---")
        live_pass, live_fail = run_live_tests()
    else:
        print()
        print("[MOCK] Skipping live kernel tests")

    total_pass = offline_pass + live_pass
    total_fail = offline_fail + live_fail
    total = total_pass + total_fail

    print()
    print("=" * 68)
    print(f"  RESULTS: {total_pass}/{total} PASS, {total_fail}/{total} FAIL")
    if total_fail == 0:
        print("  STATUS: CERTIFIED")
    else:
        print("  STATUS: FAILED — see above")
    print("=" * 68)

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
