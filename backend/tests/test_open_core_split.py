"""
VOS3 v20.5.2 — Open-Core Split Source-Shape Tests
====================================================

Validates the open-core dual-license scaffolding:
  - kernel/pro/ marker README documents the split
  - backend/pro/ houses the relocated finetune_engine
  - kernel/src/pro/license_check.c implements the boot-time gate
  - Makefile wires VOS3_BUILD_TYPE → VOS3_PRO define
  - pmm.c #ifdef VOS3_PRO gates 5,120-entry vs 256-entry hugepage pool
  - License headers are present on PRO files (with legal-review caveat)
  - The open-core charter is documented in docs/strategy/

Anti-regression specifically:
  - vos3_vmm_cas_pte is NOT inside any #ifdef VOS3_PRO (it's infrastructure,
    not a gated feature). Test 7 enforces this loudly.
"""

from __future__ import annotations

import os
import re

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read(path: str) -> str:
    full = os.path.join(REPO_ROOT, path)
    with open(full) as f:
        return f.read()


def _exists(path: str) -> bool:
    return os.path.isfile(os.path.join(REPO_ROOT, path))


# ---------------------------------------------------------------------------
# Test 1: pro/ directories exist with marker READMEs
# ---------------------------------------------------------------------------


def test_pro_directories_have_readmes():
    assert _exists("kernel/pro/README.md")
    assert _exists("backend/pro/README.md")
    kr = _read("kernel/pro/README.md")
    assert "Open-Core" in kr or "open-core" in kr
    # Must explicitly state which files are CORE vs PRO
    assert "vos3_vmm_cas_pte" in kr
    assert "always available" in kr.lower() or "always present" in kr.lower()


# ---------------------------------------------------------------------------
# Test 2: backend/pro/finetune_engine.py is the relocated file
# ---------------------------------------------------------------------------


def test_finetune_engine_relocated_to_backend_pro():
    assert _exists("backend/pro/finetune_engine.py")
    assert _exists("backend/pro/__init__.py")
    # Old location must not exist
    assert not _exists(
        "backend/services/finetune_engine.py"
    ), "Old location still present — git mv was incomplete"
    # Header must carry the proposed proprietary marker WITH legal caveat
    src = _read("backend/pro/finetune_engine.py")
    assert "LicenseRef-VOS3-Pro-Proprietary" in src
    assert "PROPOSED" in src.upper()
    assert "MIT" in src and "legal review" in src.lower()


# ---------------------------------------------------------------------------
# Test 3: license_check.c exists and is honest about scope
# ---------------------------------------------------------------------------


def test_license_check_implementation_is_honest_stub():
    # File at the actual build path (under SRC_DIR for Makefile compatibility)
    assert _exists("kernel/src/pro/license_check.c")
    src = _read("kernel/src/pro/license_check.c")
    # API surface
    assert "vos3_pro_license_check(" in src
    assert "vos3_pro_build_label(" in src
    # Compile-time gate using VOS3_PRO
    assert "#ifdef VOS3_PRO" in src
    # MUST explicitly state what it does NOT disable (the open-core charter)
    assert "vos3_vmm_cas_pte" in src or "infrastructure" in src.lower()
    # Honest caveat about real signature verification being deferred
    assert "v20.6" in src
    # Boot-log message for both flavors
    assert "VOS3 build:" in src or "[PRO]" in src or "[CORE]" in src


# ---------------------------------------------------------------------------
# Test 4: Makefile wires VOS3_BUILD_TYPE flag
# ---------------------------------------------------------------------------


def test_makefile_supports_build_type_flag():
    src = _read("kernel/Makefile")
    # VOS3_BUILD_TYPE conditional must be present
    assert "VOS3_BUILD_TYPE" in src
    assert "ifeq ($(VOS3_BUILD_TYPE),CORE)" in src
    assert "ifeq ($(VOS3_BUILD_TYPE),PRO)" in src
    # CFLAGS += -DVOS3_PRO must appear in PRO branch
    assert "-DVOS3_PRO" in src
    # license_check.c must be in SRCS
    assert "pro/license_check.c" in src


# ---------------------------------------------------------------------------
# Test 5: pmm.c gates the 5,120 pool size behind #ifdef VOS3_PRO
# ---------------------------------------------------------------------------


def test_pmm_hugepage_pool_size_is_pro_gated():
    src = _read("kernel/src/mm/pmm.c")
    # Locate the VOS3_HUGEPAGE_POOL_MAX block — must have BOTH branches
    assert "#ifdef VOS3_PRO" in src
    assert "VOS3_HUGEPAGE_POOL_MAX  5120" in src or "VOS3_HUGEPAGE_POOL_MAX 5120" in src
    # CORE branch must define the smaller ceiling
    m = re.search(
        r"#else\s*\n\s*#define\s+VOS3_HUGEPAGE_POOL_MAX\s+(\d+)",
        src,
    )
    assert m is not None, "CORE branch (#else) must define a smaller pool"
    core_size = int(m.group(1))
    assert (
        core_size <= 256
    ), f"CORE pool must be <= 256 entries (512 MB ceiling); got {core_size}"
    # 256 entries × 2 MiB = 512 MB — matches docs
    assert core_size == 256


