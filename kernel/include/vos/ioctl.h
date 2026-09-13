/**
 * @file ioctl.h
 * @brief VOS3 IOCTL Command Definitions
 *
 * @details Defines ioctl command codes for device control operations.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 18 - Device Infrastructure
 */

#ifndef VOS3_IOCTL_H
#define VOS3_IOCTL_H

#include <stdint.h>

/* ============================================================================
 * IOCTL MAGIC NUMBERS
 * ============================================================================ */

#define VOS3_IOC_MAGIC_AI       'A'   /**< AI Memory Guard devices (0x41) */
#define VOS3_IOC_MAGIC_TTY      'T'   /**< TTY/terminal devices */
#define VOS3_IOC_MAGIC_BLK      'B'   /**< Block devices */

/* ============================================================================
 * IOCTL COMMAND ENCODING
 * ============================================================================ */

/**
 * @brief Build ioctl command from magic and number
 * @param magic Device type magic character
 * @param nr Command number within device type
 */
#define VOS3_IOC(magic, nr)     (((uint32_t)(magic) << 8) | (nr))

/** @brief Extract magic from command */
#define VOS3_IOC_MAGIC(cmd)     (((cmd) >> 8) & 0xFF)

/** @brief Extract number from command */
#define VOS3_IOC_NR(cmd)        ((cmd) & 0xFF)

/* ============================================================================
 * AI TELEMETRY IOCTL COMMANDS (/dev/ai_telemetry)
 * ============================================================================ */

/**
 * @brief Get AI guard statistics
 * @param arg Pointer to vos3_ai_ioctl_stats_t buffer
 * @return 0 on success, negative on error
 */
#define VOS3_IOCTL_AI_GET_STATS     VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x01)

/**
 * @brief Reset AI guard statistics counters
 * @param arg Unused (NULL)
 * @return 0 on success, negative on error
 * @note Requires appropriate permissions
 */
#define VOS3_IOCTL_AI_RESET_STATS   VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x02)

/**
 * @brief Set anomaly detection threshold
 * @param arg Pointer to uint32_t threshold value
 * @return 0 on success, negative on error
 */
#define VOS3_IOCTL_AI_SET_THRESHOLD VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x03)

/**
 * @brief Get current telemetry mode (POLL/EVENT)
 * @param arg Pointer to uint32_t to receive mode
 * @return 0 on success, negative on error
 */
#define VOS3_IOCTL_AI_GET_MODE      VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x04)

/**
 * @brief Set telemetry mode
 * @param arg Mode value (0=POLL, 1=EVENT)
 * @return 0 on success, negative on error
 */
#define VOS3_IOCTL_AI_SET_MODE      VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x05)

/**
 * @brief Get pending alert count
 * @param arg Pointer to uint32_t to receive count
 * @return 0 on success, negative on error
 */
#define VOS3_IOCTL_AI_ALERT_COUNT   VOS3_IOC(VOS3_IOC_MAGIC_AI, 0x06)

/* ============================================================================
 * AI STATISTICS STRUCTURE (for GET_STATS ioctl)
 * ============================================================================ */

/**
 * @brief AI Guard statistics for ioctl export
 */
typedef struct vos3_ai_ioctl_stats {
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
} vos3_ai_ioctl_stats_t;

/* ============================================================================
 * SMP IOCTL COMMANDS (/dev/smp)
 * ============================================================================ */

#define VOS3_IOC_MAGIC_SMP      'S'   /**< SMP devices (0x53) */

/**
 * @brief Get SMP/CPU statistics
 * @param arg Pointer to vos3_smp_ioctl_stats_t buffer
 * @return 0 on success, negative on error
 */
#define VOS3_IOCTL_SMP_GET_STATS    VOS3_IOC(VOS3_IOC_MAGIC_SMP, 0x01)

/** @brief Maximum CPUs supported (canonical definition in smp.h) */
#ifndef VOS3_SMP_MAX_CPUS
#define VOS3_SMP_MAX_CPUS       256
#endif

/* ============================================================================
 * SMP STATISTICS STRUCTURE (for GET_STATS ioctl)
 * ============================================================================ */

/**
 * @brief Per-CPU statistics
 */
typedef struct vos3_cpu_ioctl_stat {
    uint32_t cpu_id;            /**< Logical CPU ID */
    uint32_t apic_id;           /**< Hardware APIC ID */
    uint32_t online;            /**< 1 if online, 0 if offline */
    uint32_t usage_percent;     /**< CPU usage (0-100) */
    uint64_t idle_ticks;        /**< Total idle ticks */
    uint64_t busy_ticks;        /**< Total busy ticks */
    uint32_t task_count;        /**< Tasks on this CPU's runqueue */
    uint32_t reserved;          /**< Padding */
} vos3_cpu_ioctl_stat_t;

/**
 * @brief SMP statistics for ioctl export
 */
typedef struct vos3_smp_ioctl_stats {
    uint32_t cpu_count;         /**< Total number of CPUs */
    uint32_t online_count;      /**< Number of online CPUs */
    uint32_t bsp_id;            /**< Bootstrap processor ID */
    uint32_t reserved;          /**< Padding */
    vos3_cpu_ioctl_stat_t cpus[VOS3_SMP_MAX_CPUS];  /**< Per-CPU stats */
} vos3_smp_ioctl_stats_t;

#endif /* VOS3_IOCTL_H */
