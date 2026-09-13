/**
 * @file auxv_interp.c
 * @brief Deep-validation ET_DYN interpreter for VOS3 dynamic linker tests
 *
 * Built as ET_DYN shared object (same as fake_interp.c). When loaded by the
 * kernel as an interpreter via PT_INTERP:
 *   1. Checks RSP 16-byte alignment
 *   2. Walks the auxiliary vector on the stack
 *   3. Validates AT_BASE == 0x40000000 (VOS3_INTERP_BASE)
 *   4. Validates AT_ENTRY in [0x400000, 0x40000000)
 *   5. Validates AT_PHDR is a plausible user-space address
 *   6. Validates AT_PAGESZ == 4096
 *   7. Validates AT_RANDOM points to 16 non-zero bytes
 *   8. Writes AT_RANDOM bytes to /tmp/at_random for cross-run uniqueness testing
 *   9. Prints individual [PASS]/[FAIL] for each check
 *  10. Jumps to AT_ENTRY (main program's entry point)
 */

/* Assembly entry — grabs RSP and calls C handler */
__asm__(
    ".text\n"
    ".global _start\n"
    ".type _start, @function\n"
    "_start:\n"
    "    movq %rsp, %rdi\n"    /* arg1: original stack pointer */
    "    movq %rsp, %rsi\n"    /* arg2: rsp value for alignment check */
    "    call _interp_main\n"
    "    ud2\n"
);

/* ============================================================================
 * INLINE SYSCALLS (no libc dependency)
 * ============================================================================ */

#define SYS_WRITE  1
#define SYS_OPEN   2
#define SYS_CLOSE  3
#define SYS_EXIT   60

#define AT_NULL    0
#define AT_PHDR    3
#define AT_PHENT   4
#define AT_PHNUM   5
#define AT_PAGESZ  6
#define AT_BASE    7
#define AT_ENTRY   9
#define AT_UID     11
#define AT_EUID    12
#define AT_GID     13
#define AT_EGID    14
#define AT_RANDOM  25

#define O_WRONLY   1
#define O_CREAT    0100
#define O_TRUNC    01000

typedef unsigned long uint64_t;
typedef long int64_t;
typedef unsigned char uint8_t;

static long syscall3(long num, long a1, long a2, long a3)
{
    long ret;
    __asm__ volatile(
        "syscall"
        : "=a"(ret)
        : "a"(num), "D"(a1), "S"(a2), "d"(a3)
        : "rcx", "r11", "memory"
    );
    return ret;
}

static void write_str(const char *s)
{
    long len = 0;
    while (s[len]) len++;
    syscall3(SYS_WRITE, 1, (long)s, len);
}

static void _exit_now(int code) __attribute__((noreturn));
static void _exit_now(int code)
{
    __asm__ volatile(
        "syscall"
        :
        : "a"((long)SYS_EXIT), "D"((long)code)
        : "rcx", "r11", "memory"
    );
    __builtin_unreachable();
}

static void write_hex(uint64_t val)
{
    char buf[19]; /* "0x" + 16 hex digits + NUL */
    buf[0] = '0';
    buf[1] = 'x';
    for (int i = 15; i >= 0; i--) {
        int nibble = (val >> (i * 4)) & 0xf;
        buf[2 + (15 - i)] = (nibble < 10) ? ('0' + nibble) : ('a' + nibble - 10);
    }
    buf[18] = '\0';
    write_str(buf);
}

/* ============================================================================
 * INTERPRETER MAIN
 * ============================================================================ */

