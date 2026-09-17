/**
 * @file exec.c
 * @brief VOS3 Program Execution
 *
 * @details Implements exec, fork, and process management.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/elf.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/ipc.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/user.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/syscall.h"
#include "../../include/vos/entropy.h"

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Maximum argument count */
#define MAX_ARGC        256U

/** @brief Maximum environment count */
#define MAX_ENVC        256U

/** @brief Maximum total argument/env size */
#define MAX_ARG_SIZE    (128U * 1024U)

/** @brief Default user stack for exec */
#define EXEC_STACK_SIZE (64U * 1024U)

/* ============================================================================
 * AUXILIARY VECTOR TYPES (Linux ABI)
 * ============================================================================ */

#define AT_NULL    0   /**< End of auxv */
#define AT_PHDR    3   /**< Program header table address */
#define AT_PHENT   4   /**< Size of program header entry */
#define AT_PHNUM   5   /**< Number of program headers */
#define AT_PAGESZ  6   /**< System page size */
#define AT_BASE    7   /**< Interpreter base address */
#define AT_ENTRY   9   /**< Program entry point */
#define AT_UID     11  /**< Real UID */
#define AT_EUID    12  /**< Effective UID */
#define AT_GID     13  /**< Real GID */
#define AT_EGID    14  /**< Effective GID */
#define AT_RANDOM  25  /**< Address of 16 random bytes */

typedef struct { uint64_t a_type; uint64_t a_val; } vos3_auxv_t;

/* ============================================================================
 * ARGUMENT SETUP
 * ============================================================================ */

/**
 * @brief Set up user stack with arguments and environment
 *
 * Stack layout (growing down):
 *   [high address]
 *   environment strings
 *   argument strings
 *   padding for alignment
 *   NULL (end of envp)
 *   envp[n-1], ..., envp[0]
 *   NULL (end of argv)
 *   argv[argc-1], ..., argv[0]
 *   argc
 *   [low address] <- initial RSP
 */
