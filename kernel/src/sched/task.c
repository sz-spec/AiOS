/**
 * @file task.c
 * @brief VOS3 Task Management Implementation
 *
 * @details Task creation, destruction, and lifecycle management.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/console.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/string.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/ai_guard.h"
#include "../../include/vos/uaccess.h"
#include "../../include/vos/tee.h"     /* Cyber overlay (Stage 3): vos3_intent_validate self-test at boot */
#include "../../include/vos/sha384.h"  /* Cyber overlay (Stage 6): incremental SHA-384 self-test */
#ifdef NATIVE_SMP_TEST
#include "../../include/vos/percpu.h"
#endif

/* ============================================================================
 * TASK TABLE — Open-addressing hash map (linear probing)
 *
 * VOS3_MAX_TASKS is 1024 (power of 2).  Hash function: tid & (VOS3_MAX_TASKS-1).
 * Collision resolution: linear probing.
 * Deleted slots use a tombstone sentinel so probes don't terminate early.
 * ============================================================================ */

/** @brief Tombstone sentinel for deleted hash table slots */
#define VOS3_TASK_TOMBSTONE  ((vos3_task_t *)1UL)

/** @brief Task table (hash map — open addressing) */
static vos3_task_t* g_task_table[VOS3_MAX_TASKS];

/** @brief Task table lock */
static vos3_spinlock_t g_task_lock = VOS3_SPINLOCK_INIT;

/** @brief Next task ID */
static vos3_atomic32_t g_next_tid = { 0 };

/** @brief Task subsystem initialized flag */
static int g_task_initialized = 0;

/* ============================================================================
 * REAPER QUEUE — Deferred resource cleanup for dead tasks
 *
 * Tasks whose parent has called waitpid() are moved here instead of being
 * freed immediately.  vos3_task_reap() drains the queue from a safe context
 * (idle loop / scheduler tick) where the dead task is guaranteed not to be
 * the current task on any CPU.
 * ============================================================================ */

/** @brief Reaper queue head (singly-linked via ->next) */
static vos3_task_t* g_reaper_head = NULL;

/** @brief Reaper queue lock */
static vos3_spinlock_t g_reaper_lock = VOS3_SPINLOCK_INIT;

/* External assembly functions */
extern void vos3_task_entry_trampoline(void);

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Allocate a task ID
 */
static vos3_tid_t alloc_tid(void)
{
    return (vos3_tid_t)vos3_atomic32_fetch_add(&g_next_tid, 1);
}

/**
 * @brief Convert unsigned integer to string (helper)
 */
static char* uint_to_str(uint32_t val, char* buf, size_t bufsize)
{
    if (bufsize == 0U) {
        return buf;
    }

    char* p = buf + bufsize - 1U;
    *p = '\0';

    if (val == 0U) {
        if (bufsize > 1U) {
            *(--p) = '0';
        }
        return p;
    }

    while (val > 0U && p > buf) {
        *(--p) = (char)('0' + (val % 10U));
        val /= 10U;
    }

    return p;
}

/**
 * @brief Find free slot in task table using hash + linear probe.
 *
 * Hashes the given TID to a starting slot and probes linearly for a
 * NULL or TOMBSTONE entry.  Returns the slot index or -1 if full.
 */
static int find_free_slot(vos3_tid_t tid)
{
    size_t start = (size_t)tid & (VOS3_MAX_TASKS - 1U);
    for (size_t i = 0U; i < VOS3_MAX_TASKS; i++) {
        size_t idx = (start + i) & (VOS3_MAX_TASKS - 1U);
        if (g_task_table[idx] == NULL || g_task_table[idx] == VOS3_TASK_TOMBSTONE) {
            return (int)idx;
        }
    }
    return -1;
}

/**
 * @brief Set up stack for new task
 *
 * Stack layout (growing down):
 *   [high address]
 *   arg (void*)                <- Initial argument
 *   entry (function ptr)       <- Entry function
 *   0 (fake return address)    <- For entry trampoline
 *   0 (r15)                    <- Callee-saved registers
 *   0 (r14)
 *   0 (r13)
 *   0 (r12)
 *   0 (rbx)
 *   0 (rbp)
 *   entry_trampoline (rip)     <- First instruction to execute
 *   [low address]              <- Initial RSP
 */
static vos3_context_t* setup_stack(void* stack_top, vos3_task_entry_t entry, void* arg)
{
    uint64_t* sp = (uint64_t*)stack_top;

    /* Push argument and entry function for trampoline to pop */
    *(--sp) = (uint64_t)arg;
    *(--sp) = (uint64_t)entry;

    /* Return address - entry trampoline that pops entry and arg, then calls entry(arg) */
    *(--sp) = (uint64_t)vos3_task_entry_trampoline;

    /* Push zeroed callee-saved registers (popped by context_switch) */
    *(--sp) = 0ULL;  /* rbp */
    *(--sp) = 0ULL;  /* rbx */
    *(--sp) = 0ULL;  /* r12 */
    *(--sp) = 0ULL;  /* r13 */
    *(--sp) = 0ULL;  /* r14 */
    *(--sp) = 0ULL;  /* r15 */

    /* The stack pointer now points to the context structure */
    return (vos3_context_t*)sp;
}

/**
 * @brief Get time slice for priority
 */
