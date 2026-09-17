#include "arch/x86_64/kpti_roots.h"
#include <stddef.h>

int vos3_kpti_sync_root(uint64_t* restricted, const uint64_t* full)
{
    if (restricted == NULL || full == NULL) return -1;
    uintptr_t dst = (uintptr_t)restricted;
    uintptr_t src = (uintptr_t)full;
    const size_t bytes = 512 * sizeof(uint64_t);
    if ((dst <= src && src - dst < bytes) ||
        (src <= dst && dst - src < bytes)) return -1;

    /* The allowed slots form one contiguous range plus the kernel-image
     * slot. Keep the same full synchronization, without testing the slot
     * policy again on each iteration of the upper-half loop. */
    for (size_t i = 0; i < 257; ++i) restricted[i] = full[i];
    for (size_t i = 257; i < 511; ++i) restricted[i] = 0;
    restricted[511] = full[511];
    return 0;
}
