"""
VOS3 v20.5 — VMM / Slot-Quarantine Source-Shape Tests
======================================================

Validates the **source-level invariants** of the kernel's W^X quarantine
sequence. The kernel itself runs only inside QEMU; full integration
testing of the C code requires a kernel-boot harness. These tests
verify that the shape of the implementation matches the v20.5 spec —
the right symbols are defined, the right calls are wired, and the
W^X check in `vmm.c` invokes the slot quarantine handler.

Run with: VOS3_ALLOW_DEV_MODE=true pytest tests/test_vmm_security.py -v
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
# Test 1: Slot enum + capability flags exist with mandated values
# ---------------------------------------------------------------------------


def test_slots_h_defines_four_immutable_roles():
    src = _read("kernel/include/ipc/slots.h")
    # Capability flags
    assert "VOS3_CAP_COORDINATOR" in src, "missing VOS3_CAP_COORDINATOR"
    assert "VOS3_CAP_PREFILL" in src, "missing VOS3_CAP_PREFILL"
    assert "VOS3_CAP_DECODE" in src, "missing VOS3_CAP_DECODE"
    assert "VOS3_CAP_GPU_DIRECT" in src, "missing VOS3_CAP_GPU_DIRECT"

    # Slot identifiers — immutable mapping per v20.5 spec
    assert "VOS3_SLOT_COORDINATOR" in src
    assert "VOS3_SLOT_PREFILL" in src
    assert "VOS3_SLOT_DECODE_PRIMARY" in src
    assert "VOS3_SLOT_DECODE_PAIRED" in src

    # State machine includes the ZOMBIE quarantine state
    assert "VOS3_SLOT_STATE_ZOMBIE" in src

    # API surface
    for fn in (
        "vos3_slot_transition_state",
        "vos3_pud_scrub",
        "mmr_record_security_violation",
        "vos3_slot_wx_violation_handler",
    ):
        assert fn in src, f"missing API: {fn}"


# ---------------------------------------------------------------------------
# Test 2: vmm.c W^X path invokes the slot quarantine handler
# ---------------------------------------------------------------------------


def test_vmm_wx_violation_calls_quarantine_handler():
    src = _read("kernel/src/mm/vmm.c")
    # Locate the W^X enforcement block.
    m = re.search(
        r"\(prot & 0x2\) && \(prot & 0x4\)\)\s*\{(.*?)return -22",
        src,
        re.DOTALL,
    )
    assert m is not None, "W^X enforcement block not found in vmm.c"
    block = m.group(1)
    assert "vos3_slot_wx_violation_handler" in block, (
        "vmm.c W^X path must call vos3_slot_wx_violation_handler before "
        "returning -EINVAL"
    )


# ---------------------------------------------------------------------------
# Test 3: slot_state.c implements the quarantine sequence
# ---------------------------------------------------------------------------


def test_slot_state_quarantine_sequence_complete():
    src = _read("kernel/src/sec/slot_state.c")
    # Locate the wx_violation_handler function and slice out its body
    # by tracking brace depth — regex alone can't balance braces.
    sig = "void vos3_slot_wx_violation_handler"
    start = src.find(sig)
    assert start != -1, "wx_violation_handler signature not found"
    brace_open = src.find("{", start)
    assert brace_open != -1
    depth = 1
    i = brace_open + 1
    while i < len(src) and depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    body = src[brace_open + 1 : i - 1]

    assert "mmr_record_security_violation" in body, "Handler must record MMR violation"
    assert "vos3_slot_transition_state" in body, "Handler must transition slot state"
    assert "VOS3_SLOT_STATE_ZOMBIE" in body, "Handler must transition to ZOMBIE state"
    assert "vos3_pud_scrub" in body, "Handler must scrub PUD memory"


# ---------------------------------------------------------------------------
# Test 4: ZOMBIE is terminal in the state machine (only RECLAIMED forward path)
# ---------------------------------------------------------------------------


def test_zombie_state_is_terminal():
    src = _read("kernel/src/sec/slot_state.c")
    # Look for the transition-state function. It must reject any
    # transition out of ZOMBIE except to RECLAIMED.
    m = re.search(
        r"int vos3_slot_transition_state\(.*?\}\s*",
        src,
        re.DOTALL,
    )
    assert m is not None
    fn = m.group(0)
    assert "VOS3_SLOT_STATE_ZOMBIE" in fn
    # The function should explicitly check ZOMBIE-vs-not-RECLAIMED.
    assert (
        "STATE_ZOMBIE" in fn and "STATE_RECLAIMED" in fn
    ), "vos3_slot_transition_state must enforce ZOMBIE → RECLAIMED only"


# ---------------------------------------------------------------------------
# Test 5: VFIO core uses ISR-safe atomic CAS for PTE updates
# ---------------------------------------------------------------------------


def test_vfio_core_uses_atomic_cas_for_ptes():
    src = _read("kernel/src/drivers/gpu/vfio_core.c")
    assert "vos3_vmm_cas_pte" in src, (
        "vfio_core.c must use vos3_vmm_cas_pte (atomic_cmpxchg) for PTE "
        "installation — ISR-safe path required per v20.5 spec"
    )
    # The capability gate must run before any mapping work.
    assert (
        "vos3_slot_has_capability" in src
    ), "vfio_core.c must gate PCI mapping on VOS3_CAP_GPU_DIRECT"
    assert "VOS3_CAP_GPU_DIRECT" in src
    # ZOMBIE slots must not be granted GPU mappings.
    assert "VOS3_SLOT_STATE_ZOMBIE" in src


# ---------------------------------------------------------------------------
# Test 6: KV cache target is set to 200 MB per spec
# ---------------------------------------------------------------------------


def test_kv_cache_target_is_200mb():
    src = _read("kernel/include/ai/kv_cache.h")
    m = re.search(r"VOS3_KV_CACHE_TARGET_MB\s+(\d+)", src)
    assert m is not None, "VOS3_KV_CACHE_TARGET_MB not defined"
    assert int(m.group(1)) == 200, (
        f"v20.5 spec requires VOS3_KV_CACHE_TARGET_MB == 200, " f"found {m.group(1)}"
    )


# ---------------------------------------------------------------------------
# Test 7: dispatcher.c carries the task_steering_logic with affinity
# ---------------------------------------------------------------------------


def test_dispatcher_has_task_steering_logic():
    src = _read("kernel/src/ipc/dispatcher.c")
    assert "task_steering_logic" in src, (
        "dispatcher.c must define task_steering_logic for v20.5 multi-role " "dispatch"
    )
    # Must reference the four slot constants.
    for sym in (
        "VOS3_SLOT_COORDINATOR",
        "VOS3_SLOT_PREFILL",
        "VOS3_SLOT_DECODE_PRIMARY",
        "VOS3_SLOT_DECODE_PAIRED",
    ):
        assert sym in src, f"dispatcher.c missing slot constant {sym}"


# ---------------------------------------------------------------------------
# Test 8: Makefile includes the new v20.5 sources
# ---------------------------------------------------------------------------


def test_makefile_includes_v205_sources():
    src = _read("kernel/Makefile")
    assert "sec/slot_state.c" in src, "Makefile must compile sec/slot_state.c"
    assert (
        "drivers/gpu/vfio_core.c" in src
    ), "Makefile must compile drivers/gpu/vfio_core.c"
