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

void vos3_wq_wait(vos3_wait_queue_t* wq)
{
    if (wq == NULL) {
        return;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return;
    }

    /* Use embedded wait entry in task struct (avoids dangling stack pointers) */
    vos3_wait_entry_t* entry = &current->wq_entry;
    entry->task = current;
    entry->next = NULL;

    vos3_spinlock_lock(&wq->lock);

    /* Add to wait queue */
    if (wq->tail != NULL) {
        wq->tail->next = entry;
    } else {
        wq->head = entry;
    }
    wq->tail = entry;
    wq->count++;

    /* Block current task */
    current->state = VOS3_TASK_BLOCKED;

    vos3_spinlock_unlock(&wq->lock);

    /* Yield to another task */
    vos3_sched_yield();
}

int vos3_wq_wake_one(vos3_wait_queue_t* wq)
{
    if (wq == NULL) {
        return 0;
    }

    vos3_spinlock_lock(&wq->lock);

    if (wq->head == NULL) {
        vos3_spinlock_unlock(&wq->lock);
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

    vos3_spinlock_unlock(&wq->lock);

    /* Wake the task */
    if (task != NULL) {
        vos3_task_wake(task);
    }

    return 1;
}

size_t vos3_wq_wake_all(vos3_wait_queue_t* wq)
{
    if (wq == NULL) {
        return 0U;
    }

    size_t woken = 0U;

    vos3_spinlock_lock(&wq->lock);

    while (wq->head != NULL) {
        vos3_wait_entry_t* entry = wq->head;
        wq->head = entry->next;

        vos3_task_t* task = entry->task;
        if (task != NULL) {
            /* Unlock while waking to avoid holding lock too long */
            vos3_spinlock_unlock(&wq->lock);
            vos3_task_wake(task);
            vos3_spinlock_lock(&wq->lock);
            woken++;
        }
    }

    wq->tail = NULL;
    wq->count = 0U;

    vos3_spinlock_unlock(&wq->lock);

    return woken;
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

    /* Try to acquire */
    while (vos3_atomic32_cmpxchg(&mutex->state,
                                  VOS3_MUTEX_UNLOCKED,
                                  VOS3_MUTEX_LOCKED) != VOS3_MUTEX_UNLOCKED) {
        /* Check for deadlock (recursive lock without recursion support) */
        if (mutex->owner == current) {
            VOS3_PANIC("Deadlock: task '%s' trying to relock mutex '%s'",
                       current ? current->name : "unknown",
                       mutex->name ? mutex->name : "unnamed");
        }

        /* Wait for unlock */
        vos3_wq_wait(&mutex->waiters);
    }

    mutex->owner = current;
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

    sem->magic = VOS3_SEM_MAGIC;
    vos3_atomic32_store(&sem->count, (int32_t)initial_count);
    sem->max_count = -1;
    vos3_wq_init(&sem->waiters);
    sem->name = name;
}

void vos3_sem_init_bounded(vos3_semaphore_t* sem, const char* name,
                           uint32_t initial_count, uint32_t max_count)
{
    vos3_sem_init(sem, name, initial_count);
    sem->max_count = (int32_t)max_count;
}

void vos3_sem_destroy(vos3_semaphore_t* sem)
{
    if (sem == NULL || sem->magic != VOS3_SEM_MAGIC) {
        return;
    }

    /* Wake all waiters before destroying */
    vos3_wq_wake_all(&sem->waiters);

    sem->magic = 0U;
}

void vos3_sem_wait(vos3_semaphore_t* sem)
{
    if (sem == NULL || sem->magic != VOS3_SEM_MAGIC) {
        return;
    }

    for (;;) {
        int32_t count = vos3_atomic32_load(&sem->count);

        if (count > 0) {
            /* Try to decrement */
            if (vos3_atomic32_cmpxchg(&sem->count, count, count - 1) == count) {
                return;  /* Successfully acquired */
            }
            /* CAS failed, retry */
            continue;
        }

        /* Count is 0, wait */
        vos3_wq_wait(&sem->waiters);
    }
}

int vos3_sem_trywait(vos3_semaphore_t* sem)
{
    if (sem == NULL || sem->magic != VOS3_SEM_MAGIC) {
        return 0;
    }

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

    int32_t count;
    do {
        count = vos3_atomic32_load(&sem->count);

        /* Check max count */
        if (sem->max_count >= 0 && count >= sem->max_count) {
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

    /* Release mutex */
    vos3_mutex_unlock(mutex);

    /* Wait on condition */
    vos3_wq_wait(&cond->waiters);

    /* Reacquire mutex */
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

    /* TODO: Implement timeout support */
    /* For now, just do a regular wait */
    vos3_cond_wait(cond, mutex);

    return VOS3_SYNC_OK;
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
    vos3_atomic32_store(&rwlock->readers, 0);
    vos3_atomic32_store(&rwlock->writers, 0);
    vos3_atomic32_store(&rwlock->write_pending, 0);
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

    for (;;) {
        /* Wait if there's a writer or pending writers */
        while (vos3_atomic32_load(&rwlock->writers) > 0 ||
               vos3_atomic32_load(&rwlock->write_pending) > 0) {
            vos3_wq_wait(&rwlock->read_waiters);
        }

        /* Try to increment readers */
        int32_t readers = vos3_atomic32_load(&rwlock->readers);
        if (vos3_atomic32_cmpxchg(&rwlock->readers, readers, readers + 1) == readers) {
            /* Double-check no writer snuck in */
            if (vos3_atomic32_load(&rwlock->writers) == 0) {
                return;
            }
            /* Writer acquired, back out */
            vos3_atomic32_fetch_sub(&rwlock->readers, 1);
        }
    }
}

int vos3_rwlock_tryrdlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return 0;
    }

    if (vos3_atomic32_load(&rwlock->writers) > 0 ||
        vos3_atomic32_load(&rwlock->write_pending) > 0) {
        return 0;
    }

    int32_t readers = vos3_atomic32_load(&rwlock->readers);
    if (vos3_atomic32_cmpxchg(&rwlock->readers, readers, readers + 1) == readers) {
        if (vos3_atomic32_load(&rwlock->writers) == 0) {
            return 1;
        }
        vos3_atomic32_fetch_sub(&rwlock->readers, 1);
    }

    return 0;
}

void vos3_rwlock_rdunlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return;
    }

    int32_t readers = vos3_atomic32_fetch_sub(&rwlock->readers, 1);

    /* If this was the last reader and there are pending writers, wake one */
    if (readers == 1 && vos3_atomic32_load(&rwlock->write_pending) > 0) {
        vos3_wq_wake_one(&rwlock->write_waiters);
    }
}

