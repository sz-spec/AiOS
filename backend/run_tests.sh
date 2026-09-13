#!/usr/bin/env bash
# VOS3 Backend Test Runner
# Runs all tests with coverage across api, core, middleware, and services layers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  VOS3 Backend Tests"
echo "=========================================="
echo ""

pytest tests/ \
    --cov=api \
    --cov=core \
    --cov=middleware \
    --cov=services \
    --cov=db \
    --cov-report=term-missing \
    --cov-report=html:coverage_html \
    -v \
    "$@"

echo ""
echo "=========================================="
echo "  Coverage report: coverage_html/index.html"
echo "=========================================="
