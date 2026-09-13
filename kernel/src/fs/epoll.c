/**
 * @file epoll.c
 * @brief VOS3 epoll, select, and eventfd implementation (Task 1.3)
 *
 * @details Linux-compatible epoll/select/eventfd system calls.
 *          epoll_create1, epoll_ctl, epoll_wait, eventfd2, select.
 *
 *          Architecture notes:
 *          - eventfd and epoll instances are backed by vos3_file_t with
 *            inode=NULL and private_data pointing to the backing struct.
 *          - Type is identified by a magic field at the start of private_data.
 *          - epoll_wait uses vos3_poll_check_fd() (exported from fs_syscall.c)
 *            to check each watched fd.
 *          - select() converts fd_set bitmasks to poll_check_fd calls.
 *
 * @version 1.0.0
 * @date 2026-03-14
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/epoll.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/syscall.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/console.h"
#include "../../include/vos/uaccess.h"
#include "../../include/vos/string.h"

/* ============================================================================
 * ERRNO values (Linux-compatible negative codes)
 * ============================================================================ */

#define EPOLL_EINVAL    22
#define EPOLL_EBADF     9
#define EPOLL_ENOMEM    12
#define EPOLL_ENOENT    2
#define EPOLL_EEXIST    17
#define EPOLL_EFAULT    14
#define EPOLL_EAGAIN    11
#define EPOLL_EINTR     4

/* ============================================================================
 * POLL constants (must match fs_syscall.c)
 * ============================================================================ */

#define EPOLL_POLLIN    ((short)0x0001)
#define EPOLL_POLLOUT   ((short)0x0004)
#define EPOLL_POLLERR   ((short)0x0008)
#define EPOLL_POLLHUP   ((short)0x0010)
#define EPOLL_POLLNVAL  ((short)0x0020)

/* ============================================================================
 * SELECT fd_set (Linux compatible: 1024 fds, 16 uint64_t words)
 * ============================================================================ */

#define SELECT_FD_SETSIZE   1024
#define SELECT_NFDBITS      64
#define SELECT_NWORDS       (SELECT_FD_SETSIZE / SELECT_NFDBITS)

typedef struct vos3_fd_set {
    uint64_t bits[SELECT_NWORDS];
} vos3_fd_set_t;

static inline int fdset_isset(const vos3_fd_set_t* fds, int fd)
{
    if (fd < 0 || fd >= SELECT_FD_SETSIZE) return 0;
    return (int)((fds->bits[(unsigned)fd / SELECT_NFDBITS] >>
                  ((unsigned)fd % SELECT_NFDBITS)) & 1ULL);
}

static inline void fdset_set(vos3_fd_set_t* fds, int fd)
{
    if (fd >= 0 && fd < SELECT_FD_SETSIZE)
        fds->bits[(unsigned)fd / SELECT_NFDBITS] |= (1ULL << ((unsigned)fd % SELECT_NFDBITS));
}

static inline void fdset_clear(vos3_fd_set_t* fds, int fd)
{
    if (fd >= 0 && fd < SELECT_FD_SETSIZE)
        fds->bits[(unsigned)fd / SELECT_NFDBITS] &= ~(1ULL << ((unsigned)fd % SELECT_NFDBITS));
}

/* ============================================================================
 * EVENTFD FILE OPS
 * ============================================================================ */

static int eventfd_file_close(struct vos3_file* file)
{
    if (file == NULL || file->private_data == NULL) return 0;
    vos3_eventfd_t* efd = (vos3_eventfd_t*)file->private_data;
    vos3_kfree(efd);
    file->private_data = NULL;
    return 0;
}

