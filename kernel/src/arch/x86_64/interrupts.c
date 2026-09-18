/**
 * @file interrupts.c
 * @brief VOS3 Interrupt and Exception Handlers
 *
 * @details Default handlers for CPU exceptions and hardware IRQs.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../../include/vos/ipc.h"
#include "../../../include/vos/uaccess.h"
#include "../../../include/arch/x86_64/idt.h"
#include "../../../include/vos/console.h"
#include "../../../include/vos/timer.h"
#include "../../../include/vos/scheduler.h"
#include "../../../include/vos/task.h"
#include "../../../include/vos/ai_guard.h"
#include "../../../include/vos/keyboard.h"
#include "../../../include/vos/vmm.h"
#include "../../../include/vos/pmm.h"
#include "../../../include/vos/vfs.h"
#include "../../../include/vos/string.h"
#include "../../../include/vos/heap.h"
#include "../../../include/arch/x86_64/cpu.h"
#include "../../../include/vos/percpu.h"
#ifdef NATIVE_SMP_TEST
#include "../../../include/arch/x86_64/smp.h"
#include "../../../include/vos/atomic.h"
#endif
#ifdef NATIVE_ISOLATION_TEST
#include "../../../include/vos/native_isolation_test.h"
#endif

/* AI Monitor functions */
extern void vos3_ai_monitor_record_access(vos3_ai_guard_region_t* region,
                                           uintptr_t addr, int is_write);
extern void vos3_ai_monitor_record_fault(vos3_ai_guard_region_t* region,
                                          uintptr_t addr);

/* Phase 7: Lazy-Thaw demand paging for AI model slots */
extern int vos3_ai_lazy_thaw_fault(uintptr_t fault_addr);

/**
 * @brief Kill current task safely from page fault context.
 *
 * This mirrors what vos3_task_exit() does but is safe to call from an
 * exception handler (no scheduler lock acquisition, no sleeping).
 *
 * For threads (is_thread=1): performs CLONE_CHILD_CLEARTID futex wake,
 * unlinks from parent's children/sibling/thread_next lists, and calls
 * vos3_task_defer_destroy() to prevent permanent zombie leaks (no parent
 * will call waitpid for threads).
 *
 * For processes: sets ZOMBIE, wakes parent for waitpid collection.
 */
static void __attribute__((noreturn))
fault_kill_current(vos3_task_t* task, int exit_code)
{
    vos3_irqflags_t flags = vos3_irq_save();

    task->exit_code = exit_code;
    __atomic_store_n(&task->state, VOS3_TASK_ZOMBIE, __ATOMIC_RELEASE);
    vos3_shm_owner_exit(task->identity_cookie);

    /* Remove from scheduler run queue so we're never re-scheduled */
    vos3_sched_remove_task(task);

    /* CLONE_CHILD_CLEARTID: futex-wake so pthread_join unblocks.
     * Skip the copy_to_user write (could recurse if page is unmapped);
     * the futex_wake alone is sufficient — musl's clear_child_tid points
     * to __thread_list_lock which holds lock-state, not TID. */
    vos3_task_release_clear_child_tid(task, 0);

    /* Wake parent if it's blocked in waitpid() or pthread_join() */
    vos3_task_t* parent = task->parent;
    if (parent != NULL && parent->state == VOS3_TASK_BLOCKED) {
        vos3_task_unblock(parent);
    }

    /* Thread cleanup: unlink from parent lists + defer destroy.
     * Without this, fault-killed threads stay as permanent zombies
     * since no parent calls waitpid() for CLONE_THREAD tasks. */
    if (task->is_thread) {
        vos3_task_t* par = task->parent;
        if (par != NULL) {
            /* Unlink from parent's children/sibling list */
            vos3_task_t** pp = &par->children;
            while (*pp != NULL) {
                if (*pp == task) {
                    *pp = task->sibling;
                    break;
                }
                pp = &(*pp)->sibling;
            }
            task->sibling = NULL;

            /* Unlink from parent's thread_next chain */
            vos3_task_t** tp = &par->thread_next;
            while (*tp != NULL) {
                if (*tp == task) {
                    *tp = task->thread_next;
                    break;
                }
                tp = &(*tp)->thread_next;
            }
            task->thread_next = NULL;
        }

        vos3_task_defer_destroy(task);
    }

    /* Yield with IRQs disabled — prevents timer preemption between
     * state=ZOMBIE and the context switch away. */
    vos3_sched_yield();

    /* Safety: should never reach here */
    vos3_irq_restore(flags);
    for (;;) { __asm__ volatile ("hlt"); }
}

/* ============================================================================
 * EXCEPTION NAMES
 * ============================================================================ */

/** @brief Exception names for debugging */
static const char* const g_exception_names[] = {
    "Divide Error (#DE)",
    "Debug (#DB)",
    "Non-Maskable Interrupt (NMI)",
    "Breakpoint (#BP)",
    "Overflow (#OF)",
    "Bound Range Exceeded (#BR)",
    "Invalid Opcode (#UD)",
    "Device Not Available (#NM)",
    "Double Fault (#DF)",
    "Coprocessor Segment Overrun",
    "Invalid TSS (#TS)",
    "Segment Not Present (#NP)",
    "Stack-Segment Fault (#SS)",
    "General Protection Fault (#GP)",
    "Page Fault (#PF)",
    "Reserved",
    "x87 FPU Error (#MF)",
    "Alignment Check (#AC)",
    "Machine Check (#MC)",
    "SIMD Exception (#XM)",
    "Virtualization Exception (#VE)",
    "Control Protection (#CP)",
    "Reserved",
    "Reserved",
    "Reserved",
    "Reserved",
    "Reserved",
    "Reserved",
    "Hypervisor Injection",
    "VMM Communication",
    "Security Exception",
    "Reserved"
};

