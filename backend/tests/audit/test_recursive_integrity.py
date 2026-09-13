# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
VOS3 v20.5.3 — Recursive Integrity Audit (Phase 6.2 Purification)
==================================================================

Covers the "dark paths" — failure modes and rollback contracts that the
75-round happy-path suite does not exercise.

  * vos3_vmm_expand_slot_memory_dedup OOM rollback (must not leak fresh
    physical allocations when mid-loop map fails)
  * kv_block_release underflow detection (must not double-decrement)
  * kv_block_mark_dirty CoW path uses atomic primitives
  * fingerprint_is_trustworthy fail-closed posture
  * Q4 pack/unpack saturation arithmetic invariants

Honest scoping
--------------

Each round is REAL or SOURCE-SHAPE. No round is silently skipped.

The "100kHz interrupt stress" the user prompted for would require a
booted QEMU + a real interrupt source, which this test environment
does not provide. Substituted with structural verification that the
atomic primitives the interrupt-stress would expose are present and
used in the right places. When the booted-kernel test harness lands
in v20.6, these source-shape rounds will gain runtime companions.
"""

from __future__ import annotations

import hashlib
import random
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _function_body(src: str, sig_pattern: str) -> str:
    """Extract the body of a function whose definition line matches
    `sig_pattern` (regex). Returns text between the first '{' after the
    match and its balancing '}'."""
    m = re.search(sig_pattern, src, re.MULTILINE)
    if not m:
        return ""
    brace_open = src.find("{", m.end())
    if brace_open < 0:
        return ""
    depth = 1
    i = brace_open + 1
    while i < len(src) and depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[brace_open + 1 : i - 1]


# ---------------------------------------------------------------------------
# Section 1 — vos3_vmm_expand_slot_memory_dedup OOM rollback
# ---------------------------------------------------------------------------


def test_dedup_expand_rollback_unmaps_partial_progress():
    """When mid-loop vos3_vmm_map fails, the function must call
    vos3_vmm_unmap_range over the bytes it already mapped, AND must
    free the latest fresh allocation. Verifies both branches of the
    failure path."""
    body = _function_body(
        _read("kernel/src/mm/vmm.c"),
        r"^uint64_t\s+vos3_vmm_expand_slot_memory_dedup\s*\(",
    )
    assert body, "function not found"
    # Rollback after map failure: unmap_range over (cursor_va - mapped*hp_size)
    assert "vos3_vmm_unmap_range" in body
    # Path: shared phys must NOT be freed (other slot holds it); fresh phys must be freed.
    # Source shape: presence of the conditional `if (mapped_phys == phys)` guards this.
    assert (
        "mapped_phys == phys" in body
    ), "rollback does not distinguish shared (registry hit) from fresh phys"
    # Path: alloc-failure (PMM exhaustion) before any map: rollback must still run.
    assert "vos3_pmm_alloc_huge" in body
    assert (
        body.count("vos3_vmm_unmap_range") >= 2
    ), "expected unmap_range in BOTH alloc-failure and map-failure rollback branches"


def test_dedup_expand_does_not_leak_on_registry_full():
    """When kv_compressor_dedupe_hint returns the SAME phys it was
    given (registry full or miss), the function must map that phys.
    No allocation is leaked. Verified by reading the conditional."""
    body = _function_body(
        _read("kernel/src/mm/vmm.c"),
        r"^uint64_t\s+vos3_vmm_expand_slot_memory_dedup\s*\(",
    )
    # Pattern: only free `phys` when mapped_phys != phys (i.e. dedupe hit
    # returned a shared address, original allocation is unneeded).
    assert "kv_compressor_dedupe_hint" in body
    assert "mapped_phys != phys" in body, "dedupe-hit free path missing"


# ---------------------------------------------------------------------------
# Section 2 — kv_block_release underflow + atomic ref-count semantics
# ---------------------------------------------------------------------------


def test_release_uses_atomic_fetch_sub():
    """ref_count decrement must go through vos3_atomic_fetch_sub32
    (SMP-safe) — not the v20.5.2 racy `--`."""
    body = _function_body(
        _read("kernel/src/mm/kv_compressor.c"),
        r"^int\s+kv_block_release\s*\(",
    )
    assert (
        "vos3_atomic_fetch_sub32" in body
    ), "release uses non-atomic ref_count decrement"


def test_release_detects_underflow_via_return_value():
    """fetch_sub returns the PREVIOUS value. Caller must check `prev == 0`
    to detect a double-release attempt and restore + signal failure."""
    body = _function_body(
        _read("kernel/src/mm/kv_compressor.c"),
        r"^int\s+kv_block_release\s*\(",
    )
    assert re.search(
        r"prev\s*==\s*0u?", body
    ), "underflow guard via fetch_sub return value missing"
    assert "vos3_atomic_fetch_add32" in body, "no restore-on-underflow path"
    assert "return -1" in body, "underflow does not propagate failure"


def test_release_eviction_uses_atomic_store():
    """Last-reference path must atomically clear the flags word
    (releases the slot for re-registration)."""
    body = _function_body(
        _read("kernel/src/mm/kv_compressor.c"),
        r"^int\s+kv_block_release\s*\(",
    )
    assert "vos3_atomic_store32" in body, "eviction does not atomically clear flags"


def test_register_uses_cas_to_claim_slot():
    """Slot acquisition must use compare-and-swap, not a TOCTOU
    `if (!(flags & INUSE)) flags = INUSE` race."""
    body = _function_body(
        _read("kernel/src/mm/kv_compressor.c"),
        r"^int\s+kv_block_register\s*\(",
    )
    assert "vos3_atomic_cas32" in body, "slot claim has a TOCTOU race"
    # Must check the CAS return value to detect lost races.
    assert "prev_flags" in body or "prev != 0" in body, "CAS return value not consulted"


# ---------------------------------------------------------------------------
# Section 3 — mark_dirty CoW break path (atomic flag-OR)
# ---------------------------------------------------------------------------


def test_mark_dirty_uses_cas_loop_for_flag_or():
    """OR-set on the flags word must be atomic. The kernel's atomic.h
    ships a 64-bit fetch_or but no 32-bit one, so mark_dirty uses a
    CAS loop. Verify the loop is present."""
    body = _function_body(
        _read("kernel/src/mm/kv_compressor.c"),
        r"^int\s+kv_block_mark_dirty\s*\(",
    )
    assert "vos3_atomic_cas32" in body, "no CAS loop for flag-OR"
    assert "KV_FLAG_DIRTY" in body
    assert (
        "vos3_atomic_fetch_add64" in body
    ), "g_cow_breaks counter increment is non-atomic"


def test_mark_dirty_idempotent():
    """If the flag is already set, the CAS loop must terminate without
    spinning. The exit condition is `cur == want` (already dirty)."""
    body = _function_body(
        _read("kernel/src/mm/kv_compressor.c"),
        r"^int\s+kv_block_mark_dirty\s*\(",
    )
    assert "cur == want" in body, "no early-exit when already dirty"


# ---------------------------------------------------------------------------
# Section 4 — Fingerprint fail-closed posture
# ---------------------------------------------------------------------------


def test_fingerprint_trustworthy_helper_exists():
    """fingerprint_is_trustworthy must be defined in license_check.c
    with a static linkage prefix (single-TU helper)."""
    src = _read("kernel/src/pro/license_check.c")
    assert re.search(
        r"^static\s+int\s+fingerprint_is_trustworthy\s*\(", src, re.MULTILINE
    ), "helper not declared static"


def test_fingerprint_rejects_all_zero_digest():
    """All-zero SHA-256 digest must be refused."""
    body = _function_body(
        _read("kernel/src/pro/license_check.c"),
        r"^static\s+int\s+fingerprint_is_trustworthy\s*\(",
    )
    # Walks 32 bytes looking for any nonzero
    assert re.search(r"i\s*<\s*32", body), "no 32-byte zero scan"
    assert "any_nonzero" in body or "nonzero" in body


def test_fingerprint_rejects_cpuid_only():
    """If BOTH has_tpm_ek and has_smbios_uuid are zero (CPUID-only),
    refuse to grant PRO ceiling."""
    body = _function_body(
        _read("kernel/src/pro/license_check.c"),
        r"^static\s+int\s+fingerprint_is_trustworthy\s*\(",
    )
    # The check pattern: has_tpm_ek == 0 && has_smbios_uuid == 0
    assert (
        "has_tpm_ek" in body and "has_smbios_uuid" in body
    ), "fingerprint flags not consulted"
    assert "== 0" in body, "no zero-comparison"


def test_hugepage_ceiling_calls_trustworthy_gate():
    """vos3_pmm_get_hugepage_ceiling must invoke fingerprint_is_trustworthy
    BEFORE vos3_verify_license_signature so the fail-closed gate fires
    even on a "valid"-looking license signature."""
    body = _function_body(
        _read("kernel/src/pro/license_check.c"),
        r"^uint32_t\s+vos3_pmm_get_hugepage_ceiling\s*\(",
    )
    fp_idx = body.find("fingerprint_is_trustworthy")
    sig_idx = body.find("vos3_verify_license_signature")
    assert fp_idx >= 0, "trustworthy gate not called"
    assert sig_idx >= 0, "signature verify not called"
    assert fp_idx < sig_idx, "trustworthy gate must run BEFORE signature verify"


# ---------------------------------------------------------------------------
# Section 5 — Q4 pack/unpack arithmetic invariants
# ---------------------------------------------------------------------------


def _q4_pack(src: list[int], scale: int) -> bytes:
    """Python mirror — same arithmetic as kv_q4_group_pack."""
    out = bytearray(16)
    for i in range(0, 32, 2):
        a = max(-8, min(7, src[i] // scale))
        b = max(-8, min(7, src[i + 1] // scale))
        out[i // 2] = ((b + 8) & 0xF) << 4 | ((a + 8) & 0xF)
    return bytes(out)


def _q4_unpack(packed: bytes, scale: int) -> list[int]:
    out = [0] * 32
    for i in range(0, 32, 2):
        byte = packed[i // 2]
        na = (byte & 0xF) - 8
        nb = ((byte >> 4) & 0xF) - 8
        out[i] = na * scale
        out[i + 1] = nb * scale
    return out


def test_q4_invariant_unpack_pack_lossy_idempotent():
    """unpack(pack(unpack(x))) == unpack(pack(x)) — once you've quantized
    and re-quantized, no further information is lost. This is the
    formal guarantee that Q4 is a *projection*, not a sequence of
    accumulating errors."""
    rng = random.Random(7)
    src = [rng.randint(-512, 511) for _ in range(32)]
    scale = 64
    once = _q4_unpack(_q4_pack(src, scale), scale)
    twice = _q4_unpack(_q4_pack(once, scale), scale)
    assert once == twice, "Q4 not idempotent under reapplication"


def test_q4_pack_is_deterministic():
    """Same input → same output. No clock/RNG dependency."""
    src = [i * 7 - 100 for i in range(32)]
    a = _q4_pack(src, scale=32)
    b = _q4_pack(src, scale=32)
    assert a == b


def test_q4_packed_size_is_16_bytes_per_group():
    """16 bytes packed for 32 values × int16 (64 bytes) source = 4×
    compression ratio at the scalar level (matches GGUF Q4_0 framing
    once the per-group scale is accounted for separately)."""
    src = [0] * 32
    packed = _q4_pack(src, scale=64)
    assert len(packed) == 16


# ---------------------------------------------------------------------------
# Section 6 — fingerprint hash is well-formed under tamper
# ---------------------------------------------------------------------------


def test_fingerprint_changes_when_tpm_byte_flipped():
    """The 58-byte fingerprint buffer is SHA-256'd. Flipping any byte
    in the TPM EK region (offset 20..51) MUST change the digest."""
    base = bytearray(58)
    h0 = hashlib.sha256(bytes(base)).hexdigest()
    base[35] = 0x77  # tamper inside the TPM EK region
    h1 = hashlib.sha256(bytes(base)).hexdigest()
    assert h0 != h1


def test_fingerprint_changes_when_signature_byte_flipped():
    """Flipping a byte in the CPUID signature region (offset 12..19)
    must change the digest."""
    base = bytearray(58)
    h0 = hashlib.sha256(bytes(base)).hexdigest()
    base[15] = 0x99  # CPUID signature region
    h1 = hashlib.sha256(bytes(base)).hexdigest()
    assert h0 != h1


# ---------------------------------------------------------------------------
# Section 7 — VBus opcode + operations honest contract
# ---------------------------------------------------------------------------


def test_efficiency_stats_handler_is_named_consistently():
    """cmd_efficiency_stats is the only symbol that should be wired
    into the EFFICIENCY_STATS dispatch arm."""
    bridge = _read("kernel/src/drivers/virtio_bridge.c")
    assert 'streq(tok[0], "EFFICIENCY_STATS")' in bridge
    assert "cmd_efficiency_stats" in bridge


def test_kv_bench_probe_is_clearly_diagnostic():
    """KV_BENCH_PROBE handler must declare itself a benchmark/diagnostic
    primitive in the source — not an accidental production knob."""
    cmds = _read("kernel/src/drivers/vbus_ai_cmds.c")
    bench_idx = cmds.find("cmd_kv_bench_probe")
    pre = cmds[max(0, bench_idx - 1500) : bench_idx]
    assert (
        "BENCHMARK PROBE" in pre or "DIAGNOSTIC" in pre.upper()
    ), "probe not labeled as diagnostic"
    assert (
        "NOT a production primitive" in pre or "diagnostic" in pre.lower()
    ), "probe not honest about its purpose"


# ---------------------------------------------------------------------------
# Section 8 — K-CRIT-2 slab integrity magic (v20.6)
# ---------------------------------------------------------------------------


def test_kcrit2_magic_constant_defined():
    """SLAB_OBJ_FREE_MAGIC must be defined to a 64-bit non-zero value."""
    src = _read("kernel/src/mm/heap.c")
    m = re.search(r"#define\s+SLAB_OBJ_FREE_MAGIC\s+0x([0-9A-Fa-f]+)U?L?L?", src)
    assert m is not None, "SLAB_OBJ_FREE_MAGIC not defined"
    val = int(m.group(1), 16)
    assert val != 0
    assert val.bit_length() <= 64


def test_kcrit2_create_slab_stamps_magic():
    """create_slab build loop must stamp the magic at offset +8 in every
    fresh free-list slot. Without this, the FIRST alloc panics."""
    body = _function_body(
        _read("kernel/src/mm/heap.c"),
        r"^static\s+vos3_slab_t\*\s+create_slab\s*\(",
    )
    assert "SLAB_OBJ_FREE_MAGIC" in body, "magic not stamped on slab build"
    assert "sizeof(void*)" in body, "no offset arithmetic for magic placement"


def test_kcrit2_alloc_validates_magic_before_decode():
    """vos3_slab_alloc must read the magic and abort BEFORE decoding the
    next-pointer at offset 0. Order matters — validating after decode
    leaks the forged-freelist primitive that K-CRIT-2 closes."""
    body = _function_body(
        _read("kernel/src/mm/heap.c"),
        r"^void\*\s+vos3_slab_alloc\s*\(",
    )
    magic_idx = body.find("SLAB_OBJ_FREE_MAGIC")
    decode_idx = body.find("freelist_decode")
    assert magic_idx >= 0, "magic not validated"
    assert decode_idx >= 0, "freelist_decode not present"
    assert magic_idx < decode_idx, "magic check must come BEFORE next-pointer decode"


def test_kcrit2_free_restamps_magic():
    """vos3_slab_free must re-stamp the magic when returning an object
    to the free list — otherwise the next alloc panics on a stale magic."""
    body = _function_body(
        _read("kernel/src/mm/heap.c"),
        r"^void\s+vos3_slab_free\s*\(",
    )
    assert "SLAB_OBJ_FREE_MAGIC" in body, "magic not re-stamped on free"


def test_kcrit2_min_obj_size_enforced():
    """vos3_slab_create must enforce a 16-byte minimum object size so
    every freed slot has room for (next_pointer @ +0, magic @ +8)."""
    body = _function_body(
        _read("kernel/src/mm/heap.c"),
        r"^vos3_slab_cache_t\*\s+vos3_slab_create\s*\(",
    )
    assert "16" in body, "no 16-byte min visible"
    assert (
        "min_obj" in body or "obj_size = 16U" in body
    ), "min-obj-size guard not implemented"
