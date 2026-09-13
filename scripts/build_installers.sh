#!/usr/bin/env bash
# scripts/build_installers.sh — local production installer build
#
# Produces a Tauri 2 release bundle for the current host:
#   * macOS arm64 → vOS_v1_Silicon.dmg
#   * macOS x86_64 → vOS_v1_Intel.dmg
#   * Linux x86_64 → vOS_v1_x64.AppImage
#   * Windows x86_64 (when run from a Windows host) → vOS_v1_Setup.msi
#
# Fails LOUD on every missing prerequisite. Does NOT fabricate a
# "signed" artifact — if signing credentials are absent, the bundle
# is renamed with the suffix "-UNSIGNED" and a warning is printed.
#
# Cross-OS signed builds are the responsibility of CI — see
# .github/workflows/release.yml. This script is the local-dev path
# for sanity-testing the bundle layout on the engineer's machine.
#
# Usage:
#   bash scripts/build_installers.sh                  # current host
#   VOS3_BUILD_SKIP_KERNEL=1 bash scripts/build_installers.sh
#   VOS3_BUILD_SKIP_FRONTEND=1 bash scripts/build_installers.sh
#
# Exit codes:
#   0  success (signed or unsigned, banner says which)
#   1  missing prerequisite
#   2  build failure
#   3  bundle missing after build

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Prereq checks — fail-fast with actionable error messages
# ---------------------------------------------------------------------------

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
red()  { printf "\033[1;31m%s\033[0m\n" "$*" >&2; }
green(){ printf "\033[1;32m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[1;33m%s\033[0m\n" "$*" >&2; }

require() {
  local name="$1" check="$2" install_hint="$3"
  if ! eval "$check" >/dev/null 2>&1; then
    red "MISSING: $name"
    red "  install:   $install_hint"
    return 1
  fi
  green "OK: $name"
  return 0
}

bold "=== Prerequisite check ==="
MISSING=0
require "node"   "command -v node"  "https://nodejs.org or 'brew install node'" || MISSING=$((MISSING+1))
require "npm"    "command -v npm"   "ships with node" || MISSING=$((MISSING+1))
require "cargo"  "command -v cargo" "https://rustup.rs (curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh)" || MISSING=$((MISSING+1))
require "tauri-cli" "cargo tauri --version" "cargo install tauri-cli --version '^2'" || MISSING=$((MISSING+1))

HOST_OS="$(uname -s)"
HOST_ARCH="$(uname -m)"
bold "host: $HOST_OS / $HOST_ARCH"

# Windows-host detection (MSYS / Cygwin / Git Bash uname -s reports).
if [[ "$HOST_OS" == MINGW* || "$HOST_OS" == CYGWIN* || "$HOST_OS" == MSYS* ]]; then
  bold "=== Windows host detected — checking WiX toolchain ==="
  WIX_OK=0
  # Tauri 2 supports WiX 3 via `candle.exe` + `light.exe`. WiX 4
  # ships a single `wix.exe`. Either is acceptable; we report which.
  if command -v candle.exe >/dev/null 2>&1 && command -v light.exe >/dev/null 2>&1; then
    green "OK: WiX 3 (candle.exe + light.exe)"
    WIX_OK=1
  elif command -v wix.exe >/dev/null 2>&1; then
    green "OK: WiX 4+ (wix.exe)"
    WIX_OK=1
  fi
  if [[ $WIX_OK -eq 0 ]]; then
    red "MISSING: WiX Toolset — required to produce vOS_v1_Setup.msi"
    red "  install (WiX 3): https://wixtoolset.org/releases/v3-14-1/"
    red "                   or 'choco install wixtoolset'"
    red "  install (WiX 4): 'dotnet tool install --global wix --version 4.*'"
    MISSING=$((MISSING+1))
  fi
  # Verify a code-signing cert is in the user store (signtool will need it).
  if command -v signtool.exe >/dev/null 2>&1; then
    green "OK: signtool.exe present (Authenticode signing available)"
  else
    yellow "WARN: signtool.exe not on PATH — MSI will be UNSIGNED"
    yellow "  install via Windows SDK or 'choco install windows-sdk-10-version-2104-windbg'"
  fi
fi

if [[ "$HOST_OS" == "Darwin" ]]; then
  if security find-identity -v -p codesigning 2>/dev/null | grep -qi "Developer ID Application"; then
    green "OK: Apple Developer ID Application identity found"
    SIGN_AVAILABLE=1
  else
    yellow "WARN: no 'Developer ID Application' codesigning identity — bundle will be UNSIGNED"
    SIGN_AVAILABLE=0
  fi
fi

if [[ $MISSING -gt 0 ]]; then
  red "=== $MISSING prerequisite(s) missing — aborting ==="
  exit 1
fi

# ---------------------------------------------------------------------------
# Frontend build (Next.js → static export consumed by Tauri)
# ---------------------------------------------------------------------------

if [[ -z "${VOS3_BUILD_SKIP_FRONTEND:-}" ]]; then
  bold "=== Frontend build (npm run build) ==="
  (cd "$REPO_ROOT/frontend" && npm ci && npm run build)
else
  yellow "Skipping frontend build (VOS3_BUILD_SKIP_FRONTEND=1)"
fi

# ---------------------------------------------------------------------------
# Kernel artifact check (we DO NOT rebuild from this script — kernel
# requires x86_64-elf-gcc cross-compiler which is a separate setup).
# ---------------------------------------------------------------------------

