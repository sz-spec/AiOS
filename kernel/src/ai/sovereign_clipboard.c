/**
 * @file sovereign_clipboard.c
 * @brief VOS3 Sovereign Clipboard — 3-Layer DLP with Privacy-by-Design
 *
 * @details Clipboard data never stored in kernel — only metadata + CRC32C hash.
 *
 *          PII Detection:
 *            Layer 1: Integer-only pattern scan (email, phone, SSN, CC, API key)
 *            Layer 2: Shannon entropy heuristic (detect secrets/API keys)
 *            Layer 3: Trust tier boundary check (PUD level matrix)
 *
 *          Ring buffer stores 16 entries of metadata for audit trail.
 *
 * @version 1.0.0
 * @date 2026-04-12
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 5.1: vSpace Desktop & Sovereign App Sandboxing
 */

#include "../../include/vos/vspace.h"
#include "../../include/vos/console.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/crc32c.h"
#include "../../include/vos/string.h"
#include "../../include/arch/x86_64/cpu.h"

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * EXTERNAL STATE
 * ============================================================================ */

extern vspace_state_t *vspace_get_state(void);
extern int pud_check_boundary(uint8_t src_pud, uint8_t dst_pud, uint8_t action_type);

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/* Memory ops: using optimized memset/memcpy from string.h (ERMS-accelerated) */
/* CRC32C: using shared crc32c() from crc32c.h (HW SSE4.2 when available) */

/* ============================================================================
 * LAYER 1: PII PATTERN DETECTION (Integer-only, no regex engine)
 * ============================================================================ */

static int sc_is_alpha(uint8_t c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
}

static int sc_is_digit(uint8_t c)
{
    return (c >= '0' && c <= '9');
}

static int sc_is_alnum(uint8_t c)
{
    return sc_is_alpha(c) || sc_is_digit(c);
}

