#!/usr/bin/env bash
# VOS3 Kernel Test Runner
#
# Builds the kernel in bench mode, boots it headlessly in QEMU,
# waits for the benchmark suite to complete, then reports results.
#
# Requirements:
#   - x86_64-elf-gcc cross-compiler in PATH
#   - qemu-system-x86_64 in PATH
#   - disk.img in kernel/ directory (or skips disk test)
#
# Usage:
#   bash run_tests.sh            # normal run
#   TIMEOUT=180 bash run_tests.sh  # custom timeout in seconds

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TIMEOUT="${TIMEOUT:-500}"
TEST_ONLY="${TEST_ONLY:-}"
CONSOLE_LOG="/tmp/vos3_test_console.log"
QEMU_PID_FILE="/tmp/vos3_test_qemu.pid"
PASS_MARKER="WEEK 2 BENCHMARK SUITE: COMPLETE"
FAIL_MARKER="FAILED"
PASS_PATTERN="^\s*\[PASS\]"
FAIL_PATTERN="^\s*\[FAIL\]"

# ============================================================================
# Preflight checks
# ============================================================================

echo "=========================================="
echo "  VOS3 Kernel Test Runner"
echo "=========================================="
echo ""

if ! command -v qemu-system-x86_64 >/dev/null 2>&1; then
    echo "[SKIP] qemu-system-x86_64 not found — kernel tests require QEMU"
    exit 0
fi

if ! command -v x86_64-elf-gcc >/dev/null 2>&1; then
    echo "[SKIP] x86_64-elf-gcc not found — kernel tests require cross-compiler"
    exit 0
fi

# ============================================================================
# Build
# ============================================================================

TEST_ONLY_FLAG=""
if [ -n "$TEST_ONLY" ]; then
    TEST_ONLY_FLAG="TEST_ONLY=$TEST_ONLY"
    echo "[1/3] Building kernel + user space in BENCH_MODE (filter: $TEST_ONLY)..."
else
    echo "[1/3] Building kernel + user space in BENCH_MODE..."
fi
rm -f "$CONSOLE_LOG"

# Force clean rebuild of user space with bench mode enabled
# (without clean, make may skip recompilation of already-built binaries)
make -C ../user clean 2>&1 | tail -2
make -C ../user BENCH_MODE=1 $TEST_ONLY_FLAG 2>&1 | tail -5

# Force clean rebuild of kernel (re-embeds user binaries)
# Pass BENCH_MODE=1 to suppress VOS3_DEBUG logging (serial bottleneck fix)
make clean && make BENCH_MODE=1 $TEST_ONLY_FLAG 2>&1 | tail -5

if [ ! -f "build/vos3.elf" ]; then
    echo "[FAIL] Build failed — build/vos3.elf not found"
    exit 1
fi
echo "[OK] Kernel built: build/vos3.elf"
echo ""

# ============================================================================
# Boot QEMU
# ============================================================================

echo "[2/3] Booting QEMU (timeout=${TIMEOUT}s)..."

DISK_ARGS=""
if [ -f "disk.img" ]; then
    DISK_ARGS="-drive file=disk.img,format=raw,if=none,id=disk0,cache=directsync -device virtio-blk-pci,drive=disk0"
fi

qemu-system-x86_64 \
    -kernel build/vos3.elf \
    -m 3072M \
    -cpu qemu64,+rdrand,+rdseed \
    -chardev file,id=con,path="$CONSOLE_LOG" \
    -serial chardev:con \
    -display none \
    -daemonize \
    -pidfile "$QEMU_PID_FILE" \
    -nic user,model=virtio-net-pci \
    $DISK_ARGS

QEMU_PID="$(cat "$QEMU_PID_FILE" 2>/dev/null || echo "")"
echo "[OK] QEMU started (PID=${QEMU_PID})"

# ============================================================================
# Wait for completion marker
# ============================================================================

echo "[3/3] Waiting for benchmark suite to complete..."

ELAPSED=0
FOUND=0
while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    if [ -f "$CONSOLE_LOG" ] && grep -q "$PASS_MARKER" "$CONSOLE_LOG" 2>/dev/null; then
        FOUND=1
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
    printf "\r    Waiting... %ds / %ds" "$ELAPSED" "$TIMEOUT"
done
echo ""

# Kill QEMU
if [ -n "$QEMU_PID" ] && kill -0 "$QEMU_PID" 2>/dev/null; then
    kill "$QEMU_PID" 2>/dev/null || true
fi
rm -f "$QEMU_PID_FILE"

# ============================================================================
# Parse results
# ============================================================================

echo ""
echo "=========================================="
echo "  Kernel Test Results"
echo "=========================================="

if [ "$FOUND" -eq 0 ]; then
    echo "[FAIL] Timed out after ${TIMEOUT}s — benchmark marker not found"
    if [ -f "$CONSOLE_LOG" ]; then
        echo "Last 20 lines of console log:"
        tail -20 "$CONSOLE_LOG"
    fi
    exit 1
fi

echo "[OK] Benchmark suite completed"
echo ""

# Count PASS/FAIL lines in log
if [ -f "$CONSOLE_LOG" ]; then
    PASS_COUNT=$(grep -c "PASS\]" "$CONSOLE_LOG" 2>/dev/null || true)
    FAIL_COUNT=$(grep -c "FAIL\]" "$CONSOLE_LOG" 2>/dev/null || true)
    PASS_COUNT="${PASS_COUNT:-0}"
    FAIL_COUNT="${FAIL_COUNT:-0}"
    echo "  PASS: $PASS_COUNT"
    echo "  FAIL: $FAIL_COUNT"
    echo ""

    if [ "${FAIL_COUNT}" != "0" ] && [ -n "${FAIL_COUNT}" ]; then
        echo "Failed items:"
        grep "FAIL\]" "$CONSOLE_LOG" | head -20
        echo ""
        exit 1
    fi
fi

echo "=========================================="
echo "  ALL KERNEL TESTS PASSED"
echo "=========================================="
exit 0
