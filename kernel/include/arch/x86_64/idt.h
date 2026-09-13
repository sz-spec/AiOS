/**
 * @file idt.h
 * @brief VOS3 Interrupt Descriptor Table (IDT) for x86_64
 *
 * @details Defines the IDT structure and interrupt gate descriptors for the
 *          VOS3 kernel. Supports all 256 interrupt vectors with IST support.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_ARCH_X86_64_IDT_H
#define VOS3_ARCH_X86_64_IDT_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include "memory_map.h"
#include "gdt.h"

/* ============================================================================
 * INTERRUPT VECTOR DEFINITIONS
 * ============================================================================ */

/** @brief Total number of IDT entries (0-255) */
#define VOS3_IDT_ENTRIES            ((size_t)256U)

/* CPU Exceptions (0-31) */
#define VOS3_INT_DIVIDE_ERROR       ((uint8_t)0U)   /**< #DE - Divide Error */
#define VOS3_INT_DEBUG              ((uint8_t)1U)   /**< #DB - Debug Exception */
#define VOS3_INT_NMI                ((uint8_t)2U)   /**< NMI - Non-Maskable Interrupt */
#define VOS3_INT_BREAKPOINT         ((uint8_t)3U)   /**< #BP - Breakpoint */
#define VOS3_INT_OVERFLOW           ((uint8_t)4U)   /**< #OF - Overflow */
#define VOS3_INT_BOUND_RANGE        ((uint8_t)5U)   /**< #BR - Bound Range Exceeded */
#define VOS3_INT_INVALID_OPCODE     ((uint8_t)6U)   /**< #UD - Invalid Opcode */
#define VOS3_INT_DEVICE_NA          ((uint8_t)7U)   /**< #NM - Device Not Available */
#define VOS3_INT_DOUBLE_FAULT       ((uint8_t)8U)   /**< #DF - Double Fault */
#define VOS3_INT_COPROC_SEG         ((uint8_t)9U)   /**< Coprocessor Segment Overrun */
#define VOS3_INT_INVALID_TSS        ((uint8_t)10U)  /**< #TS - Invalid TSS */
#define VOS3_INT_SEG_NOT_PRESENT    ((uint8_t)11U)  /**< #NP - Segment Not Present */
#define VOS3_INT_STACK_FAULT        ((uint8_t)12U)  /**< #SS - Stack-Segment Fault */
#define VOS3_INT_GENERAL_PROT       ((uint8_t)13U)  /**< #GP - General Protection Fault */
#define VOS3_INT_PAGE_FAULT         ((uint8_t)14U)  /**< #PF - Page Fault */
#define VOS3_INT_RESERVED_15        ((uint8_t)15U)  /**< Reserved */
#define VOS3_INT_FPU_ERROR          ((uint8_t)16U)  /**< #MF - x87 FPU Error */
#define VOS3_INT_ALIGNMENT          ((uint8_t)17U)  /**< #AC - Alignment Check */
#define VOS3_INT_MACHINE_CHECK      ((uint8_t)18U)  /**< #MC - Machine Check */
#define VOS3_INT_SIMD_ERROR         ((uint8_t)19U)  /**< #XM - SIMD Exception */
#define VOS3_INT_VIRT_ERROR         ((uint8_t)20U)  /**< #VE - Virtualization Exception */
#define VOS3_INT_CONTROL_PROT       ((uint8_t)21U)  /**< #CP - Control Protection */
/* 22-27 Reserved */
#define VOS3_INT_HYPERVISOR         ((uint8_t)28U)  /**< Hypervisor Injection */
#define VOS3_INT_VMM_COMM           ((uint8_t)29U)  /**< VMM Communication */
#define VOS3_INT_SECURITY           ((uint8_t)30U)  /**< Security Exception */
/* 31 Reserved */