/* ============================================================================
 * EXCEPTION HANDLERS
 * ============================================================================ */

/**
 * @brief Divide Error Handler (#DE)
 */
static void handle_divide_error(vos3_int_frame_t* frame)
{
    vos3_panic(
        "Divide Error (#DE)\n"
        "  RIP: 0x%016llx\n"
        "  RSP: 0x%016llx",
        (unsigned long long)frame->rip,
        (unsigned long long)frame->rsp
    );
}

/**
 * @brief Debug Exception Handler (#DB)
 */
static void handle_debug(vos3_int_frame_t* frame)
{
    VOS3_DEBUG("Debug Exception at RIP: 0x%016llx",
               (unsigned long long)frame->rip);
    /* For now, just continue */
}

/**
 * @brief NMI Handler
 */
static void handle_nmi(vos3_int_frame_t* frame)
{
    VOS3_ERROR("NMI received at RIP: 0x%016llx",
               (unsigned long long)frame->rip);
    /* NMI can indicate hardware issues, but we don't panic */
}

/**
 * @brief Breakpoint Handler (#BP)
 */
static void handle_breakpoint(vos3_int_frame_t* frame)
{
    VOS3_DEBUG("Breakpoint at RIP: 0x%016llx",
               (unsigned long long)frame->rip);
    /* For debugging, just continue */
}

/**
 * @brief Invalid Opcode Handler (#UD)
 */
static void handle_invalid_opcode(vos3_int_frame_t* frame)
{
    vos3_panic(
        "Invalid Opcode (#UD)\n"
        "  RIP: 0x%016llx\n"
        "  RSP: 0x%016llx",
        (unsigned long long)frame->rip,
        (unsigned long long)frame->rsp
    );
}

/**
 * @brief Double Fault Handler (#DF)
 */
static void handle_double_fault(vos3_int_frame_t* frame)
{
    vos3_panic(
        "Double Fault (#DF)\n"
        "  Error Code: 0x%016llx\n"
        "  RIP: 0x%016llx\n"
        "  RSP: 0x%016llx",
        (unsigned long long)frame->error_code,
        (unsigned long long)frame->rip,
        (unsigned long long)frame->rsp
    );
}

/**
 * @brief General Protection Fault Handler (#GP)
 */
static void handle_gpf(vos3_int_frame_t* frame)
{
    vos3_panic(
        "General Protection Fault (#GP)\n"
        "  Error Code: 0x%016llx\n"
        "  RIP: 0x%016llx\n"
        "  RSP: 0x%016llx\n"
        "  CS:  0x%04llx",
        (unsigned long long)frame->error_code,
        (unsigned long long)frame->rip,
        (unsigned long long)frame->rsp,
        (unsigned long long)frame->cs
    );
}

/**
 * @brief Page Fault Handler (#PF)
 *
 * Extended with AI Guard support for Phase 17.3:
 * - Checks if fault is in AI-protected region
 * - Handles guard page violations gracefully
 * - Records monitored page accesses
 */
