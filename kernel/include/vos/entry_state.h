#ifndef VOS3_ENTRY_STATE_H
#define VOS3_ENTRY_STATE_H

/* Shared C/assembly ABI, addressed through kernel GS after SWAPGS. */
#define VOS3_ENTRY_KERNEL_RSP 0
#define VOS3_ENTRY_USER_RSP 8
#define VOS3_ENTRY_KERNEL_CR3 16
#define VOS3_ENTRY_USER_CR3 24
#define VOS3_ENTRY_TRAMPOLINE_TOP 32
#define VOS3_ENTRY_SCRATCH_RAX 40
#define VOS3_ENTRY_SCRATCH_RDI 48

#ifndef __ASSEMBLER__
#include <stdint.h>
#include <stddef.h>

typedef struct vos3_entry_state {
    uint64_t kernel_rsp;
    uint64_t user_rsp;
    uint64_t kernel_cr3;
    uint64_t user_cr3;
    uint64_t trampoline_top;
    uint64_t scratch_rax;
    uint64_t scratch_rdi;
} __attribute__((aligned(64))) vos3_entry_state_t;

_Static_assert(offsetof(vos3_entry_state_t, kernel_rsp) == VOS3_ENTRY_KERNEL_RSP,
               "syscall kernel stack ABI");
_Static_assert(offsetof(vos3_entry_state_t, user_rsp) == VOS3_ENTRY_USER_RSP,
               "syscall user stack ABI");

void vos3_entry_set_kernel_stack(uint64_t stack_top);
void vos3_entry_bind_roots(uint64_t full, uint64_t restricted);
uint64_t vos3_entry_trampoline_top(void);
uint64_t vos3_entry_get_kernel_cr3(void);
uint64_t vos3_entry_get_user_cr3(void);
_Static_assert(offsetof(vos3_entry_state_t, kernel_cr3) == VOS3_ENTRY_KERNEL_CR3, "full root ABI");
_Static_assert(offsetof(vos3_entry_state_t, user_cr3) == VOS3_ENTRY_USER_CR3, "user root ABI");
_Static_assert(offsetof(vos3_entry_state_t, trampoline_top) == VOS3_ENTRY_TRAMPOLINE_TOP, "trampoline ABI");
_Static_assert(offsetof(vos3_entry_state_t, scratch_rax) == VOS3_ENTRY_SCRATCH_RAX, "rax scratch ABI");
_Static_assert(offsetof(vos3_entry_state_t, scratch_rdi) == VOS3_ENTRY_SCRATCH_RDI, "rdi scratch ABI");
#endif
#endif
