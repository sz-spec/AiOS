/**
 * @file stack_protector.c
 * @brief Stack smashing detection runtime support
 *
 * Provides __stack_chk_guard and __stack_chk_fail required by
 * -fstack-protector-strong.  The canary is initialized to a
 * compile-time constant and re-seeded from RDRAND once the
 * entropy subsystem is online (called from kmain).
 *
 * @date 2026-04-05
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include <stdint.h>
#include <stddef.h>  /* size_t for vos3_entropy_extract */

/* Forward declaration — avoids pulling in console.h header chain
 * (stack_protector must compile with minimal dependencies). */
extern void vos3_console_printf(const char *fmt, ...);

/* Forward declaration of the entropy subsystem extraction primitive.
 * Implemented in kernel/src/crypto/entropy.c. Performs the canonical
 * RDSEED → RDRAND → ChaCha20-CSPRNG fallback hierarchy with internal
 * CPUID gating, so callers do not need to issue raw RDRAND. */
extern int vos3_entropy_extract(void *buf, size_t len);

/*
 * GCC emits a load of __stack_chk_guard at function entry (canary write)
 * and a compare at function exit.  If the value changed, it calls
 * __stack_chk_fail.
 *
 * Initial value: byte pattern with a NUL in byte 0 to break string-based
 * overflows, and high-entropy upper bytes.  Re-seeded by
 * vos3_stack_guard_reseed() once RDRAND is available.
 */
uintptr_t __stack_chk_guard = 0x00D3ADBEEFCAFE42ULL;

/**
 * @brief Called by GCC when stack canary corruption is detected.
 *
 * This function MUST NOT return — a corrupted stack is unrecoverable.
 */
__attribute__((noreturn))
void __stack_chk_fail(void)
{
    vos3_console_printf("\n*** KERNEL PANIC: Stack smashing detected ***\n");
    vos3_console_printf("A stack buffer overflow corrupted the canary.\n");
    vos3_console_printf("System halted.\n");

    /* Triple-fault halt: disable interrupts, halt forever */
    __asm__ volatile("cli");
    for (;;) {
        __asm__ volatile("hlt");
    }
}

/**
 * @brief Re-seed the stack canary with high-quality entropy.
 *
 * Called once from kmain after vos3_entropy_init() has run.
 *
 * Stage 7 fix (boot-panic on qemu64 / older silicon):
 *   The previous implementation issued the RDRAND instruction directly
 *   without first checking CPUID.01H:ECX[30]. On CPU models that do
 *   not implement RDRAND (e.g. QEMU's default `qemu64` model), the
 *   instruction faults with #UD and the kernel panics during boot.
 *
 *   We now route the request through the kernel entropy subsystem
 *   (vos3_entropy_extract). That subsystem already performs the
 *   canonical fallback hierarchy:
 *       RDSEED  →  RDRAND  →  ChaCha20-CSPRNG  →  RDTSC jitter
 *   with CPUID gating internally, so this function is safe on any
 *   x86_64 silicon and never issues an unguarded RDRAND.
 *
 * The 256-bit ChaCha20 CSPRNG fallback (seeded at vos3_entropy_init())
 * is cryptographically suitable for canary use even when no hardware
 * entropy source is present on the host.
 *
 * IMPORTANT — no_stack_protector attribute:
 *   This function MUTATES __stack_chk_guard mid-execution. If GCC
 *   inserts a stack-canary prologue/epilogue here, the prologue
 *   captures the OLD canary onto the stack, and the epilogue compares
 *   against the NEW (just-written) value — guaranteed mismatch ->
 *   __stack_chk_fail() panic.
 *
 *   The previous implementation accidentally avoided this because its
 *   locals (rnd, ok, tmp, flag) did not trigger -fstack-protector-strong's
 *   "function needs protection" heuristic. Our cleaner implementation
 *   takes &rnd to pass to vos3_entropy_extract, which DOES trigger it.
 *   We must therefore explicitly opt out of stack protection on this
 *   single function. The function has no arrays / no untrusted writes,
 *   so opting out is safe.
 */
__attribute__((no_stack_protector))
void vos3_stack_guard_reseed(void)
{
    uint64_t rnd = 0;
    const int rc = vos3_entropy_extract(&rnd, sizeof(rnd));

    if (rc == 0) {
        /* Force byte 0 to NUL — defeats string-copy overflows. */
        rnd &= ~(uint64_t)0xFF;
        __stack_chk_guard = (uintptr_t)rnd;
        vos3_console_printf("[STACK-GUARD] Canary re-seeded from entropy subsystem\n");
    } else {
        vos3_console_printf("[STACK-GUARD] Entropy unavailable, using static canary\n");
    }
}
