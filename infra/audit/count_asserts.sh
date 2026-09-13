#!/usr/bin/env bash
# infra/audit/count_asserts.sh — canonical ASSERT counter (Zero-Gap Task 3).
#
# Output (JSON to stdout) is the single source of truth for the
# "N certified ASSERT statements" claim in CLAUDE.md and the Product
# Specification. CI runs this on every PR; if the total drifts, either
# the script is updated or the claim is updated — never both silently.
#
# Exit code:
#   0 — totals collected and printed
#   1 — kernel source tree not found at expected path
#
# Usage:
#   bash infra/audit/count_asserts.sh
#   bash infra/audit/count_asserts.sh --total-only   # prints just the integer

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KSRC="${REPO_ROOT}/kernel/src"

if [[ ! -d "${KSRC}" ]]; then
    echo "ERROR: kernel source tree not found at ${KSRC}" >&2
    exit 1
fi

# Count occurrences of each phase-prefixed ASSERT macro across kernel/src.
# `grep -rE -o` then `wc -l` because some lines contain multiple invocations.
count_pattern() {
    local pattern="$1"
    # `grep` exits 1 when there are no matches, which `set -e` would
    # treat as fatal. Pipe through `wc -l` and `|| true` to swallow
    # the no-match case and report 0.
    { grep -rEoh "${pattern}" "${KSRC}" 2>/dev/null || true; } | wc -l | tr -d ' '
}

GEN=$(count_pattern 'GEN_ASSERT\(')
VEL=$(count_pattern 'VEL_ASSERT\(')
END=$(count_pattern 'END_ASSERT\(')
EXT=$(count_pattern 'EXT_ASSERT\(')
DIV=$(count_pattern 'DIV_ASSERT\(')
TRX=$(count_pattern 'TRX_ASSERT\(')
EVH=$(count_pattern 'EVH_ASSERT\(')
FOG=$(count_pattern 'FOG_ASSERT\(')
OMG=$(count_pattern 'OMG_ASSERT\(')
TGS=$(count_pattern 'TGS_ASSERT\(')
CERT=$(count_pattern 'VOS3_ASSERT_CERT\(')
STATIC=$(count_pattern '_Static_assert\(')
PLAIN=$(count_pattern '\bassert\(')

# Total = every "ASSERT(" macro (case-sensitive) — the canonical metric.
# This is the figure that should match the CLAUDE.md "N certified
# ASSERT statements" line.
TOTAL=$({ grep -rEoh '[A-Z_][A-Z0-9_]*ASSERT[A-Z0-9_]*\(' "${KSRC}" 2>/dev/null || true; } | wc -l | tr -d ' ')

if [[ "${1:-}" == "--total-only" ]]; then
    echo "${TOTAL}"
    exit 0
fi

cat <<JSON
{
  "phase_7_genesis_gate":      ${GEN},
  "phase_8_1_velocity_alpha":  ${VEL},
  "phase_8_2_endurance":       ${END},
  "phase_extreme":             ${EXT},
  "phase_divine":              ${DIV},
  "phase_transcendent":        ${TRX},
  "phase_event_horizon":       ${EVH},
  "phase_fog_of_war":          ${FOG},
  "phase_omega":               ${OMG},
  "phase_tgs":                 ${TGS},
  "vos3_assert_cert_macro":    ${CERT},
  "_static_assert":            ${STATIC},
  "plain_assert":              ${PLAIN},
  "total_uppercase_asserts":   ${TOTAL}
}
JSON
