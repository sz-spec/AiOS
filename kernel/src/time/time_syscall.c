/**
 * @file time_syscall.c
 * @brief VOS3 Time System Calls
 *
 * @details Implements time-related system calls.
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/time.h"
#include "../../include/vos/timer.h"
#include "../../include/vos/syscall.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/uaccess.h"

/* ============================================================================
 * SYSCALL NUMBERS (Linux x86_64 compatible)
 * ============================================================================ */

#define SYS_TIME            202  /* was 201, collided with VOS3_SYS_CONFIG_SET */
#define SYS_GETTIMEOFDAY    96
#define SYS_CLOCK_GETTIME   228
#define SYS_UNAME           63

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

/** @brief Boot time (Unix timestamp) */
static int64_t g_boot_time = 0;

/** @brief System name info */
static vos3_utsname_t g_utsname = {
    .sysname  = "VOS3",
    .nodename = "vos3",
    .release  = "1.0.0",
    .version  = "#1 SMP 2026-02-16",
    .machine  = "x86_64"
};

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/** @brief Days in each month (non-leap year) */
static const int days_in_month[] = {
    31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31
};

/**
 * @brief Check if year is leap year
 */
static int is_leap_year(int year)
{
    return ((year % 4 == 0) && (year % 100 != 0)) || (year % 400 == 0);
}

/**
 * @brief Get days in month
 */
static int get_days_in_month(int year, int month)
{
    if (month == 1 && is_leap_year(year)) {
        return 29;
    }
    return days_in_month[month];
}

/* ============================================================================
 * TIME FUNCTIONS
 * ============================================================================ */

void vos3_time_set_boot_time(int64_t boot_time)
{
    g_boot_time = boot_time;
    VOS3_INFO("Boot time set to %lld", (long long)boot_time);
}

int64_t vos3_time_get_boot_time(void)
{
    return g_boot_time;
}

int vos3_gettimeofday(vos3_timeval_t* tv, vos3_timezone_t* tz)
{
    if (tv == NULL) {
        return -22;  /* EINVAL */
    }

    /* Get uptime in milliseconds */
    uint64_t uptime_ms = vos3_timer_get_uptime_ms();

    /* Calculate current time */
    tv->tv_sec = g_boot_time + (int64_t)(uptime_ms / 1000ULL);
    tv->tv_usec = (int64_t)((uptime_ms % 1000ULL) * 1000ULL);

    /* Timezone is UTC for now */
    if (tz != NULL) {
        tz->tz_minuteswest = 0;
        tz->tz_dsttime = 0;
    }

    return 0;
}

int vos3_clock_gettime(int clk_id, vos3_timespec_t* tp)
{
    if (tp == NULL) {
        return -22;  /* EINVAL */
    }

    uint64_t uptime_ms = vos3_timer_get_uptime_ms();

    switch (clk_id) {
        case VOS3_CLOCK_REALTIME:
            tp->tv_sec = g_boot_time + (int64_t)(uptime_ms / 1000ULL);
            tp->tv_nsec = (int64_t)((uptime_ms % 1000ULL) * 1000000LL);
            break;

        case VOS3_CLOCK_MONOTONIC:
        case VOS3_CLOCK_BOOTTIME:
            tp->tv_sec = (int64_t)(uptime_ms / 1000ULL);
            tp->tv_nsec = (int64_t)((uptime_ms % 1000ULL) * 1000000LL);
            break;

        case VOS3_CLOCK_PROCESS_CPUTIME:
        case VOS3_CLOCK_THREAD_CPUTIME:
            /* TODO: Track per-process/thread CPU time */
            tp->tv_sec = (int64_t)(uptime_ms / 1000ULL);
            tp->tv_nsec = (int64_t)((uptime_ms % 1000ULL) * 1000000LL);
            break;

        default:
            return -22;  /* EINVAL */
    }

    return 0;
}

int64_t vos3_time(int64_t* tloc)
{
    uint64_t uptime_sec = vos3_timer_get_uptime_sec();
    int64_t t = g_boot_time + (int64_t)uptime_sec;

    if (tloc != NULL) {
        *tloc = t;
    }

    return t;
}

vos3_tm_t* vos3_gmtime(const int64_t* timep, vos3_tm_t* result)
{
    if (timep == NULL || result == NULL) {
        return NULL;
    }

    int64_t t = *timep;

    /* Handle negative time (before epoch) */
    if (t < 0) {
        return NULL;
    }

    /* Calculate seconds, minutes, hours */
    result->tm_sec = (int32_t)(t % 60);
    t /= 60;
    result->tm_min = (int32_t)(t % 60);
    t /= 60;
    result->tm_hour = (int32_t)(t % 24);
    t /= 24;

    /* t is now days since epoch (Jan 1, 1970) */
    int64_t days = t;

    /* Day of week (Jan 1, 1970 was Thursday = 4) */
    result->tm_wday = (int32_t)((days + 4) % 7);

    /* Calculate year */
    int year = 1970;
    while (1) {
        int days_in_year = is_leap_year(year) ? 366 : 365;
        if (days < days_in_year) {
            break;
        }
        days -= days_in_year;
        year++;
    }
    result->tm_year = year - 1900;
    result->tm_yday = (int32_t)days;

    /* Calculate month and day */
    int month = 0;
    while (month < 12) {
        int dim = get_days_in_month(year, month);
        if (days < dim) {
            break;
        }
        days -= dim;
        month++;
    }
    result->tm_mon = month;
    result->tm_mday = (int32_t)(days + 1);

    result->tm_isdst = 0;

    return result;
}

