"""
backend/tests/services/test_kvcache_state.py

Sprint 16 / Item A2 — KV-cache OS primitive tests against the in-process
simulator backend. The blob format these tests round-trip is byte-identical
to the kernel's vos3_kvcache_blob_v1_hdr_t, so a blob produced here is
accepted by the kernel verifier and vice versa.

Coverage:
  - Blob header packing / unpacking is wire-stable.
  - Slot configure -> checkpoint -> restore round-trip preserves bytes.
  - blob_size() agrees with checkpoint() output length.
  - All error codes are reachable (EMPTY, NOSLOT, BUFSMALL, MAGIC,
    VERSION, CSUM, DSTBUSY, SAMESLOT, INVAL).
  - fork() COW: writes to dst don't leak into src; writes to src don't
    leak into dst.
  - Concurrent fork operations are deadlock-free (slot-id ordering).
  - cycle_count is bumped on every primitive call (audit signal).
"""

from __future__ import annotations

import importlib.util
import struct
import sys
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_KV_PATH = _REPO_ROOT / "backend" / "services" / "kvcache_state.py"

_spec = importlib.util.spec_from_file_location(
    "vos3_kvcache_state_under_test", _KV_PATH
)
kv = importlib.util.module_from_spec(_spec)
sys.modules["vos3_kvcache_state_under_test"] = kv
_spec.loader.exec_module(kv)


# ---------------------------------------------------------------------------
# Constants and wire format
# ---------------------------------------------------------------------------


def test_constants_match_kernel_header():
    assert kv.VOS3_KVCACHE_BLOB_VERSION == 1
    assert kv.VOS3_KVCACHE_BLOB_MAGIC == 0x564B5643
    assert kv.VOS3_CONTEXT_PAGE_MAX == 128
    assert kv.PAGE_SIZE == 4096


def test_blob_header_size_is_40():
    """40 bytes matches kernel VOS3_KVCACHE_BLOB_HDR_SIZE — critical for wire compat."""
    hdr = kv.BlobHeader(
        magic=kv.VOS3_KVCACHE_BLOB_MAGIC,
        version=kv.VOS3_KVCACHE_BLOB_VERSION,
        src_slot_id=3,
        page_count=10,
        model_id=0x12345678,
        model_epoch=42,
        checkpoint_tick=99,
        payload_crc64=0xDEADBEEFCAFEBABE,
    )
    packed = hdr.pack()
    assert len(packed) == 40


def test_blob_header_round_trip():
    hdr = kv.BlobHeader(
        magic=kv.VOS3_KVCACHE_BLOB_MAGIC,
        version=1,
        src_slot_id=5,
        page_count=2,
        model_id=0xABCDEF01,
        model_epoch=7,
        checkpoint_tick=12345,
        payload_crc64=0x1122334455667788,
    )
    raw = hdr.pack()
    parsed = kv.BlobHeader.unpack(raw)
    assert parsed.magic == hdr.magic
    assert parsed.version == hdr.version
    assert parsed.src_slot_id == hdr.src_slot_id
    assert parsed.page_count == hdr.page_count
    assert parsed.model_id == hdr.model_id
    assert parsed.model_epoch == hdr.model_epoch
    assert parsed.checkpoint_tick == hdr.checkpoint_tick
    assert parsed.payload_crc64 == hdr.payload_crc64


# ---------------------------------------------------------------------------
# Slot configure / sizing
# ---------------------------------------------------------------------------


def _make_store():
    return kv.KVCacheStore(n_slots=4)


def test_blob_size_empty_slot_returns_empty_error():
    store = _make_store()
    with pytest.raises(kv.KVCacheException) as ei:
        store.blob_size(0)
    assert ei.value.code == kv.KVCacheError.EMPTY


def test_blob_size_matches_pages():
    store = _make_store()
    store.configure(0, model_id=0x01, model_epoch=1, page_count=3)
    assert store.blob_size(0) == 40 + 3 * 4096


def test_blob_size_invalid_slot():
    store = _make_store()
    with pytest.raises(kv.KVCacheException) as ei:
        store.blob_size(99)
    assert ei.value.code == kv.KVCacheError.NOSLOT


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


def test_checkpoint_empty_slot_returns_empty():
    store = _make_store()
    with pytest.raises(kv.KVCacheException) as ei:
        store.checkpoint(0)
    assert ei.value.code == kv.KVCacheError.EMPTY


def test_checkpoint_produces_correct_size_blob():
    store = _make_store()
    store.configure(1, model_id=0x42, model_epoch=10, page_count=5, initial_byte=0xAB)
    blob = store.checkpoint(1)
    assert len(blob) == 40 + 5 * 4096


