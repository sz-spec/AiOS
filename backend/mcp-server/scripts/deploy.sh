#!/bin/bash

# צבעים להדפסה בטרמינל
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Starting MCP SmartRouter Deployment (2026 Edition) ===${NC}"

# 1. אימות IDs ב-config/router.yaml
echo -e "🔍 Verifying Model IDs in configuration..."

EXPECTED_SONNET="claude-sonnet-4-5-20250929"
ACTUAL_SONNET=$(grep "model_id:" config/router.yaml | grep "sonnet" | awk '{print $2}')

if [ "$ACTUAL_SONNET" != "$EXPECTED_SONNET" ]; then
    echo -e "${RED}❌ Critical Error: Sonnet ID mismatch!${NC}"
    echo -e "Expected: $EXPECTED_SONNET | Found: $ACTUAL_SONNET"
    exit 1
fi
echo -e "${GREEN}✅ Config IDs Verified.${NC}"

# 2. בדיקת סביבת עבודה (Environment)
if [ ! -f .env ]; then
    echo -e "${RED}❌ Error: .env file missing!${NC}"
    exit 1
fi

# 3. ניקוי תהליכים ישנים
echo -e "🔄 Cleaning up old server instances..."
pkill -f "tsx.*server" 2>/dev/null || true

# 4. הרצת השרת במצב Development (עם Logging)
echo -e "${BLUE}🚀 Launching Server...${NC}"
set -a; source .env; set +a
npm run dev > server.log 2>&1 &

# 5. המתנה קצרה ובדיקת חיות (Health Check)
sleep 5
if pgrep -f "tsx.*server" > /dev/null; then
    echo -e "${GREEN}✅ Server is UP and Running!${NC}"
    echo -e "${BLUE}📊 Run 'tail -f server.log' to watch the SmartRouter in action.${NC}"
else
    echo -e "${RED}❌ Server failed to start. Check server.log for details.${NC}"
    exit 1
fi

echo -e "--- Deployment Complete ---"
