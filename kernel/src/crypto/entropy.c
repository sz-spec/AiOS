/**
 * @file entropy.c
 * @brief VOS3 Entropy Subsystem — RDRAND/RDSEED + ChaCha20 CSPRNG
 *
 * @details Implements a cryptographically secure random number generator:
 *          - CPUID detection of RDRAND (CPUID.01H:ECX[30]) and RDSEED (CPUID.07H:EBX[18])
 *          - ChaCha20 block function (RFC 7539) as the core CSPRNG
 *          - Seed hierarchy: RDSEED (primary) -> RDRAND (secondary) -> RDTSC+jitter (fallback)
 *          - Forward secrecy: re-key after every extraction
 *          - Fork safety: PID + RDTSC nonce mixing before each output
 *          - Auto-reseed after 1MB of output
 *
 * @version 1.0.0
 * @date 2026-03-19
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/entropy.h"
#include "../../include/vos/console.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * HARDWARE CAPABILITY FLAGS
 * ============================================================================ */

static int g_has_rdrand = 0;
static int g_has_rdseed = 0;
static int g_entropy_initialized = 0;

/* ============================================================================
 * CPUID DETECTION
 * ============================================================================ */

static void cpuid(uint32_t leaf, uint32_t subleaf,
                  uint32_t* eax, uint32_t* ebx, uint32_t* ecx, uint32_t* edx)
{
    __asm__ volatile(
        "cpuid"
        : "=a"(*eax), "=b"(*ebx), "=c"(*ecx), "=d"(*edx)
        : "a"(leaf), "c"(subleaf)
    );
}

static void detect_hw_rng(void)
{
    uint32_t eax, ebx, ecx, edx;

    /* CPUID.01H:ECX[30] = RDRAND */
    cpuid(0x01, 0, &eax, &ebx, &ecx, &edx);
    g_has_rdrand = (ecx >> 30) & 1;

    /* CPUID.07H:EBX[18] = RDSEED */
    cpuid(0x07, 0, &eax, &ebx, &ecx, &edx);
    g_has_rdseed = (ebx >> 18) & 1;
}

/* ============================================================================
 * HARDWARE RNG PRIMITIVES
 * ============================================================================ */

/**
 * @brief Read 64-bit random value from RDRAND
 * @param[out] out  Pointer to store result
 * @return 1 on success, 0 on failure (after 10 retries per Intel SDM)
 */
static int rdrand64(uint64_t* out)
{
    unsigned char ok;
    for (int i = 0; i < 10; i++) {
        __asm__ volatile("rdrand %0; setc %1" : "=r"(*out), "=qm"(ok));
        if (ok) return 1;
        __asm__ volatile("pause");
    }
    return 0;
}

/**
 * @brief Read 64-bit random value from RDSEED
 * @param[out] out  Pointer to store result
 * @return 1 on success, 0 on failure (after 10 retries)
 */
static int rdseed64(uint64_t* out)
{
    unsigned char ok;
    for (int i = 0; i < 10; i++) {
        __asm__ volatile("rdseed %0; setc %1" : "=r"(*out), "=qm"(ok));
        if (ok) return 1;
        __asm__ volatile("pause");
    }
    return 0;
}

/**
 * @brief Read RDTSC timestamp counter
 */
static uint64_t read_tsc(void)
{
    uint32_t lo, hi;
    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

/* ============================================================================
 * ChaCha20 BLOCK FUNCTION (RFC 7539)
 * ============================================================================ */

#define ROTL32(v, n) (((v) << (n)) | ((v) >> (32 - (n))))

#define QR(a, b, c, d) do {       \
    (a) += (b); (d) ^= (a); (d) = ROTL32((d), 16); \
    (c) += (d); (b) ^= (c); (b) = ROTL32((b), 12); \
    (a) += (b); (d) ^= (a); (d) = ROTL32((d),  8); \
    (c) += (d); (b) ^= (c); (b) = ROTL32((b),  7); \
} while (0)

/**
 * @brief ChaCha20 block function — produces 64 bytes of keystream
 *
 * @param[in]  key      256-bit key (8 x uint32_t)
 * @param[in]  counter  Block counter
 * @param[in]  nonce    96-bit nonce (3 x uint32_t)
 * @param[out] out      64-byte output buffer (16 x uint32_t)
 */
