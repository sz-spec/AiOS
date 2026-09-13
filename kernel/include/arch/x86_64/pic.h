/**
 * @file pic.h
 * @brief VOS3 8259 PIC (Programmable Interrupt Controller) Driver
 *
 * @details Driver for the legacy 8259 PIC. Used for handling hardware
 *          IRQs before APIC initialization.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_ARCH_X86_64_PIC_H
#define VOS3_ARCH_X86_64_PIC_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/* ============================================================================
 * PIC PORTS
 * ============================================================================ */

/** @brief PIC1 (Master) command port */
#define VOS3_PIC1_CMD       ((uint16_t)0x20U)

/** @brief PIC1 (Master) data port */
#define VOS3_PIC1_DATA      ((uint16_t)0x21U)

/** @brief PIC2 (Slave) command port */
#define VOS3_PIC2_CMD       ((uint16_t)0xA0U)

/** @brief PIC2 (Slave) data port */
#define VOS3_PIC2_DATA      ((uint16_t)0xA1U)

/* ============================================================================
 * PIC COMMANDS
 * ============================================================================ */

/** @brief End of Interrupt command */
#define VOS3_PIC_EOI        ((uint8_t)0x20U)

/** @brief Read IRR (Interrupt Request Register) */
#define VOS3_PIC_READ_IRR   ((uint8_t)0x0AU)

/** @brief Read ISR (In-Service Register) */
#define VOS3_PIC_READ_ISR   ((uint8_t)0x0BU)

/* ============================================================================
 * ICW (Initialization Command Words)
 * ============================================================================ */

/** @brief ICW1: Initialization, expect ICW4 */
#define VOS3_ICW1_INIT      ((uint8_t)0x10U)
#define VOS3_ICW1_ICW4      ((uint8_t)0x01U)

/** @brief ICW4: 8086 mode */
#define VOS3_ICW4_8086      ((uint8_t)0x01U)

/* ============================================================================
 * IRQ VECTOR OFFSETS
 * ============================================================================ */

/** @brief Vector offset for PIC1 IRQs (IRQ0-7) */
#define VOS3_PIC1_OFFSET    ((uint8_t)32U)

/** @brief Vector offset for PIC2 IRQs (IRQ8-15) */
#define VOS3_PIC2_OFFSET    ((uint8_t)40U)

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the 8259 PIC
 * @note Remaps IRQs to vectors 32-47
 */
void vos3_pic_init(void);

/**
 * @brief Send End-of-Interrupt to PIC
 * @param[in] irq IRQ number (0-15)
 */
void vos3_pic_eoi(uint8_t irq);

/**
 * @brief Disable (mask) an IRQ
 * @param[in] irq IRQ number (0-15)
 */
void vos3_pic_mask_irq(uint8_t irq);

/**
 * @brief Enable (unmask) an IRQ
 * @param[in] irq IRQ number (0-15)
 */
void vos3_pic_unmask_irq(uint8_t irq);

/**
 * @brief Disable all IRQs
 */
void vos3_pic_disable_all(void);

/**
 * @brief Disable the PIC (for APIC usage)
 * @note Masks all IRQs on both PICs
 */
void vos3_pic_disable(void);

/**
 * @brief Get the IRR (Interrupt Request Register)
 * @return Combined IRR from both PICs (PIC2 in high byte)
 */
uint16_t vos3_pic_get_irr(void);

/**
 * @brief Get the ISR (In-Service Register)
 * @return Combined ISR from both PICs (PIC2 in high byte)
 */
uint16_t vos3_pic_get_isr(void);

/**
 * @brief Check if IRQ is spurious
 * @param[in] irq IRQ number (7 or 15 for spurious)
 * @return 1 if spurious, 0 otherwise
 */
int vos3_pic_is_spurious(uint8_t irq);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ARCH_X86_64_PIC_H */