int vos3_uname(vos3_utsname_t* buf)
{
    if (buf == NULL) {
        return -22;  /* EINVAL */
    }

    memcpy(buf, &g_utsname, sizeof(vos3_utsname_t));
    return 0;
}

/* ============================================================================
 * SYSCALL HANDLERS
 * ============================================================================ */

/**
 * @brief Time syscall handler (Phase 29: Hardened)
 */
static int64_t time_syscall_handler(vos3_syscall_frame_t* frame)
{
    uint64_t syscall_num = frame->rax;

    switch (syscall_num) {
        case SYS_TIME: {
            int64_t* user_tloc = (int64_t*)frame->rdi;
            int64_t ktime;

            /* Get time into kernel buffer */
            ktime = vos3_time(NULL);

            /* Copy to user space if pointer provided */
            if (user_tloc != NULL) {
                if (!access_ok(user_tloc, sizeof(int64_t))) {
                    return -EFAULT;
                }
                if (copy_to_user(user_tloc, &ktime, sizeof(int64_t)) != 0) {
                    return -EFAULT;
                }
            }
            return ktime;
        }

        case SYS_GETTIMEOFDAY: {
            vos3_timeval_t* user_tv = (vos3_timeval_t*)frame->rdi;
            vos3_timezone_t* user_tz = (vos3_timezone_t*)frame->rsi;
            vos3_timeval_t ktv;
            vos3_timezone_t ktz;
            int result;

            /* Phase 29: Validate user pointers */
            if (user_tv != NULL && !access_ok(user_tv, sizeof(vos3_timeval_t))) {
                return -EFAULT;
            }
            if (user_tz != NULL && !access_ok(user_tz, sizeof(vos3_timezone_t))) {
                return -EFAULT;
            }

            /* Get time into kernel buffers */
            result = vos3_gettimeofday(&ktv, user_tz ? &ktz : NULL);
            if (result != 0) {
                return (int64_t)result;
            }

            /* Copy to user space */
            if (user_tv != NULL) {
                if (copy_to_user(user_tv, &ktv, sizeof(vos3_timeval_t)) != 0) {
                    return -EFAULT;
                }
            }
            if (user_tz != NULL) {
                if (copy_to_user(user_tz, &ktz, sizeof(vos3_timezone_t)) != 0) {
                    return -EFAULT;
                }
            }
            return 0;
        }

        case SYS_CLOCK_GETTIME: {
            int clk_id = (int)frame->rdi;
            vos3_timespec_t* user_tp = (vos3_timespec_t*)frame->rsi;
            vos3_timespec_t ktp;
            int result;

            /* Phase 29: Validate user pointer */
            if (user_tp == NULL || !access_ok(user_tp, sizeof(vos3_timespec_t))) {
                return -EFAULT;
            }

            /* Get time into kernel buffer */
            result = vos3_clock_gettime(clk_id, &ktp);
            if (result != 0) {
                return (int64_t)result;
            }

            /* Copy to user space */
            if (copy_to_user(user_tp, &ktp, sizeof(vos3_timespec_t)) != 0) {
                return -EFAULT;
            }
            return 0;
        }

        case SYS_UNAME: {
            vos3_utsname_t* user_buf = (vos3_utsname_t*)frame->rdi;
            vos3_utsname_t kbuf;
            int result;

            /* Phase 29: Validate user pointer */
            if (user_buf == NULL || !access_ok(user_buf, sizeof(vos3_utsname_t))) {
                return -EFAULT;
            }

            /* Get uname into kernel buffer */
            result = vos3_uname(&kbuf);
            if (result != 0) {
                return (int64_t)result;
            }

            /* Copy to user space */
            if (copy_to_user(user_buf, &kbuf, sizeof(vos3_utsname_t)) != 0) {
                return -EFAULT;
            }
            return 0;
        }

        default:
            return -38;  /* ENOSYS */
    }
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

void vos3_time_syscalls_init(void)
{
    VOS3_INFO("Registering time syscalls");

    vos3_syscall_register(SYS_TIME, time_syscall_handler);
    vos3_syscall_register(SYS_GETTIMEOFDAY, time_syscall_handler);
    vos3_syscall_register(SYS_CLOCK_GETTIME, time_syscall_handler);
    vos3_syscall_register(SYS_UNAME, time_syscall_handler);

    /* Set default boot time (can be updated from CMOS RTC) */
    /* Default: Feb 16, 2026 00:00:00 UTC = 1771113600 */
    if (g_boot_time == 0) {
        vos3_time_set_boot_time(1771113600LL);
    }

    VOS3_INFO("Time syscalls registered");
}
