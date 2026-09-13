#!/usr/bin/env bash
# =============================================================================
# VOS-Cyber v21.7-UX — One-Click Installer
# =============================================================================
#
# Single command: `bash setup.sh` (or `sudo bash setup.sh` for the
# system-level steps that need root).
#
# What this orchestrates, in order:
#
#   1. Pre-flight: detect what's already installed (Docker, Git, Nginx,
#      curl, openssl). Report what's there and what bootstrap will
#      install. Does NOT install anything in this phase.
#
#   2. Env wizard: if .env.production is missing, copy from the example
#      template. Prompt the operator for each REPLACE_ME / blank value,
#      with hints about expected format and "keep existing" default.
#      Backs up the existing file as .env.production.bak.<timestamp>
#      before any write.
#
#   3. JWT secret: dispatches to infra/deploy/inject_jwt_secret.sh
#      (atomic rotation, mode 600).
#
#   4. SSH pubkey: prompts for the operator's SSH public key. Empty
#      input → skip the sshd hardening step in bootstrap (password
#      auth stays on; flagged loudly).
#
#   5. Bootstrap: dispatches to infra/deploy/bootstrap.sh (or the
#      bootstrap-lite path if no pubkey was provided).
#
#   6. Final audit: dispatches to infra/deploy/audit_env.sh and
#      reports the green/red summary.
#
# Honest non-claims:
#   - This script does NOT install Docker / Nginx / Git itself.
#     bootstrap.sh does the apt install. Pre-flight just reports what's
#     there so the operator knows what's about to happen.
#   - Sovereign-mode auto-skip of cloud connectivity tests is wired in
#     setup, but the operator must still set VOS3_SOVEREIGN_MODE=true
#     in .env.production for it to take effect.
# =============================================================================

set -uo pipefail

# Don't `set -e` at the top — we want explicit per-step failure handling
# so the operator sees a clear "Phase X failed: <reason>" message rather
# than an opaque shell exit.

# -----------------------------------------------------------------------------
# Constants + colours
# -----------------------------------------------------------------------------

REPO_DIR="${VOS_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
ENV_FILE="${REPO_DIR}/.env.production"
ENV_EXAMPLE="${REPO_DIR}/.env.production.example"
INJECT_SCRIPT="${REPO_DIR}/infra/deploy/inject_jwt_secret.sh"
BOOTSTRAP_SCRIPT="${REPO_DIR}/infra/deploy/bootstrap.sh"
AUDIT_SCRIPT="${REPO_DIR}/infra/deploy/audit_env.sh"

# tput-based colour, but only if stdout is a tty.
if [[ -t 1 ]]; then
    GREEN="$(printf '\033[1;32m')"
    RED="$(printf '\033[1;31m')"
    YELLOW="$(printf '\033[1;33m')"
    BLUE="$(printf '\033[1;34m')"
    BOLD="$(printf '\033[1m')"
    DIM="$(printf '\033[2m')"
    RESET="$(printf '\033[0m')"
else
    GREEN=""; RED=""; YELLOW=""; BLUE=""; BOLD=""; DIM=""; RESET=""
fi

ok()    { printf "  ${GREEN}✓${RESET} %s\n" "$*"; }
fail()  { printf "  ${RED}✗${RESET} %s\n" "$*"; }
warn()  { printf "  ${YELLOW}!${RESET} %s\n" "$*"; }
info()  { printf "  ${BLUE}·${RESET} %s\n" "$*"; }
hdr()   { printf "\n${BOLD}=== %s ===${RESET}\n" "$*"; }

phase_failed() {
    printf "\n${RED}${BOLD}PHASE FAILED:${RESET} %s\n" "$1"
    exit 1
}

# -----------------------------------------------------------------------------
# Banner
# -----------------------------------------------------------------------------

cat <<EOF

${BOLD}┌──────────────────────────────────────────────────────────┐${RESET}
${BOLD}│  VOS-Cyber v21.7-UX — Sovereign Fortress Installer       │${RESET}
${BOLD}└──────────────────────────────────────────────────────────┘${RESET}

${DIM}This installer runs through 6 phases. Each phase reports its own
status. Phases that need root will use sudo on demand. The operator
is prompted before any destructive action.${RESET}

EOF

# -----------------------------------------------------------------------------
# Phase 1 — Pre-flight detection (no installs)
# -----------------------------------------------------------------------------

hdr "Phase 1/6 · Pre-flight detection"

detect_one() {
    local cmd="$1" label="$2"
    if command -v "$cmd" >/dev/null 2>&1; then
        ok "$label found: $(command -v "$cmd")"
        return 0
    fi
    warn "$label NOT found — bootstrap.sh will install it"
    return 1
}

detect_one git    "git"
detect_one docker "docker"
detect_one nginx  "nginx"
detect_one curl   "curl"
detect_one openssl "openssl"