static uint64_t get_time_slice(vos3_task_priority_t priority)
{
    switch (priority) {
        case VOS3_PRIORITY_IDLE:
            return VOS3_SCHED_SLICE_IDLE;
        case VOS3_PRIORITY_LOW:
            return VOS3_SCHED_SLICE_LOW;
        case VOS3_PRIORITY_NORMAL:
            return VOS3_SCHED_SLICE_NORMAL;
        case VOS3_PRIORITY_HIGH:
            return VOS3_SCHED_SLICE_HIGH;
        case VOS3_PRIORITY_REALTIME:
            return VOS3_SCHED_SLICE_REALTIME;
        default:
            return VOS3_SCHED_SLICE_NORMAL;
    }
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

int vos3_task_init(void)
{
    if (g_task_initialized != 0) {
        return VOS3_TASK_ERR_INVALID;
    }

    /* Clear task table */
    for (size_t i = 0U; i < VOS3_MAX_TASKS; i++) {
        g_task_table[i] = NULL;
    }

    vos3_atomic32_store(&g_next_tid, 0);

    g_task_initialized = 1;

    VOS3_DEBUG("Task subsystem initialized (max %zu tasks)", VOS3_MAX_TASKS);

    /* Cyber overlay (Stage 3): IntentManifest validator boot self-test.
     *
     * vos.v1 does not yet expose a userspace IntentManifest receiver.
     * Until a real syscall/VBus path is wired, run a once-only negative
     * self-test at task-subsystem init: hand the validator a NULL input
     * and verify it returns VOS3_INTENT_E_NULL. This (a) confirms the
     * validator correctly rejects malformed input on this build, and
     * (b) keeps vos3_intent_validate live against linker GC so the
     * symbol is present in vos3.elf for attestation/audit tools to find.
     *
     * Cost: ~10 ns once at boot. Failure is logged, never fatal — the
     * validator API contract is the artefact under test, not the kernel.
     */
    {
        uint8_t digest_unused[48];
        const int rc = vos3_intent_validate(NULL, 0, digest_unused);
        if (rc == VOS3_INTENT_E_NULL) {
            VOS3_DEBUG("[CYBER] IntentManifest validator self-test PASS (rc=%d)", rc);
        } else {
            VOS3_ERROR("[CYBER] IntentManifest validator self-test FAIL — expected %d, got %d",
                       VOS3_INTENT_E_NULL, rc);
        }
    }

    /* Cyber overlay (Stage 6): SHA-384 incremental-vs-one-shot parity test.
     *
     * Verifies that the streaming API (init/update/final) and the
     * convenience one-shot vos3_sha384() produce bit-identical digests.
     * Catches a class of bugs (mis-buffered partial blocks, length-field
     * encoding, final-padding) at boot rather than at first crypto use.
     *
     * Also defeats linker GC for the streaming API — without this call,
     * vos3_sha384_init was being stripped because no production caller
     * uses the streaming flavour yet.
     *
     * Cost: one 11-byte hash, ≈ 1 µs once at boot. Failure logs ERROR
     * and continues — the kernel is functional either way; the test is
     * a correctness probe of the SHA-384 module itself.
     */
    {
        const uint8_t input[]  = "vos.v1-Cyber";   /* 12 bytes incl. NUL? we use sizeof - 1 */
        const size_t  in_len   = sizeof(input) - 1U;
        uint8_t one_shot[48];
        uint8_t streamed[48];

        vos3_sha384(input, in_len, one_shot);

        vos3_sha384_ctx_t ctx;
        vos3_sha384_init(&ctx);
        /* Split across two updates to actually exercise the buffering. */
        vos3_sha384_update(&ctx, input,           5U);
        vos3_sha384_update(&ctx, input + 5U,      in_len - 5U);
        vos3_sha384_final(&ctx, streamed);

        int match = 1;
        for (uint32_t i = 0U; i < VOS3_SHA384_DIGEST_SIZE; i++) {
            if (one_shot[i] != streamed[i]) { match = 0; break; }
        }
        if (match) {
            VOS3_DEBUG("[CYBER] SHA-384 streaming-vs-one-shot self-test PASS");
        } else {
            VOS3_ERROR("[CYBER] SHA-384 streaming-vs-one-shot self-test FAIL — digests differ");
        }
    }

    /* Stage 14 — SHAKE-128/256 KAT self-test.
     *
     * Foundation primitive for the ML-KEM-768 lattice port (Stage 14.B.2).
     * Real Keccak-f[1600] permutation; KATs validate codegen. Failure here
     * would point to a toolchain regression rather than a logic bug.
     *
     * Also defeats linker --gc-sections on the SHAKE module by giving it
     * a call from a TU referenced by boot init.
     */
    {
        extern int vos3_mlkem768_self_test(void);
        const int rc = vos3_mlkem768_self_test();
        if (rc == 0) {
            VOS3_DEBUG("[CYBER] SHAKE-128/256 KAT self-test PASS (ML-KEM lattice ops STUBBED)");
        } else {
            VOS3_ERROR("[CYBER] SHAKE-128/256 KAT self-test FAIL rc=%d", rc);
        }
    }

    return VOS3_TASK_OK;
}

vos3_task_t* vos3_task_create(const char* name,
                               vos3_task_entry_t entry,
                               void* arg,
                               vos3_task_priority_t priority)
{
    if (g_task_initialized == 0) {
        VOS3_ERROR("[TASK] Task system not initialized");
        return NULL;
    }

    if (entry == NULL) {
        VOS3_ERROR("[TASK] Entry point is NULL");
        return NULL;
    }

    /* Allocate task structure */
    vos3_task_t* task = (vos3_task_t*)vos3_kzalloc(sizeof(vos3_task_t));
    if (task == NULL) {
        VOS3_ERROR("[TASK] Failed to allocate task struct (%zu bytes)", sizeof(vos3_task_t));
        return NULL;
    }

    /* Allocate kernel stack via vmap (guard page + mapped pages) */
    uintptr_t guard_va = 0;
    task->kernel_stack = vos3_vmap_stack_alloc(&guard_va);
    if (task->kernel_stack == NULL) {
        VOS3_ERROR("[TASK] Failed to allocate vmap kernel stack");
        vos3_kfree(task);
        return NULL;
    }
    task->kernel_stack_size = VOS3_VMAP_STACK_PAGES * VOS3_PAGE_SIZE;
    task->kernel_stack_guard = guard_va;

    /* [OLYMPUS-FIX APEX-HOME E1 — LOGIC-COMPLETE v21.2.2]
     * Allocate the per-task XSAVE area. The size comes from runtime
     * CPUID(0xD,0).ECX via vos3_xsave_get_area_size(). On a host that
     * does not advertise XSAVE (or before the probe has run), the
     * size is 0 and the area is left NULL — this matches the v20.x
     * behavior where no extended-register save is performed.
     *
     * We over-allocate by 64 bytes so we can hand out a 64-byte
     * aligned pointer (XSAVE requires 64-byte alignment per Intel
     * SDM Vol.1 §13.7). The raw kzalloc pointer is stored for
     * vos3_kfree() at task_destroy time.
     *
     * IMPORTANT: this allocation prepares the area; it does not
     * yet save/restore on context switch — that is a separate
     * v21.3.x-XSAVE-WIRE assembly edit. The buffer sits dormant
     * until context_switch.S is updated to use it. */
    extern uint32_t vos3_xsave_get_area_size(void);
    {
        uint32_t xsv_size = vos3_xsave_get_area_size();
        if (xsv_size > 0u) {
            uint8_t *raw = (uint8_t *)vos3_kzalloc(xsv_size + 64u);
            if (raw != NULL) {
                uintptr_t aligned =
                    ((uintptr_t)raw + 63u) & ~((uintptr_t)63u);
                task->xsave_area_raw  = raw;
                task->xsave_area      = (void *)aligned;
                task->xsave_area_size = xsv_size;
            }
            /* If allocation fails, leave fields NULL/0 — the task
             * still runs; only AVX-512 SIMD across context switch
             * is unavailable for this task (which today is unused
             * because context_switch.S hasn't been updated yet). */
        }
    }

    /* Calculate stack top (stack grows down) */
    void* stack_top = (void*)((uintptr_t)task->kernel_stack + task->kernel_stack_size);

    /* Set up initial context on stack */
    task->context = setup_stack(stack_top, entry, arg);
    task->kernel_rsp = (uint64_t)(uintptr_t)task->context;

    /* Initialize task identity */
    task->tid = alloc_tid();
    task->pid = task->tid;  /* For kernel tasks, pid == tid */

    if (name != NULL) {
        size_t len = strlen(name);
        if (len >= VOS3_TASK_NAME_LEN) {
            len = VOS3_TASK_NAME_LEN - 1U;
        }
        memcpy(task->name, name, len);
        task->name[len] = '\0';
    } else {
        /* Generate default name: "task_<tid>" */
        char num_buf[12];
        const char* num = uint_to_str(task->tid, num_buf, sizeof(num_buf));
        static const char prefix[] = "task_";
        size_t prefix_len = sizeof(prefix) - 1U;
        size_t num_len = strlen(num);
        if (prefix_len + num_len < VOS3_TASK_NAME_LEN) {
            memcpy(task->name, prefix, prefix_len);
            memcpy(task->name + prefix_len, num, num_len);
            task->name[prefix_len + num_len] = '\0';
        } else {
            memcpy(task->name, prefix, prefix_len);
            task->name[prefix_len] = '\0';
        }
    }

    /* Initialize state */
    task->state = VOS3_TASK_READY;
    task->flags = VOS3_TASK_FLAG_KERNEL;
    task->priority = priority;
    task->cpu_id = 0U;

    /* Initialize scheduling */
    task->time_slice = get_time_slice(priority);
    task->total_runtime = 0ULL;
    task->last_scheduled = 0ULL;
    task->wake_time = 0ULL;

    /* Cyber overlay (Stage 3): SCHED_CORE cookie init.
     * Cookie 0 = "neutral" — task can co-execute with any sibling on the
     * same physical core. AI-slot activation will override this with a
     * trust-domain-specific cookie via vos3_sched_set_cookie() so that
     * tasks from different owners cannot share a core.
     * The call also keeps vos3_sched_set_cookie alive against linker GC. */
    (void)vos3_sched_set_cookie(task, 0ULL);

    /* Initialize lists */
    task->next = NULL;
    task->prev = NULL;
    task->parent = NULL;
    task->children = NULL;
    task->sibling = NULL;

    /* Initialize stats */
    task->context_switches = 0ULL;
    task->voluntary_switches = 0ULL;
    task->involuntary_switches = 0ULL;

    /* Initialize address space - kernel tasks use kernel address space */
    task->address_space = vos3_vmm_get_kernel_space();

    /* Phase v17 (K-C5): Per-process mmap bump allocator base */
    task->mmap_next = 0x0000000030000000ULL;  /* VOS3_MMAP_BASE */

    /* Initialize file descriptor table */
    task->fd_table = (vos3_fd_table_t*)vos3_kzalloc(sizeof(vos3_fd_table_t));
    if (task->fd_table != NULL) {
        (void)vos3_fd_table_init(task->fd_table);
    }
    task->cwd = NULL;  /* Will be set to root after VFS init */

    /* Initialize AI Guard context (lazy - NULL until first use) */
    task->ai_guard_ctx = NULL;

    /* Initialize app isolation fields (Phase N) */
    task->app_id = 0U;
    task->cpu_ticks_used = 0ULL;
    task->cpu_ticks_limit = 0ULL;

    /* Add to task table (hash map — keyed by tid) */
    vos3_spinlock_lock(&g_task_lock);

    int slot = find_free_slot(task->tid);
    if (slot < 0) {
        vos3_spinlock_unlock(&g_task_lock);
        vos3_vmap_stack_free(task->kernel_stack, task->kernel_stack_guard);
        vos3_kfree(task);
        return NULL;
    }

    g_task_table[slot] = task;

    vos3_spinlock_unlock(&g_task_lock);

    VOS3_DEBUG("Created task '%s' (tid=%u, priority=%u, slot=%d)",
               task->name, task->tid, task->priority, slot);

    return task;
}

vos3_task_t* vos3_task_create_idle(uint32_t cpu_id)
{
    char name[VOS3_TASK_NAME_LEN];
    char num_buf[12];
    const char* num = uint_to_str(cpu_id, num_buf, sizeof(num_buf));
    strcpy(name, "idle/");
    strcpy(name + 5U, num);

    /* Create idle task using the assembly idle loop */
    vos3_task_t* task = vos3_task_create(name, vos3_idle_loop, NULL, VOS3_PRIORITY_IDLE);
    if (task == NULL) {
        return NULL;
    }

    /* Mark as idle task */
    task->flags |= VOS3_TASK_FLAG_IDLE;
    task->cpu_id = cpu_id;
#ifdef NATIVE_SMP_TEST
    task->sched_owner_plus_one = cpu_id + 1;
#endif

    VOS3_DEBUG("Created idle task for CPU %u", cpu_id);

    return task;
}

void vos3_task_destroy(vos3_task_t* task)
{
    if (task == NULL) {
        return;
    }

    /* Cannot destroy current task */
#ifdef NATIVE_SMP_TEST
    if (task->sched_owner_plus_one != 0 &&
        task->sched_owner_plus_one != get_cpu_id() + 1) {
        VOS3_ERROR("Cannot directly destroy a remote-owned task");
        return;
    }
#endif
    if (task == vos3_sched_current()) {
        VOS3_ERROR("Cannot destroy current task");
        return;
    }

    vos3_spinlock_lock(&g_task_lock);

    /* Remove from task table — set TOMBSTONE for hash probe continuity */
    size_t d_start = (size_t)task->tid & (VOS3_MAX_TASKS - 1U);
    for (size_t i = 0U; i < VOS3_MAX_TASKS; i++) {
        size_t idx = (d_start + i) & (VOS3_MAX_TASKS - 1U);
        if (g_task_table[idx] == task) {
            g_task_table[idx] = VOS3_TASK_TOMBSTONE;
            break;
        }
        if (g_task_table[idx] == NULL) {
            break;  /* Not found — probe chain ended */
        }
    }

    vos3_spinlock_unlock(&g_task_lock);

    /* Free resources */
    if (task->kernel_stack != NULL) {
        vos3_vmap_stack_free(task->kernel_stack, task->kernel_stack_guard);
    }
    if (task->user_stack != NULL) {
        vos3_kfree(task->user_stack);
    }
    /* Decrement fd_table ref_count with CAS; destroy only on 1→0 transition */
    if (task->fd_table != NULL) {
        uint32_t expected = __atomic_load_n(&task->fd_table->ref_count, __ATOMIC_ACQUIRE);
        while (expected > 0U) {
            uint32_t desired = expected - 1U;
            if (__atomic_compare_exchange_n(&task->fd_table->ref_count, &expected, desired,
                                            0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
                if (desired == 0U) {
                    vos3_fd_table_destroy(task->fd_table);
                    vos3_kfree(task->fd_table);
                }
                break;
            }
            /* CAS failed — expected was reloaded, retry */
        }
    }

    /* Cleanup AI Guard context */
    if (task->ai_guard_ctx != NULL) {
        vos3_ai_guard_ctx_destroy(task->ai_guard_ctx);
        task->ai_guard_ctx = NULL;
    }

    /* Release FPU ownership and free state buffer */
    vos3_fpu_release_owner(task);
    if (task->fpu_state_raw != NULL) {
        vos3_kfree(task->fpu_state_raw);
        task->fpu_state_raw = NULL;
        task->fpu_state = NULL;
    }

    /* [OLYMPUS-FIX APEX-HOME E1] free the per-task XSAVE area. */
    if (task->xsave_area_raw != NULL) {
        vos3_kfree(task->xsave_area_raw);
        task->xsave_area_raw  = NULL;
        task->xsave_area      = NULL;
        task->xsave_area_size = 0u;
    }

    /* Every task owns one reference, including CLONE_VM children. */
    vos3_vmm_destroy_address_space(task->address_space);
    task->address_space = NULL;

    VOS3_DEBUG("Destroyed task '%s' (tid=%u)", task->name, task->tid);

    vos3_kfree(task);
}

void vos3_task_defer_destroy(vos3_task_t* task)
{
    if (task == NULL) {
        return;
    }

    /* Remove from task table so no new lookups find it — TOMBSTONE for probe continuity */
    vos3_spinlock_lock(&g_task_lock);

    size_t dd_start = (size_t)task->tid & (VOS3_MAX_TASKS - 1U);
    for (size_t i = 0U; i < VOS3_MAX_TASKS; i++) {
        size_t idx = (dd_start + i) & (VOS3_MAX_TASKS - 1U);
        if (g_task_table[idx] == task) {
            g_task_table[idx] = VOS3_TASK_TOMBSTONE;
            break;
        }
        if (g_task_table[idx] == NULL) {
            break;  /* Not found — probe chain ended */
        }
    }

    vos3_spinlock_unlock(&g_task_lock);

    /* Mark as dead — distinct from ZOMBIE */
    task->state = VOS3_TASK_DEAD;
    task->reap_after_tick = vos3_sched_get_ticks() + 2;  /* Wait 2 ticks minimum */

    /* Enqueue for deferred cleanup */
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_reaper_lock);

    task->next = g_reaper_head;
    g_reaper_head = task;

    vos3_spinlock_unlock(&g_reaper_lock);
    vos3_irq_restore(flags);

    VOS3_DEBUG("Deferred destroy of task '%s' (tid=%u)", task->name, task->tid);
}

void vos3_task_reap(void)
{
    /* Quick check without locking */
    if (g_reaper_head == NULL) {
        return;
    }

    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_reaper_lock);

    /* Steal the entire list */
    vos3_task_t* list = g_reaper_head;
    g_reaper_head = NULL;

    vos3_spinlock_unlock(&g_reaper_lock);
    vos3_irq_restore(flags);

    vos3_task_t* current = vos3_sched_current();
    uint64_t now = vos3_sched_get_ticks();
    vos3_task_t* deferred = NULL;

    /* Free resources for each dead task (outside lock) */
    while (list != NULL) {
        vos3_task_t* task = list;
        list = list->next;

        /* Not ready yet or still current — re-defer */
        if (now < task->reap_after_tick || task == current
#ifdef NATIVE_SMP_TEST
            || (task->sched_owner_plus_one != 0 &&
                task->sched_owner_plus_one != get_cpu_id() + 1)
#endif
        ) {
            task->next = deferred;
            deferred = task;
            continue;
        }

        task->next = NULL;

        VOS3_DEBUG("Reaping task '%s' (tid=%u)", task->name, task->tid);

        if (task->kernel_stack != NULL) {
            vos3_vmap_stack_free(task->kernel_stack, task->kernel_stack_guard);
        }
        if (task->user_stack != NULL) {
            vos3_kfree(task->user_stack);
        }
        /* Decrement fd_table ref_count with CAS; destroy only on 1→0 transition */
        if (task->fd_table != NULL) {
            uint32_t expected = __atomic_load_n(&task->fd_table->ref_count, __ATOMIC_ACQUIRE);
            while (expected > 0U) {
                uint32_t desired = expected - 1U;
                if (__atomic_compare_exchange_n(&task->fd_table->ref_count, &expected, desired,
                                                0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
                    if (desired == 0U) {
                        vos3_fd_table_destroy(task->fd_table);
                        vos3_kfree(task->fd_table);
                    }
                    break;
                }
            }
        }
        if (task->ai_guard_ctx != NULL) {
            vos3_ai_guard_ctx_destroy(task->ai_guard_ctx);
        }
        /* Release FPU ownership and free state buffer */
        vos3_fpu_release_owner(task);
        if (task->fpu_state_raw != NULL) {
            vos3_kfree(task->fpu_state_raw);
        }
        /* Final address-space release owns SHM mapping cleanup. */
        vos3_vmm_destroy_address_space(task->address_space);
        task->address_space = NULL;

        vos3_kfree(task);
    }

    /* Re-enqueue deferred tasks */
    if (deferred != NULL) {
        flags = vos3_irq_save();
        vos3_spinlock_lock(&g_reaper_lock);

        vos3_task_t* tail = deferred;
        while (tail->next != NULL) { tail = tail->next; }
        tail->next = g_reaper_head;
        g_reaper_head = deferred;

        vos3_spinlock_unlock(&g_reaper_lock);
        vos3_irq_restore(flags);
    }
}

vos3_task_t* vos3_task_current(void)
{
    return vos3_sched_current();
}

int vos3_task_register(vos3_task_t* task)
{
    if (task == NULL) {
        return -1;
    }

    vos3_spinlock_lock(&g_task_lock);

    int slot = find_free_slot(task->tid);
    if (slot < 0) {
        vos3_spinlock_unlock(&g_task_lock);
        VOS3_ERROR("task_register: task table full (tid=%u)", task->tid);
        return -1;
    }

    g_task_table[slot] = task;

    vos3_spinlock_unlock(&g_task_lock);

    VOS3_DEBUG("Registered task '%s' (tid=%u) in slot %d", task->name, task->tid, slot);
    return 0;
}

vos3_task_t* vos3_task_get(vos3_tid_t tid)
{
    vos3_spinlock_lock(&g_task_lock);

    size_t start = (size_t)tid & (VOS3_MAX_TASKS - 1U);
    for (size_t i = 0U; i < VOS3_MAX_TASKS; i++) {
        size_t idx = (start + i) & (VOS3_MAX_TASKS - 1U);
        vos3_task_t* entry = g_task_table[idx];
        if (entry == NULL) {
            /* Empty slot — TID not in table (probe chain ends) */
            break;
        }
        if (entry != VOS3_TASK_TOMBSTONE && entry->tid == tid) {
            vos3_spinlock_unlock(&g_task_lock);
            return entry;
        }
        /* TOMBSTONE or different TID — continue probing */
    }

    vos3_spinlock_unlock(&g_task_lock);
    return NULL;
}

vos3_task_t* vos3_task_find_by_pid(vos3_pid_t pid)
{
    vos3_spinlock_lock(&g_task_lock);

    /* PID is not the hash key — must do full scan, skipping tombstones */
    for (size_t i = 0U; i < VOS3_MAX_TASKS; i++) {
        if (g_task_table[i] != NULL &&
            g_task_table[i] != VOS3_TASK_TOMBSTONE &&
            g_task_table[i]->pid == pid) {
            vos3_task_t* task = g_task_table[i];
            vos3_spinlock_unlock(&g_task_lock);
            return task;
        }
    }

    vos3_spinlock_unlock(&g_task_lock);
    return NULL;
}

void vos3_task_set_state(vos3_task_t* task, vos3_task_state_t state)
{
    if (task == NULL) {
        return;
    }

    vos3_task_state_t old_state = task->state;
    task->state = state;

    VOS3_DEBUG("Task '%s' state: %s -> %s",
               task->name,
               vos3_task_state_name(old_state),
               vos3_task_state_name(state));
}

void vos3_task_set_priority(vos3_task_t* task, vos3_task_priority_t priority)
{
    if (task == NULL || priority >= VOS3_PRIORITY_COUNT) {
        return;
    }

    task->priority = priority;
    task->time_slice = get_time_slice(priority);
}

void vos3_task_yield(void)
{
    vos3_task_t* current = vos3_sched_current();
    if (current != NULL) {
        current->voluntary_switches++;
    }
    vos3_sched_yield();
}

void vos3_task_sleep(uint64_t ticks)
{
    if (ticks == 0ULL) {
        return;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return;
    }

    /* Disable interrupts during sleep setup to prevent the timer ISR
     * from seeing the task with state=SLEEPING before it's in the sleep
     * queue. Without this, the ISR's reschedule could context-switch away
     * from a task that isn't in any queue, losing it forever. */
    vos3_irqflags_t flags = vos3_irq_save();

    uint64_t wake_time = vos3_sched_get_ticks() + ticks;
    current->wake_time = wake_time;
    current->state = VOS3_TASK_SLEEPING;

    vos3_sched_sleep_until(current, wake_time);

    vos3_irq_restore(flags);

    /* Yield to next task — reschedule has its own irq_save protection. */
    vos3_sched_yield();
}

void vos3_task_sleep_ms(uint64_t ms)
{
    uint64_t ticks = (ms + VOS3_MS_PER_TICK - 1ULL) / VOS3_MS_PER_TICK;
    vos3_task_sleep(ticks);
}

void vos3_task_wake(vos3_task_t* task)
{
    if (task == NULL) {
        return;
    }

    if (task->state == VOS3_TASK_SLEEPING || task->state == VOS3_TASK_BLOCKED) {
        task->state = VOS3_TASK_READY;
        task->wake_time = 0ULL;
        vos3_sched_add_task(task);
    }
}

__attribute__((noreturn))
void vos3_task_exit(int exit_code)
{
    vos3_task_t* current = vos3_sched_current();

    /*
     * Disable interrupts for the entire exit sequence + first yield.
     * CRITICAL: Without this, a timer ISR can preempt us after we set
     * state=ZOMBIE but before we context-switch away. Since ZOMBIE tasks are
     * never re-enqueued, the parent wake-up would be lost forever, causing
     * the parent to stay BLOCKED indefinitely. Keeping IRQs off through the
     * first yield also prevents the reaper from freeing our kernel stack
     * while our frozen context is still on it.
     */
    vos3_irqflags_t exit_flags = vos3_irq_save();

    if (current != NULL) {
        VOS3_INFO("Task '%s' (pid=%u) exiting with code %d",
                  current->name, current->pid, exit_code);

        current->exit_code = exit_code;
        current->state = VOS3_TASK_ZOMBIE;

        /* Remove from scheduler run queue */
        vos3_sched_remove_task(current);

        /* Wake parent if it's waiting in waitpid() (fork'd processes) */
        if (!current->is_thread) {
            vos3_task_t* parent = current->parent;
            if (parent != NULL && parent->state == VOS3_TASK_BLOCKED) {
                VOS3_DEBUG("Task '%s' waking parent '%s' (pid=%u)",
                           current->name, parent->name, parent->pid);
                vos3_task_unblock(parent);
            }
        }

        /* Reparent children to init (pid 1).
         * Hold g_task_lock across the entire reparenting to prevent SMP
         * races with concurrent waitpid() iterating init->children. */
        if (current->children != NULL) {
            vos3_spinlock_lock(&g_task_lock);
            /* Direct table scan — vos3_task_find_by_pid() would re-acquire
             * g_task_lock and deadlock. AP idle tasks can also have numeric
             * PID 1; only the live user init can adopt process children. */
            vos3_task_t* init = NULL;
            for (size_t ri = 0U; ri < VOS3_MAX_TASKS; ri++) {
                if (g_task_table[ri] != NULL &&
                    g_task_table[ri] != VOS3_TASK_TOMBSTONE &&
                    g_task_table[ri] != current &&
                    g_task_table[ri]->pid == 1 &&
                    (g_task_table[ri]->flags & VOS3_TASK_FLAG_USER) &&
                    !(g_task_table[ri]->flags & VOS3_TASK_FLAG_IDLE) &&
                    g_task_table[ri]->state != VOS3_TASK_ZOMBIE &&
                    g_task_table[ri]->state != VOS3_TASK_DEAD) {
                    init = g_task_table[ri];
                    break;
                }
            }
            vos3_task_t* child = current->children;

            while (child != NULL) {
                vos3_task_t* next_child = child->sibling;
                child->parent = init;

                /* Add to init's children list */
                if (init != NULL) {
                    child->sibling = init->children;
                    init->children = child;
                } else {
                    child->sibling = NULL;
                }

                child = next_child;
            }
            current->children = NULL;
            vos3_spinlock_unlock(&g_task_lock);
        }

        /* CLONE_CHILD_CLEARTID: conditionally write 0 to *clear_child_tid
         * and futex_wake so that pthread_join() unblocks.
         *
         * CRITICAL: Only write 0 if the current value equals this thread's
         * TID.  musl sets clear_child_tid = &__thread_list_lock, which
         * holds lock-state values (0/1/2), never a TID.  Unconditionally
         * writing 0 corrupts the lock when another thread holds it (the
         * window between user-space __tl_unlock and kernel task_exit is
         * wide on single-CPU QEMU).  By checking tid first, we preserve
         * correct behaviour for TID-based clear_child_tid users while
         * avoiding lock corruption for musl's __thread_list_lock usage.
         *
         * Always wake ALL waiters (INT_MAX) to compensate for any missed
         * user-space wakes caused by previous lock-state corruption. */
        if (current->clear_child_tid != NULL) {
            volatile uint32_t* tidptr = current->clear_child_tid;
            if ((uintptr_t)tidptr < 0xFFFF800000000000ULL &&
                ((uintptr_t)tidptr & 3U) == 0U) {
                uint32_t cur_val = *(volatile uint32_t*)tidptr;
                if (cur_val == (uint32_t)current->tid) {
                    uint32_t zero = 0;
                    copy_to_user((void*)tidptr, &zero, sizeof(zero));
                }
                extern int64_t vos3_futex_wake_addr(volatile uint32_t* uaddr, int count);
                vos3_futex_wake_addr(tidptr, 0x7FFFFFFF);
            }
            current->clear_child_tid = NULL;
        }

        /* Thread fallback: wake parent after CLONE_CHILD_CLEARTID.
         * The futex wake above fires on clear_child_tid (musl's __thread_list_lock),
         * NOT on &t->detach_state which pthread_join waits on. The user-space
         * __wake from __pthread_exit is the primary path, but if it didn't
         * reach the parent (timing), this unblock is the deterministic fallback.
         * vos3_task_unblock checks state==BLOCKED, so it's a no-op if the
         * parent was already woken by the futex path — no double-signaling. */
        if (current->is_thread) {
            vos3_task_t* parent = current->parent;
            if (parent != NULL && parent->state == VOS3_TASK_BLOCKED) {
                vos3_task_unblock(parent);
            }
        }

        /*
         * Threads are detached — no parent will call waitpid(), so the
         * ZOMBIE task struct and kernel stack would otherwise leak forever.
         * Defer-destroy immediately: sets state=DEAD, enqueues to reaper
         * queue (freed 2+ ticks later, safely after context-switch away).
         * Regular fork()d processes remain ZOMBIE until their parent's
         * waitpid() calls vos3_task_defer_destroy().
         */
        if (current->is_thread) {
            vos3_task_t* par = current->parent;
            if (par != NULL) {
                /* Unlink from parent's children/sibling list */
                vos3_task_t** pp = &par->children;
                while (*pp != NULL) {
                    if (*pp == current) {
                        *pp = current->sibling;
                        break;
                    }
                    pp = &(*pp)->sibling;
                }
                current->sibling = NULL;

                /* Unlink from parent's thread_next chain */
                vos3_task_t** tp = &par->thread_next;
                while (*tp != NULL) {
                    if (*tp == current) {
                        *tp = current->thread_next;
                        break;
                    }
                    tp = &(*tp)->thread_next;
                }
                current->thread_next = NULL;
            }

            vos3_task_defer_destroy(current);
        }

    }

    /* First yield with IRQs disabled — prevents timer preemption between
     * state=ZOMBIE and the context switch away.  vos3_sched_reschedule()
     * does its own irq_save/restore; the NEW task gets its own EFLAGS. */
    vos3_sched_yield();

    /* Safety: if ever re-scheduled (shouldn't happen), enable IRQs */
    vos3_irq_restore(exit_flags);
    for (;;) {
        vos3_sched_yield();
    }
}

void vos3_task_kill(vos3_task_t* task, int signal)
{
    if (task == NULL) {
        return;
    }

    /* Already dead or zombie — nothing to do */
    if (task->state == VOS3_TASK_ZOMBIE || task->state == VOS3_TASK_DEAD) {
        return;
    }

    VOS3_INFO("[TASK] Killing task '%s' (pid=%u) with signal %d",
              task->name, task->pid, signal);

    /* If the task is blocked or sleeping, wake it so the scheduler can
     * remove it cleanly.  We set state=READY first; it will be immediately
     * moved to ZOMBIE below. */
    if (task->state == VOS3_TASK_BLOCKED || task->state == VOS3_TASK_SLEEPING) {
        task->wake_time = 0ULL;
        task->state = VOS3_TASK_READY;
    }

    /* Set exit code and transition to zombie */
    task->exit_code = -signal;
    task->state = VOS3_TASK_ZOMBIE;

    /* Remove from scheduler run queue */
    vos3_sched_remove_task(task);

    /* Wake parent if it's blocked (e.g. in waitpid) */
    vos3_task_t* parent = task->parent;
    if (parent != NULL && parent->state == VOS3_TASK_BLOCKED) {
        vos3_task_unblock(parent);
    }
}

void vos3_task_block(void)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return;
    }

    current->state = VOS3_TASK_BLOCKED;
    vos3_sched_remove_task(current);
    vos3_sched_yield();
}

