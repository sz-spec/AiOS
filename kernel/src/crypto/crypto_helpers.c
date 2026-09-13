/**
 * @file crypto_helpers.c
 * @brief VOS3 Cryptographic Utilities -- Cache Wipe, FPU Guard, HKDF, Feature Detection
 *
 * @details Freestanding kernel implementation of cryptographic helper routines
 *          for the VOS3 Sovereign Crypto Shield. All code is GPR-only compatible
 *          (built with -mno-sse); SIMD instructions are encoded via .byte directives
 *          to avoid assembler complaints.
 *
 *          Capabilities provided:
 *          - vos3_cache_wipe()    : Volatile-zero + CLFLUSHOPT/CLFLUSH cache eviction
 *          - vos3_cache_flush()   : Cache line flush without zeroing
 *          - vos3_fpu_begin()     : Save FPU/SSE state, clear CR0.TS for AES-NI
 *          - vos3_fpu_end()       : Restore FPU/SSE state, re-arm CR0.TS
 *          - vos3_hkdf_extract()  : RFC 5869 HKDF-Extract (HMAC-SHA256)
 *          - vos3_hkdf_expand()   : RFC 5869 HKDF-Expand  (HMAC-SHA256)
 *          - vos3_tls13_hkdf_expand_label() : RFC 8446 Section 7.1 key schedule
 *          - vos3_cpu_has_aesni()     : CPUID AES-NI detection
 *          - vos3_cpu_has_pclmulqdq() : CPUID PCLMULQDQ detection
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 7.2 -- Sovereign Crypto Shield (v35.1)
 * @note Freestanding: no libc dependencies. Uses kernel string.h, sha256.h, cpu.h.
 * @note All inline asm uses .byte encoding for SSE/FXSAVE/CLFLUSHOPT since the
 *       kernel is compiled with -mno-sse and the assembler would otherwise reject
 *       these mnemonics.
 */

#include "../../include/vos/crypto.h"
#include "../../include/vos/sha256.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/arch/x86_64/cpu.h"

/* ============================================================================
 * EXTERNAL SYMBOLS
 * ============================================================================ */

/** @brief Set to 1 if CPUID.07H:EBX[23] indicates CLFLUSHOPT support (ai_pte.c) */
extern int g_cpu_has_clflushopt;

/** @brief Set to 1 if XSAVE is available and enabled (interrupts.c) */
extern int g_use_xsave;

/** @brief XCR0 feature mask for XSAVE/XRSTOR (interrupts.c) */
extern uint64_t g_xcr0_mask;

/** @brief Returns the current CPU ID (0 or 1 for SMP-2) (interrupts.c) */
extern uint32_t get_cpu_id(void);

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Cache line size on all modern x86_64 (64 bytes) */
#define CACHE_LINE_SIZE         64U

/** @brief CR0.TS (Task Switched) bit -- bit 3 */
#define CR0_TS_BIT              (1ULL << 3)

/** @brief Maximum CPUs in VOS3 (SMP-2) */
#define VOS3_MAX_CPUS           2U

/** @brief FXSAVE/FXRSTOR state area size (512 bytes minimum, 1024 for safety) */
#define FPU_STATE_SIZE          1024U

/** @brief Maximum HKDF-Expand iterations (RFC 5869 Section 2.3) */
#define HKDF_MAX_ITERATIONS     255U

/** @brief TLS 1.3 label prefix "tls13 " (6 bytes, no NUL) */
#define TLS13_LABEL_PREFIX      "tls13 "
#define TLS13_LABEL_PREFIX_LEN  6U

/** @brief Maximum TLS 1.3 HkdfLabel info blob size
 *  2 (length) + 1 (label_len) + 6 (prefix) + 249 (label max) + 1 (ctx_len) + 255 (ctx max) = 514
 *  We cap at 512 for sanity.
 */
#define TLS13_HKDF_LABEL_MAX   512U

/* ============================================================================
 * PER-CPU FPU STATE SAVE AREA
 * ============================================================================
 *
 * FXSAVE requires 16-byte alignment. We use 64-byte alignment (cache line)
 * to avoid false sharing between CPUs. Each slot is 1024 bytes to accommodate
 * XSAVE extended state if g_use_xsave is set.
 *
 * Layout: g_crypto_fpu_state[cpu_id][0..1023]
 */
