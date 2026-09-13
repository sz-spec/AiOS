/**
 * @file timer.h
 * @brief VOS3 Timer Interface
 *
 * @details Programmable Interval Timer (PIT) driver for preemptive
 *          multitasking and timekeeping.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_TIMER_H
#define VOS3_TIMER_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/* ============================================================================
 * PIT CONFIGURATION
 * ============================================================================ */

/** @brief PIT base frequency (1.193182 MHz) */
#define VOS3_PIT_FREQUENCY      ((uint32_t)1193182U)

/** @brief PIT I/O ports */
#define VOS3_PIT_CHANNEL0       ((uint16_t)0x40U)
#define VOS3_PIT_CHANNEL1       ((uint16_t)0x41U)
#define VOS3_PIT_CHANNEL2       ((uint16_t)0x42U)
#define VOS3_PIT_COMMAND        ((uint16_t)0x43U)

/** @brief PIT command bits */
#define VOS3_PIT_CMD_CHANNEL0   ((uint8_t)0x00U)
#define VOS3_PIT_CMD_LOHI       ((uint8_t)0x30U)    /* Low byte then high byte */
#define VOS3_PIT_CMD_SQUARE     ((uint8_t)0x06U)    /* Square wave mode */
#define VOS3_PIT_CMD_RATE       ((uint8_t)0x04U)    /* Rate generator mode */

/** @brief Default timer frequency (100 Hz = 10ms ticks) */
#define VOS3_DEFAULT_TIMER_FREQ ((uint32_t)100U)

/* ============================================================================
 * TIMER STATISTICS
 * ============================================================================ */

/**
 * @brief Timer statistics
 */
typedef struct vos3_timer_stats {
    uint64_t total_ticks;       /**< Total ticks since boot */
    uint64_t total_interrupts;  /**< Total timer interrupts */
    uint32_t frequency;         /**< Current frequency (Hz) */
    uint32_t divisor;           /**< PIT divisor value */
} vos3_timer_stats_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the timer
 * @param[in] frequency Desired frequency in Hz
 * @return 0 on success, negative error code on failure
 */
int vos3_timer_init(uint32_t frequency);

/**
 * @brief Set timer frequency
 * @param[in] frequency New frequency in Hz
 * @return 0 on success, negative error code on failure
 */
int vos3_timer_set_frequency(uint32_t frequency);

/**
 * @brief Get current tick count
 * @return Ticks since boot
 */
uint64_t vos3_timer_get_ticks(void);

/**
 * @brief Get uptime in milliseconds
 * @return Milliseconds since boot
 */
uint64_t vos3_timer_get_uptime_ms(void);

/**
 * @brief Get uptime in seconds
 * @return Seconds since boot
 */
uint64_t vos3_timer_get_uptime_sec(void);

/**
 * @brief Get timer statistics
 * @param[out] stats Statistics output
 */
void vos3_timer_get_stats(vos3_timer_stats_t* stats);

/**
 * @brief Timer interrupt handler
 * @note Called from IRQ0 handler
 */
void vos3_timer_irq_handler(void);

/**
 * @brief Busy-wait delay in milliseconds
 * @param[in] ms Milliseconds to wait
 * @note Should only be used during early boot
 */
void vos3_timer_delay_ms(uint32_t ms);

/**
 * @brief Busy-wait delay in microseconds
 * @param[in] us Microseconds to wait
 * @note Should only be used during early boot
 */
void vos3_timer_delay_us(uint32_t us);

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_TIMER_OK           (0)
#define VOS3_TIMER_ERR_INVALID  (-1)
#define VOS3_TIMER_ERR_RANGE    (-2)

#ifdef __cplusplus
}
#endif

#endif /* VOS3_TIMER_H */
