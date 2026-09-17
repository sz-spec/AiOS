/**
 * @file sync.c
 * @brief VOS3 Synchronization Primitives Implementation
 *
 * @details Mutex, semaphore, condition variable, rwlock, and barrier.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/sync.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/console.h"
#include "../../include/arch/x86_64/cpu.h"

/* ============================================================================
 * WAIT QUEUE IMPLEMENTATION
 * ============================================================================ */

/* Serializes embedded-entry ownership changes across all queues.  Queue
 * operations always take this lock before a queue's own lock.  Besides
 * preventing cross-queue ABA, this keeps cancel() from following an owner
 * pointer while a destroy/wake path is detaching it. */
static vos3_spinlock_t g_wait_membership_lock = VOS3_SPINLOCK_INIT;

void vos3_wq_init(vos3_wait_queue_t* wq)
{
    if (wq == NULL) {
        return;
    }

    wq->head = NULL;
    wq->tail = NULL;
    wq->count = 0U;
    vos3_spinlock_init(&wq->lock);
}

/* Queue the current task while the caller holds wq->lock.  Keeping this
 * small operation separate lets condition-bearing primitives recheck their
 * condition under the same queue lock before committing to sleep. */
static void wq_enqueue_current_locked(vos3_wait_queue_t* wq,
                                      vos3_task_t* current)
{
    vos3_wait_entry_t* entry = &current->wq_entry;
    if (__atomic_load_n(&entry->queue, __ATOMIC_ACQUIRE) != NULL) {
        VOS3_PANIC("Task '%s' already belongs to a wait queue",
                   current->name);
    }
    entry->task = current;
    entry->next = NULL;
    __atomic_store_n(&entry->queue, wq, __ATOMIC_RELEASE);

    if (wq->tail != NULL) {
        wq->tail->next = entry;
    } else {
        wq->head = entry;
    }
    wq->tail = entry;
    wq->count++;
    current->state = VOS3_TASK_BLOCKED;
}

/* The membership lock is held and entry->queue is already NULL.  Do not call
 * task_wake(), whose external-wake path deliberately enters cancellation. */
static void wq_wake_detached(vos3_task_t* task)
{
    if (task != NULL && task->state == VOS3_TASK_BLOCKED) {
        task->state = VOS3_TASK_READY;
        task->wake_time = 0ULL;
        vos3_sched_add_task(task);
    }
}

/* Publish the current task before sleeping and return with local IRQs still
 * disabled.  Callers may release a predicate lock after publication and
 * before the first yield without opening a signal-before-enqueue window. */
static vos3_irqflags_t wq_prepare_wait(vos3_wait_queue_t* wq,
                                      vos3_task_t* current)
{
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_wait_membership_lock);
    vos3_spinlock_lock(&wq->lock);
    wq_enqueue_current_locked(wq, current);
    vos3_spinlock_unlock(&wq->lock);
    vos3_spinlock_unlock(&g_wait_membership_lock);
    return flags;
}

void vos3_wq_wait(vos3_wait_queue_t* wq)
{
    if (wq == NULL) {
        return;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return;
    }

    vos3_irqflags_t flags = wq_prepare_wait(wq, current);

    /* Keep local interrupts disabled until the blocked task has switched out;
     * an ISR must not wake and requeue the still-executing context. */
    vos3_sched_yield();
    vos3_irq_restore(flags);
}

int vos3_wq_wake_one(vos3_wait_queue_t* wq)
{
    if (wq == NULL) {
        return 0;
    }

    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_wait_membership_lock);
    vos3_spinlock_lock(&wq->lock);

    if (wq->head == NULL) {
        vos3_spinlock_unlock(&wq->lock);
        vos3_spinlock_unlock(&g_wait_membership_lock);
        vos3_irq_restore(flags);
        return 0;
    }

    /* Remove first waiter */
    vos3_wait_entry_t* entry = wq->head;
    wq->head = entry->next;
    if (wq->head == NULL) {
        wq->tail = NULL;
    }
    wq->count--;

    vos3_task_t* task = entry->task;
    entry->next = NULL;
    __atomic_store_n(&entry->queue, NULL, __ATOMIC_RELEASE);

    vos3_spinlock_unlock(&wq->lock);
    wq_wake_detached(task);
    vos3_spinlock_unlock(&g_wait_membership_lock);
    vos3_irq_restore(flags);

    return 1;
}

