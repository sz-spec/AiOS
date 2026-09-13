"""
VOS3 v20.5.1 Phase 5.1 — Memory Scaling Source-Shape Tests
============================================================

The runtime equivalent of `kernel/tests/test_memory_scaling.c`. Verifies
the v20.5.1 contract at the source level (kernel C cannot run from
pytest without a kmain harness — that's v20.6 work). Asserts:

  - VOS3_KV_CACHE_MAX_GB = 10
  - VOS3_DYNAMIC_EXPANSION_ENABLED = 1
  - VOS3_HUGEPAGE_POOL_MAX = 5,120 in pmm.c (10 GiB ceiling)
  - vos3_vmm_expand_slot_memory + vos3_vmm_contract_slot_memory exist
    in vmm.c with the documented signatures
  - Slot isolation invariant: slot N's expansion VA base is computed
    from a static formula that guarantees disjoint regions per slot
  - Python-side request_memory_expansion / release_memory_on_idle
    estimate context-driven byte counts correctly + cap at 10 GiB
"""

from __future__ import annotations

import os
import re

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read(path: str) -> str:
    full = os.path.join(REPO_ROOT, path)
    with open(full) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Test 1: kv_cache.h carries the v20.5.1 constants
# ---------------------------------------------------------------------------


def test_kv_cache_max_gb_is_10():
    src = _read("kernel/include/ai/kv_cache.h")
    m = re.search(r"VOS3_KV_CACHE_MAX_GB\s+(\d+)", src)
    assert m is not None, "VOS3_KV_CACHE_MAX_GB not defined"
    assert (
        int(m.group(1)) == 10
    ), f"v20.5.1 spec mandates VOS3_KV_CACHE_MAX_GB == 10, got {m.group(1)}"


def test_dynamic_expansion_enabled_flag():
    src = _read("kernel/include/ai/kv_cache.h")
    m = re.search(r"VOS3_DYNAMIC_EXPANSION_ENABLED\s+(\d+)U?", src)
    assert m is not None, "VOS3_DYNAMIC_EXPANSION_ENABLED not defined"
    assert int(m.group(1)) == 1, (
        f"VOS3_DYNAMIC_EXPANSION_ENABLED must be 1 to activate v20.5.1, "
        f"got {m.group(1)}"
    )


def test_long_context_threshold_32k():
    src = _read("kernel/include/ai/kv_cache.h")
    m = re.search(r"VOS3_LONG_CONTEXT_THRESHOLD_TOKENS\s+(\d+)", src)
    assert m is not None
    assert int(m.group(1)) == 32768


# ---------------------------------------------------------------------------
# Test 2: pmm.c hugepage pool bumped to 5,120 (10 GiB)
# ---------------------------------------------------------------------------


def test_hugepage_pool_max_is_5120():
    src = _read("kernel/src/mm/pmm.c")
    m = re.search(r"VOS3_HUGEPAGE_POOL_MAX\s+(\d+)", src)
    assert m is not None
    assert int(m.group(1)) == 5120, (
        f"v20.5.1 spec requires VOS3_HUGEPAGE_POOL_MAX == 5120, " f"got {m.group(1)}"
    )
    # Sanity: 5120 × 2 MiB = 10 GiB
    assert 5120 * 2 * 1024 * 1024 == 10 * 1024 * 1024 * 1024


# ---------------------------------------------------------------------------
# Test 3: vmm.c carries expand + contract + slot-isolation primitives
# ---------------------------------------------------------------------------


def test_vmm_expansion_api_exists():
    src = _read("kernel/src/mm/vmm.c")
    assert (
        "uint64_t vos3_vmm_expand_slot_memory(uint8_t slot_id, size_t extra_bytes)"
        in src
    )
    assert "uint64_t vos3_vmm_contract_slot_memory(uint8_t slot_id)" in src
    assert "uint64_t vos3_vmm_slot_expansion_bytes(uint8_t slot_id)" in src


def test_vmm_slot_isolation_via_static_va_base():
    src = _read("kernel/src/mm/vmm.c")
    # Slot N's VA base is computed from a static formula → disjoint per slot.
    assert "VOS3_SLOT_EXPAND_VA_BASE" in src
    assert "VOS3_SLOT_EXPAND_VA_STRIDE" in src
    assert "slot_expand_va_base" in src
    # The formula must take slot_id as input
    fn_match = re.search(
        r"slot_expand_va_base\(uint32_t slot_id\)\s*\{(.*?)\}",
        src,
        re.DOTALL,
    )
    assert fn_match is not None
    body = fn_match.group(1)
    assert "slot_id" in body
    assert "VOS3_SLOT_EXPAND_VA_STRIDE" in body