static void handle_page_fault(vos3_int_frame_t* frame)
{
    uint64_t cr2;
    __asm__ volatile ("movq %%cr2, %0" : "=r" (cr2));

    /* Decode error code */
    int is_write = (frame->error_code & 0x02) ? 1 : 0;
    int is_user = (frame->error_code & 0x04) ? 1 : 0;
    uintptr_t copy_fixup = vos3_usercopy_fault_fixup(frame->rip, frame->cs,
        frame->error_code, (uintptr_t)cr2, frame->rsi, frame->rdi, frame->rcx);
    if (copy_fixup != 0 && (vos3_read_cr4() & VOS3_CR4_SMAP) != 0) {
        /* Run fault handling with SMAP enabled. IRET restores saved AC only
         * when retrying a successfully resolved copy instruction. */
        __asm__ volatile(".byte 0x0f, 0x01, 0xca" ::: "memory", "cc");
    }

    /* ===== AI Guard Integration ===== */
    vos3_task_t* current = vos3_sched_current();
    if (current != NULL && current->ai_guard_ctx != NULL) {
        /* Check if fault is in an AI Guard region */
        vos3_ai_guard_region_t* region =
            vos3_ai_guard_find_region(current->ai_guard_ctx, (uintptr_t)cr2);

        if (region != NULL) {
            /* Check if this is a guard page violation */
            if (vos3_ai_guard_is_guard_page(current->ai_guard_ctx, (uintptr_t)cr2)) {
                /* Guard page fault - log, record, and KILL the task */
                (void)vos3_ai_guard_handle_fault(
                    current->ai_guard_ctx, (uintptr_t)cr2, frame->error_code);
                vos3_ai_monitor_record_fault(region, (uintptr_t)cr2);
                VOS3_WARN("[AI-GUARD] Guard page violation KILLING task '%s' (pid=%u) addr=0x%llx",
                          current->name, current->pid, (unsigned long long)cr2);
                fault_kill_current(current, 128 + 11);
            }

            /* Check if page has MONITORED flag - trap-and-emulate style */
            /* For monitored pages, record access and allow continuation */
            if (region->flags & VOS3_AI_FLAG_MONITOR_ACCESS) {
                vos3_ai_monitor_record_access(region, (uintptr_t)cr2, is_write);
                VOS3_INFO("[AI-GUARD] Monitored access: addr=0x%llx %s",
                          (unsigned long long)cr2, is_write ? "WRITE" : "READ");

                if (is_write) {
                    /* Temporarily make page writable so instruction can retry.
                     * AG-F fix: Include USER bit so user-space can still access
                     * the page after the temporary writable window. */
                    uintptr_t page_addr = (uintptr_t)cr2 & ~((uintptr_t)0xFFF);
                    vos3_vmm_update_flags(page_addr,
                        (vos3_vmm_flags_t)(VOS3_VMM_FLAG_WRITE | VOS3_VMM_FLAG_USER));
                    /* Mark region for re-protection on next tick */
                    region->needs_reprotect = 1U;
                }
                /* Return to retry the faulting instruction */
                return;
            }
        }
    }

    /* ===== Phase 4.2.7: Deep Diagnostic Feedback for AI HugePage Faults ===== */
    {
        int8_t fault_slot = vos3_ai_fault_find_slot((uintptr_t)cr2);
        if (fault_slot >= 0) {
            /* Capture 128 bytes from faulting stack pointer */
            uint8_t stack_buf[128];
            uint8_t stack_len = 0;
            uintptr_t rsp_val = frame->rsp;
            /* Validate RSP is in kernel space before reading */
            if (rsp_val >= 0xFFFF800000000000ULL && rsp_val + 128 > rsp_val) {
                memcpy(stack_buf, (const void *)rsp_val, 128);
                stack_len = 128;
            }
            vos3_ai_send_feedback((uint8_t)fault_slot, (uintptr_t)cr2,
                                  frame->rip, rsp_val, stack_buf, stack_len);
        }
    }

    /* ===== Phase F: Kernel Stack Overflow Detection ===== */
    if (current != NULL && current->kernel_stack_guard != 0) {
        uintptr_t guard = current->kernel_stack_guard;
        if ((uintptr_t)cr2 >= guard && (uintptr_t)cr2 < guard + 0x1000) {
            vos3_panic(
                "KERNEL STACK OVERFLOW\n"
                "  Task: '%s' (pid=%u)\n"
                "  Guard page: 0x%016llx\n"
                "  Fault addr: 0x%016llx\n"
                "  RIP: 0x%016llx",
                current->name, current->pid,
                (unsigned long long)guard,
                (unsigned long long)cr2,
                (unsigned long long)frame->rip);
        }
    }

    /* ===== Phase 4.2.9: AI Context COW Handling ===== */
    if (is_write && vos3_ai_context_cow_fault((uintptr_t)cr2) == 0) {
        VOS3_DEBUG("[AI-COW] Context page duplicated: addr=0x%llx", (unsigned long long)cr2);
        return;
    }

    /* ===== Phase 28: Copy-on-Write Handling ===== */
    if (vos3_vmm_handle_cow_fault((uintptr_t)cr2, frame->error_code) == 0) {
        /* COW fault handled successfully - return to user */
        VOS3_DEBUG("[COW] Page fault handled: addr=0x%llx", (unsigned long long)cr2);
        return;
    }

    /* ===== Phase 7: Lazy-Thaw AI Slot Demand Paging ===== */
    {
        int present = (frame->error_code & 0x01);
        if (!present) {
            /* Not-present fault — check if it's a lazy-thaw AI model page */
            if (vos3_ai_lazy_thaw_fault((uintptr_t)cr2) == 0) {
                VOS3_DEBUG("[LAZY-THAW] HugePage demand-paged: addr=0x%llx",
                           (unsigned long long)cr2);
                return;  /* Retry faulting instruction */
            }
        }
    }

    /* ===== Demand Paging: Lazy Allocation ===== */
    {
        int present = (frame->error_code & 0x01);
        vos3_vma_t* demand_vma = current ? vos3_vmm_find_vma(current->address_space, (uintptr_t)cr2) : NULL;
        int fetch = (frame->error_code & 0x10) != 0;
        int allowed = demand_vma == NULL ? !fetch :
            (fetch ? !!(demand_vma->vm_prot & 4) :
             is_write ? !!(demand_vma->vm_prot & 2) : !!(demand_vma->vm_prot & 3));
        if (!present && (is_user || copy_fixup != 0) && allowed) {
            /* Page not present in user space — check if address is valid */
            if (vos3_vmm_is_valid_user_addr((uintptr_t)cr2)) {
                /* Allocate zero-filled page on demand */
                uintptr_t page = vos3_pmm_alloc(0);
                if (page != 0) {
                    void* kvirt = vos3_phys_to_virt(page);
                    uint64_t aligned = cr2 & ~((uint64_t)VOS3_PAGE_SIZE - 1);

                    /* Check if this is a file-backed VMA */
                    vos3_vma_t* vma = vos3_vmm_find_vma(
                        current ? current->address_space : NULL, (uintptr_t)cr2);
                    int backing_ok = 1;
                    if (vma != NULL && vma->vm_file != NULL) {
                        vos3_file_t* file = vma->vm_file;
                        uint64_t delta = aligned - vma->vm_start;
                        int64_t nread = -1;
                        /* The backing reference belongs to the VMA, never to
                         * the faulting task's descriptor table. Positional I/O
                         * concurrency remains outside this serialized path. */
                        if (vma->vm_offset <= INT64_MAX &&
                            delta <= (uint64_t)INT64_MAX - vma->vm_offset &&
                            file->ops && file->ops->read && file->ops->lseek) {
                            uint64_t off = vma->vm_offset + delta;
                            if (off <= (uint64_t)INT64_MAX - VOS3_PAGE_SIZE) {
                                int64_t saved = file->ops->lseek(file, 0, 1);
                                if (saved >= 0) {
                                    if (file->ops->lseek(file, (int64_t)off, 0) == (int64_t)off)
                                        nread = file->ops->read(file, kvirt, VOS3_PAGE_SIZE);
                                    if (file->ops->lseek(file, saved, 0) != saved) nread = -1;
                                }
                            }
                        }
                        if (nread < 0 || nread > (int64_t)VOS3_PAGE_SIZE) {
                            backing_ok = 0;
                        } else {
                            memset((uint8_t*)kvirt + (size_t)nread, 0,
                                   VOS3_PAGE_SIZE - (size_t)nread);
                        }
                    } else {
                        /* Zero-fill for anonymous pages */
                        uint64_t* ptr = (uint64_t*)kvirt;
                        for (size_t i = 0; i < VOS3_PAGE_SIZE / sizeof(uint64_t); i++) {
                            ptr[i] = 0ULL;
                        }
                    }
                    uint64_t flags = VOS3_PTE_PRESENT | VOS3_PTE_USER;
                    if (demand_vma == NULL || (demand_vma->vm_prot & 2)) flags |= VOS3_PTE_WRITABLE;
                    if (demand_vma == NULL || !(demand_vma->vm_prot & 4)) flags |= VOS3_PTE_NO_EXECUTE;
                    if (backing_ok && vos3_vmm_map_user(aligned, page, flags) == 0) {
#ifdef NATIVE_ISOLATION_TEST
                        vos3_native_isolation_mapping(current, aligned, page);
#endif
                        VOS3_DEBUG("[DEMAND] Lazy page allocated: addr=0x%llx phys=0x%llx",
                                   (unsigned long long)cr2, (unsigned long long)page);
                        return;  /* Retry faulting instruction */
                    }
                    /* Mapping failed — free the page */
                    vos3_pmm_free(page);
                }
            }
        }
    }

    /* ===== Phase F: Cross-App Memory Access Check =====
     * AG-C fix: Moved AFTER COW and demand-paging handlers.
     * Previously this ran before COW, so a forked app touching a COW page
     * would be killed as a cross-app violation before the COW handler
     * had a chance to resolve the fault. */
    if (current != NULL && (current->flags & VOS3_TASK_FLAG_APP)) {
        if (vos3_ai_guard_check_app_access((uintptr_t)cr2, current->app_id) != 0) {
            VOS3_WARN("[AI-GUARD] Cross-app access violation: task '%s' (pid=%u, app=%u) addr=0x%llx",
                      current->name, current->pid, current->app_id,
                      (unsigned long long)cr2);
            fault_kill_current(current, 128 + 11);
        }
    }

    /* A precise copy-site failure returns to its caller for normal cleanup.
     * COW/demand paging above retain first chance to resolve and retry. */
    if (copy_fixup != 0) {
        frame->rip = copy_fixup;
        frame->rflags &= ~(1ULL << 18); /* Never return to the caller with AC set. */
        return;
    }

    /* ===== Unhandled Page Fault ===== */
    if (is_user) {
        /*
         * User-mode fault that wasn't COW or demand-paging — deliver SIGSEGV.
         * Uses fault_kill_current() which sets ZOMBIE and wakes parent for
         * waitpid() collection, avoiding deadlock with g_sched_lock.
         */
#ifdef NATIVE_ISOLATION_TEST
        vos3_native_isolation_fault(current, (uintptr_t)cr2);
#endif
        VOS3_WARN("SIGSEGV: task '%s' (pid=%u) addr=0x%llx RIP=0x%llx err=0x%llx",
                  current ? current->name : "?",
                  current ? current->pid : 0,
                  (unsigned long long)cr2,
                  (unsigned long long)frame->rip,
                  (unsigned long long)frame->error_code);
        if (current != NULL) {
            fault_kill_current(current, 128 + 11);
        }
        for (;;) { __asm__ volatile ("hlt"); }
    }

    /*
     * Kernel-mode fault on user-space address: the kernel was doing
     * copy_from_user/copy_to_user with a bad user pointer or size.
     * Kill the task rather than panicking the whole system.
     * AG-D fix: same safe-kill pattern as above.
     */
    if (current != NULL && cr2 < 0x00007FFFFFFFFFFFULL) {
        VOS3_WARN("SIGSEGV (kernel uaccess): task '%s' (pid=%u) addr=0x%llx RIP=0x%llx",
                  current->name, current->pid,
                  (unsigned long long)cr2, (unsigned long long)frame->rip);
        if (current->address_space != NULL) {
            uint64_t active_root = vos3_read_cr3() & ~0xFFFULL;
            uint64_t task_root = current->address_space->pml4_phys;
            if (active_root != task_root) {
                VOS3_ERROR("uaccess address-space mismatch: active CR3=0x%llx task CR3=0x%llx error=0x%llx",
                           (unsigned long long)active_root,
                           (unsigned long long)task_root,
                           (unsigned long long)frame->error_code);
            }
        }
        fault_kill_current(current, 128 + 11);
    }

    /* Kernel-mode fault on kernel address — unrecoverable, panic */
    const char* present = (frame->error_code & 0x01) ? "protection" : "not-present";
    const char* rw = is_write ? "write" : "read";
    const char* reserved = (frame->error_code & 0x08) ? " reserved-bit" : "";
    const char* ifetch = (frame->error_code & 0x10) ? " instruction-fetch" : "";

    vos3_panic(
        "Page Fault (#PF)\n"
        "  Faulting Address: 0x%016llx\n"
        "  Error Code: 0x%04llx (kernel %s %s%s%s)\n"
        "  RIP: 0x%016llx\n"
        "  RSP: 0x%016llx",
        (unsigned long long)cr2,
        (unsigned long long)frame->error_code,
        rw, present, reserved, ifetch,
        (unsigned long long)frame->rip,
        (unsigned long long)frame->rsp
    );
}