size_t vos3_wq_wake_all(vos3_wait_queue_t* wq)
{
    if (wq == NULL) {
        return 0U;
    }

    size_t woken = 0U;

    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_wait_membership_lock);
    vos3_spinlock_lock(&wq->lock);

    while (wq->head != NULL) {
        vos3_wait_entry_t* entry = wq->head;
        wq->head = entry->next;
        if (wq->head == NULL) wq->tail = NULL;
        if (wq->count > 0U) wq->count--;

        vos3_task_t* task = entry->task;
        entry->next = NULL;
        __atomic_store_n(&entry->queue, NULL, __ATOMIC_RELEASE);
        if (task != NULL) {
            wq_wake_detached(task);
            woken++;
        }
    }

    wq->tail = NULL;
    wq->count = 0U;

    vos3_spinlock_unlock(&wq->lock);
    vos3_spinlock_unlock(&g_wait_membership_lock);
    vos3_irq_restore(flags);

    return woken;
}

int vos3_wq_cancel(vos3_task_t* task)
{
    if (task == NULL) return 0;
    vos3_wait_entry_t* entry = &task->wq_entry;

    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_wait_membership_lock);
    vos3_wait_queue_t* wq = __atomic_load_n(&entry->queue, __ATOMIC_ACQUIRE);
    if (wq == NULL) {
        vos3_spinlock_unlock(&g_wait_membership_lock);
        vos3_irq_restore(flags);
        return 0;
    }
    vos3_spinlock_lock(&wq->lock);
    int removed = 0;
    if (__atomic_load_n(&entry->queue, __ATOMIC_ACQUIRE) == wq) {
        vos3_wait_entry_t* prev = NULL;
        vos3_wait_entry_t* it = wq->head;
        while (it != NULL && it != entry) {
            prev = it;
            it = it->next;
        }
        if (it == entry) {
            if (prev != NULL) prev->next = entry->next;
            else wq->head = entry->next;
            if (wq->tail == entry) wq->tail = prev;
            if (wq->count > 0U) wq->count--;
            removed = 1;
        }
        entry->next = NULL;
        __atomic_store_n(&entry->queue, NULL, __ATOMIC_RELEASE);
    }
    vos3_spinlock_unlock(&wq->lock);
    vos3_spinlock_unlock(&g_wait_membership_lock);
    vos3_irq_restore(flags);
    return removed;
}

int vos3_wq_empty(const vos3_wait_queue_t* wq)
{
    if (wq == NULL) {
        return 1;
    }
    return (wq->head == NULL) ? 1 : 0;
}

/* ============================================================================
 * MUTEX IMPLEMENTATION
 * ============================================================================ */

void vos3_mutex_init(vos3_mutex_t* mutex, const char* name)
{
    if (mutex == NULL) {
        return;
    }

    mutex->magic = VOS3_MUTEX_MAGIC;
    vos3_atomic32_store(&mutex->state, VOS3_MUTEX_UNLOCKED);
    mutex->owner = NULL;
    mutex->recursion = 0U;
    vos3_wq_init(&mutex->waiters);
    mutex->name = name;
}

void vos3_mutex_destroy(vos3_mutex_t* mutex)
{
    if (mutex == NULL || mutex->magic != VOS3_MUTEX_MAGIC) {
        return;
    }

    /* Check if locked */
    if (vos3_atomic32_load(&mutex->state) != VOS3_MUTEX_UNLOCKED) {
        VOS3_WARN("Destroying locked mutex '%s'", mutex->name ? mutex->name : "unnamed");
    }

    mutex->magic = 0U;
}

