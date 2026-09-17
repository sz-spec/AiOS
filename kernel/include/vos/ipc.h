/**
 * @file ipc.h
 * @brief VOS3 Inter-Process Communication
 *
 * @details Message queues, shared memory, pipes, and signals for
 *          inter-process and inter-thread communication.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_IPC_H
#define VOS3_IPC_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "task.h"
#include "sync.h"

/* ============================================================================
 * IPC CONFIGURATION
 * ============================================================================ */

/** @brief Maximum message size */
#define VOS3_MSG_MAX_SIZE       ((size_t)4096U)

/** @brief Maximum messages per queue */
#define VOS3_MSG_QUEUE_MAX      ((size_t)64U)

/** @brief Maximum IPC objects */
#define VOS3_IPC_MAX_OBJECTS    ((size_t)256U)

/** @brief Maximum shared memory regions */
#define VOS3_SHM_MAX_REGIONS    ((size_t)64U)

/** @brief Maximum pipes */
#define VOS3_PIPE_MAX           ((size_t)128U)

/** @brief Pipe buffer size */
#define VOS3_PIPE_BUFFER_SIZE   ((size_t)4096U)

/** @brief Maximum signal number */
#define VOS3_SIG_MAX            ((int)31)

/* ============================================================================
 * IPC TYPES
 * ============================================================================ */

/** @brief IPC object ID type */
typedef uint32_t vos3_ipc_id_t;

/** @brief Invalid IPC ID */
#define VOS3_IPC_INVALID        ((vos3_ipc_id_t)0xFFFFFFFFU)

/** @brief IPC ID indicating name already exists */
#define VOS3_IPC_EEXIST         ((vos3_ipc_id_t)0xFFFFFFFEU)

/** @brief IPC object types */
typedef enum vos3_ipc_type {
    VOS3_IPC_TYPE_NONE      = 0,
    VOS3_IPC_TYPE_MSGQUEUE  = 1,
    VOS3_IPC_TYPE_SHM       = 2,
    VOS3_IPC_TYPE_PIPE      = 3,
    VOS3_IPC_TYPE_SIGNAL    = 4
} vos3_ipc_type_t;

/* ============================================================================
 * MESSAGE QUEUE
 * ============================================================================ */

/** @brief Message queue magic */
#define VOS3_MSGQ_MAGIC         ((uint32_t)0x4D534751U)  /* "MSGQ" */

/**
 * @brief Message header
 */
typedef struct vos3_msg_header {
    vos3_tid_t sender;          /**< Sender task ID */
    uint32_t type;              /**< Message type (user-defined) */
    size_t size;                /**< Payload size */
    uint64_t timestamp;         /**< Send timestamp */
} vos3_msg_header_t;

/**
 * @brief Message structure
 */
typedef struct vos3_message {
    vos3_msg_header_t header;   /**< Message header */
    uint8_t data[];             /**< Variable-length payload */
} vos3_message_t;

/**
 * @brief Message queue entry (internal)
 */
typedef struct vos3_msgq_entry {
    struct vos3_msgq_entry* next;
    size_t total_size;          /**< Header + payload size */
    vos3_message_t msg;         /**< Message (variable size) */
} vos3_msgq_entry_t;

/**
 * @brief Message queue structure
 */
typedef struct vos3_msgqueue {
    uint32_t magic;             /**< Magic for validation */
    vos3_ipc_id_t id;           /**< Queue ID */
    char name[32];              /**< Queue name */

    vos3_msgq_entry_t* head;    /**< First message */
    vos3_msgq_entry_t* tail;    /**< Last message */
    size_t count;               /**< Message count */
    size_t max_count;           /**< Maximum messages */
    size_t max_msg_size;        /**< Maximum message size */

    vos3_spinlock_t lock;       /**< Short, non-sleeping message-list lock */
    vos3_semaphore_t sem_space; /**< Space available */
    vos3_semaphore_t sem_msgs;  /**< Messages available */
    vos3_atomic32_t active_ops; /**< Registry reference plus operation pins */
    volatile uint32_t closing;  /**< Removed from table; reject new pins */

    vos3_tid_t owner;           /**< Owner task */
    uint64_t owner_identity;    /**< Non-reusable creator principal */
    uint32_t flags;             /**< Queue flags */
} vos3_msgqueue_t;

/** @brief Message queue flags */
typedef enum vos3_msgq_flags {
    VOS3_MSGQ_FLAG_NONE     = 0U,
    VOS3_MSGQ_FLAG_NONBLOCK = (1U << 0),   /**< Non-blocking operations */
    VOS3_MSGQ_FLAG_PRIORITY = (1U << 1),   /**< Priority-based ordering */
} vos3_msgq_flags_t;

