#!/usr/bin/env bash
# =============================================================================
# VOS-Cyber v21.7-UX — JWT secret injector
# =============================================================================
# Generates a 32-byte (64-hex-char) JWT secret with `openssl rand -hex 32`,
# atomically patches the JWT_SECRET= line in .env.production, verifies the
# patch took, and exits non-zero on any anomaly.
#
# Idempotent: safe to re-run. Re-running will rotate the secret (the old
# secret is overwritten and not retained anywhere).
#
# Usage (default path /opt/vos-cyber/.env.production):
#     sudo bash infra/deploy/inject_jwt_secret.sh
#
# Override env file path:
#     sudo VOS_ENV_FILE=/custom/path/.env.production bash infra/deploy/inject_jwt_secret.sh
#
# Why a script and not a one-liner:
#   - Atomic write: tempfile + rename, so a killed process can't leave a
#     half-written .env.production.
#   - Exact-match assertion: refuses to proceed unless EXACTLY one
#     JWT_SECRET= line was patched. Catches the case where the operator
#     duplicated or deleted the line during a `nano` pass.
#   - Mode 600 enforced after write — no chance of group/other read.
#   - The secret is never written to stdout, never persisted in shell
#     history (variable scope is the function), never committed.
# =============================================================================

set -euo pipefail

ENV_FILE="${VOS_ENV_FILE:-/opt/vos-cyber/.env.production}"

if [[ ! -f "$ENV_FILE" ]]; then
    printf 'FAIL: %s does not exist\n' "$ENV_FILE" >&2
    printf 'Hint: copy .env.production.example to .env.production first.\n' >&2
    exit 1
fi

# Generate the secret. Local-only variable — never echoed.
SECRET="$(openssl rand -hex 32)"
if [[ ${#SECRET} -ne 64 ]]; then
    printf 'FAIL: openssl produced unexpected length %d (expected 64)\n' "${#SECRET}" >&2
    exit 1
fi

# Atomic write: produce a tempfile in the same directory (so `mv` is rename,
# not cross-device copy), set perms before content lands, swap atomically.
ENV_DIR="$(dirname "$ENV_FILE")"
TMP="$(mktemp "${ENV_DIR}/.env.tmp.XXXXXX")"
chmod 600 "$TMP"

cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

# Patch in-place if JWT_SECRET= line exists; otherwise append.
if grep -qE '^JWT_SECRET=' "$ENV_FILE"; then
    awk -v s="$SECRET" '
        BEGIN { n = 0 }
        /^JWT_SECRET=/ {
            print "JWT_SECRET=" s
            n++
            next
        }
        { print }
        END {
            if (n != 1) {
                printf "FAIL: expected exactly 1 JWT_SECRET line, found %d\n", n > "/dev/stderr"
                exit 1
            }
        }
    ' "$ENV_FILE" > "$TMP"
else
    cat "$ENV_FILE" > "$TMP"
    printf 'JWT_SECRET=%s\n' "$SECRET" >> "$TMP"
fi

# Wipe the secret from this shell's environment immediately after writing.
unset SECRET

# Atomic swap: rename is atomic on the same filesystem.
mv "$TMP" "$ENV_FILE"
chmod 600 "$ENV_FILE"
trap - EXIT  # tempfile no longer exists; clear cleanup hook

# Verification: exactly one well-formed line.
# `|| true` so set -e doesn't abort on grep's exit-1 (zero matches).
COUNT="$(grep -cE '^JWT_SECRET=[a-f0-9]{64}$' "$ENV_FILE" || true)"
if [[ "$COUNT" != "1" ]]; then
    printf 'FAIL: post-write verification — expected 1 valid JWT_SECRET line, found %s\n' "$COUNT" >&2
    exit 1
fi

# Confirm file permissions ended up locked down.
PERMS="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE" 2>/dev/null)"
if [[ "$PERMS" != "600" ]]; then
    printf 'WARN: %s ended up with mode %s (expected 600). Fixing.\n' "$ENV_FILE" "$PERMS" >&2
    chmod 600 "$ENV_FILE"
fi

printf 'OK: JWT_SECRET injected into %s (1 line, 64 hex chars, mode 600)\n' "$ENV_FILE"
