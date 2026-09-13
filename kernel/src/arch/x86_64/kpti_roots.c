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

    for (size_t i = 0; i < 256; ++i) restricted[i] = full[i];
    for (size_t i = 256; i < 512; ++i) {
        restricted[i] = (i == 256 || i == 511) ? full[i] : 0;
    }
    return 0;
}
