/**
 * @file kaslr.c
 * @brief Kernel Address Space Layout Randomization enhancements
 *
 * Randomizes heap, stack, and MMIO virtual base addresses at boot
 * using hardware entropy (RDTSC + RDSEED).
 */
#include <stdint.h>
#include <stddef.h>
#include <vos/console.h>

/* Entropy sources */
static inline uint64_t rdtsc_entropy(void)
{
    uint32_t lo, hi;
    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

static inline int rdseed_available(void)
{
    uint32_t eax, ebx, ecx, edx;
    __asm__ volatile("cpuid" : "=a"(eax), "=b"(ebx), "=c"(ecx), "=d"(edx)
                     : "a"(7), "c"(0));
    return (ebx >> 18) & 1;  /* RDSEED: CPUID.07H:EBX[18] */
}

static inline uint64_t rdseed_entropy(void)
{
    uint64_t val;
    uint8_t ok;
    for (int i = 0; i < 10; i++) {
        __asm__ volatile("rdseed %0; setc %1" : "=r"(val), "=qm"(ok));
        if (ok) return val;
    }
    return 0;  /* Fallback: no entropy from RDSEED */
}

/* Exported offsets (applied during boot) */
static int64_t g_kaslr_stack_offset;
static int64_t g_kaslr_heap_offset;

int64_t vos3_kaslr_stack_offset(void) { return g_kaslr_stack_offset; }
int64_t vos3_kaslr_heap_offset(void)  { return g_kaslr_heap_offset; }

/**
 * @brief Initialize KASLR offsets using hardware entropy
 *
 * Called early in boot before heap/stack subsystems are initialized.
 * Randomizes:
 *   - Stack base: +-256MB, 2MB aligned
 *   - Heap base:  +-128MB, 4KB aligned
 */
void vos3_kaslr_init(void)
{
    uint64_t entropy = rdtsc_entropy();

    if (rdseed_available()) {
        entropy ^= rdseed_entropy();
    }

    /* Stack offset: +-256MB, 2MB aligned (bits [27:21] from entropy) */
    uint64_t stack_raw = (entropy >> 0) & 0xFFU;  /* 0-255 */
    g_kaslr_stack_offset = (int64_t)(stack_raw * 0x200000ULL);  /* * 2MB */
    if (entropy & (1ULL << 32))
        g_kaslr_stack_offset = -g_kaslr_stack_offset;

    /* Heap offset: +-128MB, 4KB aligned (bits [47:36] from entropy) */
    uint64_t heap_raw = (entropy >> 36) & 0x7FFFU;  /* 0-32767 */
    g_kaslr_heap_offset = (int64_t)(heap_raw * 0x1000ULL);  /* * 4KB */
    if (heap_raw > 0x3FFFU)
        g_kaslr_heap_offset = -(int64_t)((0x7FFFU - heap_raw) * 0x1000ULL);

    /* Clamp to safe ranges */
    if (g_kaslr_stack_offset > 0x10000000LL)       /* +256MB */
        g_kaslr_stack_offset = 0x10000000LL;
    if (g_kaslr_stack_offset < -0x10000000LL)       /* -256MB */
        g_kaslr_stack_offset = -0x10000000LL;
    if (g_kaslr_heap_offset > 0x8000000LL)          /* +128MB */
        g_kaslr_heap_offset = 0x8000000LL;
    if (g_kaslr_heap_offset < -0x8000000LL)          /* -128MB */
        g_kaslr_heap_offset = -0x8000000LL;

    vos3_console_printf("[KASLR] Stack offset: %s0x%llx (2MB aligned)\n",
                        g_kaslr_stack_offset >= 0 ? "+" : "-",
                        (unsigned long long)(g_kaslr_stack_offset >= 0 ?
                         g_kaslr_stack_offset : -g_kaslr_stack_offset));
    vos3_console_printf("[KASLR] Heap offset:  %s0x%llx (4KB aligned)\n",
                        g_kaslr_heap_offset >= 0 ? "+" : "-",
                        (unsigned long long)(g_kaslr_heap_offset >= 0 ?
                         g_kaslr_heap_offset : -g_kaslr_heap_offset));
}
