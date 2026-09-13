#ifndef VOS3_KPTI_ROOTS_H
#define VOS3_KPTI_ROOTS_H
#include <stdint.h>

/* Caller owns/locks both 512-entry roots. Returns -1 for missing/overlapping
 * storage. Current partial boundary retains kernel entries 256 and 511. */
int vos3_kpti_sync_root(uint64_t* restricted, const uint64_t* full);
#endif
