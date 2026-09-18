#include <stdint.h>
#include <stddef.h>

typedef struct context {
    uint64_t r15, r14, r13, r12, rbx, rbp, rip;
} context_t;

extern void vos3_context_switch(context_t **old_ctx, context_t *new_ctx);
extern void vos3_task_entry_trampoline(void);
extern uint64_t vos3_get_rsp(void);
extern void trampoline_entry_probe(void *arg);
extern uint8_t boot_stack_bottom[], boot_stack_top[];

enum { STACK_BOOT, STACK_C, STACK_A, STACK_B, STACK_TRAMPOLINE };
static uint8_t stack_c[16384] __attribute__((aligned(16)));
static uint8_t stack_a[16384] __attribute__((aligned(16)));
static uint8_t stack_b[16384] __attribute__((aligned(16)));
static uint8_t stack_trampoline[16384] __attribute__((aligned(16)));
static context_t *boot_ctx, *c_ctx, *a_ctx, *b_ctx, *trampoline_ctx;
static volatile uint32_t expected_stack;
static volatile uint32_t ack_count;
static volatile uint32_t fail_code;
static volatile uint32_t trampoline_verified;
static const uintptr_t trampoline_arg = UINT64_C(0xC0DEC0DE51A7A11D);

static void outb(uint16_t port, uint8_t value) {
    __asm__ volatile("outb %0,%1" :: "a"(value), "Nd"(port));
}
static uint8_t inb(uint16_t port) {
    uint8_t v; __asm__ volatile("inb %1,%0" : "=a"(v) : "Nd"(port)); return v;
}
static void serial_init(void) {
    outb(0x3F9,0); outb(0x3FB,0x80); outb(0x3F8,1); outb(0x3F9,0);
    outb(0x3FB,3); outb(0x3FA,0xC7); outb(0x3FC,0x0B);
}
static void putc(char c) { while ((inb(0x3FD)&0x20)==0) {} outb(0x3F8,(uint8_t)c); }
static void puts(const char *s) { while (*s) putc(*s++); }
static __attribute__((noreturn)) void finish(int ok) {
    puts(ok ? "PASS context.S real-stack handoff ack\n" : "FAIL context.S real-stack handoff ack\n");
    outb(0xF4, ok ? 0x10 : (uint8_t)(0x20 + fail_code));
    for (;;) __asm__ volatile("hlt");
}
static int within(uintptr_t p, const uint8_t *lo, const uint8_t *hi) {
    return p >= (uintptr_t)lo && p < (uintptr_t)hi;
}
static int on_stack(uint32_t id, uintptr_t rsp) {
    if (id == STACK_BOOT) return within(rsp, boot_stack_bottom, boot_stack_top);
    if (id == STACK_C) return within(rsp, stack_c, stack_c + sizeof stack_c);
    if (id == STACK_A) return within(rsp, stack_a, stack_a + sizeof stack_a);
    if (id == STACK_B) return within(rsp, stack_b, stack_b + sizeof stack_b);
    if (id == STACK_TRAMPOLINE)
        return within(rsp, stack_trampoline,
                      stack_trampoline + sizeof stack_trampoline);
    return 0;
}

/* Called by the candidate context.S after loading incoming RSP and before pops. */
void vos3_sched_switch_stack_ack(void) {
    uintptr_t rsp = (uintptr_t)vos3_get_rsp();
    uint32_t id = expected_stack;
    if (!on_stack(id, rsp) && fail_code == 0) fail_code = 1;
    ack_count++;
}

void vos3_task_exit(int code) {
    uintptr_t rsp = (uintptr_t)vos3_get_rsp();
    if (code != 0 || !trampoline_verified || ack_count != 8 ||
        expected_stack != STACK_TRAMPOLINE ||
        !on_stack(STACK_TRAMPOLINE, rsp) || fail_code) {
        if (!fail_code) fail_code = 90;
        finish(0);
    }
    finish(1);
}

