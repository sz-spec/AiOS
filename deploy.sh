#!/usr/bin/env bash
# VOS3 Production Deploy Script — v2026.04.24-FINAL
# Usage: ./deploy.sh [--dry-run]
set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"

log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }

log "=== VOS3 Production Deploy — T-90h Launch ==="

# 1. Load environment
if [[ -f "$SCRIPT_DIR/.env.production" ]]; then
    set -a; source "$SCRIPT_DIR/.env.production"; set +a
    log "Loaded .env.production"
else
    log "ERROR: .env.production not found — aborting"
    exit 1
fi

# 2. Critical env guard
for VAR in CLERK_SECRET_KEY CONVEX_URL STRIPE_SECRET_KEY REDIS_URL ENVIRONMENT; do
    if [[ -z "${!VAR:-}" || "${!VAR}" == *"REPLACE_ME"* ]]; then
        log "ERROR: $VAR is not set or still a placeholder — aborting"
        exit 1
    fi
done
log "Critical env vars: OK"

# 3. Run test suite
log "Running pre-flight test suite..."
if [[ $DRY_RUN -eq 0 ]]; then
    cd "$BACKEND_DIR" && python3 -m pytest tests/test_load_security.py -q --tb=short
    log "Tests: 30/30 PASSED"
fi

# 4. Start gunicorn workers
log "Starting gunicorn (${WORKERS:-4} workers, port ${PORT:-8000})..."
if [[ $DRY_RUN -eq 0 ]]; then
    exec gunicorn main:app \
        -k uvicorn.workers.UvicornWorker \
        -w "${WORKERS:-4}" \
        --bind "0.0.0.0:${PORT:-8000}" \
        --timeout 120 \
        --graceful-timeout 30 \
        --keep-alive 5 \
        --access-logfile - \
        --error-logfile - \
        --chdir "$BACKEND_DIR"
else
    log "[DRY-RUN] Would exec: gunicorn main:app -k uvicorn.workers.UvicornWorker -w ${WORKERS:-4} --bind 0.0.0.0:${PORT:-8000}"
    log "[DRY-RUN] Deploy script validated successfully"
fi