static __attribute__((aligned(64))) uint8_t g_crypto_fpu_state[VOS3_MAX_CPUS][FPU_STATE_SIZE];

/** @brief Per-CPU saved CR0 value (to restore TS bit accurately) */
static uint64_t g_crypto_saved_cr0[VOS3_MAX_CPUS];

/* ============================================================================
 * CACHE LINE WIPE -- CLFLUSHOPT / CLFLUSH
 * ============================================================================ */

/**
 * @brief Flush cache lines covering [buf, buf+len) using CLFLUSHOPT or CLFLUSH
 *
 * @param[in] buf   Start address
 * @param[in] len   Length in bytes
 *
 * @note Uses .byte encoding:
 *       CLFLUSHOPT (%rax) = 0x66, 0x0F, 0xAE, 0x38
 *       CLFLUSH    (%rax) = 0x0F, 0xAE, 0x38
 *       Address is loaded into %rax before each .byte sequence.
 */
static void cache_flush_lines(const void *buf, size_t len)
{
    if (buf == NULL || len == 0U) {
        return;
    }

    /* Align start down to cache line boundary */
    uintptr_t start = (uintptr_t)buf & ~((uintptr_t)(CACHE_LINE_SIZE - 1U));
    uintptr_t end   = (uintptr_t)buf + len;

    if (g_cpu_has_clflushopt) {
        /* CLFLUSHOPT path -- ordered flush, faster than CLFLUSH on Haswell+ */
        for (uintptr_t addr = start; addr < end; addr += CACHE_LINE_SIZE) {
            __asm__ volatile(
                ".byte 0x66, 0x0F, 0xAE, 0x38"   /* clflushopt (%rax) */
                :
                : "a"(addr)
                : "memory"
            );
        }
    } else {
        /* CLFLUSH fallback -- available on all x86_64 since Pentium 4 */
        for (uintptr_t addr = start; addr < end; addr += CACHE_LINE_SIZE) {
            __asm__ volatile(
                ".byte 0x0F, 0xAE, 0x38"          /* clflush (%rax) */
                :
                : "a"(addr)
                : "memory"
            );
        }
    }

    /* Full memory fence to ensure all flushes are globally visible */
    __asm__ volatile("mfence" ::: "memory");
}

/**
 * @brief Securely wipe a buffer: zero memory, then flush from all cache levels
 *
 * Uses volatile memset to prevent the compiler from optimizing away the zeroing,
 * then evicts every cache line with CLFLUSHOPT (or CLFLUSH). This ensures secret
 * material does not persist in L1/L2/L3 caches after the buffer is freed.
 *
 * @param[in,out] buf  Buffer to wipe (may be NULL -- returns immediately)
 * @param[in]     len  Buffer length in bytes (may be 0 -- returns immediately)
 */
void vos3_cache_wipe(void *buf, size_t len)
{
    if (buf == NULL || len == 0U) {
        return;
    }

    /*
     * Volatile pointer to memset prevents the compiler from dead-store
     * eliminating the zeroing pass. The C standard permits the compiler to
     * remove a memset if the buffer is never read again; volatile defeats this.
     */
    volatile uint8_t *volatile_ptr = (volatile uint8_t *)buf;
    for (size_t i = 0U; i < len; i++) {
        volatile_ptr[i] = 0U;
    }

    /* Compiler barrier -- ensure the zero loop is fully retired before flushing */
    __asm__ volatile("" ::: "memory");

    /* Flush all cache lines covering the buffer */
    cache_flush_lines(buf, len);
}

/**
 * @brief Flush cache lines for a buffer WITHOUT zeroing
 *
 * Useful for forcing dirty data to memory (e.g., before DMA or ivshmem reads)
 * without destroying the buffer contents.
 *
 * @param[in] buf  Buffer to flush (may be NULL -- returns immediately)
 * @param[in] len  Buffer length in bytes
 */
void vos3_cache_flush(const void *buf, size_t len)
{
    cache_flush_lines(buf, len);
}

/* ============================================================================
 * FPU / SIMD GUARD -- Protect AI register state during AES-NI crypto ops
 * ============================================================================ */