/* Hardware IRQs (remapped to 32-47) */
#define VOS3_IRQ_BASE               ((uint8_t)32U)
#define VOS3_IRQ_TIMER              ((uint8_t)32U)  /**< IRQ0 - PIT Timer */
#define VOS3_IRQ_KEYBOARD           ((uint8_t)33U)  /**< IRQ1 - Keyboard */
#define VOS3_IRQ_CASCADE            ((uint8_t)34U)  /**< IRQ2 - Cascade */
#define VOS3_IRQ_COM2               ((uint8_t)35U)  /**< IRQ3 - COM2 */
#define VOS3_IRQ_COM1               ((uint8_t)36U)  /**< IRQ4 - COM1 */
#define VOS3_IRQ_LPT2               ((uint8_t)37U)  /**< IRQ5 - LPT2 */
#define VOS3_IRQ_FLOPPY             ((uint8_t)38U)  /**< IRQ6 - Floppy */
#define VOS3_IRQ_LPT1               ((uint8_t)39U)  /**< IRQ7 - LPT1 */
#define VOS3_IRQ_RTC                ((uint8_t)40U)  /**< IRQ8 - RTC */
#define VOS3_IRQ_ACPI               ((uint8_t)41U)  /**< IRQ9 - ACPI */
#define VOS3_IRQ_AVAILABLE1         ((uint8_t)42U)  /**< IRQ10 - Available */
#define VOS3_IRQ_AVAILABLE2         ((uint8_t)43U)  /**< IRQ11 - Available */
#define VOS3_IRQ_MOUSE              ((uint8_t)44U)  /**< IRQ12 - PS/2 Mouse */
#define VOS3_IRQ_COPROC             ((uint8_t)45U)  /**< IRQ13 - Coprocessor */
#define VOS3_IRQ_ATA_PRIMARY        ((uint8_t)46U)  /**< IRQ14 - ATA Primary */
#define VOS3_IRQ_ATA_SECONDARY      ((uint8_t)47U)  /**< IRQ15 - ATA Secondary */

/* APIC Interrupts */
#define VOS3_INT_APIC_SPURIOUS      ((uint8_t)0xFFU)  /**< APIC Spurious */
#define VOS3_INT_APIC_ERROR         ((uint8_t)0xFEU)  /**< APIC Error */
#define VOS3_INT_APIC_TIMER         ((uint8_t)0xFDU)  /**< APIC Timer */
#define VOS3_INT_IPI_SCHEDULE       ((uint8_t)0xFCU)  /**< IPI: Reschedule */
#define VOS3_INT_IPI_TLB_FLUSH      ((uint8_t)0xFBU)  /**< IPI: TLB Flush */
#define VOS3_INT_IPI_HALT           ((uint8_t)0xFAU)  /**< IPI: Halt CPU */

/* System Call */
#define VOS3_INT_SYSCALL            ((uint8_t)0x80U)  /**< System call interrupt */

/* ============================================================================
 * IDT GATE TYPES
 * ============================================================================ */

/** @brief Interrupt Gate (64-bit) - clears IF */
#define VOS3_IDT_GATE_INTERRUPT     ((uint8_t)0x8EU)

/** @brief Trap Gate (64-bit) - does not clear IF */
#define VOS3_IDT_GATE_TRAP          ((uint8_t)0x8FU)

/** @brief Call Gate (64-bit) */
#define VOS3_IDT_GATE_CALL          ((uint8_t)0x8CU)

/** @brief Gate present flag */
#define VOS3_IDT_PRESENT            ((uint8_t)0x80U)

/** @brief DPL 0 (Ring 0 only) */
#define VOS3_IDT_DPL0               ((uint8_t)0x00U)

/** @brief DPL 3 (User accessible) */
#define VOS3_IDT_DPL3               ((uint8_t)0x60U)

/* ============================================================================
 * INTERRUPT STACK TABLE (IST) ASSIGNMENTS
 * ============================================================================
 *
 * IST 1: Double Fault (#DF) - Dedicated stack for nested faults
 * IST 2: NMI - Non-Maskable Interrupt
 * IST 3: Machine Check (#MC)
 * IST 4: Debug exceptions (#DB, #BP)
 * IST 5-7: Reserved for future use
 *
 * ============================================================================ */

/** @brief IST index for Double Fault */
#define VOS3_IST_DOUBLE_FAULT       ((uint8_t)1U)

/** @brief IST index for NMI */
#define VOS3_IST_NMI                ((uint8_t)2U)

