/**
 * @file timer.c
 * @brief VOS3 Timer (PIT) Driver
 *
 * @details Programmable Interval Timer driver for x86_64.
 *          Provides system tick and preemption support.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/timer.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/console.h"
#include "../../include/arch/x86_64/cpu.h"
#include "../../include/arch/x86_64/idt.h"

/* ============================================================================
 * I/O PORT ACCESS
 * ============================================================================ */

static inline void outb(uint16_t port, uint8_t value)
{
    __asm__ volatile ("outb %0, %1" : : "a"(value), "Nd"(port));
}

static inline uint8_t inb(uint16_t port)
{
    uint8_t value;
    __asm__ volatile ("inb %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}

/* ============================================================================
 * TIMER STATE
 * ============================================================================ */

/** @brief Timer initialized flag */
static int g_timer_initialized = 0;

/** @brief Current timer frequency */
static uint32_t g_timer_frequency = 0U;

/** @brief PIT divisor */
static uint32_t g_pit_divisor = 0U;

/** @brief Tick counter */
static volatile uint64_t g_tick_count = 0ULL;

/** @brief Interrupt counter */
static volatile uint64_t g_interrupt_count = 0ULL;

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

int vos3_timer_init(uint32_t frequency)
{
    if (frequency == 0U || frequency > VOS3_PIT_FREQUENCY) {
        return VOS3_TIMER_ERR_RANGE;
    }

    /* Calculate divisor */
    uint32_t divisor = VOS3_PIT_FREQUENCY / frequency;

    /* Clamp to valid range */
    if (divisor < 1U) {
        divisor = 1U;
    }
    if (divisor > 65535U) {
        divisor = 65535U;
    }

    g_pit_divisor = divisor;
    g_timer_frequency = VOS3_PIT_FREQUENCY / divisor;

    /* Configure PIT Channel 0 */
    /* Command: Channel 0, Access mode lo/hi, Mode 2 (rate generator) */
    uint8_t command = VOS3_PIT_CMD_CHANNEL0 | VOS3_PIT_CMD_LOHI | VOS3_PIT_CMD_RATE;
    outb(VOS3_PIT_COMMAND, command);

    /* Send divisor (low byte first, then high byte) */
    outb(VOS3_PIT_CHANNEL0, (uint8_t)(divisor & 0xFFU));
    outb(VOS3_PIT_CHANNEL0, (uint8_t)((divisor >> 8) & 0xFFU));

    g_tick_count = 0ULL;
    g_interrupt_count = 0ULL;
    g_timer_initialized = 1;

    VOS3_INFO("Timer initialized: %u Hz (divisor %u)",
              g_timer_frequency, g_pit_divisor);

    return VOS3_TIMER_OK;
}

int vos3_timer_set_frequency(uint32_t frequency)
{
    if (g_timer_initialized == 0) {
        return VOS3_TIMER_ERR_INVALID;
    }

    if (frequency == 0U || frequency > VOS3_PIT_FREQUENCY) {
        return VOS3_TIMER_ERR_RANGE;
    }

    uint32_t divisor = VOS3_PIT_FREQUENCY / frequency;
    if (divisor < 1U) {
        divisor = 1U;
    }
    if (divisor > 65535U) {
        divisor = 65535U;
    }

    g_pit_divisor = divisor;
    g_timer_frequency = VOS3_PIT_FREQUENCY / divisor;

    /* Disable interrupts while reprogramming */
    vos3_int_disable();

    uint8_t command = VOS3_PIT_CMD_CHANNEL0 | VOS3_PIT_CMD_LOHI | VOS3_PIT_CMD_RATE;
    outb(VOS3_PIT_COMMAND, command);
    outb(VOS3_PIT_CHANNEL0, (uint8_t)(divisor & 0xFFU));
    outb(VOS3_PIT_CHANNEL0, (uint8_t)((divisor >> 8) & 0xFFU));

    vos3_int_enable();

    VOS3_DEBUG("Timer frequency changed to %u Hz", g_timer_frequency);

    return VOS3_TIMER_OK;
}

uint64_t vos3_timer_get_ticks(void)
{
    return g_tick_count;
}

uint64_t vos3_timer_get_uptime_ms(void)
{
    if (g_timer_frequency == 0U) {
        return 0ULL;
    }
    return (g_tick_count * 1000ULL) / (uint64_t)g_timer_frequency;
}

uint64_t vos3_timer_get_uptime_sec(void)
{
    if (g_timer_frequency == 0U) {
        return 0ULL;
    }
    return g_tick_count / (uint64_t)g_timer_frequency;
}

void vos3_timer_get_stats(vos3_timer_stats_t* stats)
{
    if (stats == NULL) {
        return;
    }

    stats->total_ticks = g_tick_count;
    stats->total_interrupts = g_interrupt_count;
    stats->frequency = g_timer_frequency;
    stats->divisor = g_pit_divisor;
}

void vos3_timer_irq_handler(void)
{
    g_tick_count++;
    g_interrupt_count++;

    /* Notify scheduler */
    if (vos3_sched_is_running() != 0) {
        vos3_sched_tick();
    }
}

void vos3_timer_delay_ms(uint32_t ms)
{
    if (g_timer_frequency == 0U) {
        /* Fallback: crude delay loop */
        for (uint32_t i = 0U; i < ms * 1000U; i++) {
            __asm__ volatile ("pause");
        }
        return;
    }

    uint64_t target_ticks = (uint64_t)ms * (uint64_t)g_timer_frequency / 1000ULL;
    uint64_t start = g_tick_count;

    while ((g_tick_count - start) < target_ticks) {
        __asm__ volatile ("pause");
    }
}

void vos3_timer_delay_us(uint32_t us)
{
    /* For microsecond delays, use busy loop since PIT resolution is ~1ms */
    /* This is calibrated for ~1GHz CPU, adjust as needed */
    for (uint32_t i = 0U; i < us * 100U; i++) {
        __asm__ volatile ("pause");
    }
}