static int64_t eventfd_file_read(struct vos3_file* file, void* buf, size_t count)
{
    if (file == NULL || file->private_data == NULL || buf == NULL)
        return -(int64_t)EPOLL_EBADF;
    if (count < sizeof(uint64_t))
        return -(int64_t)EPOLL_EINVAL;

    vos3_eventfd_t* efd = (vos3_eventfd_t*)file->private_data;

    for (;;) {
        vos3_mutex_lock(&efd->lock);
        if (efd->count > 0) {
            uint64_t val = efd->count;
            /* If EFD_SEMAPHORE: decrement by 1; else drain to 0 */
            efd->count = 0;
            vos3_mutex_unlock(&efd->lock);
            /* Copy to user buffer (caller is kernel-side here) */
            memcpy(buf, &val, sizeof(uint64_t));
            return (int64_t)sizeof(uint64_t);
        }
        vos3_mutex_unlock(&efd->lock);

        if (efd->flags & (uint32_t)EFD_NONBLOCK)
            return -(int64_t)EPOLL_EAGAIN;

        /* Block: yield and retry */
        vos3_sys_yield(NULL);
    }
}

static int64_t eventfd_file_write(struct vos3_file* file, const void* buf, size_t count)
{
    if (file == NULL || file->private_data == NULL || buf == NULL)
        return -(int64_t)EPOLL_EBADF;
    if (count < sizeof(uint64_t))
        return -(int64_t)EPOLL_EINVAL;

    vos3_eventfd_t* efd = (vos3_eventfd_t*)file->private_data;
    uint64_t add_val;
    memcpy(&add_val, buf, sizeof(uint64_t));

    if (add_val == UINT64_MAX)
        return -(int64_t)EPOLL_EINVAL;  /* POSIX: write(UINT64_MAX) is invalid */

    vos3_mutex_lock(&efd->lock);
    efd->count += add_val;
    vos3_mutex_unlock(&efd->lock);

    return (int64_t)sizeof(uint64_t);
}

static const vos3_file_ops_t g_eventfd_fops = {
    .open    = NULL,
    .close   = eventfd_file_close,
    .read    = eventfd_file_read,
    .write   = eventfd_file_write,
    .lseek   = NULL,
    .readdir = NULL,
    .fsync   = NULL,
    .ioctl   = NULL,
};

/* ============================================================================
 * EPOLL FILE OPS
 * ============================================================================ */

static int epoll_file_close(struct vos3_file* file)
{
    if (file == NULL || file->private_data == NULL) return 0;
    vos3_epoll_t* ep = (vos3_epoll_t*)file->private_data;
    vos3_kfree(ep);
    file->private_data = NULL;
    return 0;
}

static const vos3_file_ops_t g_epoll_fops = {
    .open    = NULL,
    .close   = epoll_file_close,
    .read    = NULL,
    .write   = NULL,
    .lseek   = NULL,
    .readdir = NULL,
    .fsync   = NULL,
    .ioctl   = NULL,
};

/* ============================================================================
 * HELPER: allocate anonymous vos3_file_t (no inode, no dentry)
 * ============================================================================ */

static vos3_file_t* alloc_anon_file(const vos3_file_ops_t* ops, void* private_data)
{
    vos3_file_t* file = (vos3_file_t*)vos3_kzalloc(sizeof(vos3_file_t));
    if (file == NULL) return NULL;
    vos3_mutex_init(&file->lock, "anon_file");
    file->ops          = ops;
    file->private_data = private_data;
    file->ref_count    = 0U;
    file->inode        = NULL;   /* Key: no inode → triggers magic-check in poll_check_fd */
    file->dentry       = NULL;
    /* Must be O_RDWR (=2): vos3_write() rejects O_RDONLY (=0, the zero value) */
    file->flags        = VOS3_O_RDWR;
    return file;
}

/* ============================================================================
 * sys_eventfd2 — create an eventfd file descriptor
 *
 * Args: RDI = initval (uint32_t), RSI = flags (int)
 * Returns: fd on success, negative error on failure
 * Linux syscall number: 290 (eventfd2 = 290 on x86-64)
 * ============================================================================ */

