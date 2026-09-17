/**
 * @file tty.c
 * @brief VOS3 TTY/Console Device Driver
 *
 * @details Character device interface to the console.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/device.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/sync.h"
#include "../../include/vos/ipc.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"

/* ============================================================================
 * DEVICE MINORS
 * ============================================================================ */

#define TTY_CONSOLE_MINOR   1U

/* ============================================================================
 * INPUT BUFFER
 * ============================================================================ */

#define TTY_INPUT_BUFSIZE   256U

/** @brief Input buffer */
static struct {
    char        buffer[TTY_INPUT_BUFSIZE];
    size_t      head;
    size_t      tail;
    size_t      count;
    vos3_spinlock_t lock;
    vos3_semaphore_t sem;
} g_tty_input;

/** @brief TTY initialized flag */
static int g_tty_initialized = 0;

/** @brief Foreground process group (for signal delivery) */
static uint32_t g_tty_foreground_pgid = 1U;  /* Default to init */

/* ============================================================================
 * SIGNAL CHARACTERS
 * ============================================================================ */

#define TTY_CTRL_C      '\x03'  /**< Ctrl+C - SIGINT */
#define TTY_CTRL_Z      '\x1A'  /**< Ctrl+Z - SIGTSTP */
#define TTY_CTRL_BSLASH '\x1C'  /**< Ctrl+\ - SIGQUIT */

/**
 * @brief Send signal to foreground process group
 */
static void tty_send_signal(int signum)
{
    uint32_t pgid = g_tty_foreground_pgid;

    VOS3_DEBUG("TTY: Sending signal %d to process group %u", signum, pgid);

    /* Send to all tasks in the foreground process group */
    for (vos3_tid_t i = 0U; i < VOS3_MAX_TASKS; i++) {
        vos3_task_t* task = vos3_task_get(i);
        if (task != NULL && task->pgid == pgid) {
            vos3_signal_send(i, signum);
        }
    }
}

/* ============================================================================
 * INPUT HANDLING
 * ============================================================================ */

/**
 * @brief Add character to input buffer (called from keyboard interrupt)
 */
void vos3_tty_input_char(char c)
{
    if (g_tty_initialized == 0) {
        return;
    }

    /* Handle special control characters */
    switch (c) {
        case TTY_CTRL_C:
            /* Ctrl+C - Send SIGINT to foreground process group */
            vos3_console_puts("^C\n");
            tty_send_signal(VOS3_SIGINT);
            return;

        case TTY_CTRL_Z:
            /* Ctrl+Z - Send SIGTSTP to foreground process group */
            vos3_console_puts("^Z\n");
            tty_send_signal(VOS3_SIGTSTP);
            return;

        case TTY_CTRL_BSLASH:
            /* Ctrl+\ - Send SIGQUIT to foreground process group */
            vos3_console_puts("^\\\n");
            tty_send_signal(VOS3_SIGQUIT);
            return;

        default:
            break;
    }

    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_tty_input.lock);

    int inserted = 0;
    if (g_tty_input.count < TTY_INPUT_BUFSIZE) {
        g_tty_input.buffer[g_tty_input.head] = c;
        g_tty_input.head = (g_tty_input.head + 1U) % TTY_INPUT_BUFSIZE;
        g_tty_input.count++;
        inserted = 1;
    }

    vos3_spinlock_unlock(&g_tty_input.lock);
    vos3_irq_restore(flags);

    /* Wake outside the buffer lock.  sem_post itself is IRQ-safe and never
     * blocks, so this path is valid from the keyboard ISR. */
    if (inserted) vos3_sem_post(&g_tty_input.sem);

    /* Echo character to console */
    if (c == '\n') {
        vos3_console_putc('\n');
    } else if (c == '\b') {
        vos3_console_puts("\b \b");
    } else if (c >= 32 && c < 127) {
        vos3_console_putc(c);
    }
}

/**
 * @brief Set foreground process group
 * @param[in] pgid Process group ID
 */
void vos3_tty_set_foreground(uint32_t pgid)
{
    g_tty_foreground_pgid = pgid;
    VOS3_DEBUG("TTY: Foreground PGID set to %u", pgid);
}

/**
 * @brief Add string to input buffer
 */
void vos3_tty_input_string(const char* str)
{
    while (*str != '\0') {
        vos3_tty_input_char(*str++);
    }
}

/* ============================================================================
 * CONSOLE DEVICE OPERATIONS
 * ============================================================================ */

static int console_open(vos3_device_t* dev, vos3_file_t* file)
{
    (void)dev;
    (void)file;
    return VOS3_DEV_OK;
}

static int console_close(vos3_device_t* dev, vos3_file_t* file)
{
    (void)dev;
    (void)file;
    return VOS3_DEV_OK;
}