/* ============================================================================
 * FPU/SSE/AVX LAZY CONTEXT SWITCH (Phase 1.2)
 *
 * Uses CR0.TS (Task Switched) bit for lazy FPU switching:
 * - Every context switch sets CR0.TS
 * - First FPU/SSE/AVX instruction triggers #NM (Device Not Available)
 * - #NM handler saves old owner's state, restores new owner's state
 * - Management tasks that never use FPU pay zero cost
 *
 * Supports XSAVE (AVX/AVX-512) when available, falls back to FXSAVE.
 * ============================================================================ */

/** @brief Per-CPU task that last used the FPU (owns current FPU register state) */
static vos3_task_t* g_fpu_owner[VOS3_MAX_CPUS] = {NULL};

/** @brief 1 if CPU supports XSAVE, 0 for FXSAVE fallback
 *  @note Non-static: also referenced by crypto_helpers.c for vos3_fpu_begin/end() */
int g_use_xsave = 0;

/** @brief XCR0 mask for XSAVE (which components to save/restore)
 *  @note Non-static: also referenced by crypto_helpers.c for vos3_fpu_begin/end() */
uint64_t g_xcr0_mask = 0;

/** @brief Size of XSAVE area in bytes */
static uint32_t g_xsave_size = 512;

/** @brief FPU state buffer size (1024 bytes, future-proof) */
#define VOS3_FPU_STATE_SIZE  1024U