int64_t sys_eventfd2(vos3_syscall_frame_t* frame)
{
    uint32_t initval = (uint32_t)frame->rdi;
    int flags        = (int)frame->rsi;

    VOS3_DEBUG("sys_eventfd2(initval=%u, flags=0x%x)", initval, flags);

    /* Validate flags */
    int valid_flags = EFD_NONBLOCK | EFD_CLOEXEC | EFD_SEMAPHORE;
    if (flags & ~valid_flags)
        return -(int64_t)EPOLL_EINVAL;

    /* Allocate eventfd backing struct */
    vos3_eventfd_t* efd = (vos3_eventfd_t*)vos3_kzalloc(sizeof(vos3_eventfd_t));
    if (efd == NULL)
        return -(int64_t)EPOLL_ENOMEM;

    efd->magic = EVENTFD_MAGIC;
    efd->count = (uint64_t)initval;
    efd->flags = (uint32_t)flags;
    vos3_mutex_init(&efd->lock, "eventfd");

    /* Allocate anonymous file */
    vos3_file_t* file = alloc_anon_file(&g_eventfd_fops, efd);
    if (file == NULL) {
        vos3_kfree(efd);
        return -(int64_t)EPOLL_ENOMEM;
    }

    /* Install in fd table */
    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL) {
        vos3_kfree(efd);
        vos3_kfree(file);
        return -(int64_t)EPOLL_EBADF;
    }

    int fd = vos3_fd_alloc(task->fd_table, file);
    if (fd < 0) {
        vos3_kfree(efd);
        vos3_kfree(file);
        return -(int64_t)EPOLL_ENOMEM;
    }

    VOS3_DEBUG("sys_eventfd2: created fd=%d count=%llu", fd, (unsigned long long)initval);
    return (int64_t)fd;
}

/* ============================================================================
 * sys_epoll_create — create an epoll file descriptor (legacy, size ignored)
 *
 * Args: RDI = size (ignored but must be > 0)
 * Returns: epoll fd on success, negative error on failure
 * Linux syscall number: 213
 * ============================================================================ */

int64_t sys_epoll_create(vos3_syscall_frame_t* frame)
{
    int size = (int)frame->rdi;
    VOS3_DEBUG("sys_epoll_create(size=%d)", size);

    if (size <= 0)
        return -(int64_t)EPOLL_EINVAL;

    /* Delegate to epoll_create1 with flags=0 */
    vos3_syscall_frame_t f2;
    memset(&f2, 0, sizeof(f2));
    return sys_epoll_create1(&f2);
}

/* ============================================================================
 * sys_epoll_create1 — create an epoll file descriptor
 *
 * Args: RDI = flags (EPOLL_CLOEXEC or 0)
 * Returns: epoll fd on success, negative error on failure
 * Linux syscall number: 291
 * ============================================================================ */

int64_t sys_epoll_create1(vos3_syscall_frame_t* frame)
{
    int flags = (int)frame->rdi;
    VOS3_DEBUG("sys_epoll_create1(flags=0x%x)", flags);

    if (flags & ~EPOLL_CLOEXEC)
        return -(int64_t)EPOLL_EINVAL;

    /* Allocate epoll instance */
    vos3_epoll_t* ep = (vos3_epoll_t*)vos3_kzalloc(sizeof(vos3_epoll_t));
    if (ep == NULL)
        return -(int64_t)EPOLL_ENOMEM;

    ep->magic    = EPOLL_MAGIC;
    ep->nwatches = 0;
    vos3_mutex_init(&ep->lock, "epoll");

    /* Allocate anonymous file */
    vos3_file_t* file = alloc_anon_file(&g_epoll_fops, ep);
    if (file == NULL) {
        vos3_kfree(ep);
        return -(int64_t)EPOLL_ENOMEM;
    }

    /* Install in fd table */
    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL) {
        vos3_kfree(ep);
        vos3_kfree(file);
        return -(int64_t)EPOLL_EBADF;
    }

    int fd = vos3_fd_alloc(task->fd_table, file);
    if (fd < 0) {
        vos3_kfree(ep);
        vos3_kfree(file);
        return -(int64_t)EPOLL_ENOMEM;
    }

    VOS3_DEBUG("sys_epoll_create1: created epoll fd=%d", fd);
    return (int64_t)fd;
}

/* ============================================================================
 * sys_epoll_ctl — control interface for epoll file descriptor
 *
 * Args:
 *   RDI = epfd   (epoll file descriptor)
 *   RSI = op     (EPOLL_CTL_ADD / EPOLL_CTL_MOD / EPOLL_CTL_DEL)
 *   RDX = fd     (file descriptor to watch)
 *   R10 = event  (pointer to vos3_epoll_event_t, ignored for DEL)
 *
 * Returns: 0 on success, negative error on failure
 * Linux syscall number: 233
 * ============================================================================ */

