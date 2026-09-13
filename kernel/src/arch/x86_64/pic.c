/**
 * @file pic.c
 * @brief VOS3 8259 PIC Driver Implementation
 *
 * @details Implements the 8259 PIC driver for legacy IRQ handling.
 *          Remaps IRQs to vectors 32-47 to avoid conflicts with
 *          CPU exceptions.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../../include/arch/x86_64/pic.h"
#include "../../../include/vos/console.h"

/* ============================================================================
 * PORT I/O HELPERS
 * ============================================================================ */

/**
 * @brief Read byte from I/O port
 */
static inline uint8_t port_inb(uint16_t port)
{
    uint8_t value;
    __asm__ volatile ("inb %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}

/**
 * @brief Write byte to I/O port
 */
static inline void port_outb(uint16_t port, uint8_t value)
{
    __asm__ volatile ("outb %0, %1" :: "a"(value), "Nd"(port));
}

/**
 * @brief I/O wait (for PIC timing)
 */
static inline void io_wait(void)
{
    /* Port 0x80 is used for POST codes, safe for delay */
    __asm__ volatile ("outb %%al, $0x80" ::: "memory");
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

void vos3_pic_init(void)
{
    VOS3_INFO("PIC: Initializing 8259 PIC");

    /* Save current masks */
    uint8_t mask1 = port_inb(VOS3_PIC1_DATA);
    uint8_t mask2 = port_inb(VOS3_PIC2_DATA);

    /* Start initialization sequence (cascade mode) */
    port_outb(VOS3_PIC1_CMD, VOS3_ICW1_INIT | VOS3_ICW1_ICW4);
    io_wait();
    port_outb(VOS3_PIC2_CMD, VOS3_ICW1_INIT | VOS3_ICW1_ICW4);
    io_wait();

    /* ICW2: Set vector offsets */
    port_outb(VOS3_PIC1_DATA, VOS3_PIC1_OFFSET);  /* IRQ0-7 -> 32-39 */
    io_wait();
    port_outb(VOS3_PIC2_DATA, VOS3_PIC2_OFFSET);  /* IRQ8-15 -> 40-47 */
    io_wait();

    /* ICW3: Tell Master PIC that Slave is at IRQ2 (0000 0100) */
    port_outb(VOS3_PIC1_DATA, 0x04U);
    io_wait();
    /* ICW3: Tell Slave PIC its cascade identity (0000 0010) */
    port_outb(VOS3_PIC2_DATA, 0x02U);
    io_wait();

    /* ICW4: 8086 mode */
    port_outb(VOS3_PIC1_DATA, VOS3_ICW4_8086);
    io_wait();
    port_outb(VOS3_PIC2_DATA, VOS3_ICW4_8086);
    io_wait();

    /* Restore saved masks */
    port_outb(VOS3_PIC1_DATA, mask1);
    port_outb(VOS3_PIC2_DATA, mask2);

    VOS3_INFO("PIC: Remapped IRQs to vectors %u-%u",
              VOS3_PIC1_OFFSET, VOS3_PIC2_OFFSET + 7U);
}

void vos3_pic_eoi(uint8_t irq)
{
    /* If IRQ came from Slave PIC, send EOI to both */
    if (irq >= 8U) {
        port_outb(VOS3_PIC2_CMD, VOS3_PIC_EOI);
    }
    /* Always send EOI to Master */
    port_outb(VOS3_PIC1_CMD, VOS3_PIC_EOI);
}

void vos3_pic_mask_irq(uint8_t irq)
{
    uint16_t port;
    uint8_t value;

    if (irq < 8U) {
        port = VOS3_PIC1_DATA;
    } else {
        port = VOS3_PIC2_DATA;
        irq -= 8U;
    }

    value = port_inb(port) | (uint8_t)(1U << irq);
    port_outb(port, value);
}

void vos3_pic_unmask_irq(uint8_t irq)
{
    uint16_t port;
    uint8_t value;

    if (irq < 8U) {
        port = VOS3_PIC1_DATA;
    } else {
        port = VOS3_PIC2_DATA;
        irq -= 8U;
    }

    value = port_inb(port) & (uint8_t)(~(1U << irq));
    port_outb(port, value);
}

void vos3_pic_disable_all(void)
{
    /* Mask all IRQs except cascade (IRQ2) */
    port_outb(VOS3_PIC1_DATA, 0xFBU);  /* 1111 1011 - keep IRQ2 for cascade */
    port_outb(VOS3_PIC2_DATA, 0xFFU);  /* 1111 1111 - mask all slave */
}

void vos3_pic_disable(void)
{
    /* Mask all IRQs on both PICs */
    port_outb(VOS3_PIC1_DATA, 0xFFU);
    port_outb(VOS3_PIC2_DATA, 0xFFU);

    VOS3_INFO("PIC: Disabled (all IRQs masked)");
}

uint16_t vos3_pic_get_irr(void)
{
    port_outb(VOS3_PIC1_CMD, VOS3_PIC_READ_IRR);
    port_outb(VOS3_PIC2_CMD, VOS3_PIC_READ_IRR);
    return (uint16_t)(((uint16_t)port_inb(VOS3_PIC2_CMD) << 8U) |
                       (uint16_t)port_inb(VOS3_PIC1_CMD));
}

uint16_t vos3_pic_get_isr(void)
{
    port_outb(VOS3_PIC1_CMD, VOS3_PIC_READ_ISR);
    port_outb(VOS3_PIC2_CMD, VOS3_PIC_READ_ISR);
    return (uint16_t)(((uint16_t)port_inb(VOS3_PIC2_CMD) << 8U) |
                       (uint16_t)port_inb(VOS3_PIC1_CMD));
}

int vos3_pic_is_spurious(uint8_t irq)
{
    uint16_t isr;

    if (irq == 7U) {
        /* Check if IRQ7 is real by reading ISR */
        port_outb(VOS3_PIC1_CMD, VOS3_PIC_READ_ISR);
        isr = port_inb(VOS3_PIC1_CMD);
        if ((isr & 0x80U) == 0U) {
            /* Bit 7 not set = spurious */
            return 1;
        }
    } else if (irq == 15U) {
        /* Check if IRQ15 is real by reading ISR */
        port_outb(VOS3_PIC2_CMD, VOS3_PIC_READ_ISR);
        isr = port_inb(VOS3_PIC2_CMD);
        if ((isr & 0x80U) == 0U) {
            /* Bit 7 not set = spurious, but still need to EOI master */
            port_outb(VOS3_PIC1_CMD, VOS3_PIC_EOI);
            return 1;
        }
    }

    return 0;
}