# ---------------------------------------------------------------------------
# Test 6: Open-core licensing strategy doc exists
# ---------------------------------------------------------------------------


def test_open_core_licensing_strategy_documented():
    assert _exists("docs/strategy/OPEN_CORE_LICENSING.md")
    src = _read("docs/strategy/OPEN_CORE_LICENSING.md")
    # Must articulate the three rules
    assert "Rule 1" in src and "Rule 2" in src and "Rule 3" in src
    # Must list vos3_vmm_cas_pte explicitly as CORE infrastructure
    assert "vos3_vmm_cas_pte" in src
    # Must explicitly note that the LICENSE file remains MIT
    assert "LICENSE" in src and "MIT" in src
    # Roadmap section must exist
    assert "Roadmap" in src or "roadmap" in src
    # Honest scope section
    assert "What This Document Does NOT Do" in src or "does not" in src.lower()


# ---------------------------------------------------------------------------
# Test 7: ANTI-REGRESSION — vos3_vmm_cas_pte is NOT under #ifdef VOS3_PRO
# ---------------------------------------------------------------------------


def test_vos3_vmm_cas_pte_is_not_pro_gated():
    """Critical open-core charter rule: infrastructure primitives are
    NEVER gated. vos3_vmm_cas_pte is used by 20+ files including
    vfio_core, dma_warp, ai_slots, virtio_vbus. If we put it behind
    #ifdef VOS3_PRO, every CORE build of the kernel would fail to link
    or, worse, would link with stub functions and silently fail at
    runtime. This test fails LOUDLY if anyone ever tries to gate it."""
    src = _read("kernel/src/mm/vmm.c")
    # Find the cas_pte definition
    sig_match = re.search(
        r"int\s+vos3_vmm_cas_pte\s*\([^)]*\)",
        src,
    )
    assert sig_match is not None, "vos3_vmm_cas_pte definition not found in vmm.c"
    # Walk backwards from the definition to find any #ifdef block
    # boundary. The function must NOT be inside an #ifdef VOS3_PRO ...
    # #endif block. We do this by counting #ifdef/#endif balance.
    pos = sig_match.start()
    prefix = src[:pos]

    # Naive but reliable: count #ifdef VOS3_PRO without matching #endif
    open_pro_blocks = len(re.findall(r"^\s*#ifdef\s+VOS3_PRO\b", prefix, re.MULTILINE))
    closed_pro_blocks = len(re.findall(r"^\s*#endif\b", prefix, re.MULTILINE))
    # If there's an open #ifdef VOS3_PRO without a matching #endif before
    # cas_pte, the function is gated — that's the regression.
    # NB: this is approximate (every #endif counts even if it's for an
    # unrelated #ifdef), but for a CORE primitive it should be safe.
    assert not (open_pro_blocks > closed_pro_blocks), (
        "vos3_vmm_cas_pte appears to be inside a #ifdef VOS3_PRO block. "
        "This violates Open-Core Rule 1: infrastructure is always present. "
        "See kernel/pro/README.md and docs/strategy/OPEN_CORE_LICENSING.md."
    )


# ---------------------------------------------------------------------------
# Test 8: kernel/pro/ documentation marker is honest about WHY files
#         physically stay under kernel/src/
# ---------------------------------------------------------------------------


def test_kernel_pro_readme_explains_structural_compromise():
    src = _read("kernel/pro/README.md")
    # Must explain the boot_drivers.c -> tpm2 dependency that prevents
    # physical relocation of tpm2.c
    assert "boot_drivers.c" in src or "boot/drivers" in src.lower()
    # Must explain the ai_guard_internal.h shared header
    assert "ai_guard_internal" in src or "ai_pte" in src
    # Must explicitly state that kernel/src/ paths are the actual source
    # of truth, kernel/pro/ is a logical marker
    assert "logical" in src.lower() or "marker" in src.lower()


# ---------------------------------------------------------------------------
# Test 9: import path migration — finetune_engine via pro.* not services.*
# ---------------------------------------------------------------------------


def test_finetune_engine_imported_from_new_path():
    src = _read("backend/tests/test_finetune_rigor.py")
    # Old import path must NOT appear (would mean the move was incomplete)
    assert (
        "from services.finetune_engine" not in src
    ), "test_finetune_rigor.py still imports from old services.finetune_engine path"
    # New import path MUST appear
    assert "from pro.finetune_engine" in src
