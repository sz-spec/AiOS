#!/usr/bin/env bash
# =============================================================================
# VOS-Cyber v21.7-UX — Server Bootstrap (Hybrid Demo)
# =============================================================================
#
# Operator runs this on the target VPS. Idempotent — safe to re-run.
#
#   curl -fsSL https://raw.githubusercontent.com/sz-spec/vos-c/main/infra/deploy/bootstrap.sh | sudo bash -s -- <SSH_PUBKEY>
#
# OR the safer two-step:
#
#   git clone https://github.com/sz-spec/vos-c.git /opt/vos-cyber
#   sudo bash /opt/vos-cyber/infra/deploy/bootstrap.sh "<SSH_PUBKEY>"
#
# What this script does — in order, fail-fast:
#
#   1. Sanity:    confirm we're root on a Debian/Ubuntu box.
#   2. SSH:       install the operator's SSH public key, lock down sshd
#                 (PasswordAuthentication no, PermitRootLogin
#                 prohibit-password). DOES NOT change the root password
#                 here — operator is expected to have rotated it BEFORE
#                 calling this script.
#   3. Packages:  apt update + install docker / docker-compose / nginx /
#                 git / curl. Enable Docker.
#   4. Repo:      clone (or fast-forward) vos-cyber into /opt/vos-cyber
#                 at tag v21.7-UX.
#   5. Env:       refuse to proceed unless /opt/vos-cyber/.env.production
#                 exists and contains no "REPLACE_ME" markers.
#   6. Build:     docker-compose build (pulls Python 3.14, Node 24).
#   7. Up:        docker-compose up -d.
#   8. Nginx:     symlink infra/nginx/vos-cyber.conf into sites-enabled,
#                 disable the default site, reload nginx.
#   9. Smoke:     check / and /api/metrics/health respond on port 80.
#  10. TEE probe: report what the host CPU advertises (TDX / SEV-SNP /
#                 vTPM / nothing). Honest report, no inflation.
#
# What this script DOES NOT do:
#   - Rotate the root password (operator's job, BEFORE running this).
#   - Configure TLS — port 80 only. Add certbot afterwards.
#   - Boot the VOS3 kernel. This is the Hybrid demo (containerised
#     userspace only). The kernel is a separate bare-metal install.
#   - Activate VOS3_SOVEREIGN_MODE. Hybrid demo uses Convex/Clerk live.
# =============================================================================

set -euo pipefail

# ---------- pretty print ----------
log()  { printf '\033[1;36m[bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bootstrap]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[bootstrap] FAIL:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 1. sanity ----------
log "Step 1: sanity check"
[[ $EUID -eq 0 ]] || fail "must run as root (use sudo)"
[[ -r /etc/os-release ]] || fail "/etc/os-release missing — Debian/Ubuntu only"
. /etc/os-release
case "$ID" in
    debian|ubuntu) log "  detected $PRETTY_NAME" ;;
    *) fail "unsupported distro: $ID (Debian/Ubuntu only)" ;;
esac

SSH_PUBKEY="${1:-}"
[[ -n "$SSH_PUBKEY" ]] || fail "usage: $0 \"<ssh-public-key>\""
# Stricter than just the prefix — must also have whitespace + base64 key
# material after the algorithm. Catches the unquoted-value class of bug
# where SSH_PUBKEY=ssh-ed25519 (without quotes) collapses to only the
# algorithm prefix and would otherwise pass a prefix-only regex check.
[[ "$SSH_PUBKEY" =~ ^(ssh-(rsa|ed25519|ecdsa)|ecdsa-sha2-)[[:alnum:]-]*[[:space:]]+[A-Za-z0-9+/=]+ ]] \
    || fail "SSH key looks malformed — missing key material after the algorithm prefix? \
Common cause: unquoted \`export SSH_PUBKEY=ssh-ed25519 AAAA...\` — wrap the whole value in double quotes."

# ---------- 2. SSH hardening ----------
log "Step 2: install SSH key + disable password auth"
mkdir -p /root/.ssh
chmod 700 /root/.ssh
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
if ! grep -qF "$SSH_PUBKEY" /root/.ssh/authorized_keys; then
    echo "$SSH_PUBKEY" >> /root/.ssh/authorized_keys
    log "  added new SSH key"
else
    log "  SSH key already present"
fi