void vos3_mutex_lock(vos3_mutex_t* mutex)
{
    if (mutex == NULL || mutex->magic != VOS3_MUTEX_MAGIC) {
        return;
    }

    vos3_task_t* current = vos3_sched_current();

    for (;;) {
        if (vos3_atomic32_cmpxchg(&mutex->state,
                                  VOS3_MUTEX_UNLOCKED,
                                  VOS3_MUTEX_LOCKED) == VOS3_MUTEX_UNLOCKED) {
            mutex->owner = current;
            return;
        }

        if (current == NULL) {
            VOS3_PANIC("Mutex '%s' contention without a current task",
                       mutex->name ? mutex->name : "unnamed");
        }

        /* Check for deadlock (recursive lock without recursion support) */
        if (mutex->owner == current) {
            VOS3_PANIC("Deadlock: task '%s' trying to relock mutex '%s'",
                       current ? current->name : "unknown",
                       mutex->name ? mutex->name : "unnamed");
        }

        /* Serialize the final lock-state check with waiter publication.
         * unlock() stores UNLOCKED before taking this queue lock to wake a
         * waiter.  Therefore it either precedes this retry, or observes the
         * waiter after publication; no unlock can be lost between them. */
        vos3_irqflags_t flags = vos3_irq_save();
        vos3_spinlock_lock(&g_wait_membership_lock);
        vos3_spinlock_lock(&mutex->waiters.lock);
        if (vos3_atomic32_cmpxchg(&mutex->state,
                                  VOS3_MUTEX_UNLOCKED,
                                  VOS3_MUTEX_LOCKED) == VOS3_MUTEX_UNLOCKED) {
            vos3_spinlock_unlock(&mutex->waiters.lock);
            vos3_spinlock_unlock(&g_wait_membership_lock);
            vos3_irq_restore(flags);
            mutex->owner = current;
            return;
        }
        wq_enqueue_current_locked(&mutex->waiters, current);
        vos3_spinlock_unlock(&mutex->waiters.lock);
        vos3_spinlock_unlock(&g_wait_membership_lock);
        vos3_sched_yield();
        vos3_irq_restore(flags);
    }
}

int vos3_mutex_trylock(vos3_mutex_t* mutex)
{
    if (mutex == NULL || mutex->magic != VOS3_MUTEX_MAGIC) {
        return 0;
    }

    if (vos3_atomic32_cmpxchg(&mutex->state,
                               VOS3_MUTEX_UNLOCKED,
                               VOS3_MUTEX_LOCKED) == VOS3_MUTEX_UNLOCKED) {
        mutex->owner = vos3_sched_current();
        return 1;
    }

    return 0;
}

void vos3_mutex_unlock(vos3_mutex_t* mutex)
{
    if (mutex == NULL || mutex->magic != VOS3_MUTEX_MAGIC) {
        return;
    }

    vos3_task_t* current = vos3_sched_current();

    /* Verify ownership */
    if (mutex->owner != current) {
        VOS3_WARN("Task '%s' unlocking mutex '%s' owned by '%s'",
                     current ? current->name : "unknown",
                     mutex->name ? mutex->name : "unnamed",
                     mutex->owner ? mutex->owner->name : "unknown");
        return;
    }

    mutex->owner = NULL;
    vos3_atomic32_store(&mutex->state, VOS3_MUTEX_UNLOCKED);

    /* Wake one waiter.
     * NOTE: Known fairness limitation — between the UNLOCKED store above
     * and the woken waiter running, a spinning CPU can steal the mutex
     * via CAS.  Acceptable on SMP-2; a handoff protocol would add
     * significant complexity for minimal gain at this core count. */
    vos3_wq_wake_one(&mutex->waiters);
}

int vos3_mutex_is_locked(const vos3_mutex_t* mutex)
{
    if (mutex == NULL || mutex->magic != VOS3_MUTEX_MAGIC) {
        return 0;
    }
    return (vos3_atomic32_load(&mutex->state) == VOS3_MUTEX_LOCKED) ? 1 : 0;
}

int vos3_mutex_is_owner(const vos3_mutex_t* mutex)
{
    if (mutex == NULL || mutex->magic != VOS3_MUTEX_MAGIC) {
        return 0;
    }
    return (mutex->owner == vos3_sched_current()) ? 1 : 0;
}

/* ============================================================================
 * SEMAPHORE IMPLEMENTATION
 * ============================================================================ */

void vos3_sem_init(vos3_semaphore_t* sem, const char* name, uint32_t initial_count)
{
    if (sem == NULL) {
        return;
    }

    if (initial_count > (uint32_t)INT32_MAX) {
        VOS3_WARN("Semaphore '%s' initial count exceeds INT32_MAX",
                  name ? name : "unnamed");
        sem->magic = 0U;
        return;
    }

    sem->magic = VOS3_SEM_MAGIC;
    vos3_atomic32_store(&sem->count, (int32_t)initial_count);
    vos3_atomic32_store(&sem->closed, 0);
    sem->max_count = -1;
    vos3_wq_init(&sem->waiters);
    sem->name = name;
}

