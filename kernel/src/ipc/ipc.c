/**
 * @file ipc.c
 * @brief VOS3 IPC Subsystem Initialization
 *
 * @details IPC subsystem initialization and system call handlers.
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
#include "../../include/vos/syscall.h"
#include "../../include/vos/console.h"
#include "../../include/vos/uaccess.h"
#include "../../include/vos/string.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/dispatcher.h"

/* Error codes (not in uaccess.h) */
#ifndef EINVAL
#define EINVAL  22
#endif
#ifndef ENOMEM
#define ENOMEM  12
#endif

/* ============================================================================
 * IPC STATE
 * ============================================================================ */

static int g_ipc_initialized = 0;

/* ============================================================================
 * IPC SYSTEM CALLS
 * ============================================================================ */

/* Syscall numbers now defined in kernel/include/vos/syscall.h (400+ range).
 * Local #define overrides removed — use VOS3_SYS_* enum values directly.
 * Linux x86-64 ABI aliases remain for backward compatibility. */

/* Linux x86-64 ABI aliases for SHM (standard numbers, keep as-is) */
#define LINUX_SYS_SHMGET        29
#define LINUX_SYS_SHMAT         30
#define LINUX_SYS_SHMCTL        31
#define LINUX_SYS_SHMDT         67

/**
 * @brief sys_msgq_create - Create message queue (Phase 29: Hardened)
 */
static int64_t sys_msgq_create(vos3_syscall_frame_t* frame)
{
    const char* user_name = (const char*)frame->rdi;
    size_t max_msgs = (size_t)frame->rsi;
    size_t max_size = (size_t)frame->rdx;

    char kname[64];
    const char* name_ptr = NULL;

    /* Phase 29: Validate and copy name from user space */
    if (user_name != NULL) {
        int64_t len = strncpy_from_user(kname, user_name, sizeof(kname));
        if (len < 0) {
            return -EFAULT;
        }
        name_ptr = kname;
    }

    vos3_ipc_id_t id = vos3_msgq_create(name_ptr, max_msgs, max_size);
    return (int64_t)id;
}

/**
 * @brief sys_msgq_destroy - Destroy message queue
 */
static int64_t sys_msgq_destroy(vos3_syscall_frame_t* frame)
{
    vos3_ipc_id_t id = (vos3_ipc_id_t)frame->rdi;
    return (int64_t)vos3_msgq_destroy(id);
}

/**
 * @brief sys_msgq_send - Send message (Phase 29: Hardened)
 */
static int64_t sys_msgq_send(vos3_syscall_frame_t* frame)
{
    vos3_ipc_id_t id = (vos3_ipc_id_t)frame->rdi;
    uint32_t type = (uint32_t)frame->rsi;
    const void* user_data = (const void*)frame->rdx;
    size_t size = (size_t)frame->r10;
    uint32_t flags = (uint32_t)frame->r8;
    int result;

    /* Phase 29: Validate and copy data from user space */
    if (size > 0 && user_data != NULL) {
        if (size > VOS3_MSG_MAX_SIZE) {
            return -EINVAL;
        }
        void* kdata = vos3_kmalloc(size);
        if (kdata == NULL) {
            return -ENOMEM;
        }
        if (copy_from_user(kdata, user_data, size) != 0) {
            vos3_kfree(kdata);
            return -EFAULT;
        }
        result = vos3_msgq_send(id, type, kdata, size, flags);
        vos3_kfree(kdata);
        return (int64_t)result;
    }

    return (int64_t)vos3_msgq_send(id, type, NULL, 0, flags);
}

/**
 * @brief sys_msgq_recv - Receive message (Phase 29: Hardened)
 */
