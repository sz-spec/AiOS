/**
 * @file posix_syscall.c
 * @brief Task 1.5 — POSIX core syscalls: getrusage, getrlimit, setrlimit,
 *                    setitimer, getitimer
 *
 * Implements Linux-compatible syscall numbers:
 *   36  getitimer
 *   38  setitimer
 *   97  getrlimit
 *   98  getrusage
 *  160  setrlimit
 */

#include "../../include/vos/syscall.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/timer.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/pmm.h"

/* ============================================================================
 * SYSCALL NUMBERS
 * ============================================================================ */

#define SYS_GETITIMER   36
#define SYS_SETITIMER   38
#define SYS_GETRLIMIT   97
#define SYS_GETRUSAGE   98
#define SYS_SYSINFO     99
#define SYS_SETRLIMIT   160

/* ============================================================================
 * TYPE DEFINITIONS  (mirror Linux ABI exactly)
 * ============================================================================ */

/** @brief POSIX struct timeval */
typedef struct {
    long tv_sec;
    long tv_usec;
} posix_timeval_t;

/** @brief struct rusage (Linux ABI) */
typedef struct {
    posix_timeval_t ru_utime;       /**< user CPU time used */
    posix_timeval_t ru_stime;       /**< system CPU time used */
    long            ru_maxrss;      /**< maximum resident set size */
    long            ru_ixrss;
    long            ru_idrss;
    long            ru_isrss;
    long            ru_minflt;
    long            ru_majflt;
    long            ru_nswap;
    long            ru_inblock;
    long            ru_oublock;
    long            ru_msgsnd;
    long            ru_msgrcv;
    long            ru_nsignals;
    long            ru_nvcsw;       /**< voluntary context switches */
    long            ru_nivcsw;      /**< involuntary context switches */
} posix_rusage_t;

/** @brief struct rlimit */
typedef struct {
    unsigned long rlim_cur;         /**< Soft limit */
    unsigned long rlim_max;         /**< Hard limit */
} posix_rlimit_t;

/** @brief struct itimerval */
typedef struct {
    posix_timeval_t it_interval;    /**< Interval for periodic timer */
    posix_timeval_t it_value;       /**< Time until next expiration */
} posix_itimerval_t;

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

#define RUSAGE_SELF         0
#define RUSAGE_CHILDREN     (-1)

#define RLIMIT_CPU          0
#define RLIMIT_FSIZE        1
#define RLIMIT_DATA         2
#define RLIMIT_STACK        3
#define RLIMIT_CORE         4
#define RLIMIT_RSS          5
#define RLIMIT_NPROC        6
#define RLIMIT_NOFILE       7
#define RLIMIT_MEMLOCK      8
#define RLIMIT_AS           9
#define RLIMIT_NLIMITS      16

#define ITIMER_REAL         0
#define ITIMER_VIRTUAL      1
#define ITIMER_PROF         2

/** @brief VOS3 timer frequency: 100 Hz (10 ms per tick) */
#define TICKS_PER_SEC       100ULL

/** @brief Unlimited resource value */
#define RLIM_INFINITY       ((unsigned long)-1UL)

/* ============================================================================
 * GLOBAL RESOURCE LIMITS (process-wide defaults)
 * ============================================================================ */

static posix_rlimit_t g_rlimits[RLIMIT_NLIMITS];

/* ============================================================================
 * HELPERS
 * ============================================================================ */

static int posix_access_ok(const void* addr, size_t len)
{
    if (addr == NULL) return 0;
    if ((uint64_t)(uintptr_t)addr >= 0xFFFF800000000000ULL) return 0;
    (void)len;
    return 1;
}

/** @brief Convert ms to a posix_timeval_t */
static void ms_to_timeval(uint64_t ms, posix_timeval_t* tv)
{
    tv->tv_sec  = (long)(ms / 1000ULL);
    tv->tv_usec = (long)((ms % 1000ULL) * 1000ULL);
}

/** @brief Convert a posix_timeval_t to ticks */
static uint64_t timeval_to_ticks(const posix_timeval_t* tv)
{
    uint64_t ms = (uint64_t)tv->tv_sec * 1000ULL
                + (uint64_t)tv->tv_usec / 1000ULL;
    /* Convert ms to ticks: divide by 10 (100 Hz timer = 10 ms/tick) */
    return ms / (1000ULL / TICKS_PER_SEC);
}

/** @brief Convert ticks to a posix_timeval_t */
static void ticks_to_timeval(uint64_t ticks, posix_timeval_t* tv)
{
    /* ticks * 10 = ms */
    uint64_t ms = ticks * (1000ULL / TICKS_PER_SEC);
    ms_to_timeval(ms, tv);
}

/* ============================================================================
 * SYS_GETRUSAGE (98)
 * ============================================================================ */

