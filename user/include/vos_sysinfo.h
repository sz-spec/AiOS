#ifndef VOS3_USER_SYSINFO_H
#define VOS3_USER_SYSINFO_H

#include "../../kernel/include/uapi/vos_sysinfo.h"
#include "syscall.h"

/* Callers must check the result before inspecting the snapshot. */
static inline int vos3_get_sysinfo(vos3_sysinfo_t* info)
{
    return (int)syscall3(VOS3_SYSINFO_SYSCALL, (long)info,
                        sizeof(*info), VOS3_SYSINFO_VERSION);
}

#endif
