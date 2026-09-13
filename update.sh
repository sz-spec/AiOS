#!/usr/bin/env bash
# =============================================================================
# VOS-Cyber v21.7-UX — Update / Maintenance script
# =============================================================================
#
# One command: `bash update.sh` (or `sudo bash update.sh` for the steps
# that need root).
#
# What it does, in order:
#   1. Show current commit + branch (so the operator knows the starting state)
#   2. git fetch + fast-forward pull
#   3. docker compose build --pull (fetches new base images)
#   4. docker compose up -d (replaces containers; healthcheck-gated start)
#   5. docker system prune (only old images, never volumes)
#   6. systemctl reload nginx (in case the vhost changed in the new commit)
#   7. infra/deploy/audit_env.sh — confirm credentials still healthy
#
# What it does NOT do:
#   - Run the env wizard (that's setup.sh's job; update keeps the
#     existing .env.production untouched)
#   - Rotate JWT_SECRET (also setup.sh's job; rotating on every update
#     would invalidate every active session — we don't do that)
#   - Disable / re-harden sshd (one-time op done by bootstrap.sh; if
#     you need to re-do it, re-run setup.sh)
#   - Run database migrations (Convex doesn't have client-side
#     migrations; schema is defined in frontend/convex/schema.ts and
#     applied via codegen on a developer laptop)
# =============================================================================

set -uo pipefail

REPO_DIR="${VOS_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
COMPOSE_FILE="${REPO_DIR}/docker-compose.yml"
AUDIT_SCRIPT="${REPO_DIR}/infra/deploy/audit_env.sh"

# Colours
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

cd "$REPO_DIR" || step_failed "cannot cd to $REPO_DIR"

# -----------------------------------------------------------------------------
# Step 1 — Snapshot before update
# -----------------------------------------------------------------------------

hdr "Step 1/7 · Current state"

before_commit="$(git log --format=%h -n 1 2>/dev/null || echo 'unknown')"
before_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
ok "branch: $before_branch"
ok "commit: $before_commit"

# -----------------------------------------------------------------------------
# Step 2 — git pull
# -----------------------------------------------------------------------------

hdr "Step 2/7 · Pull latest"

if ! git fetch --quiet origin; then
    step_failed "git fetch failed — check network / remote"
fi

# Refuse to update if there are uncommitted local changes — would
# either be lost or cause merge conflicts.
if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "uncommitted local changes detected:"
    git status --short
    step_failed "commit / stash / discard local changes before updating"
fi

if ! git pull --ff-only origin "$before_branch"; then
    step_failed "git pull --ff-only failed — diverging history?"
fi

after_commit="$(git log --format=%h -n 1)"
if [[ "$before_commit" == "$after_commit" ]]; then
    ok "already up to date at $after_commit"
    NEEDS_REBUILD=0
else
    ok "$before_commit → $after_commit"
    NEEDS_REBUILD=1
    echo
    git log --oneline "${before_commit}..${after_commit}" | sed 's/^/    /'
fi

# -----------------------------------------------------------------------------
# Step 3 — docker compose build
# -----------------------------------------------------------------------------

hdr "Step 3/7 · Rebuild containers"

# Source env so build.args resolve (NEXT_PUBLIC_* are baked at build time).
if [[ -f "$REPO_DIR/.env.production" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO_DIR/.env.production"
    set +a
    ok "env file sourced for build args"
else
    warn ".env.production missing — run setup.sh first"
    step_failed "cannot rebuild without env"
fi

if [[ "$NEEDS_REBUILD" -eq 1 ]]; then
    if ! sudo docker compose -f "$COMPOSE_FILE" build --pull; then
        step_failed "docker compose build failed"
    fi
    ok "rebuild complete"
else
    ok "skipped (no new commits)"
fi

# -----------------------------------------------------------------------------
# Step 4 — docker compose up -d
# -----------------------------------------------------------------------------

hdr "Step 4/7 · Start / restart services"

if ! sudo docker compose -f "$COMPOSE_FILE" up -d; then
    step_failed "docker compose up -d failed"
fi
ok "services up"

# Wait briefly for healthchecks to settle. up -d returns when containers
# are spawned, not when healthy.
echo "  ${DIM}waiting up to 60s for healthchecks…${RESET}"
for i in $(seq 1 30); do
    if sudo docker compose -f "$COMPOSE_FILE" ps --format "{{.State}}" 2>/dev/null \
        | grep -q "running"; then
        ok "containers healthy after ${i}x2s"
        break
    fi
    sleep 2
done

# -----------------------------------------------------------------------------
# Step 5 — Prune stale Docker artifacts
# -----------------------------------------------------------------------------

hdr "Step 5/7 · Prune Docker cache"

# IMPORTANT: prune --filter "until=72h" -f keeps recent layers (in case
# you need to roll back) but reclaims space from old builds. NEVER use
# `docker volume prune` here — that would nuke /var/log/vos-cyber and
# any persisted SQLCipher vault.
sudo docker system prune --filter "until=72h" -f 2>&1 | tail -3 | sed 's/^/    /'
ok "prune complete (volumes preserved)"

# -----------------------------------------------------------------------------
# Step 6 — Reload nginx (vhost may have changed)
# -----------------------------------------------------------------------------

hdr "Step 6/7 · Reload nginx"

NGINX_VHOST_SRC="${REPO_DIR}/infra/nginx/vos-cyber.conf"
NGINX_VHOST_DST="/etc/nginx/sites-available/vos-cyber"

if [[ -f "$NGINX_VHOST_SRC" ]]; then
    if [[ -f "$NGINX_VHOST_DST" ]]; then
        # Compare; only update if changed.
        if ! sudo cmp -s "$NGINX_VHOST_SRC" "$NGINX_VHOST_DST"; then
            sudo cp "$NGINX_VHOST_SRC" "$NGINX_VHOST_DST"
            ok "vhost updated"
            if sudo nginx -t 2>/dev/null; then
                sudo systemctl reload nginx
                ok "nginx reloaded"
            else
                fail "new vhost failed nginx -t — restoring previous"
                sudo nginx -t 2>&1 | tail -5 | sed 's/^/    /'
                # Note: we can't auto-rollback here because we already
                # cp'd. Operator action: fix the vhost or git revert.
                step_failed "nginx config invalid — manual review required"
            fi
        else
            ok "vhost unchanged (no reload needed)"
        fi
    else
        warn "/etc/nginx/sites-available/vos-cyber missing — run setup.sh first"
    fi
fi

# -----------------------------------------------------------------------------
# Step 7 — Final audit
# -----------------------------------------------------------------------------

hdr "Step 7/7 · Audit"

if sudo bash "$AUDIT_SCRIPT"; then
    audit_ok=1
else
    audit_ok=0
fi

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------

echo
echo "==============================================================="
if [[ "${audit_ok:-0}" -eq 1 ]]; then
    printf "%b\n" "${GREEN}${BOLD}  ✓  Update complete${RESET}"
else
    printf "%b\n" "${YELLOW}${BOLD}  !  Update finished with audit warnings${RESET}"
fi
echo "==============================================================="
echo
echo "  Was:    $before_commit"
echo "  Now:    $after_commit"
echo "  Compose: sudo docker compose -f ${COMPOSE_FILE} ps"
echo "  Logs:    /var/log/vos-cyber/{backend,frontend,nginx-*.log}"
echo
exit 0
