/**
 * @file time.h
 * @brief VOS3 User-Space Time Definitions
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_USER_TIME_H
#define VOS3_USER_TIME_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * TYPE DEFINITIONS
 * ============================================================================ */

typedef int64_t time_t;
typedef int64_t suseconds_t;
typedef int32_t clockid_t;

/* ============================================================================
 * TIME STRUCTURES
 * ============================================================================ */

/** @brief Time value (seconds + microseconds) */
struct timeval {
    time_t      tv_sec;     /**< Seconds since epoch */
    suseconds_t tv_usec;    /**< Microseconds */
};

/** @brief Time specification (seconds + nanoseconds) */
struct timespec {
    time_t  tv_sec;     /**< Seconds since epoch */
    long    tv_nsec;    /**< Nanoseconds */
};

/** @brief Timezone */
struct timezone {
    int tz_minuteswest; /**< Minutes west of UTC */
    int tz_dsttime;     /**< DST correction type */
};

/** @brief Broken-down time */
struct tm {
    int tm_sec;     /**< Seconds (0-60) */
    int tm_min;     /**< Minutes (0-59) */
    int tm_hour;    /**< Hours (0-23) */
    int tm_mday;    /**< Day of month (1-31) */
    int tm_mon;     /**< Month (0-11) */
    int tm_year;    /**< Years since 1900 */
    int tm_wday;    /**< Day of week (0-6, Sunday=0) */
    int tm_yday;    /**< Day of year (0-365) */
    int tm_isdst;   /**< DST flag */
};

/* ============================================================================
 * CLOCK IDS
 * ============================================================================ */

#define CLOCK_REALTIME          0
#define CLOCK_MONOTONIC         1
#define CLOCK_PROCESS_CPUTIME_ID 2
#define CLOCK_THREAD_CPUTIME_ID  3
#define CLOCK_BOOTTIME          7

/* ============================================================================
 * SYSTEM INFO STRUCTURE
 * ============================================================================ */

/** @brief System information */
struct utsname {
    char sysname[65];   /**< Operating system name */
    char nodename[65];  /**< Network node hostname */
    char release[65];   /**< OS release */
    char version[65];   /**< OS version */
    char machine[65];   /**< Hardware identifier */
};

/* ============================================================================
 * TIME FUNCTIONS
 * ============================================================================ */

/**
 * @brief Get current Unix time
 * @param[out] tloc If non-NULL, time is stored here
 * @return Current Unix timestamp
 */
time_t time(time_t *tloc);

/**
 * @brief Get current time of day
 * @param[out] tv Time value
 * @param[out] tz Timezone (may be NULL)
 * @return 0 on success, -1 on error
 */
int gettimeofday(struct timeval *tv, struct timezone *tz);

/**
 * @brief Get time from specified clock
 * @param[in] clk_id Clock ID
 * @param[out] tp Timespec output
 * @return 0 on success, -1 on error
 */
int clock_gettime(clockid_t clk_id, struct timespec *tp);

/**
 * @brief Convert timestamp to broken-down time (UTC)
 * @param[in] timep Timestamp
 * @param[out] result Broken-down time
 * @return result on success, NULL on error
 */
struct tm *gmtime_r(const time_t *timep, struct tm *result);

/**
 * @brief Convert timestamp to broken-down time (UTC) - non-reentrant
 * @param[in] timep Timestamp
 * @return Static broken-down time
 */
struct tm *gmtime(const time_t *timep);

/**
 * @brief Convert timestamp to broken-down time (local)
 * @param[in] timep Timestamp
 * @param[out] result Broken-down time
 * @return result on success, NULL on error
 */
struct tm *localtime_r(const time_t *timep, struct tm *result);

/**
 * @brief Convert timestamp to broken-down time (local) - non-reentrant
 * @param[in] timep Timestamp
 * @return Static broken-down time
 */
struct tm *localtime(const time_t *timep);

/**
 * @brief Format time to string
 * @param[out] s Output buffer
 * @param[in] max Buffer size
 * @param[in] format Format string
 * @param[in] tm Broken-down time
 * @return Number of bytes written, 0 on error
 */
size_t strftime(char *s, size_t max, const char *format, const struct tm *tm);

/**
 * @brief Get system information
 * @param[out] buf System info buffer
 * @return 0 on success, -1 on error
 */
int uname(struct utsname *buf);

#endif /* VOS3_USER_TIME_H */
