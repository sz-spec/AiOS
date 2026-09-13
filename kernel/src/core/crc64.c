/**
 * @file crc64.c
 * @brief CRC64-ECMA implementation (polynomial 0x42F0E1EBA9EA3693)
 *
 * Table-driven 256-entry lookup for high throughput.
 * Used for VBus frame integrity when negotiated via HANDSHAKE.
 */
#include <vos/crc64.h>

#define CRC64_ECMA_POLY 0x42F0E1EBA9EA3693ULL

static uint64_t g_crc64_table[256];
static int g_crc64_table_ready;

void vos3_crc64_init(void)
{
    for (int i = 0; i < 256; i++) {
        uint64_t crc = (uint64_t)i;
        for (int j = 0; j < 8; j++) {
            if (crc & 1)
                crc = (crc >> 1) ^ CRC64_ECMA_POLY;
            else
                crc >>= 1;
        }
        g_crc64_table[i] = crc;
    }
    g_crc64_table_ready = 1;
}

uint64_t vos3_crc64(uint64_t crc, const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    crc = ~crc;
    for (size_t i = 0; i < len; i++) {
        crc = g_crc64_table[(uint8_t)(crc ^ p[i])] ^ (crc >> 8);
    }
    return ~crc;
}
