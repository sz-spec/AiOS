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

/** @brief Per-slot generations; zero means the slot has never been issued. */
static uint32_t g_msgq_generation[VOS3_IPC_MAX_OBJECTS];

/** @brief Message queue table lock */
static vos3_spinlock_t g_msgq_lock = VOS3_SPINLOCK_INIT;

/** @brief Next message queue ID */
#define VOS3_MSGQ_SLOT_BITS       8U
#define VOS3_MSGQ_SLOT_MASK       ((vos3_ipc_id_t)0xFFU)
#define VOS3_MSGQ_GENERATION_MAX  ((uint32_t)0x007FFFFFU)

_Static_assert(VOS3_IPC_MAX_OBJECTS == 256U,
               "Message queue handle encoding requires 256 registry slots");

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Pin a message queue by ID
 */
static size_t msgq_slot(vos3_ipc_id_t id)
{
    return (size_t)(id & VOS3_MSGQ_SLOT_MASK);
}

static int msgq_id_valid(vos3_ipc_id_t id)
{
    size_t slot = msgq_slot(id);
    uint32_t generation = id >> VOS3_MSGQ_SLOT_BITS;
    return id != VOS3_IPC_INVALID && id != VOS3_IPC_EEXIST &&
           slot > 0U && slot < VOS3_IPC_MAX_OBJECTS && generation != 0U;
}

static void msgq_finalize(vos3_msgqueue_t* mq)
{
    vos3_ipc_id_t id = mq->id;
    /* Reaching zero references proves the registry and every operation have
     * relinquished the object, so no list lock is needed during finalization. */
    vos3_msgq_entry_t* entry = mq->head;
    while (entry != NULL) {
        vos3_msgq_entry_t* next = entry->next;
        vos3_kfree(entry);
        entry = next;
    }
    mq->head = NULL;
    mq->tail = NULL;
    mq->count = 0U;
    vos3_sem_destroy(&mq->sem_space);
    vos3_sem_destroy(&mq->sem_msgs);
    mq->magic = 0U;
    vos3_kfree(mq);
    VOS3_DEBUG("Finalized message queue (id=%u)", id);
    (void)id;
}

/** @brief Drop a lifetime reference; the 1->0 owner performs finalization. */
static void msgq_put_ref(vos3_msgqueue_t* mq)
{
    int32_t previous = vos3_atomic32_fetch_sub(&mq->active_ops, 1);
    if (previous <= 0) {
        VOS3_PANIC("Message queue reference underflow");
    }
    if (previous == 1) {
        msgq_finalize(mq);
    }
}

static void msgq_put_wait_ref(void* context)
{
    msgq_put_ref((vos3_msgqueue_t*)context);
}

static vos3_msgqueue_t* msgq_get_ref(vos3_ipc_id_t id)
{
    if (!msgq_id_valid(id)) return NULL;
    size_t slot = msgq_slot(id);

    vos3_spinlock_lock(&g_msgq_lock);
    vos3_msgqueue_t* mq = g_msgq_table[slot];
    if (mq == NULL || mq->id != id || mq->magic != VOS3_MSGQ_MAGIC ||
        __atomic_load_n(&mq->closing, __ATOMIC_ACQUIRE) != 0U) {
        vos3_spinlock_unlock(&g_msgq_lock);
        return NULL;
    }

    if (vos3_atomic32_load(&mq->active_ops) == INT32_MAX) {
        vos3_spinlock_unlock(&g_msgq_lock);
        return NULL;
    }
    (void)vos3_atomic32_fetch_add(&mq->active_ops, 1);
    vos3_spinlock_unlock(&g_msgq_lock);
    return mq;
}

/**
 * @brief Find free slot in message queue table
 */
