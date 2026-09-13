#!/bin/bash
#
# Generate C source file with embedded user binaries
#
# Usage: embed_binaries.sh <output.c> <binary1> [binary2] ...
#

OUTPUT="$1"
shift

if [ -z "$OUTPUT" ]; then
    echo "Usage: $0 <output.c> <binary1> [binary2] ..."
    exit 1
fi

cat > "$OUTPUT" << 'HEADER'
/**
 * @file embedded_bins.c
 * @brief Embedded user-space binaries
 * @note Auto-generated - do not edit!
 */

#include "../../include/vos/types.h"

HEADER

# Generate arrays for each binary
for BIN in "$@"; do
    if [ ! -f "$BIN" ]; then
        echo "Warning: $BIN not found, skipping"
        continue
    fi

    NAME=$(basename "$BIN")
    # Convert name to valid C identifier
    CNAME=$(echo "$NAME" | tr '.-' '__')

    SIZE=$(stat -f%z "$BIN" 2>/dev/null || stat -c%s "$BIN" 2>/dev/null)

    echo "/* Embedded binary: $NAME ($SIZE bytes) */"
    echo "const uint8_t _embedded_${CNAME}[] = {"
    xxd -i < "$BIN" | sed 's/^/    /'
    echo "};"
    echo "const size_t _embedded_${CNAME}_size = ${SIZE};"
    echo ""
done >> "$OUTPUT"

# Generate the embedded binary table
cat >> "$OUTPUT" << 'TABLE_START'

/* Embedded binary table */
typedef struct {
    const char* name;
    const uint8_t* data;
    size_t size;
} embedded_binary_t;

const embedded_binary_t g_embedded_binaries[] = {
TABLE_START

for BIN in "$@"; do
    if [ ! -f "$BIN" ]; then
        continue
    fi

    NAME=$(basename "$BIN")
    CNAME=$(echo "$NAME" | tr '.-' '__')
    echo "    { \"$NAME\", _embedded_${CNAME}, sizeof(_embedded_${CNAME}) }," >> "$OUTPUT"
done

cat >> "$OUTPUT" << 'TABLE_END'
    { NULL, NULL, 0 }
};

const size_t g_embedded_binaries_count = sizeof(g_embedded_binaries) / sizeof(g_embedded_binaries[0]) - 1;
TABLE_END

echo "Generated $OUTPUT with $(echo "$@" | wc -w) binaries"
