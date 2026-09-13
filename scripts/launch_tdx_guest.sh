#!/bin/bash
# scripts/launch_tdx_guest.sh
# ===========================
#
# Sprint 14.2 (Gap 3) — boot the vOS kernel inside an Intel TDX trust
# domain on the silicon-CI self-hosted runner.
#
# Operator template — this file is intentionally small and explicit.
# Each deployment customizes:
#
#   QEMU_BIN          Path to a qemu-system-x86_64 built with
#                     --enable-tdx and --enable-virtfs.
#   OVMF_TDX          Path to the TDX-enabled UEFI firmware
#                     (e.g., /usr/share/OVMF/OVMF_CODE.fd from a TDX-
#                     patched edk2 build; Azure DCedsv6 images include
#                     it at /usr/share/qemu/OVMF/).
#   MEM, SMP, CPU     VM sizing — start with 4G/2/host.
#
# The kernel ELF must already be built at kernel/build/vos3.elf before
# this script runs (the workflow ensures that).
#
# What this produces
# ------------------
# A QEMU process running in TDX mode with:
#   - virtio-serial chardev exposed on /tmp/vos_vbus.sock (the back-
#     channel the FastAPI side talks to)
#   - serial console redirected to /tmp/vos_kernel_console.log
# The process is meant to be run via `nohup` in the workflow; the
# workflow's tear-down step kills it.

set -euo pipefail

# ---------------------------------------------------------------------------
# Operator-tunable settings — override via env when invoking
# ---------------------------------------------------------------------------
QEMU_BIN="${QEMU_BIN:-/usr/bin/qemu-system-x86_64}"
OVMF_TDX="${OVMF_TDX:-/usr/share/qemu/OVMF/OVMF_CODE_4M.fd}"
KERNEL_ELF="${KERNEL_ELF:-kernel/build/vos3.elf}"
VBUS_SOCK="${VBUS_SOCK:-/tmp/vos_vbus.sock}"
CONSOLE_LOG="${CONSOLE_LOG:-/tmp/vos_kernel_console.log}"
MEM="${MEM:-4G}"
SMP="${SMP:-2}"
CPU="${CPU:-host,+tdx}"

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
[[ -x "$QEMU_BIN" ]] || { echo "::error::QEMU not found at $QEMU_BIN"; exit 1; }
[[ -r "$OVMF_TDX" ]] || { echo "::error::TDX-OVMF not found at $OVMF_TDX"; exit 1; }
[[ -r "$KERNEL_ELF" ]] || { echo "::error::kernel ELF not built at $KERNEL_ELF"; exit 1; }
[[ -d /sys/firmware/tdx ]] || { echo "::error::host kernel does not expose /sys/firmware/tdx — not TDX-capable"; exit 1; }

# Confirm qemu was built with --enable-tdx
if ! "$QEMU_BIN" -accel help 2>/dev/null | grep -q tdx; then
    echo "::error::$QEMU_BIN was not built with TDX support (run `qemu-system-x86_64 -accel help` to confirm)"
    exit 1
fi

rm -f "$VBUS_SOCK" "$CONSOLE_LOG"

# ---------------------------------------------------------------------------
# Boot the TDX guest
# ---------------------------------------------------------------------------
echo "[launch_tdx_guest] booting vOS kernel under TDX..."
echo "  QEMU:   $QEMU_BIN"
echo "  OVMF:   $OVMF_TDX"
echo "  KERNEL: $KERNEL_ELF"
echo "  VBUS:   $VBUS_SOCK"
echo "  CPU:    $CPU"
echo "  MEM:    $MEM"
echo "  SMP:    $SMP"

exec "$QEMU_BIN" \
    -accel kvm,tdx=on \
    -object tdx-guest,id=tdx0 \
    -machine q35,kernel-irqchip=split,confidential-guest-support=tdx0,memory-encryption=tdx \
    -m "$MEM" \
    -smp "$SMP" \
    -cpu "$CPU" \
    -bios "$OVMF_TDX" \
    -kernel "$KERNEL_ELF" \
    -append "console=ttyS0 quiet" \
    -nographic \
    -nodefaults \
    -serial "file:$CONSOLE_LOG" \
    -chardev "socket,id=vbus,path=$VBUS_SOCK,server=on,wait=off" \
    -device virtio-serial \
    -device "virtconsole,chardev=vbus,name=vos.vbus" \
    -no-reboot