/** @brief FPU state alignment (64 bytes for XSAVE) */
#define VOS3_FPU_STATE_ALIGN 64U


/**
 * @brief Allocate a 64-byte aligned FPU state buffer for a task
 */
static uint8_t* fpu_alloc_state(vos3_task_t* task)
{
    void* raw = vos3_kzalloc(VOS3_FPU_STATE_SIZE + VOS3_FPU_STATE_ALIGN - 1);
    if (raw == NULL) {
        return NULL;
    }
    task->fpu_state_raw = raw;
    uint8_t* aligned = (uint8_t*)(((uintptr_t)raw + VOS3_FPU_STATE_ALIGN - 1)
                                   & ~((uintptr_t)VOS3_FPU_STATE_ALIGN - 1));
    task->fpu_state = aligned;
    task->fpu_initialized = 0;
    return aligned;
}

/**
 * @brief Save FPU state of the given task using XSAVE or FXSAVE
 */
static inline void fpu_save(uint8_t* area)
{
    if (g_use_xsave) {
        uint32_t lo = (uint32_t)g_xcr0_mask;
        uint32_t hi = (uint32_t)(g_xcr0_mask >> 32);
        __asm__ volatile("xsave (%0)" :: "r"(area), "a"(lo), "d"(hi) : "memory");
    } else {
        __asm__ volatile("fxsave (%0)" :: "r"(area) : "memory");
    }
}

/**
 * @brief Restore FPU state of the given task using XRSTOR or FXRSTOR
 */
static inline void fpu_restore(uint8_t* area)
{
    if (g_use_xsave) {
        uint32_t lo = (uint32_t)g_xcr0_mask;
        uint32_t hi = (uint32_t)(g_xcr0_mask >> 32);
        __asm__ volatile("xrstor (%0)" :: "r"(area), "a"(lo), "d"(hi) : "memory");
    } else {
        __asm__ volatile("fxrstor (%0)" :: "r"(area) : "memory");
    }
}

/**
 * @brief Device Not Available (#NM) Handler — Lazy FPU Context Switch
 *
 * Triggered when a task uses FPU/SSE/AVX after CR0.TS was set.
 * Saves the previous owner's FPU state and restores the current task's state.
 */
