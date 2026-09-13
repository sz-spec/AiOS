#!/usr/bin/env bash
# =============================================================================
# VOS-Cyber v21.7 — Bulletproof redeploy script
# =============================================================================
#
# Wipes containers + images + build cache, then rebuilds from scratch using
# `--env-file .env.production`. The --env-file flag makes docker compose
# read the env BEFORE substituting ${X} in build.args, which is the only
# foolproof pattern for getting NEXT_PUBLIC_* baked into the bundle.
#
# Usage:
#     cd /opt/vos-cyber
#     sudo bash redeploy.sh
#
# What this script does NOT do:
#   - Edit .env.production. The operator must populate it with real values
#     (or REPLACE_ME placeholders that this script will reject loudly).
#   - Install Docker / nginx. bootstrap.sh does that.
#   - Mint Clerk / Convex keys. Operator gets those from the dashboards.
# =============================================================================

set -euo pipefail

REPO_DIR="${VOS_REPO_DIR:-/opt/vos-cyber}"
cd "$REPO_DIR"

if [[ -t 1 ]]; then
    GREEN="$(printf '\033[1;32m')"
    RED="$(printf '\033[1;31m')"
    YELLOW="$(printf '\033[1;33m')"
    BOLD="$(printf '\033[1m')"
    DIM="$(printf '\033[2m')"
    RESET="$(printf '\033[0m')"
else
    GREEN=""; RED=""; YELLOW=""; BOLD=""; DIM=""; RESET=""
fi

ok()    { printf "  ${GREEN}✓${RESET} %s\n" "$*"; }
fail()  { printf "  ${RED}✗${RESET} %s\n" "$*"; }
warn()  { printf "  ${YELLOW}!${RESET} %s\n" "$*"; }
hdr()   { printf "\n${BOLD}=== %s ===${RESET}\n" "$*"; }

step_failed() {
    printf "\n${RED}${BOLD}STEP FAILED:${RESET} %s\n" "$1"
    exit 1
}

# -----------------------------------------------------------------------------
# 1. Validate env file
# -----------------------------------------------------------------------------

hdr "Step 1/6 · Validate .env.production"

[[ -f .env.production ]] || step_failed ".env.production missing — run setup.sh or create it from .env.production.example"

# REPLACE_ME anywhere on the line catches both `=REPLACE_ME` and
# `=pk_test_REPLACE_ME` (the latter is what the example template uses
# to hint at the expected Clerk key prefix).
if grep -qE "REPLACE_ME" .env.production; then
    fail ".env.production still has REPLACE_ME placeholders:"
    grep -nE "REPLACE_ME" .env.production
    step_failed "fix the env file and rerun"
fi
ok "no REPLACE_ME placeholders"

# Check the required keys are present (length-only — no secret leak)
for key in NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY CLERK_SECRET_KEY NEXT_PUBLIC_CONVEX_URL CONVEX_DEPLOY_KEY JWT_SECRET; do
    val="$(awk -F= -v k="$key" '$0 ~ "^"k"=" { sub("^"k"=", "", $0); print; exit }' .env.production)"
    if [[ -z "$val" ]]; then
        fail "$key MISSING (no line in env file)"
        step_failed "fix the env file and rerun"
    else
        ok "$key present (${#val} chars)"
    fi
done

# -----------------------------------------------------------------------------
# 2. Nuke old containers + images + build cache
# -----------------------------------------------------------------------------

hdr "Step 2/6 · Wipe stale Docker state"

sudo docker compose down --remove-orphans 2>/dev/null || true

# Force-remove any containers from previous runs that compose lost track of.
stale_containers="$(sudo docker ps -aq --filter "name=vos-cyber" 2>/dev/null || true)"
if [[ -n "$stale_containers" ]]; then
    sudo docker rm -f $stale_containers 2>/dev/null || true
    ok "removed $(echo "$stale_containers" | wc -l) stale container(s)"
else
    ok "no stale containers"
fi

# Remove the named images so they get rebuilt fresh.
sudo docker image rm -f vos-cyber-backend:latest vos-cyber-frontend:latest 2>/dev/null || true
ok "removed cached images"

# Prune build cache. -af = all + force, no prompt.
sudo docker builder prune -af >/dev/null
ok "build cache pruned"

# -----------------------------------------------------------------------------
# 3. Build with --env-file (the foolproof pattern)
# -----------------------------------------------------------------------------