static context_t *make_context(uint8_t *mem, size_t size, void (*entry)(void)) {
    uintptr_t top = ((uintptr_t)(mem + size)) & ~(uintptr_t)15;
    uint64_t *sp = (uint64_t *)top;
    *(--sp) = 0;                 /* impossible return, preserves C-entry ABI */
    *(--sp) = (uint64_t)(uintptr_t)entry;
    *(--sp) = 0xB0B0B0B0B0B0B0B0ULL; /* rbp */
    *(--sp) = 0xBBBBBBBBBBBBBBBBULL; /* rbx */
    *(--sp) = 0x1212121212121212ULL; /* r12 */
    *(--sp) = 0x1313131313131313ULL; /* r13 */
    *(--sp) = 0x1414141414141414ULL; /* r14 */
    *(--sp) = 0x1515151515151515ULL; /* r15 */
    return (context_t *)sp;
}
static context_t *make_trampoline_context(uint8_t *mem, size_t size,
                                          void (*entry)(void *), void *arg) {
    uintptr_t top = ((uintptr_t)(mem + size)) & ~(uintptr_t)15;
    uint64_t *sp = (uint64_t *)top;
    *(--sp) = (uint64_t)(uintptr_t)arg;
    *(--sp) = (uint64_t)(uintptr_t)entry;
    *(--sp) = (uint64_t)(uintptr_t)vos3_task_entry_trampoline;
    *(--sp) = 0; /* rbp */
    *(--sp) = 0; /* rbx */
    *(--sp) = 0; /* r12 */
    *(--sp) = 0; /* r13 */
    *(--sp) = 0; /* r14 */
    *(--sp) = 0; /* r15 */
    return (context_t *)sp;
}
static void check(uint32_t count, uint32_t stack, uint32_t code) {
    uintptr_t rsp = (uintptr_t)vos3_get_rsp();
    if (ack_count != count || expected_stack != stack || !on_stack(stack, rsp) || fail_code) {
        if (!fail_code) fail_code = code;
        finish(0);
    }
}

static void c_entry(void) {
    check(1, STACK_C, 10);
    expected_stack = STACK_BOOT;
    vos3_context_switch(&c_ctx, boot_ctx);
    fail_code = 11; finish(0);
}
static void a_entry(void) {
    check(3, STACK_A, 20);
    expected_stack = STACK_B;
    vos3_context_switch(&a_ctx, b_ctx);
    check(5, STACK_A, 21);
    expected_stack = STACK_B;
    vos3_context_switch(&a_ctx, b_ctx);
    check(7, STACK_A, 22);
    expected_stack = STACK_TRAMPOLINE;
    vos3_context_switch(&a_ctx, trampoline_ctx);
    fail_code = 23; finish(0);
}
static void b_entry(void) {
    check(4, STACK_B, 30);
    expected_stack = STACK_A;
    vos3_context_switch(&b_ctx, a_ctx);
    check(6, STACK_B, 31);
    expected_stack = STACK_A;
    vos3_context_switch(&b_ctx, a_ctx);
    fail_code = 32; finish(0);
}

void trampoline_entry_observed(void *arg, uintptr_t entry_rsp,
                               uint64_t entry_rflags) {
    uintptr_t rsp = (uintptr_t)vos3_get_rsp();
    if ((uintptr_t)arg != trampoline_arg || (entry_rsp & 15U) != 8U ||
        (entry_rflags & (UINT64_C(1) << 9)) == 0U || ack_count != 8 ||
        expected_stack != STACK_TRAMPOLINE ||
        !on_stack(STACK_TRAMPOLINE, entry_rsp) ||
        !on_stack(STACK_TRAMPOLINE, rsp) || fail_code) {
        if (!fail_code) fail_code = 50;
        finish(0);
    }
    trampoline_verified = 1;
}

void kmain(void) {
    serial_init();
    puts("BEGIN context.S real-stack test\n");

    c_ctx = make_context(stack_c, sizeof stack_c, c_entry);
    expected_stack = STACK_C;
    vos3_context_switch(&boot_ctx, c_ctx);
    check(2, STACK_BOOT, 40); /* proves return to a saved ordinary C stack */

    a_ctx = make_context(stack_a, sizeof stack_a, a_entry);
    b_ctx = make_context(stack_b, sizeof stack_b, b_entry);
    trampoline_ctx = make_trampoline_context(
        stack_trampoline, sizeof stack_trampoline, trampoline_entry_probe,
        (void *)trampoline_arg);
    expected_stack = STACK_A;
    vos3_context_switch((context_t **)0, a_ctx); /* scheduler first-switch form */
    fail_code = 41; finish(0);
}