static void handle_device_na(vos3_int_frame_t* frame)
{
    (void)frame;

    /* Clear CR0.TS so FPU instructions work */
    uint64_t cr0 = vos3_read_cr0();
    vos3_write_cr0(cr0 & ~(1ULL << 3));

    vos3_task_t* current = vos3_task_current();
    if (current == NULL) {
        return;
    }

    uint32_t cpu = get_cpu_id();

    /* If current task already owns the FPU, nothing to do */
    if (g_fpu_owner[cpu] == current) {
        return;
    }

    /* Save previous owner's FPU state */
    if (g_fpu_owner[cpu] != NULL && g_fpu_owner[cpu]->fpu_state != NULL) {
        fpu_save(g_fpu_owner[cpu]->fpu_state);
        g_fpu_owner[cpu]->fpu_initialized = 1;
    }

    /* Allocate FPU state buffer on first use (lazy allocation) */
    if (current->fpu_state == NULL) {
        if (fpu_alloc_state(current) == NULL) {
            VOS3_ERROR("FPU: Failed to allocate state for task '%s' (pid=%u)",
                       current->name, current->pid);
            return;
        }
    }

    /* Restore current task's FPU state or initialize fresh */
    if (current->fpu_initialized) {
        fpu_restore(current->fpu_state);
    } else {
        /* Initialize clean FPU state — SECURITY CRITICAL:
         * fninit only resets x87 state.  XMM/YMM registers still contain
         * the previous owner's data (AI computation residue).  Zero all
         * XMM0-15 using raw byte encoding (kernel is built with -mno-sse,
         * so GCC clobbers for XMM registers are unavailable). */
        __asm__ volatile("fninit");
        uint32_t mxcsr_val = 0x1F80U;
        __asm__ volatile("ldmxcsr %0" :: "m"(mxcsr_val));
        /* xorps xmmN, xmmN — zero each SSE register */
        __asm__ volatile(
            ".byte 0x0F, 0x57, 0xC0\n\t"       /* xorps xmm0, xmm0   */
            ".byte 0x0F, 0x57, 0xC9\n\t"       /* xorps xmm1, xmm1   */
            ".byte 0x0F, 0x57, 0xD2\n\t"       /* xorps xmm2, xmm2   */
            ".byte 0x0F, 0x57, 0xDB\n\t"       /* xorps xmm3, xmm3   */
            ".byte 0x0F, 0x57, 0xE4\n\t"       /* xorps xmm4, xmm4   */
            ".byte 0x0F, 0x57, 0xED\n\t"       /* xorps xmm5, xmm5   */
            ".byte 0x0F, 0x57, 0xF6\n\t"       /* xorps xmm6, xmm6   */
            ".byte 0x0F, 0x57, 0xFF\n\t"       /* xorps xmm7, xmm7   */
            ".byte 0x45, 0x0F, 0x57, 0xC0\n\t" /* xorps xmm8, xmm8   */
            ".byte 0x45, 0x0F, 0x57, 0xC9\n\t" /* xorps xmm9, xmm9   */
            ".byte 0x45, 0x0F, 0x57, 0xD2\n\t" /* xorps xmm10, xmm10 */
            ".byte 0x45, 0x0F, 0x57, 0xDB\n\t" /* xorps xmm11, xmm11 */
            ".byte 0x45, 0x0F, 0x57, 0xE4\n\t" /* xorps xmm12, xmm12 */
            ".byte 0x45, 0x0F, 0x57, 0xED\n\t" /* xorps xmm13, xmm13 */
            ".byte 0x45, 0x0F, 0x57, 0xF6\n\t" /* xorps xmm14, xmm14 */
            ".byte 0x45, 0x0F, 0x57, 0xFF"     /* xorps xmm15, xmm15 */
            ::: "memory"
        );
        current->fpu_initialized = 1;
    }

    g_fpu_owner[cpu] = current;

    VOS3_DEBUG("FPU Context Restored for PID %d", (int)current->pid);
}

/**
 * @brief Initialize FPU subsystem
 *
 * Detects XSAVE support via CPUID, enables CR4.OSXSAVE if available,
 * configures XCR0 for x87+SSE+AVX components.
 */
void vos3_fpu_init(void)
{
    uint32_t eax, ebx, ecx, edx;
    vos3_cpuid(1, 0, &eax, &ebx, &ecx, &edx);

    if (ecx & (1U << 26)) {  /* XSAVE supported */
        /* Enable CR4.OSXSAVE (bit 18) */
        uint64_t cr4 = vos3_read_cr4();
        vos3_write_cr4(cr4 | (1ULL << 18));

        /* Build XCR0 mask: x87 (bit 0) + SSE (bit 1) always enabled */
        g_xcr0_mask = 0x3ULL;

        /* Check for AVX support (ECX bit 28) */
        if (ecx & (1U << 28)) {
            g_xcr0_mask |= 0x4ULL;  /* AVX: YMM high 128 bits (bit 2) */
        }

        /* Check for AVX-512 support (CPUID leaf 7, EBX bit 16) */
        uint32_t eax7, ebx7, ecx7, edx7;
        vos3_cpuid(7, 0, &eax7, &ebx7, &ecx7, &edx7);
        if (ebx7 & (1U << 16)) {
            g_xcr0_mask |= 0xE0ULL;  /* AVX-512: opmask(5) + ZMM_Hi256(6) + Hi16_ZMM(7) */
        }

        /* Write XCR0 via XSETBV (ECX=0 selects XCR0) */
        uint32_t xcr0_lo = (uint32_t)g_xcr0_mask;
        uint32_t xcr0_hi = (uint32_t)(g_xcr0_mask >> 32);
        __asm__ volatile("xsetbv" :: "a"(xcr0_lo), "d"(xcr0_hi), "c"(0));

        /* Query required XSAVE area size (CPUID leaf 0xD, subleaf 0) */
        uint32_t eax_d, ebx_d, ecx_d, edx_d;
        vos3_cpuid(0xD, 0, &eax_d, &ebx_d, &ecx_d, &edx_d);
        g_xsave_size = ebx_d;  /* Size in bytes for current XCR0 */
        if (g_xsave_size > VOS3_FPU_STATE_SIZE) {
            g_xsave_size = VOS3_FPU_STATE_SIZE;
        }

        g_use_xsave = 1;
        VOS3_INFO("FPU: XSAVE enabled (XCR0=0x%llx, size=%u bytes)",
                  (unsigned long long)g_xcr0_mask, g_xsave_size);
    } else {
        g_use_xsave = 0;
        g_xsave_size = 512;
        VOS3_INFO("FPU: Using FXSAVE fallback (XSAVE not available)");
    }

    /* Ensure CR0.TS is clear during kernel init (boot code may have set it) */
    uint64_t cr0 = vos3_read_cr0();
    vos3_write_cr0(cr0 & ~(1ULL << 3));

    VOS3_INFO("FPU: Lazy context switching initialized");
}

