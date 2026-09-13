#!/usr/bin/env bash
# Package the native kernel with a BIOS + IA32/x64 UEFI Limine bootloader.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
kernel_elf="${KERNEL_ELF:-$repo_root/kernel/build/vos3.elf}"
output_iso="${OUTPUT_ISO:-$repo_root/dist/vos5.iso}"
limine_bin="${LIMINE_BUILD_DIR:-$repo_root/kernel/build/limine}/bin"
[[ -f "$kernel_elf" ]] || { echo "Kernel missing: $kernel_elf" >&2; exit 1; }
command -v xorriso >/dev/null
for artifact in limine limine-bios.sys limine-bios-cd.bin limine-uefi-cd.bin BOOTX64.EFI BOOTIA32.EFI; do
    [[ -f "$limine_bin/$artifact" ]] || {
        echo "Missing $artifact; run bash infra/build_limine.sh first." >&2
        exit 1
    }
done
# Never erase an arbitrary user-supplied staging path.
staging="$(mktemp -d /tmp/vos5-iso.XXXXXX)"
trap 'rm -rf "$staging"' EXIT
mkdir -p "$staging/root/boot/limine" "$staging/root/EFI/BOOT"
cp "$kernel_elf" "$staging/root/boot/vos3.elf"
cp "$limine_bin/limine-bios.sys" "$limine_bin/limine-bios-cd.bin" \
    "$limine_bin/limine-uefi-cd.bin" "$staging/root/boot/limine/"
cp "$limine_bin/BOOTX64.EFI" "$limine_bin/BOOTIA32.EFI" "$staging/root/EFI/BOOT/"
# Use the kernel's implemented 32-to-64-bit Multiboot2 entry on both firmwares.
# The separate Limine-protocol entry is not yet release-qualified.
cat > "$staging/root/boot/limine/limine.conf" <<'CONFIG'
timeout: 0
serial: yes
/VOS 5
    protocol: multiboot2
    path: boot():/boot/vos3.elf
CONFIG
xorriso -as mkisofs -R -r -J \
    -b boot/limine/limine-bios-cd.bin -no-emul-boot -boot-load-size 4 -boot-info-table \
    -hfsplus -apm-block-size 2048 \
    --efi-boot boot/limine/limine-uefi-cd.bin -efi-boot-part --efi-boot-image \
    --protective-msdos-label "$staging/root" -o "$staging/vos5.iso"
"$limine_bin/limine" bios-install "$staging/vos5.iso"
xorriso -indev "$staging/vos5.iso" -report_el_torito plain > "$staging/boot-catalog.txt" 2>&1
# A successful ISO writer is insufficient: both boot catalog entries must exist.
grep -Eq 'El Torito boot img.*BIOS' "$staging/boot-catalog.txt"
grep -Eq 'El Torito boot img.*UEFI' "$staging/boot-catalog.txt"
mkdir -p "$(dirname "$output_iso")"
cp "$staging/vos5.iso" "$output_iso"
cp "$staging/boot-catalog.txt" "$output_iso.boot-catalog.txt"
echo "Built BIOS + UEFI image: $output_iso"
shasum -a 256 "$output_iso"
