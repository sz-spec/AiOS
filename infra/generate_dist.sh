#!/usr/bin/env bash
# ============================================================================
# vOS·Adaptive·SHA=aeb3736·Phase=P-deploy
#
# Multi-platform distribution artifact generator.
#
# Takes dist/vos3_installer.iso (built by infra/build_iso.sh) and
# emits three platform-specific virtual-disk formats:
#
#   * dist/vos3_linux_kvm.qcow2          — qemu/KVM/virt-manager (Linux hosts)
#   * dist/vos3_windows_hyperv.vhdx       — Microsoft Hyper-V (Windows hosts)
#   * dist/vos3_macos_portable.utm/       — UTM bundle (macOS hosts, Intel + ARM)
#
# Honest scope
# ------------
# * The qcow2 and vhdx are LIVE-CD images, not installed-system images.
#   qemu-img is being used to convert an ISO into a hypervisor-native
#   container, so the VM boots from the ISO contents but cannot
#   persist state. That's correct for vOS today — the kernel is the
#   only thing in the artifact; there's no user-space installer to
#   write to a disk. When user-space delivery lands in a later phase,
#   this script grows a "build installed-system disk" path too.
#
# * The .utm bundle is a minimum-viable config + the same qcow2 disk.
#   UTM may prompt "Import VM..." if the config schema drifts between
#   UTM versions; the bundled config targets UTM 4.x (the current
#   stable line as of 2026-05). The operator can re-create the VM
#   from scratch using docs/PLATFORM_GUIDE.md if the import fails.
#
# * .vhdx is produced as the "dynamic" subtype (qemu-img's default for
#   the vhdx format). Hyper-V accepts both Dynamic and Fixed, but
#   Fixed is much larger on disk. Dynamic is the right default.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

INPUT_ISO="${INPUT_ISO:-${REPO_ROOT}/dist/vos3_installer.iso}"
DIST_DIR="${DIST_DIR:-${REPO_ROOT}/dist}"

LINUX_QCOW2="${DIST_DIR}/vos3_linux_kvm.qcow2"
WINDOWS_VHDX="${DIST_DIR}/vos3_windows_hyperv.vhdx"
MACOS_BUNDLE="${DIST_DIR}/vos3_macos_portable.utm"

# ANSI helpers
if [[ -t 1 ]]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
    BOLD=$'\033[1m'; NC=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; BOLD=""; NC=""
fi
step() { echo "${BOLD}━━━ $* ━━━${NC}"; }
ok()   { echo "${GREEN}[OK]${NC}   $*"; }
warn() { echo "${YELLOW}[WARN]${NC} $*"; }
die()  { echo "${RED}[FAIL]${NC} $*" >&2; exit 1; }

# ----------------------------------------------------------------------------
# Preflight
# ----------------------------------------------------------------------------

preflight() {
    step "Preflight"

    if [[ ! -f "$INPUT_ISO" ]]; then
        die "Installer ISO not found at $INPUT_ISO
       Build it first:  (cd kernel && make iso)"
    fi
    ok "Input ISO: $INPUT_ISO ($(wc -c <"$INPUT_ISO" | tr -d ' ') bytes)"

    if ! command -v qemu-img >/dev/null 2>&1; then
        die "qemu-img not in PATH.
       Linux:  sudo apt-get install qemu-utils
       macOS:  brew install qemu
       Windows: install QEMU from https://www.qemu.org/download/"
    fi
    ok "qemu-img: $(qemu-img --version 2>/dev/null | head -1 | awk '{print $NF}')"

    mkdir -p "$DIST_DIR"
    ok "Output dir: $DIST_DIR"
}

# ----------------------------------------------------------------------------
# Hash + report helper
# ----------------------------------------------------------------------------

report_artifact() {
    local path="$1" label="$2"
    if [[ ! -e "$path" ]]; then
        warn "$label was not produced — skipping report"
        return
    fi
    local size_bytes size_mb sha256
    size_bytes=$(du -sk "$path" 2>/dev/null | awk '{print $1*1024}')
    size_mb=$(( size_bytes / 1024 / 1024 ))
    if [[ -d "$path" ]]; then
        # For bundle directories, sha-sum the disk image inside.
        local disk
        disk="$(find "$path" -name '*.qcow2' -type f 2>/dev/null | head -1)"
        if [[ -n "$disk" ]]; then
            sha256="$(shasum -a 256 "$disk" 2>/dev/null | awk '{print $1}' \
                    || sha256sum "$disk" 2>/dev/null | awk '{print $1}' \
                    || echo n/a)"
        else
            sha256="n/a (bundle)"
        fi
    else
        sha256="$(shasum -a 256 "$path" 2>/dev/null | awk '{print $1}' \
                || sha256sum "$path" 2>/dev/null | awk '{print $1}' \
                || echo n/a)"
    fi
    ok "$label"
    echo "    Path:   $path"
    echo "    Size:   ${size_mb} MB"
    echo "    SHA256: $sha256"
}

# ----------------------------------------------------------------------------
# Linux — qcow2 for KVM / virt-manager / libvirt
# ----------------------------------------------------------------------------