/**
 * @brief Begin FPU/SIMD section -- saves current FPU state and clears CR0.TS
 *
 * Must be called before any AES-NI, PCLMULQDQ, or SSE/AVX operations in
 * kernel crypto code. The sequence is:
 *   1. Read CR0 and save it (per-CPU)
 *   2. Clear CR0.TS so SIMD instructions do not trigger #NM (Device Not Available)
 *   3. Save the current FPU/SSE/AVX state via FXSAVE or XSAVE
 *
 * The saved state is restored by vos3_fpu_end(). Nesting is NOT supported.
 *
 * @note Must NOT be called from ISR context (FPU save is not reentrant)
 * @note The .byte encoding is required because the kernel is built with -mno-sse,
 *       which causes the assembler to reject FXSAVE/XSAVE mnemonics.
 *
 * Encoding reference:
 *   FXSAVE (%rax)  = .byte 0x0F, 0xAE, 0x00   (ModR/M = 00 000 000 = [rax])
 *   XSAVE  (%rdi)  = .byte 0x0F, 0xAE, 0x27   (ModR/M = 00 100 111 = [rdi], /4)
 */
void vos3_fpu_begin(void)
{
    uint32_t cpu = get_cpu_id();
    if (cpu >= VOS3_MAX_CPUS) {
        cpu = 0U;   /* Defensive clamp */
    }

    /* Step 1: Save current CR0 and clear TS bit */
    uint64_t cr0 = vos3_read_cr0();
    g_crypto_saved_cr0[cpu] = cr0;
    vos3_write_cr0(cr0 & ~CR0_TS_BIT);

    /* Step 2: Save FPU/SSE state into per-CPU buffer */
    uint8_t *state = g_crypto_fpu_state[cpu];

    if (g_use_xsave) {
        /*
         * XSAVE (%rdi) with EDX:EAX = g_xcr0_mask
         * Encoding: 0x0F, 0xAE, 0x27  (ModR/M = 00 100 111 = [rdi], /4)
         *
         * EAX:EDX hold the component mask, so the buffer address goes through %rdi.
         * XSAVE saves all state components indicated by EDX:EAX & XCR0.
         */
        uint32_t mask_lo = (uint32_t)(g_xcr0_mask & 0xFFFFFFFFULL);
        uint32_t mask_hi = (uint32_t)(g_xcr0_mask >> 32);
        __asm__ volatile(
            ".byte 0x0F, 0xAE, 0x27"              /* xsave (%rdi) */
            :
            : "a"(mask_lo), "d"(mask_hi), "D"((uintptr_t)state)
            : "memory"
        );
    } else {
        /*
         * FXSAVE (%rax) = .byte 0x0F, 0xAE, 0x00
         * Saves x87 + SSE state (512 bytes) to the 16-byte aligned buffer at %rax.
         */
        __asm__ volatile(
            ".byte 0x0F, 0xAE, 0x00"              /* fxsave (%rax) */
            :
            : "a"((uintptr_t)state)
            : "memory"
        );
    }
}

/**
 * @brief End FPU/SIMD section -- restores previous FPU state and re-arms CR0.TS
 *
 * Must be called after every vos3_fpu_begin(). The sequence is:
 *   1. Restore FPU/SSE/AVX state via FXRSTOR or XRSTOR
 *   2. Restore the saved CR0 value (re-sets TS bit if it was set before)
 *
 * Encoding reference:
 *   FXRSTOR (%rax) = .byte 0x0F, 0xAE, 0x08   (ModR/M = 00 001 000 = [rax])
 *   XRSTOR  (%rdi) = .byte 0x0F, 0xAE, 0x2F   (ModR/M = 00 101 111 = [rdi], /5)
 */