SSHD_CONF=/etc/ssh/sshd_config
cp -n "$SSHD_CONF" "${SSHD_CONF}.bak.$(date +%s)" || true
sed -i \
    -e 's/^[# ]*PasswordAuthentication.*/PasswordAuthentication no/'   \
    -e 's/^[# ]*PermitRootLogin.*/PermitRootLogin prohibit-password/'  \
    -e 's/^[# ]*PubkeyAuthentication.*/PubkeyAuthentication yes/'      \
    "$SSHD_CONF"
grep -qE "^PasswordAuthentication no" "$SSHD_CONF" \
    || echo "PasswordAuthentication no" >> "$SSHD_CONF"
grep -qE "^PermitRootLogin prohibit-password" "$SSHD_CONF" \
    || echo "PermitRootLogin prohibit-password" >> "$SSHD_CONF"

# Validate config BEFORE reloading; a bad sshd_config can lock you out.
sshd -t || fail "sshd_config validation failed — refusing to reload"
systemctl reload ssh 2>/dev/null || systemctl reload sshd
log "  sshd reloaded; password login disabled"

# ---------- 3. packages ----------
log "Step 3: install docker / nginx / git"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    ca-certificates curl git nginx \
    docker.io docker-compose-v2

systemctl enable --now docker
systemctl enable --now nginx

# Allow port 80 if ufw is active.
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp || true
fi

# ---------- 4. repo ----------
#
# v21.7-deploy-fix — was previously checking out the v21.7-UX tag,
# which is pinned at b6c58a0 (before the python:3.14 Dockerfile bump,
# the pydantic version fix, the perf+fortress commits, and setup.sh).
# Pinning to a stale tag silently bypassed every fix shipped after the
# tag was cut. Switched to tracking main, which is where the deploy
# fixes actually live.
log "Step 4: clone or update repo at /opt/vos-cyber (tracking origin/main)"
REPO_DIR=/opt/vos-cyber
if [[ ! -d "$REPO_DIR/.git" ]]; then
    git clone https://github.com/sz-spec/vos-c.git "$REPO_DIR"
fi
cd "$REPO_DIR"
git fetch --tags --quiet origin
# If we were left on a detached HEAD by an earlier bootstrap run that
# checked out the tag, switch back to main first.
git checkout --quiet main
git pull --ff-only origin main
log "  HEAD: $(git log --oneline -1)"

# ---------- 5. env validation ----------
log "Step 5: validate .env.production"
ENV_FILE="$REPO_DIR/.env.production"
if [[ ! -f "$ENV_FILE" ]]; then
    fail "$ENV_FILE missing — copy .env.production.example, fill in real values, rerun"
fi
# NOTE: pattern is `REPLACE_ME` anywhere on the line (no `=` anchor). The
# example template uses values like `pk_live_REPLACE_ME` and
# `sk_live_REPLACE_ME` to hint at the expected Clerk format; an
# `^[A-Z_]+=REPLACE_ME` regex would silently miss those.
if grep -qE "REPLACE_ME" "$ENV_FILE"; then
    grep -nE "REPLACE_ME" "$ENV_FILE" >&2
    fail ".env.production still contains REPLACE_ME placeholders — fill them in"
fi
log "  env file present, no REPLACE_ME markers"

# Centralised log dir.
mkdir -p /var/log/vos-cyber/{backend,frontend}
chown -R 1000:1000 /var/log/vos-cyber/backend
chown -R 1001:1001 /var/log/vos-cyber/frontend

# ---------- 6 + 7. compose ----------
log "Step 6+7: docker compose build + up"
# Export env file for compose substitution of build.args (NEXT_PUBLIC_*).
set -a
# shellcheck disable=SC1091
. "$ENV_FILE"
set +a

docker compose -f "$REPO_DIR/docker-compose.yml" build --pull
# v21.7-deploy-fix — --remove-orphans cleans up stale containers from
# previous failed bootstrap runs (otherwise we hit "container name
# already in use" because Docker doesn't auto-prune containers from
# crashed `up -d` invocations). Belt-and-braces: also `down` first
# to make the up idempotent regardless of prior container state.
docker compose -f "$REPO_DIR/docker-compose.yml" down --remove-orphans 2>/dev/null || true
docker compose -f "$REPO_DIR/docker-compose.yml" up -d --remove-orphans

# ---------- 8. nginx ----------
log "Step 8: install nginx vhost"
NGINX_CONF=/etc/nginx/sites-available/vos-cyber
NGINX_LINK=/etc/nginx/sites-enabled/vos-cyber
cp "$REPO_DIR/infra/nginx/vos-cyber.conf" "$NGINX_CONF"
ln -sf "$NGINX_CONF" "$NGINX_LINK"
# Disable default site if present.
rm -f /etc/nginx/sites-enabled/default
nginx -t || fail "nginx config validation failed"
systemctl reload nginx