static int64_t sys_msgq_recv(vos3_syscall_frame_t* frame)
{
    vos3_ipc_id_t id = (vos3_ipc_id_t)frame->rdi;
    uint32_t* user_type_out = (uint32_t*)frame->rsi;
    void* user_data = (void*)frame->rdx;
    size_t max_size = (size_t)frame->r10;
    uint32_t flags = (uint32_t)frame->r8;

    uint32_t ktype;
    void* kdata = NULL;
    int64_t result;

    /* Phase 29: Validate user pointers */
    if (user_type_out != NULL && !access_ok(user_type_out, sizeof(uint32_t))) {
        return -EFAULT;
    }
    if (user_data != NULL && max_size > 0) {
        if (!access_ok(user_data, max_size)) {
            return -EFAULT;
        }
        kdata = vos3_kmalloc(max_size);
        if (kdata == NULL) {
            return -ENOMEM;
        }
    }

    /* Receive into kernel buffers */
    result = vos3_msgq_recv(id, user_type_out ? &ktype : NULL, kdata, max_size, flags);

    if (result >= 0) {
        /* Copy results to user space */
        if (user_type_out != NULL) {
            if (copy_to_user(user_type_out, &ktype, sizeof(uint32_t)) != 0) {
                if (kdata) vos3_kfree(kdata);
                return -EFAULT;
            }
        }
        if (user_data != NULL && result > 0) {
            if (copy_to_user(user_data, kdata, (size_t)result) != 0) {
                if (kdata) vos3_kfree(kdata);
                return -EFAULT;
            }
        }
    }

    if (kdata) vos3_kfree(kdata);
    return result;
}

/**
 * @brief sys_shm_create - Create shared memory (Phase 29: Hardened)
 */
static int64_t sys_shm_create(vos3_syscall_frame_t* frame)
{
    const char* user_name = (const char*)frame->rdi;
    size_t size = (size_t)frame->rsi;
    uint32_t flags = (uint32_t)frame->rdx;

    char kname[64];
    const char* name_ptr = NULL;

    /* Phase 29: Validate and copy name from user space */
    if (user_name != NULL) {
        int64_t len = strncpy_from_user(kname, user_name, sizeof(kname));
        if (len < 0) {
            return -EFAULT;
        }
        name_ptr = kname;
    }

    vos3_ipc_id_t id = vos3_shm_create(name_ptr, size, flags);
    if (id == VOS3_IPC_EEXIST) {
        return -17;  /* -EEXIST */
    }
    if (id == VOS3_IPC_INVALID) {
        return -12;  /* -ENOMEM */
    }
    return (int64_t)id;
}

/**
 * @brief sys_shm_destroy - Destroy shared memory
 */
static int64_t sys_shm_destroy(vos3_syscall_frame_t* frame)
{
    if (frame->rdi > UINT32_MAX) return -EINVAL;
    vos3_ipc_id_t id = (vos3_ipc_id_t)frame->rdi;
    return (int64_t)vos3_shm_destroy(id);
}

/* Only IPC_RMID is supported. Other Linux commands must not close a region. */
static int64_t sys_shmctl(vos3_syscall_frame_t* frame)
{
    if (frame->rsi != 0) return -EINVAL;
    return sys_shm_destroy(frame);
}

/**
 * @brief sys_shm_map - Map shared memory
 */
static int64_t sys_shm_map(vos3_syscall_frame_t* frame)
{
    if (frame->rdi > UINT32_MAX) return -EINVAL;
    vos3_ipc_id_t id = (vos3_ipc_id_t)frame->rdi;
    uint32_t flags = (uint32_t)frame->rsi;

    void* addr = vos3_shm_map(id, flags);
    return (int64_t)(uintptr_t)addr;
}

/**
 * @brief sys_shm_unmap - Unmap shared memory
 */
static int64_t sys_shm_unmap(vos3_syscall_frame_t* frame)
{
    if (frame->rdi > UINT32_MAX) return -EINVAL;
    vos3_ipc_id_t id = (vos3_ipc_id_t)frame->rdi;
    void* addr = (void*)frame->rsi;
    return (int64_t)vos3_shm_unmap(id, addr);
}

/**
 * @brief sys_shm_size - Get shared memory region size
 */
static int64_t sys_shm_size(vos3_syscall_frame_t* frame)
{
    if (frame->rdi > UINT32_MAX) return -EINVAL;
    vos3_ipc_id_t id = (vos3_ipc_id_t)frame->rdi;
    return (int64_t)vos3_shm_size(id);
}

/**
 * @brief sys_shm_create_device - Create device MMIO shared memory
 */