void vos3_fpu_end(void)
{
    uint32_t cpu = get_cpu_id();
    if (cpu >= VOS3_MAX_CPUS) {
        cpu = 0U;   /* Defensive clamp */
    }

    /* Step 1: Restore FPU/SSE state from per-CPU buffer */
    uint8_t *state = g_crypto_fpu_state[cpu];

    if (g_use_xsave) {
        /*
         * XRSTOR (%rdi) with EDX:EAX = g_xcr0_mask
         * Encoding: 0x0F, 0xAE, 0x2F  (ModR/M = 00 101 111 = [rdi], /5)
         */
        uint32_t mask_lo = (uint32_t)(g_xcr0_mask & 0xFFFFFFFFULL);
        uint32_t mask_hi = (uint32_t)(g_xcr0_mask >> 32);
        __asm__ volatile(
            ".byte 0x0F, 0xAE, 0x2F"              /* xrstor (%rdi) */
            :
            : "a"(mask_lo), "d"(mask_hi), "D"((uintptr_t)state)
            : "memory"
        );
    } else {
        /*
         * FXRSTOR (%rax) = .byte 0x0F, 0xAE, 0x08
         * Restores x87 + SSE state (512 bytes) from the buffer at %rax.
         */
        __asm__ volatile(
            ".byte 0x0F, 0xAE, 0x08"              /* fxrstor (%rax) */
            :
            : "a"((uintptr_t)state)
            : "memory"
        );
    }

    /* Step 2: Restore CR0 (re-arms TS bit if it was originally set) */
    vos3_write_cr0(g_crypto_saved_cr0[cpu]);
}

/* ============================================================================
 * HKDF-SHA256 -- RFC 5869 HMAC-based Extract-and-Expand KDF
 * ============================================================================ */

/**
 * @brief HKDF-Extract: derive a pseudorandom key from input keying material
 *
 * Implements RFC 5869 Section 2.2:
 *   PRK = HMAC-Hash(salt, IKM)
 *
 * If salt is NULL, a string of HashLen (32) zero bytes is used as salt,
 * per the RFC recommendation.
 *
 * @param[in]  salt      Optional salt value (may be NULL)
 * @param[in]  salt_len  Salt length in bytes (ignored if salt is NULL)
 * @param[in]  ikm       Input Keying Material (must not be NULL)
 * @param[in]  ikm_len   IKM length in bytes
 * @param[out] prk       Output buffer for Pseudorandom Key (32 bytes)
 */
void vos3_hkdf_extract(const uint8_t *salt, size_t salt_len,
                        const uint8_t *ikm, size_t ikm_len,
                        uint8_t prk[32])
{
    /* RFC 5869 Section 2.2: if salt not provided, use HashLen zero bytes */
    uint8_t zero_salt[VOS3_SHA256_DIGEST_SIZE];

    if (salt == NULL) {
        memset(zero_salt, 0, sizeof(zero_salt));
        salt     = zero_salt;
        salt_len = sizeof(zero_salt);
    }

    /*
     * PRK = HMAC-SHA256(salt, IKM)
     *
     * Note: salt is the HMAC key, IKM is the HMAC message.
     * This is intentional per RFC 5869 -- the salt acts as the key.
     */
    vos3_hmac_sha256(salt, salt_len, ikm, ikm_len, prk);
}

/**
 * @brief HKDF-Expand: derive output keying material from a pseudorandom key
 *
 * Implements RFC 5869 Section 2.3:
 *   N = ceil(L / HashLen)
 *   T(0) = empty string
 *   T(i) = HMAC-Hash(PRK, T(i-1) || info || i)   for i = 1..N
 *   OKM  = first L bytes of T(1) || T(2) || ... || T(N)
 *
 * @param[in]  prk       Pseudorandom key (from HKDF-Extract, >= 32 bytes)
 * @param[in]  prk_len   PRK length (must be >= VOS3_SHA256_DIGEST_SIZE)
 * @param[in]  info      Optional context/application-specific info (may be NULL if info_len=0)
 * @param[in]  info_len  Info length in bytes
 * @param[out] okm       Output buffer for keying material
 * @param[in]  okm_len   Desired output length (max 255 * 32 = 8160 bytes)
 * @return 0 on success, -1 on invalid parameters
 */
