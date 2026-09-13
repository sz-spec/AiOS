#!/usr/bin/env bash
# VOS3 Unified Test Runner
#
# Runs all three test layers in sequence and prints a summary table.
#
# Usage:
#   bash run_all_tests.sh            # run all layers
#   bash run_all_tests.sh --no-kernel  # skip kernel (no QEMU/cross-compiler needed)
#
# Exit code: 0 if all layers pass, 1 if any layer fails.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ============================================================================
# Configuration
# ============================================================================

RUN_KERNEL=1
for arg in "$@"; do
    case "$arg" in
        --no-kernel) RUN_KERNEL=0 ;;
    esac
done

# Results tracking
BACKEND_STATUS="NOT RUN"
FRONTEND_STATUS="NOT RUN"
KERNEL_STATUS="NOT RUN"

BACKEND_PASS=0
BACKEND_FAIL=0
FRONTEND_PASS=0
FRONTEND_FAIL=0

# ============================================================================
# Helpers
# ============================================================================

section() {
    echo ""
    echo "╔══════════════════════════════════════════╗"
    printf  "║  %-40s║\n" "$1"
    echo "╚══════════════════════════════════════════╝"
    echo ""
}

# ============================================================================
# Backend Tests
# ============================================================================

section "Backend Tests (Python / pytest)"

if [ -f "backend/run_tests.sh" ]; then
    pushd backend >/dev/null
    if bash run_tests.sh --no-cov-on-fail 2>&1; then
        BACKEND_STATUS="PASS"
    else
        BACKEND_STATUS="FAIL"
    fi
    # Try to extract pass/fail counts from pytest output
    RESULT=$(python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3)
    BACKEND_PASS=$(echo "$RESULT" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo "?")
    BACKEND_FAIL=$(echo "$RESULT" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || echo "0")
    popd >/dev/null
else
    echo "[WARN] backend/run_tests.sh not found"
    BACKEND_STATUS="SKIP"
fi

# ============================================================================
# Frontend Tests
# ============================================================================

section "Frontend Tests (Vitest)"

if [ -f "frontend/package.json" ]; then
    pushd frontend >/dev/null
    if npx vitest run 2>&1; then
        FRONTEND_STATUS="PASS"
    else
        FRONTEND_STATUS="FAIL"
    fi
    # Try to extract counts
    RESULT=$(npx vitest run --reporter=verbose 2>&1 | tail -5)
    FRONTEND_PASS=$(echo "$RESULT" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo "?")
    FRONTEND_FAIL=$(echo "$RESULT" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || echo "0")
    popd >/dev/null
else
    echo "[WARN] frontend/package.json not found"
    FRONTEND_STATUS="SKIP"
fi

# ============================================================================
# Kernel Tests
# ============================================================================

if [ "$RUN_KERNEL" -eq 1 ]; then
    section "Kernel Tests (QEMU)"
    if [ -f "kernel/run_tests.sh" ]; then
        pushd kernel >/dev/null
        if bash run_tests.sh 2>&1; then
            KERNEL_STATUS="PASS"
        else
            # Script exits 0 on SKIP (no QEMU/compiler), 1 on FAIL
            if [ $? -eq 0 ]; then
                KERNEL_STATUS="SKIP"
            else
                KERNEL_STATUS="FAIL"
            fi
        fi
        popd >/dev/null
    else
        echo "[WARN] kernel/run_tests.sh not found"
        KERNEL_STATUS="SKIP"
    fi
else
    KERNEL_STATUS="SKIP (--no-kernel)"
fi

# ============================================================================
# Summary Table
# ============================================================================

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║         VOS3 Test Suite Summary          ║"
echo "╠══════════════╦═══════╦═══════╦═══════════╣"
echo "║ Layer        ║ Pass  ║ Fail  ║ Status    ║"
echo "╠══════════════╬═══════╬═══════╬═══════════╣"
printf "║ %-12s ║ %-5s ║ %-5s ║ %-9s ║\n" "Backend"  "$BACKEND_PASS"  "$BACKEND_FAIL"  "$BACKEND_STATUS"
printf "║ %-12s ║ %-5s ║ %-5s ║ %-9s ║\n" "Frontend" "$FRONTEND_PASS" "$FRONTEND_FAIL" "$FRONTEND_STATUS"
printf "║ %-12s ║ %-5s ║ %-5s ║ %-9s ║\n" "Kernel"   "-"              "-"              "$KERNEL_STATUS"
echo "╚══════════════╩═══════╩═══════╩═══════════╝"
echo ""

# ============================================================================
# Exit code
# ============================================================================

OVERALL_PASS=1
[ "$BACKEND_STATUS"  = "FAIL" ] && OVERALL_PASS=0
[ "$FRONTEND_STATUS" = "FAIL" ] && OVERALL_PASS=0
[ "$KERNEL_STATUS"   = "FAIL" ] && OVERALL_PASS=0

if [ "$OVERALL_PASS" -eq 1 ]; then
    echo "All test layers passed (or skipped)."
    exit 0
else
    echo "One or more test layers FAILED."
    exit 1
fi
