/**
 * @file smp_telemetry.c
 * @brief VOS3 SMP Telemetry Export Device
 *
 * @details Character device driver for exporting SMP/CPU statistics.
 *          Provides /dev/smp for monitoring tools like ai_top.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 * @note Phase 25 - Multicore Stress Testing
 */

#include "../../../include/arch/x86_64/smp.h"
#include "../../../include/vos/device.h"
#include "../../../include/vos/ioctl.h"
#include "../../../include/vos/console.h"
#include "../../../include/vos/timer.h"
#include "../../../include/vos/string.h"
#include "../../../include/vos/uaccess.h"
#include "../../../include/vos/scheduler.h"

/* ============================================================================
 * DEVICE CONFIGURATION
 * ============================================================================ */

/** @brief Device major/minor numbers */
#define VOS3_SMP_TELEMETRY_MAJOR     10U
#define VOS3_SMP_TELEMETRY_MINOR     201U

/* ============================================================================
 * INTERNAL STATE
 * ============================================================================ */

/** @brief Device structure */
static vos3_device_t* g_smp_telemetry_dev = NULL;

/** @brief Initialized flag */
static int g_smp_telemetry_initialized = 0;

/** @brief Per-CPU tick counters (updated by scheduler) */
static volatile uint64_t g_cpu_idle_ticks[VOS3_SMP_MAX_CPUS] = {0};
static volatile uint64_t g_cpu_busy_ticks[VOS3_SMP_MAX_CPUS] = {0};

/* ============================================================================
 * CHARACTER DEVICE OPERATIONS
 * ============================================================================ */

/**
 * @brief Open SMP telemetry device
 */
static int smp_telemetry_open(vos3_device_t* dev, vos3_file_t* file)
{
    (void)dev;
    (void)file;
    return 0;
}

/**
 * @brief Close SMP telemetry device
 */
static int smp_telemetry_close(vos3_device_t* dev, vos3_file_t* file)
{
    (void)dev;
    (void)file;
    return 0;
}

/**
 * @brief Read from SMP telemetry device (returns basic text info)
 */
static int64_t smp_telemetry_read(vos3_device_t* dev, vos3_file_t* file,
                                   void* buf, size_t count)
{
    (void)dev;
    (void)file;

    if (buf == NULL || count == 0) {
        return VOS3_DEV_ERR_INVAL;
    }

    /* Return simple text summary */
    char info[64];
    char* p = info;
    uint32_t online = vos3_smp_online_count();
    uint32_t total = vos3_smp_cpu_count();

    /* Build string manually (kernel doesn't have snprintf) */
    memcpy(p, "SMP: ", 5);
    p += 5;
    *p++ = '0' + (online % 10);
    *p++ = '/';
    *p++ = '0' + (total % 10);
    memcpy(p, " CPUs online\n", 13);
    p += 13;
    *p = '\0';

    size_t len = (size_t)(p - info);
    size_t to_copy = len < count ? len : count;
    if (copy_to_user(buf, info, to_copy) != 0) {
        return -EFAULT;
    }

    return (int64_t)to_copy;
}

/**
 * @brief Write to SMP telemetry device (not supported)
 */
static int64_t smp_telemetry_write(vos3_device_t* dev, vos3_file_t* file,
                                    const void* buf, size_t count)
{
    (void)dev;
    (void)file;
    (void)buf;
    (void)count;
    return VOS3_DEV_ERR_INVAL;
}

/**
 * @brief IOCTL handler for SMP telemetry device
 */