build_linux_qcow2() {
    step "Linux: vos3_linux_kvm.qcow2"

    rm -f "$LINUX_QCOW2"
    # `qemu-img convert -O qcow2 in.iso out.qcow2` produces a qcow2
    # whose backing data is the raw ISO. The resulting image boots in
    # QEMU/KVM as if it were a hard disk preloaded with the ISO.
    # Compression is on by default for qcow2 — saves ~30% on disk.
    qemu-img convert -O qcow2 -c "$INPUT_ISO" "$LINUX_QCOW2"
    ok "qcow2 produced"
}

# ----------------------------------------------------------------------------
# Windows — VHDX for Hyper-V
# ----------------------------------------------------------------------------

build_windows_vhdx() {
    step "Windows: vos3_windows_hyperv.vhdx"

    rm -f "$WINDOWS_VHDX"
    # `-O vhdx` defaults to the "dynamic" subtype (sparse on disk,
    # grows on write). Hyper-V accepts both Dynamic and Fixed.
    qemu-img convert -O vhdx -o subformat=dynamic "$INPUT_ISO" "$WINDOWS_VHDX"
    ok "vhdx produced (dynamic subtype)"
}

# ----------------------------------------------------------------------------
# macOS — UTM bundle (Apple Silicon + Intel)
# ----------------------------------------------------------------------------

build_macos_utm() {
    step "macOS: vos3_macos_portable.utm"

    rm -rf "$MACOS_BUNDLE"
    mkdir -p "$MACOS_BUNDLE/Data"

    # Place the disk image inside the bundle's Data/ subdir so UTM
    # finds it via the relative path in config.plist.
    local bundle_disk="${MACOS_BUNDLE}/Data/disk-0.qcow2"
    qemu-img convert -O qcow2 -c "$INPUT_ISO" "$bundle_disk"

    # UUID for the VM — UTM stores one per VM. Generated fresh here
    # so two installs on the same host don't collide.
    local vm_uuid
    vm_uuid="$(uuidgen 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')"

    # config.plist — minimum-viable UTM 4.x configuration. Targets
    # x86_64 + q35 because the vOS kernel IS x86_64. On Apple Silicon
    # this runs under TCG emulation (slow but universal); on Intel
    # Mac it can use Hypervisor.framework for native speed.
    cat > "${MACOS_BUNDLE}/config.plist" <<UTM_CFG
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Backend</key>
    <string>QEMU</string>
    <key>ConfigurationVersion</key>
    <integer>4</integer>
    <key>Information</key>
    <dict>
        <key>Name</key>
        <string>vOS v1</string>
        <key>UUID</key>
        <string>${vm_uuid}</string>
        <key>Notes</key>
        <string>vOS·Adaptive·SHA=aeb3736 — built by infra/generate_dist.sh</string>
    </dict>
    <key>System</key>
    <dict>
        <key>Architecture</key>
        <string>x86_64</string>
        <key>Target</key>
        <string>q35</string>
        <key>MemorySize</key>
        <integer>1024</integer>
        <key>CPUCount</key>
        <integer>2</integer>
        <key>BootDevice</key>
        <string>cd</string>
    </dict>
    <key>Display</key>
    <array>
        <dict>
            <key>ConsoleOnly</key>
            <true/>
        </dict>
    </array>
    <key>Drives</key>
    <array>
        <dict>
            <key>ImageName</key>
            <string>disk-0.qcow2</string>
            <key>Interface</key>
            <string>VirtIO</string>
            <key>RemovableDrive</key>
            <false/>
        </dict>
    </array>
    <key>Serial</key>
    <array>
        <dict>
            <key>Mode</key>
            <string>BuiltIn</string>
            <key>Terminal</key>
            <dict>
                <key>FontSize</key>
                <integer>12</integer>
            </dict>
        </dict>
    </array>
</dict>
</plist>
UTM_CFG

    # macOS-specific: tag the bundle as a "package" so Finder treats
    # the .utm directory as a single file. The PkgInfo + Info.plist
    # combo is the standard way; UTM uses LSItemContentTypes filtering
    # on its own to recognise bundles.
    cat > "${MACOS_BUNDLE}/Info.plist" <<INFO_CFG
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.utmapp.vos.v1</string>
    <key>CFBundleName</key>
    <string>vOS v1</string>
    <key>CFBundlePackageType</key>
    <string>VMUT</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
</dict>
</plist>
INFO_CFG

    ok "UTM bundle assembled"
}

# ----------------------------------------------------------------------------
# Wire it together
# ----------------------------------------------------------------------------

main() {
    preflight
    build_linux_qcow2
    build_windows_vhdx
    build_macos_utm

    echo
    step "Artifacts"
    report_artifact "$LINUX_QCOW2"   "Linux qcow2 (KVM / virt-manager)"
    report_artifact "$WINDOWS_VHDX"  "Windows vhdx (Hyper-V)"
    report_artifact "$MACOS_BUNDLE"  "macOS UTM bundle (Apple Silicon + Intel)"

    echo
    step "Done"
    echo "Distribution artifacts ready in $DIST_DIR/"
    echo
    echo "Next steps:"
    echo "  Bundle them:  cd $(dirname "$DIST_DIR") && make release   # → dist/release_v1_0.zip"
    echo "  Operator docs: docs/PLATFORM_GUIDE.md"
}

main "$@"
