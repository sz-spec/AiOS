/**
 * @file sync.h
 * @brief VOS3 Synchronization Primitives
 *
 * @details Mutex, semaphore, and condition variable implementations
 *          for kernel-level synchronization.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_SYNC_H
#define VOS3_SYNC_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "task.h"
#include "atomic.h"

/* ============================================================================
 * MUTEX
 * ============================================================================ */

/** @brief Mutex magic for validation */
#define VOS3_MUTEX_MAGIC        ((uint32_t)0x4D555458U)  /* "MUTX" */

/**
 * @brief Mutex state
 */
typedef enum vos3_mutex_state {
    VOS3_MUTEX_UNLOCKED = 0,
    VOS3_MUTEX_LOCKED   = 1
} vos3_mutex_state_t;

/* vos3_wait_entry_t is defined in task.h (embedded in vos3_task_t) */

/**
 * @brief Wait queue
 */
typedef struct vos3_wait_queue {
    vos3_wait_entry_t* head;        /**< First waiter */
    vos3_wait_entry_t* tail;        /**< Last waiter */
    size_t count;                   /**< Number of waiters */
    vos3_spinlock_t lock;           /**< Queue lock */
} vos3_wait_queue_t;

/**
 * @brief Mutex structure
 */
typedef struct vos3_mutex {
    uint32_t magic;                 /**< Magic for validation */
    vos3_atomic32_t state;          /**< Lock state */
    struct vos3_task* owner;        /**< Current owner */
    uint32_t recursion;             /**< Recursion count (for recursive mutexes) */
    vos3_wait_queue_t waiters;      /**< Waiting tasks */
    const char* name;               /**< Debug name */
} vos3_mutex_t;

/** @brief Static mutex initializer */
#define VOS3_MUTEX_INIT(n) { \
    .magic = VOS3_MUTEX_MAGIC, \
    .state = { 0 }, \
    .owner = NULL, \
    .recursion = 0U, \
    .waiters = { NULL, NULL, 0U, VOS3_SPINLOCK_INIT }, \
    .name = (n) \
}

/**
 * @brief Initialize a mutex
 * @param[out] mutex Mutex to initialize
 * @param[in] name Debug name (may be NULL)
 */
void vos3_mutex_init(vos3_mutex_t* mutex, const char* name);

/**
 * @brief Destroy a mutex
 * @param[in] mutex Mutex to destroy
 */
void vos3_mutex_destroy(vos3_mutex_t* mutex);

/**
 * @brief Acquire mutex (blocking)
 * @param[in] mutex Mutex to acquire
 */
void vos3_mutex_lock(vos3_mutex_t* mutex);

/**
 * @brief Try to acquire mutex (non-blocking)
 * @param[in] mutex Mutex to try to acquire
 * @return 1 if acquired, 0 if already locked
 */
int vos3_mutex_trylock(vos3_mutex_t* mutex);

/**
 * @brief Release mutex
 * @param[in] mutex Mutex to release
 */
void vos3_mutex_unlock(vos3_mutex_t* mutex);

/**
 * @brief Check if mutex is locked
 * @param[in] mutex Mutex to check
 * @return 1 if locked, 0 if unlocked
 */
int vos3_mutex_is_locked(const vos3_mutex_t* mutex);

/**
 * @brief Check if current task owns the mutex
 * @param[in] mutex Mutex to check
 * @return 1 if owned by current task, 0 otherwise
 */
int vos3_mutex_is_owner(const vos3_mutex_t* mutex);

/* ============================================================================
 * SEMAPHORE
 * ============================================================================ */

/** @brief Semaphore magic for validation */
#define VOS3_SEM_MAGIC          ((uint32_t)0x53454D41U)  /* "SEMA" */

/**
 * @brief Semaphore structure
 */
typedef struct vos3_semaphore {
    uint32_t magic;                 /**< Magic for validation */
    vos3_atomic32_t count;          /**< Current count */
    int32_t max_count;              /**< Maximum count (-1 for unlimited) */
    vos3_wait_queue_t waiters;      /**< Waiting tasks */
    const char* name;               /**< Debug name */
} vos3_semaphore_t;

/** @brief Static semaphore initializer */
#define VOS3_SEM_INIT(n, c) { \
    .magic = VOS3_SEM_MAGIC, \
    .count = { (int32_t)(c) }, \
    .max_count = -1, \
    .waiters = { NULL, NULL, 0U, VOS3_SPINLOCK_INIT }, \
    .name = (n) \
}

/**
 * @brief Initialize a semaphore
 * @param[out] sem Semaphore to initialize
 * @param[in] name Debug name (may be NULL)
 * @param[in] initial_count Initial count
 */
void vos3_sem_init(vos3_semaphore_t* sem, const char* name, uint32_t initial_count);

