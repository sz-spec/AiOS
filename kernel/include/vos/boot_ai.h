#ifndef VOS3_BOOT_AI_H
#define VOS3_BOOT_AI_H

/**
 * @brief Initialize AI subsystems.
 *
 * AI Memory Guard, AI Access Monitor, and Phase 17.5 extended
 * self-tests (integrity automation, NUMA awareness, shared regions,
 * memory pressure, telemetry export).  Warns on failure; non-fatal.
 *
 * @return 0 on success, negative on failure (non-fatal).
 */
int boot_ai_init(void);

#endif /* VOS3_BOOT_AI_H */
