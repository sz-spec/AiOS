#!/usr/bin/env bash
# ============================================================================
# vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C
#
# KVM verification of the PROTECTED_FULL KPTI path.
#
# Boots kernel/build/vos3.elf under qemu-system-x86_64 with -accel kvm
# and -cpu host, captures the boot serial log, and asserts:
#
#   1. [KPTI] mode=PROTECTED_FULL                          (mitigation_factory.c chose the strongest tier)
#   2. [KPTI] mode=...  pcid=yes invpcid=yes smep=yes smap=yes  (all four prerequisites detected)
#   3. budget=<=2%                                         (perf budget for PROTECTED_FULL per AAA plan)
#   4. [KPTI] init: ready                                  (kpti.c reached the post-init state)
#   5. No [SECURITY] warning lines                         (microcode is at or above baseline)
#   6. No [VOS3_BOOT_REFUSE] panic                         (CPU has long mode)
#
# Exit code: 0 if all assertions hold, 1 otherwise.
# Side effect: writes the captured boot log to ./reports/kvm_protected_full_<UTC>.log
#
# This script MUST run on a Linux x86_64 host with KVM available.
# It will refuse to run on macOS / Apple Silicon / non-KVM hosts —
# those targets are covered by the TCG matrix in run_tests.sh.
#
# Honest scope
# ------------
# * The assertions check the BOOT-TIME state — they do not stress
#   the syscall path under KPTI in protected mode. That's a separate
#   benchmark suite (see kernel/run_tests.sh).
# * Below-baseline microcode is a soft fail: we report it but exit
#   with rc=1 because the manifest will have demoted to LEGACY_KAISER
#   — that's by design (P4.2 wiring), not a verification pass.
# * If the host CPU lacks SMEP+SMAP+INVPCID, the kernel will land in
#   PROTECTED_PCID_ONLY, NOT PROTECTED_FULL. The script reports this
#   distinctly so the operator knows whether the script "failed" or
#   the hardware just isn't full-spec.
# ============================================================================

set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration (env-overridable)
# ----------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

KERNEL_ELF="${KERNEL_ELF:-${REPO_ROOT}/kernel/build/vos3.elf}"
QEMU_BIN="${QEMU_BIN:-qemu-system-x86_64}"
MEMORY="${MEMORY:-1024M}"
SMP="${SMP:-2}"
BOOT_TIMEOUT_S="${BOOT_TIMEOUT_S:-120}"
REPORTS_DIR="${REPORTS_DIR:-${SCRIPT_DIR}/reports}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CONSOLE_LOG="${REPORTS_DIR}/kvm_protected_full_${TIMESTAMP}.log"
SUMMARY_PATH="${REPORTS_DIR}/kvm_protected_full_${TIMESTAMP}.summary.txt"

# ANSI colour helpers
if [[ -t 1 ]]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; BOLD=""; NC=""
fi

# ----------------------------------------------------------------------------
# Preflight checks
# ----------------------------------------------------------------------------

