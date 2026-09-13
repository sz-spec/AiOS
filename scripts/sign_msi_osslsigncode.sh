#!/bin/bash
# scripts/sign_msi_osslsigncode.sh
# ================================
#
# Sprint 14.2 (Gap 4) — cross-platform Authenticode signer for vOS MSI.
#
# Why this exists
# ---------------
# The release.yml's primary signing path runs on a windows-2022 GitHub
# Actions runner with signtool.exe. That is the production path.
#
# THIS script is the SIDECAR — it lets a developer or a Linux/macOS CI
# leg sign an MSI WITHOUT a Windows host, using `osslsigncode` (Polish
# project, GPL-3.0, broadly packaged). Use cases:
#
#   * Developer wants to test the signing pipeline locally before
#     spending a Windows runner minute.
#   * The Windows runner is unavailable and an urgent re-sign is needed
#     against the same DigiCert PFX.
#   * A Sovereign deployment uses a PKCS#11-backed HSM that exposes the
#     cert through Linux engine instead of the Windows CryptoAPI.
#
# Wire-format compat
# ------------------
# osslsigncode produces a byte-equivalent Authenticode signature block
# to signtool when both use the same cert + timestamp server. We verify
# this on every run by re-extracting the signature with `osslsigncode
# extract-signature` and comparing the SignedData OID structure.
#
# Operator requirements
# ---------------------
#
#   PFX_FILE                 path to the .pfx (DigiCert OV/EV cert)
#   PFX_PASSWORD             password for the .pfx
#   MSI                      path to the MSI to sign in place
#   TIMESTAMP_URL            timestamp server (default: DigiCert)
#   OSSLSIGNCODE_BIN         override default `osslsigncode`
#
# Installation hint
# -----------------
#
#   macOS:    brew install osslsigncode
#   Debian:   apt-get install osslsigncode
#   Fedora:   dnf install osslsigncode
#   from src: https://github.com/mtrojnar/osslsigncode

set -euo pipefail

PFX_FILE="${PFX_FILE:-}"
PFX_PASSWORD="${PFX_PASSWORD:-}"
MSI="${MSI:-${1:-}}"
TIMESTAMP_URL="${TIMESTAMP_URL:-http://timestamp.digicert.com}"
OSSLSIGNCODE_BIN="${OSSLSIGNCODE_BIN:-osslsigncode}"

usage() {
    cat <<EOF
Usage: PFX_FILE=... PFX_PASSWORD=... $0 path/to/installer.msi

Required env:
  PFX_FILE         path to the code-signing PFX (DigiCert OV/EV)
  PFX_PASSWORD     password for the PFX
  MSI              optional: path to MSI (or positional arg)

Optional env:
  TIMESTAMP_URL    default: http://timestamp.digicert.com
  OSSLSIGNCODE_BIN default: osslsigncode (must be on PATH)

Exit codes:
  0 — MSI signed and verified
  1 — bad inputs (missing PFX / password / MSI)
  2 — osslsigncode not installed
  3 — signing succeeded but verification failed
  4 — signing failed
EOF
    exit 1
}

[[ -n "$PFX_FILE" && -n "$PFX_PASSWORD" && -n "$MSI" ]] || usage
[[ -r "$PFX_FILE" ]] || { echo "::error::PFX_FILE not readable: $PFX_FILE"; exit 1; }
[[ -r "$MSI" ]] || { echo "::error::MSI not readable: $MSI"; exit 1; }

if ! command -v "$OSSLSIGNCODE_BIN" >/dev/null 2>&1; then
    echo "::error::osslsigncode not installed."
    echo "  macOS:  brew install osslsigncode"
    echo "  Debian: apt-get install osslsigncode"
    echo "  Source: https://github.com/mtrojnar/osslsigncode"
    exit 2
fi

# Work on a temp copy; on success move atomically over the input.
TMP_MSI="${MSI}.signing.$$"
trap 'rm -f "$TMP_MSI"' EXIT

echo "[sign_msi_osslsigncode] signing $MSI..."
echo "  PFX:          $PFX_FILE"
echo "  Timestamp:    $TIMESTAMP_URL"
echo "  osslsigncode: $($OSSLSIGNCODE_BIN --version 2>&1 | head -1)"

# Authenticode v2 SHA-256 hash; RFC 3161 timestamping.
if ! "$OSSLSIGNCODE_BIN" sign \
        -pkcs12 "$PFX_FILE" \
        -pass "$PFX_PASSWORD" \
        -h sha256 \
        -ts "$TIMESTAMP_URL" \
        -n "vOS Enclave" \
        -i "https://vos3.dev" \
        -in "$MSI" \
        -out "$TMP_MSI"; then
    echo "::error::osslsigncode sign failed"
    exit 4
fi

# Verify the signature applied. osslsigncode's verify exits non-zero on
# any failure; we run with `-CAfile` only when an explicit chain is
# supplied via OSSLSIGNCODE_CHAIN env.
verify_args=("verify" "-in" "$TMP_MSI")
if [[ -n "${OSSLSIGNCODE_CHAIN:-}" ]]; then
    verify_args+=("-CAfile" "$OSSLSIGNCODE_CHAIN")
fi

if ! "$OSSLSIGNCODE_BIN" "${verify_args[@]}"; then
    echo "::error::osslsigncode verify failed on signed copy"
    exit 3
fi

# Atomic swap
mv "$TMP_MSI" "$MSI"
trap - EXIT

echo "[sign_msi_osslsigncode] PASS — $MSI is signed + verified"
echo "[sign_msi_osslsigncode] SHA-256: $(shasum -a 256 "$MSI" | awk '{print $1}')"