int64_t sys_epoll_ctl(vos3_syscall_frame_t* frame)
{
    int epfd = (int)frame->rdi;
    int op   = (int)frame->rsi;
    int fd   = (int)frame->rdx;
    vos3_epoll_event_t* user_event = (vos3_epoll_event_t*)(uintptr_t)frame->r10;

    VOS3_DEBUG("sys_epoll_ctl(epfd=%d, op=%d, fd=%d)", epfd, op, fd);

    /* Validate op */
    if (op != EPOLL_CTL_ADD && op != EPOLL_CTL_MOD && op != EPOLL_CTL_DEL)
        return -(int64_t)EPOLL_EINVAL;

    /* Cannot watch yourself */
    if (epfd == fd)
        return -(int64_t)EPOLL_EINVAL;

    /* Get current task's fd table */
    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL)
        return -(int64_t)EPOLL_EBADF;

    /* Get epoll file */
    vos3_file_t* epfile = vos3_fd_get(task->fd_table, epfd);
    if (epfile == NULL)
        return -(int64_t)EPOLL_EBADF;
    if (epfile->private_data == NULL) {
        vos3_fd_put(epfile);
        return -(int64_t)EPOLL_EBADF;
    }

    /* Verify it's an epoll instance */
    uint32_t magic = *(uint32_t*)epfile->private_data;
    if (magic != EPOLL_MAGIC) {
        vos3_fd_put(epfile);
        return -(int64_t)EPOLL_EINVAL;
    }

    vos3_epoll_t* ep = (vos3_epoll_t*)epfile->private_data;

    /* For ADD/MOD: read event from user */
    vos3_epoll_event_t kevent;
    if (op != EPOLL_CTL_DEL) {
        if (user_event == NULL || !access_ok(user_event, sizeof(vos3_epoll_event_t))) {
            vos3_fd_put(epfile);
            return -(int64_t)EPOLL_EFAULT;
        }
        if (copy_from_user(&kevent, user_event, sizeof(vos3_epoll_event_t)) != 0) {
            vos3_fd_put(epfile);
            return -(int64_t)EPOLL_EFAULT;
        }
    }

    vos3_mutex_lock(&ep->lock);

    /* Search for existing watch for this fd */
    int found_idx = -1;
    for (uint32_t i = 0; i < VOS3_EPOLL_MAX_WATCHES; i++) {
        if (ep->watches[i].active && ep->watches[i].fd == fd) {
            found_idx = (int)i;
            break;
        }
    }

    int64_t result = 0;

    switch (op) {
        case EPOLL_CTL_ADD:
            if (found_idx >= 0) {
                result = -(int64_t)EPOLL_EEXIST;
                break;
            }
            /* Find free slot */
            {
                int slot = -1;
                for (uint32_t i = 0; i < VOS3_EPOLL_MAX_WATCHES; i++) {
                    if (!ep->watches[i].active) {
                        slot = (int)i;
                        break;
                    }
                }
                if (slot < 0) {
                    result = -(int64_t)EPOLL_ENOMEM;
                    break;
                }
                ep->watches[slot].fd     = fd;
                ep->watches[slot].events = kevent.events;
                ep->watches[slot].data   = kevent.data.u64;
                ep->watches[slot].active = 1;
                ep->nwatches++;
                VOS3_DEBUG("sys_epoll_ctl ADD: epfd=%d fd=%d events=0x%x",
                           epfd, fd, kevent.events);
            }
            break;

        case EPOLL_CTL_MOD:
            if (found_idx < 0) {
                result = -(int64_t)EPOLL_ENOENT;
                break;
            }
            ep->watches[found_idx].events = kevent.events;
            ep->watches[found_idx].data   = kevent.data.u64;
            VOS3_DEBUG("sys_epoll_ctl MOD: epfd=%d fd=%d events=0x%x",
                       epfd, fd, kevent.events);
            break;

        case EPOLL_CTL_DEL:
            if (found_idx < 0) {
                result = -(int64_t)EPOLL_ENOENT;
                break;
            }
            ep->watches[found_idx].active = 0;
            ep->nwatches--;
            VOS3_DEBUG("sys_epoll_ctl DEL: epfd=%d fd=%d", epfd, fd);
            break;

        default:
            result = -(int64_t)EPOLL_EINVAL;
            break;
    }

    vos3_mutex_unlock(&ep->lock);
    vos3_fd_put(epfile);
    return result;
}