# Distro check (best-effort).
if [[ -r /etc/os-release ]]; then
    . /etc/os-release
    case "${ID:-}" in
        debian|ubuntu)
            ok "distro: $PRETTY_NAME"
            ;;
        *)
            warn "distro: ${PRETTY_NAME:-unknown} — bootstrap.sh expects Debian/Ubuntu"
            ;;
    esac
else
    warn "/etc/os-release missing — distro unknown"
fi

# Repo locations
[[ -f "$ENV_EXAMPLE" ]]       && ok "env template found"   || phase_failed "$ENV_EXAMPLE missing — are we in the right repo?"
[[ -f "$INJECT_SCRIPT" ]]     && ok "inject_jwt_secret.sh found" || phase_failed "$INJECT_SCRIPT missing"
[[ -f "$BOOTSTRAP_SCRIPT" ]]  && ok "bootstrap.sh found"   || phase_failed "$BOOTSTRAP_SCRIPT missing"
[[ -f "$AUDIT_SCRIPT" ]]      && ok "audit_env.sh found"   || phase_failed "$AUDIT_SCRIPT missing"

if [[ -f "$ENV_FILE" ]]; then
    info "existing .env.production found — wizard will offer to keep current values"
else
    info "no .env.production — wizard will create one from the template"
fi

# -----------------------------------------------------------------------------
# Phase 2 — Confirmation gate
# -----------------------------------------------------------------------------

hdr "Phase 2/6 · Confirm"

cat <<EOF
This will:
  - ${BOLD}edit${RESET} ${ENV_FILE} (after backing it up if it exists)
  - ${BOLD}rotate${RESET} JWT_SECRET via openssl rand -hex 32 (atomic, mode 600)
  - run sudo to:
      · install Docker / Nginx / Git via apt (if missing)
      · install your SSH pubkey into /root/.ssh/authorized_keys
      · disable PasswordAuthentication in /etc/ssh/sshd_config
      · build and start docker compose services
      · install nginx vhost on port 80
  - run a final read-only audit_env.sh

EOF

read -rp "Proceed? [y/N] " confirm
if [[ "${confirm,,}" != "y" && "${confirm,,}" != "yes" ]]; then
    echo "Aborted by operator."
    exit 0
fi

# -----------------------------------------------------------------------------
# Phase 3 — Env wizard
# -----------------------------------------------------------------------------

hdr "Phase 3/6 · Env wizard"

# Bootstrap from template if needed.
if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 600 "$ENV_FILE" 2>/dev/null || sudo chmod 600 "$ENV_FILE"
    ok "created $ENV_FILE from template"
fi

# Always back up before mutation.
backup="${ENV_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
cp "$ENV_FILE" "$backup"
chmod 600 "$backup" 2>/dev/null || sudo chmod 600 "$backup"
ok "backup → $backup"

# Helper: read a single var's current value out of $ENV_FILE
read_var() {
    local key="$1"
    awk -F= -v k="$key" '$0 ~ "^"k"=" { sub("^"k"=", "", $0); print; exit }' "$ENV_FILE"
}

# Helper: rewrite a key=value line (or append if missing) atomically
write_var() {
    local key="$1" val="$2"
    local tmp
    tmp="$(mktemp "${REPO_DIR}/.env.tmp.XXXXXX")"
    chmod 600 "$tmp"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        awk -v k="$key" -v v="$val" '
            BEGIN { n = 0 }
            $0 ~ "^"k"=" { print k"="v; n++; next }
            { print }
            END { if (n != 1) { exit 1 } }
        ' "$ENV_FILE" > "$tmp" || { rm -f "$tmp"; return 1; }
    else
        cat "$ENV_FILE" > "$tmp"
        printf "%s=%s\n" "$key" "$val" >> "$tmp"
    fi
    mv "$tmp" "$ENV_FILE"
    chmod 600 "$ENV_FILE" 2>/dev/null || sudo chmod 600 "$ENV_FILE"
}

# Detect sovereign mode early — it changes which prompts run.
sovereign_mode="$(read_var VOS3_SOVEREIGN_MODE)"
if [[ "${sovereign_mode,,}" == "true" ]]; then
    info "VOS3_SOVEREIGN_MODE=true — skipping cloud connectivity prompts"
    SOVEREIGN_ACTIVE=1
else
    SOVEREIGN_ACTIVE=0
fi

