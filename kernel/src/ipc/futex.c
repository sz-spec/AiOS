/**
 * @file futex.c
 * @brief Task 1.5 — Linux-compatible futex(2) for POSIX thread synchronization
 *
 * Implements FUTEX_WAIT and FUTEX_WAKE operations (Linux syscall 202).
 * Uses a fixed-size wait table protected by IRQ-disabled critical sections.
 *
 * Race-free design: WAIT sets state=BLOCKED while IRQs are disabled,
 * so any WAKE that fires after IRQs are re-enabled correctly sees
 * state==BLOCKED and calls vos3_task_wake().
 */

#include "../../include/vos/syscall.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/uaccess.h"

/* ============================================================================
 * SYSCALL NUMBER
 * ============================================================================ */

#define SYS_FUTEX               202

/* ============================================================================
 * FUTEX OPERATION FLAGS
 * ============================================================================ */

#define FUTEX_WAIT              0
#define FUTEX_WAKE              1
#define FUTEX_PRIVATE_FLAG      0x80
#define FUTEX_CLOCK_REALTIME    0x100

/* ============================================================================
 * WAIT TABLE
 * ============================================================================ */

#define FUTEX_TABLE_SIZE    64

/** @brief Number of hash buckets (must be power of 2) */
#define FUTEX_HASH_BITS     4
#define FUTEX_HASH_SIZE     (1 << FUTEX_HASH_BITS)   /* 16 buckets */
#define FUTEX_HASH_MASK     (FUTEX_HASH_SIZE - 1)

typedef struct {
    volatile uint32_t* uaddr;   /**< User-space address being waited on */
    vos3_task_t*       task;    /**< Blocked task */
    int                active;  /**< 1 if slot in use */
    int                next;    /**< Next entry in hash chain (-1 = end) */
} futex_entry_t;

/** @brief Global futex wait table */
static futex_entry_t g_futex_table[FUTEX_TABLE_SIZE];

/** @brief Hash bucket heads (-1 = empty) */
static int g_futex_buckets[FUTEX_HASH_SIZE];

/** @brief Free list head (-1 = full) */
static int g_futex_free_head;

/** @brief Hash a user-space address to a bucket index */
static inline int futex_hash(volatile uint32_t* uaddr)
{
    /* Knuth multiplicative hash on the address (shifted right by 2 for alignment) */
    uint64_t h = ((uint64_t)(uintptr_t)uaddr >> 2) * 2654435761ULL;
    return (int)(h & FUTEX_HASH_MASK);
}

/** @brief Initialize hash table and free list */
static void futex_table_init(void)
{
    for (int i = 0; i < FUTEX_HASH_SIZE; i++) {
        g_futex_buckets[i] = -1;
    }
    /* Build free list: chain all entries */
    for (int i = 0; i < FUTEX_TABLE_SIZE - 1; i++) {
        g_futex_table[i].active = 0;
        g_futex_table[i].next = i + 1;
    }
    g_futex_table[FUTEX_TABLE_SIZE - 1].active = 0;
    g_futex_table[FUTEX_TABLE_SIZE - 1].next = -1;
    g_futex_free_head = 0;
}

/* ============================================================================
 * FUTEX WAIT
 * ============================================================================ */

/**
 * @brief FUTEX_WAIT: if *uaddr == val, block current task.
 *
 * Lost-wakeup-safe design:
 * 1. Disable IRQs.
 * 2. Read *uaddr; if != val, return EAGAIN immediately.
 * 3. Register wait entry in hash table.
 * 4. Set task state = BLOCKED (while IRQs still disabled).
 * 5. Re-enable IRQs — now any concurrent WAKE sees state==BLOCKED.
 * 6. RE-CHECK *uaddr: if value changed, self-remove and return EAGAIN.
 * 7. vos3_sched_yield() performs context switch.
 * 8. On return (woken by WAKE), clear wait entry.
 */
