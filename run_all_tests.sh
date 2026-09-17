#!/usr/bin/env bash
# Run each requested test layer exactly once. --no-kernel is an explicit partial run.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
RUN_KERNEL=1
for arg in "$@"; do
    case "$arg" in
        --no-kernel) RUN_KERNEL=0 ;;
        --help|-h) echo "Usage: bash run_all_tests.sh [--no-kernel]"; exit 0 ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done
LOG_DIR=$(mktemp -d "${TMPDIR:-/tmp}/vos-test-run.XXXXXX") || exit 1
echo "Test logs: $LOG_DIR"
OVERALL=0
run_layer() {
    local name="$1" directory="$2" log="$LOG_DIR/$1.log" code
    local -a statuses
    shift 2
    echo "=== $name ==="
    if [ ! -d "$directory" ]; then
        echo "$name: FAIL (missing component: $directory)"
        OVERALL=1
        return
    fi
    (cd "$directory" && "$@") 2>&1 | tee "$log"
    statuses=("${PIPESTATUS[@]}")
    code=${statuses[0]}
    # Legacy runners report unavailable prerequisites as exit 0 plus [SKIP].
    # A requested layer must actually execute; it cannot silently qualify.
    if [ "$code" -ne 0 ] || [ "${statuses[1]}" -ne 0 ] || grep -q '^\[SKIP\]' "$log"; then
        echo "$name: FAIL (exit $code; see $log)"
        OVERALL=1
    else
        echo "$name: PASS"
    fi
}
run_layer Backend backend bash run_tests.sh --no-cov-on-fail
run_layer Frontend frontend npm run test -- --run
if [ "$RUN_KERNEL" -eq 1 ]; then
    if [ -n "${TEST_ONLY:-}" ]; then
        echo "Kernel: filtered workload (TEST_ONLY=$TEST_ONLY); partial test run"
    fi
    run_layer Kernel kernel bash run_tests.sh
else
    echo "Kernel: NOT REQUESTED (--no-kernel); partial test run"
fi
if [ "$OVERALL" -ne 0 ]; then
    echo "One or more requested test layers FAILED."
    exit 1
fi
if [ "$RUN_KERNEL" -eq 1 ]; then
    if [ -n "${TEST_ONLY:-}" ]; then
        echo "Requested hosted layers and filtered kernel workload passed (TEST_ONLY=$TEST_ONLY); partial test run."
    else
        echo "All requested test layers passed."
    fi
else
    echo "Requested hosted layers passed; kernel tests were not run."
fi