/* Message Queue Functions */

/**
 * @brief Create a message queue
 * @param[in] name Queue name (may be NULL)
 * @param[in] max_msgs Maximum messages
 * @param[in] max_size Maximum message size
 * @return Queue ID, or VOS3_IPC_INVALID on failure
 */
vos3_ipc_id_t vos3_msgq_create(const char* name, size_t max_msgs, size_t max_size);

/**
 * @brief Destroy a message queue
 * @param[in] id Queue ID
 * @return 0 on success, negative error on failure
 */
int vos3_msgq_destroy(vos3_ipc_id_t id);

/**
 * @brief Send a message
 * @param[in] id Queue ID
 * @param[in] type Message type
 * @param[in] data Message data
 * @param[in] size Data size
 * @param[in] flags Send flags
 * @return 0 on success, negative error on failure
 */
int vos3_msgq_send(vos3_ipc_id_t id, uint32_t type,
                   const void* data, size_t size, uint32_t flags);

/**
 * @brief Receive a message
 * @param[in] id Queue ID
 * @param[out] type_out Message type (may be NULL)
 * @param[out] data Buffer for message data
 * @param[in] max_size Buffer size
 * @param[in] flags Receive flags
 * @return Bytes received, or negative error
 */
int64_t vos3_msgq_recv(vos3_ipc_id_t id, uint32_t* type_out,
                       void* data, size_t max_size, uint32_t flags);

/**
 * @brief Get message queue by name
 * @param[in] name Queue name
 * @return Queue ID, or VOS3_IPC_INVALID if not found
 */
vos3_ipc_id_t vos3_msgq_find(const char* name);

/**
 * @brief Get message count
 * @param[in] id Queue ID
 * @return Message count, or negative error
 */
int64_t vos3_msgq_count(vos3_ipc_id_t id);

/* ============================================================================
 * SHARED MEMORY
 * ============================================================================ */

/** @brief Shared memory magic */
#define VOS3_SHM_MAGIC          ((uint32_t)0x53484D45U)  /* "SHME" */

/**
 * @brief Shared memory region
 */
typedef struct vos3_shm_region {
    uint32_t magic;             /**< Magic for validation */
    vos3_ipc_id_t id;           /**< Region ID */
    char name[32];              /**< Region name */

    void* kernel_addr;          /**< Kernel virtual address */
    uint64_t phys_addr;         /**< Physical address */
    size_t size;                /**< Region size */

    uint32_t ref_count;         /**< Reference count */
    uint32_t flags;             /**< Region flags */

    vos3_mutex_t lock;          /**< Region lock */
    vos3_tid_t owner;           /**< Diagnostic TID; not authorization */
    uint64_t owner_identity;    /**< Immutable creator principal; zero is kernel-only */
} vos3_shm_region_t;

/** @brief Shared memory flags */
typedef enum vos3_shm_flags {
    VOS3_SHM_FLAG_NONE      = 0U,
    VOS3_SHM_FLAG_READONLY  = (1U << 0),   /**< Read-only mapping */
    VOS3_SHM_FLAG_EXEC      = (1U << 1),   /**< Executable */
    VOS3_SHM_FLAG_NOCACHE   = (1U << 2),   /**< Disable caching */
    VOS3_SHM_FLAG_HUGETLB   = (1U << 4),   /**< Use 2MB HugePages */
    VOS3_SHM_FLAG_DEVICE    = (1U << 5),   /**< Device MMIO mapping (fixed phys addr, no PMM alloc) */
    VOS3_SHM_FLAG_PUBLIC    = (1U << 6),   /**< Allow cross-task mapping (default: owner-only) */
} vos3_shm_flags_t;

/* Shared Memory Functions */

/**
 * @brief Create a shared memory region
 * @param[in] name Region name (may be NULL)
 * @param[in] size Region size
 * @param[in] flags Region flags
 * @return Region ID, or VOS3_IPC_INVALID on failure
 */
vos3_ipc_id_t vos3_shm_create(const char* name, size_t size, uint32_t flags);

/**
 * @brief Create a shared memory region for device MMIO (fixed physical address)
 * @param[in] name Region name (may be NULL)
 * @param[in] phys_addr Fixed physical address (device BAR)
 * @param[in] size Region size
 * @param[in] flags Region flags (VOS3_SHM_FLAG_DEVICE required)
 * @return Region ID, or VOS3_IPC_INVALID on failure
 */
