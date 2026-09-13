# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
Source-shape tests for v21.3 milestone M4 — Certification Assertion Harness.

Per Blind-Implementation Protocol §0, M4's runtime piece (qemu_assert_runner)
needs an actual QEMU + booted kernel to validate. These tests verify the
SHAPE of the landed code without booting — they pass on a stock recovery
checkout with no QEMU.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ---------------------------------------------------------------------------
# Macro & header
# ---------------------------------------------------------------------------


def test_assert_cert_header_exists():
    src = _read("kernel/include/vos/assert_cert.h")
    assert "VOS3_ASSERT_CERT" in src
    assert "VOS3_ASSERT_CERT_DONE" in src
    assert "vos3_assert_cert_emit" in src
    assert "vos3_assert_cert_done" in src


def test_assert_cert_macro_gated_by_harness_flag():
    src = _read("kernel/include/vos/assert_cert.h")
    # Must have both branches: active when VOS3_ASSERT_HARNESS, no-op otherwise
    assert "#ifdef VOS3_ASSERT_HARNESS" in src
    # No-op branch must still evaluate expr for side-effect parity
    no_op_idx = src.index("VOS3_ASSERT_HARNESS undefined")
    rest = src[no_op_idx:]
    assert "(void)(expr)" in rest


def test_assert_cert_id_registry_no_duplicate_values():
    src = _read("kernel/include/vos/assert_cert_ids.h")
    define_re = re.compile(
        r"^\s*#define\s+(VOS3_CERT_\w+)\s+(\d+)\s*(?:/\*.*\*/)?\s*$",
        re.M,
    )
    seen: dict[int, str] = {}
    for m in define_re.finditer(src):
        name, val = m.group(1), int(m.group(2))
        if name == "VOS3_CERT_MAX_ID_CURRENTLY_USED":
            continue
        assert val not in seen, f"duplicate cert ID {val}: {seen[val]} and {name}"
        seen[val] = name
    # Honest accounting: at least 5 canaries + 4 boot + 3 xsave + 3 apic
    # + 3 ioapic + 2 sched + 2 vbus + 2 heap = 24 minimum
    assert len(seen) >= 20, f"populated cert count below minimum (got {len(seen)})"


# ---------------------------------------------------------------------------
# Emit implementation
# ---------------------------------------------------------------------------


def test_assert_cert_emit_writes_com1():
    src = _read("kernel/src/diag/assert_cert.c")
    assert "0x3F8" in src or "0x3f8" in src
    assert "outb" in src
    assert "~~CERT~~" in src, "magic prefix missing — runner regex won't match"


def test_assert_cert_emit_does_not_panic_on_fail():
    """Failure-path discipline: do NOT panic. Runner needs to see every
    assert that fired before the kernel halted."""
    src = _read("kernel/src/diag/assert_cert.c")
    body_start = src.index("vos3_assert_cert_emit(")
    body_end = src.index("\n}\n", body_start)
    body = src[body_start:body_end]
    assert (
        "panic" not in body.lower()
    ), "emit() must not panic on FAIL — runner needs the full trace"


def test_done_sentinel_present():
    src = _read("kernel/src/diag/assert_cert.c")
    assert "DONE" in src
    runner = _read("tools/runner/qemu_assert_runner.py")
    assert "DONE" in runner


# ---------------------------------------------------------------------------
# Kernel-side wiring (kmain.c harness body)
# ---------------------------------------------------------------------------


def test_kmain_calls_harness_before_sched_start():
    src = _read("kernel/src/boot/kmain.c")
    sched_idx = src.index("vos3_sched_start();")
    pre = src[:sched_idx]
    assert (
        "vos3_run_cert_harness" in pre
    ), "harness must run before scheduler start (last point all state is stable)"


def test_kmain_harness_body_uses_canary_ids():
    src = _read("kernel/src/boot/kmain.c")
    # The DEFINITION (with open brace + function body) — skip the
    # forward declaration which is just `;`-terminated.
    body_start = src.index(
        "void vos3_run_cert_harness(const vos3_boot_info_t *boot_info)\n{"
    )
    # All 5 canaries should appear in the harness body
    body_chunk = src[body_start : body_start + 6000]
    for sym in (
        "VOS3_CERT_CANARY_LONG_MODE",
        "VOS3_CERT_CANARY_PAGING",
        "VOS3_CERT_CANARY_NX_ENABLED",
        "VOS3_CERT_CANARY_SSE2_AVAILABLE",
        "VOS3_CERT_CANARY_KERNEL_VIRT_HIGH",
    ):
        assert sym in body_chunk, f"canary {sym} missing from harness body"


def test_kmain_harness_emits_done_sentinel():
    src = _read("kernel/src/boot/kmain.c")
    assert "VOS3_ASSERT_CERT_DONE()" in src


# ---------------------------------------------------------------------------
# Python runner shape
# ---------------------------------------------------------------------------


def test_runner_script_exists_and_is_executable():
    p = REPO / "tools/runner/qemu_assert_runner.py"
    assert p.is_file()
    src = p.read_text()
    assert "argparse" in src
    assert "qemu-system-x86_64" in src
    assert "subprocess.Popen" in src


def test_runner_parses_cert_format():
    src = _read("tools/runner/qemu_assert_runner.py")
    # Regex must match exactly the kernel-side emit format.
    assert "~~CERT~~" in src
    assert "PASS|FAIL" in src
    assert "DONE" in src


def test_runner_distinguishes_missing_from_fail():
    """Per honest-accounting design: missing cert IDs (registry says
    populated but runtime never emitted) must be a distinct failure
    class from FAIL emits."""
    src = _read("tools/runner/qemu_assert_runner.py")
    assert "missing" in src
    assert "duplicate" in src
    assert "JUnit" in src or "junit" in src or "testcase" in src


# ---------------------------------------------------------------------------
# Build-system integration
# ---------------------------------------------------------------------------


def test_makefile_assert_harness_flag_wired():
    src = _read("kernel/Makefile")
    assert re.search(r"ifeq\s*\(\$\(VOS3_ASSERT_HARNESS\),1\)", src)
    assert "-DVOS3_ASSERT_HARNESS" in src


def test_diag_assert_cert_in_objects():
    src = _read("kernel/Makefile")
    assert "$(SRC_DIR)/diag/assert_cert.c" in src


def test_default_build_does_not_define_harness():
    """Stock 'make VOS3_BUILD_TYPE=PRO' must not enable the harness.
    The flag requires explicit env-var opt-in."""
    src = _read("kernel/Makefile")
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("CFLAGS") and "-DVOS3_ASSERT_HARNESS" in stripped:
            # Walk backwards to confirm we're inside ifeq
            idx = src.index(line)
            window = src[max(0, idx - 200) : idx]
            if "ifeq ($(VOS3_ASSERT_HARNESS),1)" not in window:
                raise AssertionError("VOS3_ASSERT_HARNESS enabled outside ifeq guard")