/**
 * @brief Initialize a bounded semaphore
 * @param[out] sem Semaphore to initialize
 * @param[in] name Debug name (may be NULL)
 * @param[in] initial_count Initial count
 * @param[in] max_count Maximum count
 */
void vos3_sem_init_bounded(vos3_semaphore_t* sem, const char* name,
                           uint32_t initial_count, uint32_t max_count);

/**
 * @brief Destroy a semaphore
 * @param[in] sem Semaphore to destroy
 */
void vos3_sem_destroy(vos3_semaphore_t* sem);

/**
 * @brief Wait on semaphore (blocking, decrements count)
 * @param[in] sem Semaphore to wait on
 */
void vos3_sem_wait(vos3_semaphore_t* sem);

/**
 * @brief Try to wait on semaphore (non-blocking)
 * @param[in] sem Semaphore to try to wait on
 * @return 1 if acquired, 0 if would block
 */
int vos3_sem_trywait(vos3_semaphore_t* sem);

/**
 * @brief Signal semaphore (increments count, wakes waiter)
 * @param[in] sem Semaphore to signal
 */
void vos3_sem_post(vos3_semaphore_t* sem);

/**
 * @brief Get current semaphore count
 * @param[in] sem Semaphore to query
 * @return Current count
 */
int32_t vos3_sem_get_count(const vos3_semaphore_t* sem);

/* ============================================================================
 * CONDITION VARIABLE
 * ============================================================================ */

/** @brief Condition variable magic for validation */
#define VOS3_COND_MAGIC         ((uint32_t)0x434F4E44U)  /* "COND" */

/**
 * @brief Condition variable structure
 */
typedef struct vos3_condvar {
    uint32_t magic;                 /**< Magic for validation */
    vos3_wait_queue_t waiters;      /**< Waiting tasks */
    const char* name;               /**< Debug name */
} vos3_condvar_t;

/** @brief Static condition variable initializer */
#define VOS3_COND_INIT(n) { \
    .magic = VOS3_COND_MAGIC, \
    .waiters = { NULL, NULL, 0U, VOS3_SPINLOCK_INIT }, \
    .name = (n) \
}

/**
 * @brief Initialize a condition variable
 * @param[out] cond Condition variable to initialize
 * @param[in] name Debug name (may be NULL)
 */
void vos3_cond_init(vos3_condvar_t* cond, const char* name);

/**
 * @brief Destroy a condition variable
 * @param[in] cond Condition variable to destroy
 */
void vos3_cond_destroy(vos3_condvar_t* cond);

/**
 * @brief Wait on condition variable
 * @param[in] cond Condition variable to wait on
 * @param[in] mutex Mutex to release while waiting
 * @note Mutex must be held when calling this function
 */
void vos3_cond_wait(vos3_condvar_t* cond, vos3_mutex_t* mutex);

/**
 * @brief Wait on condition variable with timeout
 * @param[in] cond Condition variable to wait on
 * @param[in] mutex Mutex to release while waiting
 * @param[in] timeout_ms Timeout in milliseconds
 * @return 0 on signal, -1 on timeout
 */
int vos3_cond_timedwait(vos3_condvar_t* cond, vos3_mutex_t* mutex,
                        uint64_t timeout_ms);

/**
 * @brief Signal one waiter
 * @param[in] cond Condition variable to signal
 */
void vos3_cond_signal(vos3_condvar_t* cond);

/**
 * @brief Signal all waiters
 * @param[in] cond Condition variable to broadcast
 */
void vos3_cond_broadcast(vos3_condvar_t* cond);

/* ============================================================================
 * READER-WRITER LOCK
 * ============================================================================ */

/** @brief RW lock magic for validation */
#define VOS3_RWLOCK_MAGIC       ((uint32_t)0x52574C4BU)  /* "RWLK" */

/**
 * @brief Reader-writer lock structure
 */
typedef struct vos3_rwlock {
    uint32_t magic;                 /**< Magic for validation */
    vos3_atomic32_t readers;        /**< Reader count */
    vos3_atomic32_t writers;        /**< Writer count (0 or 1) */
    vos3_atomic32_t write_pending;  /**< Writers waiting */
    struct vos3_task* writer;       /**< Current writer */
    vos3_wait_queue_t read_waiters; /**< Readers waiting */
    vos3_wait_queue_t write_waiters;/**< Writers waiting */
    const char* name;               /**< Debug name */
} vos3_rwlock_t;

/** @brief Static RW lock initializer */
#define VOS3_RWLOCK_INIT(n) { \
    .magic = VOS3_RWLOCK_MAGIC, \
    .readers = { 0 }, \
    .writers = { 0 }, \
    .write_pending = { 0 }, \
    .writer = NULL, \
    .read_waiters = { NULL, NULL, 0U, VOS3_SPINLOCK_INIT }, \
    .write_waiters = { NULL, NULL, 0U, VOS3_SPINLOCK_INIT }, \
    .name = (n) \
}