vos3_ipc_id_t vos3_shm_create_device(const char* name, uint64_t phys_addr,
                                       size_t size, uint32_t flags);

/**
 * @brief Release creator ownership; only the creating task principal may close
 * @param[in] id Region ID
 * @return 0 on success, negative error on failure
 */
int vos3_shm_destroy(vos3_ipc_id_t id);
/* Kernel lifecycle APIs: notification is IRQ-safe; drain needs safe process context. */
void vos3_shm_owner_exit(uint64_t identity);
void vos3_shm_reap_creators(void);

/**
 * @brief Map shared memory into current address space
 * @param[in] id Region ID
 * @param[in] flags Mapping flags
 * @return Mapped address, or NULL on failure
 */
void* vos3_shm_map(vos3_ipc_id_t id, uint32_t flags);

/**
 * @brief Unmap shared memory
 * @param[in] id   Region ID
 * @param[in] addr User-space address returned by vos3_shm_map (NULL for kernel)
 * @return 0 on success, negative error on failure
 */
int vos3_shm_unmap(vos3_ipc_id_t id, void* addr);

/**
 * @brief Find shared memory by name
 * @param[in] name Region name
 * @return Region ID, or VOS3_IPC_INVALID if not found
 */
vos3_ipc_id_t vos3_shm_find(const char* name);

/**
 * @brief Get shared memory size
 * @param[in] id Region ID
 * @return Size in bytes, or 0 on error
 */
size_t vos3_shm_size(vos3_ipc_id_t id);

/* ============================================================================
 * PIPES
 * ============================================================================ */

/** @brief Pipe magic */
#define VOS3_PIPE_MAGIC         ((uint32_t)0x50495045U)  /* "PIPE" */

/**
 * @brief Pipe structure
 */
typedef struct vos3_pipe {
    uint32_t magic;             /**< Magic for validation */
    vos3_ipc_id_t id;           /**< Pipe ID */

    uint8_t* buffer;            /**< Circular buffer */
    size_t buf_size;            /**< Buffer size */
    size_t read_pos;            /**< Read position */
    size_t write_pos;           /**< Write position */
    size_t data_size;           /**< Data in buffer */

    vos3_mutex_t lock;          /**< Pipe lock */
    vos3_semaphore_t sem_read;  /**< Data available for reading */
    vos3_semaphore_t sem_write; /**< Space available for writing */

    vos3_tid_t reader;          /**< Reader task (0 = any) */
    vos3_tid_t writer;          /**< Writer task (0 = any) */

    uint32_t flags;             /**< Pipe flags */
    int read_closed;            /**< Read end closed */
    int write_closed;           /**< Write end closed */
    uint32_t ref_count;         /**< Active references (endpoints + in-flight ops) */
} vos3_pipe_t;

/** @brief Pipe flags */
typedef enum vos3_pipe_flags {
    VOS3_PIPE_FLAG_NONE     = 0U,
    VOS3_PIPE_FLAG_NONBLOCK = (1U << 0),   /**< Non-blocking I/O */
} vos3_pipe_flags_t;

/* Pipe Functions */

/**
 * @brief Create a pipe
 * @param[out] read_id Read end ID
 * @param[out] write_id Write end ID
 * @return 0 on success, negative error on failure
 */
int vos3_pipe_create(vos3_ipc_id_t* read_id, vos3_ipc_id_t* write_id);

/**
 * @brief Create a named pipe
 * @param[in] name Pipe name
 * @param[in] flags Pipe flags
 * @return Pipe ID, or VOS3_IPC_INVALID on failure
 */
vos3_ipc_id_t vos3_pipe_create_named(const char* name, uint32_t flags);

/**
 * @brief Close a pipe end
 * @param[in] id Pipe ID
 * @param[in] is_write 1 to close write end, 0 for read end
 * @return 0 on success, negative error on failure
 */
int vos3_pipe_close(vos3_ipc_id_t id, int is_write);

/**
 * @brief Read from pipe
 * @param[in] id Pipe ID
 * @param[out] buf Buffer for data
 * @param[in] count Bytes to read
 * @return Bytes read, or negative error
 */
int64_t vos3_pipe_read(vos3_ipc_id_t id, void* buf, size_t count);

/**
 * @brief Write to pipe
 * @param[in] id Pipe ID
 * @param[in] buf Data to write
 * @param[in] count Bytes to write
 * @return Bytes written, or negative error
 */
int64_t vos3_pipe_write(vos3_ipc_id_t id, const void* buf, size_t count);

/**
 * @brief Find named pipe
 * @param[in] name Pipe name
 * @return Pipe ID, or VOS3_IPC_INVALID if not found
 */