/** @brief IST index for Machine Check */
#define VOS3_IST_MACHINE_CHECK      ((uint8_t)3U)

/** @brief IST index for Debug */
#define VOS3_IST_DEBUG              ((uint8_t)4U)

/** @brief IST stack size (16 KiB per stack) */
#define VOS3_IST_STACK_SIZE         ((size_t)0x4000U)

/* ============================================================================
 * IDT STRUCTURES
 * ============================================================================ */

/**
 * @brief IDT Gate Descriptor (16 bytes for 64-bit mode)
 */
typedef struct __attribute__((packed)) vos3_idt_entry {
    uint16_t offset_low;    /**< Handler offset (bits 0-15) */
    uint16_t selector;      /**< Code segment selector */
    uint8_t  ist;           /**< IST index (bits 0-2), reserved (bits 3-7) */
    uint8_t  type_attr;     /**< Type and attributes */
    uint16_t offset_mid;    /**< Handler offset (bits 16-31) */
    uint32_t offset_high;   /**< Handler offset (bits 32-63) */
    uint32_t reserved;      /**< Reserved, must be zero */
} vos3_idt_entry_t;

VOS3_STATIC_ASSERT(sizeof(vos3_idt_entry_t) == 16U,
                   "IDT entry must be 16 bytes");

/**
 * @brief IDT Pointer structure for LIDT instruction
 */
typedef struct __attribute__((packed)) vos3_idt_ptr {
    uint16_t limit;     /**< IDT size - 1 */
    uint64_t base;      /**< IDT base address */
} vos3_idt_ptr_t;

VOS3_STATIC_ASSERT(sizeof(vos3_idt_ptr_t) == 10U,
                   "IDT pointer must be 10 bytes");

/**
 * @brief Interrupt frame pushed by CPU
 * @note Layout when interrupt occurs from Ring 3
 */
typedef struct __attribute__((packed)) vos3_int_frame {
    /* Pushed by handler stub */
    uint64_t r15;
    uint64_t r14;
    uint64_t r13;
    uint64_t r12;
    uint64_t r11;
    uint64_t r10;
    uint64_t r9;
    uint64_t r8;
    uint64_t rbp;
    uint64_t rdi;
    uint64_t rsi;
    uint64_t rdx;
    uint64_t rcx;
    uint64_t rbx;
    uint64_t rax;

    /* Interrupt number and error code */
    uint64_t int_num;       /**< Interrupt vector number */
    uint64_t error_code;    /**< Error code (or 0 if none) */

    /* Pushed by CPU */
    uint64_t rip;           /**< Return instruction pointer */
    uint64_t cs;            /**< Code segment */
    uint64_t rflags;        /**< CPU flags */
    uint64_t rsp;           /**< Stack pointer (if privilege change) */
    uint64_t ss;            /**< Stack segment (if privilege change) */
} vos3_int_frame_t;

/**
 * @brief Exception frame with error code information
 */
typedef struct vos3_exception_info {
    uint64_t vector;        /**< Exception vector number */
    uint64_t error_code;    /**< Error code */
    uint64_t cr2;           /**< CR2 (for page faults) */
    uint64_t rip;           /**< Faulting instruction */
    uint64_t rsp;           /**< Stack at fault */
    uint64_t rbp;           /**< Frame pointer */
} vos3_exception_info_t;

/* ============================================================================
 * INTERRUPT HANDLER TYPES
 * ============================================================================ */

/**
 * @brief Interrupt handler function type
 * @param[in,out] frame Pointer to interrupt frame
 */
typedef void (*vos3_int_handler_t)(vos3_int_frame_t* frame);

/**
 * @brief Exception handler function type
 * @param[in,out] frame Pointer to interrupt frame
 * @param[in] info Additional exception information
 */
typedef void (*vos3_exc_handler_t)(vos3_int_frame_t* frame,
                                    const vos3_exception_info_t* info);

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize the Interrupt Descriptor Table
 * @note Must be called by BSP during early boot
 */
void vos3_idt_init(void);

/**
 * @brief Load the IDT on the current CPU
 * @param[in] idt_ptr Pointer to IDT pointer structure
 */
