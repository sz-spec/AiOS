/**
 * @file fd.c
 * @brief VOS3 File Descriptor Management
 *
 * @details Per-task file descriptor tables.
 *
 * All locking uses IRQ-safe spinlocks only (no mutexes).  This is critical
 * because vos3_fd_table_destroy() may be called from vos3_task_reap() in
 * timer ISR context (via vos3_sched_tick()) -- sleeping in ISR context is
 * a deadlock.
 *
 * file->ref_count and fd_table->ref_count are manipulated with GCC
 * __atomic builtins to prevent races in concurrent fork/clone paths.
 *
 * @version 2.0.0
 * @date 2026-03-19
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/vfs.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * FILE DESCRIPTOR TABLE
 * ============================================================================ */

int vos3_fd_table_init(vos3_fd_table_t* table)
{
    if (table == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    memset(table->entries, 0, sizeof(table->entries));
    vos3_spinlock_init(&table->spinlock);
    __atomic_store_n(&table->ref_count, 1U, __ATOMIC_RELEASE);

    return VOS3_FS_OK;
}

void vos3_fd_table_destroy(vos3_fd_table_t* table)
{
    if (table == NULL) {
        return;
    }

    /*
     * Snapshot all open files under the spinlock, then close them outside
     * the lock. Close handlers may sleep (e.g., pipe_close acquires a mutex),
     * so we must NOT hold the spinlock during close.
     *
     * This function may be called from vos3_task_reap() in timer ISR context
     * via vos3_sched_tick(), so we use IRQ-safe spinlock only (no mutex).
     */
    vos3_file_t* to_close[VOS3_MAX_FD];
    int close_count = 0;

    uint64_t irqflags;
    __asm__ volatile("pushfq; pop %0; cli" : "=r"(irqflags));
    vos3_spinlock_acquire(&table->spinlock);

    for (int i = 0; i < (int)VOS3_MAX_FD; i++) {
        if (table->entries[i].file != NULL) {
            vos3_file_t* file = table->entries[i].file;
            table->entries[i].file = NULL;

            /* Atomically decrement ref_count */
            uint32_t old = __atomic_sub_fetch(&file->ref_count, 1U, __ATOMIC_ACQ_REL);
            if (old == 0U) {
                to_close[close_count++] = file;
            }
        }
    }

    vos3_spinlock_release(&table->spinlock);
    __asm__ volatile("push %0; popfq" :: "r"(irqflags) : "memory", "cc");

    /* Close files outside the lock -- close handlers may sleep */
    for (int i = 0; i < close_count; i++) {
        if (to_close[i]->ops != NULL && to_close[i]->ops->close != NULL) {
            (void)to_close[i]->ops->close(to_close[i]);
        }
    }
}

/* Requires an already owned reference or a locked descriptor-table entry. */
int vos3_file_retain(vos3_file_t* file)
{
    if (file == NULL) return -22;
    uint32_t refs = __atomic_load_n(&file->ref_count, __ATOMIC_ACQUIRE);
    for (;;) {
        if (refs == 0) return -5;
        if (refs == UINT32_MAX) return -75;
        if (__atomic_compare_exchange_n(&file->ref_count, &refs, refs + 1U,
                0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return 0;
    }
}

/* Independent descriptor ownership snapshot for fork/clone without CLONE_FILES. */
vos3_fd_table_t* vos3_fd_table_clone(vos3_fd_table_t* table)
{
    extern void* vos3_kzalloc(size_t size);
    extern void vos3_kfree(void* ptr);
    if (table == NULL) return NULL;
    vos3_fd_table_t* copy = vos3_kzalloc(sizeof(*copy));
    if (copy == NULL) return NULL;
    vos3_fd_table_init(copy);
    uint64_t irq;
    __asm__ volatile("pushfq; pop %0; cli" : "=r"(irq) :: "memory");
    vos3_spinlock_acquire(&table->spinlock);
    for (unsigned i = 0; i < VOS3_MAX_FD; ++i) {
        vos3_file_t* file = table->entries[i].file;
        if (file != NULL) {
            if (vos3_file_retain(file) != 0) goto fail;
        }
        copy->entries[i] = table->entries[i];
    }
    vos3_spinlock_release(&table->spinlock);
    __asm__ volatile("push %0; popfq" :: "r"(irq) : "memory", "cc");
    return copy;
fail:
    vos3_spinlock_release(&table->spinlock);
    __asm__ volatile("push %0; popfq" :: "r"(irq) : "memory", "cc");
    vos3_fd_table_destroy(copy);
    vos3_kfree(copy);
    return NULL;
}

int vos3_fd_alloc(vos3_fd_table_t* table, vos3_file_t* file)
{
    if (table == NULL || file == NULL) {
        return VOS3_FS_ERR_INVAL;
    }

    uint64_t flags;
    __asm__ volatile("pushfq; pop %0; cli" : "=r"(flags));
    vos3_spinlock_acquire(&table->spinlock);

    /* Find first free slot */
    int fd = VOS3_FD_INVALID;
    for (int i = 0; i < (int)VOS3_MAX_FD; i++) {
        if (table->entries[i].file == NULL) {
            fd = i;
            break;
        }
    }

    if (fd == VOS3_FD_INVALID) {
        vos3_spinlock_release(&table->spinlock);
        __asm__ volatile("push %0; popfq" :: "r"(flags) : "memory", "cc");
        return VOS3_FS_ERR_MFILE;
    }

    /* Atomically increment file reference count */
    __atomic_add_fetch(&file->ref_count, 1U, __ATOMIC_ACQ_REL);

    table->entries[fd].file = file;
    table->entries[fd].flags = 0U;

    vos3_spinlock_release(&table->spinlock);
    __asm__ volatile("push %0; popfq" :: "r"(flags) : "memory", "cc");

    return fd;
}

/**
 * @brief Get file from FD with atomic reference bump (fget pattern)
 *
 * Acquires the table spinlock, reads the file pointer, atomically increments
 * ref_count WHILE THE LOCK IS STILL HELD, then releases.  This guarantees
 * the caller holds a live reference — a concurrent close() on the same FD
 * will decrement ref_count but cannot free the file until the caller calls
 * vos3_fd_put().
 *
 * Memory ordering: __ATOMIC_ACQ_REL on the ref_count increment ensures:
 *   - ACQUIRE: all subsequent reads of file fields see committed state
 *   - RELEASE: the ref_count store is visible to other CPUs before we
 *              return the pointer (prevents reordering past unlock)
 *
 * Every successful call MUST be paired with exactly one vos3_fd_put().
 *
 * @param[in] table  FD table (per-task)
 * @param[in] fd     File descriptor index
 * @return File pointer with bumped ref_count, or NULL if fd invalid/closed.
 *
 * (Phase 5.6H hardening — K-C3 fix: eliminates Use-After-Free TOCTOU)
 */
vos3_file_t* vos3_fd_get(vos3_fd_table_t* table, int fd)
{
    if (table == NULL || fd < 0 || fd >= (int)VOS3_MAX_FD) {
        return NULL;
    }

    uint64_t flags;
    __asm__ volatile("pushfq; pop %0; cli" : "=r"(flags));
    vos3_spinlock_acquire(&table->spinlock);

    vos3_file_t* file = table->entries[fd].file;
    if (file != NULL) {
        /* Atomic ref_count bump UNDER the lock — prevents concurrent
         * close() from freeing the file between our read and return.
         * This is the "Reference Sealing" protocol. */
        if (vos3_file_retain(file) != 0) file = NULL;
    }

    vos3_spinlock_release(&table->spinlock);
    __asm__ volatile("push %0; popfq" :: "r"(flags) : "memory", "cc");

    return file;
}

/**
 * @brief Release a reference obtained from vos3_fd_get()
 *
 * Atomically decrements ref_count.  If the count reaches zero, the file's
 * close handler is invoked and the file structure may be freed.
 *
 * Memory ordering: __ATOMIC_ACQ_REL on the decrement ensures:
 *   - ACQUIRE: the close handler sees all prior writes to file state
 *   - RELEASE: our final field accesses complete before the decrement
 *              is visible to other CPUs
 *
 * @param[in] file  File pointer previously returned by vos3_fd_get()
 *
 * (Phase 5.6H hardening — K-C3 complementary release path)
 */
void vos3_fd_put(vos3_file_t* file)
{
    if (file == NULL) {
        return;
    }

    uint32_t remaining = __atomic_sub_fetch(&file->ref_count, 1U, __ATOMIC_ACQ_REL);
    if (remaining == 0U) {
        /* Last reference dropped — invoke close handler.
         * This runs outside any spinlock so close() may sleep. */
        if (file->ops != NULL && file->ops->close != NULL) {
            (void)file->ops->close(file);
        }
    }
}

int vos3_fd_free(vos3_fd_table_t* table, int fd)
{
    if (table == NULL || fd < 0 || fd >= (int)VOS3_MAX_FD) {
        return VOS3_FS_ERR_BADF;
    }

    uint64_t irqflags;
    __asm__ volatile("pushfq; pop %0; cli" : "=r"(irqflags));
    vos3_spinlock_acquire(&table->spinlock);

    vos3_file_t* file = table->entries[fd].file;
    if (file == NULL) {
        vos3_spinlock_release(&table->spinlock);
        __asm__ volatile("push %0; popfq" :: "r"(irqflags) : "memory", "cc");
        return VOS3_FS_ERR_BADF;
    }

    table->entries[fd].file = NULL;
    table->entries[fd].flags = 0U;

    /* Atomically decrement ref_count */
    uint32_t remaining = __atomic_sub_fetch(&file->ref_count, 1U, __ATOMIC_ACQ_REL);

    vos3_spinlock_release(&table->spinlock);
    __asm__ volatile("push %0; popfq" :: "r"(irqflags) : "memory", "cc");

    /* Close file if no more references (outside spinlock -- close may sleep) */
    if (remaining == 0U) {
        if (file->ops != NULL && file->ops->close != NULL) {
            return file->ops->close(file);
        }
    }

    return VOS3_FS_OK;
}

int vos3_dup(vos3_fd_table_t* table, int oldfd)
{
    if (table == NULL || oldfd < 0 || oldfd >= (int)VOS3_MAX_FD) {
        return VOS3_FS_ERR_BADF;
    }

    uint64_t irqflags;
    __asm__ volatile("pushfq; pop %0; cli" : "=r"(irqflags));
    vos3_spinlock_acquire(&table->spinlock);

    vos3_file_t* file = table->entries[oldfd].file;
    if (file == NULL) {
        vos3_spinlock_release(&table->spinlock);
        __asm__ volatile("push %0; popfq" :: "r"(irqflags) : "memory", "cc");
        return VOS3_FS_ERR_BADF;
    }

    /* Find first free slot */
    int newfd = VOS3_FD_INVALID;
    for (int i = 0; i < (int)VOS3_MAX_FD; i++) {
        if (table->entries[i].file == NULL) {
            newfd = i;
            break;
        }
    }

    if (newfd == VOS3_FD_INVALID) {
        vos3_spinlock_release(&table->spinlock);
        __asm__ volatile("push %0; popfq" :: "r"(irqflags) : "memory", "cc");
        return VOS3_FS_ERR_MFILE;
    }

    /* Atomically increment file reference count */
    __atomic_add_fetch(&file->ref_count, 1U, __ATOMIC_ACQ_REL);

    table->entries[newfd].file = file;
    table->entries[newfd].flags = 0U;

    vos3_spinlock_release(&table->spinlock);
    __asm__ volatile("push %0; popfq" :: "r"(irqflags) : "memory", "cc");

    return newfd;
}

int vos3_dup2(vos3_fd_table_t* table, int oldfd, int newfd)
{
    if (table == NULL || oldfd < 0 || oldfd >= (int)VOS3_MAX_FD ||
        newfd < 0 || newfd >= (int)VOS3_MAX_FD) {
        return VOS3_FS_ERR_BADF;
    }

    if (oldfd == newfd) {
        /* Check if oldfd is valid */
        vos3_file_t* file = vos3_fd_get(table, oldfd);
        if (file != NULL) {
            vos3_fd_put(file);
            return newfd;
        }
        return VOS3_FS_ERR_BADF;
    }

    uint64_t irqflags;
    __asm__ volatile("pushfq; pop %0; cli" : "=r"(irqflags));
    vos3_spinlock_acquire(&table->spinlock);

    vos3_file_t* oldfile = table->entries[oldfd].file;
    if (oldfile == NULL) {
        vos3_spinlock_release(&table->spinlock);
        __asm__ volatile("push %0; popfq" :: "r"(irqflags) : "memory", "cc");
        return VOS3_FS_ERR_BADF;
    }

    /* Close newfd if open -- snapshot for deferred close */
    vos3_file_t* closefile = NULL;
    vos3_file_t* newfile = table->entries[newfd].file;
    if (newfile != NULL) {
        table->entries[newfd].file = NULL;
        uint32_t remaining = __atomic_sub_fetch(&newfile->ref_count, 1U, __ATOMIC_ACQ_REL);
        if (remaining == 0U) {
            closefile = newfile;
        }
    }

    /* Duplicate */
    __atomic_add_fetch(&oldfile->ref_count, 1U, __ATOMIC_ACQ_REL);
    table->entries[newfd].file = oldfile;
    table->entries[newfd].flags = 0U;

    vos3_spinlock_release(&table->spinlock);
    __asm__ volatile("push %0; popfq" :: "r"(irqflags) : "memory", "cc");

    /* Close the evicted file outside the spinlock */
    if (closefile != NULL && closefile->ops != NULL &&
        closefile->ops->close != NULL) {
        (void)closefile->ops->close(closefile);
    }

    return newfd;
}
