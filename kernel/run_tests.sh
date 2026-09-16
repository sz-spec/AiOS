#!/usr/bin/env bash
# Full BENCH_MODE workload through the unified Limine ISO build.
# TIMEOUT=500 TEST_ONLY=substring bash kernel/run_tests.sh
# TEST_ONLY is explicitly partial; missing prerequisites fail closed.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/../scripts/native_bench.py" "$@"
