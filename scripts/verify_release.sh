#!/usr/bin/env bash
# scripts/verify_release.sh
#
# Verifies the authenticity of vOS.v1 release artifacts using the
# in-tree Sigstore v3-shaped bundle + Rekor v2 transparency log.
#
# Usage:
#   bash scripts/verify_release.sh                # verify all default artifacts
#   bash scripts/verify_release.sh <artifact>     # verify a specific artifact
#
# Exit codes:
#   0  all verifications passed
#   1  one or more verifications failed
#   2  required tooling or files missing

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

C_GREEN=$(printf '\033[32m')
C_RED=$(printf '\033[31m')
C_YEL=$(printf '\033[33m')
C_RST=$(printf '\033[0m')

PUB_KEY="infra/security/keys/vos3_dev_signing.pub"

if [[ ! -f "$PUB_KEY" ]]; then
    echo "${C_RED}✖${C_RST} Missing public key: $PUB_KEY" >&2
    exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "${C_RED}✖${C_RST} python3 not found in PATH" >&2
    exit 2
fi

verify_one() {
    local artifact="$1"
    local bundle="$2"
    local label="$3"

    if [[ ! -f "$artifact" ]]; then
        echo "${C_YEL}⚠${C_RST}  $label — artifact missing: $artifact"
        return 1
    fi
    if [[ ! -f "$bundle" ]]; then
        echo "${C_YEL}⚠${C_RST}  $label — bundle missing: $bundle"
        return 1
    fi

    local sha
    sha=$(shasum -a 256 "$artifact" | awk '{print $1}')

    if python3 - "$artifact" "$bundle" "$PUB_KEY" <<'PY' >/dev/null 2>&1
import sys
sys.path.insert(0, '.')
from infra.security.sigstore_v3_bundle import Verifier
artifact, bundle, pub = sys.argv[1:4]
result = Verifier.from_dev_key(pub).verify_artifact(artifact, bundle)
assert result.get("verified") is True, f"verified={result.get('verified')}"
assert result.get("rekor_inclusion") == "passed", f"rekor={result.get('rekor_inclusion')}"
PY
    then
        echo "${C_GREEN}✔${C_RST}  $label"
        echo "      artifact:  $artifact"
        echo "      sha-256:   $sha"
        echo "      bundle:    $bundle"
        return 0
    else
        echo "${C_RED}✖${C_RST}  $label — verification FAILED"
        echo "      artifact:  $artifact"
        echo "      sha-256:   $sha"
        return 1
    fi
}

echo "════════════════════════════════════════════════════════════════"
echo "  vOS.v1 — Release Artifact Verification"
echo "  Tier: dev (CN=VOS3-DEV-NOT-FULCIO, ECDSA P-256)"
echo "════════════════════════════════════════════════════════════════"
echo

failed=0

if [[ $# -eq 0 ]]; then
    # Default: verify all known release artifacts
    verify_one \
        "kernel/build/vos3.elf" \
        "infra/security/release_artifacts/vos3_elf_v1_1_0_ga.bundle.json" \
        "kernel ELF (v1.1.0-ga)" \
        || failed=$((failed+1))
    echo
    verify_one \
        "dist/release_v1_0.zip" \
        "infra/security/release_artifacts/release_v1_0_zip.bundle.json" \
        "release bundle ZIP (v1.0.0)" \
        || failed=$((failed+1))
elif [[ $# -ge 2 ]]; then
    # Caller-supplied artifact + bundle (preferred form): use as given.
    verify_one "$1" "$2" "$(basename "$1")" || failed=$((failed+1))
else
    # Single-arg legacy form: derive bundle name from artifact basename
    art="$1"
    base=$(basename "$art")
    case "$base" in
        vos3.elf)
            bundle="infra/security/release_artifacts/vos3_elf_v1_1_0_ga.bundle.json" ;;
        release_v1_0.zip)
            bundle="infra/security/release_artifacts/release_v1_0_zip.bundle.json" ;;
        *)
            echo "${C_YEL}⚠${C_RST} Unknown artifact — pass bundle path as second arg" >&2
            exit 2 ;;
    esac
    verify_one "$art" "$bundle" "$base" || failed=$((failed+1))
fi

echo
echo "════════════════════════════════════════════════════════════════"
if [[ $failed -eq 0 ]]; then
    echo "${C_GREEN}  RESULT: all verifications PASSED${C_RST}"
    echo "════════════════════════════════════════════════════════════════"
    exit 0
else
    echo "${C_RED}  RESULT: ${failed} verification(s) FAILED${C_RST}"
    echo "════════════════════════════════════════════════════════════════"
    exit 1
fi