void vos3_sem_init_bounded(vos3_semaphore_t* sem, const char* name,
                           uint32_t initial_count, uint32_t max_count)
{
    if (sem == NULL) {
        return;
    }
    if (max_count > (uint32_t)INT32_MAX || initial_count > max_count) {
        VOS3_WARN("Semaphore '%s' has invalid bounds",
                  name ? name : "unnamed");
        sem->magic = 0U;
        return;
    }
    vos3_sem_init(sem, name, initial_count);
    if (sem->magic != VOS3_SEM_MAGIC) return;
    sem->max_count = (int32_t)max_count;
}

void vos3_sem_destroy(vos3_semaphore_t* sem)
{
    if (sem == NULL || sem->magic != VOS3_SEM_MAGIC) {
        return;
    }

    vos3_sem_close(sem);

    sem->magic = 0U;
}

void vos3_sem_close(vos3_semaphore_t* sem)
{
    if (sem == NULL || sem->magic != VOS3_SEM_MAGIC) {
        return;
    }

    vos3_atomic32_store(&sem->closed, 1);
    (void)vos3_wq_wake_all(&sem->waiters);
}

int vos3_sem_wait_status(vos3_semaphore_t* sem)
{
    if (sem == NULL || sem->magic != VOS3_SEM_MAGIC) {
        return VOS3_SYNC_ERR_INVALID;
    }

    for (;;) {
        if (vos3_atomic32_load(&sem->closed) != 0) {
            return VOS3_SYNC_ERR_CLOSED;
        }
        int32_t count = vos3_atomic32_load(&sem->count);

        if (count > 0) {
            /* Try to decrement */
            if (vos3_atomic32_cmpxchg(&sem->count, count, count - 1) == count) {
                return VOS3_SYNC_OK;
            }
            /* CAS failed, retry */
            continue;
        }

        /* Publish the waiter only after rechecking count under the same
         * queue lock used by post()'s wake.  A post either increments before
         * this recheck, or waits for the published waiter, closing the
         * check-then-sleep lost-wakeup window. */
        vos3_task_t* current = vos3_sched_current();
        if (current == NULL) return VOS3_SYNC_ERR_INVALID;
        vos3_irqflags_t flags = vos3_irq_save();
        vos3_spinlock_lock(&g_wait_membership_lock);
        vos3_spinlock_lock(&sem->waiters.lock);
        for (;;) {
            if (vos3_atomic32_load(&sem->closed) != 0) {
                vos3_spinlock_unlock(&sem->waiters.lock);
                vos3_spinlock_unlock(&g_wait_membership_lock);
                vos3_irq_restore(flags);
                return VOS3_SYNC_ERR_CLOSED;
            }
            count = vos3_atomic32_load(&sem->count);
            if (count <= 0) break;
            if (vos3_atomic32_cmpxchg(&sem->count, count, count - 1) == count) {
                vos3_spinlock_unlock(&sem->waiters.lock);
                vos3_spinlock_unlock(&g_wait_membership_lock);
                vos3_irq_restore(flags);
                return VOS3_SYNC_OK;
            }
        }
        wq_enqueue_current_locked(&sem->waiters, current);
        vos3_spinlock_unlock(&sem->waiters.lock);
        vos3_spinlock_unlock(&g_wait_membership_lock);
        vos3_sched_yield();
        vos3_irq_restore(flags);
    }
}

void vos3_sem_wait(vos3_semaphore_t* sem)
{
    (void)vos3_sem_wait_status(sem);
}

int vos3_sem_trywait(vos3_semaphore_t* sem)
{
    if (sem == NULL || sem->magic != VOS3_SEM_MAGIC) {
        return 0;
    }
    if (vos3_atomic32_load(&sem->closed) != 0) return 0;

    int32_t count = vos3_atomic32_load(&sem->count);

    if (count > 0) {
        if (vos3_atomic32_cmpxchg(&sem->count, count, count - 1) == count) {
            return 1;
        }
    }

    return 0;
}

