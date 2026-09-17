#!/usr/bin/env bash
# Build the vendored bootloader without network access or legacy repositories.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="${LIMINE_BUILD_DIR:-$repo_root/kernel/build/limine}/bin"
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1700000000}"
if [[ "${LIMINE_FORCE_REBUILD:-0}" != 1 ]] && \
   python3 "$repo_root/scripts/limine_build_state.py" "$output_dir"; then
    echo "Limine source, tools and artifacts unchanged."
    exit 0
fi
# Upstream's nested makefiles do not support spaces in their source paths.
# Build a private source copy, then export only the finished boot artifacts.
scratch="$(mktemp -d /tmp/vos5-limine.XXXXXX)"
trap 'rm -rf "$scratch"' EXIT
cp -R "$repo_root/kernel/boot/limine" "$scratch/source"
# Upstream sources its release timestamps during configure, overriding the
# environment. Normalize the private copy, never the vendored source tree.
python3 - "$scratch/source/timestamps" "$SOURCE_DATE_EPOCH" <<'PY'
import datetime, pathlib, re, sys
path = pathlib.Path(sys.argv[1])
epoch = int(sys.argv[2])
stamp = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
text = path.read_text()
text = re.sub(r'^SOURCE_DATE_EPOCH=.*$', f'SOURCE_DATE_EPOCH="{epoch}"', text, flags=re.M)
text = re.sub(r'^SOURCE_DATE_EPOCH_TOUCH=.*$',
              f'SOURCE_DATE_EPOCH_TOUCH="{stamp:%Y%m%d%H%M}.{stamp:%S}"', text, flags=re.M)
path.write_text(text)
PY
mkdir "$scratch/build"
cd "$scratch/build"
# Preserve GNU build IDs, but exclude private build-directory debug data from
# their inputs. BIOS embeds the IDs of intermediate ELF files into the shipped
# image. Those ELF debug sections are not part of the published boot artifacts.
repro_flags=("CFLAGS=${CFLAGS:--g -O2 -pipe} -ffile-prefix-map=$scratch=/vos-limine"
             "LDFLAGS_FOR_TARGET=${LDFLAGS_FOR_TARGET:-} --strip-debug")
if [[ ${CFLAGS_FOR_TARGET+x} ]]; then
    repro_flags+=("CFLAGS_FOR_TARGET=$CFLAGS_FOR_TARGET -ffile-prefix-map=$scratch=/vos-limine")
fi
"$scratch/source/configure" \
    --enable-bios --enable-bios-cd --enable-uefi-x86-64 \
    --enable-uefi-ia32 --enable-uefi-cd \
    "TOOLCHAIN_FOR_TARGET=${TOOLCHAIN_FOR_TARGET:-x86_64-elf}-" \
    "${repro_flags[@]}"
make -j "${BUILD_JOBS:-4}"
mkdir -p "$output_dir"
for artifact in limine limine-bios.sys limine-bios-cd.bin limine-uefi-cd.bin BOOTX64.EFI BOOTIA32.EFI; do
    cp "bin/$artifact" "$output_dir/$artifact"
done
python3 "$repo_root/scripts/limine_build_state.py" "$output_dir" --record
