#!/usr/bin/env bash
# =============================================================================
# VOS-Cyber v21.7-UX — .env.production credentials integrity audit
# =============================================================================
#
# Run on the production server, inside the repo:
#
#     sudo bash infra/deploy/audit_env.sh
#
# Reports presence / format / Docker propagation status for the four
# credential pairs the system needs (Clerk public+secret, Convex URL+
# deploy key) WITHOUT printing any secret values to stdout. Every check
# emits PASS/FAIL/WARN with a short reason; the actual key contents are
# never echoed.
#
# What the script does NOT do:
#   - Validate that the keys are accepted by Clerk / Convex (would
#     require an actual API call with live secrets — the operator runs
#     that separately if they want runtime confirmation).
#   - Modify the env file in any way. Read-only audit.
# =============================================================================

set -uo pipefail

ENV_FILE="${VOS_ENV_FILE:-/opt/vos-cyber/.env.production}"
COMPOSE_FILE="${VOS_COMPOSE_FILE:-/opt/vos-cyber/docker-compose.yml}"

PASS_MARK="\033[1;32m✓\033[0m"
FAIL_MARK="\033[1;31m✗\033[0m"
WARN_MARK="\033[1;33m!\033[0m"

# Tally for the final exit code.
fail_count=0
warn_count=0

# -----------------------------------------------------------------------------
# helpers — never print the value, only metadata about it
# -----------------------------------------------------------------------------

# Pull a single var's value out of the env file. Returns empty on miss.
read_var() {
    local key="$1"
    awk -F= -v k="$key" '
        $0 ~ "^"k"=" {
            sub("^"k"=", "", $0)
            print
            exit
        }
    ' "$ENV_FILE"
}

# Print a status line. Args: mark, label, detail.
report() {
    local mark="$1" label="$2" detail="${3:-}"
    printf "  %b  %-50s %s\n" "$mark" "$label" "$detail"
}

check_present() {
    local key="$1"
    local val
    val="$(read_var "$key")"
    if [[ -z "$val" ]]; then
        report "$FAIL_MARK" "$key" "MISSING (no line in env file)"
        fail_count=$((fail_count + 1))
        return 1
    fi
    if [[ "$val" == *REPLACE_ME* ]]; then
        report "$FAIL_MARK" "$key" "STILL HAS REPLACE_ME placeholder"
        fail_count=$((fail_count + 1))
        return 1
    fi
    report "$PASS_MARK" "$key" "PRESENT (${#val} chars)"
    return 0
}

check_prefix() {
    local key="$1" expected_prefixes="$2"
    local val
    val="$(read_var "$key")"
    if [[ -z "$val" ]]; then return 1; fi
    local matched=0
    local IFS='|'
    for p in $expected_prefixes; do
        if [[ "$val" == "$p"* ]]; then
            matched=1
            break
        fi
    done
    if [[ $matched -eq 1 ]]; then
        report "$PASS_MARK" "$key prefix" "matches one of [$expected_prefixes]"
        return 0
    fi
    report "$FAIL_MARK" "$key prefix" "does not match expected [$expected_prefixes]"
    fail_count=$((fail_count + 1))
    return 1
}