static int64_t futex_wait(volatile uint32_t* uaddr, uint32_t val)
{
    vos3_irqflags_t irqf = vos3_irq_save();

    /* Atomic value check — use copy_from_user for SMAP safety */
    uint32_t uval;
    if (copy_from_user(&uval, (const void*)uaddr, sizeof(uval)) != 0) {
        vos3_irq_restore(irqf);
        return -14;  /* EFAULT */
    }
    if (uval != val) {
        vos3_irq_restore(irqf);
        return -11;  /* EAGAIN */
    }

    /* Allocate from free list — O(1) */
    int slot = g_futex_free_head;
    if (slot < 0) {
        vos3_irq_restore(irqf);
        VOS3_WARN("futex: wait table full");
        return -12;  /* ENOMEM */
    }
    g_futex_free_head = g_futex_table[slot].next;

    /* Insert into hash bucket — O(1) */
    int bucket = futex_hash(uaddr);
    vos3_task_t* cur = vos3_sched_current();
    g_futex_table[slot].uaddr  = uaddr;
    g_futex_table[slot].task   = cur;
    g_futex_table[slot].active = 1;
    g_futex_table[slot].next   = g_futex_buckets[bucket];
    g_futex_buckets[bucket]    = slot;

    /* Mark BLOCKED before re-enabling IRQs — atomic with table registration */
    cur->state = VOS3_TASK_BLOCKED;
    vos3_sched_remove_task(cur);

    /* Re-enable IRQs: WAKE may now fire and will see state==BLOCKED */
    vos3_irq_restore(irqf);

    /* RE-CHECK: Catch lost wakeups — if value changed after we joined the
     * wait queue, a concurrent WAKE was issued while we weren't yet visible.
     * Self-remove from the queue and return EAGAIN so user-space retries.
     *
     * If we were preempted between irq_restore and here, a futex_wake may
     * have already removed us from the table (active=0) and set our state
     * to READY/RUNNING.  Only re-add to the run queue if we're still
     * BLOCKED — otherwise we'd corrupt the scheduler state.
     *
     * CRITICAL: Check task ownership before removing — futex_wake may have
     * freed our slot and another thread may have reused it.  Without this
     * check we would corrupt the new waiter's entry (slot-reuse race). */
    /* RE-CHECK with SMAP-safe read */
    uint32_t recheck_val;
    if (copy_from_user(&recheck_val, (const void*)uaddr, sizeof(recheck_val)) != 0 ||
        recheck_val != val) {
        irqf = vos3_irq_save();
        if (g_futex_table[slot].active && g_futex_table[slot].task == cur) {
            int b2 = futex_hash(g_futex_table[slot].uaddr);
            int* pp2 = &g_futex_buckets[b2];
            while (*pp2 != -1) {
                if (*pp2 == slot) { *pp2 = g_futex_table[slot].next; break; }
                pp2 = &g_futex_table[*pp2].next;
            }
            g_futex_table[slot].active = 0;
            g_futex_table[slot].next = g_futex_free_head;
            g_futex_free_head = slot;
        }
        if (cur->state == VOS3_TASK_BLOCKED) {
            cur->state = VOS3_TASK_READY;
            vos3_sched_add_task(cur);
        }
        vos3_irq_restore(irqf);
        return -11;  /* EAGAIN */
    }

    /* Yield — context switch to another task */
    vos3_sched_yield();

    /* Woken up (by WAKE or spuriously): remove from bucket if not already done.
     * CRITICAL: Verify slot ownership — if futex_wake already freed our slot,
     * another waiter may have reused it.  Removing a reused slot would cause
     * that waiter to never be woken (deadlock). */
    irqf = vos3_irq_save();
    if (g_futex_table[slot].active && g_futex_table[slot].task == cur) {
        /* Remove from hash chain */
        int b = futex_hash(g_futex_table[slot].uaddr);
        int* pp = &g_futex_buckets[b];
        while (*pp != -1) {
            if (*pp == slot) { *pp = g_futex_table[slot].next; break; }
            pp = &g_futex_table[*pp].next;
        }
        g_futex_table[slot].active = 0;
        /* Return to free list */
        g_futex_table[slot].next = g_futex_free_head;
        g_futex_free_head = slot;
    }
    vos3_irq_restore(irqf);

    return 0;
}

/* ============================================================================
 * FUTEX WAKE
 * ============================================================================ */

/**
 * @brief FUTEX_WAKE: wake up to 'count' tasks waiting on uaddr.
 * @return Number of tasks woken.
 */
static int64_t futex_wake(volatile uint32_t* uaddr, int count)
{
    int woken = 0;

    vos3_irqflags_t irqf = vos3_irq_save();

    /* Walk only the hash chain for this uaddr — O(chain_len) not O(64) */
    int bucket = futex_hash(uaddr);
    int* pp = &g_futex_buckets[bucket];
    while (*pp != -1 && woken < count) {
        int idx = *pp;
        if (g_futex_table[idx].active &&
            g_futex_table[idx].uaddr == uaddr) {
            vos3_task_t* task = g_futex_table[idx].task;
            /* Unlink from chain */
            *pp = g_futex_table[idx].next;
            g_futex_table[idx].active = 0;
            /* Return to free list */
            g_futex_table[idx].next = g_futex_free_head;
            g_futex_free_head = idx;
            vos3_task_wake(task);
            woken++;
        } else {
            pp = &g_futex_table[idx].next;
        }
    }

    vos3_irq_restore(irqf);

    return woken;
}

/* ============================================================================
 * SYSCALL HANDLER
 * ============================================================================ */

static int64_t sys_futex(vos3_syscall_frame_t* frame)
{
    volatile uint32_t* uaddr = (volatile uint32_t*)frame->rdi;
    int futex_op = (int)frame->rsi & ~(FUTEX_PRIVATE_FLAG | FUTEX_CLOCK_REALTIME);
    uint32_t val  = (uint32_t)frame->rdx;

    /* Validate user pointer */
    if (uaddr == NULL ||
        (uint64_t)(uintptr_t)uaddr >= 0xFFFF800000000000ULL ||
        ((uintptr_t)uaddr & 3U) != 0U) {
        return -14;  /* EFAULT / EINVAL — must be 4-byte aligned */
    }

    switch (futex_op) {
        case FUTEX_WAIT:
            return futex_wait(uaddr, val);

        case FUTEX_WAKE:
            return futex_wake(uaddr, (int)val);

        default:
            VOS3_WARN("futex: unsupported op %d (raw=%d)", futex_op, (int)frame->rsi);
            return -38;  /* ENOSYS */
    }
}

/* ============================================================================
 * PUBLIC WRAPPER — called from task_exit for CLONE_CHILD_CLEARTID
 * ============================================================================ */

/**
 * @brief Wake waiters on a futex address (public API for task_exit)
 */
int64_t vos3_futex_wake_addr(volatile uint32_t* uaddr, int count)
{
    return futex_wake(uaddr, count);
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

void vos3_futex_init(void)
{
    VOS3_INFO("Initializing futex subsystem (Task 1.5)");

    memset(g_futex_table, 0, sizeof(g_futex_table));
    futex_table_init();

    vos3_syscall_register(SYS_FUTEX, sys_futex);

    VOS3_INFO("futex(%d) registered — %d slots, %d hash buckets",
              SYS_FUTEX, FUTEX_TABLE_SIZE, FUTEX_HASH_SIZE);
}
