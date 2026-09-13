#!/usr/bin/env bash
# Build the vendored bootloader without network access or legacy repositories.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${LIMINE_BUILD_DIR:-$repo_root/kernel/build/limine}/bin"
# Upstream's nested makefiles do not support spaces in their source paths.
# Build a private source copy, then export only the finished boot artifacts.
scratch="$(mktemp -d /tmp/vos5-limine.XXXXXX)"
trap 'rm -rf "$scratch"' EXIT
cp -R "$repo_root/kernel/boot/limine" "$scratch/source"
mkdir "$scratch/build"
cd "$scratch/build"
"$scratch/source/configure" \
    --enable-bios --enable-bios-cd --enable-uefi-x86-64 \
    --enable-uefi-ia32 --enable-uefi-cd \
    "TOOLCHAIN_FOR_TARGET=${TOOLCHAIN_FOR_TARGET:-x86_64-elf}"
make -j "${BUILD_JOBS:-4}"
mkdir -p "$output_dir"
for artifact in limine limine-bios.sys limine-bios-cd.bin limine-uefi-cd.bin BOOTX64.EFI BOOTIA32.EFI; do
    cp "bin/$artifact" "$output_dir/$artifact"
done
