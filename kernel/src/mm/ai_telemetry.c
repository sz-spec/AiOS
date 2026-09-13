/**
 * @file ai_telemetry.c
 * @brief VOS3 AI Memory Telemetry Export Device
 *
 * @details Character device driver for exporting AI memory guard telemetry.
 *          Provides /dev/ai_telemetry for external monitoring tools.
 *
 * @version 2.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 * @note Phase 17.5.5 - Telemetry Export
 * @note Phase 18 - Device Infrastructure & IOCTL
 */

#include "../../include/vos/ai_guard.h"
#include "../../include/vos/device.h"
#include "../../include/vos/ioctl.h"
#include "../../include/vos/console.h"
#include "../../include/vos/timer.h"
#include "../../include/vos/atomic.h"
#include "../../include/vos/string.h"
#include "../../include/vos/uaccess.h"

/* ============================================================================
 * TELEMETRY CONFIGURATION
 * ============================================================================ */

/** @brief Maximum alert queue size */
#define VOS3_TELEMETRY_ALERT_QUEUE_SIZE     64U

/** @brief Device major/minor numbers */
#define VOS3_AI_TELEMETRY_MAJOR     10U
#define VOS3_AI_TELEMETRY_MINOR     200U

/** @brief Default anomaly threshold */
#define VOS3_AI_DEFAULT_THRESHOLD   10000U

/* ============================================================================
 * INTERNAL DATA
 * ============================================================================ */

/** @brief Telemetry initialized flag */
static volatile int g_telemetry_initialized = 0;

/** @brief Current telemetry mode */
static vos3_ai_telemetry_mode_t g_telemetry_mode = VOS3_AI_TELEMETRY_POLL;

/** @brief Anomaly detection threshold */
static uint32_t g_anomaly_threshold = VOS3_AI_DEFAULT_THRESHOLD;

/** @brief Total violation count */
static volatile uint32_t g_violation_count = 0;

/** @brief Alert queue (ring buffer) */
static struct {
    vos3_ai_alert_t alerts[VOS3_TELEMETRY_ALERT_QUEUE_SIZE];
    volatile uint32_t head;
    volatile uint32_t tail;
    volatile uint32_t count;
    uint32_t overflow;
} g_alert_queue = {0};

/** @brief Spinlock for queue access */
static vos3_spinlock_t g_telemetry_lock = VOS3_SPINLOCK_INIT;

/** @brief Device structure */
static vos3_device_t* g_ai_telemetry_dev = NULL;

/** @brief External: Get global context list for snapshot */
extern vos3_ai_guard_ctx_t* vos3_ai_guard_get_global_ctx(void);

/* Forward declarations */
int64_t vos3_ai_telemetry_read(void* buffer, size_t size);
int64_t vos3_ai_telemetry_snapshot(void* buffer, size_t buffer_size);

/* ============================================================================
 * LOCK HELPERS
 * ============================================================================ */

static inline void telemetry_lock(void)
{
    vos3_spinlock_acquire(&g_telemetry_lock);
}

static inline void telemetry_unlock(void)
{
    vos3_spinlock_release(&g_telemetry_lock);
}

/* ============================================================================
 * ALERT QUEUE OPERATIONS
 * ============================================================================ */

/**
 * @brief Push alert to queue
 */
static void alert_queue_push(const vos3_ai_alert_t* alert)
{
    telemetry_lock();

    uint32_t pos = g_alert_queue.head;
    g_alert_queue.alerts[pos] = *alert;
    g_alert_queue.head = (pos + 1U) % VOS3_TELEMETRY_ALERT_QUEUE_SIZE;

    if (g_alert_queue.count < VOS3_TELEMETRY_ALERT_QUEUE_SIZE) {
        g_alert_queue.count++;
    } else {
        /* Queue full - overwrite oldest */
        g_alert_queue.tail = (g_alert_queue.tail + 1U) %
                              VOS3_TELEMETRY_ALERT_QUEUE_SIZE;
        g_alert_queue.overflow++;
    }

    /* Increment violation count */
    g_violation_count++;

    telemetry_unlock();
}

/**
 * @brief Pop alert from queue
 * @return 1 if alert returned, 0 if queue empty
 */