/**
 * @brief Release FPU ownership for a dying task
 */
void vos3_fpu_release_owner(vos3_task_t* task)
{
#ifdef NATIVE_SMP_TEST
    /* Fixed scheduler ownership permits only local reclamation. */
    vos3_irqflags_t flags = vos3_irq_save();
    uint32_t cpu = get_cpu_id();
    if (g_fpu_owner[cpu] == task)
        g_fpu_owner[cpu] = NULL;
    vos3_irq_restore(flags);
#else
    for (uint32_t i = 0; i < VOS3_MAX_CPUS; i++) {
        if (g_fpu_owner[i] == task) {
            g_fpu_owner[i] = NULL;
        }
    }
#endif
}

/**
 * @brief Set CR0.TS bit for lazy FPU switching
 */
void vos3_fpu_set_ts(void)
{
    uint64_t cr0 = vos3_read_cr0();
    vos3_write_cr0(cr0 | (1ULL << 3));
}

/**
 * @brief Machine Check Handler (#MC)
 */
static void handle_machine_check(vos3_int_frame_t* frame)
{
    vos3_panic(
        "Machine Check Exception (#MC)\n"
        "  RIP: 0x%016llx\n"
        "  Hardware failure detected!",
        (unsigned long long)frame->rip
    );
}

/* ============================================================================
 * IRQ HANDLERS
 * ============================================================================ */

/* Forward declaration for PIC EOI */
extern void vos3_pic_eoi(uint8_t irq);

#ifdef NATIVE_SMP_TEST
static void handle_schedule_ipi(vos3_int_frame_t* frame)
{
    (void)frame;
    /* Retire this interrupt before switching away from its stack. */
    vos3_lapic_eoi();
    if (vos3_sched_is_running()) {
        vos3_sched_request_reschedule();
        /* Kernel code may hold locks even while the current task is idle.
         * Its idle loop consumes the request only after deferred work ends.
         * Arbitrary kernel preemption requires a separate qualification. */
        if ((frame->cs & 3U) == 3U) {
            vos3_sched_tick();
            if (vos3_sched_need_reschedule())
                vos3_sched_reschedule();
        }
    }
}
#endif

/**
 * @brief Timer IRQ Handler (IRQ0)
 */
static void handle_timer_irq(vos3_int_frame_t* frame)
{
    (void)frame;

    /* Call timer handler (handles ticks and scheduler) */
    vos3_timer_irq_handler();

    vos3_pic_eoi(0U);
#ifdef NATIVE_SMP_TEST
    if (vos3_sched_is_running()) {
        for (uint32_t cpu = 1; cpu < vos3_smp_cpu_count(); cpu++) {
            const vos3_smp_cpu_info_t *info = vos3_smp_get_cpu_info(cpu);
            if (info != NULL && info->started)
                vos3_lapic_send_ipi(info->apic_id, VOS3_INT_IPI_SCHEDULE);
        }
    }
#endif

    /* Reschedule if needed — this is critical for waking sleeping tasks.
     * vos3_sched_tick() processes the sleep queue and sets g_need_reschedule,
     * but without actually calling reschedule here, the idle loop (sti;hlt;jmp)
     * never yields to the woken task. */
    if (vos3_sched_is_running() != 0 && vos3_sched_need_reschedule() != 0) {
        vos3_sched_reschedule();
    }
}

/**
 * @brief Keyboard IRQ Handler (IRQ1)
 *
 * @note Phase 26: Forwards scancodes to keyboard driver for ASCII translation
 */
static void handle_keyboard_irq(vos3_int_frame_t* frame)
{
    (void)frame;

    /* Read scancode from PS/2 data port */
    uint8_t scancode;
    __asm__ volatile ("inb $0x60, %0" : "=a"(scancode));

    /* Forward to keyboard driver for translation and TTY input */
    vos3_keyboard_handle_scancode(scancode);

    vos3_pic_eoi(1U);
}

/**
 * @brief Spurious IRQ Handler (IRQ7/IRQ15)
 */
static void handle_spurious_irq(vos3_int_frame_t* frame)
{
    (void)frame;
    /* Don't send EOI for spurious interrupts */
    VOS3_DEBUG("Spurious IRQ received");
}

/* ============================================================================
 * CONTROL PROTECTION EXCEPTION (#CP, Vector 21)
 * ============================================================================ */

/**
 * @brief Handle #CP — Control Protection Exception (CET)
 *
 * Triggered when CET detects an indirect branch without ENDBR64 (IBT),
 * or a shadow stack mismatch (SHSTK). Error code format:
 *   bit 0: NEAR_RET — shadow stack mismatch on near RET
 *   bit 1: FAR_RET  — shadow stack mismatch on far RET/IRET
 *   bit 2: ENDBR    — missing ENDBR at indirect branch target
 *   bit 3: RSTORSSP — RSTORSSP token mismatch
 */