void vos3_task_unblock(vos3_task_t* task)
{
    if (task == NULL || task->state != VOS3_TASK_BLOCKED) {
        return;
    }

    task->state = VOS3_TASK_READY;
    vos3_sched_add_task(task);
}

/* ============================================================================
 * APP ISOLATION (Phase N)
 * ============================================================================ */

int vos3_task_set_app_mode(vos3_task_t* task, uint8_t app_id)
{
    if (task == NULL) {
        return VOS3_TASK_ERR_INVALID;
    }

    if (app_id >= 8U) {
        VOS3_ERROR("[TASK] Invalid app_id %u (max 7)", app_id);
        return VOS3_TASK_ERR_INVALID;
    }

    task->flags |= VOS3_TASK_FLAG_APP;
    task->app_id = app_id;

    VOS3_INFO("[TASK] Task '%s' (pid=%u) set to app mode (app_id=%u)",
              task->name, task->pid, app_id);

    return VOS3_TASK_OK;
}

void vos3_task_set_cpu_limit(vos3_task_t* task, uint64_t limit)
{
    if (task == NULL) {
        return;
    }
    task->cpu_ticks_limit = limit;

    VOS3_DEBUG("[TASK] Task '%s' CPU limit set to %llu ticks",
               task->name, (unsigned long long)limit);
}