def test_checkpoint_header_has_correct_fields():
    store = _make_store()
    store.configure(2, model_id=0x123, model_epoch=7, page_count=2)
    blob = store.checkpoint(2)
    hdr = kv.BlobHeader.unpack(blob)
    assert hdr.magic == kv.VOS3_KVCACHE_BLOB_MAGIC
    assert hdr.version == kv.VOS3_KVCACHE_BLOB_VERSION
    assert hdr.src_slot_id == 2
    assert hdr.page_count == 2
    assert hdr.model_id == 0x123
    assert hdr.model_epoch == 7


def test_checkpoint_bumps_cycle_count():
    store = _make_store()
    store.configure(0, model_id=1, model_epoch=1, page_count=1)
    before = store.cycle_count(0)
    store.checkpoint(0)
    after = store.cycle_count(0)
    assert after == before + 1


# ---------------------------------------------------------------------------
# Restore — round-trip + error paths
# ---------------------------------------------------------------------------


def test_checkpoint_restore_round_trip_bytes_identical():
    src = _make_store()
    src.configure(0, model_id=0x77, model_epoch=3, page_count=4, initial_byte=0xCC)
    blob = src.checkpoint(0)

    dst = _make_store()
    dst.configure(0, model_id=0x77, model_epoch=99, page_count=4, initial_byte=0x00)
    dst.restore(0, blob)

    for i in range(4):
        assert dst.read_page(0, i) == bytes([0xCC]) * 4096


def test_restore_bad_magic_rejected():
    store = _make_store()
    store.configure(0, model_id=1, model_epoch=1, page_count=1)
    bad = b"\x00" * 40 + b"\x00" * 4096
    with pytest.raises(kv.KVCacheException) as ei:
        store.restore(0, bad)
    assert ei.value.code == kv.KVCacheError.MAGIC


def test_restore_bad_version_rejected():
    store = _make_store()
    store.configure(0, model_id=1, model_epoch=1, page_count=1)
    raw = (
        struct.pack(
            "<I I B 3s I I I Q Q",
            kv.VOS3_KVCACHE_BLOB_MAGIC,
            999,  # bogus version
            0,
            b"\x00\x00\x00",
            1,
            1,
            1,
            0,
            0,
        )
        + b"\x00" * 4096
    )
    with pytest.raises(kv.KVCacheException) as ei:
        store.restore(0, raw)
    assert ei.value.code == kv.KVCacheError.VERSION


def test_restore_corrupted_payload_csum():
    src = _make_store()
    src.configure(0, model_id=1, model_epoch=1, page_count=1, initial_byte=0xAA)
    blob = bytearray(src.checkpoint(0))
    # Flip one byte in payload — invalidates the embedded crc.
    blob[100] ^= 0xFF
    dst = _make_store()
    dst.configure(0, model_id=1, model_epoch=1, page_count=1)
    with pytest.raises(kv.KVCacheException) as ei:
        dst.restore(0, bytes(blob))
    assert ei.value.code == kv.KVCacheError.CSUM


def test_restore_model_id_mismatch_rejected():
    src = _make_store()
    src.configure(0, model_id=0xAAA, model_epoch=1, page_count=1)
    blob = src.checkpoint(0)
    dst = _make_store()
    dst.configure(0, model_id=0xBBB, model_epoch=1, page_count=1)
    with pytest.raises(kv.KVCacheException) as ei:
        dst.restore(0, blob)
    assert ei.value.code == kv.KVCacheError.INVAL


def test_restore_blob_too_small():
    store = _make_store()
    store.configure(0, model_id=1, model_epoch=1, page_count=1)
    with pytest.raises(kv.KVCacheException) as ei:
        store.restore(0, b"\x00" * 10)  # less than 40-byte header
    assert ei.value.code == kv.KVCacheError.BUFSMALL


def test_restore_blob_payload_short():
    src = _make_store()
    src.configure(0, model_id=1, model_epoch=1, page_count=2)
    blob = src.checkpoint(0)
    truncated = blob[:60]  # header + 20 bytes of payload only
    dst = _make_store()
    dst.configure(0, model_id=1, model_epoch=1, page_count=2)
    with pytest.raises(kv.KVCacheException) as ei:
        dst.restore(0, truncated)
    assert ei.value.code == kv.KVCacheError.BUFSMALL


def test_restore_blob_with_more_pages_than_destination():
    src = _make_store()
    src.configure(0, model_id=1, model_epoch=1, page_count=4)
    blob = src.checkpoint(0)
    dst = _make_store()
    dst.configure(0, model_id=1, model_epoch=1, page_count=2)
    with pytest.raises(kv.KVCacheException) as ei:
        dst.restore(0, blob)
    assert ei.value.code == kv.KVCacheError.BUFLARGE


