/**
 * @file pipe.c
 * @brief VOS3 Pipe Implementation
 *
 * @details Unidirectional byte-stream IPC.
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
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * PIPE TABLE
 * ============================================================================ */

/** @brief Pipe table */
static vos3_pipe_t* g_pipe_table[VOS3_PIPE_MAX];

/** @brief Pipe table lock */
static vos3_spinlock_t g_pipe_lock = VOS3_SPINLOCK_INIT;

/** @brief Named pipes (for lookup) */
typedef struct vos3_named_pipe {
    char name[32];
    vos3_ipc_id_t id;
} vos3_named_pipe_t;

static vos3_named_pipe_t g_named_pipes[VOS3_PIPE_MAX];

/* ============================================================================
 * INTERNAL HELPERS
 * ============================================================================ */

/**
 * @brief Get pipe by ID
 */
static vos3_pipe_t* pipe_get(vos3_ipc_id_t id)
{
    if (id == VOS3_IPC_INVALID || id >= VOS3_PIPE_MAX) {
        return NULL;
    }

    vos3_pipe_t* pipe = g_pipe_table[id];
    if (pipe == NULL || pipe->magic != VOS3_PIPE_MAGIC) {
        return NULL;
    }

    return pipe;
}

/**
 * @brief Allocate pipe ID
 */
static vos3_ipc_id_t pipe_alloc_id(void)
{
    for (vos3_ipc_id_t i = 1U; i < VOS3_PIPE_MAX; i++) {
        if (g_pipe_table[i] == NULL) {
            return i;
        }
    }
    return VOS3_IPC_INVALID;
}

/**
 * @brief Create a pipe internal
 */