static int alert_queue_pop(vos3_ai_alert_t* alert)
{
    telemetry_lock();

    if (g_alert_queue.count == 0U) {
        telemetry_unlock();
        return 0;
    }

    uint32_t pos = g_alert_queue.tail;
    *alert = g_alert_queue.alerts[pos];
    g_alert_queue.tail = (pos + 1U) % VOS3_TELEMETRY_ALERT_QUEUE_SIZE;
    g_alert_queue.count--;

    telemetry_unlock();
    return 1;
}

/* ============================================================================
 * CHARACTER DEVICE OPERATIONS
 * ============================================================================ */

/**
 * @brief Open telemetry device
 */
static int ai_telemetry_open(vos3_device_t* dev, vos3_file_t* file)
{
    (void)dev;
    (void)file;
    return 0;  /* Always allow open */
}

/**
 * @brief Close telemetry device
 */
static int ai_telemetry_close(vos3_device_t* dev, vos3_file_t* file)
{
    (void)dev;
    (void)file;
    return 0;
}

/**
 * @brief Read from telemetry device
 *
 * In POLL mode: Returns current snapshot (header + regions)
 * In EVENT mode: Returns next alert from queue
 */
static int64_t ai_telemetry_dev_read(vos3_device_t* dev, vos3_file_t* file,
                                      void* buf, size_t count)
{
    (void)dev;
    (void)file;

    if (buf == NULL || count == 0) {
        return VOS3_DEV_ERR_INVAL;
    }

    return vos3_ai_telemetry_read(buf, count);
}

/**
 * @brief Write to telemetry device (not supported)
 */
static int64_t ai_telemetry_dev_write(vos3_device_t* dev, vos3_file_t* file,
                                       const void* buf, size_t count)
{
    (void)dev;
    (void)file;
    (void)buf;
    (void)count;
    return VOS3_DEV_ERR_INVAL;  /* Read-only device */
}

/**
 * @brief IOCTL handler for telemetry device
 */
static int ai_telemetry_ioctl(vos3_device_t* dev, vos3_file_t* file,
                               uint32_t cmd, void* arg)
{
    (void)dev;
    (void)file;

    /* Verify this is an AI device command */
    if (VOS3_IOC_MAGIC(cmd) != VOS3_IOC_MAGIC_AI) {
        return VOS3_DEV_ERR_INVAL;
    }

    switch (cmd) {
        case VOS3_IOCTL_AI_GET_STATS: {
            if (arg == NULL) {
                return VOS3_DEV_ERR_INVAL;
            }

            /* Gather statistics */
            vos3_ai_ioctl_stats_t stats;
            memset(&stats, 0, sizeof(stats));

            /* Get global context for region stats */
            vos3_ai_guard_ctx_t* ctx = vos3_ai_guard_get_global_ctx();
            vos3_ai_guard_region_t* region = (ctx != NULL) ? ctx->regions : NULL;

            while (region != NULL) {
                stats.region_count++;
                stats.total_bytes += region->size;
                stats.read_count += region->stats.read_count;
                stats.write_count += region->stats.write_count;
                stats.fault_count += region->stats.fault_count;
                region = region->next;
            }

            stats.alert_count = g_alert_queue.count;
            stats.violation_count = g_violation_count;
            stats.overflow_count = g_alert_queue.overflow;
            stats.mode = (uint32_t)g_telemetry_mode;
            stats.threshold = g_anomaly_threshold;

            /* Copy to user buffer with validation */
            if (copy_to_user(arg, &stats, sizeof(stats)) != 0) {
                return -EFAULT;
            }

            return 0;
        }

        case VOS3_IOCTL_AI_RESET_STATS: {
            /* Reset counters */
            telemetry_lock();
            g_alert_queue.head = 0U;
            g_alert_queue.tail = 0U;
            g_alert_queue.count = 0U;
            g_alert_queue.overflow = 0U;
            g_violation_count = 0;
            telemetry_unlock();

            vos3_console_printf("[AI-TELEMETRY] Statistics reset\n");
            return 0;
        }

        case VOS3_IOCTL_AI_SET_THRESHOLD: {
            if (arg == NULL) {
                return VOS3_DEV_ERR_INVAL;
            }

            uint32_t new_threshold;
            if (copy_from_user(&new_threshold, arg, sizeof(new_threshold)) != 0) {
                return -EFAULT;
            }
            g_anomaly_threshold = new_threshold;

            vos3_console_printf("[AI-TELEMETRY] Threshold set to %u\n", new_threshold);
            return 0;
        }

        case VOS3_IOCTL_AI_GET_MODE: {
            if (arg == NULL) {
                return VOS3_DEV_ERR_INVAL;
            }

            uint32_t mode = (uint32_t)g_telemetry_mode;
            if (copy_to_user(arg, &mode, sizeof(mode)) != 0) {
                return -EFAULT;
            }
            return 0;
        }

        case VOS3_IOCTL_AI_SET_MODE: {
            uint32_t mode = (uint32_t)(uintptr_t)arg;
            if (mode > VOS3_AI_TELEMETRY_EVENT) {
                return VOS3_DEV_ERR_INVAL;
            }

            g_telemetry_mode = (vos3_ai_telemetry_mode_t)mode;
            vos3_console_printf("[AI-TELEMETRY] Mode set to %s\n",
                               (mode == 0) ? "POLL" : "EVENT");
            return 0;
        }

        case VOS3_IOCTL_AI_ALERT_COUNT: {
            if (arg == NULL) {
                return VOS3_DEV_ERR_INVAL;
            }

            uint32_t count = g_alert_queue.count;
            if (copy_to_user(arg, &count, sizeof(count)) != 0) {
                return -EFAULT;
            }
            return 0;
        }

        default:
            return VOS3_DEV_ERR_INVAL;
    }
}

