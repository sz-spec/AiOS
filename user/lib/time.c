/**
 * @file time.c
 * @brief VOS3 User-Space Time Functions
 *
 * @version 1.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "time.h"
#include "syscall.h"
#include "string.h"
#include "stdio.h"

/* ============================================================================
 * SYSCALL NUMBERS
 * ============================================================================ */

#define SYS_TIME            202  /* was 201, collided with VOS3_SYS_CONFIG_SET */
#define SYS_GETTIMEOFDAY    96
#define SYS_CLOCK_GETTIME   228
#define SYS_UNAME           63

/* ============================================================================
 * STATIC DATA
 * ============================================================================ */

/** @brief Static tm for non-reentrant functions */
static struct tm g_tm;

/** @brief Days in each month (non-leap year) */
static const int days_in_month[] = {
    31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31
};

/** @brief Month names */
static const char *month_names[] = {
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
};

/** @brief Day names */
static const char *day_names[] = {
    "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"
};

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

static int is_leap_year(int year)
{
    return ((year % 4 == 0) && (year % 100 != 0)) || (year % 400 == 0);
}

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

time_t time(time_t *tloc)
{
    return (time_t)syscall1(SYS_TIME, (long)tloc);
}

int gettimeofday(struct timeval *tv, struct timezone *tz)
{
    return (int)syscall2(SYS_GETTIMEOFDAY, (long)tv, (long)tz);
}

int clock_gettime(clockid_t clk_id, struct timespec *tp)
{
    return (int)syscall2(SYS_CLOCK_GETTIME, (long)clk_id, (long)tp);
}

struct tm *gmtime_r(const time_t *timep, struct tm *result)
{
    if (timep == NULL || result == NULL) {
        return NULL;
    }

    time_t t = *timep;

    if (t < 0) {
        return NULL;
    }

    /* Calculate seconds, minutes, hours */
    result->tm_sec = (int)(t % 60);
    t /= 60;
    result->tm_min = (int)(t % 60);
    t /= 60;
    result->tm_hour = (int)(t % 24);
    t /= 24;

    /* t is now days since epoch */
    long days = (long)t;

    /* Day of week (Jan 1, 1970 was Thursday = 4) */
    result->tm_wday = (int)((days + 4) % 7);

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
    result->tm_yday = (int)days;

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
    result->tm_mday = (int)(days + 1);

    result->tm_isdst = 0;

    return result;
}

struct tm *gmtime(const time_t *timep)
{
    return gmtime_r(timep, &g_tm);
}

struct tm *localtime_r(const time_t *timep, struct tm *result)
{
    /* For now, local time = UTC */
    return gmtime_r(timep, result);
}

struct tm *localtime(const time_t *timep)
{
    return localtime_r(timep, &g_tm);
}

size_t strftime(char *s, size_t max, const char *format, const struct tm *tm)
{
    if (s == NULL || format == NULL || tm == NULL || max == 0) {
        return 0;
    }

    size_t pos = 0;
    const char *p = format;

    while (*p != '\0' && pos < max - 1) {
        if (*p != '%') {
            s[pos++] = *p++;
            continue;
        }

        p++;  /* Skip '%' */

        if (*p == '\0') {
            break;
        }

        char buf[32];
        const char *str = buf;

        switch (*p) {
            case 'a':  /* Abbreviated weekday */
                str = day_names[tm->tm_wday % 7];
                break;

            case 'A':  /* Full weekday */
                str = day_names[tm->tm_wday % 7];
                break;

            case 'b':  /* Abbreviated month */
            case 'h':
                str = month_names[tm->tm_mon % 12];
                break;

            case 'B':  /* Full month */
                str = month_names[tm->tm_mon % 12];
                break;

            case 'd':  /* Day of month (01-31) */
                snprintf(buf, sizeof(buf), "%02d", tm->tm_mday);
                break;

            case 'e':  /* Day of month ( 1-31) */
                snprintf(buf, sizeof(buf), "%2d", tm->tm_mday);
                break;

            case 'H':  /* Hour (00-23) */
                snprintf(buf, sizeof(buf), "%02d", tm->tm_hour);
                break;

            case 'I':  /* Hour (01-12) */
                {
                    int h = tm->tm_hour % 12;
                    if (h == 0) h = 12;
                    snprintf(buf, sizeof(buf), "%02d", h);
                }
                break;

            case 'j':  /* Day of year (001-366) */
                snprintf(buf, sizeof(buf), "%03d", tm->tm_yday + 1);
                break;

            case 'm':  /* Month (01-12) */
                snprintf(buf, sizeof(buf), "%02d", tm->tm_mon + 1);
                break;

            case 'M':  /* Minute (00-59) */
                snprintf(buf, sizeof(buf), "%02d", tm->tm_min);
                break;

            case 'n':  /* Newline */
                buf[0] = '\n';
                buf[1] = '\0';
                break;

            case 'p':  /* AM/PM */
                str = (tm->tm_hour < 12) ? "AM" : "PM";
                break;

            case 'S':  /* Second (00-60) */
                snprintf(buf, sizeof(buf), "%02d", tm->tm_sec);
                break;

            case 't':  /* Tab */
                buf[0] = '\t';
                buf[1] = '\0';
                break;

            case 'u':  /* Weekday (1-7, Monday=1) */
                snprintf(buf, sizeof(buf), "%d", tm->tm_wday == 0 ? 7 : tm->tm_wday);
                break;

            case 'w':  /* Weekday (0-6, Sunday=0) */
                snprintf(buf, sizeof(buf), "%d", tm->tm_wday);
                break;

            case 'Y':  /* Year with century */
                snprintf(buf, sizeof(buf), "%d", tm->tm_year + 1900);
                break;

            case 'y':  /* Year without century (00-99) */
                snprintf(buf, sizeof(buf), "%02d", (tm->tm_year + 1900) % 100);
                break;

            case 'Z':  /* Timezone name */
                str = "UTC";
                break;

            case '%':  /* Literal % */
                buf[0] = '%';
                buf[1] = '\0';
                break;

            default:
                buf[0] = '%';
                buf[1] = *p;
                buf[2] = '\0';
                break;
        }

        /* Copy string to output */
        while (*str != '\0' && pos < max - 1) {
            s[pos++] = *str++;
        }

        p++;
    }

    s[pos] = '\0';
    return pos;
}

int uname(struct utsname *buf)
{
    return (int)syscall1(SYS_UNAME, (long)buf);
}