vos3_ipc_id_t vos3_pipe_find(const char* name);

/* ============================================================================
 * SIGNALS
 * ============================================================================ */

/** @brief Signal numbers */
typedef enum vos3_signal {
    VOS3_SIGHUP     = 1,    /**< Hangup */
    VOS3_SIGINT     = 2,    /**< Interrupt (Ctrl+C) */
    VOS3_SIGQUIT    = 3,    /**< Quit */
    VOS3_SIGILL     = 4,    /**< Illegal instruction */
    VOS3_SIGTRAP    = 5,    /**< Trace trap */
    VOS3_SIGABRT    = 6,    /**< Abort */
    VOS3_SIGBUS     = 7,    /**< Bus error */
    VOS3_SIGFPE     = 8,    /**< Floating point exception */
    VOS3_SIGKILL    = 9,    /**< Kill (cannot be caught) */
    VOS3_SIGUSR1    = 10,   /**< User signal 1 */
    VOS3_SIGSEGV    = 11,   /**< Segmentation fault */
    VOS3_SIGUSR2    = 12,   /**< User signal 2 */
    VOS3_SIGPIPE    = 13,   /**< Broken pipe */
    VOS3_SIGALRM    = 14,   /**< Alarm clock */
    VOS3_SIGTERM    = 15,   /**< Termination */
    VOS3_SIGCHLD    = 17,   /**< Child status changed */
    VOS3_SIGCONT    = 18,   /**< Continue */
    VOS3_SIGSTOP    = 19,   /**< Stop (cannot be caught) */
    VOS3_SIGTSTP    = 20,   /**< Terminal stop */
} vos3_signal_t;

/** @brief Signal handler function type */
typedef void (*vos3_sighandler_t)(int signum);

/** @brief Default signal handler */
#define VOS3_SIG_DFL    ((vos3_sighandler_t)0)

/** @brief Ignore signal */
#define VOS3_SIG_IGN    ((vos3_sighandler_t)1)

/** @brief Signal action flags */
typedef enum vos3_sigaction_flags {
    VOS3_SA_NONE        = 0U,
    VOS3_SA_RESTART     = (1U << 0),   /**< Restart interrupted syscalls */
    VOS3_SA_NODEFER     = (1U << 1),   /**< Don't block signal during handler */
    VOS3_SA_RESETHAND   = (1U << 2),   /**< Reset to SIG_DFL after handling */
} vos3_sigaction_flags_t;

/**
 * @brief Signal action structure
 */
typedef struct vos3_sigaction {
    vos3_sighandler_t handler;  /**< Signal handler */
    uint32_t mask;              /**< Signals to block during handler */
    uint32_t flags;             /**< Action flags */
    uint64_t sa_restorer;       /**< User-space signal return trampoline (SA_RESTORER) */
} vos3_sigaction_t;

/**
 * @brief SA_RESTORER flag (musl sets this to indicate restorer is valid)
 */
#define VOS3_SA_RESTORER    0x04000000U

/**
 * @brief Signal frame pushed onto user stack for signal delivery.
 *
 * Layout: pretcode is at the lowest address (acts as return address).
 * When the handler does 'ret', it returns to sa_restorer (__restore_rt),
 * which calls rt_sigreturn(15) to restore pre-signal state.
 */
typedef struct vos3_sigframe {
    uint64_t pretcode;       /**< Return address = sa_restorer (__restore_rt) */
    uint64_t saved_rax;      /**< Syscall return value to restore */
    uint64_t saved_rdi;
    uint64_t saved_rsi;
    uint64_t saved_rdx;
    uint64_t saved_r10;
    uint64_t saved_r8;
    uint64_t saved_r9;
    uint64_t saved_rcx;      /**< Original user RIP */
    uint64_t saved_r11;      /**< Original user RFLAGS */
    uint64_t saved_rbx;
    uint64_t saved_rbp;
    uint64_t saved_r12;
    uint64_t saved_r13;
    uint64_t saved_r14;
    uint64_t saved_r15;
    uint64_t saved_rsp;      /**< Original user RSP */
    uint64_t signo;          /**< Signal number (for debugging) */
    uint64_t saved_blocked;  /**< Internal pre-handler mask, restored via sigprocmask */
} __attribute__((packed)) vos3_sigframe_t;

/**
 * @brief Pending signals for a task
 */
typedef struct vos3_sigpending {
    uint32_t pending;           /**< Pending signal bitmap */
    uint32_t blocked;           /**< Blocked signal bitmap */
    vos3_sigaction_t actions[VOS3_SIG_MAX + 1];  /**< Signal actions */
} vos3_sigpending_t;