/** @brief Character device operations */
static const vos3_char_ops_t g_ai_telemetry_ops = {
    .open   = ai_telemetry_open,
    .close  = ai_telemetry_close,
    .read   = ai_telemetry_dev_read,
    .write  = ai_telemetry_dev_write,
    .ioctl  = ai_telemetry_ioctl,
    .poll   = NULL,
};

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

int vos3_ai_telemetry_init(void)
{
    if (g_telemetry_initialized) {
        return 0;
    }

    /* Initialize alert queue */
    g_alert_queue.head = 0U;
    g_alert_queue.tail = 0U;
    g_alert_queue.count = 0U;
    g_alert_queue.overflow = 0U;
    g_violation_count = 0;
    g_anomaly_threshold = VOS3_AI_DEFAULT_THRESHOLD;

    g_telemetry_mode = VOS3_AI_TELEMETRY_POLL;

    /* Register character device */
    vos3_dev_t devno = VOS3_MKDEV(VOS3_AI_TELEMETRY_MAJOR, VOS3_AI_TELEMETRY_MINOR);
    g_ai_telemetry_dev = vos3_cdev_register("ai_telemetry", devno,
                                             &g_ai_telemetry_ops, NULL);

    if (g_ai_telemetry_dev == NULL) {
        vos3_console_printf("[AI-TELEMETRY] Failed to register device\n");
        return -1;
    }

    /* Create /dev/ai_telemetry node */
    int result = vos3_devfs_create("ai_telemetry", g_ai_telemetry_dev);
    if (result != 0) {
        vos3_console_printf("[AI-TELEMETRY] Failed to create /dev node (error %d)\n", result);
        /* Continue anyway - device is registered */
    }

    g_telemetry_initialized = 1;

    vos3_console_printf("[AI-TELEMETRY] Device initialized at /dev/ai_telemetry\n");
    vos3_console_printf("[AI-TELEMETRY] IOCTL commands: GET_STATS=0x%04X RESET=0x%04X\n",
                        VOS3_IOCTL_AI_GET_STATS, VOS3_IOCTL_AI_RESET_STATS);

    return 0;
}

