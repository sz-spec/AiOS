#ifndef VOS3_KASLR_H
#define VOS3_KASLR_H

#include <stdint.h>

/**
 * @brief Initialize KASLR offsets using hardware entropy (RDTSC + RDSEED)
 * Must be called before heap and stack initialization.
 */
void vos3_kaslr_init(void);

/** @brief Get stack base randomization offset (2MB aligned) */
int64_t vos3_kaslr_stack_offset(void);

/** @brief Get heap base randomization offset (4KB aligned) */
int64_t vos3_kaslr_heap_offset(void);

#endif /* VOS3_KASLR_H */