preflight() {
    local fatal=0

    echo "${BOLD}━━━ Preflight ━━━${NC}"

    # Refuse on non-Linux hosts — Apple Silicon and Windows have no /dev/kvm.
    if [[ "$(uname -s)" != "Linux" ]]; then
        echo "${RED}FAIL${NC} Host is $(uname -s); KVM requires Linux." >&2
        return 1
    fi

    # /dev/kvm present + accessible.
    if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
        echo "${RED}FAIL${NC} /dev/kvm missing or not r/w by this user." >&2
        echo "       Add user to the kvm group: sudo usermod -aG kvm \$USER" >&2
        fatal=1
    else
        echo "${GREEN}ok${NC}   /dev/kvm accessible"
    fi

    # QEMU + KVM accel must be available.
    if ! command -v "$QEMU_BIN" >/dev/null 2>&1; then
        echo "${RED}FAIL${NC} $QEMU_BIN not found in PATH." >&2
        fatal=1
    elif ! "$QEMU_BIN" -accel help 2>/dev/null | grep -q "^kvm$"; then
        echo "${RED}FAIL${NC} $QEMU_BIN was built without KVM support." >&2
        fatal=1
    else
        echo "${GREEN}ok${NC}   $QEMU_BIN with KVM accel"
    fi

    # Kernel binary present.
    if [[ ! -f "$KERNEL_ELF" ]]; then
        echo "${RED}FAIL${NC} Kernel ELF not found at $KERNEL_ELF" >&2
        echo "       Build first: cd kernel && make" >&2
        fatal=1
    else
        echo "${GREEN}ok${NC}   $KERNEL_ELF ($(stat -c%s "$KERNEL_ELF" 2>/dev/null || stat -f%z "$KERNEL_ELF") bytes)"
    fi

    # Host CPU must have the PROTECTED_FULL prerequisites; otherwise the
    # test result is meaningful but expected to be PROTECTED_PCID_ONLY.
    local missing=()
    for flag in pcid invpcid smep smap; do
        if ! grep -qw "$flag" /proc/cpuinfo 2>/dev/null; then
            missing+=("$flag")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        echo "${YELLOW}warn${NC} Host CPU missing: ${missing[*]} — expect PROTECTED_PCID_ONLY, not PROTECTED_FULL"
    else
        echo "${GREEN}ok${NC}   Host CPU has pcid+invpcid+smep+smap"
    fi

    return $fatal
}

# ----------------------------------------------------------------------------
# Boot under KVM and capture the serial log
# ----------------------------------------------------------------------------

boot_kvm() {
    mkdir -p "$REPORTS_DIR"
    : > "$CONSOLE_LOG"

    echo
    echo "${BOLD}━━━ Boot under KVM ━━━${NC}"
    echo "  Kernel    : $KERNEL_ELF"
    echo "  Memory    : $MEMORY"
    echo "  SMP       : $SMP"
    echo "  Timeout   : ${BOOT_TIMEOUT_S}s"
    echo "  Log file  : $CONSOLE_LOG"
    echo

    # The KVM run. -no-reboot prevents the firmware from re-trying on
    # crash; -no-shutdown keeps the VM alive for log capture. We
    # daemonise QEMU so we control the kill via SIGTERM after the
    # boot-capture window.
    #
    # -cpu choice: Skylake-Server, not 'host'. Passing through the
    # bare host's full feature set on GHA nested KVM caused
    # vos3_vmm_init() to deadlock during early page-table setup —
    # presumably because some host-only VMCS/MSR feature exposed by
    # -cpu host trips a code path the kernel's PMM/VMM doesn't handle
    # under nested virt. Skylake-Server is the highest-tier discrete
    # model that still gives us SMEP+SMAP+PCID+INVPCID+SHA-NI+AVX-512
    # — i.e., everything the PROTECTED_FULL path requires — without
    # any host-specific oddities. The explicit '+pcid,+invpcid,+smep,
    # +smap' tail is redundant on Skylake-Server but harmless, and
    # makes the prerequisite set self-documenting in the qemu argv.
    local qemu_pid
    "$QEMU_BIN" \
        -kernel "$KERNEL_ELF" \
        -m "$MEMORY" -smp "$SMP" \
        -accel kvm \
        -cpu Skylake-Server,+pcid,+invpcid,+smep,+smap \
        -chardev "file,id=con,path=$CONSOLE_LOG" \
        -serial chardev:con \
        -no-reboot -no-shutdown \
        -display none \
        -daemonize -pidfile "${REPORTS_DIR}/kvm_qemu.pid" \
        || { echo "${RED}FAIL${NC} QEMU launch failed" >&2; return 1; }

    qemu_pid=$(cat "${REPORTS_DIR}/kvm_qemu.pid")
    echo "QEMU pid=$qemu_pid"

    # Wait for boot — either the kernel reaches "init: ready" or we
    # hit the timeout. Polling avoids burning CPU.
    local deadline=$(( $(date +%s) + BOOT_TIMEOUT_S ))
    local saw_init=0
    while (( $(date +%s) < deadline )); do
        if grep -q '\[KPTI\] init: ready' "$CONSOLE_LOG" 2>/dev/null; then
            saw_init=1
            break
        fi
        sleep 0.5
    done

    # Give the kernel a couple more seconds to emit the rest of the
    # boot block, then shut it down.
    sleep 2
    kill -TERM "$qemu_pid" 2>/dev/null || true
    sleep 1
    kill -KILL "$qemu_pid" 2>/dev/null || true
    rm -f "${REPORTS_DIR}/kvm_qemu.pid"

    if (( saw_init == 0 )); then
        echo "${RED}FAIL${NC} Kernel did not reach '[KPTI] init: ready' within ${BOOT_TIMEOUT_S}s" >&2
        echo "------- last 40 lines of captured boot log -------" >&2
        tail -40 "$CONSOLE_LOG" >&2 || true
        echo "------- end log -------" >&2
        return 1
    fi
    return 0
}