static int64_t sys_shm_create_device(vos3_syscall_frame_t* frame)
{
    const char* user_name = (const char*)frame->rdi;
    uint64_t phys_addr = (uint64_t)frame->rsi;
    size_t size = (size_t)frame->rdx;
    uint32_t flags = (uint32_t)frame->r10;

    char kname[64];
    const char* name_ptr = NULL;

    if (user_name != NULL) {
        int64_t len = strncpy_from_user(kname, user_name, sizeof(kname));
        if (len < 0) {
            return -14; /* EFAULT */
        }
        name_ptr = kname;
    }

    vos3_ipc_id_t id = vos3_shm_create_device(name_ptr, phys_addr, size, flags);
    return (int64_t)id;
}

/**
 * @brief sys_pipe_create - Create pipe (Phase 29: Hardened)
 */
static int64_t sys_pipe_create(vos3_syscall_frame_t* frame)
{
    vos3_ipc_id_t* user_read_id = (vos3_ipc_id_t*)frame->rdi;
    vos3_ipc_id_t* user_write_id = (vos3_ipc_id_t*)frame->rsi;

    vos3_ipc_id_t kread_id, kwrite_id;
    int result;

    /* Phase 29: Validate user pointers */
    if (user_read_id == NULL || user_write_id == NULL) {
        return -EINVAL;
    }
    if (!access_ok(user_read_id, sizeof(vos3_ipc_id_t)) ||
        !access_ok(user_write_id, sizeof(vos3_ipc_id_t))) {
        return -EFAULT;
    }

    /* Create pipe with kernel buffers */
    result = vos3_pipe_create(&kread_id, &kwrite_id);
    if (result != 0) {
        return (int64_t)result;
    }

    /* Copy IDs to user space */
    if (copy_to_user(user_read_id, &kread_id, sizeof(vos3_ipc_id_t)) != 0 ||
        copy_to_user(user_write_id, &kwrite_id, sizeof(vos3_ipc_id_t)) != 0) {
        /* Cleanup on failure */
        vos3_pipe_close(kread_id, 0);
        vos3_pipe_close(kwrite_id, 1);
        return -EFAULT;
    }

    return 0;
}

/**
 * @brief sys_pipe_read - Read from pipe (Phase 29: Hardened)
 */
static int64_t sys_pipe_read(vos3_syscall_frame_t* frame)
{
    vos3_ipc_id_t id = (vos3_ipc_id_t)frame->rdi;
    void* user_buf = (void*)frame->rsi;
    size_t count = (size_t)frame->rdx;

    void* kbuf;
    int64_t result;

    /* Phase 29: Validate user pointer */
    if (user_buf == NULL || count == 0) {
        return -EINVAL;
    }
    if (!access_ok(user_buf, count)) {
        return -EFAULT;
    }

    /* Allocate kernel buffer */
    kbuf = vos3_kmalloc(count);
    if (kbuf == NULL) {
        return -ENOMEM;
    }

    /* Read into kernel buffer */
    result = vos3_pipe_read(id, kbuf, count);

    /* Copy to user space on success */
    if (result > 0) {
        if (copy_to_user(user_buf, kbuf, (size_t)result) != 0) {
            vos3_kfree(kbuf);
            return -EFAULT;
        }
    }

    vos3_kfree(kbuf);
    return result;
}

/**
 * @brief sys_pipe_write - Write to pipe (Phase 29: Hardened)
 */
static int64_t sys_pipe_write(vos3_syscall_frame_t* frame)
{
    vos3_ipc_id_t id = (vos3_ipc_id_t)frame->rdi;
    const void* user_buf = (const void*)frame->rsi;
    size_t count = (size_t)frame->rdx;

    void* kbuf;
    int64_t result;

    /* Phase 29: Validate user pointer */
    if (user_buf == NULL || count == 0) {
        return -EINVAL;
    }
    if (!access_ok(user_buf, count)) {
        return -EFAULT;
    }

    /* Allocate kernel buffer and copy from user */
    kbuf = vos3_kmalloc(count);
    if (kbuf == NULL) {
        return -ENOMEM;
    }

    if (copy_from_user(kbuf, user_buf, count) != 0) {
        vos3_kfree(kbuf);
        return -EFAULT;
    }

    /* Write from kernel buffer */
    result = vos3_pipe_write(id, kbuf, count);

    vos3_kfree(kbuf);
    return result;
}

