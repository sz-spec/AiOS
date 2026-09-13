/**
 * @file ioctl.h
 * @brief VOS3 User-Space IOCTL Definitions
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 18 - Device Infrastructure
 */

#ifndef VOS3_USER_IOCTL_H
#define VOS3_USER_IOCTL_H

#include <stdint.h>

/* ============================================================================
 * IOCTL SYSTEM CALL
 * ============================================================================ */

/**
 * @brief Perform device control operation
 * @param fd File descriptor
 * @param cmd IOCTL command
 * @param arg Command argument (interpretation depends on cmd)
 * @return 0 on success, negative on error
 */
int ioctl(int fd, unsigned int cmd, void* arg);

/* ============================================================================
 * IOCTL MAGIC NUMBERS
 * ============================================================================ */

#define VOS3_IOC_MAGIC_AI       'A'   /**< AI Memory Guard devices (0x41) */

/* ============================================================================
 * IOCTL COMMAND ENCODING
 * ============================================================================ */

#define VOS3_IOC(magic, nr)     (((unsigned int)(magic) << 8) | (nr))

/* ============================================================================
 * AI TELEMETRY IOCTL COMMANDS (/dev/ai_telemetry)
 * ============================================================================ */

/** @brief Get AI guard statistics */
#define VOS3_IOCTL_AI_GET_STATS     VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x01)

/** @brief Reset AI guard statistics counters */
#define VOS3_IOCTL_AI_RESET_STATS   VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x02)

/** @brief Set anomaly detection threshold */
#define VOS3_IOCTL_AI_SET_THRESHOLD VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x03)

/** @brief Get current telemetry mode */
#define VOS3_IOCTL_AI_GET_MODE      VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x04)

/** @brief Set telemetry mode */
#define VOS3_IOCTL_AI_SET_MODE      VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x05)

/** @brief Get pending alert count */
#define VOS3_IOCTL_AI_ALERT_COUNT   VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x06)

/* ============================================================================
 * AI STATISTICS STRUCTURE
 * ============================================================================ */

/**
 * @brief AI Guard statistics (returned by GET_STATS ioctl)
 */
typedef struct vos3_ai_stats {
    uint32_t region_count;      /**< Number of active regions */
    uint32_t alert_count;       /**< Pending alerts in queue */
    uint32_t violation_count;   /**< Total violations detected */
    uint32_t overflow_count;    /**< Dropped alerts (queue overflow) */
    uint64_t total_bytes;       /**< Total bytes under protection */
    uint64_t read_count;        /**< Total read operations */
    uint64_t write_count;       /**< Total write operations */
    uint64_t fault_count;       /**< Total page faults */
    uint32_t mode;              /**< Current telemetry mode */
    uint32_t threshold;         /**< Anomaly detection threshold */
} vos3_ai_stats_t;

/* ============================================================================
 * SMP IOCTL COMMANDS (/dev/smp)
 * ============================================================================ */

#define VOS3_IOC_MAGIC_SMP      'S'   /**< SMP devices (0x53) */

/** @brief Get SMP/CPU statistics */
#define VOS3_IOCTL_SMP_GET_STATS    VOS3_IOC(VOS3_IOC_MAGIC_SMP, 0x01)

/** @brief Maximum CPUs supported */
#define VOS3_SMP_MAX_CPUS       256

/* ============================================================================
 * SMP STATISTICS STRUCTURE
 * ============================================================================ */

/**
 * @brief Per-CPU statistics
 */
typedef struct vos3_cpu_stat {
    uint32_t cpu_id;            /**< Logical CPU ID */
    uint32_t apic_id;           /**< Hardware APIC ID */
    uint32_t online;            /**< 1 if online, 0 if offline */
    uint32_t usage_percent;     /**< CPU usage (0-100) */
    uint64_t idle_ticks;        /**< Total idle ticks */
    uint64_t busy_ticks;        /**< Total busy ticks */
    uint32_t task_count;        /**< Tasks on this CPU's runqueue */
    uint32_t reserved;          /**< Padding */
} vos3_cpu_stat_t;

/**
 * @brief SMP statistics (returned by GET_STATS ioctl)
 */
typedef struct vos3_smp_stats {
    uint32_t cpu_count;         /**< Total number of CPUs */
    uint32_t online_count;      /**< Number of online CPUs */
    uint32_t bsp_id;            /**< Bootstrap processor ID */
    uint32_t reserved;          /**< Padding */
    vos3_cpu_stat_t cpus[VOS3_SMP_MAX_CPUS];  /**< Per-CPU stats */
} vos3_smp_stats_t;

#endif /* VOS3_USER_IOCTL_H */