static int64_t sys_getrusage(int who, posix_rusage_t* user_buf)
{
    if (!posix_access_ok(user_buf, sizeof(posix_rusage_t))) {
        return -14;  /* EFAULT */
    }

    posix_rusage_t ru;
    memset(&ru, 0, sizeof(ru));

    if (who == RUSAGE_SELF) {
        vos3_task_t* current = vos3_sched_current();
        if (current != NULL) {
            /* total_runtime is in ticks; convert to user CPU time */
            ticks_to_timeval(current->total_runtime, &ru.ru_utime);
            ru.ru_nvcsw  = (long)current->voluntary_switches;
            ru.ru_nivcsw = (long)current->involuntary_switches;
        }
    }
    /* RUSAGE_CHILDREN: return zeros (no child tracking yet) */

    memcpy(user_buf, &ru, sizeof(posix_rusage_t));
    return 0;
}

/* ============================================================================
 * SYS_GETRLIMIT (97) / SYS_SETRLIMIT (160)
 * ============================================================================ */

static int64_t sys_getrlimit(unsigned int resource, posix_rlimit_t* user_buf)
{
    if (resource >= RLIMIT_NLIMITS) {
        return -22;  /* EINVAL */
    }
    if (!posix_access_ok(user_buf, sizeof(posix_rlimit_t))) {
        return -14;  /* EFAULT */
    }

    memcpy(user_buf, &g_rlimits[resource], sizeof(posix_rlimit_t));
    return 0;
}

static int64_t sys_setrlimit(unsigned int resource, const posix_rlimit_t* user_buf)
{
    if (resource >= RLIMIT_NLIMITS) {
        return -22;  /* EINVAL */
    }
    if (!posix_access_ok(user_buf, sizeof(posix_rlimit_t))) {
        return -14;  /* EFAULT */
    }

    posix_rlimit_t rl;
    memcpy(&rl, user_buf, sizeof(posix_rlimit_t));

    /* Soft limit cannot exceed hard limit */
    if (rl.rlim_cur > rl.rlim_max) {
        return -22;  /* EINVAL */
    }

    g_rlimits[resource] = rl;
    return 0;
}

/* ============================================================================
 * SYS_SETITIMER (38) / SYS_GETITIMER (36)
 * ============================================================================ */

static int64_t sys_setitimer(int which, const posix_itimerval_t* user_new,
                              posix_itimerval_t* user_old)
{
    if (which != ITIMER_REAL) {
        /* Only ITIMER_REAL supported; VIRTUAL/PROF require in-kernel CPU accounting */
        return -22;  /* EINVAL */
    }
    if (user_new != NULL && !posix_access_ok(user_new, sizeof(posix_itimerval_t))) {
        return -14;
    }
    if (user_old != NULL && !posix_access_ok(user_old, sizeof(posix_itimerval_t))) {
        return -14;
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -3;  /* ESRCH */
    }

    /* Return old timer value if requested */
    if (user_old != NULL) {
        posix_itimerval_t old;
        memset(&old, 0, sizeof(old));

        if (current->alarm_deadline != 0ULL) {
            uint64_t now = vos3_timer_get_ticks();
            uint64_t remaining = (current->alarm_deadline > now)
                                 ? (current->alarm_deadline - now) : 0ULL;
            ticks_to_timeval(remaining, &old.it_value);
        }
        ticks_to_timeval(current->itimer_interval_ticks, &old.it_interval);
        memcpy(user_old, &old, sizeof(posix_itimerval_t));
    }

    /* Install new timer */
    if (user_new != NULL) {
        posix_itimerval_t nv;
        memcpy(&nv, user_new, sizeof(posix_itimerval_t));

        uint64_t delay_ticks    = timeval_to_ticks(&nv.it_value);
        uint64_t interval_ticks = timeval_to_ticks(&nv.it_interval);

        if (delay_ticks == 0) {
            /* Cancel timer */
            current->alarm_deadline        = 0ULL;
            current->itimer_interval_ticks = 0ULL;
        } else {
            current->alarm_deadline        = vos3_timer_get_ticks() + delay_ticks;
            current->itimer_interval_ticks = interval_ticks;
        }
    }

    return 0;
}

static int64_t sys_getitimer(int which, posix_itimerval_t* user_buf)
{
    if (which != ITIMER_REAL) {
        return -22;  /* EINVAL */
    }
    if (!posix_access_ok(user_buf, sizeof(posix_itimerval_t))) {
        return -14;  /* EFAULT */
    }

    vos3_task_t* current = vos3_sched_current();
    if (current == NULL) {
        return -3;  /* ESRCH */
    }

    posix_itimerval_t tv;
    memset(&tv, 0, sizeof(tv));

    if (current->alarm_deadline != 0ULL) {
        uint64_t now = vos3_timer_get_ticks();
        uint64_t remaining = (current->alarm_deadline > now)
                             ? (current->alarm_deadline - now) : 0ULL;
        ticks_to_timeval(remaining, &tv.it_value);
    }
    ticks_to_timeval(current->itimer_interval_ticks, &tv.it_interval);

    memcpy(user_buf, &tv, sizeof(posix_itimerval_t));
    return 0;
}

