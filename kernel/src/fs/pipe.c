/**
 * @file pipe.c
 * @brief VOS3 Pipe Implementation
 *
 * @details Unix-style pipes for inter-process communication via file descriptors.
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/vfs.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/sync.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"

/* Signal delivery — declared here to avoid ipc.h pipe struct conflict */
extern int vos3_signal_send(uint32_t tid, int signum);
#define VOS3_SIGPIPE 13

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Pipe buffer size */
#define VOS3_PIPE_BUFSIZE   4096U

/** @brief Maximum concurrent pipes */
#define VOS3_MAX_PIPES      64U

/* ============================================================================
 * PIPE STRUCTURE
 * ============================================================================ */

/** @brief Pipe structure */
typedef struct vos3_pipe {
    char            buffer[VOS3_PIPE_BUFSIZE];  /**< Circular buffer */
    size_t          head;                        /**< Write position */
    size_t          tail;                        /**< Read position */
    size_t          count;                       /**< Bytes in buffer */
    vos3_mutex_t    lock;                        /**< Pipe lock */
    vos3_semaphore_t read_sem;                   /**< Reader semaphore */
    vos3_semaphore_t write_sem;                  /**< Writer semaphore */
    uint32_t        readers;                     /**< Number of readers */
    uint32_t        writers;                     /**< Number of writers */
    uint32_t        in_use;                      /**< Pipe is allocated */
} vos3_pipe_t;

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

/** @brief Pipe pool */
static vos3_pipe_t g_pipes[VOS3_MAX_PIPES];

/** @brief Pipe pool lock */
static vos3_mutex_t g_pipes_lock;

/** @brief Pipe system initialized */
static int g_pipes_initialized = 0;

/* ============================================================================
 * PIPE ALLOCATION
 * ============================================================================ */

/**
 * @brief Allocate a new pipe
 * @return Pipe pointer or NULL
 */
static vos3_pipe_t* pipe_alloc(void)
{
    vos3_mutex_lock(&g_pipes_lock);

    for (uint32_t i = 0U; i < VOS3_MAX_PIPES; i++) {
        if (g_pipes[i].in_use == 0U) {
            vos3_pipe_t* pipe = &g_pipes[i];

            memset(pipe->buffer, 0, VOS3_PIPE_BUFSIZE);
            pipe->head = 0U;
            pipe->tail = 0U;
            pipe->count = 0U;
            pipe->readers = 0U;
            pipe->writers = 0U;
            pipe->in_use = 1U;

            vos3_mutex_init(&pipe->lock, "pipe");
            vos3_sem_init(&pipe->read_sem, "pipe_rd", 0U);
            vos3_sem_init(&pipe->write_sem, "pipe_wr", VOS3_PIPE_BUFSIZE);

            vos3_mutex_unlock(&g_pipes_lock);
            return pipe;
        }
    }

    vos3_mutex_unlock(&g_pipes_lock);
    return NULL;
}

/**
 * @brief Free a pipe
 * @param[in] pipe Pipe to free
 */
static void pipe_free(vos3_pipe_t* pipe)
{
    if (pipe == NULL) {
        return;
    }

    vos3_mutex_lock(&g_pipes_lock);
    pipe->in_use = 0U;
    vos3_mutex_destroy(&pipe->lock);
    vos3_sem_destroy(&pipe->read_sem);
    vos3_sem_destroy(&pipe->write_sem);
    vos3_mutex_unlock(&g_pipes_lock);
}

/* ============================================================================
 * PIPE FILE OPERATIONS
 * ============================================================================ */

/**
 * @brief Close pipe file
 */