/**
 * @brief Initialize a reader-writer lock
 * @param[out] rwlock Lock to initialize
 * @param[in] name Debug name (may be NULL)
 */
void vos3_rwlock_init(vos3_rwlock_t* rwlock, const char* name);

/**
 * @brief Destroy a reader-writer lock
 * @param[in] rwlock Lock to destroy
 */
void vos3_rwlock_destroy(vos3_rwlock_t* rwlock);

/**
 * @brief Acquire read lock (shared)
 * @param[in] rwlock Lock to acquire
 */
void vos3_rwlock_rdlock(vos3_rwlock_t* rwlock);

/**
 * @brief Try to acquire read lock (non-blocking)
 * @param[in] rwlock Lock to try to acquire
 * @return 1 if acquired, 0 if would block
 */
int vos3_rwlock_tryrdlock(vos3_rwlock_t* rwlock);

/**
 * @brief Release read lock
 * @param[in] rwlock Lock to release
 */
void vos3_rwlock_rdunlock(vos3_rwlock_t* rwlock);

/**
 * @brief Acquire write lock (exclusive)
 * @param[in] rwlock Lock to acquire
 */
void vos3_rwlock_wrlock(vos3_rwlock_t* rwlock);

/**
 * @brief Try to acquire write lock (non-blocking)
 * @param[in] rwlock Lock to try to acquire
 * @return 1 if acquired, 0 if would block
 */
int vos3_rwlock_trywrlock(vos3_rwlock_t* rwlock);

/**
 * @brief Release write lock
 * @param[in] rwlock Lock to release
 */
void vos3_rwlock_wrunlock(vos3_rwlock_t* rwlock);

/* ============================================================================
 * BARRIER
 * ============================================================================ */

/** @brief Barrier magic for validation */
#define VOS3_BARRIER_MAGIC      ((uint32_t)0x42415252U)  /* "BARR" */

/**
 * @brief Barrier structure
 */
typedef struct vos3_barrier {
    uint32_t magic;                 /**< Magic for validation */
    uint32_t threshold;             /**< Number of threads required */
    vos3_atomic32_t count;          /**< Current waiting count */
    vos3_atomic32_t generation;     /**< Generation counter */
    vos3_wait_queue_t waiters;      /**< Waiting tasks */
    const char* name;               /**< Debug name */
} vos3_barrier_t;

/**
 * @brief Initialize a barrier
 * @param[out] barrier Barrier to initialize
 * @param[in] name Debug name (may be NULL)
 * @param[in] count Number of threads required
 */
void vos3_barrier_init(vos3_barrier_t* barrier, const char* name, uint32_t count);

/**
 * @brief Destroy a barrier
 * @param[in] barrier Barrier to destroy
 */
void vos3_barrier_destroy(vos3_barrier_t* barrier);

/**
 * @brief Wait at barrier
 * @param[in] barrier Barrier to wait at
 * @return 1 for the releasing thread, 0 for others
 */
int vos3_barrier_wait(vos3_barrier_t* barrier);

/* ============================================================================
 * WAIT QUEUE HELPERS
 * ============================================================================ */

/**
 * @brief Initialize a wait queue
 * @param[out] wq Wait queue to initialize
 */
void vos3_wq_init(vos3_wait_queue_t* wq);

/**
 * @brief Add current task to wait queue and block
 * @param[in] wq Wait queue
 */
void vos3_wq_wait(vos3_wait_queue_t* wq);

/**
 * @brief Wake one task from wait queue
 * @param[in] wq Wait queue
 * @return 1 if a task was woken, 0 if queue was empty
 */
int vos3_wq_wake_one(vos3_wait_queue_t* wq);

/**
 * @brief Wake all tasks from wait queue
 * @param[in] wq Wait queue
 * @return Number of tasks woken
 */
size_t vos3_wq_wake_all(vos3_wait_queue_t* wq);

/**
 * @brief Check if wait queue is empty
 * @param[in] wq Wait queue
 * @return 1 if empty, 0 if not
 */
int vos3_wq_empty(const vos3_wait_queue_t* wq);

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_SYNC_OK            (0)
#define VOS3_SYNC_ERR_INVALID   (-1)
#define VOS3_SYNC_ERR_TIMEOUT   (-2)
#define VOS3_SYNC_ERR_DEADLOCK  (-3)
#define VOS3_SYNC_ERR_NOTOWNER  (-4)

#ifdef __cplusplus
}
#endif

#endif /* VOS3_SYNC_H */
