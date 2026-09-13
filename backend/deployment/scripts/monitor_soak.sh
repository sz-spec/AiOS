#!/bin/bash
# monitor_soak.sh - LangGraph Upgrade Soak Period Monitor
# Run daily during soak period: ./monitor_soak.sh [log_file] [hours]
#
# Usage:
#   ./monitor_soak.sh                           # Default: /var/log/vos3/app.log, 24h
#   ./monitor_soak.sh /path/to/app.log          # Custom log file
#   ./monitor_soak.sh /path/to/app.log 48       # Custom log file and hours
#
# Add to cron for daily monitoring:
#   0 9 * * * /path/to/monitor_soak.sh >> /var/log/vos3/soak_report.log 2>&1

set -e

# Configuration
LOG_FILE="${1:-/var/log/vos3/app.log}"
HOURS="${2:-24}"
ALERT_THRESHOLD_ERRORS=10
ALERT_THRESHOLD_AGG_ERRORS=5

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=============================================="
echo "LangGraph Soak Monitor: $(date)"
echo "Log file: $LOG_FILE"
echo "Period: Last ${HOURS} hours"
echo "=============================================="

# Check if log file exists
if [ ! -f "$LOG_FILE" ]; then
    echo -e "${RED}ERROR: Log file not found: $LOG_FILE${NC}"
    exit 1
fi

# Filter logs by time if possible (depends on log format)
# For now, we process the entire file

echo ""
echo "=== 1. ERROR SUMMARY ==="
# Use grep + wc for reliable counting (grep -c can have issues)
ERROR_COUNT=$(grep "\[PERF\].*status=error" "$LOG_FILE" 2>/dev/null | wc -l | tr -d ' ')
ERROR_COUNT=${ERROR_COUNT:-0}
echo "Total errors: $ERROR_COUNT"

if [ "$ERROR_COUNT" -gt 0 ]; then
    echo ""
    echo "Recent errors:"
    grep "\[PERF\].*status=error" "$LOG_FILE" 2>/dev/null | tail -5
fi

if [ "$ERROR_COUNT" -gt "$ALERT_THRESHOLD_ERRORS" ]; then
    echo -e "${RED}ALERT: Error count ($ERROR_COUNT) exceeds threshold ($ALERT_THRESHOLD_ERRORS)${NC}"
fi

echo ""
echo "=== 2. PARALLEL EXECUTION PERFORMANCE ==="

# ── Performance Parsing (format-agnostic) ──
# Extracts duration number regardless of log prefix format
# Works with any prefix (timestamp, loglevel, logger name, etc.)
# Matches: [PERF] frontend: 5.12s | status=ok
parse_perf() {
    local node_name="$1"
    local log_file="$2"

    # Extract duration numbers (e.g., "5.12" from "5.12s")
    local values=$(grep "\[PERF\] ${node_name}:" "$log_file" 2>/dev/null | \
        grep -oE '[0-9]+\.[0-9]+s' | grep -oE '[0-9]+\.[0-9]+')

    if [ -z "$values" ]; then
        echo "  ${node_name}: no data"
        return
    fi

    echo "$values" | awk -v name="$node_name" '
    {
        sum += $1; n++;
        if (n == 1 || $1 < min) min = $1;
        if (n == 1 || $1 > max) max = $1;
    }
    END {
        printf "  %s: avg=%.2fs | min=%.2fs | max=%.2fs | count=%d\n", name, sum/n, min, max, n
    }'
}

# Core nodes (parallel execution)
parse_perf "frontend" "$LOG_FILE"
parse_perf "backend" "$LOG_FILE"
parse_perf "aggregator" "$LOG_FILE"

echo ""
echo "Other nodes:"
parse_perf "architect" "$LOG_FILE"
parse_perf "tester" "$LOG_FILE"
parse_perf "reviewer" "$LOG_FILE"
parse_perf "finalize" "$LOG_FILE"