static void chacha20_block(const uint32_t key[8], uint32_t counter,
                           const uint32_t nonce[3], uint32_t out[16])
{
    /* "expand 32-byte k" */
    uint32_t state[16];
    state[ 0] = 0x61707865U;
    state[ 1] = 0x3320646EU;
    state[ 2] = 0x79622D32U;
    state[ 3] = 0x6B206574U;
    state[ 4] = key[0];
    state[ 5] = key[1];
    state[ 6] = key[2];
    state[ 7] = key[3];
    state[ 8] = key[4];
    state[ 9] = key[5];
    state[10] = key[6];
    state[11] = key[7];
    state[12] = counter;
    state[13] = nonce[0];
    state[14] = nonce[1];
    state[15] = nonce[2];

    /* Copy initial state for final addition */
    uint32_t working[16];
    for (int i = 0; i < 16; i++) {
        working[i] = state[i];
    }

    /* 20 rounds (10 double-rounds) */
    for (int i = 0; i < 10; i++) {
        /* Column rounds */
        QR(working[ 0], working[ 4], working[ 8], working[12]);
        QR(working[ 1], working[ 5], working[ 9], working[13]);
        QR(working[ 2], working[ 6], working[10], working[14]);
        QR(working[ 3], working[ 7], working[11], working[15]);
        /* Diagonal rounds */
        QR(working[ 0], working[ 5], working[10], working[15]);
        QR(working[ 1], working[ 6], working[11], working[12]);
        QR(working[ 2], working[ 7], working[ 8], working[13]);
        QR(working[ 3], working[ 4], working[ 9], working[14]);
    }

    /* Add initial state back (modular addition) */
    for (int i = 0; i < 16; i++) {
        out[i] = working[i] + state[i];
    }
}

/* ============================================================================
 * ENTROPY POOL
 * ============================================================================ */

/** @brief Auto-reseed threshold: 1 MB of output */
#define RESEED_THRESHOLD (1024U * 1024U)

/** @brief ChaCha20 output block size in bytes */
#define CHACHA20_BLOCK_SIZE 64U

/** @brief ChaCha20 key size in bytes */
#define CHACHA20_KEY_SIZE   32U

typedef struct {
    uint32_t        key[8];       /**< 256-bit ChaCha20 key */
    uint32_t        nonce[3];     /**< 96-bit nonce */
    uint32_t        counter;      /**< Block counter */
    uint64_t        bytes_since_reseed; /**< Bytes generated since last reseed */
    vos3_spinlock_t lock;         /**< Pool lock */
} entropy_pool_t;

static entropy_pool_t g_pool = {
    .key     = {0},
    .nonce   = {0},
    .counter = 0,
    .bytes_since_reseed = 0,
    .lock    = VOS3_SPINLOCK_INIT,
};

/* ============================================================================
 * SEED COLLECTION
 * ============================================================================ */

/**
 * @brief Collect entropy from best available source
 *
 * Seed hierarchy: RDSEED -> RDRAND -> RDTSC+jitter
 *
 * @param[out] buf   Buffer for raw entropy (array of uint64_t)
 * @param[in]  count Number of uint64_t values to collect
 */
static void collect_entropy(uint64_t* buf, size_t count)
{
    for (size_t i = 0; i < count; i++) {
        uint64_t val = 0;
        int got = 0;

        /* Try RDSEED first (conditioned entropy from Intel DRNG) */
        if (g_has_rdseed) {
            got = rdseed64(&val);
        }

        /* Fall back to RDRAND (CSPRNG output from Intel DRNG) */
        if (!got && g_has_rdrand) {
            got = rdrand64(&val);
        }

        /* Ultimate fallback: RDTSC + jitter mixing */
        if (!got) {
            uint64_t tsc1 = read_tsc();
            /* Busy-loop for jitter — the number of cycles will vary */
            for (volatile int j = 0; j < 100; j++) {
                /* spin */
            }
            uint64_t tsc2 = read_tsc();
            val = tsc1 ^ (tsc2 * 6364136223846793005ULL);
            val ^= (tsc2 - tsc1) << 32;
        }

        buf[i] = val;
    }
}

/* ============================================================================
 * INIT / RESEED
 * ============================================================================ */

void vos3_entropy_init(void)
{
    detect_hw_rng();

    VOS3_INFO("[ENTROPY] RDRAND: %s, RDSEED: %s",
              g_has_rdrand ? "available" : "not available",
              g_has_rdseed ? "available" : "not available");

    /* Collect initial seed: 4 x RDSEED + 4 x RDRAND = 64 bytes = 256-bit key + 192-bit nonce + extra */
    uint64_t seed[8];
    collect_entropy(seed, 8);

    /* Load key from first 4 seed values (256 bits) */
    for (int i = 0; i < 4; i++) {
        g_pool.key[i * 2]     = (uint32_t)(seed[i] & 0xFFFFFFFFU);
        g_pool.key[i * 2 + 1] = (uint32_t)(seed[i] >> 32);
    }

    /* Load nonce from next seed values (96 bits) */
    g_pool.nonce[0] = (uint32_t)(seed[4] & 0xFFFFFFFFU);
    g_pool.nonce[1] = (uint32_t)(seed[4] >> 32);
    g_pool.nonce[2] = (uint32_t)(seed[5] & 0xFFFFFFFFU);

    /* Mix remaining entropy into counter */
    g_pool.counter = (uint32_t)(seed[6] ^ seed[7]);
    g_pool.bytes_since_reseed = 0;

    g_entropy_initialized = 1;

    VOS3_INFO("[ENTROPY] CSPRNG initialized (ChaCha20, 256-bit key)");
}