static int setup_user_stack(uint64_t stack_top,
                             const char* argv[],
                             const char* envp[],
                             vos3_auxv_t* auxv,
                             size_t auxv_count,
                             uint64_t* out_sp,
                             uint64_t* out_argc)
{
    /* Count arguments */
    size_t argc = 0U;
    size_t argv_size = 0U;
    if (argv != NULL) {
        while (argv[argc] != NULL && argc < MAX_ARGC) {
            argv_size += strlen(argv[argc]) + 1U;
            argc++;
        }
    }

    /* Count environment variables */
    size_t envc = 0U;
    size_t envp_size = 0U;
    if (envp != NULL) {
        while (envp[envc] != NULL && envc < MAX_ENVC) {
            envp_size += strlen(envp[envc]) + 1U;
            envc++;
        }
    }

    /* Check total size */
    size_t total_strings = argv_size + envp_size;
    if (total_strings > MAX_ARG_SIZE) {
        return VOS3_ELF_ERR_INVALID;
    }

    /* Calculate stack layout:
     *   [High]  16 random bytes (AT_RANDOM target)
     *           environment strings
     *           argument strings
     *           padding (16-byte align)
     *           auxv[N] = {AT_NULL, 0}
     *           auxv[N-1] ... auxv[0]
     *           NULL (envp terminator)
     *           envp[M] ... envp[0]
     *           NULL (argv terminator)
     *           argv[argc-1] ... argv[0]
     *           argc
     *   [Low = RSP]
     */
    size_t auxv_size = (auxv != NULL) ? (auxv_count * sizeof(vos3_auxv_t)) : 0U;
    size_t random_size = (auxv != NULL) ? 16U : 0U;
    size_t pointers_size = (argc + 1U + envc + 1U + 1U) * sizeof(uint64_t);
    size_t total_size = total_strings + random_size + pointers_size + auxv_size + 16U;

    /* Align to 16 bytes */
    total_size = (total_size + 15U) & ~15ULL;

    /* Guard against total_size exceeding available stack space */
    if (total_size > stack_top) {
        return -22; /* EINVAL */
    }

    uint64_t sp = stack_top - total_size;
    sp &= ~15ULL;  /* 16-byte alignment */

    /* Map stack pages if not already mapped */
    uint64_t stack_pages_needed = (total_size + VOS3_PAGE_SIZE - 1U) / VOS3_PAGE_SIZE;
    for (uint64_t i = 0U; i < stack_pages_needed; i++) {
        if (sp < i * VOS3_PAGE_SIZE) {
            return -22; /* EINVAL — address underflow */
        }
        uint64_t page_addr = (sp - (i * VOS3_PAGE_SIZE)) & ~(VOS3_PAGE_SIZE - 1ULL);

        /* Allocate and map stack page */
        uint64_t phys = vos3_pmm_alloc(0);
        if (phys == 0ULL) {
            return VOS3_ELF_ERR_NOMEM;
        }

        int result = vos3_vmm_map_user(page_addr, phys,
                                        VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE |
                                        VOS3_PTE_USER | VOS3_PTE_NO_EXECUTE);
        if (result != 0) {
            vos3_pmm_free(phys);
            return VOS3_ELF_ERR_NOMEM;
        }
    }

    /* Write 16 random bytes at the very top (for AT_RANDOM) */
    uint64_t random_addr = 0;
    if (auxv != NULL) {
        random_addr = stack_top - random_size;
        void* rnd_kaddr = vos3_vmm_get_kernel_addr(random_addr);
        if (rnd_kaddr != NULL) {
            /* Use ChaCha20 CSPRNG for cryptographically secure random bytes */
            vos3_entropy_extract(rnd_kaddr, 16);
        }
    }

    /* Write strings below random bytes */
    uint64_t string_ptr = stack_top - random_size - total_strings;
    uint64_t* argv_ptrs = (uint64_t*)vos3_kmalloc((argc + 1U) * sizeof(uint64_t));
    uint64_t* envp_ptrs = (uint64_t*)vos3_kmalloc((envc + 1U) * sizeof(uint64_t));

    if (argv_ptrs == NULL || envp_ptrs == NULL) {
        if (argv_ptrs != NULL) vos3_kfree(argv_ptrs);
        if (envp_ptrs != NULL) vos3_kfree(envp_ptrs);
        return VOS3_ELF_ERR_NOMEM;
    }

    /* Copy environment strings */
    uint64_t current_ptr = string_ptr;
    for (size_t i = 0U; i < envc; i++) {
        size_t len = strlen(envp[i]) + 1U;
        envp_ptrs[i] = current_ptr;

        void* kaddr = vos3_vmm_get_kernel_addr(current_ptr);
        if (kaddr != NULL) {
            memcpy(kaddr, envp[i], len);
        }
        current_ptr += len;
    }
    envp_ptrs[envc] = 0ULL;

    /* Copy argument strings */
    for (size_t i = 0U; i < argc; i++) {
        size_t len = strlen(argv[i]) + 1U;
        argv_ptrs[i] = current_ptr;

        void* kaddr = vos3_vmm_get_kernel_addr(current_ptr);
        if (kaddr != NULL) {
            memcpy(kaddr, argv[i], len);
        }
        current_ptr += len;
    }
    argv_ptrs[argc] = 0ULL;

    /* Write pointers, auxv, and argc */
    uint64_t* stack = (uint64_t*)vos3_vmm_get_kernel_addr(sp);
    if (stack != NULL) {
        size_t idx = 0U;

        /* argc */
        stack[idx++] = argc;

        /* argv pointers */
        for (size_t i = 0U; i <= argc; i++) {
            stack[idx++] = argv_ptrs[i];
        }

        /* envp pointers */
        for (size_t i = 0U; i <= envc; i++) {
            stack[idx++] = envp_ptrs[i];
        }

        /* Auxiliary vector (only when auxv != NULL) */
        if (auxv != NULL && auxv_count > 0) {
            for (size_t i = 0U; i < auxv_count; i++) {
                /* Patch AT_RANDOM to point to actual random bytes */
                if (auxv[i].a_type == AT_RANDOM) {
                    auxv[i].a_val = random_addr;
                }
                stack[idx++] = auxv[i].a_type;
                stack[idx++] = auxv[i].a_val;
            }
        }
    }

    vos3_kfree(argv_ptrs);
    vos3_kfree(envp_ptrs);

    *out_sp = sp;
    *out_argc = argc;

    return VOS3_ELF_OK;
}