/**
 * @brief sys_pipe_close - Close pipe end
 */
static int64_t sys_pipe_close(vos3_syscall_frame_t* frame)
{
    vos3_ipc_id_t id = (vos3_ipc_id_t)frame->rdi;
    int is_write = (int)frame->rsi;

    return (int64_t)vos3_pipe_close(id, is_write);
}

/**
 * @brief sys_kill - Send signal to task
 */
static int64_t sys_kill(vos3_syscall_frame_t* frame)
{
    vos3_tid_t tid = (vos3_tid_t)frame->rdi;
    int signum = (int)frame->rsi;

    return (int64_t)vos3_signal_send(tid, signum);
}

/**
 * @brief sys_signal - Set signal handler (simple interface)
 */
static int64_t sys_signal(vos3_syscall_frame_t* frame)
{
    int signum = (int)frame->rdi;
    vos3_sighandler_t handler = (vos3_sighandler_t)frame->rsi;

    vos3_sighandler_t old = vos3_signal_handler(signum, handler);
    return (int64_t)(uintptr_t)old;
}

/**
 * @brief sys_sigaction - Set signal action (Phase 29: Hardened)
 */
static int64_t sys_sigaction(vos3_syscall_frame_t* frame)
{
    int signum = (int)frame->rdi;
    const vos3_sigaction_t* user_act = (const vos3_sigaction_t*)frame->rsi;
    vos3_sigaction_t* user_oldact = (vos3_sigaction_t*)frame->rdx;

    vos3_sigaction_t kact, koldact;
    const vos3_sigaction_t* act_ptr = NULL;
    vos3_sigaction_t* oldact_ptr = NULL;
    int result;

    /* Phase 29: Validate and copy from user space */
    if (user_act != NULL) {
        if (!access_ok(user_act, sizeof(vos3_sigaction_t))) {
            return -EFAULT;
        }
        if (copy_from_user(&kact, user_act, sizeof(vos3_sigaction_t)) != 0) {
            return -EFAULT;
        }
        act_ptr = &kact;
    }
    if (user_oldact != NULL) {
        if (!access_ok(user_oldact, sizeof(vos3_sigaction_t))) {
            return -EFAULT;
        }
        oldact_ptr = &koldact;
    }

    result = vos3_sigaction(signum, act_ptr, oldact_ptr);

    /* Copy old action to user space */
    if (result == 0 && user_oldact != NULL) {
        if (copy_to_user(user_oldact, &koldact, sizeof(vos3_sigaction_t)) != 0) {
            return -EFAULT;
        }
    }

    return (int64_t)result;
}

/**
 * @brief sys_sigprocmask - Block/unblock signals (Phase 29: Hardened)
 */
static int64_t sys_sigprocmask(vos3_syscall_frame_t* frame)
{
    int how = (int)frame->rdi;
    const uint32_t* user_set = (const uint32_t*)frame->rsi;
    uint32_t* user_oldset = (uint32_t*)frame->rdx;

    uint32_t kset, koldset;
    const uint32_t* set_ptr = NULL;
    uint32_t* oldset_ptr = NULL;
    int result;

    /* Phase 29: Validate and copy from user space */
    if (user_set != NULL) {
        if (!access_ok(user_set, sizeof(uint32_t))) {
            return -EFAULT;
        }
        if (copy_from_user(&kset, user_set, sizeof(uint32_t)) != 0) {
            return -EFAULT;
        }
        set_ptr = &kset;
    }
    if (user_oldset != NULL) {
        if (!access_ok(user_oldset, sizeof(uint32_t))) {
            return -EFAULT;
        }
        oldset_ptr = &koldset;
    }

    result = vos3_sigprocmask(how, set_ptr, oldset_ptr);

    /* Copy old set to user space */
    if (result == 0 && user_oldset != NULL) {
        if (copy_to_user(user_oldset, &koldset, sizeof(uint32_t)) != 0) {
            return -EFAULT;
        }
    }

    return (int64_t)result;
}

