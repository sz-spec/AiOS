#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# tools/deploy/nginx_render.sh — render nginx.conf from template
#
# Substitutes __VOS3_*__ tokens in nginx.conf.template using env vars and
# writes the result to nginx.conf (or to the path passed as argv[1]).
#
# Required env:
#   VOS3_DOMAIN           e.g. vos3.app
# Optional env:
#   VOS3_CLERK_DOMAIN     defaults to clerk.${VOS3_DOMAIN}
#   VOS3_API_DOMAIN       defaults to api.${VOS3_DOMAIN}
#
# Designed to be called from deploy.sh OR from a docker entrypoint that
# mounts nginx.conf.template at /etc/nginx/templates/nginx.conf.template.
#
# Exits non-zero if any required token isn't substituted (i.e. some
# VOS3_DOMAIN was missing).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="${REPO_ROOT}/nginx.conf.template"
OUTPUT="${1:-${REPO_ROOT}/nginx.conf}"

if [[ ! -f "$TEMPLATE" ]]; then
    echo "ERROR: template not found at $TEMPLATE" >&2
    exit 2
fi

VOS3_DOMAIN="${VOS3_DOMAIN:-}"
if [[ -z "$VOS3_DOMAIN" ]]; then
    echo "ERROR: VOS3_DOMAIN env var is required (e.g. vos3.app)" >&2
    exit 3
fi

VOS3_CLERK_DOMAIN="${VOS3_CLERK_DOMAIN:-clerk.${VOS3_DOMAIN}}"
VOS3_API_DOMAIN="${VOS3_API_DOMAIN:-api.${VOS3_DOMAIN}}"

# Use sed -e with delimiter | to avoid issues with the / in URLs.
sed \
    -e "s|__VOS3_DOMAIN__|${VOS3_DOMAIN}|g" \
    -e "s|__VOS3_CLERK_DOMAIN__|${VOS3_CLERK_DOMAIN}|g" \
    -e "s|__VOS3_API_DOMAIN__|${VOS3_API_DOMAIN}|g" \
    "$TEMPLATE" > "$OUTPUT"

# Sanity: no unsubstituted __VOS3_*__ tokens remain.
if grep -q '__VOS3_[A-Z_]*__' "$OUTPUT"; then
    echo "ERROR: nginx.conf still contains unsubstituted tokens:" >&2
    grep -n '__VOS3_[A-Z_]*__' "$OUTPUT" >&2
    exit 4
fi

echo "Rendered nginx.conf:"
echo "  template:        $TEMPLATE"
echo "  output:          $OUTPUT"
echo "  VOS3_DOMAIN:     $VOS3_DOMAIN"
echo "  CLERK_DOMAIN:    $VOS3_CLERK_DOMAIN"
echo "  API_DOMAIN:      $VOS3_API_DOMAIN"
