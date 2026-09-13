/**
 * @file context.S
 * @brief VOS3 Context Switching Assembly
 *
 * @details Low-level context switch implementation for x86_64.
 *          Saves and restores callee-saved registers per System V AMD64 ABI.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

.section .text
.code64

/* ============================================================================
 * CONTEXT SWITCH
 * ============================================================================
 *
 * void vos3_context_switch(vos3_context_t** old_ctx, vos3_context_t* new_ctx);
 *
 * Parameters:
 *   rdi = pointer to old context pointer (vos3_context_t**)
 *   rsi = new context pointer (vos3_context_t*)
 *
 * The context structure (vos3_context_t):
 *   offset 0x00: r15
 *   offset 0x08: r14
 *   offset 0x10: r13
 *   offset 0x18: r12
 *   offset 0x20: rbx
 *   offset 0x28: rbp
 *   offset 0x30: rip (return address)
 *
 * Total size: 56 bytes (7 x 8)
 */
.global vos3_context_switch
.type vos3_context_switch, @function
vos3_context_switch:
    /*
     * Save current context (if old_ctx is not NULL)
     * We push callee-saved registers onto the stack, then save RSP
     */

    /* Check if old_ctx pointer is NULL */
    testq %rdi, %rdi
    jz .Lload_new_context

    /* Push callee-saved registers (in reverse order of struct) */
    pushq %rbp
    pushq %rbx
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15

    /* Save current stack pointer to old context pointer */
    /* *old_ctx = rsp */
    movq %rsp, (%rdi)

.Lload_new_context:
    /* Load new context */
    /* rsp = new_ctx */
    movq %rsi, %rsp

    /* Pop callee-saved registers */
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbx
    popq %rbp

    /* Return to new task (RIP was pushed by caller's CALL instruction) */
    ret

.size vos3_context_switch, . - vos3_context_switch


/* ============================================================================
 * INITIAL TASK ENTRY
 * ============================================================================
 *
 * void vos3_task_entry_trampoline(void);
 *
 * This is the initial entry point for new tasks. The stack is set up by
 * vos3_task_setup_stack() to look like:
 *
 *   [rsp+0x38] -> arg (void*)
 *   [rsp+0x30] -> entry (function pointer)
 *   [rsp+0x28] -> vos3_task_exit_wrapper (return address for entry)
 *   [rsp+0x00-0x28] -> zeroed callee-saved regs (r15, r14, r13, r12, rbx, rbp)
 *
 * After popping the callee-saved registers, rsp points to return address.
 * We pop it, get entry and arg, then call entry(arg).
 */
.global vos3_task_entry_trampoline
.type vos3_task_entry_trampoline, @function
vos3_task_entry_trampoline:
    /* At this point, stack contains:
     *   [rsp]    -> entry function pointer
     *   [rsp+8]  -> arg
     *
     * rbp was just popped, so we're ready to set up the call
     */

    /* Pop entry function pointer into rax */
    popq %rax

    /* Pop argument into rdi (first argument register) */
    popq %rdi

    /* Align stack to 16 bytes before call (required by ABI) */
    andq $~0xF, %rsp
    subq $8, %rsp

    /* Call the entry function: entry(arg) */
    call *%rax

    /* If entry function returns, call vos3_task_exit(0) */
    xorl %edi, %edi         /* exit_code = 0 */
    call vos3_task_exit

    /* Should never reach here (vos3_task_exit is noreturn) */
    ud2

.size vos3_task_entry_trampoline, . - vos3_task_entry_trampoline


/* ============================================================================
 * IDLE TASK LOOP
 * ============================================================================
 *
 * void vos3_idle_loop(void* arg);
 *
 * Simple idle loop that halts the CPU until an interrupt occurs.
 */
.global vos3_idle_loop
.type vos3_idle_loop, @function
vos3_idle_loop:
1:
    sti                     /* Enable interrupts */
    hlt                     /* Halt until interrupt */
    jmp 1b                  /* Loop forever */

.size vos3_idle_loop, . - vos3_idle_loop


/* ============================================================================
 * GET STACK POINTER
 * ============================================================================
 *
 * uint64_t vos3_get_rsp(void);
 */
.global vos3_get_rsp
.type vos3_get_rsp, @function
vos3_get_rsp:
    movq %rsp, %rax
    ret

.size vos3_get_rsp, . - vos3_get_rsp


/* ============================================================================
 * GET FRAME POINTER
 * ============================================================================
 *
 * uint64_t vos3_get_rbp(void);
 */
.global vos3_get_rbp
.type vos3_get_rbp, @function
vos3_get_rbp:
    movq %rbp, %rax
    ret

.size vos3_get_rbp, . - vos3_get_rbp


#ifdef VOS3_HW_XSAVE
/* ============================================================================
 * XSAVE64 / XRSTOR64 — raw extended-state save and restore
 * ============================================================================
 *
 * void vos3_xsave64(void *area, uint32_t mask_lo, uint32_t mask_hi)
 *
 *   Saves processor extended state components (XMM, YMM, ZMM, AMX …)
 *   whose bit in the 64-bit RFBM (EDX:EAX) is set to *area*.
 *   *area* MUST be 64-byte aligned (caller allocates via
 *   vos3_xsave_get_area_size()).
 *
 *   System V AMD64 ABI:
 *     rdi = area  (64-byte aligned destination)
 *     rsi = mask_lo  → loaded into EAX (RFBM[31:0])
 *     rdx = mask_hi  → already in EDX  (RFBM[63:32])
 *
 *   Intel SDM Vol. 1 §13.6: XSAVE64 saves only the components whose
 *   bit is set in both RFBM and XCR0; other components in *area* are
 *   left unchanged.
 */
.global vos3_xsave64
.type vos3_xsave64, @function
vos3_xsave64:
    movl    %esi, %eax          /* mask_lo → EAX (RFBM low half)  */
    /* rdx already holds mask_hi (RFBM high half) — pass-through   */
    xsave64 (%rdi)              /* save to [rdi], 64-byte aligned  */
    ret
.size vos3_xsave64, . - vos3_xsave64

/* ============================================================================
 * XRSTOR64
 * ============================================================================
 *
 * void vos3_xrstor64(const void *area, uint32_t mask_lo, uint32_t mask_hi)
 *
 *   Restores processor extended state from *area* for the components
 *   whose bit is set in EDX:EAX.  *area* must be the 64-byte-aligned
 *   buffer previously written by vos3_xsave64 with the same or a
 *   superset RFBM.
 *
 *   Intel SDM Vol. 1 §13.7: if a component bit is set in RFBM but its
 *   XSTATE_BV flag in the header region is 0, XRSTOR64 loads the
 *   processor INIT state for that component rather than the saved value.
 *   This "optimized restore" is correct and expected behavior.
 */
.global vos3_xrstor64
.type vos3_xrstor64, @function
vos3_xrstor64:
    movl     %esi, %eax          /* mask_lo → EAX */
    /* rdx already holds mask_hi → EDX              */
    xrstor64 (%rdi)              /* restore from [rdi] */
    ret
.size vos3_xrstor64, . - vos3_xrstor64
#endif /* VOS3_HW_XSAVE */
