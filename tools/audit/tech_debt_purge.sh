#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
#
# tech_debt_purge.sh — TODO/FIXME/HACK marker inventory + CI gate.
#
# v20.6 zero-debt charter (docs/technical/V20_6_MASTER_REPAIR_SPEC.md §5):
# every TODO/FIXME/HACK/XXX/DEPRECATED in the listed source dirs must
# have a corresponding entry in docs/backlog/V20_7_AND_BEYOND.md, OR
# carry a `pragma` comment that the reviewer accepted.
#
# Modes:
#   tools/audit/tech_debt_purge.sh --inventory   # report count, exit 0
#   tools/audit/tech_debt_purge.sh               # CI gate: exits 1 on regression
#
# ==============================================================================
# SCAFFOLD: Reconstructed on 2026-05-01 due to data loss. Integrity vs
# original v20.6 ELF not guaranteed.
# Recovered: lines 26..50 of the original (the SOURCE_DIRS tail + grep
# pipeline + COUNT computation), byte-faithful from a transcript Read.
# Scaffolded: shebang/preamble/baseline-comparison logic (top + tail).
# ==============================================================================

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
REPORT="${REPO_ROOT}/.tech_debt_inventory.txt"

# Baseline: the count under which CI is allowed to pass. v20.6 sets this
# to 0; any regression must either fix the marker or raise the baseline
# in a separate commit with explicit reviewer sign-off.
BASELINE_FILE="${REPO_ROOT}/tools/audit/tech_debt_baseline.txt"
BASELINE="$(cat "${BASELINE_FILE}" 2>/dev/null | tr -d ' \n' || echo 0)"
BASELINE="${BASELINE:-0}"

SOURCE_DIRS=(
  "kernel/src"
  "kernel/include"
  "backend/api"
  "backend/services"
  "backend/middleware"
  "backend/core"
  "backend/memory"
  "backend/pro"
  "frontend/app"
  "frontend/components"
  "frontend/hooks"
  "desktop/src-tauri/src"
  "sdk/python/vos3_sdk"
)

> "$REPORT"
for d in "${SOURCE_DIRS[@]}"; do
    [[ -d "${REPO_ROOT}/${d}" ]] || continue
    # Source-only includes; deliberately skip *.test.* and node_modules.
    # Also skip codegen_routes.py — TODO markers there are intentional
    # CONTENT of code-generation template f-strings shipped to user output
    # (documented in docs/backlog/V20_7_AND_BEYOND.md
    # "Intentional non-debt — codegen templates").
    grep -rn -E '\b(TODO|FIXME|HACK|XXX|DEPRECATED)\b' "${REPO_ROOT}/${d}" \
        --include='*.py' --include='*.c' --include='*.h' \
        --include='*.ts' --include='*.tsx' --include='*.rs' \
        --exclude-dir='node_modules' --exclude='*test*' --exclude='*.test.*' \
        --exclude='codegen_routes.py' \
        2>/dev/null >> "$REPORT" || true
done

COUNT=$(wc -l < "$REPORT" | tr -d ' ')

if [[ "${1:-}" == "--inventory" ]]; then
    echo "Tech-debt markers: ${COUNT}"
    echo "Report: ${REPORT}"
    exit 0
fi

# CI gate mode.
echo "tech_debt_purge: found ${COUNT} markers (baseline ${BASELINE})"

if (( COUNT > BASELINE )); then
    echo ""
    echo "REGRESSION: tech-debt count rose from ${BASELINE} to ${COUNT}." >&2
    echo "Either fix the new markers, or raise ${BASELINE_FILE} with reviewer sign-off." >&2
    echo "" >&2
    echo "Top offenders:" >&2
    head -n 20 "$REPORT" >&2
    exit 1
fi

if (( COUNT < BASELINE )); then
    echo ""
    echo "Improvement: tech-debt fell from ${BASELINE} to ${COUNT}."
    echo "Consider lowering the baseline in ${BASELINE_FILE} to lock the gain in."
fi

exit 0
