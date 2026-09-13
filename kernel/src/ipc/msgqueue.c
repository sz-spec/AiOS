/**
 * @file msgqueue.c
 * @brief VOS3 Message Queue Implementation
 *
 * @details Message-based IPC between tasks.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/ipc.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/timer.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * MESSAGE QUEUE TABLE
 * ============================================================================ */

/** @brief Message queue table */
static vos3_msgqueue_t* g_msgq_table[VOS3_IPC_MAX_OBJECTS];

/** @brief Message queue table lock */
static vos3_spinlock_t g_msgq_lock = VOS3_SPINLOCK_INIT;

/** @brief Next message queue ID */
static vos3_ipc_id_t g_next_msgq_id __attribute__((unused)) = 1U;

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Get message queue by ID
 */
static vos3_msgqueue_t* msgq_get(vos3_ipc_id_t id)
{
    if (id == VOS3_IPC_INVALID || id >= VOS3_IPC_MAX_OBJECTS) {
        return NULL;
    }

    vos3_msgqueue_t* mq = g_msgq_table[id];
    if (mq == NULL || mq->magic != VOS3_MSGQ_MAGIC) {
        return NULL;
    }

    return mq;
}

/**
 * @brief Find free slot in message queue table
 */
static vos3_ipc_id_t msgq_alloc_id(void)
{
    for (vos3_ipc_id_t i = 1U; i < VOS3_IPC_MAX_OBJECTS; i++) {
        if (g_msgq_table[i] == NULL) {
            return i;
        }
    }
    return VOS3_IPC_INVALID;
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

vos3_ipc_id_t vos3_msgq_create(const char* name, size_t max_msgs, size_t max_size)
{
    if (max_msgs == 0U || max_msgs > VOS3_MSG_QUEUE_MAX) {
        max_msgs = VOS3_MSG_QUEUE_MAX;
    }

    if (max_size == 0U || max_size > VOS3_MSG_MAX_SIZE) {
        max_size = VOS3_MSG_MAX_SIZE;
    }

    /* Allocate message queue structure */
    vos3_msgqueue_t* mq = (vos3_msgqueue_t*)vos3_kzalloc(sizeof(vos3_msgqueue_t));
    if (mq == NULL) {
        return VOS3_IPC_INVALID;
    }

    vos3_spinlock_lock(&g_msgq_lock);

    vos3_ipc_id_t id = msgq_alloc_id();
    if (id == VOS3_IPC_INVALID) {
        vos3_spinlock_unlock(&g_msgq_lock);
        vos3_kfree(mq);
        return VOS3_IPC_INVALID;
    }

    /* Initialize message queue */
    mq->magic = VOS3_MSGQ_MAGIC;
    mq->id = id;

    if (name != NULL) {
        size_t len = strlen(name);
        if (len >= sizeof(mq->name)) {
            len = sizeof(mq->name) - 1U;
        }
        memcpy(mq->name, name, len);
        mq->name[len] = '\0';
    }

    mq->head = NULL;
    mq->tail = NULL;
    mq->count = 0U;
    mq->max_count = max_msgs;
    mq->max_msg_size = max_size;

    vos3_mutex_init(&mq->lock, "msgq_lock");
    vos3_sem_init(&mq->sem_space, "msgq_space", (uint32_t)max_msgs);
    vos3_sem_init(&mq->sem_msgs, "msgq_msgs", 0U);

    vos3_task_t* current = vos3_sched_current();
    mq->owner = current ? current->tid : 0U;
    mq->flags = 0U;

    g_msgq_table[id] = mq;

    vos3_spinlock_unlock(&g_msgq_lock);

    VOS3_DEBUG("Created message queue '%s' (id=%u, max=%zu, size=%zu)",
               mq->name, id, max_msgs, max_size);

    return id;
}

int vos3_msgq_destroy(vos3_ipc_id_t id)
{
    vos3_spinlock_lock(&g_msgq_lock);

    vos3_msgqueue_t* mq = msgq_get(id);
    if (mq == NULL) {
        vos3_spinlock_unlock(&g_msgq_lock);
        return VOS3_IPC_ERR_NOTFOUND;
    }

    /* Remove from table */
    g_msgq_table[id] = NULL;

    vos3_spinlock_unlock(&g_msgq_lock);

    /* Free all pending messages */
    vos3_mutex_lock(&mq->lock);

    vos3_msgq_entry_t* entry = mq->head;
    while (entry != NULL) {
        vos3_msgq_entry_t* next = entry->next;
        vos3_kfree(entry);
        entry = next;
    }

    vos3_mutex_unlock(&mq->lock);

    /* Destroy synchronization primitives */
    vos3_mutex_destroy(&mq->lock);
    vos3_sem_destroy(&mq->sem_space);
    vos3_sem_destroy(&mq->sem_msgs);

    /* Clear magic and free */
    mq->magic = 0U;
    vos3_kfree(mq);

    VOS3_DEBUG("Destroyed message queue (id=%u)", id);

    return VOS3_IPC_OK;
}

int vos3_msgq_send(vos3_ipc_id_t id, uint32_t type,
                   const void* data, size_t size, uint32_t flags)
{
    vos3_msgqueue_t* mq = msgq_get(id);
    if (mq == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    if (size > mq->max_msg_size) {
        return VOS3_IPC_ERR_INVALID;
    }

    /* Wait for space (or try) */
    if ((flags & VOS3_MSGQ_FLAG_NONBLOCK) != 0U) {
        if (vos3_sem_trywait(&mq->sem_space) == 0) {
            return VOS3_IPC_ERR_WOULDBLOCK;
        }
    } else {
        vos3_sem_wait(&mq->sem_space);
    }

    /* Allocate message entry */
    size_t entry_size = sizeof(vos3_msgq_entry_t) + size;
    vos3_msgq_entry_t* entry = (vos3_msgq_entry_t*)vos3_kmalloc(entry_size);
    if (entry == NULL) {
        vos3_sem_post(&mq->sem_space);  /* Return the space */
        return VOS3_IPC_ERR_NOMEM;
    }

    /* Fill message */
    entry->next = NULL;
    entry->total_size = sizeof(vos3_msg_header_t) + size;

    vos3_task_t* current = vos3_sched_current();
    entry->msg.header.sender = current ? current->tid : 0U;
    entry->msg.header.type = type;
    entry->msg.header.size = size;
    entry->msg.header.timestamp = vos3_timer_get_ticks();

    if (data != NULL && size > 0U) {
        memcpy(entry->msg.data, data, size);
    }

    /* Add to queue */
    vos3_mutex_lock(&mq->lock);

    if (mq->tail != NULL) {
        mq->tail->next = entry;
    } else {
        mq->head = entry;
    }
    mq->tail = entry;
    mq->count++;

    vos3_mutex_unlock(&mq->lock);

    /* Signal message available */
    vos3_sem_post(&mq->sem_msgs);

    return VOS3_IPC_OK;
}

int64_t vos3_msgq_recv(vos3_ipc_id_t id, uint32_t* type_out,
                       void* data, size_t max_size, uint32_t flags)
{
    vos3_msgqueue_t* mq = msgq_get(id);
    if (mq == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    /* Wait for message (or try) */
    if ((flags & VOS3_MSGQ_FLAG_NONBLOCK) != 0U) {
        if (vos3_sem_trywait(&mq->sem_msgs) == 0) {
            return VOS3_IPC_ERR_WOULDBLOCK;
        }
    } else {
        vos3_sem_wait(&mq->sem_msgs);
    }

    /* Get message from queue */
    vos3_mutex_lock(&mq->lock);

    vos3_msgq_entry_t* entry = mq->head;
    if (entry == NULL) {
        vos3_mutex_unlock(&mq->lock);
        return VOS3_IPC_ERR_EMPTY;
    }

    mq->head = entry->next;
    if (mq->head == NULL) {
        mq->tail = NULL;
    }
    mq->count--;

    vos3_mutex_unlock(&mq->lock);

    /* Copy message data */
    size_t copy_size = entry->msg.header.size;
    if (copy_size > max_size) {
        copy_size = max_size;
    }

    if (type_out != NULL) {
        *type_out = entry->msg.header.type;
    }

    if (data != NULL && copy_size > 0U) {
        memcpy(data, entry->msg.data, copy_size);
    }

    /* Free entry and signal space available */
    vos3_kfree(entry);
    vos3_sem_post(&mq->sem_space);

    return (int64_t)copy_size;
}

vos3_ipc_id_t vos3_msgq_find(const char* name)
{
    if (name == NULL) {
        return VOS3_IPC_INVALID;
    }

    vos3_spinlock_lock(&g_msgq_lock);

    for (vos3_ipc_id_t i = 1U; i < VOS3_IPC_MAX_OBJECTS; i++) {
        vos3_msgqueue_t* mq = g_msgq_table[i];
        if (mq != NULL && mq->magic == VOS3_MSGQ_MAGIC) {
            if (strcmp(mq->name, name) == 0) {
                vos3_spinlock_unlock(&g_msgq_lock);
                return i;
            }
        }
    }

    vos3_spinlock_unlock(&g_msgq_lock);
    return VOS3_IPC_INVALID;
}

int64_t vos3_msgq_count(vos3_ipc_id_t id)
{
    vos3_msgqueue_t* mq = msgq_get(id);
    if (mq == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    return (int64_t)mq->count;
}