static int64_t console_read(vos3_device_t* dev, vos3_file_t* file,
                             void* buf, size_t count)
{
    (void)dev;
    (void)file;

    if (buf == NULL || count == 0U) {
        return VOS3_DEV_ERR_INVAL;
    }

    char* p = (char*)buf;
    size_t read_count = 0U;

    while (read_count < count) {
        /* Wait for input */
        if (vos3_sem_wait_status(&g_tty_input.sem) != VOS3_SYNC_OK) break;

        vos3_irqflags_t flags = vos3_irq_save();
        vos3_spinlock_lock(&g_tty_input.lock);

        if (g_tty_input.count > 0U) {
            char c = g_tty_input.buffer[g_tty_input.tail];
            g_tty_input.tail = (g_tty_input.tail + 1U) % TTY_INPUT_BUFSIZE;
            g_tty_input.count--;

            vos3_spinlock_unlock(&g_tty_input.lock);
            vos3_irq_restore(flags);

            *p++ = c;
            read_count++;

            /* Return on newline for line-buffered mode */
            if (c == '\n') {
                break;
            }
        } else {
            vos3_spinlock_unlock(&g_tty_input.lock);
            vos3_irq_restore(flags);
        }
    }

    return (int64_t)read_count;
}

static int64_t console_write(vos3_device_t* dev, vos3_file_t* file,
                              const void* buf, size_t count)
{
    (void)dev;
    (void)file;

    if (buf == NULL) {
        return VOS3_DEV_ERR_INVAL;
    }

#ifdef NATIVE_SMP_WORKLOAD
    /* sys_write has already copied this bounded record into kernel memory. */
    uint64_t record_flags = vos3_console_record_begin();
#endif
    const char* p = (const char*)buf;
    for (size_t i = 0U; i < count; i++) {
        vos3_console_putc(p[i]);
    }
#ifdef NATIVE_SMP_WORKLOAD
    vos3_console_record_end(record_flags);
#endif

    return (int64_t)count;
}

static int console_ioctl(vos3_device_t* dev, vos3_file_t* file,
                          uint32_t cmd, void* arg)
{
    (void)dev;
    (void)file;

    switch (cmd) {
        case VOS3_IOCTL_TIOCGWINSZ:
            /* TODO: Return window size */
            return VOS3_DEV_ERR_INVAL;

        case VOS3_IOCTL_TIOCGPGRP:
            /* Get foreground process group */
            if (arg != NULL) {
                *(uint32_t*)arg = g_tty_foreground_pgid;
                return VOS3_DEV_OK;
            }
            return VOS3_DEV_ERR_INVAL;

        case VOS3_IOCTL_TIOCSPGRP:
            /* Set foreground process group */
            if (arg != NULL) {
                g_tty_foreground_pgid = *(uint32_t*)arg;
                VOS3_DEBUG("TTY: Foreground PGID set to %u via ioctl", g_tty_foreground_pgid);
                return VOS3_DEV_OK;
            }
            return VOS3_DEV_ERR_INVAL;

        case VOS3_IOCTL_TIOCSCTTY:
            /* Set controlling terminal - just succeed for now */
            return VOS3_DEV_OK;

        default:
            return VOS3_DEV_ERR_INVAL;
    }
}

static const vos3_char_ops_t g_console_ops = {
    .open  = console_open,
    .close = console_close,
    .read  = console_read,
    .write = console_write,
    .ioctl = console_ioctl,
    .poll  = NULL,
};

/* ============================================================================
 * REGISTRATION
 * ============================================================================ */

int vos3_tty_init(void)
{
    VOS3_INFO("Registering TTY devices");

    /* Initialize input buffer */
    g_tty_input.head = 0U;
    g_tty_input.tail = 0U;
    g_tty_input.count = 0U;
    vos3_spinlock_init(&g_tty_input.lock);
    vos3_sem_init(&g_tty_input.sem, "tty_sem", 0U);

    g_tty_initialized = 1;

    /* Register /dev/console */
    vos3_device_t* dev = vos3_cdev_register("console",
                                             VOS3_MKDEV(VOS3_CONSOLE_MAJOR, TTY_CONSOLE_MINOR),
                                             &g_console_ops, NULL);
    if (dev != NULL) {
        (void)vos3_devfs_create("console", dev);
    }

    /* Create /dev/tty symlink (for now, same as console) */
    dev = vos3_cdev_register("tty",
                              VOS3_MKDEV(VOS3_TTY_MAJOR, 0U),
                              &g_console_ops, NULL);
    if (dev != NULL) {
        (void)vos3_devfs_create("tty", dev);
    }

    VOS3_INFO("TTY devices registered: console, tty");

    return VOS3_DEV_OK;
}