/**
 * @brief sys_sigwait - Wait for signal (Phase 29: Hardened)
 */
static int64_t sys_sigwait(vos3_syscall_frame_t* frame)
{
    const uint32_t* user_set = (const uint32_t*)frame->rdi;

    uint32_t kset;

    /* Phase 29: Validate and copy from user space */
    if (user_set == NULL) {
        return -EINVAL;
    }
    if (!access_ok(user_set, sizeof(uint32_t))) {
        return -EFAULT;
    }
    if (copy_from_user(&kset, user_set, sizeof(uint32_t)) != 0) {
        return -EFAULT;
    }

    return (int64_t)vos3_sigwait(&kset);
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

int vos3_ipc_init(void)
{
    if (g_ipc_initialized != 0) {
        return VOS3_IPC_ERR_INVALID;
    }

    VOS3_INFO("Initializing IPC subsystem");

    /* Register IPC system calls */

    /* Message queues */
    vos3_syscall_register(VOS3_SYS_MSGQ_CREATE, sys_msgq_create);
    vos3_syscall_register(VOS3_SYS_MSGQ_DESTROY, sys_msgq_destroy);
    vos3_syscall_register(VOS3_SYS_MSGQ_SEND, sys_msgq_send);
    vos3_syscall_register(VOS3_SYS_MSGQ_RECV, sys_msgq_recv);

    /* Shared memory */
    vos3_syscall_register(VOS3_SYS_SHM_CREATE, sys_shm_create);
    vos3_syscall_register(VOS3_SYS_SHM_DESTROY, sys_shm_destroy);
    vos3_syscall_register(VOS3_SYS_SHM_MAP, sys_shm_map);
    vos3_syscall_register(VOS3_SYS_SHM_UNMAP, sys_shm_unmap);
    vos3_syscall_register(VOS3_SYS_SHM_SIZE, sys_shm_size);
    vos3_syscall_register(VOS3_SYS_SHM_CREATE_DEVICE, sys_shm_create_device);

    /* Shared memory — Linux x86-64 ABI aliases */
    vos3_syscall_register(LINUX_SYS_SHMGET, sys_shm_create);   /* shmget(29) → create */
    vos3_syscall_register(LINUX_SYS_SHMAT,  sys_shm_map);      /* shmat(30)  → map */
    vos3_syscall_register(LINUX_SYS_SHMCTL, sys_shmctl);   /* shmctl(31) → destroy */
    vos3_syscall_register(LINUX_SYS_SHMDT,  sys_shm_unmap);    /* shmdt(67)  → unmap */

    /* Pipes */
    vos3_syscall_register(VOS3_SYS_PIPE_CREATE, sys_pipe_create);
    vos3_syscall_register(VOS3_SYS_PIPE_READ, sys_pipe_read);
    vos3_syscall_register(VOS3_SYS_PIPE_WRITE, sys_pipe_write);
    vos3_syscall_register(VOS3_SYS_PIPE_CLOSE, sys_pipe_close);

    /* Signals */
    vos3_syscall_register(VOS3_SYS_KILL, sys_kill);
    vos3_syscall_register(VOS3_SYS_SIGNAL, sys_signal);
    vos3_syscall_register(VOS3_SYS_SIGACTION, sys_sigaction);
    vos3_syscall_register(VOS3_SYS_SIGPROCMASK, sys_sigprocmask);
    vos3_syscall_register(VOS3_SYS_SIGWAIT, sys_sigwait);

    /* Dispatcher (Phase 3) */
    vos3_dispatcher_init();

    g_ipc_initialized = 1;

    VOS3_INFO("IPC subsystem initialized");
    VOS3_INFO("  Message queues: max %zu", (size_t)VOS3_IPC_MAX_OBJECTS);
    VOS3_INFO("  Shared memory:  max %zu regions", (size_t)VOS3_SHM_MAX_REGIONS);
    VOS3_INFO("  Pipes:          max %zu", (size_t)VOS3_PIPE_MAX);
    VOS3_INFO("  Signals:        1-%d", VOS3_SIG_MAX);

    return VOS3_IPC_OK;
}
