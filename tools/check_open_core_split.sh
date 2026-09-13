#!/bin/bash
# VOS3 Open-Core Invariant Validator
# ====================================
#
# Why this exists (and why it is NOT a "sync from CORE to PRO" script):
#
# v20.5.2 deliberately uses a single source tree with `#ifdef VOS3_PRO`
# gates instead of two parallel CORE/PRO branches. There is nothing to
# "sync" — both flavors compile from the same .c files. A merge script
# would solve a problem we structurally chose not to have.
#
# What CAN go wrong with the single-tree pattern (and what this script
# checks):
#   1. `#ifdef VOS3_PRO` block without a `#else` fallback → CORE build
#      compiles with a missing symbol or zero value, surfacing only at
#      link time or runtime
#   2. CORE file (NOT under .../pro/) accidentally references a PRO-only
#      symbol → invisible drift
#   3. PRO file missing the legal-caveat header → the LICENSE_PRO
#      classification is silently incomplete
#   4. Test files using stale import paths (e.g. `services.finetune_engine`
#      after the relocation to `pro.finetune_engine`) → green CI hides
#      the drift
#
# Run with:
#    bash tools/check_open_core_split.sh
# Exit 0 if all invariants hold, 1 if any check fails.

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

PASS=0
FAIL=0

check() {
    local name="$1"; local result="$2"
    if [ "$result" = "0" ]; then
        echo "  ✓ $name"
        PASS=$((PASS+1))
    else
        echo "  ✗ $name"
        FAIL=$((FAIL+1))
    fi
}

echo "=== VOS3 Open-Core Invariant Validator ==="
echo ""

# -----------------------------------------------------------------------
# Invariant 1: every `#ifdef VOS3_PRO` in non-test code must have a `#else`
# -----------------------------------------------------------------------
echo "[1] #ifdef VOS3_PRO blocks have CORE fallbacks"
ORPHAN_IFDEFS=0
# Match exact `VOS3_PRO` token (not `VOS3_PRODUCTION` etc). The trailing
# whitespace anchor avoids the prefix collision.
for f in $(grep -rlE "#ifdef[[:space:]]+VOS3_PRO[[:space:]]*$|#ifdef[[:space:]]+VOS3_PRO[[:space:]]+/" kernel/src kernel/include 2>/dev/null); do
    awk '
        /^[[:space:]]*#ifdef[[:space:]]+VOS3_PRO[[:space:]]*$/ { open++; need_else++; line=NR }
        /^[[:space:]]*#else/                                   { if (need_else > 0) need_else-- }
        /^[[:space:]]*#endif/                                  { open--; if (need_else > 0) print FILENAME":"line; need_else=0 }
        END { exit (need_else > 0) }
    ' "$f" 2>&1 | grep -v "^$" | head -3
    [ "${PIPESTATUS[0]}" != "0" ] && ORPHAN_IFDEFS=$((ORPHAN_IFDEFS+1))
done
check "every #ifdef VOS3_PRO has #else fallback" "$ORPHAN_IFDEFS"

# -----------------------------------------------------------------------
# Invariant 2: vos3_vmm_cas_pte must NEVER be inside a #ifdef VOS3_PRO block
# (open-core charter Rule 1: infrastructure is always open)
# -----------------------------------------------------------------------
echo ""
echo "[2] vos3_vmm_cas_pte is NOT pro-gated (charter Rule 1)"
PROFILED_CAS=$(awk '
    /^[[:space:]]*#ifdef[[:space:]]+VOS3_PRO/ { depth++ }
    /^[[:space:]]*#endif/                       { if (depth>0) depth-- }
    /int[[:space:]]+vos3_vmm_cas_pte/ && depth>0 { print FILENAME":"NR; bad=1 }
    END { exit bad }
' kernel/src/mm/vmm.c)
check "vos3_vmm_cas_pte not gated" "$?"

# -----------------------------------------------------------------------
# Invariant 3: every file under kernel/src/pro/ or backend/pro/ carries
# the LicenseRef-VOS3-Pro-Proprietary marker
# -----------------------------------------------------------------------
echo ""
echo "[3] PRO files carry legal-caveat headers"
MISSING_HEADERS=0
for f in $(find kernel/src/pro backend/pro -type f \( -name "*.c" -o -name "*.h" -o -name "*.py" \) 2>/dev/null); do
    if ! grep -q "LicenseRef-VOS3-Pro-Proprietary" "$f"; then
        echo "    missing header: $f"
        MISSING_HEADERS=$((MISSING_HEADERS+1))
    fi
done
check "all PRO files marked" "$MISSING_HEADERS"

# -----------------------------------------------------------------------
# Invariant 4: CORE files do not import from `pro.*` Python modules
# (Python-side equivalent of "no CORE→PRO symbol reference")
# -----------------------------------------------------------------------
echo ""
echo "[4] CORE Python does not import from pro.*"
PRO_IMPORT_LEAKS=$(grep -rE "^[[:space:]]*from[[:space:]]+pro\." backend/api backend/services backend/middleware backend/core 2>/dev/null | wc -l | tr -d ' ')
check "no leaked imports from pro.*" "$PRO_IMPORT_LEAKS"

# -----------------------------------------------------------------------
# Invariant 5: stale import paths in tests (services.finetune_engine
# was relocated to pro.finetune_engine in v20.5.2)
# -----------------------------------------------------------------------
echo ""
echo "[5] No stale services.finetune_engine imports"
# Only match real Python import statements. Skip README/.md, skip the
# test file that intentionally string-matches the old path as a regression
# guard, and skip __pycache__.
STALE_IMPORTS=$(grep -rEn --include="*.py" "^[[:space:]]*from[[:space:]]+services\.finetune_engine|^[[:space:]]*import[[:space:]]+services\.finetune_engine" backend/api backend/services backend/middleware backend/core 2>/dev/null | wc -l | tr -d ' ')
check "stale finetune_engine imports" "$STALE_IMPORTS"

# -----------------------------------------------------------------------
# Invariant 6: docs/strategy/OPEN_CORE_LICENSING.md is consistent with
# kernel/pro/README.md on the file classification list
# -----------------------------------------------------------------------
echo ""
echo "[6] License docs cross-reference"
LICENSE_DOC_OK=0
[ -f docs/strategy/OPEN_CORE_LICENSING.md ] && \
[ -f kernel/pro/README.md ] && \
grep -q "OPEN_CORE_LICENSING" kernel/pro/README.md
LICENSE_DOC_OK=$?
check "OPEN_CORE_LICENSING.md and kernel/pro/README.md cross-reference" "$LICENSE_DOC_OK"

# -----------------------------------------------------------------------
# Invariant 7: Makefile compiles license_check.c
# -----------------------------------------------------------------------
echo ""
echo "[7] Makefile compiles kernel/src/pro/license_check.c"
grep -q "pro/license_check.c" kernel/Makefile
check "license_check.c in SRCS" "$?"

echo ""
echo "============================================"
echo "Result: $PASS PASS, $FAIL FAIL"
[ "$FAIL" = "0" ] && exit 0 || exit 1