/* ============================================================================
 * SYS_SYSINFO (99) — kernel telemetry for sustained tests
 * ============================================================================ */

/** @brief VOS3 sysinfo structure (user-space ABI).
 *  NOTE: new fields appended at end for backward compatibility. */
typedef struct {
    uint64_t free_pages;
    uint64_t total_pages;
    uint32_t nr_tasks;
    uint32_t nr_zombies;
    uint64_t uptime_ms;
    /* Added for VMM soak test — hugepage pool telemetry */
    uint32_t hugepage_total;
    uint32_t hugepage_used;
} vos3_sysinfo_t;

static int64_t sys_sysinfo(vos3_sysinfo_t* user_buf)
{
    if (!posix_access_ok(user_buf, sizeof(vos3_sysinfo_t))) {
        return -14;  /* EFAULT */
    }

    vos3_sysinfo_t info;
    info.free_pages  = (uint64_t)vos3_pmm_free_pages_count();
    info.total_pages = (uint64_t)vos3_pmm_total_pages_count();
    vos3_task_count_stats(&info.nr_tasks, &info.nr_zombies);
    info.uptime_ms   = vos3_timer_get_uptime_ms();
    vos3_pmm_hugepage_stats(&info.hugepage_total, &info.hugepage_used);

    memcpy(user_buf, &info, sizeof(vos3_sysinfo_t));
    return 0;
}

/* ============================================================================
 * DISPATCH
 * ============================================================================ */

static int64_t posix_syscall_handler(vos3_syscall_frame_t* frame)
{
    uint64_t num = frame->rax;

    switch (num) {
        case SYS_GETRUSAGE:
            return sys_getrusage((int)frame->rdi,
                                 (posix_rusage_t*)frame->rsi);

        case SYS_GETRLIMIT:
            return sys_getrlimit((unsigned int)frame->rdi,
                                 (posix_rlimit_t*)frame->rsi);

        case SYS_SETRLIMIT:
            return sys_setrlimit((unsigned int)frame->rdi,
                                 (const posix_rlimit_t*)frame->rsi);

        case SYS_SETITIMER:
            return sys_setitimer((int)frame->rdi,
                                 (const posix_itimerval_t*)frame->rsi,
                                 (posix_itimerval_t*)frame->rdx);

        case SYS_GETITIMER:
            return sys_getitimer((int)frame->rdi,
                                 (posix_itimerval_t*)frame->rsi);

        case SYS_SYSINFO:
            return sys_sysinfo((vos3_sysinfo_t*)frame->rdi);

        default:
            return -38;  /* ENOSYS */
    }
}

/* ============================================================================
 * INITIALIZATION
 * ============================================================================ */

void vos3_posix_syscalls_init(void)
{
    VOS3_INFO("Registering POSIX core syscalls (Task 1.5)");

    /* Initialize resource limits with sensible defaults */
    for (int i = 0; i < RLIMIT_NLIMITS; i++) {
        g_rlimits[i].rlim_cur = RLIM_INFINITY;
        g_rlimits[i].rlim_max = RLIM_INFINITY;
    }
    /* VOS3-specific overrides */
    g_rlimits[RLIMIT_NOFILE].rlim_cur = 1024UL;
    g_rlimits[RLIMIT_NOFILE].rlim_max = 1024UL;
    g_rlimits[RLIMIT_STACK].rlim_cur  = 8UL * 1024UL * 1024UL;
    g_rlimits[RLIMIT_STACK].rlim_max  = 64UL * 1024UL * 1024UL;
    g_rlimits[RLIMIT_CORE].rlim_cur   = 0UL;   /* No core dumps */
    g_rlimits[RLIMIT_CORE].rlim_max   = 0UL;

    vos3_syscall_register(SYS_GETITIMER, posix_syscall_handler);
    vos3_syscall_register(SYS_SETITIMER, posix_syscall_handler);
    vos3_syscall_register(SYS_GETRLIMIT, posix_syscall_handler);
    vos3_syscall_register(SYS_GETRUSAGE, posix_syscall_handler);
    vos3_syscall_register(SYS_SYSINFO,  posix_syscall_handler);
    vos3_syscall_register(SYS_SETRLIMIT, posix_syscall_handler);

    VOS3_INFO("POSIX core syscalls registered (getrusage/getrlimit/setrlimit/setitimer/getitimer/sysinfo)");
}