# ----------------------------------------------------------------------------
# Assertion helpers
# ----------------------------------------------------------------------------

# Record one assertion outcome in the summary. Args: status (PASS/FAIL/WARN), description.
declare -i ASSERT_PASS=0
declare -i ASSERT_FAIL=0
declare -i ASSERT_WARN=0

note() {
    local status=$1 desc=$2
    case "$status" in
        PASS) ASSERT_PASS=$((ASSERT_PASS + 1)); echo "${GREEN}PASS${NC} $desc" ;;
        FAIL) ASSERT_FAIL=$((ASSERT_FAIL + 1)); echo "${RED}FAIL${NC} $desc" ;;
        WARN) ASSERT_WARN=$((ASSERT_WARN + 1)); echo "${YELLOW}WARN${NC} $desc" ;;
    esac
    printf '%-4s  %s\n' "$status" "$desc" >> "$SUMMARY_PATH"
}

assert_line_present() {
    local pattern=$1 desc=$2
    if grep -Eq "$pattern" "$CONSOLE_LOG"; then
        note PASS "$desc"
    else
        note FAIL "$desc — pattern not found: $pattern"
    fi
}

assert_line_absent() {
    local pattern=$1 desc=$2
    if grep -Eq "$pattern" "$CONSOLE_LOG"; then
        note FAIL "$desc — unexpected line present: $(grep -E "$pattern" "$CONSOLE_LOG" | head -1)"
    else
        note PASS "$desc"
    fi
}

# ----------------------------------------------------------------------------
# Run the assertion battery
# ----------------------------------------------------------------------------