/* Pending/mask belong to each task. Only dispositions may be shared. */
typedef struct vos3_signal_actions {
    uint32_t refs; /* protected by the signal lock, checked before increment */
    vos3_sigaction_t actions[VOS3_SIG_MAX + 1];
} vos3_signal_actions_t;

typedef struct vos3_signal_state {
    uint32_t pending;
    uint32_t blocked;
    vos3_signal_actions_t* handlers;
} vos3_signal_state_t;

/* Initialize/clone before publication; destroy only after task retirement.
 * Clone starts with no pending signals and inherits the blocked mask.
 * Exec atomically detaches dispositions, resets caught handlers, retains IGN.
 * No API pins a remotely looked-up task: callers own task lifetime. */
int vos3_signal_task_init(vos3_task_t* task);
int vos3_signal_task_clone(vos3_task_t* child, const vos3_task_t* parent,
                           int share_handlers);
void vos3_signal_task_destroy(vos3_task_t* task);
int vos3_signal_task_exec(vos3_task_t* task);

/* Signal Functions */

/**
 * @brief Send a signal to a task
 * @param[in] tid Target task ID
 * @param[in] signum Signal number
 * @return 0 on success, negative error on failure
 */
int vos3_signal_send(vos3_tid_t tid, int signum);

/* Shared syscall authorization; internal kernel senders use signal_send. */
int vos3_signal_check_permission(vos3_task_t* sender, vos3_task_t* target, int sig);

/**
 * @brief Set signal handler
 * @param[in] signum Signal number
 * @param[in] handler New handler
 * @return Previous handler, or VOS3_SIG_ERR on error
 */
vos3_sighandler_t vos3_signal_handler(int signum, vos3_sighandler_t handler);

/**
 * @brief Set signal action
 * @param[in] signum Signal number
 * @param[in] act New action (may be NULL to query)
 * @param[out] oldact Previous action (may be NULL)
 * @return 0 on success, negative error on failure
 */
int vos3_sigaction(int signum, const vos3_sigaction_t* act,
                   vos3_sigaction_t* oldact);

/**
 * @brief Block/unblock signals
 * @param[in] how How to modify mask (SIG_BLOCK, SIG_UNBLOCK, SIG_SETMASK)
 * @param[in] set New signal set
 * @param[out] oldset Previous set (may be NULL)
 * @return 0 on success, negative error on failure
 */
int vos3_sigprocmask(int how, const uint32_t* set, uint32_t* oldset);

/** @brief Signal mask operations */
#define VOS3_SIG_BLOCK      0   /**< Block signals in set */
#define VOS3_SIG_UNBLOCK    1   /**< Unblock signals in set */
#define VOS3_SIG_SETMASK    2   /**< Set mask to set */

/**
 * @brief Wait for signal
 * @param[in] set Signals to wait for
 * @return Signal number received, or negative error
 */
int vos3_sigwait(const uint32_t* set);

/**
 * @brief Deliver pending signals to user space via stack frame.
 * @param[in,out] frame  Syscall frame (modified if signal delivered)
 * @param[in]     result Syscall return value (saved in sigframe for rt_sigreturn)
 * @note Called before returning to user mode after every syscall.
 */
struct vos3_syscall_frame;
void vos3_signal_deliver(struct vos3_syscall_frame *frame, int64_t result);

/**
 * @brief Initialize signal handling for a task
 * @param[in] task Task to initialize
 */
void vos3_signal_init_task(vos3_task_t* task);

/* ============================================================================
 * IPC INITIALIZATION
 * ============================================================================ */

/**
 * @brief Initialize IPC subsystem
 * @return 0 on success, negative error on failure
 */
int vos3_ipc_init(void);

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_IPC_OK             (0)
#define VOS3_IPC_ERR_NOMEM      (-1)
#define VOS3_IPC_ERR_INVALID    (-2)
#define VOS3_IPC_ERR_NOTFOUND   (-3)
#define VOS3_IPC_ERR_FULL       (-4)
#define VOS3_IPC_ERR_EMPTY      (-5)
#define VOS3_IPC_ERR_WOULDBLOCK (-11)
#define VOS3_IPC_ERR_INTR       (-12)
#define VOS3_IPC_ERR_PIPE       (-13)
#define VOS3_IPC_ERR_ACCESS     (-13) /* EACCES for SHM authorization */

/** @brief Error return for signal handler */
#define VOS3_SIG_ERR    ((vos3_sighandler_t)-1)

#ifdef __cplusplus
}
#endif

#endif /* VOS3_IPC_H */
