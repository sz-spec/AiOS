#!/usr/bin/env bash
# Kernel A3/A6 host-audit runner (TEST_PLAN_300 §A3/§A6).
# Compiles the freestanding crypto/verify sources directly and runs the
# adversarial host tests. Deterministic, no QEMU, no cross-compiler needed
# (the gates are pure ed25519/sha512/sha256 + verify logic).
#
#   bash kernel/tests/audit/run_kernel_audit.sh
set -euo pipefail
cd "$(dirname "$0")/../.."        # -> kernel/
CC="${CC:-clang}"
INC="-Iinclude"
OUT="${TMPDIR:-/tmp}"
rc=0

echo "=== A1 — PTE W^X sanitizer + memory boundary enforcement ==="
$CC -O2 $INC -o "$OUT/a1_audit" tests/audit/test_a1_w_x_sanitizer.c
"$OUT/a1_audit" || rc=1

echo
echo "=== A6 — model/driver SecureBoot signature gate ==="
$CC -O2 $INC -o "$OUT/a6_audit" tests/audit/test_a6_model_signature.c
"$OUT/a6_audit" || rc=1

echo
echo "=== A3 — VBus per-command HMAC trailer verification ==="
# sha256.c pulls kernel/include/vos/string.h (x86 asm); the test #defines
# VOS3_STRING_H before including it, so host libc mem* are used instead.
$CC -O2 $INC -o "$OUT/a3_audit" tests/audit/test_a3_vbus_hmac.c
"$OUT/a3_audit" || rc=1

echo
echo "=== A8 — vVFS isolation / path-traversal / multi-tenant ACL ==="
$CC -O2 $INC -o "$OUT/a8_audit" tests/audit/test_a8_vector_vfs_sandbox.c
"$OUT/a8_audit" || rc=1

echo
echo "=== M3 — vVFS model-signature read-gate (ENFORCEMENT ON, mock key) ==="
# Compiled with the M3 macro forced ON for the test build only — the
# production default in include/vos/vvfs.h (=0) is NOT touched.
$CC -O2 $INC -DVOS3_VVFS_REQUIRE_MODEL_SIG=1 \
    -o "$OUT/m3_enforce_audit" tests/audit/test_m3_model_sig_enforcement.c
"$OUT/m3_enforce_audit" || rc=1

echo
echo "=== M3 — same gate, DEFAULT OFF (no-brick pass-through proof) ==="
$CC -O2 $INC -DVOS3_VVFS_REQUIRE_MODEL_SIG=0 \
    -o "$OUT/m3_default_audit" tests/audit/test_m3_model_sig_enforcement.c
"$OUT/m3_default_audit" || rc=1

echo
echo "=== B3-MARK-PUSH — atomic mark-and-push struct contract (taint_maps.h, G5) ==="
# Pure host compile + sizeof/offset probe of struct vos3_taint_mark_push_arg.
# Validates the Finding B3-1 (Option (a)) control structure is byte-for-byte
# (16-byte header + UNCHANGED 65568-byte entry == 65584). Does NOT load eBPF.
$CC -O2 $INC -o "$OUT/b3mp_audit" tests/audit/test_b3_mark_push_struct.c
"$OUT/b3mp_audit" || rc=1

echo
echo "=== B3-LOAD-SIM — eBPF load-simulation (Docker; advisory, Phase 19.4) ==="
# OPTIONAL Docker-based eBPF pre-flight. The host C twins above are the portable
# release-gate proxy; this step cross-compiles the REAL LSM object and statically
# validates it (bpftool gen skeleton + BTF/section checks). It runs ONLY if a
# Docker daemon AND the vos-ebpf-builder:b31 image are present, and is ADVISORY:
# a missing Docker SKIPs it WITHOUT failing the host audit. It is a STATIC
# pre-flight, NOT the live kernel verifier.
SIM="../infra/runners/verify_ebpf_load_simulation.sh"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 \
   && docker image inspect vos-ebpf-builder:b31 >/dev/null 2>&1; then
    if bash "$SIM"; then echo "B3-LOAD-SIM: PASS (static pre-flight)"; else echo "B3-LOAD-SIM: FAIL"; rc=1; fi
else
    echo "B3-LOAD-SIM: SKIP — Docker daemon or vos-ebpf-builder:b31 image unavailable."
    echo "             (build it on a Docker host via infra/runners/run_isolated_ebpf_build.sh)"
fi

echo
if [ "$rc" -eq 0 ]; then echo "ALL KERNEL AUDIT TESTS PASSED"; else echo "KERNEL AUDIT FAILURES"; fi
exit "$rc"