def test_vmm_expansion_uses_pmm_alloc_huge():
    src = _read("kernel/src/mm/vmm.c")
    # Locate the expand function body
    sig = "uint64_t vos3_vmm_expand_slot_memory("
    start = src.find(sig)
    assert start != -1
    brace_open = src.find("{", start)
    depth = 1
    i = brace_open + 1
    while i < len(src) and depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    body = src[brace_open + 1 : i - 1]

    assert "vos3_pmm_alloc_huge" in body, "Expansion must allocate hugepages from PMM"
    # Rollback path must exist (failure case must not leak hugepages)
    assert (
        "vos3_vmm_unmap_range" in body
    ), "Expansion must rollback partial mappings on failure"
    # Must update slot zone tracking so PUD scrub on ZOMBIE covers expanded mem
    assert (
        "vos3_slot_register_zone" in body
    ), "Expansion must register the new zone with slot_state"


def test_vmm_expansion_caps_at_10gb_ceiling():
    src = _read("kernel/src/mm/vmm.c")
    sig = "uint64_t vos3_vmm_expand_slot_memory("
    start = src.find(sig)
    brace_open = src.find("{", start)
    depth = 1
    i = brace_open + 1
    while i < len(src) and depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    body = src[brace_open + 1 : i - 1]
    # Ceiling check must reference VOS3_KV_CACHE_MAX_BYTES
    assert (
        "VOS3_KV_CACHE_MAX_BYTES" in body
    ), "Expansion must check the 10 GiB ceiling before allocating"


# ---------------------------------------------------------------------------
# Test 4: Python orchestration estimates expansion correctly
# ---------------------------------------------------------------------------


def test_python_expansion_estimator_short_context_returns_zero():
    from services.agent_orchestration import _estimate_expansion_bytes

    # Below threshold → 0
    assert _estimate_expansion_bytes(8192) == 0
    assert _estimate_expansion_bytes(32768) == 0


def test_python_expansion_estimator_long_context_scales():
    from services.agent_orchestration import (
        _estimate_expansion_bytes,
        KV_CACHE_MAX_BYTES,
        KV_CACHE_PAGE_SIZE,
    )

    # 64k tokens — should produce a positive expansion size
    extra = _estimate_expansion_bytes(64 * 1024)
    assert extra > 0
    # Rounded to hugepage granularity
    assert extra % KV_CACHE_PAGE_SIZE == 0
    # Bounded by 10 GiB ceiling
    extra_max = _estimate_expansion_bytes(100 * 1024 * 1024)
    assert extra_max <= KV_CACHE_MAX_BYTES


def test_python_request_memory_expansion_kernel_offline_returns_zero():
    """When the kernel is offline (default in dev), request_memory_expansion
    must return 0 silently and never raise."""
    from services.agent_orchestration import request_memory_expansion

    # 64k context → would request expansion if kernel were up; offline → 0
    result = request_memory_expansion(slot_id=1, context_length_tokens=64 * 1024)
    assert result == 0


def test_python_release_memory_on_idle_kernel_offline_returns_zero():
    from services.agent_orchestration import release_memory_on_idle

    result = release_memory_on_idle(slot_id=1)
    assert result == 0


# ---------------------------------------------------------------------------
# Test 5: Reference C test exists at the spec'd path
# ---------------------------------------------------------------------------


def test_kernel_test_file_exists_at_spec_path():
    """kernel/tests/test_memory_scaling.c is the user-spec'd location.
    Currently a reference test (NOT in the build SRCS); promoted to
    kernel/src/tests/ in v20.6 once a kmain test harness lands."""
    src = _read("kernel/tests/test_memory_scaling.c")
    # Must call the public expansion API
    assert "vos3_vmm_expand_slot_memory" in src
    assert "vos3_vmm_contract_slot_memory" in src
    # Must include the ceiling enforcement test
    assert "test_ceiling_enforcement" in src or "ceiling" in src.lower()
    # Must include the slot isolation test
    assert "test_slot_isolation" in src or "slot_isolation" in src.lower()
    # Test runner entry point
    assert "test_memory_scaling_run" in src


# ---------------------------------------------------------------------------
# Test 6: Constants in Python orchestration mirror kernel header
# ---------------------------------------------------------------------------


def test_python_constants_mirror_kernel_header():
    from services.agent_orchestration import (
        LONG_CONTEXT_THRESHOLD_TOKENS,
        KV_CACHE_PAGE_SIZE,
        KV_CACHE_MAX_BYTES,
    )

    assert LONG_CONTEXT_THRESHOLD_TOKENS == 32768
    assert KV_CACHE_PAGE_SIZE == 2 * 1024 * 1024
    assert KV_CACHE_MAX_BYTES == 10 * 1024 * 1024 * 1024