static void handle_control_protection(vos3_int_frame_t* frame)
{
    uint64_t err = frame->error_code;
    const char *reason = "unknown";

    if (err & (1ULL << 2)) {
        reason = "missing ENDBR64 at indirect branch target";
    } else if (err & (1ULL << 0)) {
        reason = "shadow stack mismatch on near RET";
    } else if (err & (1ULL << 1)) {
        reason = "shadow stack mismatch on far RET/IRET";
    } else if (err & (1ULL << 3)) {
        reason = "RSTORSSP token mismatch";
    }

    /* Check if fault is from user-space */
    if ((frame->cs & 0x3) != 0) {
        vos3_console_printf("[CET] #CP in user task: %s (RIP=0x%llx err=0x%llx)\n",
                            reason,
                            (unsigned long long)frame->rip,
                            (unsigned long long)err);
        /* Kill the offending user task (exit code = 128 + 21) */
        vos3_task_t *current = vos3_sched_current();
        if (current != NULL) {
            fault_kill_current(current, 128 + 21);
        }
        /* NOTREACHED */
    }

    /* Kernel #CP — fatal */
    vos3_console_printf("\n*** KERNEL #CP: Control Protection Exception ***\n");
    vos3_console_printf("  Reason:     %s\n", reason);
    vos3_console_printf("  Error code: 0x%llx\n", (unsigned long long)err);
    vos3_console_printf("  RIP:        0x%llx\n", (unsigned long long)frame->rip);
    vos3_console_printf("  RSP:        0x%llx\n", (unsigned long long)frame->rsp);
    vos3_console_printf("  CS:         0x%llx\n", (unsigned long long)frame->cs);

    vos3_panic("CET #CP: %s at RIP 0x%llx", reason,
               (unsigned long long)frame->rip);
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

/**
 * @brief Register default interrupt handlers
 */
void vos3_interrupts_init(void)
{
    VOS3_INFO("INT: Registering default interrupt handlers");
#ifdef NATIVE_SMP_TEST
    if (vos3_int_register(VOS3_INT_IPI_SCHEDULE, handle_schedule_ipi) != 0)
        vos3_panic("Cannot register scheduler IPI");
#endif

    /* CPU Exceptions */
    vos3_int_register(VOS3_INT_DIVIDE_ERROR, handle_divide_error);
    vos3_int_register(VOS3_INT_DEBUG, handle_debug);
    vos3_int_register(VOS3_INT_NMI, handle_nmi);
    vos3_int_register(VOS3_INT_BREAKPOINT, handle_breakpoint);
    vos3_int_register(VOS3_INT_INVALID_OPCODE, handle_invalid_opcode);
    vos3_int_register(VOS3_INT_DEVICE_NA, handle_device_na);
    vos3_int_register(VOS3_INT_DOUBLE_FAULT, handle_double_fault);
    vos3_int_register(VOS3_INT_GENERAL_PROT, handle_gpf);
    vos3_int_register(VOS3_INT_PAGE_FAULT, handle_page_fault);
    vos3_int_register(VOS3_INT_MACHINE_CHECK, handle_machine_check);
    vos3_int_register(VOS3_INT_CONTROL_PROT, handle_control_protection);

    /* Hardware IRQs */
    vos3_int_register(VOS3_IRQ_TIMER, handle_timer_irq);
    vos3_int_register(VOS3_IRQ_KEYBOARD, handle_keyboard_irq);
    vos3_int_register(VOS3_IRQ_LPT1, handle_spurious_irq);  /* IRQ7 can be spurious */

    VOS3_INFO("INT: Default handlers registered");
}

/**
 * @brief Get exception name
 * @param[in] vector Exception vector number
 * @return Exception name string
 */
const char* vos3_exception_name(uint8_t vector)
{
    if (vector < 32U) {
        return g_exception_names[vector];
    }
    return "Unknown";
}

/**
 * @brief Dump interrupt frame for debugging
 * @param[in] frame Pointer to interrupt frame
 */
void vos3_dump_int_frame(const vos3_int_frame_t* frame)
{
    vos3_console_printf("Interrupt Frame Dump:\n");
    vos3_console_printf("  RAX: 0x%016llx  RBX: 0x%016llx\n",
        (unsigned long long)frame->rax, (unsigned long long)frame->rbx);
    vos3_console_printf("  RCX: 0x%016llx  RDX: 0x%016llx\n",
        (unsigned long long)frame->rcx, (unsigned long long)frame->rdx);
    vos3_console_printf("  RSI: 0x%016llx  RDI: 0x%016llx\n",
        (unsigned long long)frame->rsi, (unsigned long long)frame->rdi);
    vos3_console_printf("  RBP: 0x%016llx  RSP: 0x%016llx\n",
        (unsigned long long)frame->rbp, (unsigned long long)frame->rsp);
    vos3_console_printf("  R8:  0x%016llx  R9:  0x%016llx\n",
        (unsigned long long)frame->r8, (unsigned long long)frame->r9);
    vos3_console_printf("  R10: 0x%016llx  R11: 0x%016llx\n",
        (unsigned long long)frame->r10, (unsigned long long)frame->r11);
    vos3_console_printf("  R12: 0x%016llx  R13: 0x%016llx\n",
        (unsigned long long)frame->r12, (unsigned long long)frame->r13);
    vos3_console_printf("  R14: 0x%016llx  R15: 0x%016llx\n",
        (unsigned long long)frame->r14, (unsigned long long)frame->r15);
    vos3_console_printf("  RIP: 0x%016llx  RFLAGS: 0x%016llx\n",
        (unsigned long long)frame->rip, (unsigned long long)frame->rflags);
    vos3_console_printf("  CS:  0x%04llx  SS: 0x%04llx\n",
        (unsigned long long)frame->cs, (unsigned long long)frame->ss);
    vos3_console_printf("  INT: %llu  ERR: 0x%016llx\n",
        (unsigned long long)frame->int_num, (unsigned long long)frame->error_code);
}