/* ============================================================================
 * EXEC IMPLEMENTATION
 * ============================================================================ */

int vos3_exec(const char* path, const char* argv[], const char* envp[])
{
    if (path == NULL) {
        return VOS3_ELF_ERR_INVALID;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return VOS3_ELF_ERR_INVALID;
    }

    VOS3_INFO("exec: Loading '%s' (pid=%u)", path, current->pid);

    /* Save old address space for cleanup/restore */
    vos3_address_space_t* old_as = current->address_space;
    uint64_t old_mmap_next = current->mmap_next;
    vos3_address_space_t* kernel_space = vos3_vmm_get_kernel_space();

    /* Create new address space for the user process
     * This creates a fresh PML4 with:
     * - Empty user space (PML4[0-255] = 0)
     * - Kernel mappings copied (PML4[256-511] from kernel space)
     */
    vos3_address_space_t* new_as = vos3_vmm_create_address_space();
    if (new_as == NULL) {
        VOS3_ERROR("exec: Failed to create address space");
        return VOS3_ELF_ERR_NOMEM;
    }

    VOS3_DEBUG("exec: Created new address space, PML4 phys=0x%llx virt=0x%llx",
               (unsigned long long)new_as->pml4_phys,
               (unsigned long long)(uintptr_t)new_as->pml4);

    /* Switch to the new address space BEFORE loading ELF
     * This ensures vos3_vmm_map_user uses the correct PML4 */
    current->address_space = new_as;
    vos3_vmm_switch_address_space(new_as);

    /* Phase v17 (K-C5): Reset per-process mmap bump allocator on exec */
    current->mmap_next = 0x0000000030000000ULL;  /* VOS3_MMAP_BASE */

    VOS3_DEBUG("exec: Switched to new address space, loading ELF...");

    /* Load ELF file into the new address space */
    vos3_elf_info_t elf_info;
    int result = vos3_elf_load_file(path, &elf_info);
    if (result != VOS3_ELF_OK) {
        VOS3_ERROR("exec: Failed to load ELF '%s' (error %d)", path, result);

        /* Restore old address space on failure */
        if (old_as != NULL && old_as != kernel_space) {
            current->address_space = old_as;
            vos3_vmm_switch_address_space(old_as);
        } else {
            current->address_space = kernel_space;
            vos3_vmm_switch_address_space(kernel_space);
        }

        /* Destroy the failed new address space */
        vos3_vmm_destroy_address_space(new_as);
        current->mmap_next = old_mmap_next;
        return result;
    }

    /* ===== Interpreter Loading (PT_INTERP / dynamic linking) ===== */
    vos3_elf_info_t interp_info;
    memset(&interp_info, 0, sizeof(interp_info));
    uint64_t actual_entry = elf_info.entry;

    if (elf_info.has_interp) {
        VOS3_INFO("exec: ELF has PT_INTERP='%s', loading interpreter at 0x%llx",
                  elf_info.interp, (unsigned long long)VOS3_INTERP_BASE);

        result = vos3_elf_load_file_at(elf_info.interp, VOS3_INTERP_BASE, &interp_info);
        if (result != VOS3_ELF_OK) {
            VOS3_ERROR("exec: Failed to load interpreter '%s' (error %d)",
                       elf_info.interp, result);
            /* Restore old address space on failure */
            if (old_as != NULL && old_as != kernel_space) {
                current->address_space = old_as;
                vos3_vmm_switch_address_space(old_as);
            } else {
                current->address_space = kernel_space;
                vos3_vmm_switch_address_space(kernel_space);
            }
            vos3_vmm_destroy_address_space(new_as);
            current->mmap_next = old_mmap_next;
            return result;
        }

        /* Reject recursive interpreters */
        if (interp_info.has_interp) {
            VOS3_ERROR("exec: Recursive interpreter detected in '%s'", elf_info.interp);
            if (old_as != NULL && old_as != kernel_space) {
                current->address_space = old_as;
                vos3_vmm_switch_address_space(old_as);
            } else {
                current->address_space = kernel_space;
                vos3_vmm_switch_address_space(kernel_space);
            }
            vos3_vmm_destroy_address_space(new_as);
            current->mmap_next = old_mmap_next;
            return VOS3_ELF_ERR_INVALID;
        }

        /* CPU enters interpreter, not main program */
        actual_entry = interp_info.entry;
        VOS3_INFO("exec: Interpreter loaded, entry=0x%llx, base=0x%llx",
                  (unsigned long long)interp_info.entry,
                  (unsigned long long)interp_info.base);
    }

    /* Set program break (heap start) in address space — uses MAIN program's brk */
    new_as->brk = elf_info.brk;
    new_as->brk_start = elf_info.brk;

    VOS3_DEBUG("exec: heap_start (brk) = 0x%llx (page-aligned from ELF max_addr)",
               (unsigned long long)new_as->brk_start);

    /* Pre-allocate the first heap page */
    uint64_t first_heap_page = new_as->brk_start & ~(VOS3_PAGE_SIZE - 1ULL);
    uint64_t heap_phys = vos3_pmm_alloc(0);
    if (heap_phys != 0ULL) {
        result = vos3_vmm_map_user(first_heap_page, heap_phys,
                                    VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE |
                                    VOS3_PTE_USER | VOS3_PTE_NO_EXECUTE);
        if (result == 0) {
            void* kaddr = vos3_vmm_get_kernel_addr(first_heap_page);
            if (kaddr != NULL) {
                memset(kaddr, 0, VOS3_PAGE_SIZE);
            }
            VOS3_DEBUG("exec: pre-mapped heap page 0x%llx (USER|WR|NX)",
                       (unsigned long long)first_heap_page);
        } else {
            vos3_pmm_free(heap_phys);
        }
    }

    /* Allocate and zero-initialize user stack */
    uint64_t stack_top = VOS3_USER_STACK_TOP;
    size_t stack_size = EXEC_STACK_SIZE;

    for (size_t i = 0U; i < stack_size / VOS3_PAGE_SIZE; i++) {
        uint64_t page_addr = stack_top - ((i + 1U) * VOS3_PAGE_SIZE);
        uint64_t phys = vos3_pmm_alloc(0);
        if (phys == 0ULL) {
            result = VOS3_ELF_ERR_NOMEM;
            goto exec_rollback;
        }

        result = vos3_vmm_map_user(page_addr, phys,
                                    VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE |
                                    VOS3_PTE_USER | VOS3_PTE_NO_EXECUTE);
        if (result != 0) {
            vos3_pmm_free(phys);
            result = VOS3_ELF_ERR_NOMEM;
            goto exec_rollback;
        }

        void* kaddr = vos3_vmm_get_kernel_addr(page_addr);
        if (kaddr != NULL) {
            memset(kaddr, 0, VOS3_PAGE_SIZE);
        }
    }

    /* Build auxiliary vector when interpreter is present */
    vos3_auxv_t auxv[12];
    size_t auxv_count = 0;
    if (elf_info.has_interp) {
        auxv[auxv_count++] = (vos3_auxv_t){ AT_PHDR,   elf_info.phdr_addr };
        auxv[auxv_count++] = (vos3_auxv_t){ AT_PHENT,  elf_info.phdr_size };
        auxv[auxv_count++] = (vos3_auxv_t){ AT_PHNUM,  elf_info.phdr_num };
        auxv[auxv_count++] = (vos3_auxv_t){ AT_PAGESZ, VOS3_PAGE_SIZE };
        auxv[auxv_count++] = (vos3_auxv_t){ AT_BASE,   interp_info.base };
        auxv[auxv_count++] = (vos3_auxv_t){ AT_ENTRY,  elf_info.entry };
        auxv[auxv_count++] = (vos3_auxv_t){ AT_UID,    current->uid };
        auxv[auxv_count++] = (vos3_auxv_t){ AT_EUID,   current->uid };
        auxv[auxv_count++] = (vos3_auxv_t){ AT_GID,    current->gid };
        auxv[auxv_count++] = (vos3_auxv_t){ AT_EGID,   current->gid };
        auxv[auxv_count++] = (vos3_auxv_t){ AT_RANDOM, 0 };  /* patched by stack setup */
        auxv[auxv_count++] = (vos3_auxv_t){ AT_NULL,   0 };
    }

    /* Set up arguments on stack */
    uint64_t user_sp;
    uint64_t argc;

    /* If no argv provided, use path as argv[0] */
    const char* default_argv[2] = { path, NULL };
    if (argv == NULL) {
        argv = default_argv;
    }

    result = setup_user_stack(stack_top, argv, envp,
                              elf_info.has_interp ? auxv : NULL, auxv_count,
                              &user_sp, &argc);
    if (result != VOS3_ELF_OK) {
        goto exec_rollback;
    }

    /* Commit signal dispositions only after all fallible ELF/stack work.
     * On allocation failure the old image and its dispositions stay intact. */
    if (vos3_signal_task_exec(current) != VOS3_IPC_OK) {
        result = VOS3_ELF_ERR_NOMEM;
        goto exec_rollback;
    }
    if (old_as != NULL && old_as != kernel_space)
        vos3_vmm_destroy_address_space(old_as);

    /* Update task name */
    const char* name = argv[0];
    const char* slash = name;
    while (*name != '\0') {
        if (*name == '/') {
            slash = name + 1;
        }
        name++;
    }

    size_t len = strlen(slash);
    if (len >= VOS3_TASK_NAME_LEN) {
        len = VOS3_TASK_NAME_LEN - 1U;
    }
    memcpy(current->name, slash, len);
    current->name[len] = '\0';

    /* Mark task as user task */
    current->flags &= ~VOS3_TASK_FLAG_KERNEL;
    current->flags |= VOS3_TASK_FLAG_USER;

    /* Store executable path for /proc/self/exe */
    {
        size_t plen = strlen(path);
        if (plen >= sizeof(current->exe_path))
            plen = sizeof(current->exe_path) - 1;
        memcpy(current->exe_path, path, plen);
        current->exe_path[plen] = '\0';
    }

    VOS3_INFO("exec: Starting '%s' at 0x%llx (sp=0x%llx)%s",
              current->name,
              (unsigned long long)actual_entry,
              (unsigned long long)user_sp,
              elf_info.has_interp ? " [via interpreter]" : "");

    /* Jump to user mode - this does not return */
    vos3_jump_to_user(actual_entry, user_sp);

    /* Should never reach here */
    return VOS3_ELF_ERR_INVALID;

exec_rollback:
    current->address_space = old_as != NULL ? old_as : kernel_space;
    vos3_vmm_switch_address_space(current->address_space);
    vos3_vmm_destroy_address_space(new_as);
    current->mmap_next = old_mmap_next;
    return result;
}