void vos3_idt_load(const vos3_idt_ptr_t* idt_ptr);

/**
 * @brief Load shared IDT on an Application Processor
 * @note v23.12: Exposes BSP's static g_idt_ptr for AP IDTR loading
 */
void vos3_idt_load_ap(void);

/**
 * @brief Set an IDT entry
 * @param[in] vector Interrupt vector number (0-255)
 * @param[in] handler Handler function address
 * @param[in] selector Code segment selector
 * @param[in] type_attr Type and attribute flags
 * @param[in] ist IST index (0 = no IST, 1-7 = IST entry)
 */
void vos3_idt_set_entry(uint8_t vector,
                        uintptr_t handler,
                        uint16_t selector,
                        uint8_t type_attr,
                        uint8_t ist);

/**
 * @brief Register an interrupt handler
 * @param[in] vector Interrupt vector number
 * @param[in] handler Handler function
 * @return 0 on success, negative error code on failure
 */
int vos3_int_register(uint8_t vector, vos3_int_handler_t handler);

/**
 * @brief Unregister an interrupt handler
 * @param[in] vector Interrupt vector number
 * @return 0 on success, negative error code on failure
 */
int vos3_int_unregister(uint8_t vector);

/**
 * @brief Enable interrupts (STI)
 */
static inline void vos3_int_enable(void)
{
    __asm__ volatile ("sti" ::: "memory");
}

/**
 * @brief Disable interrupts (CLI)
 */
static inline void vos3_int_disable(void)
{
    __asm__ volatile ("cli" ::: "memory");
}

/**
 * @brief Save interrupt state and disable
 * @return Previous interrupt state (RFLAGS)
 */
static inline uint64_t vos3_int_save_disable(void)
{
    uint64_t flags;
    __asm__ volatile (
        "pushfq\n\t"
        "cli\n\t"
        "popq %0"
        : "=r" (flags)
        :
        : "memory"
    );
    return flags;
}

/**
 * @brief Restore interrupt state
 * @param[in] flags Previous RFLAGS value
 */
static inline void vos3_int_restore(uint64_t flags)
{
    __asm__ volatile (
        "pushq %0\n\t"
        "popfq"
        :
        : "r" (flags)
        : "memory", "cc"
    );
}

/**
 * @brief Check if interrupts are enabled
 * @return 1 if enabled, 0 if disabled
 */
static inline int vos3_int_enabled(void)
{
    uint64_t flags;
    __asm__ volatile ("pushfq; popq %0" : "=r" (flags));
    return (flags & 0x200U) ? 1 : 0;
}

/**
 * @brief Create an IDT entry
 * @param[in] handler Handler address
 * @param[in] selector Code segment selector
 * @param[in] ist IST index
 * @param[in] type_attr Type and attributes
 * @return Configured IDT entry
 */
static inline vos3_idt_entry_t vos3_idt_create_entry(
    uint64_t handler,
    uint16_t selector,
    uint8_t ist,
    uint8_t type_attr)
{
    vos3_idt_entry_t entry;

    entry.offset_low  = (uint16_t)(handler & 0xFFFFU);
    entry.selector    = selector;
    entry.ist         = ist & 0x07U;
    entry.type_attr   = type_attr;
    entry.offset_mid  = (uint16_t)((handler >> 16U) & 0xFFFFU);
    entry.offset_high = (uint32_t)((handler >> 32U) & 0xFFFFFFFFU);
    entry.reserved    = 0U;

    return entry;
}

/* ============================================================================
 * EXCEPTION MACROS
 * ============================================================================ */

/**
 * @brief Check if vector has error code
 * @param[in] vec Interrupt vector number
 * @return 1 if error code present, 0 otherwise
 */
#define VOS3_INT_HAS_ERROR_CODE(vec) \
    (((vec) == 8U) || ((vec) >= 10U && (vec) <= 14U) || \
     ((vec) == 17U) || ((vec) == 21U) || ((vec) == 29U) || ((vec) == 30U))

#ifdef __cplusplus
}
#endif

#endif /* VOS3_ARCH_X86_64_IDT_H */