int64_t vos3_ai_telemetry_snapshot(void* buffer, size_t buffer_size)
{
    if (buffer == NULL || buffer_size == 0) {
        return -1;
    }

    /* Allow snapshot even before full device init for self-tests */
    /* The magic header will still be valid */

    uint8_t* out = (uint8_t*)buffer;
    size_t offset = 0;
    size_t remaining = buffer_size;

    /* Get global context */
    vos3_ai_guard_ctx_t* ctx = vos3_ai_guard_get_global_ctx();

    /* Count regions */
    uint32_t region_count = 0;
    uint64_t total_bytes = 0;
    vos3_ai_guard_region_t* region = (ctx != NULL) ? ctx->regions : NULL;
    while (region != NULL) {
        region_count++;
        total_bytes += region->size;
        region = region->next;
    }

    /* Write header */
    if (remaining < sizeof(vos3_ai_telemetry_header_t)) {
        return -3;  /* Buffer too small */
    }

    vos3_ai_telemetry_header_t* header = (vos3_ai_telemetry_header_t*)out;
    header->magic = VOS3_AI_TELEMETRY_MAGIC;
    header->version = VOS3_AI_TELEMETRY_VERSION;
    header->timestamp = vos3_timer_get_ticks();
    header->region_count = region_count;
    header->alert_count = g_alert_queue.count;
    header->total_bytes = total_bytes;

    offset += sizeof(vos3_ai_telemetry_header_t);
    remaining -= sizeof(vos3_ai_telemetry_header_t);

    /* Write region entries */
    region = (ctx != NULL) ? ctx->regions : NULL;
    while (region != NULL && remaining >= sizeof(vos3_ai_telemetry_region_t)) {
        vos3_ai_telemetry_region_t* entry =
            (vos3_ai_telemetry_region_t*)(out + offset);

        entry->region_id = region->id;
        entry->pid = 0;  /* TODO: Track owning PID */
        entry->type = (uint8_t)region->type;
        entry->state = (uint8_t)region->state;
        entry->numa_node = region->numa_node;
        entry->flags = (uint8_t)region->flags;
        entry->base = region->base;
        entry->size = region->size;
        entry->read_count = region->stats.read_count;
        entry->write_count = region->stats.write_count;
        entry->fault_count = region->stats.fault_count;
        entry->last_access_time = region->stats.last_access_time;
        entry->ref_count = (region->shared != NULL) ?
                            region->shared->ref_count : 1U;
        entry->_padding = 0;

        offset += sizeof(vos3_ai_telemetry_region_t);
        remaining -= sizeof(vos3_ai_telemetry_region_t);
        region = region->next;
    }

    return (int64_t)offset;
}

int vos3_ai_telemetry_set_mode(vos3_ai_telemetry_mode_t mode)
{
    if (mode > VOS3_AI_TELEMETRY_EVENT) {
        return -1;
    }

    g_telemetry_mode = mode;

    vos3_console_printf("[AI-TELEMETRY] Mode set to %s\n",
                        (mode == VOS3_AI_TELEMETRY_POLL) ? "POLL" : "EVENT");

    return 0;
}

void vos3_ai_telemetry_queue_alert(const vos3_ai_alert_t* alert)
{
    if (alert == NULL || !g_telemetry_initialized) {
        return;
    }

    alert_queue_push(alert);

    /* In EVENT mode, this would wake any blocked readers */
    if (g_telemetry_mode == VOS3_AI_TELEMETRY_EVENT) {
        /* TODO: Wake blocked tasks */
    }
}

/* ============================================================================
 * DEVICE READ/WRITE (for VFS integration - legacy API)
 * ============================================================================ */

/**
 * @brief Read from telemetry device
 *
 * In POLL mode: Returns current snapshot
 * In EVENT mode: Blocks until alert available, returns alert
 *
 * @param[out] buffer Output buffer
 * @param[in] size Buffer size
 * @return Bytes read, or negative error
 */
int64_t vos3_ai_telemetry_read(void* buffer, size_t size)
{
    if (g_telemetry_mode == VOS3_AI_TELEMETRY_POLL) {
        return vos3_ai_telemetry_snapshot(buffer, size);
    }

    /* EVENT mode: return next alert */
    if (size < sizeof(vos3_ai_alert_t)) {
        return -1;
    }

    vos3_ai_alert_t alert;
    if (alert_queue_pop(&alert)) {
        *(vos3_ai_alert_t*)buffer = alert;
        return sizeof(vos3_ai_alert_t);
    }

    return 0;  /* No alerts available */
}

/**
 * @brief Get pending alert count
 */
uint32_t vos3_ai_telemetry_pending_alerts(void)
{
    return g_alert_queue.count;
}