static vos3_ipc_id_t msgq_alloc_id(size_t* slot_out)
{
    for (size_t i = 1U; i < VOS3_IPC_MAX_OBJECTS; i++) {
        if (g_msgq_table[i] == NULL) {
            uint32_t generation = g_msgq_generation[i];
            if (generation >= VOS3_MSGQ_GENERATION_MAX) continue;
            generation++;
            g_msgq_generation[i] = generation;
            *slot_out = i;
            return (vos3_ipc_id_t)((generation << VOS3_MSGQ_SLOT_BITS) |
                                   (uint32_t)i);
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

    size_t slot = 0U;
    vos3_ipc_id_t id = msgq_alloc_id(&slot);
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

    vos3_spinlock_init(&mq->lock);
    vos3_sem_init(&mq->sem_space, "msgq_space", (uint32_t)max_msgs);
    vos3_sem_init(&mq->sem_msgs, "msgq_msgs", 0U);
    /* One registry reference keeps the object alive until destroy detaches it. */
    vos3_atomic32_store(&mq->active_ops, 1);
    __atomic_store_n(&mq->closing, 0U, __ATOMIC_RELEASE);

    vos3_task_t* current = vos3_sched_current();
    mq->owner = current ? current->tid : 0U;
    mq->owner_identity = current ? current->identity_cookie : 0U;
    mq->flags = 0U;

    g_msgq_table[slot] = mq;

    char log_name[sizeof(mq->name)];
    memcpy(log_name, mq->name, sizeof(log_name));

    vos3_spinlock_unlock(&g_msgq_lock);

    VOS3_DEBUG("Created message queue '%s' (id=%u, max=%zu, size=%zu)",
               log_name, id, max_msgs, max_size);

    return id;
}

int vos3_msgq_destroy(vos3_ipc_id_t id)
{
    vos3_spinlock_lock(&g_msgq_lock);

    if (!msgq_id_valid(id)) {
        vos3_spinlock_unlock(&g_msgq_lock);
        return VOS3_IPC_ERR_NOTFOUND;
    }
    size_t slot = msgq_slot(id);
    vos3_msgqueue_t* mq = g_msgq_table[slot];
    if (mq == NULL || mq->id != id || mq->magic != VOS3_MSGQ_MAGIC ||
        __atomic_load_n(&mq->closing, __ATOMIC_ACQUIRE) != 0U) {
        vos3_spinlock_unlock(&g_msgq_lock);
        return VOS3_IPC_ERR_NOTFOUND;
    }

    vos3_task_t* current = vos3_sched_current();
    if ((mq->owner_identity == 0U && current != NULL) ||
        (mq->owner_identity != 0U &&
         (current == NULL || current->identity_cookie == 0U ||
          current->identity_cookie != mq->owner_identity))) {
        vos3_spinlock_unlock(&g_msgq_lock);
        return VOS3_IPC_ERR_ACCESS;
    }

    /* Stop new references before waking blocked operations. */
    __atomic_store_n(&mq->closing, 1U, __ATOMIC_RELEASE);
    g_msgq_table[slot] = NULL;

    vos3_spinlock_unlock(&g_msgq_lock);

    vos3_sem_close(&mq->sem_space);
    vos3_sem_close(&mq->sem_msgs);
    VOS3_DEBUG("Detached message queue (id=%u)", id);
    /* Drop the registry reference. The last operation/reaper pin finalizes. */
    msgq_put_ref(mq);

    return VOS3_IPC_OK;
}

int vos3_msgq_send(vos3_ipc_id_t id, uint32_t type,
                   const void* data, size_t size, uint32_t flags)
{
    vos3_msgqueue_t* mq = msgq_get_ref(id);
    if (mq == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    if (size > mq->max_msg_size) {
        msgq_put_ref(mq);
        return VOS3_IPC_ERR_INVALID;
    }

    /* Wait for space (or try) */
    if ((flags & VOS3_MSGQ_FLAG_NONBLOCK) != 0U) {
        if (vos3_sem_trywait(&mq->sem_space) == 0) {
            msgq_put_ref(mq);
            return VOS3_IPC_ERR_WOULDBLOCK;
        }
    } else {
        vos3_task_t* waiting = vos3_sched_current();
        int armed = vos3_task_arm_wait_cleanup(waiting, msgq_put_wait_ref, mq);
        if (armed <= 0) {
            if (armed == 0) msgq_put_ref(mq);
            return VOS3_IPC_ERR_INTR;
        }
        int wait_result = vos3_sem_wait_status(&mq->sem_space);
        if (!vos3_task_disarm_wait_cleanup(waiting, msgq_put_wait_ref, mq)) {
            return VOS3_IPC_ERR_INTR;
        }
        if (wait_result != VOS3_SYNC_OK) {
            msgq_put_ref(mq);
            return VOS3_IPC_ERR_NOTFOUND;
        }
    }

    if (__atomic_load_n(&mq->closing, __ATOMIC_ACQUIRE) != 0U) {
        msgq_put_ref(mq);
        return VOS3_IPC_ERR_NOTFOUND;
    }

    /* Allocate message entry */
    size_t entry_size = sizeof(vos3_msgq_entry_t) + size;
    vos3_msgq_entry_t* entry = (vos3_msgq_entry_t*)vos3_kmalloc(entry_size);
    if (entry == NULL) {
        vos3_sem_post(&mq->sem_space);  /* Return the space */
        msgq_put_ref(mq);
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
    vos3_irqflags_t lock_flags = vos3_irq_save();
    vos3_spinlock_lock(&mq->lock);

    if (__atomic_load_n(&mq->closing, __ATOMIC_ACQUIRE) != 0U) {
        vos3_spinlock_unlock(&mq->lock);
        vos3_irq_restore(lock_flags);
        vos3_kfree(entry);
        msgq_put_ref(mq);
        return VOS3_IPC_ERR_NOTFOUND;
    }

    if (mq->tail != NULL) {
        mq->tail->next = entry;
    } else {
        mq->head = entry;
    }
    mq->tail = entry;
    mq->count++;

    vos3_spinlock_unlock(&mq->lock);
    vos3_irq_restore(lock_flags);

    /* Signal message available */
    vos3_sem_post(&mq->sem_msgs);

    msgq_put_ref(mq);

    return VOS3_IPC_OK;
}

int64_t vos3_msgq_recv(vos3_ipc_id_t id, uint32_t* type_out,
                       void* data, size_t max_size, uint32_t flags)
{
    vos3_msgqueue_t* mq = msgq_get_ref(id);
    if (mq == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    /* Wait for message (or try) */
    if ((flags & VOS3_MSGQ_FLAG_NONBLOCK) != 0U) {
        if (vos3_sem_trywait(&mq->sem_msgs) == 0) {
            msgq_put_ref(mq);
            return VOS3_IPC_ERR_WOULDBLOCK;
        }
    } else {
        vos3_task_t* waiting = vos3_sched_current();
        int armed = vos3_task_arm_wait_cleanup(waiting, msgq_put_wait_ref, mq);
        if (armed <= 0) {
            if (armed == 0) msgq_put_ref(mq);
            return VOS3_IPC_ERR_INTR;
        }
        int wait_result = vos3_sem_wait_status(&mq->sem_msgs);
        if (!vos3_task_disarm_wait_cleanup(waiting, msgq_put_wait_ref, mq)) {
            return VOS3_IPC_ERR_INTR;
        }
        if (wait_result != VOS3_SYNC_OK) {
            msgq_put_ref(mq);
            return VOS3_IPC_ERR_NOTFOUND;
        }
    }

    if (__atomic_load_n(&mq->closing, __ATOMIC_ACQUIRE) != 0U) {
        msgq_put_ref(mq);
        return VOS3_IPC_ERR_NOTFOUND;
    }

    /* Get message from queue */
    vos3_irqflags_t lock_flags = vos3_irq_save();
    vos3_spinlock_lock(&mq->lock);

    vos3_msgq_entry_t* entry = mq->head;
    if (entry == NULL) {
        vos3_spinlock_unlock(&mq->lock);
        vos3_irq_restore(lock_flags);
        msgq_put_ref(mq);
        return VOS3_IPC_ERR_EMPTY;
    }

    mq->head = entry->next;
    if (mq->head == NULL) {
        mq->tail = NULL;
    }
    mq->count--;

    vos3_spinlock_unlock(&mq->lock);
    vos3_irq_restore(lock_flags);

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

    msgq_put_ref(mq);

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
        if (mq != NULL && mq->magic == VOS3_MSGQ_MAGIC &&
            __atomic_load_n(&mq->closing, __ATOMIC_ACQUIRE) == 0U) {
            if (strcmp(mq->name, name) == 0) {
                vos3_ipc_id_t found = mq->id;
                vos3_spinlock_unlock(&g_msgq_lock);
                return found;
            }
        }
    }

    vos3_spinlock_unlock(&g_msgq_lock);
    return VOS3_IPC_INVALID;
}

int64_t vos3_msgq_count(vos3_ipc_id_t id)
{
    vos3_msgqueue_t* mq = msgq_get_ref(id);
    if (mq == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    vos3_irqflags_t lock_flags = vos3_irq_save();
    vos3_spinlock_lock(&mq->lock);
    int64_t count = (int64_t)mq->count;
    vos3_spinlock_unlock(&mq->lock);
    vos3_irq_restore(lock_flags);
    msgq_put_ref(mq);
    return count;
}