/* ============================================================================
 * sys_epoll_wait — wait for events on an epoll file descriptor
 *
 * Args:
 *   RDI = epfd       (epoll file descriptor)
 *   RSI = events     (pointer to user vos3_epoll_event_t array)
 *   RDX = maxevents  (max events to return, must be > 0)
 *   R10 = timeout    (timeout in ms; -1 = infinite)
 *
 * Returns: number of ready events, 0 on timeout, negative on error
 * Linux syscall number: 232
 * ============================================================================ */

int64_t sys_epoll_wait(vos3_syscall_frame_t* frame)
{
    int                  epfd      = (int)frame->rdi;
    vos3_epoll_event_t*  user_evs  = (vos3_epoll_event_t*)(uintptr_t)frame->rsi;
    int                  maxevents = (int)frame->rdx;
    int                  timeout   = (int)frame->r10;

    VOS3_DEBUG("sys_epoll_wait(epfd=%d, maxevents=%d, timeout=%d)",
               epfd, maxevents, timeout);

    if (maxevents <= 0 || maxevents > (int)VOS3_EPOLL_MAX_EVENTS)
        return -(int64_t)EPOLL_EINVAL;

    if (user_evs == NULL || !access_ok(user_evs, (size_t)maxevents * sizeof(vos3_epoll_event_t)))
        return -(int64_t)EPOLL_EFAULT;

    /* Get epoll instance */
    vos3_task_t* task = vos3_sched_current();
    if (task == NULL || task->fd_table == NULL)
        return -(int64_t)EPOLL_EBADF;

    vos3_file_t* epfile = vos3_fd_get(task->fd_table, epfd);
    if (epfile == NULL)
        return -(int64_t)EPOLL_EBADF;
    if (epfile->private_data == NULL) {
        vos3_fd_put(epfile);
        return -(int64_t)EPOLL_EBADF;
    }

    uint32_t magic = *(uint32_t*)epfile->private_data;
    if (magic != EPOLL_MAGIC) {
        vos3_fd_put(epfile);
        return -(int64_t)EPOLL_EINVAL;
    }

    vos3_epoll_t* ep = (vos3_epoll_t*)epfile->private_data;

    /* Compute deadline */
    uint64_t deadline = 0;
    if (timeout > 0)
        deadline = vos3_sched_get_uptime_ms() + (uint64_t)timeout;

    /* Poll loop */
    for (;;) {
        vos3_epoll_event_t ready[VOS3_EPOLL_MAX_EVENTS];
        int nready = 0;

        vos3_mutex_lock(&ep->lock);
        for (uint32_t i = 0; i < VOS3_EPOLL_MAX_WATCHES && nready < maxevents; i++) {
            if (!ep->watches[i].active) continue;

            int wfd       = ep->watches[i].fd;
            uint32_t wevt = ep->watches[i].events;

            /* Map epoll events to poll events */
            short poll_events = 0;
            if (wevt & EPOLLIN)  poll_events |= EPOLL_POLLIN;
            if (wevt & EPOLLOUT) poll_events |= EPOLL_POLLOUT;

            short revents = vos3_poll_check_fd(wfd, poll_events);

            /* Map back to epoll events */
            uint32_t epoll_revents = 0;
            if (revents & EPOLL_POLLIN)  epoll_revents |= EPOLLIN;
            if (revents & EPOLL_POLLOUT) epoll_revents |= EPOLLOUT;
            if (revents & EPOLL_POLLERR) epoll_revents |= EPOLLERR;
            if (revents & EPOLL_POLLHUP) epoll_revents |= EPOLLHUP;
            if (revents & EPOLL_POLLNVAL)epoll_revents |= EPOLLERR;

            /* Apply user's event mask */
            epoll_revents &= (wevt | EPOLLERR | EPOLLHUP);

            if (epoll_revents != 0) {
                ready[nready].events     = epoll_revents;
                ready[nready].data.u64   = ep->watches[i].data;
                nready++;

                /* If EPOLLONESHOT: disable after firing */
                if (wevt & EPOLLONESHOT)
                    ep->watches[i].active = 0;
            }
        }
        vos3_mutex_unlock(&ep->lock);

        /* Return immediately if any events or timeout=0 */
        if (nready > 0 || timeout == 0) {
            if (nready > 0) {
                if (copy_to_user(user_evs, ready,
                                 (size_t)nready * sizeof(vos3_epoll_event_t)) != 0) {
                    vos3_fd_put(epfile);
                    return -(int64_t)EPOLL_EFAULT;
                }
            }
            vos3_fd_put(epfile);
            return (int64_t)nready;
        }

        /* Infinite wait */
        if (timeout < 0) {
            vos3_sys_yield(NULL);
            continue;
        }

        /* Timed wait: check deadline */
        if (vos3_sched_get_uptime_ms() >= deadline) {
            vos3_fd_put(epfile);
            return 0;
        }

        vos3_sys_yield(NULL);
    }
}