void vos3_sem_post(vos3_semaphore_t* sem)
{
    if (sem == NULL || sem->magic != VOS3_SEM_MAGIC) {
        return;
    }
    if (vos3_atomic32_load(&sem->closed) != 0) return;

    int32_t count;
    do {
        count = vos3_atomic32_load(&sem->count);

        /* Check max count */
        if (count >= INT32_MAX ||
            (sem->max_count >= 0 && count >= sem->max_count)) {
            VOS3_WARN("Semaphore '%s' at max count",
                         sem->name ? sem->name : "unnamed");
            return;
        }
    } while (vos3_atomic32_cmpxchg(&sem->count, count, count + 1) != count);

    /* Wake one waiter */
    vos3_wq_wake_one(&sem->waiters);
}

int32_t vos3_sem_get_count(const vos3_semaphore_t* sem)
{
    if (sem == NULL || sem->magic != VOS3_SEM_MAGIC) {
        return 0;
    }
    return vos3_atomic32_load(&sem->count);
}

/* ============================================================================
 * CONDITION VARIABLE IMPLEMENTATION
 * ============================================================================ */

void vos3_cond_init(vos3_condvar_t* cond, const char* name)
{
    if (cond == NULL) {
        return;
    }

    cond->magic = VOS3_COND_MAGIC;
    vos3_wq_init(&cond->waiters);
    cond->name = name;
}

void vos3_cond_destroy(vos3_condvar_t* cond)
{
    if (cond == NULL || cond->magic != VOS3_COND_MAGIC) {
        return;
    }

    /* Wake all waiters before destroying */
    vos3_wq_wake_all(&cond->waiters);

    cond->magic = 0U;
}

void vos3_cond_wait(vos3_condvar_t* cond, vos3_mutex_t* mutex)
{
    if (cond == NULL || cond->magic != VOS3_COND_MAGIC) {
        return;
    }
    if (mutex == NULL || mutex->magic != VOS3_MUTEX_MAGIC) {
        return;
    }
    if (!vos3_mutex_is_owner(mutex)) {
        VOS3_WARN("Condition wait without owning mutex '%s'",
                  mutex->name ? mutex->name : "unnamed");
        return;
    }
    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) return;

    /* Publish while the predicate mutex is still held.  The signaler cannot
     * both change the protected predicate and miss this waiter. */
    vos3_irqflags_t flags = wq_prepare_wait(&cond->waiters, current);
    vos3_mutex_unlock(mutex);
    vos3_sched_yield();
    vos3_irq_restore(flags);
    vos3_mutex_lock(mutex);
}

int vos3_cond_timedwait(vos3_condvar_t* cond, vos3_mutex_t* mutex,
                        uint64_t timeout_ms)
{
    if (cond == NULL || cond->magic != VOS3_COND_MAGIC) {
        return VOS3_SYNC_ERR_INVALID;
    }
    if (mutex == NULL || mutex->magic != VOS3_MUTEX_MAGIC) {
        return VOS3_SYNC_ERR_INVALID;
    }

    /* No timer-to-wait-queue cancellation protocol exists yet.  Fail
     * explicitly instead of blocking forever while claiming timeout support. */
    (void)timeout_ms;
    return VOS3_SYNC_ERR_UNSUPPORTED;
}

void vos3_cond_signal(vos3_condvar_t* cond)
{
    if (cond == NULL || cond->magic != VOS3_COND_MAGIC) {
        return;
    }

    vos3_wq_wake_one(&cond->waiters);
}

void vos3_cond_broadcast(vos3_condvar_t* cond)
{
    if (cond == NULL || cond->magic != VOS3_COND_MAGIC) {
        return;
    }

    vos3_wq_wake_all(&cond->waiters);
}

/* ============================================================================
 * READER-WRITER LOCK IMPLEMENTATION
 * ============================================================================ */

void vos3_rwlock_init(vos3_rwlock_t* rwlock, const char* name)
{
    if (rwlock == NULL) {
        return;
    }

    rwlock->magic = VOS3_RWLOCK_MAGIC;
    vos3_spinlock_init(&rwlock->state_lock);
    vos3_atomic32_store(&rwlock->readers, 0);
    vos3_atomic32_store(&rwlock->writers, 0);
    rwlock->writer = NULL;
    vos3_wq_init(&rwlock->read_waiters);
    vos3_wq_init(&rwlock->write_waiters);
    rwlock->name = name;
}