hdr "Step 3/6 · Build with --env-file .env.production"

# This is the critical line. --env-file tells docker compose to source
# the env file BEFORE substituting ${X} in build.args. Without it, the
# shell-environment-propagation gymnastics (set -a / sudo -E / etc) is
# the only path, and that's been the source of every "WARN: variable
# not set" we've been chasing.
sudo docker compose --env-file .env.production build --no-cache
ok "build complete"

# -----------------------------------------------------------------------------
# 4. Bring services up
# -----------------------------------------------------------------------------

hdr "Step 4/6 · Bring services up"

sudo docker compose --env-file .env.production up -d
ok "services started"

# -----------------------------------------------------------------------------
# 5. Wait for health
# -----------------------------------------------------------------------------

hdr "Step 5/6 · Wait for healthchecks"

echo -n "  waiting up to 60s for backend healthy..."
for i in $(seq 1 30); do
    state="$(sudo docker inspect -f '{{.State.Health.Status}}' vos-cyber-backend 2>/dev/null || echo unknown)"
    if [[ "$state" == "healthy" ]]; then
        printf "\n  ${GREEN}✓${RESET} backend healthy after ${i}x2s\n"
        break
    fi
    printf "."
    sleep 2
    if [[ $i -eq 30 ]]; then
        printf "\n"
        fail "backend did not become healthy in 60s"
        echo "  Last logs:"
        sudo docker logs vos-cyber-backend --tail 30 2>&1 | sed 's/^/    /'
        step_failed "diagnose with: sudo docker logs vos-cyber-backend"
    fi
done

echo -n "  waiting up to 30s for frontend healthy..."
for i in $(seq 1 15); do
    state="$(sudo docker inspect -f '{{.State.Health.Status}}' vos-cyber-frontend 2>/dev/null || echo unknown)"
    if [[ "$state" == "healthy" ]]; then
        printf "\n  ${GREEN}✓${RESET} frontend healthy after ${i}x2s\n"
        break
    fi
    printf "."
    sleep 2
    if [[ $i -eq 15 ]]; then
        printf "\n"
        warn "frontend not yet healthy after 30s; continuing (may still come up)"
        echo "  Last logs:"
        sudo docker logs vos-cyber-frontend --tail 30 2>&1 | sed 's/^/    /'
    fi
done

# -----------------------------------------------------------------------------
# 6. End-to-end smoke
# -----------------------------------------------------------------------------

hdr "Step 6/6 · End-to-end checks"

backend_status="$(curl -fsS -o /dev/null -m 5 -w '%{http_code}' http://localhost:8000/health 2>/dev/null || echo 000)"
frontend_status="$(curl -fsS -o /dev/null -m 5 -w '%{http_code}' http://localhost:3000 2>/dev/null || echo 000)"
public_status="$(curl -fsS -o /dev/null -m 5 -w '%{http_code}' http://localhost 2>/dev/null || echo 000)"

case "$backend_status" in
    2*|3*) ok "backend  /health        → HTTP $backend_status" ;;
    *)     fail "backend  /health        → HTTP $backend_status" ;;
esac
case "$frontend_status" in
    2*|3*) ok "frontend /              → HTTP $frontend_status" ;;
    *)     fail "frontend /              → HTTP $frontend_status" ;;
esac
case "$public_status" in
    2*|3*) ok "public   localhost:80   → HTTP $public_status (nginx routing OK)" ;;
    *)     warn "public   localhost:80   → HTTP $public_status (nginx may not be reloaded)" ;;
esac

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

echo
public_ip="$(curl -fsS -m 3 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo "<your-ip>")"
echo "==============================================================="
if [[ "$frontend_status" =~ ^2 ]] && [[ "$backend_status" =~ ^2 ]]; then
    printf "%b\n" "${GREEN}${BOLD}  ✓  redeploy complete — services healthy${RESET}"
    echo "==============================================================="
    echo "  Open: http://${public_ip}/"
else
    printf "%b\n" "${YELLOW}${BOLD}  !  redeploy finished but at least one service is unhealthy${RESET}"
    echo "==============================================================="
    echo "  Diagnose:"
    echo "    sudo docker compose ps"
    echo "    sudo docker compose logs backend --tail 50"
    echo "    sudo docker compose logs frontend --tail 50"
fi
echo
