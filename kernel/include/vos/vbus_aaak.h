/**
 * @file vbus_aaak.h
 * @brief V-AAAK Semantic Compression Dictionary — Phase 6.6
 *
 * 64-token dictionary for the VBus AAAK (Abbreviated AI Application Kodex)
 * semantic dialect.  Each token maps a single byte code (0x00-0x3F) to a
 * frequently-occurring LLM token string.  Compression: [0xFF, code] → token.
 *
 * This dictionary MUST match the Python V_AAAK_DICT in
 * backend/services/vbus_driver.py byte-for-byte, or the provenance chain
 * will detect a mismatch and reject the frame.
 *
 * Encoding:
 *   - Greedy longest-match by first byte
 *   - Token match → [0xFF, index] (2 bytes)
 *   - Literal 0xFF → [0xFF, 0xFF] (escape)
 *   - All other bytes → literal (1 byte)
 *
 * Decoding:
 *   - On 0xFF: next byte is code (0-63 → token) or 0xFF (literal escape)
 *   - Code >= 64 → error (-EILSEQ)
 *   - Truncated escape → error (-EILSEQ)
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_VBUS_AAAK_H
#define VOS3_VBUS_AAAK_H

#include <stdint.h>
#include <stddef.h>

/** @brief Escape byte — signals dictionary token or literal 0xFF follows */
#define V_AAAK_ESCAPE       0xFFU

/** @brief Number of tokens in the AAAK dictionary */
#define V_AAAK_DICT_SIZE    64U

/** @brief Maximum token length in bytes (longest: " which" / " there" = 6) */
#define V_AAAK_MAX_TOKEN_LEN  6U

/**
 * @brief The 64-token AAAK dictionary.
 *
 * Index 0-47: Space-prefixed common English words (LLM token vocabulary)
 * Index 48-55: Common suffixes and standalone fragments
 * Index 56-63: Whitespace and punctuation patterns
 *
 * Stored as NUL-terminated C strings.  Cache-line aligned for scan performance.
 */
static const char * const g_aaak_dict[V_AAAK_DICT_SIZE] __attribute__((aligned(64))) = {
    /* 0-7:   Space-prefixed determiners/prepositions */
    " the",   " a",     " is",    " of",
    " and",   " to",    " in",    " it",
    /* 8-15:  Space-prefixed conjunctions/verbs */
    " that",  " for",   " was",   " on",
    " are",   " with",  " as",    " this",
    /* 16-23: Space-prefixed verbs/prepositions */
    " be",    " at",    " have",  " from",
    " or",    " by",    " not",   " but",
    /* 24-31: Space-prefixed question words/pronouns */
    " what",  " all",   " were",  " when",
    " we",    " there", " can",   " an",
    /* 32-39: Space-prefixed possessives/conditionals */
    " your",  " which", " their", " if",
    " do",    " will",  " each",  " how",
    /* 40-47: Space-prefixed pronouns/adverbs */
    " them",  " then",  " he",    " she",
    " my",    " no",    " more",  " so",
    /* 48-55: Standalone fragments/suffixes */
    "the",    "and",    "ing",    "tion",
    "ed ",    "er ",    "es ",    "re ",
    /* 56-63: Whitespace and punctuation */
    "\n",     "  ",     ", ",     ". ",
    ": ",     ";\n",    "}\n",    "{\n",
};

/** @brief Pre-computed token lengths (bytes, excluding NUL) */
static const uint8_t g_aaak_lens[V_AAAK_DICT_SIZE] = {
    /* 0-7 */   4, 2, 3, 3, 4, 3, 3, 3,
    /* 8-15 */  5, 4, 4, 3, 4, 5, 3, 5,
    /* 16-23 */ 3, 3, 5, 5, 3, 3, 4, 4,
    /* 24-31 */ 5, 4, 5, 5, 3, 6, 4, 3,
    /* 32-39 */ 5, 6, 6, 3, 3, 5, 5, 4,
    /* 40-47 */ 5, 5, 3, 4, 3, 3, 5, 3,
    /* 48-55 */ 3, 3, 3, 4, 3, 3, 3, 3,
    /* 56-63 */ 1, 2, 2, 2, 2, 2, 2, 2,
};

/**
 * @brief Encode raw data using V-AAAK greedy longest-match compression.
 *
 * Scans input left-to-right.  At each position, tries to match the longest
 * dictionary token starting with the current byte.  On match, emits [0xFF, idx].
 * Literal 0xFF bytes are escaped as [0xFF, 0xFF].  All other bytes pass through.
 *
 * @param in        Input data
 * @param in_len    Input length
 * @param out       Output buffer (must be >= 2 * in_len for worst case)
 * @param out_max   Output buffer capacity
 * @return Encoded length on success, 0 on error (buffer overflow)
 */
uint32_t vos3_v_aaak_encode(const uint8_t *in, uint32_t in_len,
                              uint8_t *out, uint32_t out_max);

/**
 * @brief Decode V-AAAK compressed data back to raw bytes.
 *
 * On 0xFF: consume next byte as dictionary code (0-63) or escape (0xFF).
 * Invalid codes (>= 64, except 0xFF) return 0 (error).
 * Truncated escape at end of input returns 0 (error).
 *
 * @param in        Compressed data
 * @param in_len    Compressed length
 * @param out       Output buffer
 * @param out_max   Output buffer capacity
 * @return Decoded length on success, 0 on error
 */
uint32_t vos3_v_aaak_decode(const uint8_t *in, uint32_t in_len,
                              uint8_t *out, uint32_t out_max);

#endif /* VOS3_VBUS_AAAK_H */