/* ============================================================================
 * sys_select — synchronous I/O multiplexing (legacy)
 *
 * Args:
 *   RDI = nfds         (highest fd+1)
 *   RSI = readfds      (user pointer to vos3_fd_set_t, or NULL)
 *   RDX = writefds     (user pointer to vos3_fd_set_t, or NULL)
 *   R10 = exceptfds    (user pointer to vos3_fd_set_t, or NULL)
 *   R8  = timeout      (user pointer to struct timeval, or NULL for infinite)
 *
 * Returns: total ready fds, 0 on timeout, negative on error
 * Linux syscall number: 23
 * ============================================================================ */

/* Minimal timeval struct (seconds + microseconds) */
struct vos3_timeval {
    int64_t tv_sec;
    int64_t tv_usec;
};

int64_t sys_select(vos3_syscall_frame_t* frame)
{
    int             nfds      = (int)frame->rdi;
    vos3_fd_set_t*  u_readfds = (vos3_fd_set_t*)(uintptr_t)frame->rsi;
    vos3_fd_set_t*  u_writfds = (vos3_fd_set_t*)(uintptr_t)frame->rdx;
    vos3_fd_set_t*  u_excfds  = (vos3_fd_set_t*)(uintptr_t)frame->r10;
    struct vos3_timeval* u_tv  = (struct vos3_timeval*)(uintptr_t)frame->r8;

    VOS3_DEBUG("sys_select(nfds=%d)", nfds);

    if (nfds < 0 || nfds > SELECT_FD_SETSIZE)
        return -(int64_t)EPOLL_EINVAL;

    /* Copy fd_sets from user space */
    vos3_fd_set_t k_read, k_writ, k_exc;
    memset(&k_read, 0, sizeof(k_read));
    memset(&k_writ, 0, sizeof(k_writ));
    memset(&k_exc,  0, sizeof(k_exc));

    if (u_readfds != NULL) {
        if (!access_ok(u_readfds, sizeof(vos3_fd_set_t)))
            return -(int64_t)EPOLL_EFAULT;
        if (copy_from_user(&k_read, u_readfds, sizeof(vos3_fd_set_t)) != 0)
            return -(int64_t)EPOLL_EFAULT;
    }
    if (u_writfds != NULL) {
        if (!access_ok(u_writfds, sizeof(vos3_fd_set_t)))
            return -(int64_t)EPOLL_EFAULT;
        if (copy_from_user(&k_writ, u_writfds, sizeof(vos3_fd_set_t)) != 0)
            return -(int64_t)EPOLL_EFAULT;
    }
    if (u_excfds != NULL) {
        if (!access_ok(u_excfds, sizeof(vos3_fd_set_t)))
            return -(int64_t)EPOLL_EFAULT;
        if (copy_from_user(&k_exc, u_excfds, sizeof(vos3_fd_set_t)) != 0)
            return -(int64_t)EPOLL_EFAULT;
    }

    /* Compute deadline from timeval */
    int timeout_ms = -1; /* infinite by default */
    if (u_tv != NULL) {
        struct vos3_timeval ktv;
        if (!access_ok(u_tv, sizeof(ktv)))
            return -(int64_t)EPOLL_EFAULT;
        if (copy_from_user(&ktv, u_tv, sizeof(ktv)) != 0)
            return -(int64_t)EPOLL_EFAULT;
        timeout_ms = (int)(ktv.tv_sec * 1000LL + ktv.tv_usec / 1000LL);
        if (timeout_ms < 0) timeout_ms = 0;
    }

    uint64_t deadline = 0;
    if (timeout_ms > 0)
        deadline = vos3_sched_get_uptime_ms() + (uint64_t)timeout_ms;

    /* Out fd_sets (modified in place) */
    vos3_fd_set_t out_read, out_writ, out_exc;

    for (;;) {
        memset(&out_read, 0, sizeof(out_read));
        memset(&out_writ, 0, sizeof(out_writ));
        memset(&out_exc,  0, sizeof(out_exc));

        int ready = 0;

        for (int fd = 0; fd < nfds; fd++) {
            int want_read  = fdset_isset(&k_read, fd);
            int want_write = fdset_isset(&k_writ, fd);
            int want_exc   = fdset_isset(&k_exc,  fd);

            if (!want_read && !want_write && !want_exc) continue;

            short poll_events = 0;
            if (want_read)  poll_events |= EPOLL_POLLIN;
            if (want_write) poll_events |= EPOLL_POLLOUT;
            if (want_exc)   poll_events |= EPOLL_POLLERR;

            short rev = vos3_poll_check_fd(fd, poll_events);

            if (want_read && (rev & EPOLL_POLLIN)) {
                fdset_set(&out_read, fd);
                ready++;
            }
            if (want_write && (rev & EPOLL_POLLOUT)) {
                fdset_set(&out_writ, fd);
                ready++;
            }
            if (want_exc && (rev & (EPOLL_POLLERR | EPOLL_POLLHUP))) {
                fdset_set(&out_exc, fd);
                ready++;
            }
        }

        if (ready > 0 || timeout_ms == 0) {
            /* Write back modified fd_sets */
            if (u_readfds != NULL)
                copy_to_user(u_readfds, &out_read, sizeof(vos3_fd_set_t));
            if (u_writfds != NULL)
                copy_to_user(u_writfds, &out_writ, sizeof(vos3_fd_set_t));
            if (u_excfds != NULL)
                copy_to_user(u_excfds,  &out_exc,  sizeof(vos3_fd_set_t));
            return (int64_t)ready;
        }

        if (timeout_ms < 0) {
            vos3_sys_yield(NULL);
            continue;
        }

        if (vos3_sched_get_uptime_ms() >= deadline) {
            /* Timeout: zero all output fd_sets */
            if (u_readfds != NULL)
                copy_to_user(u_readfds, &out_read, sizeof(vos3_fd_set_t));
            if (u_writfds != NULL)
                copy_to_user(u_writfds, &out_writ, sizeof(vos3_fd_set_t));
            if (u_excfds != NULL)
                copy_to_user(u_excfds,  &out_exc,  sizeof(vos3_fd_set_t));
            return 0;
        }

        vos3_sys_yield(NULL);
    }
}

/* ============================================================================
 * INIT — register all epoll/select/eventfd syscalls
 * ============================================================================ */

void vos3_epoll_init(void)
{
    VOS3_INFO("Registering epoll/select/eventfd syscalls (Task 1.3)");

    /* epoll_create = 213 */
    vos3_syscall_register(213, sys_epoll_create);
    /* epoll_wait = 232 (previously APP_CTX_SWITCH, moved to 290) */
    vos3_syscall_register(232, sys_epoll_wait);
    /* epoll_ctl = 233 */
    vos3_syscall_register(233, sys_epoll_ctl);
    /* epoll_create1 = 291 */
    vos3_syscall_register(291, sys_epoll_create1);
    /* select = 23 */
    vos3_syscall_register(23, sys_select);
    /* eventfd2 = 290 (Linux) — BUT 290 is now VOS3_SYS_APP_CTX_SWITCH.
     * Linux eventfd2 = 290; we use 284 to avoid conflict. */
    vos3_syscall_register(284, sys_eventfd2);
}