void vos3_rwlock_destroy(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return;
    }

    vos3_wq_wake_all(&rwlock->read_waiters);
    vos3_wq_wake_all(&rwlock->write_waiters);

    rwlock->magic = 0U;
}

void vos3_rwlock_rdlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) return;
    for (;;) {
        vos3_irqflags_t flags = vos3_irq_save();
        vos3_spinlock_lock(&rwlock->state_lock);
        if (vos3_atomic32_load(&rwlock->writers) == 0) {
            int32_t readers = vos3_atomic32_load(&rwlock->readers);
            if (readers == INT32_MAX) {
                vos3_spinlock_unlock(&rwlock->state_lock);
                vos3_irq_restore(flags);
                VOS3_PANIC("Reader count saturated on '%s'",
                           rwlock->name ? rwlock->name : "unnamed");
            }
            vos3_atomic32_store(&rwlock->readers, readers + 1);
            vos3_spinlock_unlock(&rwlock->state_lock);
            vos3_irq_restore(flags);
            return;
        }
        vos3_spinlock_lock(&g_wait_membership_lock);
        vos3_spinlock_lock(&rwlock->read_waiters.lock);
        wq_enqueue_current_locked(&rwlock->read_waiters, current);
        vos3_spinlock_unlock(&rwlock->read_waiters.lock);
        vos3_spinlock_unlock(&g_wait_membership_lock);
        vos3_spinlock_unlock(&rwlock->state_lock);
        vos3_sched_yield();
        vos3_irq_restore(flags);
    }
}

int vos3_rwlock_tryrdlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return 0;
    }

    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&rwlock->state_lock);
    int acquired = 0;
    if (vos3_atomic32_load(&rwlock->writers) == 0) {
        int32_t readers = vos3_atomic32_load(&rwlock->readers);
        if (readers < INT32_MAX) {
            vos3_atomic32_store(&rwlock->readers, readers + 1);
            acquired = 1;
        }
    }
    vos3_spinlock_unlock(&rwlock->state_lock);
    vos3_irq_restore(flags);
    return acquired;
}

void vos3_rwlock_rdunlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return;
    }

    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&rwlock->state_lock);
    int32_t readers = vos3_atomic32_load(&rwlock->readers);
    if (readers <= 0) {
        vos3_spinlock_unlock(&rwlock->state_lock);
        vos3_irq_restore(flags);
        VOS3_WARN("Read unlock without reader ownership on '%s'",
                  rwlock->name ? rwlock->name : "unnamed");
        return;
    }
    vos3_atomic32_store(&rwlock->readers, readers - 1);
    int became_idle = readers == 1;
    vos3_spinlock_unlock(&rwlock->state_lock);
    vos3_irq_restore(flags);
    if (became_idle) {
        /* Wake every contender.  Readers are never parked merely because a
         * writer is queued, so killing a selected/queued writer cannot leave
         * readers stranded.  The state lock remains the acquisition arbiter. */
        (void)vos3_wq_wake_all(&rwlock->write_waiters);
        (void)vos3_wq_wake_all(&rwlock->read_waiters);
    }
}

void vos3_rwlock_wrlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) return;
    vos3_irqflags_t flags;
    for (;;) {
        flags = vos3_irq_save();
        vos3_spinlock_lock(&rwlock->state_lock);
        if (vos3_atomic32_load(&rwlock->readers) == 0 &&
            vos3_atomic32_load(&rwlock->writers) == 0) {
            vos3_atomic32_store(&rwlock->writers, 1);
            rwlock->writer = current;
            vos3_spinlock_unlock(&rwlock->state_lock);
            vos3_irq_restore(flags);
            return;
        }
        vos3_spinlock_lock(&g_wait_membership_lock);
        vos3_spinlock_lock(&rwlock->write_waiters.lock);
        wq_enqueue_current_locked(&rwlock->write_waiters, current);
        vos3_spinlock_unlock(&rwlock->write_waiters.lock);
        vos3_spinlock_unlock(&g_wait_membership_lock);
        vos3_spinlock_unlock(&rwlock->state_lock);
        vos3_sched_yield();
        vos3_irq_restore(flags);
    }
}