check_https_url() {
    local key="$1"
    local val
    val="$(read_var "$key")"
    if [[ -z "$val" ]]; then return 1; fi
    if [[ "$val" =~ ^https://[a-zA-Z0-9.-]+(/.*)?$ ]]; then
        report "$PASS_MARK" "$key format" "valid https URL"
        return 0
    fi
    report "$FAIL_MARK" "$key format" "not a valid https URL"
    fail_count=$((fail_count + 1))
    return 1
}

# -----------------------------------------------------------------------------
# 1. Env file existence + readability
# -----------------------------------------------------------------------------

echo
echo "=== 1. ENV FILE AUDIT ==="
echo

if [[ ! -f "$ENV_FILE" ]]; then
    report "$FAIL_MARK" "env file" "MISSING: $ENV_FILE"
    fail_count=$((fail_count + 1))
    echo
    echo "Cannot continue without the env file. Run:"
    echo "  cp /opt/vos-cyber/.env.production.example /opt/vos-cyber/.env.production"
    echo "  sudo nano /opt/vos-cyber/.env.production"
    exit 1
fi
report "$PASS_MARK" "env file readable" "$ENV_FILE"

# Mode 600 check — only root should read/write
mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE" 2>/dev/null)"
if [[ "$mode" == "600" ]]; then
    report "$PASS_MARK" "env file permissions" "mode 600 (root-only)"
else
    report "$WARN_MARK" "env file permissions" "mode $mode — recommended: 600"
    warn_count=$((warn_count + 1))
fi

# Presence + REPLACE_ME check for the four required pairs.
echo
echo "Required credentials:"
check_present "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
check_present "CLERK_SECRET_KEY"
check_present "NEXT_PUBLIC_CONVEX_URL"
check_present "CONVEX_URL"
check_present "CONVEX_DEPLOY_KEY"
check_present "CLERK_ISSUER_URL"
check_present "JWT_SECRET"

# -----------------------------------------------------------------------------
# 2. Format validation — prefixes + URL shape
# -----------------------------------------------------------------------------

echo
echo "=== 2. FORMAT VALIDATION ==="
echo

check_prefix "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" "pk_live_|pk_test_"
check_prefix "CLERK_SECRET_KEY"                  "sk_live_|sk_test_"
check_prefix "CLERK_ISSUER_URL"                  "https://"
check_prefix "CONVEX_DEPLOY_KEY"                 "prod:|dev:"
check_https_url "NEXT_PUBLIC_CONVEX_URL"
check_https_url "CONVEX_URL"

# JWT_SECRET should be exactly 64 hex chars after inject_jwt_secret.sh.
jwt_val="$(read_var JWT_SECRET)"
if [[ -n "$jwt_val" ]]; then
    if [[ "$jwt_val" =~ ^[a-f0-9]{64}$ ]]; then
        report "$PASS_MARK" "JWT_SECRET format" "64 hex chars (openssl rand -hex 32)"
    else
        report "$FAIL_MARK" "JWT_SECRET format" "not 64 hex chars — run: sudo bash infra/deploy/inject_jwt_secret.sh"
        fail_count=$((fail_count + 1))
    fi
fi

# -----------------------------------------------------------------------------
# 3. Docker propagation — frontend build args, NOT just runtime env
# -----------------------------------------------------------------------------

echo
echo "=== 3. DOCKER BUILD-ARG PROPAGATION ==="
echo

if [[ ! -f "$COMPOSE_FILE" ]]; then
    report "$WARN_MARK" "compose file" "MISSING — run from a checkout"
    warn_count=$((warn_count + 1))
else
    report "$PASS_MARK" "compose file" "$COMPOSE_FILE"

    # The three NEXT_PUBLIC_* vars MUST be in the frontend service's
    # build.args block (otherwise next.js bakes empty strings into the
    # client bundle and Clerk/Convex are silently undefined at runtime).
    for key in NEXT_PUBLIC_API_URL NEXT_PUBLIC_CONVEX_URL NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY; do
        if grep -qE "^[[:space:]]+${key}:[[:space:]]+\\\$\\{${key}\\}" "$COMPOSE_FILE"; then
            report "$PASS_MARK" "$key" "wired as build.arg"
        else
            report "$FAIL_MARK" "$key" "NOT wired as build.arg in compose"
            fail_count=$((fail_count + 1))
        fi
    done
fi

# -----------------------------------------------------------------------------
# 4. Runtime connectivity — HEAD against Convex URL (no auth needed)
# -----------------------------------------------------------------------------

echo
echo "=== 4. RUNTIME CONNECTIVITY ==="
echo

convex_url="$(read_var NEXT_PUBLIC_CONVEX_URL)"
if [[ -n "$convex_url" ]] && command -v curl >/dev/null 2>&1; then
    # Convex's WebSocket endpoint responds 426 (Upgrade Required) to a
    # bare HTTP HEAD — that's a successful reach, not an error. We
    # treat any 2xx/3xx/4xx as "host reachable"; only timeout / DNS
    # failure / 5xx are connectivity issues.
    http_status="$(curl -fsS -m 5 -o /dev/null -w '%{http_code}' "$convex_url" 2>/dev/null || true)"
    case "$http_status" in
        2*|3*|4*)
            report "$PASS_MARK" "Convex URL reachable" "HTTP $http_status (host responding)"
            ;;
        5*)
            report "$WARN_MARK" "Convex URL reachable" "HTTP $http_status (server-side error)"
            warn_count=$((warn_count + 1))
            ;;
        000|"")
            report "$FAIL_MARK" "Convex URL reachable" "no response (DNS / network / firewall)"
            fail_count=$((fail_count + 1))
            ;;
        *)
            report "$WARN_MARK" "Convex URL reachable" "unexpected HTTP $http_status"
            warn_count=$((warn_count + 1))
            ;;
    esac
else
    report "$WARN_MARK" "Convex URL reachable" "skipped (no curl or no URL)"
    warn_count=$((warn_count + 1))
fi

# Same shape for Clerk issuer.
clerk_issuer="$(read_var CLERK_ISSUER_URL)"
if [[ -n "$clerk_issuer" ]] && command -v curl >/dev/null 2>&1; then
    http_status="$(curl -fsS -m 5 -o /dev/null -w '%{http_code}' "${clerk_issuer}/.well-known/openid-configuration" 2>/dev/null || true)"
    case "$http_status" in
        2*|3*)
            report "$PASS_MARK" "Clerk OIDC discovery" "HTTP $http_status"
            ;;
        4*)
            report "$WARN_MARK" "Clerk OIDC discovery" "HTTP $http_status (issuer URL may be wrong)"
            warn_count=$((warn_count + 1))
            ;;
        *)
            report "$FAIL_MARK" "Clerk OIDC discovery" "no response (host: ${clerk_issuer})"
            fail_count=$((fail_count + 1))
            ;;
    esac
fi

# -----------------------------------------------------------------------------
# Final tally
# -----------------------------------------------------------------------------

echo
echo "==============================================================="
echo "  SUMMARY"
echo "==============================================================="
if [[ $fail_count -eq 0 && $warn_count -eq 0 ]]; then
    printf "  %b  All credentials present, formatted, propagated, reachable\n" "$PASS_MARK"
    echo "==============================================================="
    exit 0
elif [[ $fail_count -eq 0 ]]; then
    printf "  %b  $warn_count warning(s) — non-blocking\n" "$WARN_MARK"
    echo "==============================================================="
    exit 0
else
    printf "  %b  $fail_count failure(s), $warn_count warning(s) — fix before deploying\n" "$FAIL_MARK"
    echo "==============================================================="
    exit 1
fi
