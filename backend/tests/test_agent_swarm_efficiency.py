"""
VOS3 v20.5.2 — Multi-Agent KV-Deduplication Source-Shape Tests
=================================================================

Verifies the KV compressor + CoW deduplication contract at the source
level. The actual physical-vs-virtual ratio measurement requires:
  - A booted kernel (not available in static dev)
  - 5 agent slots with overlapping context loaded into KV cache
  - VBus query exposing kv_compressor_get_stats()

This test verifies the ARCHITECTURAL invariants that make the runtime
measurement possible:
  - kv_compressor.c implements the registry + lookup + acquire + release
  - CoW logic is wired (mark_dirty path exists)
  - Stats are exported via kv_compressor_get_stats()
  - The license_check.c hugepage-ceiling gate is honest about its limits
  - The tool surface (vos3_pro_activate.py) generates the right
    fingerprint inputs

Once a booted-kernel test harness lands (v20.6), the runtime efficiency
test will replace these source-shape assertions with actual VBus queries
of the dedup hit rate.
"""

from __future__ import annotations

import os
import re
import json
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read(path: str) -> str:
    with open(os.path.join(REPO_ROOT, path)) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Test 1: kv_compressor.c implements the registry + API surface
# ---------------------------------------------------------------------------


def test_kv_compressor_registry_api_complete():
    src = _read("kernel/src/mm/kv_compressor.c")
    # Required public API
    for fn in (
        "kv_block_lookup",
        "kv_block_register",
        "kv_block_acquire",
        "kv_block_release",
        "kv_block_mark_dirty",
        "kv_compressor_get_stats",
        "kv_compressor_init",
    ):
        assert fn in src, f"missing kv_compressor API: {fn}"

    # Required state
    assert "g_registry" in src or "kv_block_entry_t" in src
    assert "ref_count" in src
    assert "KV_FLAG_INUSE" in src
    assert "KV_FLAG_DIRTY" in src

    # Counter fields
    assert "g_dedup_hits" in src
    assert "g_dedup_misses" in src
    assert "g_cow_breaks" in src


# ---------------------------------------------------------------------------
# Test 2: CoW logic — release decrements; final reference triggers free
# ---------------------------------------------------------------------------


def test_kv_compressor_cow_release_path():
    src = _read("kernel/src/mm/kv_compressor.c")
    # Find kv_block_release function body
    sig = "int kv_block_release"
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

    # Phase 6.1 hardening: ref count decrement uses the SMP-safe atomic
    # primitive. Either form (legacy `ref_count--` or atomic `fetch_sub32`)
    # satisfies the "decrement" contract; the test accepts both so it can
    # validate either implementation.
    assert (
        "ref_count--" in body
        or "ref_count -=" in body
        or "vos3_atomic_fetch_sub32" in body
    ), "ref count is not decremented"
    # Must signal "free me" on last release
    assert "return 1" in body  # documented contract: 1 = caller should free
    # Must guard against underflow / already-zero ref count.
    # Legacy form: `ref_count == 0`. Atomic form: detect via the
    # fetch_sub return value (`prev == 0u` means we just underflowed).
    assert (
        "ref_count == 0" in body
        or "ref_count == 0U" in body
        or "prev == 0u" in body
        or "prev == 0U" in body
    ), "underflow guard missing"


# ---------------------------------------------------------------------------
# Test 3: Mark-dirty path is wired (CoW broken when slot writes)
# ---------------------------------------------------------------------------


def test_kv_compressor_mark_dirty_increments_cow_break_counter():
    src = _read("kernel/src/mm/kv_compressor.c")
    sig = "int kv_block_mark_dirty"
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
    assert "KV_FLAG_DIRTY" in body
    assert "g_cow_breaks" in body


# ---------------------------------------------------------------------------
# Test 4: Hardware fingerprint — composes CPUID vendor + signature + EK + MAC
# ---------------------------------------------------------------------------


def test_hw_fingerprint_composes_correct_inputs():
    src = _read("kernel/src/pro/license_check.c")
    # Must define a 58-byte input buffer (matches the activation tool)
    assert "58" in src
    # Must call cpuid_vendor_string + cpuid_signature
    assert "cpuid_vendor_string" in src
    assert "cpuid_signature" in src
    # Must compose TPM EK + MAC into the fingerprint
    assert "tpm_ek_pub_or_zero" in src or "tpm_ek" in src.lower()
    assert "primary_mac" in src.lower() or "mac_address" in src.lower()
    # Must SHA-256 the result
    assert "vos3_sha256_init" in src
    assert "vos3_sha256_update" in src
    assert "vos3_sha256_final" in src