static int pipe_close(vos3_file_t* file)
{
    if (file == NULL || file->private_data == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_pipe_t* pipe = (vos3_pipe_t*)file->private_data;

    vos3_mutex_lock(&pipe->lock);

    /* Check if this is read or write end */
    if ((file->flags & VOS3_O_ACCMODE) == VOS3_O_RDONLY) {
        if (pipe->readers > 0U) {
            pipe->readers--;
        }
        /* Wake up writers if no more readers */
        if (pipe->readers == 0U) {
            vos3_sem_post(&pipe->write_sem);
        }
    } else {
        if (pipe->writers > 0U) {
            pipe->writers--;
        }
        /* Wake up readers if no more writers */
        if (pipe->writers == 0U) {
            vos3_sem_post(&pipe->read_sem);
        }
    }

    uint32_t readers = pipe->readers;
    uint32_t writers = pipe->writers;

    vos3_mutex_unlock(&pipe->lock);

    /* Free pipe if no more users */
    if (readers == 0U && writers == 0U) {
        pipe_free(pipe);
    }

    return VOS3_FS_OK;
}

/**
 * @brief Read from pipe
 */
static int64_t pipe_read(vos3_file_t* file, void* buf, size_t count)
{
    if (file == NULL || buf == NULL || file->private_data == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_pipe_t* pipe = (vos3_pipe_t*)file->private_data;
    char* p = (char*)buf;
    size_t read_count = 0U;

    while (read_count < count) {
        vos3_mutex_lock(&pipe->lock);

        /* Check if there's data to read */
        if (pipe->count > 0U) {
            /* Read one byte */
            *p++ = pipe->buffer[pipe->tail];
            pipe->tail = (pipe->tail + 1U) % VOS3_PIPE_BUFSIZE;
            pipe->count--;
            read_count++;

            /* Signal writer that space is available */
            vos3_sem_post(&pipe->write_sem);

            vos3_mutex_unlock(&pipe->lock);
        } else {
            /* No data available */
            uint32_t writers = pipe->writers;
            vos3_mutex_unlock(&pipe->lock);

            /* If we've read something or no writers left, return */
            if (read_count > 0U || writers == 0U) {
                break;
            }

            /* Wait for data */
            vos3_sem_wait(&pipe->read_sem);
        }
    }

    return (int64_t)read_count;
}

/**
 * @brief Write to pipe
 */
static int64_t pipe_write(vos3_file_t* file, const void* buf, size_t count)
{
    if (file == NULL || buf == NULL || file->private_data == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_pipe_t* pipe = (vos3_pipe_t*)file->private_data;
    const char* p = (const char*)buf;
    size_t written = 0U;

    while (written < count) {
        vos3_mutex_lock(&pipe->lock);

        /* Check if there are readers */
        if (pipe->readers == 0U) {
            vos3_mutex_unlock(&pipe->lock);
            /* No readers - broken pipe: send SIGPIPE */
            vos3_task_t* writer = vos3_sched_current();
            VOS3_INFO("[PIPE] Broken pipe detected, readers=0, writer=%s tid=%u",
                      writer ? writer->name : "NULL",
                      writer ? writer->tid : 0);
            if (writer != NULL) {
                int sig_rc = vos3_signal_send(writer->tid, VOS3_SIGPIPE);
                VOS3_INFO("[PIPE] vos3_signal_send(tid=%u, SIGPIPE) returned %d",
                          writer->tid, sig_rc);
            }
            if (written == 0U) {
                return -32;  /* EPIPE */
            }
            return (int64_t)written;
        }

        /* Check if there's space to write */
        if (pipe->count < VOS3_PIPE_BUFSIZE) {
            /* Write one byte */
            pipe->buffer[pipe->head] = *p++;
            pipe->head = (pipe->head + 1U) % VOS3_PIPE_BUFSIZE;
            pipe->count++;
            written++;

            /* Signal reader that data is available */
            vos3_sem_post(&pipe->read_sem);

            vos3_mutex_unlock(&pipe->lock);
        } else {
            /* Buffer full, wait for space */
            vos3_mutex_unlock(&pipe->lock);
            vos3_sem_wait(&pipe->write_sem);
        }
    }

    return (int64_t)written;
}

/** @brief Pipe read-end file operations */
static const vos3_file_ops_t g_pipe_read_ops = {
    .open   = NULL,
    .close  = pipe_close,
    .read   = pipe_read,
    .write  = NULL,  /* Read end cannot write */
    .lseek  = NULL,
    .readdir = NULL,
    .fsync  = NULL,
    .ioctl  = NULL,
};

/** @brief Pipe write-end file operations */
static const vos3_file_ops_t g_pipe_write_ops = {
    .open   = NULL,
    .close  = pipe_close,
    .read   = NULL,  /* Write end cannot read */
    .write  = pipe_write,
    .lseek  = NULL,
    .readdir = NULL,
    .fsync  = NULL,
    .ioctl  = NULL,
};

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize pipe subsystem
 * @return 0 on success
 */
int vos3_pipe_init(void)
{
    if (g_pipes_initialized != 0) {
        return VOS3_FS_OK;
    }

    memset(g_pipes, 0, sizeof(g_pipes));
    vos3_mutex_init(&g_pipes_lock, "pipes");
    g_pipes_initialized = 1;

    VOS3_INFO("Pipe subsystem initialized (%u max pipes)", VOS3_MAX_PIPES);

    return VOS3_FS_OK;
}

/**
 * @brief Create a pipe
 * @param[out] pipefd Array of two file descriptors [read_fd, write_fd]
 * @return 0 on success, negative on error
 */
int vos3_pipe(int pipefd[2])
{
    if (pipefd == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    /* Get current task's fd table */
    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    vos3_fd_table_t* fd_table = (vos3_fd_table_t*)task->fd_table;

    /* Allocate pipe */
    vos3_pipe_t* pipe = pipe_alloc();
    if (pipe == NULL) {
        return VOS3_FS_ERR_NOMEM;
    }

    /* Create read-end file */
    static vos3_file_t read_files[VOS3_MAX_PIPES];
    static vos3_file_t write_files[VOS3_MAX_PIPES];

    int pipe_idx = (int)(pipe - g_pipes);

    vos3_file_t* read_file = &read_files[pipe_idx];
    memset(read_file, 0, sizeof(vos3_file_t));
    read_file->dentry = NULL;
    read_file->inode = NULL;
    read_file->flags = VOS3_O_RDONLY;
    read_file->mode = 0U;
    read_file->pos = 0;
    read_file->ref_count = 0U;  /* Will be incremented by fd_alloc */
    read_file->ops = &g_pipe_read_ops;
    read_file->private_data = pipe;
    vos3_mutex_init(&read_file->lock, "pipe_rd");

    /* Create write-end file */
    vos3_file_t* write_file = &write_files[pipe_idx];
    memset(write_file, 0, sizeof(vos3_file_t));
    write_file->dentry = NULL;
    write_file->inode = NULL;
    write_file->flags = VOS3_O_WRONLY;
    write_file->mode = 0U;
    write_file->pos = 0;
    write_file->ref_count = 0U;
    write_file->ops = &g_pipe_write_ops;
    write_file->private_data = pipe;
    vos3_mutex_init(&write_file->lock, "pipe_wr");

    /* Allocate file descriptors */
    int read_fd = vos3_fd_alloc(fd_table, read_file);
    if (read_fd < 0) {
        pipe_free(pipe);
        return read_fd;
    }

    int write_fd = vos3_fd_alloc(fd_table, write_file);
    if (write_fd < 0) {
        vos3_fd_free(fd_table, read_fd);
        pipe_free(pipe);
        return write_fd;
    }

    /* Update pipe reader/writer counts */
    vos3_mutex_lock(&pipe->lock);
    pipe->readers = 1U;
    pipe->writers = 1U;
    vos3_mutex_unlock(&pipe->lock);

    pipefd[0] = read_fd;
    pipefd[1] = write_fd;

    VOS3_DEBUG("Created pipe: read_fd=%d, write_fd=%d", read_fd, write_fd);

    return VOS3_FS_OK;
}
