#!/usr/bin/env bash
# =============================================================================
# VOS3 Secret Leak Scanner
# =============================================================================
# Scans the repository for accidentally committed API keys and secrets.
# Run before every commit or as a pre-commit hook.
#
# Usage: bash tools/leak_check.sh [--fix]
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FOUND=0

echo "==========================================="
echo "  VOS3 Secret Leak Scanner"
echo "==========================================="
echo ""

# Patterns to search for (regex)
PATTERNS=(
    'sk-proj-[A-Za-z0-9_-]{20,}'         # OpenAI API keys
    'sk-ant-api[A-Za-z0-9_-]{20,}'        # Anthropic API keys
    'AIzaSy[A-Za-z0-9_-]{30,}'            # Google API keys
    'xai-[A-Za-z0-9_-]{20,}'              # XAI (Grok) API keys
    'sk_live_[A-Za-z0-9]{20,}'            # Stripe live keys
    'sk_test_[A-Za-z0-9]{20,}'            # Stripe test keys
    'whsec_[A-Za-z0-9]{20,}'             # Stripe webhook secrets
    'ghp_[A-Za-z0-9]{36,}'               # GitHub personal access tokens
    'gho_[A-Za-z0-9]{36,}'               # GitHub OAuth tokens
    'AKIA[A-Z0-9]{16}'                    # AWS Access Key IDs
    'eyJ[A-Za-z0-9_-]{50,}\.[A-Za-z0-9_-]{50,}\.[A-Za-z0-9_-]{50,}'  # JWT tokens
)

PATTERN_NAMES=(
    "OpenAI API Key"
    "Anthropic API Key"
    "Google API Key"
    "XAI API Key"
    "Stripe Live Key"
    "Stripe Test Key"
    "Stripe Webhook Secret"
    "GitHub PAT"
    "GitHub OAuth Token"
    "AWS Access Key"
    "JWT Token"
)

# Files/dirs to exclude from scanning
EXCLUDE_DIRS=".git|node_modules|__pycache__|.next|build|dist|.env.example"

echo "Scanning: $REPO_ROOT"
echo ""

for i in "${!PATTERNS[@]}"; do
    pattern="${PATTERNS[$i]}"
    name="${PATTERN_NAMES[$i]}"

    # Search tracked files, excluding binary and build dirs
    matches=$(grep -rn --include='*.py' --include='*.ts' --include='*.tsx' \
        --include='*.js' --include='*.json' --include='*.yml' --include='*.yaml' \
        --include='*.env' --include='*.env.*' --include='*.txt' --include='*.md' \
        --include='*.toml' --include='*.cfg' --include='*.ini' --include='*.sh' \
        -E "$pattern" "$REPO_ROOT" 2>/dev/null \
        | grep -vE "$EXCLUDE_DIRS" \
        | grep -v 'leak_check.sh' \
        | grep -v '.env.example' \
        || true)

    if [ -n "$matches" ]; then
        echo -e "${RED}FOUND: $name${NC}"
        echo "$matches" | while IFS= read -r line; do
            echo -e "  ${YELLOW}$line${NC}"
        done
        echo ""
        FOUND=$((FOUND + 1))
    fi
done

# Also check for common secret patterns in .env files
echo "--- Checking .env files ---"
env_files=$(find "$REPO_ROOT" -name '.env' -o -name '.env.backup' -o -name '.env.txt' 2>/dev/null \
    | grep -v node_modules | grep -v .git || true)

if [ -n "$env_files" ]; then
    echo -e "${YELLOW}WARNING: Found .env files that should not be committed:${NC}"
    echo "$env_files" | while IFS= read -r f; do
        echo -e "  ${RED}$f${NC}"
    done
    echo ""
    FOUND=$((FOUND + 1))
fi

echo "==========================================="
if [ "$FOUND" -gt 0 ]; then
    echo -e "${RED}SCAN FAILED: $FOUND secret pattern(s) detected!${NC}"
    echo ""
    echo "Actions required:"
    echo "  1. Rotate ALL exposed keys immediately"
    echo "  2. Remove secrets from git history:"
    echo "     git filter-repo --invert-paths --path .env --path .env.txt --path .env.backup"
    echo "  3. Use .env.example for templates only"
    echo ""
    exit 1
else
    echo -e "${GREEN}SCAN PASSED: No leaked secrets found.${NC}"
    exit 0
fi