__attribute__((visibility("hidden")))
void _interp_main(unsigned long *sp, unsigned long rsp_val)
{
    unsigned long *original_sp = sp;
    int failures = 0;

    /* --- Test 1: RSP 16-byte alignment --- */
    if ((rsp_val & 0xFULL) == 0) {
        write_str("[PASS] test_dynlink: rsp_aligned\n");
    } else {
        write_str("[FAIL] test_dynlink: rsp_aligned (rsp=");
        write_hex(rsp_val);
        write_str(")\n");
        failures++;
    }

    /* Parse argc */
    unsigned long argc = *sp++;

    /* Skip argv */
    sp += argc + 1; /* argv entries + NULL terminator */

    /* Skip envp */
    while (*sp != 0) sp++;
    sp++; /* past NULL terminator */

    /* Parse auxiliary vector */
    uint64_t at_entry = 0, at_base = 0, at_phdr = 0;
    uint64_t at_pagesz = 0, at_random = 0;
    uint64_t at_phent = 0, at_phnum = 0;
    int found_entry = 0, found_base = 0, found_phdr = 0;
    int found_pagesz = 0, found_random = 0;
    int found_phent = 0, found_phnum = 0;

    while (sp[0] != AT_NULL) {
        switch (sp[0]) {
            case AT_ENTRY:   at_entry   = sp[1]; found_entry   = 1; break;
            case AT_BASE:    at_base    = sp[1]; found_base    = 1; break;
            case AT_PHDR:    at_phdr    = sp[1]; found_phdr    = 1; break;
            case AT_PHENT:   at_phent   = sp[1]; found_phent   = 1; break;
            case AT_PHNUM:   at_phnum   = sp[1]; found_phnum   = 1; break;
            case AT_PAGESZ:  at_pagesz  = sp[1]; found_pagesz  = 1; break;
            case AT_RANDOM:  at_random  = sp[1]; found_random  = 1; break;
        }
        sp += 2;
    }

    /* --- Test 2: AT_BASE == 0x40000000 (VOS3_INTERP_BASE) --- */
    if (found_base && at_base == 0x40000000ULL) {
        write_str("[PASS] test_dynlink: at_base_correct\n");
    } else {
        write_str("[FAIL] test_dynlink: at_base_correct (");
        if (!found_base) write_str("missing");
        else { write_str("got "); write_hex(at_base); }
        write_str(")\n");
        failures++;
    }

    /* --- Test 3: AT_ENTRY in [0x400000, 0x40000000) --- */
    if (found_entry && at_entry >= 0x400000ULL && at_entry < 0x40000000ULL) {
        write_str("[PASS] test_dynlink: at_entry_valid\n");
    } else {
        write_str("[FAIL] test_dynlink: at_entry_valid (");
        if (!found_entry) write_str("missing");
        else { write_str("got "); write_hex(at_entry); }
        write_str(")\n");
        failures++;
    }

    /* --- Test 4: AT_PHDR is a plausible user-space address --- */
    if (found_phdr && at_phdr >= 0x400000ULL && at_phdr < 0x7FFFFF000000ULL) {
        write_str("[PASS] test_dynlink: at_phdr_valid\n");
    } else {
        write_str("[FAIL] test_dynlink: at_phdr_valid (");
        if (!found_phdr) write_str("missing");
        else { write_str("got "); write_hex(at_phdr); }
        write_str(")\n");
        failures++;
    }

    /* --- Test 5: AT_PHENT present --- */
    if (found_phent && at_phent > 0) {
        write_str("[PASS] test_dynlink: at_phent_present\n");
    } else {
        write_str("[FAIL] test_dynlink: at_phent_present\n");
        failures++;
    }

    /* --- Test 6: AT_PHNUM present --- */
    if (found_phnum && at_phnum > 0) {
        write_str("[PASS] test_dynlink: at_phnum_present\n");
    } else {
        write_str("[FAIL] test_dynlink: at_phnum_present\n");
        failures++;
    }

    /* --- Test 7: AT_PAGESZ == 4096 --- */
    if (found_pagesz && at_pagesz == 4096ULL) {
        write_str("[PASS] test_dynlink: at_pagesz_correct\n");
    } else {
        write_str("[FAIL] test_dynlink: at_pagesz_correct\n");
        failures++;
    }

    /* --- Test 8: AT_RANDOM non-zero 16 bytes --- */
    if (found_random && at_random != 0) {
        uint8_t *rnd = (uint8_t *)at_random;
        int all_zero = 1;
        for (int i = 0; i < 16; i++) {
            if (rnd[i] != 0) { all_zero = 0; break; }
        }
        if (!all_zero) {
            write_str("[PASS] test_dynlink: at_random_nonzero\n");
        } else {
            write_str("[FAIL] test_dynlink: at_random_nonzero (all bytes zero)\n");
            failures++;
        }

        /* Write AT_RANDOM bytes to /tmp/at_random for cross-run uniqueness */
        long fd = syscall3(SYS_OPEN, (long)"/tmp/at_random",
                           O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd >= 0) {
            syscall3(SYS_WRITE, fd, (long)rnd, 16);
            /* close */
            __asm__ volatile(
                "syscall"
                :
                : "a"((long)SYS_CLOSE), "D"(fd)
                : "rcx", "r11", "memory"
            );
        }
    } else {
        write_str("[FAIL] test_dynlink: at_random_nonzero (missing or null)\n");
        failures++;
    }

    if (failures > 0) {
        _exit_now(failures);
    }

    /* Jump to main program's entry point with original stack frame */
    __asm__ volatile(
        "movq %0, %%rsp\n"
        "jmpq *%1\n"
        :
        : "r"(original_sp), "r"(at_entry)
    );
    __builtin_unreachable();
}