int vos3_hkdf_expand(const uint8_t *prk, size_t prk_len,
                     const uint8_t *info, size_t info_len,
                     uint8_t *okm, size_t okm_len)
{
    /* Validate parameters per RFC 5869 Section 2.3 */
    if (prk == NULL || okm == NULL) {
        return -1;
    }
    if (prk_len < VOS3_SHA256_DIGEST_SIZE) {
        return -1;
    }

    /* N = ceil(okm_len / 32) -- must not exceed 255 */
    size_t n = (okm_len + VOS3_SHA256_DIGEST_SIZE - 1U) / VOS3_SHA256_DIGEST_SIZE;
    if (n > HKDF_MAX_ITERATIONS) {
        return -1;
    }
    if (okm_len == 0U) {
        return 0;   /* Nothing to derive */
    }

    /*
     * Iterative HMAC expansion:
     *   T(i) = HMAC-SHA256(PRK, T(i-1) || info || i)
     *
     * We use the incremental HMAC API to avoid concatenating T(i-1) || info || i
     * into a single buffer, which would require dynamic allocation.
     */
    uint8_t t_prev[VOS3_SHA256_DIGEST_SIZE];   /* T(i-1), initially empty */
    uint8_t t_curr[VOS3_SHA256_DIGEST_SIZE];   /* T(i) output */
    size_t offset = 0U;

    for (size_t i = 1U; i <= n; i++) {
        vos3_hmac_ctx_t hmac_ctx;
        vos3_hmac_sha256_init(&hmac_ctx, prk, prk_len);

        /* Feed T(i-1) -- empty for i=1 */
        if (i > 1U) {
            vos3_hmac_sha256_update(&hmac_ctx, t_prev, VOS3_SHA256_DIGEST_SIZE);
        }

        /* Feed info */
        if (info != NULL && info_len > 0U) {
            vos3_hmac_sha256_update(&hmac_ctx, info, info_len);
        }

        /* Feed counter byte (1-based, single octet per RFC 5869) */
        uint8_t counter = (uint8_t)i;
        vos3_hmac_sha256_update(&hmac_ctx, &counter, 1U);

        vos3_hmac_sha256_final(&hmac_ctx, t_curr);

        /* Copy to OKM (may be partial on the last block) */
        size_t remaining = okm_len - offset;
        size_t to_copy = (remaining < VOS3_SHA256_DIGEST_SIZE)
                         ? remaining : VOS3_SHA256_DIGEST_SIZE;
        memcpy(okm + offset, t_curr, to_copy);
        offset += to_copy;

        /* T(i) becomes T(i-1) for the next iteration */
        memcpy(t_prev, t_curr, VOS3_SHA256_DIGEST_SIZE);
    }

    /* Wipe intermediate HMAC material from the stack
     * Uses vos3_cache_wipe() instead of memset to prevent dead-store elimination
     * and ensure cache-line eviction of secret HKDF intermediates.
     */
    vos3_cache_wipe(t_prev, sizeof(t_prev));
    vos3_cache_wipe(t_curr, sizeof(t_curr));

    return 0;
}

/* ============================================================================
 * TLS 1.3 HKDF-Expand-Label -- RFC 8446 Section 7.1
 * ============================================================================ */

/**
 * @brief Derive keys per TLS 1.3 key schedule (RFC 8446 Section 7.1)
 *
 * Constructs the HkdfLabel structure:
 *   struct {
 *       uint16 length = out_len;
 *       opaque label<7..255> = "tls13 " + Label;
 *       opaque context<0..255> = Context;
 *   } HkdfLabel;
 *
 * Then calls: HKDF-Expand(Secret, HkdfLabel, out_len)
 *
 * @param[in]  secret       Input secret (32 bytes, from HKDF-Extract)
 * @param[in]  label        ASCII label without the "tls13 " prefix
 * @param[in]  label_len    Label length (must be <= 249 to fit with prefix in 255)
 * @param[in]  context      Handshake context hash (may be NULL for empty context)
 * @param[in]  context_len  Context length (max 255)
 * @param[out] out          Output key material
 * @param[in]  out_len      Desired output length
 * @return 0 on success, -1 on error (bad parameters or label too long)
 */