if [[ -z "${VOS3_BUILD_SKIP_KERNEL:-}" ]]; then
  if [[ ! -f "$REPO_ROOT/kernel/build/vos3.elf" ]]; then
    red "MISSING: kernel/build/vos3.elf — run 'cd kernel && make' first"
    red "  (requires x86_64-elf-gcc — see CLAUDE.md)"
    exit 1
  fi
  green "OK: kernel/build/vos3.elf present"
fi

# ---------------------------------------------------------------------------
# Tauri bundle
# ---------------------------------------------------------------------------

bold "=== Tauri 2 release bundle ==="
cd "$REPO_ROOT/desktop"

if [[ "$HOST_OS" == "Darwin" && -n "${SIGN_AVAILABLE:-}" && "$SIGN_AVAILABLE" == "1" ]]; then
  : "${APPLE_SIGNING_IDENTITY:?set APPLE_SIGNING_IDENTITY='Developer ID Application: Name (TEAMID)'}"
  : "${APPLE_ID:?set APPLE_ID=your-apple-id@example.com}"
  : "${APPLE_PASSWORD:?set APPLE_PASSWORD=app-specific-password}"
  : "${APPLE_TEAM_ID:?set APPLE_TEAM_ID=10-char-id}"
  export APPLE_SIGNING_IDENTITY APPLE_ID APPLE_PASSWORD APPLE_TEAM_ID
fi

cargo tauri build --bundles dmg,msi,appimage 2>&1 | tail -100 || {
  red "Tauri build failed"
  exit 2
}

# ---------------------------------------------------------------------------
# Locate + rename the produced bundle
# ---------------------------------------------------------------------------

BUNDLE_DIR="$REPO_ROOT/desktop/src-tauri/target/release/bundle"
DIST_DIR="$REPO_ROOT/dist"
mkdir -p "$DIST_DIR"

bold "=== Stage installer(s) into $DIST_DIR ==="

stage() {
  local glob="$1" target="$2"
  shopt -s nullglob
  local matches=( $glob )
  shopt -u nullglob
  if [[ ${#matches[@]} -eq 0 ]]; then
    return 1
  fi
  cp "${matches[0]}" "$DIST_DIR/$target"
  green "  -> $DIST_DIR/$target"
}

case "$HOST_OS/$HOST_ARCH" in
  Darwin/arm64)
    if [[ "${SIGN_AVAILABLE:-0}" == "1" ]]; then
      stage "$BUNDLE_DIR/dmg/*.dmg" "vOS_v1_Silicon.dmg" \
        || { red "DMG not found under $BUNDLE_DIR/dmg"; exit 3; }
    else
      stage "$BUNDLE_DIR/dmg/*.dmg" "vOS_v1_Silicon-UNSIGNED.dmg" \
        || { red "DMG not found under $BUNDLE_DIR/dmg"; exit 3; }
      yellow "Artifact is UNSIGNED — do NOT distribute. Use CI for signed builds."
    fi
    ;;
  Darwin/x86_64)
    stage "$BUNDLE_DIR/dmg/*.dmg" "vOS_v1_Intel${SIGN_AVAILABLE:+}${SIGN_AVAILABLE:-"-UNSIGNED"}.dmg" \
      || { red "DMG not found"; exit 3; }
    ;;
  Linux/x86_64)
    stage "$BUNDLE_DIR/appimage/*.AppImage" "vOS_v1_x64.AppImage" \
      || { red "AppImage not found"; exit 3; }
    ;;
  *)
    if [[ "$HOST_OS" == "MINGW"* || "$HOST_OS" == "CYGWIN"* ]]; then
      # Sprint 14.2 (Gap 4) — MSI is produced natively here. Sign with
      # signtool.exe if available (operator local). The release-CI path
      # in .github/workflows/release.yml does the final Authenticode
      # sign against the secret-pinned DigiCert PFX; this local stage
      # produces an UNSIGNED MSI when no cert is on the local store.
      stage "$BUNDLE_DIR/msi/*.msi" "vOS_v1_Setup.msi" \
        || { red "MSI not found"; exit 3; }
      if [[ "${SIGN_AVAILABLE:-0}" != "1" ]]; then
        yellow "MSI is UNSIGNED — do NOT distribute. Use .github/workflows/release.yml for signed builds."
      fi
    else
      # Sprint 14.2 (Gap 4) — MSI cross-build from non-Windows hosts
      # was deferred to v1.2; the official path is the release workflow
      # on a windows-2022 runner. Local Mac/Linux developers who need
      # to test the MSI bundle can:
      #   (a) run this script through `cargo tauri build` against a
      #       windows-msvc target with `cargo-xwin` (produces the binary
      #       only, NOT the MSI installer wrapper); or
      #   (b) post-sign an MSI produced by the CI using
      #       `scripts/sign_msi_osslsigncode.sh`.
      red "Unsupported host for MSI staging: $HOST_OS/$HOST_ARCH."
      red "Use the .github/workflows/release.yml Windows job or"
      red "  scripts/sign_msi_osslsigncode.sh against a CI-produced MSI."
      exit 3
    fi
    ;;
esac

bold "=== sha256 ==="
( cd "$DIST_DIR" && shasum -a 256 ./* )

green "Done. Artifact(s) staged in $DIST_DIR/"