/* ============================================================================
 * FORK IMPLEMENTATION
 * ============================================================================ */

/**
 * @brief Fork with user state for proper child return
 *
 * @param frame Syscall frame containing user state
 * @param user_rsp User RSP at syscall
 * @return Child PID to parent, 0 to child, -1 on error
 */
int vos3_fork_with_frame(vos3_syscall_frame_t* frame, uint64_t user_rsp)
{
    vos3_task_t* parent = vos3_sched_current();
    if (parent == NULL || frame == NULL) {
        return -1;
    }

    /* Independent SHM fork needs a separate shared-mapping retain protocol. */
    if (parent->address_space && parent->address_space->shm_count) return -95;
    if (parent->ai_guard_ctx != NULL) return -95; /* No policy-preserving clone API. */

    /* Extract user state from syscall frame */
    uint64_t user_rip = frame->rcx;      /* SYSCALL saves RIP to RCX */
    uint64_t user_rflags = frame->r11;   /* SYSCALL saves RFLAGS to R11 */

    VOS3_DEBUG("fork: parent pid=%u, user_rip=0x%llx, user_rsp=0x%llx",
               parent->pid, (unsigned long long)user_rip,
               (unsigned long long)user_rsp);

    /* Create child task structure */
    vos3_task_t* child = (vos3_task_t*)vos3_kzalloc(sizeof(vos3_task_t));
    if (child == NULL) {
        VOS3_ERROR("fork: failed to allocate child task structure (%zu bytes)",
                   sizeof(vos3_task_t));
        return VOS3_ELF_ERR_NOMEM;
    }

    /* Copy parent task */
    memcpy(child, parent, sizeof(vos3_task_t));
    child->signal_state = NULL; /* Never alias the copied per-task signal state. */
#ifdef NATIVE_SMP_WORKLOAD
    child->native_smp_reported = 0;
#endif
#ifdef NATIVE_SMP_TEST
    child->sched_owner_plus_one = 0;
#endif

    /* FPU: each child must have its own state buffer.
     * Clear copied pointers so #NM handler allocates fresh on first use. */
    child->fpu_state_raw  = NULL;
    child->fpu_state      = NULL;
    child->fpu_initialized = 0;
    child->xsave_area_raw = NULL;
    child->xsave_area = NULL;
    child->xsave_area_size = 0;
    child->ai_guard_ctx = NULL;

    /* Allocate new TID/PID */
    static uint32_t next_pid = 100U;
    child->tid = next_pid++;
    child->pid = child->tid;

    /* Copy name */
    size_t name_len = strlen(parent->name);
    if (name_len > VOS3_TASK_NAME_LEN - 1U) {
        name_len = VOS3_TASK_NAME_LEN - 1U;
    }
    memcpy(child->name, parent->name, name_len);
    child->name[name_len] = '\0';

    /* Allocate new kernel stack via vmap (guard page + mapped pages) */
    uintptr_t guard_va = 0;
    child->kernel_stack = vos3_vmap_stack_alloc(&guard_va);
    if (child->kernel_stack == NULL) {
        VOS3_ERROR("fork: failed to allocate vmap kernel stack");
        vos3_kfree(child);
        return VOS3_ELF_ERR_NOMEM;
    }
    child->kernel_stack_size = VOS3_VMAP_STACK_PAGES * VOS3_PAGE_SIZE;
    child->kernel_stack_guard = guard_va;

    /*
     * Set up child's fork return state.
     * When the child is scheduled, vos3_fork_child_return() will use
     * these values to return to user mode with RAX=0.
     *
     * COW handles user stack separation: parent and child share the same
     * virtual stack address.  On first write by either process, the COW
     * fault handler copies the physical page, giving each process its own
     * private copy.  This is standard fork() behavior (Linux, BSD) and
     * avoids allocating per-child user stacks that pollute the parent's
     * address space and leak page-table pages on repeated fork/exit cycles.
     */
    child->fork_ret_rip = user_rip;
    child->fork_ret_rsp = user_rsp;  /* Same VA — COW handles separation */
    child->fork_ret_rflags = user_rflags;

    /* Callee-saved registers: inherit parent's values unchanged.
     * No adjustment needed since child uses the same virtual stack address. */
    child->fork_ret_rbx = frame->rbx;
    child->fork_ret_rbp = frame->rbp;
    child->fork_ret_r12 = frame->r12;
    child->fork_ret_r13 = frame->r13;
    child->fork_ret_r14 = frame->r14;
    child->fork_ret_r15 = frame->r15;
    /* Caller-saved: zero for fork (security) — only clone needs them */
    child->fork_ret_r8  = 0;
    child->fork_ret_r9  = 0;
    child->fork_ret_r10 = 0;
    child->is_fork_child = 1;

    VOS3_DEBUG("fork: child rbp adjusted: 0x%llx -> 0x%llx",
               (unsigned long long)frame->rbp,
               (unsigned long long)child->fork_ret_rbp);

    /*
     * Set up child's kernel stack and context.
     * The context is placed at the top of the kernel stack (minus sizeof(context)).
     * When scheduled, the context switch will "return" to the RIP stored in context.
     * We set that RIP to vos3_fork_child_return().
     *
     * CRITICAL: System V AMD64 ABI requires RSP to be 16n-8 aligned on function
     * entry (because CALL pushes return address). Since we use RET to "call"
     * fork_child_return, we must arrange for RSP after RET to be 16n-8.
     *
     * Stack layout:
     *   stack_top (16n aligned)
     *   stack_top - 8 = dummy slot (for alignment)
     *   stack_top - 16 to stack_top - 64 = context (56 bytes)
     *
     * After context_switch pops 6 regs (48 bytes) and RETs:
     *   RSP = stack_top - 8 (16n - 8, correct for function entry)
     */
    uint64_t stack_top = (uint64_t)(uintptr_t)child->kernel_stack + child->kernel_stack_size;
    /* Align to 16 bytes */
    stack_top &= ~15ULL;
    /* Reserve 8 bytes for alignment - after ret, RSP will be 16n-8 */
    stack_top -= 8;
    /* Place context on stack */
    child->context = (vos3_context_t*)(stack_top - sizeof(vos3_context_t));

    /* Initialize context - all callee-saved registers to 0 */
    memset(child->context, 0, sizeof(vos3_context_t));

    /* Set RIP to the fork child return trampoline */
    child->context->rip = (uint64_t)(uintptr_t)vos3_fork_child_return;

    /* Set kernel RSP to context location (for context switch) */
    child->kernel_rsp = (uint64_t)(uintptr_t)child->context;

    VOS3_DEBUG("fork: child context at 0x%llx, rip=0x%llx (fork_child_return)",
               (unsigned long long)(uintptr_t)child->context,
               (unsigned long long)child->context->rip);

    child->fd_table = parent->fd_table ? vos3_fd_table_clone(parent->fd_table) : NULL;
    if (parent->fd_table && !child->fd_table) {
        vos3_vmap_stack_free(child->kernel_stack, child->kernel_stack_guard);
        vos3_kfree(child);
        return VOS3_ELF_ERR_NOMEM;
    }
    child->parent = parent;
    child->children = NULL;
    child->sibling = NULL;
    child->is_thread = 0;
    child->flags &= ~VOS3_TASK_FLAG_THREAD;
    child->thread_group_leader = child;
    child->tgid = child->pid;
    child->thread_next = NULL;
    child->clear_child_tid = NULL;

    /* Initialize child state */
    child->state = VOS3_TASK_READY;
    child->next = NULL;
    child->prev = NULL;

    /*
     * Phase 28: Clone address space with Copy-on-Write (COW).
     * This creates a new address space for the child where all writable
     * pages are shared with the parent but marked read-only. When either
     * process writes to a shared page, a COW fault is triggered and the
     * page is copied, giving each process its own private copy.
     */
    child->address_space = vos3_vmm_clone_cow(parent->address_space);
    if (child->address_space == NULL) {
        VOS3_ERROR("fork: failed to clone address space with COW");
        if (child->fd_table) {
            vos3_fd_table_destroy(child->fd_table);
            vos3_kfree(child->fd_table);
        }
        vos3_vmap_stack_free(child->kernel_stack, child->kernel_stack_guard);
        vos3_kfree(child);
        return VOS3_ELF_ERR_NOMEM;
    }

    VOS3_DEBUG("fork: child pid=%u has COW-cloned address space 0x%llx (parent 0x%llx)",
               child->pid,
               (unsigned long long)(uintptr_t)child->address_space,
               (unsigned long long)(uintptr_t)parent->address_space);

    /* Register in task table (needed for signal delivery, task lookup) */
    if (vos3_signal_task_clone(child, parent, 0) != VOS3_IPC_OK ||
        vos3_task_register(child) != 0) {
        vos3_signal_task_destroy(child);
        VOS3_ERROR("fork: failed to register child in task table");
        vos3_vmm_destroy_address_space(child->address_space);
        if (child->fd_table) {
            vos3_fd_table_destroy(child->fd_table);
            vos3_kfree(child->fd_table);
        }
        vos3_vmap_stack_free(child->kernel_stack, child->kernel_stack_guard);
        vos3_kfree(child);
        return VOS3_ELF_ERR_NOMEM;
    }

    child->sibling = parent->children;
    parent->children = child;

    /* Fork-safety: reseed entropy pool so child gets unique CSPRNG state */
    vos3_entropy_reseed();

    /* Add to scheduler */
    vos3_sched_add_task(child);

    VOS3_INFO("fork: Created child pid=%u from parent pid=%u",
              child->pid, parent->pid);

    /* Return child PID to parent */
    return (int)child->pid;
}