# ---------- 9. smoke test ----------
log "Step 9: smoke test (waiting up to 60s for services)"
for i in $(seq 1 30); do
    if curl -fsS -m 3 "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
        log "  backend healthy after ${i}x2s"
        break
    fi
    sleep 2
    if [[ $i -eq 30 ]]; then
        warn "backend did not become healthy in 60s — check: docker compose logs backend"
    fi
done

if curl -fsS -m 3 "http://127.0.0.1/" >/dev/null 2>&1; then
    log "  nginx + frontend responding on port 80"
else
    warn "nginx not yet responding on / — check: nginx -t && journalctl -u nginx"
fi

# ---------- 10. TEE / TPM probe (honest report) ----------
log "Step 10: TEE / TPM probe — reporting what this VPS actually advertises"
echo "=========================================================="

# CPUID feature bits.
if grep -qiw "tdx" /proc/cpuinfo 2>/dev/null; then
    echo "  Intel TDX:    YES  (host CPU advertises tdx_guest in /proc/cpuinfo)"
elif dmesg 2>/dev/null | grep -iq "tdx"; then
    echo "  Intel TDX:    advertised in dmesg only — CPU may support, BIOS may not"
else
    echo "  Intel TDX:    NO   (no tdx flag in /proc/cpuinfo, no tdx in dmesg)"
fi

if grep -qiw "sev_snp" /proc/cpuinfo 2>/dev/null \
    || [[ -r /sys/module/kvm_amd/parameters/sev_snp ]] \
    && grep -q Y /sys/module/kvm_amd/parameters/sev_snp 2>/dev/null; then
    echo "  AMD SEV-SNP:  YES  (sev_snp advertised by kernel)"
elif grep -qiw "sev" /proc/cpuinfo 2>/dev/null; then
    echo "  AMD SEV-SNP:  partial — host has sev but not sev_snp"
else
    echo "  AMD SEV-SNP:  NO   (no sev/sev_snp flags)"
fi

if [[ -c /dev/tpm0 || -c /dev/tpmrm0 ]]; then
    echo "  TPM 2.0:      YES  (device node present)"
    if command -v tpm2_getcap >/dev/null 2>&1; then
        FAM=$(tpm2_getcap properties-fixed 2>/dev/null | awk '/TPM2_PT_FAMILY_INDICATOR/{print $2}')
        echo "                  family indicator: ${FAM:-unknown}"
    fi
else
    echo "  TPM 2.0:      NO   (no /dev/tpm[0|rm0] — typical for budget VPS)"
fi

if dmesg 2>/dev/null | grep -iq "hypervisor"; then
    HV=$(dmesg | grep -i "hypervisor" | head -1)
    echo "  Hypervisor:   $(echo "$HV" | head -c 100)"
else
    echo "  Hypervisor:   not detected via dmesg"
fi

echo "=========================================================="
echo
echo "Honest interpretation for this Hybrid demo:"
echo "  - The Hybrid demo runs in containers and does NOT consume any"
echo "    of the TEE primitives above. Whatever this VPS advertises is"
echo "    a property of the rented hardware, not of the v21.7 deploy."
echo "  - The only Hybrid-mode security gate engaged here is application-"
echo "    layer: the Pydantic input bounds, JWT auth, and the Z3-PROVEN"
echo "    egress policy verifier (which lives in the kernel — also NOT"
echo "    engaged in this Hybrid demo)."
echo "  - For a Sovereign deployment that uses TDX/SEV-SNP, you would"
echo "    boot the VOS3 ELF kernel directly (Mode A in the deployment"
echo "    guide). That requires console access and a TEE-capable host;"
echo "    most budget VPS providers do NOT expose the underlying TEE"
echo "    extensions to guest VMs."
echo

PUBLIC_IP=$(curl -fsS -m 3 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
echo "Done. Open: http://${PUBLIC_IP}/"
echo
echo "Useful follow-ups:"
echo "  docker compose -f /opt/vos-cyber/docker-compose.yml ps"
echo "  docker compose -f /opt/vos-cyber/docker-compose.yml logs -f backend"
echo "  systemctl status nginx"
echo
echo "TLS step (recommended, not done by this script):"
echo "  apt install -y certbot python3-certbot-nginx"
echo "  certbot --nginx -d your.domain.example.com"