static int smp_telemetry_ioctl(vos3_device_t* dev, vos3_file_t* file,
                                uint32_t cmd, void* arg)
{
    (void)dev;
    (void)file;

    /* Verify this is an SMP device command */
    if (VOS3_IOC_MAGIC(cmd) != VOS3_IOC_MAGIC_SMP) {
        return VOS3_DEV_ERR_INVAL;
    }

    switch (cmd) {
        case VOS3_IOCTL_SMP_GET_STATS: {
            if (arg == NULL) {
                return VOS3_DEV_ERR_INVAL;
            }

            /* Build statistics structure */
            vos3_smp_ioctl_stats_t stats;
            memset(&stats, 0, sizeof(stats));

            stats.cpu_count = vos3_smp_cpu_count();
            stats.online_count = vos3_smp_online_count();
            stats.bsp_id = 0;  /* BSP is always CPU 0 */

            /* Fill per-CPU stats */
            for (uint32_t i = 0; i < stats.cpu_count && i < VOS3_SMP_MAX_CPUS; i++) {
                const vos3_smp_cpu_info_t* cpu_info = vos3_smp_get_cpu_info(i);

                stats.cpus[i].cpu_id = i;
                stats.cpus[i].apic_id = (cpu_info != NULL) ? cpu_info->apic_id : 0;
                stats.cpus[i].online = (cpu_info != NULL &&
                                        (cpu_info->flags & VOS3_SMP_CPU_ONLINE)) ? 1 : 0;

                /* Get task count from scheduler runqueue */
                stats.cpus[i].task_count = vos3_sched_get_cpu_task_count(i);

                /* Calculate usage from tick counters */
                uint64_t idle = g_cpu_idle_ticks[i];
                uint64_t busy = g_cpu_busy_ticks[i];
                uint64_t total = idle + busy;

                if (total > 0) {
                    stats.cpus[i].usage_percent = (uint32_t)((busy * 100ULL) / total);
                } else {
                    /* Estimate from task count if no tick data */
                    stats.cpus[i].usage_percent = (stats.cpus[i].task_count > 0) ?
                        (stats.cpus[i].task_count * 25) : 0;
                    if (stats.cpus[i].usage_percent > 100) {
                        stats.cpus[i].usage_percent = 100;
                    }
                }

                stats.cpus[i].idle_ticks = idle;
                stats.cpus[i].busy_ticks = busy;
            }

            /* Copy to user buffer */
            if (copy_to_user(arg, &stats, sizeof(stats)) != 0) {
                return -EFAULT;
            }

            return 0;
        }

        default:
            return VOS3_DEV_ERR_INVAL;
    }
}

/** @brief Character device operations */
static const vos3_char_ops_t g_smp_telemetry_ops = {
    .open   = smp_telemetry_open,
    .close  = smp_telemetry_close,
    .read   = smp_telemetry_read,
    .write  = smp_telemetry_write,
    .ioctl  = smp_telemetry_ioctl,
    .poll   = NULL,
};

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Update CPU tick counters (called from scheduler)
 */
void vos3_smp_telemetry_tick(uint32_t cpu_id, int is_idle)
{
    if (cpu_id >= VOS3_SMP_MAX_CPUS) {
        return;
    }

    if (is_idle) {
        g_cpu_idle_ticks[cpu_id]++;
    } else {
        g_cpu_busy_ticks[cpu_id]++;
    }
}

/**
 * @brief Initialize SMP telemetry device
 */
int vos3_smp_telemetry_init(void)
{
    if (g_smp_telemetry_initialized) {
        return 0;
    }

    /* Clear tick counters */
    memset((void*)g_cpu_idle_ticks, 0, sizeof(g_cpu_idle_ticks));
    memset((void*)g_cpu_busy_ticks, 0, sizeof(g_cpu_busy_ticks));

    /* Register character device */
    vos3_dev_t devno = VOS3_MKDEV(VOS3_SMP_TELEMETRY_MAJOR, VOS3_SMP_TELEMETRY_MINOR);
    g_smp_telemetry_dev = vos3_cdev_register("smp", devno,
                                              &g_smp_telemetry_ops, NULL);

    if (g_smp_telemetry_dev == NULL) {
        vos3_console_printf("[SMP-TELEMETRY] Failed to register device\n");
        return -1;
    }

    /* Create /dev/smp node */
    int result = vos3_devfs_create("smp", g_smp_telemetry_dev);
    if (result != 0) {
        vos3_console_printf("[SMP-TELEMETRY] Failed to create /dev node (error %d)\n", result);
    }

    g_smp_telemetry_initialized = 1;

    vos3_console_printf("[SMP-TELEMETRY] Device initialized at /dev/smp\n");

    return 0;
}
