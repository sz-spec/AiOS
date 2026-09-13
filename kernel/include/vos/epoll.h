/**
 * @file epoll.h
 * @brief VOS3 epoll, select, and eventfd interface (Task 1.3)
 *
 * @details Linux-compatible epoll/select/eventfd syscall infrastructure.
 *          Uses magic-number type detection for private_data discrimination.
 *
 * @version 1.0.0
 * @date 2026-03-14
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_EPOLL_H
#define VOS3_EPOLL_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "sync.h"
#include "syscall.h"

/* ============================================================================
 * MAGIC NUMBERS FOR TYPE DETECTION
 * (checked as first uint32_t in file->private_data)
 * ============================================================================ */

/** @brief eventfd magic: "EVFD" */
#define EVENTFD_MAGIC   ((uint32_t)0x45564644U)
/** @brief epoll magic: "EPOL" */
#define EPOLL_MAGIC     ((uint32_t)0x45504F4CU)

/* ============================================================================
 * EPOLL EVENT FLAGS (Linux-compatible)
 * ============================================================================ */

#define EPOLLIN        ((uint32_t)0x00000001U)  /**< Readable */
#define EPOLLPRI       ((uint32_t)0x00000002U)  /**< Urgent data */
#define EPOLLOUT       ((uint32_t)0x00000004U)  /**< Writable */
#define EPOLLERR       ((uint32_t)0x00000008U)  /**< Error */
#define EPOLLHUP       ((uint32_t)0x00000010U)  /**< Hang up */
#define EPOLLRDHUP     ((uint32_t)0x00002000U)  /**< Peer shutdown */
#define EPOLLET        ((uint32_t)0x80000000U)  /**< Edge-triggered */
#define EPOLLONESHOT   ((uint32_t)0x40000000U)  /**< One-shot */

/* ============================================================================
 * EPOLL_CTL OPERATIONS
 * ============================================================================ */

#define EPOLL_CTL_ADD  1   /**< Add fd to epoll instance */
#define EPOLL_CTL_DEL  2   /**< Remove fd from epoll instance */
#define EPOLL_CTL_MOD  3   /**< Modify watch for fd */

/* ============================================================================
 * EPOLL_CREATE1 FLAGS
 * ============================================================================ */

#define EPOLL_CLOEXEC  ((int)0x80000)   /**< Set close-on-exec on epoll fd */

/* ============================================================================
 * LIMITS
 * ============================================================================ */

/** @brief Max watches per epoll instance */
#define VOS3_EPOLL_MAX_WATCHES  64U

/** @brief Max events returned per epoll_wait call */
#define VOS3_EPOLL_MAX_EVENTS   64U

/* ============================================================================
 * EPOLL EVENT STRUCTURE (Linux-compatible packed layout)
 * ============================================================================ */

/** @brief epoll_event as returned to / received from user space */
typedef struct vos3_epoll_event {
    uint32_t events;    /**< Event bitmask (EPOLLIN | EPOLLOUT | ...) */
    union {
        void*    ptr;
        int      fd;
        uint32_t u32;
        uint64_t u64;
    } data;             /**< User data */
} __attribute__((packed)) vos3_epoll_event_t;

/* ============================================================================
 * EVENTFD PRIVATE DATA
 * ============================================================================ */

/** @brief eventfd backing structure (stored in file->private_data) */
typedef struct vos3_eventfd {
    uint32_t     magic;     /**< EVENTFD_MAGIC */
    uint32_t     flags;     /**< EFD_NONBLOCK etc. */
    uint64_t     count;     /**< Counter value */
    vos3_mutex_t lock;      /**< Counter lock */
} vos3_eventfd_t;

/* EFD flags */
#define EFD_NONBLOCK   ((int)0x0800)   /**< Non-blocking eventfd */
#define EFD_CLOEXEC    ((int)0x80000)  /**< Close-on-exec */
#define EFD_SEMAPHORE  ((int)1)        /**< Semaphore semantics */

/* ============================================================================
 * EPOLL WATCH ENTRY
 * ============================================================================ */

/** @brief Single watch within an epoll instance */
typedef struct vos3_epoll_watch {
    int      fd;        /**< Watched file descriptor */
    uint32_t events;    /**< Registered event mask */
    uint64_t data;      /**< User data (u64 for simplicity) */
    uint8_t  active;    /**< 1 if slot in use */
} vos3_epoll_watch_t;

/* ============================================================================
 * EPOLL INSTANCE PRIVATE DATA
 * ============================================================================ */

/** @brief epoll backing structure (stored in file->private_data) */
typedef struct vos3_epoll {
    uint32_t           magic;                           /**< EPOLL_MAGIC */
    uint32_t           nwatches;                        /**< Active watch count */
    vos3_mutex_t       lock;                            /**< Instance lock */
    vos3_epoll_watch_t watches[VOS3_EPOLL_MAX_WATCHES]; /**< Watch table */
} vos3_epoll_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/** @brief Initialize epoll subsystem and register syscalls */
void vos3_epoll_init(void);

/* Syscall handlers (registered by vos3_epoll_init) */
int64_t sys_epoll_create(vos3_syscall_frame_t* frame);
int64_t sys_epoll_create1(vos3_syscall_frame_t* frame);
int64_t sys_epoll_ctl(vos3_syscall_frame_t* frame);
int64_t sys_epoll_wait(vos3_syscall_frame_t* frame);
int64_t sys_eventfd2(vos3_syscall_frame_t* frame);

/** @brief Poll-check wrapper (for use by epoll_wait) */
short vos3_poll_check_fd(int fd, short events);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_EPOLL_H */