int vos3_tls13_hkdf_expand_label(const uint8_t secret[32],
                                  const char *label, size_t label_len,
                                  const uint8_t *context, size_t context_len,
                                  uint8_t *out, size_t out_len)
{
    /* Validate inputs */
    if (secret == NULL || out == NULL) {
        return -1;
    }
    if (label == NULL && label_len > 0U) {
        return -1;
    }

    /* "tls13 " prefix (6 bytes) + label must fit in 255 bytes */
    size_t full_label_len = TLS13_LABEL_PREFIX_LEN + label_len;
    if (full_label_len > 255U) {
        return -1;
    }
    if (context_len > 255U) {
        return -1;
    }

    /*
     * Build the HkdfLabel info blob on the stack:
     *   [0..1]  uint16_be  length      = out_len
     *   [2]     uint8      label_len   = 6 + label_len
     *   [3..8]  "tls13 "               (6 bytes)
     *   [9..]   label                  (label_len bytes)
     *   [next]  uint8      context_len
     *   [next.. ] context              (context_len bytes)
     */
    uint8_t info[TLS13_HKDF_LABEL_MAX];
    size_t pos = 0U;

    /* 2-byte length (big-endian, per TLS 1.3 wire format) */
    info[pos++] = (uint8_t)((out_len >> 8) & 0xFFU);
    info[pos++] = (uint8_t)(out_len & 0xFFU);

    /* 1-byte label length (including "tls13 " prefix) */
    info[pos++] = (uint8_t)full_label_len;

    /* "tls13 " prefix */
    memcpy(&info[pos], TLS13_LABEL_PREFIX, TLS13_LABEL_PREFIX_LEN);
    pos += TLS13_LABEL_PREFIX_LEN;

    /* Actual label */
    if (label_len > 0U && label != NULL) {
        memcpy(&info[pos], label, label_len);
        pos += label_len;
    }

    /* 1-byte context length */
    info[pos++] = (uint8_t)context_len;

    /* Context data */
    if (context_len > 0U && context != NULL) {
        memcpy(&info[pos], context, context_len);
        pos += context_len;
    }

    /* Invoke HKDF-Expand with the constructed info blob */
    int ret = vos3_hkdf_expand(secret, VOS3_SHA256_DIGEST_SIZE,
                               info, pos, out, out_len);

    /* Wipe the info blob from stack (may contain partial secrets)
     * Uses vos3_cache_wipe() to prevent dead-store elimination.
     */
    vos3_cache_wipe(info, pos);

    return ret;
}

/* ============================================================================
 * AES-NI / PCLMULQDQ CAPABILITY DETECTION
 * ============================================================================ */

/**
 * @brief Check if the CPU supports AES-NI (Advanced Encryption Standard New Instructions)
 *
 * Queries CPUID leaf 01H and tests ECX bit 25.
 *
 * @return 1 if AES-NI is available, 0 otherwise
 */
int vos3_cpu_has_aesni(void)
{
    uint32_t eax, ebx, ecx, edx;
    vos3_cpuid(VOS3_CPUID_FEATURES, 0, &eax, &ebx, &ecx, &edx);

    return (ecx & VOS3_CPU_FEAT_AES) ? 1 : 0;
}

/**
 * @brief Check if the CPU supports PCLMULQDQ (carry-less multiplication for GCM)
 *
 * Queries CPUID leaf 01H and tests ECX bit 1.
 *
 * @return 1 if PCLMULQDQ is available, 0 otherwise
 */
int vos3_cpu_has_pclmulqdq(void)
{
    uint32_t eax, ebx, ecx, edx;
    vos3_cpuid(VOS3_CPUID_FEATURES, 0, &eax, &ebx, &ecx, &edx);

    return (ecx & VOS3_CPU_FEAT_PCLMULQDQ) ? 1 : 0;
}

/* ============================================================================
 * CONSTANT-TIME MEMORY EQUALITY (declared in sha256.h)
 * ============================================================================ */

/**
 * @brief Constant-time memory equality check (timing-attack resistant).
 *
 * Uses volatile XOR accumulator to prevent the compiler from optimizing
 * the loop into an early-exit branch that would leak comparison timing.
 *
 * @param a   First buffer
 * @param b   Second buffer
 * @param len Number of bytes to compare
 * @return 1 if all bytes are equal, 0 if any byte differs
 */
int vos3_ct_equal(const uint8_t *a, const uint8_t *b, size_t len)
{
    volatile uint8_t diff = 0;
    for (size_t i = 0; i < len; i++)
        diff |= a[i] ^ b[i];
    return diff == 0;
}