/**
 * @brief Legacy fork function (without frame)
 * @deprecated Use vos3_fork_with_frame() for proper child return
 */
int vos3_fork(void)
{
    VOS3_WARN("vos3_fork() called without frame - child may not return correctly");
    /* Return error - should use vos3_fork_with_frame instead */
    return -1;
}

/* ============================================================================
 * WAIT IMPLEMENTATION
 * ============================================================================ */

int vos3_wait(int* status)
{
    return vos3_waitpid(-1, status, 0);
}

int vos3_waitpid(int pid, int* status, int options)
{
    vos3_task_t* parent = vos3_sched_current();
    if (parent == NULL) {
        return -1;
    }

    VOS3_DEBUG("waitpid: parent='%s' pid=%d waiting for child pid=%d options=0x%x",
               parent->name, parent->pid, pid, options);

    for (;;) {
        /*
         * Disable interrupts for the entire check-and-block sequence.
         * CRITICAL: Without this, a child can exit (become ZOMBIE) after we
         * scan the list but before we call vos3_task_block(). Since we're
         * still RUNNING when the child exits, its wake-up check
         * (parent->state == BLOCKED) fails. We then block and never wake.
         *
         * With IRQs disabled: either we see the zombie and collect it, or
         * we atomically transition to BLOCKED so the child's exit will
         * find us BLOCKED and wake us.
         */
        vos3_irqflags_t wait_flags = vos3_irq_save();

        /* Search for matching child */
        vos3_task_t* child = parent->children;
        vos3_task_t* prev = NULL;

        while (child != NULL) {
            /* Check if this is the child we're waiting for */
            int match = 0;
            if (pid == -1) {
                match = 1;  /* Any child */
            } else if (pid > 0 && child->pid == (uint32_t)pid) {
                match = 1;  /* Specific child */
            } else if (pid == 0) {
                /* Same process group */
                if (child->pgid == parent->pgid) {
                    match = 1;
                }
            } else if (pid < -1) {
                /* Specific process group */
                if (child->pgid == (uint32_t)(-pid)) {
                    match = 1;
                }
            }

            if (match != 0 && child->state == VOS3_TASK_ZOMBIE) {
                /* Found terminated child */
                int child_pid = (int)child->pid;
                int child_exit = child->exit_code;

                VOS3_DEBUG("waitpid: found zombie child '%s' pid=%d exit=%d",
                           child->name, child_pid, child_exit);

                if (status != NULL) {
                    /* Build status word (exit code in high byte) */
                    *status = (child_exit & 0xFF) << 8;
                }

                /* Remove from parent's child list */
                if (prev == NULL) {
                    parent->children = child->sibling;
                } else {
                    prev->sibling = child->sibling;
                }

                /* Defer cleanup — reaper will free resources safely */
                vos3_task_defer_destroy(child);

                vos3_irq_restore(wait_flags);

                VOS3_DEBUG("waitpid: returning pid=%d", child_pid);
                return child_pid;
            }

            prev = child;
            child = child->sibling;
        }

        /* No zombie child found */
        if ((options & WNOHANG) != 0) {
            vos3_irq_restore(wait_flags);
            VOS3_DEBUG("waitpid: WNOHANG, no zombie, returning 0");
            return 0;  /* Would block, return immediately */
        }

        if (parent->children == NULL) {
            vos3_irq_restore(wait_flags);
            VOS3_DEBUG("waitpid: no children, returning -1 (ECHILD)");
            return -10;  /* ECHILD - No child processes */
        }

        /* Block and wait for child to exit.
         * IRQs are still disabled — vos3_task_block() sets BLOCKED and
         * yields into the scheduler, which does its own irq_save/restore
         * around the context switch.  When we're woken and resume here,
         * IRQs are still disabled (scheduler restored to our saved state). */
        VOS3_DEBUG("waitpid: blocking parent '%s' waiting for child exit",
                   parent->name);
        vos3_task_block();

        /* Woken up — restore IRQs before looping back to re-scan */
        vos3_irq_restore(wait_flags);

        VOS3_DEBUG("waitpid: parent '%s' woken, checking for zombies",
                   parent->name);
    }
}
