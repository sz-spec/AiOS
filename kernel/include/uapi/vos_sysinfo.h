#ifndef VOS3_UAPI_SYSINFO_H
#define VOS3_UAPI_SYSINFO_H

#include <stddef.h>
#include <stdint.h>

/* Native telemetry, independent of Linux syscall 99. Both size and version
 * are mandatory arguments; v1 accepts exactly this 40-byte wire layout.
 * Unknown versions/sizes return -EINVAL without writing the destination.
 * A failed copy returns -EFAULT and may have written a prefix. */
#define VOS3_SYSINFO_SYSCALL 483
#define VOS3_SYSINFO_VERSION 1

typedef struct {
    uint64_t free_pages;
    uint64_t total_pages;
    uint32_t nr_tasks;
    uint32_t nr_zombies;
    uint64_t uptime_ms;
    uint32_t hugepage_total;
    uint32_t hugepage_used;
} vos3_sysinfo_t;

_Static_assert(sizeof(vos3_sysinfo_t) == 40, "sysinfo v1 wire size");
_Static_assert(offsetof(vos3_sysinfo_t, free_pages) == 0, "free_pages offset");
_Static_assert(offsetof(vos3_sysinfo_t, total_pages) == 8, "total_pages offset");
_Static_assert(offsetof(vos3_sysinfo_t, nr_tasks) == 16, "nr_tasks offset");
_Static_assert(offsetof(vos3_sysinfo_t, nr_zombies) == 20, "nr_zombies offset");
_Static_assert(offsetof(vos3_sysinfo_t, uptime_ms) == 24, "uptime_ms offset");
_Static_assert(offsetof(vos3_sysinfo_t, hugepage_total) == 32, "hugepage_total offset");
_Static_assert(offsetof(vos3_sysinfo_t, hugepage_used) == 36, "hugepage_used offset");

#endif