echo ""
echo "=== 3. COMMAND ROUTING DISTRIBUTION ==="
# Extracts agent name after "Routed to: " regardless of prefix
ROUTING_DATA=$(grep "\[COMMAND\] Routed to:" "$LOG_FILE" 2>/dev/null | \
    grep -oE 'Routed to: [^ ]+' | sed 's/Routed to: //' | \
    sort | uniq -c | sort -rn)

if [ -n "$ROUTING_DATA" ]; then
    echo "$ROUTING_DATA"
    TOTAL_ROUTES=$(grep "\[COMMAND\] Routed to:" "$LOG_FILE" 2>/dev/null | wc -l | tr -d ' ')
    echo "  Total routed: ${TOTAL_ROUTES:-0}"
else
    echo "  No routing data found"
fi

echo ""
echo "=== 4. PARALLEL DISPATCH STATS ==="
PARALLEL_BOTH=$(grep "\[PARALLEL\] Dispatching branches: frontend=True, backend=True" "$LOG_FILE" 2>/dev/null | wc -l | tr -d ' ')
PARALLEL_BOTH=${PARALLEL_BOTH:-0}
PARALLEL_FRONTEND=$(grep "\[PARALLEL\] Dispatching branches: frontend=True, backend=False" "$LOG_FILE" 2>/dev/null | wc -l | tr -d ' ')
PARALLEL_FRONTEND=${PARALLEL_FRONTEND:-0}
echo "  Frontend + Backend (parallel): $PARALLEL_BOTH"
echo "  Frontend only:                 $PARALLEL_FRONTEND"

echo ""
echo "=== 5. AGGREGATION HEALTH ==="
AGG_TOTAL=$(grep "\[PARALLEL\] Aggregated:" "$LOG_FILE" 2>/dev/null | wc -l | tr -d ' ')
AGG_TOTAL=${AGG_TOTAL:-0}
AGG_ERRORS=$(grep "\[PARALLEL\] Aggregated:" "$LOG_FILE" 2>/dev/null | grep -v "errors=none" | wc -l | tr -d ' ')
AGG_ERRORS=${AGG_ERRORS:-0}
AGG_SUCCESS=$((AGG_TOTAL - AGG_ERRORS))

echo "Total aggregations: $AGG_TOTAL"
echo "Successful:         $AGG_SUCCESS"
echo "With errors:        $AGG_ERRORS"

if [ "$AGG_ERRORS" -gt "$ALERT_THRESHOLD_AGG_ERRORS" ]; then
    echo -e "${RED}ALERT: Aggregation errors ($AGG_ERRORS) exceed threshold ($ALERT_THRESHOLD_AGG_ERRORS)${NC}"
    echo ""
    echo "Recent aggregation errors:"
    grep "\[PARALLEL\] Aggregated:" "$LOG_FILE" | grep -v "errors=none" | tail -5
fi

if [ "$AGG_TOTAL" -gt 0 ]; then
    SUCCESS_RATE=$(awk "BEGIN {printf \"%.1f\", ($AGG_SUCCESS / $AGG_TOTAL) * 100}")
    echo "Success rate:       ${SUCCESS_RATE}%"
fi

echo ""
echo "=== 6. HEALTH CHECK ==="
# Call the API health endpoint if available
API_URL="${VOS3_API_URL:-http://localhost:8000}"
if command -v curl &> /dev/null; then
    echo "Checking $API_URL/api/metrics/health/langgraph..."
    HEALTH=$(curl -s --connect-timeout 5 "$API_URL/api/metrics/health/langgraph" 2>/dev/null || echo '{"status": "unreachable"}')
    echo "$HEALTH" | python3 -m json.tool 2>/dev/null || echo "$HEALTH"
else
    echo "curl not available, skipping API health check"
fi

echo ""
echo "=============================================="
echo "Soak Monitor Complete: $(date)"
echo "=============================================="

# Exit with error code if alerts triggered
if [ "$ERROR_COUNT" -gt "$ALERT_THRESHOLD_ERRORS" ] || [ "$AGG_ERRORS" -gt "$ALERT_THRESHOLD_AGG_ERRORS" ]; then
    exit 1
fi

exit 0