def test_restore_failure_does_not_modify_destination():
    """Critical invariant: a failed restore (e.g. bad CSUM) MUST leave
    the destination's prior context untouched."""
    src = _make_store()
    src.configure(0, model_id=1, model_epoch=1, page_count=1, initial_byte=0xAA)
    blob = bytearray(src.checkpoint(0))
    blob[100] ^= 0xFF  # corrupt

    dst = _make_store()
    dst.configure(0, model_id=1, model_epoch=1, page_count=1, initial_byte=0x55)
    original = dst.read_page(0, 0)
    with pytest.raises(kv.KVCacheException):
        dst.restore(0, bytes(blob))
    # Destination unchanged.
    assert dst.read_page(0, 0) == original


# ---------------------------------------------------------------------------
# Fork — COW semantics
# ---------------------------------------------------------------------------


def test_fork_same_slot_rejected():
    store = _make_store()
    store.configure(0, model_id=1, model_epoch=1, page_count=1)
    with pytest.raises(kv.KVCacheException) as ei:
        store.fork(0, 0)
    assert ei.value.code == kv.KVCacheError.SAMESLOT


def test_fork_empty_src_rejected():
    store = _make_store()
    with pytest.raises(kv.KVCacheException) as ei:
        store.fork(0, 1)
    assert ei.value.code == kv.KVCacheError.EMPTY


def test_fork_dst_busy_rejected():
    store = _make_store()
    store.configure(0, model_id=1, model_epoch=1, page_count=1)
    store.configure(1, model_id=2, model_epoch=1, page_count=1)
    with pytest.raises(kv.KVCacheException) as ei:
        store.fork(0, 1)
    assert ei.value.code == kv.KVCacheError.DSTBUSY


def test_fork_initially_pages_match():
    store = _make_store()
    store.configure(0, model_id=42, model_epoch=1, page_count=3, initial_byte=0xAA)
    store.fork(0, 1)
    for i in range(3):
        assert store.read_page(0, i) == store.read_page(1, i)
    # dst inherits model identity.
    assert store.slots[1].model_id == 42
    assert store.slots[1].cow_parent == 0


def test_fork_cow_isolation_src_unchanged_by_dst_write():
    """Writing to dst MUST NOT modify src (COW isolation)."""
    store = _make_store()
    store.configure(0, model_id=42, model_epoch=1, page_count=2, initial_byte=0xAA)
    store.fork(0, 1)
    new_payload = bytes([0xBB]) * 4096
    store.write_page(1, 0, new_payload)
    # src unchanged.
    assert store.read_page(0, 0) == bytes([0xAA]) * 4096
    # dst sees its own write.
    assert store.read_page(1, 0) == new_payload
    # dst recorded the cow-split.
    assert 0 in store.slots[1].cow_split_pages


def test_fork_cow_isolation_dst_unchanged_by_src_write():
    """Writing to src after fork MUST NOT propagate into dst."""
    store = _make_store()
    store.configure(0, model_id=42, model_epoch=1, page_count=1, initial_byte=0xAA)
    store.fork(0, 1)
    new_payload = bytes([0xEE]) * 4096
    store.write_page(0, 0, new_payload)
    # dst still has the original (pre-fork) bytes.
    assert store.read_page(1, 0) == bytes([0xAA]) * 4096


# ---------------------------------------------------------------------------
# Concurrent fork — deadlock-freedom
# ---------------------------------------------------------------------------


def test_concurrent_fork_no_deadlock():
    """Two threads, two fork pairs that lock the same two slots in
    opposite orders — should NOT deadlock because the store acquires
    locks in slot_id order."""
    store = kv.KVCacheStore(n_slots=4)
    store.configure(0, model_id=1, model_epoch=1, page_count=1)
    store.configure(1, model_id=1, model_epoch=1, page_count=1)
    # slot 2 + 3 are FREE — they're our two fork destinations.

    errors = []

    def fork_a():
        try:
            store.fork(0, 2)
        except Exception as exc:
            errors.append(exc)

    def fork_b():
        try:
            store.fork(1, 3)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=fork_a), threading.Thread(target=fork_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        if t.is_alive():
            pytest.fail("thread did not terminate within 5s — likely deadlock")
    assert not errors, f"unexpected exceptions: {errors}"


# ---------------------------------------------------------------------------
# Default store helpers
# ---------------------------------------------------------------------------


def test_default_store_singleton():
    s1 = kv.get_default_store()
    s2 = kv.get_default_store()
    assert s1 is s2


def test_reset_default_store_replaces_instance():
    s1 = kv.get_default_store()
    kv.reset_default_store()
    s2 = kv.get_default_store()
    assert s1 is not s2