void vos3_task_check_cpu_quota(void)
{
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return;
    }

    /* Only enforce on app tasks with a limit set */
    if (!(current->flags & VOS3_TASK_FLAG_APP)) {
        return;
    }

    /* Increment tick count */
    current->cpu_ticks_used++;

    /* Check against limit */
    if (current->cpu_ticks_limit > 0U &&
        current->cpu_ticks_used >= current->cpu_ticks_limit) {

        VOS3_WARN("[TASK] App task '%s' (pid=%u) exceeded CPU quota "
                  "(%llu/%llu ticks) — forcing exit",
                  current->name, current->pid,
                  (unsigned long long)current->cpu_ticks_used,
                  (unsigned long long)current->cpu_ticks_limit);

        /* Force task exit */
        current->exit_code = -9;  /* SIGKILL equivalent */
        current->state = VOS3_TASK_ZOMBIE;
        vos3_sched_remove_task(current);

        /* Wake parent so waitpid() doesn't block forever on this zombie */
        if (!current->is_thread && current->parent != NULL &&
            current->parent->state == VOS3_TASK_BLOCKED) {
            vos3_task_unblock(current->parent);
        }
    }
}

void vos3_task_count_stats(uint32_t* nr_tasks, uint32_t* nr_zombies)
{
    uint32_t tasks = 0;
    uint32_t zombies = 0;
    vos3_spinlock_lock(&g_task_lock);
    for (size_t i = 0U; i < VOS3_MAX_TASKS; i++) {
        if (g_task_table[i] != NULL && g_task_table[i] != VOS3_TASK_TOMBSTONE) {
            tasks++;
            if (g_task_table[i]->state == VOS3_TASK_ZOMBIE) {
                zombies++;
            }
        }
    }
    vos3_spinlock_unlock(&g_task_lock);
    if (nr_tasks)   *nr_tasks   = tasks;
    if (nr_zombies) *nr_zombies = zombies;
}