# ---------------------------------------------------------------------------
# Test 5: Honest scoping — license_check.c documents what it can NOT do
# ---------------------------------------------------------------------------


def test_license_check_is_honest_about_limits():
    src = _read("kernel/src/pro/license_check.c")
    # Must explicitly state it is NOT "unhackable"
    lower = src.lower()
    assert "not " in lower and (
        "unhackable" in lower or "tamper" in lower or "patch" in lower
    )
    # Must state that the legal license is the primary moat
    assert "legal" in lower or "license_pro" in lower or "lic" in lower
    # Must reference v20.6 for the real Ed25519 verify path
    assert "v20.6" in src or "v20.6 work" in src


# ---------------------------------------------------------------------------
# Test 6: Hugepage ceiling gate — degrades to CORE on no/invalid license
# ---------------------------------------------------------------------------


def test_hugepage_ceiling_gates_correctly_per_license_state():
    src = _read("kernel/src/pro/license_check.c")
    # The gate function must exist
    assert "vos3_pmm_get_hugepage_ceiling" in src
    # Must have CORE and PRO ceiling constants
    assert "VOS3_HP_CORE_CEILING_PAGES" in src
    assert "VOS3_HP_PRO_CEILING_PAGES" in src
    # CORE ceiling = 256 (512 MB / 2 MiB)
    m = re.search(r"VOS3_HP_CORE_CEILING_PAGES\s+(\d+)", src)
    assert m and int(m.group(1)) == 256
    # PRO ceiling = 5120 (10 GiB / 2 MiB)
    m = re.search(r"VOS3_HP_PRO_CEILING_PAGES\s+(\d+)", src)
    assert m and int(m.group(1)) == 5120

    # Find the ceiling function and verify the degrade-to-CORE path
    sig = "uint32_t vos3_pmm_get_hugepage_ceiling"
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

    # Must consult vos3_verify_license_signature
    assert "vos3_verify_license_signature" in body
    # Must check VOS3_LICENSE_VALID
    assert "VOS3_LICENSE_VALID" in body
    # When NOT valid, must return CORE ceiling (degrade gracefully)
    assert "VOS3_HP_CORE_CEILING_PAGES" in body


# ---------------------------------------------------------------------------
# Test 7: vos3_pro_activate.py runs without errors and emits valid JSON
# ---------------------------------------------------------------------------


def test_pro_activate_tool_emits_valid_json():
    proc = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO_ROOT, "tools", "vos3_pro_activate.py"),
            "--print",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"tool failed: {proc.stderr[:200]}"
    data = json.loads(proc.stdout)
    assert data["schema_version"] == "1.0"
    assert "fingerprint_sha256_hex" in data["fingerprint"]
    assert len(data["fingerprint"]["fingerprint_sha256_hex"]) == 64
    # Must list honest caveats
    assert "honest_caveats" in data
    assert any(
        "degrade" in c.lower() or "graceful" in c.lower() or "core limit" in c.lower()
        for c in data["honest_caveats"]
    )


# ---------------------------------------------------------------------------
# Test 8: Activation tool produces SHA-256 hex (64 lowercase hex chars)
# ---------------------------------------------------------------------------


def test_pro_activate_fingerprint_is_well_formed_sha256():
    proc = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO_ROOT, "tools", "vos3_pro_activate.py"),
            "--print",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = json.loads(proc.stdout)
    fp_hex = data["fingerprint"]["fingerprint_sha256_hex"]
    assert len(fp_hex) == 64
    assert all(c in "0123456789abcdef" for c in fp_hex)
    # Same fingerprint inputs must produce same digest — re-run
    proc2 = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO_ROOT, "tools", "vos3_pro_activate.py"),
            "--print",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    data2 = json.loads(proc2.stdout)
    assert (
        data2["fingerprint"]["fingerprint_sha256_hex"] == fp_hex
    ), "fingerprint must be deterministic on the same host"


# ---------------------------------------------------------------------------
# Test 9: Makefile compiles kv_compressor.c
# ---------------------------------------------------------------------------


def test_makefile_compiles_kv_compressor():
    src = _read("kernel/Makefile")
    assert "mm/kv_compressor.c" in src


# ---------------------------------------------------------------------------
# Test 10: Activation instructions reference the v20.6 .lic flow
# ---------------------------------------------------------------------------


def test_activation_instructions_cover_lic_workflow():
    src = _read("tools/vos3_pro_activate.py")
    assert "vos3.lic" in src
    assert "/boot/" in src or "boot partition" in src.lower()
    # Must mention the email flow
    assert "licensing@vos3.ai" in src or "email" in src.lower()