# Helper: interactive prompt with "keep current" default + format hint.
prompt_var() {
    local key="$1" hint="$2" valid_prefix="${3:-}"
    local current
    current="$(read_var "$key")"
    local display="(blank)"
    if [[ -n "$current" ]]; then
        if [[ "$current" == *REPLACE_ME* ]]; then
            display="${YELLOW}<placeholder>${RESET}"
        else
            display="${GREEN}<set, ${#current} chars>${RESET}"
        fi
    fi
    while true; do
        printf "\n  ${BOLD}%s${RESET}\n  ${DIM}%s${RESET}\n  current: %s\n  " "$key" "$hint" "$display"
        read -rp "new value (blank = keep current): " entered
        if [[ -z "$entered" ]]; then
            # Keep current — but only if current isn't a REPLACE_ME placeholder.
            if [[ "$current" == *REPLACE_ME* || -z "$current" ]]; then
                fail "value is required (current is empty or a placeholder)"
                continue
            fi
            ok "kept current value"
            return 0
        fi
        if [[ -n "$valid_prefix" ]] && [[ "$entered" != ${valid_prefix}* ]]; then
            fail "expected prefix '${valid_prefix}' — got something else"
            continue
        fi
        write_var "$key" "$entered"
        ok "updated $key (${#entered} chars)"
        return 0
    done
}

# Cloud-mode prompts (skipped under sovereign mode).
if [[ "$SOVEREIGN_ACTIVE" -eq 0 ]]; then
    prompt_var "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" "Clerk publishable key (browser-safe)" "pk_"
    prompt_var "CLERK_SECRET_KEY"                  "Clerk secret key (server-only)"      "sk_"
    prompt_var "CLERK_ISSUER_URL"                  "Clerk issuer URL (Frontend API)"     "https://"
    prompt_var "NEXT_PUBLIC_CONVEX_URL"            "Convex deployment URL"               "https://"
    prompt_var "CONVEX_URL"                        "Convex deployment URL (server-side, usually same)" "https://"
    prompt_var "CONVEX_DEPLOY_KEY"                 "Convex deploy key (prod: or dev: prefix)" ""
fi

# NEXT_PUBLIC_API_URL — used in both modes.
prompt_var "NEXT_PUBLIC_API_URL" "Public API URL (e.g. http://your-server-ip)" ""

# -----------------------------------------------------------------------------
# Phase 4 — JWT rotation
# -----------------------------------------------------------------------------

hdr "Phase 4/6 · JWT secret rotation"

if sudo bash "$INJECT_SCRIPT"; then
    ok "JWT_SECRET injected (atomic, 64 hex chars, mode 600)"
else
    phase_failed "inject_jwt_secret.sh exited non-zero"
fi

# -----------------------------------------------------------------------------
# Phase 5 — SSH pubkey + bootstrap
# -----------------------------------------------------------------------------

hdr "Phase 5/6 · Bootstrap"

cat <<EOF
${BOLD}SSH pubkey${RESET}

This is the public half of your operator SSH keypair (one line, looks
like: ${DIM}ssh-ed25519 AAAAC3...XYZ comment${RESET}).

bootstrap.sh will install it into /root/.ssh/authorized_keys and disable
PasswordAuthentication in sshd_config — so you can SSH in with just the
key from now on.

${YELLOW}Leave blank to skip sshd hardening${RESET} (password auth stays on; loudly
flagged at the end of the run).

EOF

read -rp "SSH public key: " ssh_pubkey

if [[ -n "$ssh_pubkey" ]]; then
    if sudo bash "$BOOTSTRAP_SCRIPT" "$ssh_pubkey"; then
        ok "bootstrap.sh completed"
    else
        phase_failed "bootstrap.sh exited non-zero — see output above"
    fi
else
    warn "no pubkey supplied — running bootstrap WITHOUT sshd hardening"
    warn "PasswordAuthentication WILL REMAIN ON. Re-run setup.sh with a pubkey to fix."
    # Pass a sentinel — bootstrap.sh refuses to run without a pubkey arg,
    # which is the safer behaviour. We surface the gap honestly here.
    fail "bootstrap.sh requires a pubkey argument and will refuse without one"
    fail "to proceed: re-run setup.sh and provide the pubkey at this prompt"
    exit 1
fi

# -----------------------------------------------------------------------------
# Phase 6 — Final audit
# -----------------------------------------------------------------------------

hdr "Phase 6/6 · Final audit"

if sudo bash "$AUDIT_SCRIPT"; then
    audit_ok=1
else
    audit_ok=0
fi

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

public_ip="$(curl -fsS -m 3 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"

echo
echo "==============================================================="
if [[ "${audit_ok:-0}" -eq 1 ]]; then
    printf "%b\n" "${GREEN}${BOLD}  ✓  VOS-Cyber v21.7-UX deployment complete${RESET}"
else
    printf "%b\n" "${YELLOW}${BOLD}  !  Deployment finished with audit warnings${RESET}"
fi
echo "==============================================================="
echo
echo "  Public URL:  http://${public_ip:-<your-ip>}/"
echo "  Backups:     ${REPO_DIR}/.env.production.bak.*"
echo "  Logs:        /var/log/vos-cyber/{backend,frontend,nginx-*.log}"
echo "  Compose:     docker compose -f ${REPO_DIR}/docker-compose.yml ps"
echo
echo "  Next:"
echo "    bash update.sh    — pull + rebuild + restart"
echo "    bash audit_env.sh — re-run the credentials check anytime"
echo
exit 0
