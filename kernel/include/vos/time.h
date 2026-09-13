/**
 * @file time.h
 * @brief VOS3 Time and Date Definitions
 *
 * @details POSIX-compatible time structures and functions.
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_TIME_H
#define VOS3_TIME_H

#include <stdint.h>

/* ============================================================================
 * TIME STRUCTURES
 * ============================================================================ */

/** @brief Time value (seconds + microseconds) */
typedef struct vos3_timeval {
    int64_t tv_sec;     /**< Seconds since epoch */
    int64_t tv_usec;    /**< Microseconds */
} vos3_timeval_t;

/** @brief Time specification (seconds + nanoseconds) */
typedef struct vos3_timespec {
    int64_t tv_sec;     /**< Seconds since epoch */
    int64_t tv_nsec;    /**< Nanoseconds */
} vos3_timespec_t;

/** @brief Timezone (for compatibility) */
typedef struct vos3_timezone {
    int32_t tz_minuteswest; /**< Minutes west of UTC */
    int32_t tz_dsttime;     /**< DST correction type */
} vos3_timezone_t;

/** @brief Broken-down time */
typedef struct vos3_tm {
    int32_t tm_sec;     /**< Seconds (0-60) */
    int32_t tm_min;     /**< Minutes (0-59) */
    int32_t tm_hour;    /**< Hours (0-23) */
    int32_t tm_mday;    /**< Day of month (1-31) */
    int32_t tm_mon;     /**< Month (0-11) */
    int32_t tm_year;    /**< Years since 1900 */
    int32_t tm_wday;    /**< Day of week (0-6, Sunday=0) */
    int32_t tm_yday;    /**< Day of year (0-365) */
    int32_t tm_isdst;   /**< DST flag */
} vos3_tm_t;

/* ============================================================================
 * CLOCK IDS
 * ============================================================================ */

#define VOS3_CLOCK_REALTIME         0   /**< System-wide realtime clock */
#define VOS3_CLOCK_MONOTONIC        1   /**< Monotonic clock (uptime) */
#define VOS3_CLOCK_PROCESS_CPUTIME  2   /**< Process CPU time */
#define VOS3_CLOCK_THREAD_CPUTIME   3   /**< Thread CPU time */
#define VOS3_CLOCK_BOOTTIME         7   /**< Time since boot */

/* ============================================================================
 * SYSTEM INFO STRUCTURE
 * ============================================================================ */

/** @brief System information */
typedef struct vos3_utsname {
    char sysname[65];   /**< Operating system name */
    char nodename[65];  /**< Network node hostname */
    char release[65];   /**< OS release */
    char version[65];   /**< OS version */
    char machine[65];   /**< Hardware identifier */
} vos3_utsname_t;

/* ============================================================================
 * BOOT TIME
 * ============================================================================ */

/**
 * @brief Set the boot time (called once during boot)
 * @param[in] boot_time Unix timestamp at boot
 */
void vos3_time_set_boot_time(int64_t boot_time);

/**
 * @brief Get boot time
 * @return Unix timestamp at boot
 */
int64_t vos3_time_get_boot_time(void);

/* ============================================================================
 * TIME FUNCTIONS
 * ============================================================================ */

/**
 * @brief Get current time of day
 * @param[out] tv Time value
 * @param[out] tz Timezone (may be NULL)
 * @return 0 on success, negative on error
 */
int vos3_gettimeofday(vos3_timeval_t* tv, vos3_timezone_t* tz);

/**
 * @brief Get time from specified clock
 * @param[in] clk_id Clock ID
 * @param[out] tp Timespec output
 * @return 0 on success, negative on error
 */
int vos3_clock_gettime(int clk_id, vos3_timespec_t* tp);

/**
 * @brief Get current Unix time
 * @param[out] tloc If non-NULL, time is stored here
 * @return Current Unix timestamp
 */
int64_t vos3_time(int64_t* tloc);

/**
 * @brief Convert timestamp to broken-down time (UTC)
 * @param[in] timep Timestamp
 * @param[out] result Broken-down time
 * @return result on success, NULL on error
 */
vos3_tm_t* vos3_gmtime(const int64_t* timep, vos3_tm_t* result);

/**
 * @brief Get system information
 * @param[out] buf System info buffer
 * @return 0 on success, negative on error
 */
int vos3_uname(vos3_utsname_t* buf);

/* ============================================================================
 * TIME SYSCALLS INIT
 * ============================================================================ */

/**
 * @brief Initialize time syscalls
 */
void vos3_time_syscalls_init(void);

#endif /* VOS3_TIME_H */