int vos3_rwlock_trywrlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return 0;
    }

    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&rwlock->state_lock);
    int acquired = 0;
    if (vos3_atomic32_load(&rwlock->readers) == 0 &&
        vos3_atomic32_load(&rwlock->writers) == 0) {
        vos3_atomic32_store(&rwlock->writers, 1);
        rwlock->writer = vos3_sched_current();
        acquired = 1;
    }
    vos3_spinlock_unlock(&rwlock->state_lock);
    vos3_irq_restore(flags);
    return acquired;
}

void vos3_rwlock_wrunlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return;
    }

    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&rwlock->state_lock);
    if (vos3_atomic32_load(&rwlock->writers) == 0 ||
        rwlock->writer != vos3_sched_current()) {
        vos3_spinlock_unlock(&rwlock->state_lock);
        vos3_irq_restore(flags);
        VOS3_WARN("Write unlock by non-owner on '%s'",
                  rwlock->name ? rwlock->name : "unnamed");
        return;
    }
    rwlock->writer = NULL;
    vos3_atomic32_store(&rwlock->writers, 0);
    vos3_spinlock_unlock(&rwlock->state_lock);
    vos3_irq_restore(flags);
    /* Broadcast both classes.  A woken writer can be killed before it runs;
     * waking only that task would otherwise strand every surviving waiter. */
    (void)vos3_wq_wake_all(&rwlock->write_waiters);
    (void)vos3_wq_wake_all(&rwlock->read_waiters);
}

/* ============================================================================
 * BARRIER IMPLEMENTATION
 * ============================================================================ */

void vos3_barrier_init(vos3_barrier_t* barrier, const char* name, uint32_t count)
{
    if (barrier == NULL || count == 0U) {
        return;
    }

    barrier->magic = VOS3_BARRIER_MAGIC;
    barrier->threshold = count;
    vos3_spinlock_init(&barrier->state_lock);
    vos3_atomic32_store(&barrier->count, 0);
    vos3_atomic32_store(&barrier->generation, 0);
    vos3_wq_init(&barrier->waiters);
    barrier->name = name;
}

void vos3_barrier_destroy(vos3_barrier_t* barrier)
{
    if (barrier == NULL || barrier->magic != VOS3_BARRIER_MAGIC) {
        return;
    }

    vos3_wq_wake_all(&barrier->waiters);

    barrier->magic = 0U;
}

int vos3_barrier_wait(vos3_barrier_t* barrier)
{
    if (barrier == NULL || barrier->magic != VOS3_BARRIER_MAGIC) {
        return 0;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) return 0;
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&barrier->state_lock);
    uint32_t gen = vos3_atomic_load32(&barrier->generation.value);
    uint32_t count = vos3_atomic_load32(&barrier->count.value) + 1U;
    if (count >= barrier->threshold) {
        vos3_atomic_store32(&barrier->count.value, 0U);
        /* Unsigned wrap is the intended generation comparison semantics. */
        vos3_atomic_store32(&barrier->generation.value, gen + 1U);
        vos3_spinlock_unlock(&barrier->state_lock);
        vos3_irq_restore(flags);
        vos3_wq_wake_all(&barrier->waiters);
        return 1;
    }
    vos3_atomic_store32(&barrier->count.value, count);
    for (;;) {
        vos3_spinlock_lock(&g_wait_membership_lock);
        vos3_spinlock_lock(&barrier->waiters.lock);
        if (vos3_atomic_load32(&barrier->generation.value) != gen) {
            vos3_spinlock_unlock(&barrier->waiters.lock);
            vos3_spinlock_unlock(&g_wait_membership_lock);
            vos3_spinlock_unlock(&barrier->state_lock);
            vos3_irq_restore(flags);
            return 0;
        }
        wq_enqueue_current_locked(&barrier->waiters, current);
        vos3_spinlock_unlock(&barrier->waiters.lock);
        vos3_spinlock_unlock(&g_wait_membership_lock);
        vos3_spinlock_unlock(&barrier->state_lock);
        vos3_sched_yield();
        vos3_irq_restore(flags);

        flags = vos3_irq_save();
        vos3_spinlock_lock(&barrier->state_lock);
        if (vos3_atomic_load32(&barrier->generation.value) != gen) {
            vos3_spinlock_unlock(&barrier->state_lock);
            vos3_irq_restore(flags);
            return 0;
        }
        /* Spurious external wake: retry checked publication. */
    }
}