static vos3_ipc_id_t pipe_create_internal(size_t buf_size, uint32_t flags)
{
    if (buf_size == 0U) {
        buf_size = VOS3_PIPE_BUFFER_SIZE;
    }

    /* Allocate pipe structure */
    vos3_pipe_t* pipe = (vos3_pipe_t*)vos3_kzalloc(sizeof(vos3_pipe_t));
    if (pipe == NULL) {
        return VOS3_IPC_INVALID;
    }

    /* Allocate buffer */
    pipe->buffer = (uint8_t*)vos3_kmalloc(buf_size);
    if (pipe->buffer == NULL) {
        vos3_kfree(pipe);
        return VOS3_IPC_INVALID;
    }

    vos3_spinlock_lock(&g_pipe_lock);

    vos3_ipc_id_t id = pipe_alloc_id();
    if (id == VOS3_IPC_INVALID) {
        vos3_spinlock_unlock(&g_pipe_lock);
        vos3_kfree(pipe->buffer);
        vos3_kfree(pipe);
        return VOS3_IPC_INVALID;
    }

    /* Initialize pipe */
    pipe->magic = VOS3_PIPE_MAGIC;
    pipe->id = id;
    pipe->buf_size = buf_size;
    pipe->read_pos = 0U;
    pipe->write_pos = 0U;
    pipe->data_size = 0U;

    vos3_mutex_init(&pipe->lock, "pipe_lock");
    vos3_sem_init(&pipe->sem_read, "pipe_read", 0U);
    vos3_sem_init_bounded(&pipe->sem_write, "pipe_write",
                          (uint32_t)buf_size, (uint32_t)buf_size);

    pipe->reader = 0U;
    pipe->writer = 0U;
    pipe->flags = flags;
    pipe->read_closed = 0;
    pipe->write_closed = 0;
    pipe->ref_count = 2U;  /* One for reader endpoint, one for writer endpoint */

    g_pipe_table[id] = pipe;

    vos3_spinlock_unlock(&g_pipe_lock);

    VOS3_DEBUG("Created pipe (id=%u, size=%zu)", id, buf_size);

    return id;
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

int vos3_pipe_create(vos3_ipc_id_t* read_id, vos3_ipc_id_t* write_id)
{
    if (read_id == NULL || write_id == NULL) {
        return VOS3_IPC_ERR_INVALID;
    }

    vos3_ipc_id_t id = pipe_create_internal(VOS3_PIPE_BUFFER_SIZE, 0U);
    if (id == VOS3_IPC_INVALID) {
        return VOS3_IPC_ERR_NOMEM;
    }

    /* For anonymous pipes, both ends use the same ID */
    /* The distinction is in how they're used (read vs write) */
    *read_id = id;
    *write_id = id;

    return VOS3_IPC_OK;
}

vos3_ipc_id_t vos3_pipe_create_named(const char* name, uint32_t flags)
{
    if (name == NULL) {
        return VOS3_IPC_INVALID;
    }

    /* Check if already exists */
    vos3_ipc_id_t existing = vos3_pipe_find(name);
    if (existing != VOS3_IPC_INVALID) {
        return existing;
    }

    vos3_ipc_id_t id = pipe_create_internal(VOS3_PIPE_BUFFER_SIZE, flags);
    if (id == VOS3_IPC_INVALID) {
        return VOS3_IPC_INVALID;
    }

    /* Register named pipe */
    vos3_spinlock_lock(&g_pipe_lock);

    for (size_t i = 0U; i < VOS3_PIPE_MAX; i++) {
        if (g_named_pipes[i].id == VOS3_IPC_INVALID ||
            g_named_pipes[i].name[0] == '\0') {
            size_t len = strlen(name);
            if (len >= sizeof(g_named_pipes[i].name)) {
                len = sizeof(g_named_pipes[i].name) - 1U;
            }
            memcpy(g_named_pipes[i].name, name, len);
            g_named_pipes[i].name[len] = '\0';
            g_named_pipes[i].id = id;
            break;
        }
    }

    vos3_spinlock_unlock(&g_pipe_lock);

    VOS3_DEBUG("Created named pipe '%s' (id=%u)", name, id);

    return id;
}

/**
 * @brief Destroy pipe internals (called when last ref_count drops to 0)
 */
static void pipe_destroy(vos3_pipe_t* pipe)
{
    vos3_ipc_id_t id = pipe->id;

    vos3_spinlock_lock(&g_pipe_lock);
    g_pipe_table[id] = NULL;

    /* Remove from named pipes */
    for (size_t i = 0U; i < VOS3_PIPE_MAX; i++) {
        if (g_named_pipes[i].id == id) {
            g_named_pipes[i].id = VOS3_IPC_INVALID;
            g_named_pipes[i].name[0] = '\0';
            break;
        }
    }

    vos3_spinlock_unlock(&g_pipe_lock);

    vos3_mutex_destroy(&pipe->lock);
    vos3_sem_destroy(&pipe->sem_read);
    vos3_sem_destroy(&pipe->sem_write);
    vos3_kfree(pipe->buffer);
    pipe->magic = 0U;
    vos3_kfree(pipe);

    VOS3_DEBUG("Destroyed pipe (id=%u)", id);
}

/**
 * @brief Decrement pipe ref_count; destroy if it reaches zero.
 *
 * Prevents use-after-free: a reader unblocked by sem_post races to
 * reacquire pipe->lock on a destroyed mutex.  With ref_count, the
 * last user (endpoint close or read/write exit) triggers destruction.
 */
static void pipe_unref(vos3_pipe_t* pipe)
{
    uint32_t remaining = __atomic_sub_fetch(&pipe->ref_count, 1U, __ATOMIC_ACQ_REL);
    if (remaining == 0U) {
        pipe_destroy(pipe);
    }
}

int vos3_pipe_close(vos3_ipc_id_t id, int is_write)
{
    vos3_pipe_t* pipe = pipe_get(id);
    if (pipe == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    vos3_mutex_lock(&pipe->lock);

    if (is_write != 0) {
        pipe->write_closed = 1;
        /* Wake any readers waiting */
        vos3_sem_post(&pipe->sem_read);
    } else {
        pipe->read_closed = 1;
        /* Wake any writers waiting */
        vos3_sem_post(&pipe->sem_write);
    }

    vos3_mutex_unlock(&pipe->lock);

    /* Decrement ref_count; only the last closer triggers destruction.
     * This prevents mutex use-after-free: a woken reader may still be
     * racing to acquire pipe->lock when the second endpoint closes. */
    pipe_unref(pipe);

    return VOS3_IPC_OK;
}

int64_t vos3_pipe_read(vos3_ipc_id_t id, void* buf, size_t count)
{
    if (buf == NULL || count == 0U) {
        return VOS3_IPC_ERR_INVALID;
    }

    vos3_pipe_t* pipe = pipe_get(id);
    if (pipe == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    size_t total_read = 0U;
    uint8_t* dst = (uint8_t*)buf;

    while (total_read < count) {
        /* Wait for data */
        if ((pipe->flags & VOS3_PIPE_FLAG_NONBLOCK) != 0U) {
            if (vos3_sem_trywait(&pipe->sem_read) == 0) {
                if (total_read > 0U) {
                    break;
                }
                return VOS3_IPC_ERR_WOULDBLOCK;
            }
        } else {
            vos3_sem_wait(&pipe->sem_read);
        }

        vos3_mutex_lock(&pipe->lock);

        /* Check for closed write end with no data */
        if (pipe->data_size == 0U && pipe->write_closed != 0) {
            vos3_mutex_unlock(&pipe->lock);
            break;
        }

        /* Read one byte */
        if (pipe->data_size > 0U) {
            dst[total_read] = pipe->buffer[pipe->read_pos];
            pipe->read_pos = (pipe->read_pos + 1U) % pipe->buf_size;
            pipe->data_size--;
            total_read++;

            /* Signal space available */
            vos3_sem_post(&pipe->sem_write);

            /* If more data available, re-signal read semaphore */
            if (pipe->data_size > 0U) {
                vos3_sem_post(&pipe->sem_read);
            }
        }

        vos3_mutex_unlock(&pipe->lock);
    }

    return (int64_t)total_read;
}

int64_t vos3_pipe_write(vos3_ipc_id_t id, const void* buf, size_t count)
{
    if (buf == NULL || count == 0U) {
        return VOS3_IPC_ERR_INVALID;
    }

    vos3_pipe_t* pipe = pipe_get(id);
    if (pipe == NULL) {
        return VOS3_IPC_ERR_NOTFOUND;
    }

    /* Check if read end is closed — send SIGPIPE (Phase 34) */
    if (pipe->read_closed != 0) {
        vos3_task_t* current = vos3_sched_current();
        if (current != NULL) {
            vos3_signal_send(current->tid, VOS3_SIGPIPE);
        }
        return VOS3_IPC_ERR_PIPE;
    }

    size_t total_written = 0U;
    const uint8_t* src = (const uint8_t*)buf;

    while (total_written < count) {
        /* Wait for space */
        if ((pipe->flags & VOS3_PIPE_FLAG_NONBLOCK) != 0U) {
            if (vos3_sem_trywait(&pipe->sem_write) == 0) {
                if (total_written > 0U) {
                    break;
                }
                return VOS3_IPC_ERR_WOULDBLOCK;
            }
        } else {
            vos3_sem_wait(&pipe->sem_write);
        }

        vos3_mutex_lock(&pipe->lock);

        /* Check for closed read end — send SIGPIPE (Phase 34) */
        if (pipe->read_closed != 0) {
            vos3_mutex_unlock(&pipe->lock);
            if (total_written > 0U) {
                break;
            }
            vos3_task_t* writer = vos3_sched_current();
            if (writer != NULL) {
                vos3_signal_send(writer->tid, VOS3_SIGPIPE);
            }
            return VOS3_IPC_ERR_PIPE;
        }

        /* Write one byte */
        if (pipe->data_size < pipe->buf_size) {
            pipe->buffer[pipe->write_pos] = src[total_written];
            pipe->write_pos = (pipe->write_pos + 1U) % pipe->buf_size;
            pipe->data_size++;
            total_written++;

            /* Signal data available */
            vos3_sem_post(&pipe->sem_read);

            /* If more space available, re-signal write semaphore */
            if (pipe->data_size < pipe->buf_size) {
                vos3_sem_post(&pipe->sem_write);
            }
        }

        vos3_mutex_unlock(&pipe->lock);
    }

    return (int64_t)total_written;
}

vos3_ipc_id_t vos3_pipe_find(const char* name)
{
    if (name == NULL) {
        return VOS3_IPC_INVALID;
    }

    vos3_spinlock_lock(&g_pipe_lock);

    for (size_t i = 0U; i < VOS3_PIPE_MAX; i++) {
        if (g_named_pipes[i].id != VOS3_IPC_INVALID &&
            strcmp(g_named_pipes[i].name, name) == 0) {
            vos3_ipc_id_t id = g_named_pipes[i].id;
            vos3_spinlock_unlock(&g_pipe_lock);
            return id;
        }
    }

    vos3_spinlock_unlock(&g_pipe_lock);
    return VOS3_IPC_INVALID;
}