static int sc_is_hex(uint8_t c)
{
    return sc_is_digit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

/**
 * @brief Detect email pattern: something@something.something
 */
static int sc_detect_email(const uint8_t *data, uint32_t len)
{
    for (uint32_t i = 1; i < len; i++) {
        if (data[i] == '@') {
            /* Check chars before @ are alphanum/dot/underscore */
            if (i > 0 && (sc_is_alnum(data[i - 1]) || data[i - 1] == '.' ||
                          data[i - 1] == '_')) {
                /* Check chars after @ have a dot */
                for (uint32_t j = i + 1; j < len; j++) {
                    if (data[j] == '.') {
                        /* Must have at least 1 char after dot */
                        if (j + 1 < len && sc_is_alpha(data[j + 1])) {
                            return 1;
                        }
                    }
                }
            }
        }
    }
    return 0;
}

/**
 * @brief Detect phone pattern: 10+ digits with optional separators.
 */
static int sc_detect_phone(const uint8_t *data, uint32_t len)
{
    uint32_t digit_count = 0;
    uint32_t consecutive_start = 0;
    int in_number = 0;

    for (uint32_t i = 0; i < len; i++) {
        if (sc_is_digit(data[i]) || data[i] == '+' || data[i] == '-' ||
            data[i] == '(' || data[i] == ')' || data[i] == ' ') {
            if (sc_is_digit(data[i])) {
                if (!in_number) {
                    in_number = 1;
                    consecutive_start = i;
                    digit_count = 1;
                } else {
                    digit_count++;
                }
            }
        } else {
            if (in_number && digit_count >= 10) {
                return 1;
            }
            in_number = 0;
            digit_count = 0;
        }
    }

    (void)consecutive_start;
    return (in_number && digit_count >= 10) ? 1 : 0;
}

/**
 * @brief Detect SSN pattern: 3digit-2digit-4digit
 */
static int sc_detect_ssn(const uint8_t *data, uint32_t len)
{
    if (len < 11) return 0;

    for (uint32_t i = 0; i + 10 < len; i++) {
        /* Pattern: DDD-DD-DDDD */
        if (sc_is_digit(data[i]) && sc_is_digit(data[i + 1]) &&
            sc_is_digit(data[i + 2]) && data[i + 3] == '-' &&
            sc_is_digit(data[i + 4]) && sc_is_digit(data[i + 5]) &&
            data[i + 6] == '-' &&
            sc_is_digit(data[i + 7]) && sc_is_digit(data[i + 8]) &&
            sc_is_digit(data[i + 9]) && sc_is_digit(data[i + 10])) {
            return 1;
        }
    }
    return 0;
}

/**
 * @brief Detect credit card pattern: 4 groups of 4 digits with separator.
 */
static int sc_detect_credit_card(const uint8_t *data, uint32_t len)
{
    if (len < 15) return 0;

    for (uint32_t i = 0; i + 15 <= len; i++) {
        /* Check for DDDD-DDDD-DDDD-DDDD or DDDD DDDD DDDD DDDD */
        if (sc_is_digit(data[i]) && sc_is_digit(data[i + 1]) &&
            sc_is_digit(data[i + 2]) && sc_is_digit(data[i + 3]) &&
            (data[i + 4] == '-' || data[i + 4] == ' ') &&
            sc_is_digit(data[i + 5]) && sc_is_digit(data[i + 6]) &&
            sc_is_digit(data[i + 7]) && sc_is_digit(data[i + 8]) &&
            (data[i + 9] == '-' || data[i + 9] == ' ') &&
            sc_is_digit(data[i + 10]) && sc_is_digit(data[i + 11]) &&
            sc_is_digit(data[i + 12]) && sc_is_digit(data[i + 13]) &&
            (data[i + 14] == '-' || data[i + 14] == ' ')) {
            /* Check 4th group */
            if (i + 18 < len &&
                sc_is_digit(data[i + 15]) && sc_is_digit(data[i + 16]) &&
                sc_is_digit(data[i + 17]) && sc_is_digit(data[i + 18])) {
                return 1;
            }
        }
    }
    return 0;
}

/**
 * @brief Detect API key: 32+ hex/base64 chars with high density.
 */
static int sc_detect_api_key(const uint8_t *data, uint32_t len)
{
    if (len < 32) return 0;

    uint32_t hex_run = 0;
    for (uint32_t i = 0; i < len; i++) {
        if (sc_is_hex(data[i]) || data[i] == '+' || data[i] == '/' ||
            data[i] == '=') {
            hex_run++;
            if (hex_run >= 32) return 1;
        } else {
            hex_run = 0;
        }
    }
    return 0;
}

/**
 * @brief Layer 1: Run all pattern detectors.
 * @return 1 if PII detected, 0 if clean
 */
static int sc_layer1_patterns(const uint8_t *data, uint32_t len)
{
    if (sc_detect_email(data, len)) return 1;
    if (sc_detect_phone(data, len)) return 1;
    if (sc_detect_ssn(data, len)) return 1;
    if (sc_detect_credit_card(data, len)) return 1;
    if (sc_detect_api_key(data, len)) return 1;
    return 0;
}

/* ============================================================================
 * LAYER 2: ENTROPY HEURISTIC
 * ============================================================================ */

/**
 * @brief Compute Shannon entropy of data using integer approximation.
 *
 * @details Returns entropy * 100 (centibits) to avoid floating point.
 *          E.g., 450 = 4.50 bits/byte.
 *
 *          Uses: H = -sum(p_i * log2(p_i))
 *          Integer approx: log2(x) ~ (x * 100) / 30 for x in [1/256, 1]
 *          Actually uses lookup table for log2(count) approximation.
 */
static uint32_t sc_entropy_centibits(const uint8_t *data, uint32_t len)
{
    if (len == 0) return 0;

    /* Count byte frequencies */
    uint32_t freq[256];
    memset(freq, 0, sizeof(freq));
    for (uint32_t i = 0; i < len; i++) {
        freq[data[i]]++;
    }

    /* Compute entropy * 100 using integer math
     * H = sum( -p_i * log2(p_i) ) where p_i = freq[i] / len
     * H * len = sum( -freq[i] * log2(freq[i]/len) )
     * H * len = sum( freq[i] * (log2(len) - log2(freq[i])) )
     *
     * We approximate log2(x) * 100 using bit-scan.
     */
    uint32_t entropy_x100 = 0;

    /* Approximate log2(len) * 100 */
    uint32_t log2_len_x100 = 0;
    {
        uint32_t tmp = len;
        while (tmp > 1) {
            log2_len_x100 += 100;
            tmp >>= 1;
        }
    }

    for (int i = 0; i < 256; i++) {
        if (freq[i] == 0) continue;

        /* Approximate log2(freq[i]) * 100 */
        uint32_t log2_fi_x100 = 0;
        {
            uint32_t tmp = freq[i];
            while (tmp > 1) {
                log2_fi_x100 += 100;
                tmp >>= 1;
            }
        }

        /* Contribution: freq[i] * (log2_len - log2_fi) */
        if (log2_len_x100 > log2_fi_x100) {
            entropy_x100 += freq[i] * (log2_len_x100 - log2_fi_x100);
        }
    }

    /* Divide by len to get bits/byte * 100 */
    return entropy_x100 / len;
}

/**
 * @brief Layer 2: Check if data has suspiciously high entropy.
 *
 * @details If entropy > 4.5 bits/byte AND length > 20 → flag as potential secret.
 * @return 1 if high entropy detected, 0 if normal
 */
static int sc_layer2_entropy(const uint8_t *data, uint32_t len)
{
    if (len <= 20) return 0;

    uint32_t entropy = sc_entropy_centibits(data, len);

    /* Threshold: 450 centibits = 4.50 bits/byte */
    if (entropy >= 450) {
        return 1;
    }

    return 0;
}

/* ============================================================================
 * LAYER 3: TRUST TIER (delegates to pud_check_boundary)
 * ============================================================================ */

/**
 * @brief Layer 3: Check PUD trust tier boundary.
 * @return SCRUB_CLEAN (allow), SCRUB_PII_DETECTED (scrub), SCRUB_BLOCKED (deny)
 */
static uint8_t sc_layer3_trust(uint8_t src_pud, uint8_t dst_pud)
{
    int rc = pud_check_boundary(src_pud, dst_pud, 0);

    if (rc == -1) return SCRUB_BLOCKED;      /* EPERM from SOVEREIGN */
    if (rc == (int)SCRUB_PII_DETECTED) return SCRUB_PII_DETECTED;
    return SCRUB_CLEAN;
}

/* ============================================================================
 * CLIPBOARD STATE (stored in g_vspace via accessor)
 * ============================================================================ */

/* Per-paste context for tracking last copy source */
static uint8_t  g_last_copy_src_pud = 0;
static uint32_t g_last_copy_size = 0;
static const void *g_last_copy_data = NULL;

/* ============================================================================
 * SOVEREIGN CLIPBOARD API
 * ============================================================================ */

/**
 * @brief Initialize the sovereign clipboard.
 */
int sclip_init(void)
{
    vspace_state_t *state = vspace_get_state();
    if (!state) return -22;

    memset(&state->clipboard, 0, sizeof(state->clipboard));
    g_last_copy_src_pud = 0;
    g_last_copy_size = 0;
    g_last_copy_data = NULL;

    VOS3_INFO("[sClip] Sovereign Clipboard initialized (ring=%u, 3-layer DLP)",
              VSPACE_CLIPBOARD_RING);
    return 0;
}

/**
 * @brief Copy data to the sovereign clipboard.
 *
 * @details Stores only metadata + CRC32C hash. The actual data pointer is
 *          kept temporarily for paste retrieval (simulated shared memory).
 */
int sclip_copy(uint8_t src_pud, const void *data, uint32_t len)
{
    vspace_state_t *state = vspace_get_state();
    if (!state || !state->initialized) return -22;
    if (!data || len == 0) return -22;
    if (src_pud >= VSPACE_MAX_PUDS || !state->puds[src_pud].active) return -22;

    vspace_clip_state_t *clip = &state->clipboard;

    /* Record entry in ring buffer */
    vspace_clip_entry_t *entry = &clip->ring[clip->head % VSPACE_CLIPBOARD_RING];
    entry->data_hash = crc32c(data, len);
    entry->source_pud = src_pud;
    entry->dest_pud = 0xFF; /* Unknown until paste */
    entry->scrub_result = SCRUB_CLEAN;
    entry->size = len;
    entry->timestamp_tsc = vos3_rdtsc();

    clip->head = (clip->head + 1) % VSPACE_CLIPBOARD_RING;
    clip->total_copies++;

    /* Store pointer for paste (simulated) */
    g_last_copy_src_pud = src_pud;
    g_last_copy_size = len;
    g_last_copy_data = data;

    return 0;
}

/**
 * @brief Paste from clipboard to destination PUD.
 *
 * @details Runs full 3-layer DLP check before allowing data copy.
 *
 * @return 0 = success (clean paste), SCRUB_PII_DETECTED, SCRUB_BLOCKED, -EINVAL
 */
int sclip_paste(uint8_t dst_pud, void *out_buf, uint32_t buf_size, uint32_t *out_len)
{
    vspace_state_t *state = vspace_get_state();
    if (!state || !state->initialized) return -22;
    if (dst_pud >= VSPACE_MAX_PUDS || !state->puds[dst_pud].active) return -22;
    if (!out_buf || buf_size == 0) return -22;
    if (!g_last_copy_data || g_last_copy_size == 0) return -22;

    /* Run scrub check */
    int scrub = sclip_scrub_check(g_last_copy_src_pud, dst_pud,
                                  g_last_copy_data, g_last_copy_size);

    if (scrub == (int)SCRUB_BLOCKED) {
        state->clipboard.total_blocks++;
        if (out_len) *out_len = 0;
        return (int)SCRUB_BLOCKED;
    }

    if (scrub == (int)SCRUB_PII_DETECTED) {
        state->clipboard.total_scrubs++;
        state->clipboard.total_blocks++;
        if (out_len) *out_len = 0;
        return (int)SCRUB_PII_DETECTED;
    }

    /* Clean — allow paste */
    uint32_t copy_len = g_last_copy_size;
    if (copy_len > buf_size) copy_len = buf_size;
    memcpy(out_buf, g_last_copy_data, copy_len);
    if (out_len) *out_len = copy_len;

    return 0;
}

/**
 * @brief Run 3-layer DLP scrub check on data.
 *
 * @return SCRUB_CLEAN, SCRUB_PII_DETECTED, or SCRUB_BLOCKED
 */
int sclip_scrub_check(uint8_t src_pud, uint8_t dst_pud,
                      const void *data, uint32_t len)
{
    vspace_state_t *state = vspace_get_state();
    if (!state || !state->initialized) return -22;
    if (!data || len == 0) return -22;

    const uint8_t *bytes = (const uint8_t *)data;

    /* Layer 3: Trust tier check (fast-path for SOVEREIGN block) */
    uint8_t tier_result = sc_layer3_trust(src_pud, dst_pud);
    if (tier_result == SCRUB_BLOCKED) {
        return (int)SCRUB_BLOCKED;
    }

    /* If same PUD or PUBLIC→PUBLIC: skip PII scan, allow */
    if (src_pud == dst_pud) {
        return (int)SCRUB_CLEAN;
    }

    /* If tier says scrub required OR we're crossing from PRIVATE→PUBLIC */
    if (tier_result == SCRUB_PII_DETECTED) {
        /* Layer 1: Pattern scan */
        if (sc_layer1_patterns(bytes, len)) {
            state->clipboard.total_scrubs++;
            return (int)SCRUB_PII_DETECTED;
        }

        /* Layer 2: Entropy heuristic */
        if (sc_layer2_entropy(bytes, len)) {
            state->clipboard.total_scrubs++;
            return (int)SCRUB_PII_DETECTED;
        }
    }

    /* For non-PRIVATE→PUBLIC crossings, still check Layer 1+2 */
    if (tier_result == SCRUB_CLEAN && src_pud != dst_pud) {
        /* Layer 1 */
        if (sc_layer1_patterns(bytes, len)) {
            state->clipboard.total_scrubs++;
            return (int)SCRUB_PII_DETECTED;
        }
        /* Layer 2 */
        if (sc_layer2_entropy(bytes, len)) {
            state->clipboard.total_scrubs++;
            return (int)SCRUB_PII_DETECTED;
        }
    }

    return (int)SCRUB_CLEAN;
}

/**
 * @brief Get clipboard DLP statistics.
 */
void sclip_get_stats(vspace_stats_t *out)
{
    if (!out) return;

    vspace_state_t *state = vspace_get_state();
    if (!state) return;

    out->clips_copied = state->clipboard.total_copies;
    out->clips_scrubbed = state->clipboard.total_scrubs;
    out->clips_blocked = state->clipboard.total_blocks;
    out->windows_created = state->windows_created;
    out->windows_destroyed = state->windows_destroyed;
    out->workspace_switches = state->workspace_switches;
    out->pud_violations = 0;
}