void vos3_rwlock_wrlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return;
    }

    /* Mark write pending */
    vos3_atomic32_fetch_add(&rwlock->write_pending, 1);

    for (;;) {
        /* Wait for no readers and no writer */
        while (vos3_atomic32_load(&rwlock->readers) > 0 ||
               vos3_atomic32_load(&rwlock->writers) > 0) {
            vos3_wq_wait(&rwlock->write_waiters);
        }

        /* Try to acquire write lock */
        if (vos3_atomic32_cmpxchg(&rwlock->writers, 0, 1) == 0) {
            rwlock->writer = vos3_sched_current();
            vos3_atomic32_fetch_sub(&rwlock->write_pending, 1);
            return;
        }
    }
}

int vos3_rwlock_trywrlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return 0;
    }

    if (vos3_atomic32_load(&rwlock->readers) > 0) {
        return 0;
    }

    if (vos3_atomic32_cmpxchg(&rwlock->writers, 0, 1) == 0) {
        rwlock->writer = vos3_sched_current();
        return 1;
    }

    return 0;
}

void vos3_rwlock_wrunlock(vos3_rwlock_t* rwlock)
{
    if (rwlock == NULL || rwlock->magic != VOS3_RWLOCK_MAGIC) {
        return;
    }

    rwlock->writer = NULL;
    vos3_atomic32_store(&rwlock->writers, 0);

    /* Prefer writers over readers (writer-preferring rwlock) */
    if (vos3_atomic32_load(&rwlock->write_pending) > 0) {
        vos3_wq_wake_one(&rwlock->write_waiters);
    } else {
        vos3_wq_wake_all(&rwlock->read_waiters);
    }
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

    int32_t gen = vos3_atomic32_load(&barrier->generation);

    int32_t count = vos3_atomic32_fetch_add(&barrier->count, 1) + 1;

    if ((uint32_t)count >= barrier->threshold) {
        /* Last thread to arrive - release everyone */
        vos3_atomic32_store(&barrier->count, 0);
        vos3_atomic32_fetch_add(&barrier->generation, 1);
        vos3_wq_wake_all(&barrier->waiters);
        return 1;  /* This is the releasing thread */
    }

    /* Wait for barrier to release */
    while (vos3_atomic32_load(&barrier->generation) == gen) {
        vos3_wq_wait(&barrier->waiters);
    }

    return 0;
}
