#!/usr/bin/env bash
#
# vOS — in-container eBPF compile step (runs INSIDE linux_ebpf_builder image).
# Generates vmlinux.h from the kernel BTF, then compiles the real LSM program
# with the production flags. Exits non-zero on ANY warning or error — no
# half-typed objects, no faked success.
set -euo pipefail

TAINT_SRC="${TAINT_SRC:-kernel/src/sec/taint_gate.c}"
OUT_OBJ="${OUT_OBJ:-/out/taint_gate.o}"
KERNEL_INCLUDE="${KERNEL_INCLUDE:-kernel/include}"

mkdir -p "$(dirname "$OUT_OBJ")"

echo "=== toolchain ==="
cat /opt/toolchain_versions.txt 2>/dev/null || clang --version

# --- Phase 19.6 Stage B: BTF-generated vmlinux.h for CO-RE -------------------
# taint_gate.c now does a CO-RE walk of current->files->fdt->fd[] for per-fd
# resolution, which needs the full kernel type layout from vmlinux.h. Generate
# it from the running kernel's BTF.
VMLINUX_DIR="${VMLINUX_DIR:-/tmp/vos3_core}"
mkdir -p "$VMLINUX_DIR"
BPFTOOL="$(find /usr/lib/linux-tools* -name bpftool -type f 2>/dev/null | head -1)"
if [ -z "$BPFTOOL" ] || [ ! -r /sys/kernel/btf/vmlinux ]; then
    echo "FATAL: need bpftool + /sys/kernel/btf/vmlinux to generate vmlinux.h" >&2
    exit 3
fi
"$BPFTOOL" btf dump file /sys/kernel/btf/vmlinux format c > "$VMLINUX_DIR/vmlinux.h"
echo "  vmlinux.h: $(wc -l < "$VMLINUX_DIR/vmlinux.h") lines"
# Empty <stdint.h> shim: vmlinux.h already typedefs uint8_t..uint64_t (as kernel
# u8/u16/u32/u64 = ...long long), which CLASH with glibc's <stdint.h>
# (unsigned long). taint_maps.h includes <stdint.h>; shadowing it with this
# empty header (first on the -I path) makes that include a no-op and relies on
# vmlinux.h's typedefs. (taint_maps.h itself is untouched — G5.)
SHIM_DIR="${SHIM_DIR:-/tmp/vos3_shim}"
mkdir -p "$SHIM_DIR"
printf '#ifndef _VOS3_SHIM_STDINT_H\n#define _VOS3_SHIM_STDINT_H\n/* empty: stdint types provided by vmlinux.h (BTF) */\n#endif\n' > "$SHIM_DIR/stdint.h"

# --- compile with the REAL production flags --------------------------------
# -target bpf + __VOS3_TAINT_GATE_REAL_BPF_BUILD=1 selects the live LSM body.
# Include order matters: SHIM_DIR first (shadow <stdint.h>), then VMLINUX_DIR
# (vmlinux.h), then the multiarch UAPI dir (asm/* for bpf headers), then ours.
ARCH_TRIPLET="$(uname -m)-linux-gnu"
case "$(uname -m)" in
    x86_64)  BPF_ARCH=x86 ;;
    aarch64) BPF_ARCH=arm64 ;;
    *)       BPF_ARCH="$(uname -m)" ;;
esac
echo "=== compiling $TAINT_SRC -> $OUT_OBJ (arch=$BPF_ARCH, triplet=$ARCH_TRIPLET) ==="
WARN_LOG="$(mktemp)"
set +e
# -Wno-unused-parameter: LSM hook prototypes mandate the full param list and the
# BPF_PROG macro injects `ctx`; not all are read. Unused params on a fixed hook
# ABI are not defects (the kernel disables this warning globally). Every OTHER
# -Wall/-Wextra warning stays fatal.
clang -O2 -g -target bpf \
      -D__VOS3_TAINT_GATE_REAL_BPF_BUILD=1 \
      "-D__TARGET_ARCH_${BPF_ARCH}" \
      -Wall -Wextra -Werror -Wno-unused-parameter \
      -I"$SHIM_DIR" \
      -I"$VMLINUX_DIR" \
      -I"/usr/include/${ARCH_TRIPLET}" \
      -I"$KERNEL_INCLUDE" \
      -c "$TAINT_SRC" \
      -o "$OUT_OBJ" 2> "$WARN_LOG"
rc=$?
set -e
cat "$WARN_LOG"

if [ "$rc" -ne 0 ]; then
    echo "BUILD FAILED (clang rc=$rc)" >&2
    exit "$rc"
fi
# Belt-and-braces: -Werror should have caught warnings, but assert anyway.
if grep -qiE "warning:" "$WARN_LOG"; then
    echo "BUILD FAILED: compiler warnings present (zero-warning gate)" >&2
    exit 4
fi

echo "=== object info ==="
command -v file >/dev/null 2>&1 && file "$OUT_OBJ"
llvm-objdump -h "$OUT_OBJ" | grep -E "SEC|lsm|\.maps" || true
( sha256sum "$OUT_OBJ" 2>/dev/null || true )
echo "EBPF BUILD OK: $OUT_OBJ (exit 0, zero warnings)"