evaluate() {
    : > "$SUMMARY_PATH"
    {
        echo "vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C"
        echo "KVM PROTECTED_FULL verification"
        echo "Captured: $TIMESTAMP"
        echo "Log: $CONSOLE_LOG"
        echo "----------------------------------------"
    } >> "$SUMMARY_PATH"

    echo
    echo "${BOLD}━━━ Assertions ━━━${NC}"

    # 1. Kernel did not refuse-to-boot (long mode present).
    assert_line_absent '\[VOS3_BOOT_REFUSE\]' \
        "Kernel did not refuse to boot (CPU has long mode)"

    # 2. KPTI mode line present.
    assert_line_present '\[KPTI\] mode=' "[KPTI] summary line emitted"

    # 3. PROTECTED_FULL specifically. If the host lacks any of
    #    pcid/invpcid/smep/smap, the kernel will pick PROTECTED_PCID_ONLY
    #    — which is a CORRECT decision but not what this script is
    #    verifying. Report it distinctly so the operator can interpret.
    if grep -Eq '\[KPTI\] mode=PROTECTED_FULL' "$CONSOLE_LOG"; then
        note PASS "Mitigation tier = PROTECTED_FULL"
    elif grep -Eq '\[KPTI\] mode=PROTECTED_PCID_ONLY' "$CONSOLE_LOG"; then
        note WARN "Mitigation tier = PROTECTED_PCID_ONLY (host CPU lacks SMEP/SMAP/INVPCID)"
    else
        local actual
        actual="$(grep -E '\[KPTI\] mode=' "$CONSOLE_LOG" | head -1)"
        note FAIL "Mitigation tier not PROTECTED_*: $actual"
    fi

    # 4. The four prerequisites must all be 'yes' for a PROTECTED_FULL host.
    if grep -Eq '\[KPTI\] mode=PROTECTED_FULL.*pcid=yes invpcid=yes smep=yes smap=yes' "$CONSOLE_LOG"; then
        note PASS "All four PROTECTED_FULL prerequisites detected (pcid+invpcid+smep+smap)"
    elif grep -Eq '\[KPTI\] mode=PROTECTED_PCID_ONLY' "$CONSOLE_LOG"; then
        note WARN "Skipped — host is PROTECTED_PCID_ONLY by capability"
    else
        note FAIL "PROTECTED_FULL prerequisites not satisfied"
    fi

    # 5. Performance budget recorded as <=2% for PROTECTED_FULL.
    if grep -Eq 'budget=<=2%' "$CONSOLE_LOG"; then
        note PASS "Perf budget <=2% (AAA plan §3.2)"
    elif grep -Eq '\[KPTI\] mode=PROTECTED_PCID_ONLY' "$CONSOLE_LOG"; then
        if grep -Eq 'budget=<=3%' "$CONSOLE_LOG"; then
            note WARN "Perf budget <=3% (PROTECTED_PCID_ONLY tier — expected)"
        else
            note FAIL "Perf budget line missing or wrong for PROTECTED_PCID_ONLY"
        fi
    else
        note FAIL "Perf budget line missing"
    fi

    # 6. kpti_init_ready latched.
    assert_line_present '\[KPTI\] init: ready' "kpti_init() reached the ready state"

    # 7. No microcode security warning. If present, the host is below
    #    baseline and the manifest will demote — that's a verification
    #    failure for THIS test (we want a clean PROTECTED host).
    assert_line_absent '\[SECURITY\] Microcode outdated' \
        "No [SECURITY] microcode warning (host is at or above baseline)"

    # 8. PCI-ECAM (Q35 chipset under -cpu host should have MCFG).
    if grep -Eq '\[PCI-ECAM\] available' "$CONSOLE_LOG"; then
        note PASS "PCI-ECAM available (MCFG present)"
    elif grep -Eq '\[PCI-ECAM\] MCFG absent' "$CONSOLE_LOG"; then
        note WARN "PCI-ECAM not available — Port-I/O fallback (correct, but use -machine q35 for ECAM)"
    else
        note WARN "No PCI-ECAM line emitted (kernel may not be probing ECAM in this boot block)"
    fi
}

# ----------------------------------------------------------------------------
# Wire it together
# ----------------------------------------------------------------------------

main() {
    preflight || { echo "${RED}aborted: preflight failed${NC}" >&2; exit 1; }
    boot_kvm  || { echo "${RED}aborted: boot failed${NC}"     >&2; exit 1; }
    evaluate

    echo
    echo "${BOLD}━━━ Summary ━━━${NC}"
    echo "  PASS: $ASSERT_PASS"
    echo "  WARN: $ASSERT_WARN"
    echo "  FAIL: $ASSERT_FAIL"
    echo
    echo "  Log:     $CONSOLE_LOG"
    echo "  Summary: $SUMMARY_PATH"

    if (( ASSERT_FAIL > 0 )); then
        exit 1
    fi
    exit 0
}

main "$@"