void vos3_entropy_reseed(void)
{
    uint64_t fresh[4];
    collect_entropy(fresh, 4);

    /* XOR fresh entropy into existing key (preserves forward secrecy) */
    for (int i = 0; i < 4; i++) {
        g_pool.key[i * 2]     ^= (uint32_t)(fresh[i] & 0xFFFFFFFFU);
        g_pool.key[i * 2 + 1] ^= (uint32_t)(fresh[i] >> 32);
    }

    /* Reset counter and bytes tracker */
    g_pool.counter = 0;
    g_pool.bytes_since_reseed = 0;
}

/* ============================================================================
 * EXTRACTION
 * ============================================================================ */

int vos3_entropy_extract(void* buf, size_t len)
{
    if (buf == NULL || len == 0) {
        return -1;
    }

    if (!g_entropy_initialized) {
        /* Fallback: fill with RDTSC jitter if init hasn't run yet */
        uint8_t* p = (uint8_t*)buf;
        for (size_t i = 0; i < len; i++) {
            uint64_t tsc = read_tsc();
            p[i] = (uint8_t)(tsc ^ (tsc >> 8) ^ (tsc >> 16));
        }
        return 0;
    }

    /* Disable interrupts while holding pool lock to prevent deadlock */
    uint64_t flags;
    __asm__ volatile("pushfq; pop %0; cli" : "=r"(flags));
    vos3_spinlock_acquire(&g_pool.lock);

    /* A1.6: Fork-safety — mix PID + RDTSC into nonce before generating output */
    {
        vos3_task_t* current = vos3_sched_current();
        uint32_t pid_mix = 0;
        if (current != NULL) {
            pid_mix = current->pid;
        }
        uint64_t tsc = read_tsc();
        g_pool.nonce[0] ^= pid_mix;
        g_pool.nonce[1] ^= (uint32_t)(tsc & 0xFFFFFFFFU);
        g_pool.nonce[2] ^= (uint32_t)(tsc >> 32);
    }

    /* Auto-reseed after threshold */
    if (g_pool.bytes_since_reseed >= RESEED_THRESHOLD) {
        vos3_entropy_reseed();
    }

    /* Generate ChaCha20 keystream and copy to output buffer */
    uint8_t* out = (uint8_t*)buf;
    size_t remaining = len;

    while (remaining > 0) {
        /* Generate one ChaCha20 block (64 bytes) */
        uint32_t block[16];
        chacha20_block(g_pool.key, g_pool.counter, g_pool.nonce, block);
        g_pool.counter++;

        size_t to_copy = remaining;
        if (to_copy > CHACHA20_BLOCK_SIZE) {
            to_copy = CHACHA20_BLOCK_SIZE;
        }

        memcpy(out, block, to_copy);
        out += to_copy;
        remaining -= to_copy;
    }

    g_pool.bytes_since_reseed += len;

    /* A1.5: Forward secrecy — re-key after every extraction.
     * Generate an extra 32 bytes and overwrite the key, then increment nonce. */
    {
        uint32_t rekey_block[16];
        chacha20_block(g_pool.key, g_pool.counter, g_pool.nonce, rekey_block);
        g_pool.counter++;

        /* Overwrite key with first 8 words (32 bytes) of rekey block */
        for (int i = 0; i < 8; i++) {
            g_pool.key[i] = rekey_block[i];
        }

        /* Increment nonce to ensure we never reuse key+nonce pair */
        g_pool.nonce[0]++;
        if (g_pool.nonce[0] == 0) {
            g_pool.nonce[1]++;
            if (g_pool.nonce[1] == 0) {
                g_pool.nonce[2]++;
            }
        }

        /* Wipe rekey block from stack */
        memset(rekey_block, 0, sizeof(rekey_block));
    }

    vos3_spinlock_release(&g_pool.lock);
    __asm__ volatile("push %0; popfq" :: "r"(flags) : "memory", "cc");

    return 0;
}

uint32_t vos3_entropy_get_u32(void)
{
    uint32_t val = 0;
    vos3_entropy_extract(&val, sizeof(val));
    return val;
}

uint64_t vos3_entropy_get_u64(void)
{
    uint64_t val = 0;
    vos3_entropy_extract(&val, sizeof(val));
    return val;
}
