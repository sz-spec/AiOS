/**
 * @file idt.c
 * @brief VOS3 Interrupt Descriptor Table Implementation
 *
 * @details Initializes and manages the IDT for x86_64.
 *          Supports all 256 interrupt vectors with IST.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../../include/arch/x86_64/idt.h"
#include "../../../include/arch/x86_64/gdt.h"
#include "../../../include/vos/console.h"

/* ============================================================================
 * EXTERNAL ISR STUBS (defined in isr_stubs.S)
 * ============================================================================ */

/* CPU Exceptions (0-31) */
extern void isr_stub_0(void);
extern void isr_stub_1(void);
extern void isr_stub_2(void);
extern void isr_stub_3(void);
extern void isr_stub_4(void);
extern void isr_stub_5(void);
extern void isr_stub_6(void);
extern void isr_stub_7(void);
extern void isr_stub_8(void);
extern void isr_stub_9(void);
extern void isr_stub_10(void);
extern void isr_stub_11(void);
extern void isr_stub_12(void);
extern void isr_stub_13(void);
extern void isr_stub_14(void);
extern void isr_stub_15(void);
extern void isr_stub_16(void);
extern void isr_stub_17(void);
extern void isr_stub_18(void);
extern void isr_stub_19(void);
extern void isr_stub_20(void);
extern void isr_stub_21(void);
extern void isr_stub_22(void);
extern void isr_stub_23(void);
extern void isr_stub_24(void);
extern void isr_stub_25(void);
extern void isr_stub_26(void);
extern void isr_stub_27(void);
extern void isr_stub_28(void);
extern void isr_stub_29(void);
extern void isr_stub_30(void);
extern void isr_stub_31(void);

/* IRQs (32-47) */
extern void isr_stub_32(void);
extern void isr_stub_33(void);
extern void isr_stub_34(void);
extern void isr_stub_35(void);
extern void isr_stub_36(void);
extern void isr_stub_37(void);
extern void isr_stub_38(void);
extern void isr_stub_39(void);
extern void isr_stub_40(void);
extern void isr_stub_41(void);
extern void isr_stub_42(void);
extern void isr_stub_43(void);
extern void isr_stub_44(void);
extern void isr_stub_45(void);
extern void isr_stub_46(void);
extern void isr_stub_47(void);

/* Syscall */
extern void isr_stub_128(void);

/* APIC interrupts */
extern void isr_stub_250(void);  /* IPI Halt */
extern void isr_stub_251(void);  /* IPI TLB Flush */
extern void isr_stub_252(void);  /* IPI Schedule */
extern void isr_stub_253(void);  /* APIC Timer */
extern void isr_stub_254(void);  /* APIC Error */
extern void isr_stub_255(void);  /* APIC Spurious */

/* ============================================================================
 * STATIC DATA
 * ============================================================================ */

/** @brief Interrupt Descriptor Table */
static vos3_idt_entry_t g_idt[VOS3_IDT_ENTRIES]
    __attribute__((aligned(16)));

/** @brief IDT pointer for LIDT instruction */
static vos3_idt_ptr_t g_idt_ptr __attribute__((aligned(8)));

/** @brief Registered interrupt handlers */
static vos3_int_handler_t g_int_handlers[VOS3_IDT_ENTRIES];

/** @brief IDT initialization flag */
static volatile uint32_t g_idt_initialized = 0U;

/** @brief ISR stub table */
typedef void (*isr_stub_fn)(void);

static const isr_stub_fn g_isr_stubs[] = {
    /* CPU Exceptions (0-31) */
    isr_stub_0,  isr_stub_1,  isr_stub_2,  isr_stub_3,
    isr_stub_4,  isr_stub_5,  isr_stub_6,  isr_stub_7,
    isr_stub_8,  isr_stub_9,  isr_stub_10, isr_stub_11,
    isr_stub_12, isr_stub_13, isr_stub_14, isr_stub_15,
    isr_stub_16, isr_stub_17, isr_stub_18, isr_stub_19,
    isr_stub_20, isr_stub_21, isr_stub_22, isr_stub_23,
    isr_stub_24, isr_stub_25, isr_stub_26, isr_stub_27,
    isr_stub_28, isr_stub_29, isr_stub_30, isr_stub_31,
    /* IRQs (32-47) */
    isr_stub_32, isr_stub_33, isr_stub_34, isr_stub_35,
    isr_stub_36, isr_stub_37, isr_stub_38, isr_stub_39,
    isr_stub_40, isr_stub_41, isr_stub_42, isr_stub_43,
    isr_stub_44, isr_stub_45, isr_stub_46, isr_stub_47,
};

/* ============================================================================
 * ASSEMBLY HELPERS
 * ============================================================================ */

/**
 * @brief Load IDT using LIDT instruction
 */
void vos3_idt_load(const vos3_idt_ptr_t* idt_ptr)
{
    __asm__ volatile ("lidt (%0)" :: "r" (idt_ptr) : "memory");
}

/**
 * @brief Load shared IDT on an Application Processor
 * @note v23.12: APs share the BSP's IDT table but each CPU must load IDTR
 */
void vos3_idt_load_ap(void)
{
    vos3_idt_load(&g_idt_ptr);
    VOS3_DEBUG("IDT: AP loaded shared IDT at 0x%016llx",
               (unsigned long long)g_idt_ptr.base);
}

/* ============================================================================
 * INTERNAL FUNCTIONS
 * ============================================================================ */

/**
 * @brief Get IST index for exception vector
 * @param[in] vector Exception vector number
 * @return IST index (0 = no IST)
 */
static uint8_t get_ist_for_vector(uint8_t vector)
{
    switch (vector) {
        case VOS3_INT_DOUBLE_FAULT:
            return VOS3_IST_DOUBLE_FAULT;
        case VOS3_INT_NMI:
            return VOS3_IST_NMI;
        case VOS3_INT_MACHINE_CHECK:
            return VOS3_IST_MACHINE_CHECK;
        case VOS3_INT_DEBUG:
        case VOS3_INT_BREAKPOINT:
            return VOS3_IST_DEBUG;
        default:
            return 0U;
    }
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

void vos3_idt_set_entry(uint8_t vector,
                        uintptr_t handler,
                        uint16_t selector,
                        uint8_t type_attr,
                        uint8_t ist)
{
    g_idt[vector] = vos3_idt_create_entry(
        (uint64_t)handler,
        selector,
        ist,
        type_attr
    );
}

void vos3_idt_init(void)
{
    if (g_idt_initialized != 0U) {
        return;
    }

    VOS3_INFO("IDT: Initializing Interrupt Descriptor Table");

    /* Clear all IDT entries and handlers */
    for (size_t i = 0U; i < VOS3_IDT_ENTRIES; i++) {
        g_idt[i] = (vos3_idt_entry_t){0};
        g_int_handlers[i] = NULL;
    }

    /* Install CPU exception handlers (0-31) */
    for (uint8_t i = 0U; i < 32U; i++) {
        uint8_t ist = get_ist_for_vector(i);
        vos3_idt_set_entry(
            i,
            (uintptr_t)g_isr_stubs[i],
            VOS3_GDT_KERNEL_CODE,
            VOS3_IDT_GATE_INTERRUPT,
            ist
        );
    }

    /* Install IRQ handlers (32-47) */
    for (uint8_t i = 32U; i < 48U; i++) {
        vos3_idt_set_entry(
            i,
            (uintptr_t)g_isr_stubs[i],
            VOS3_GDT_KERNEL_CODE,
            VOS3_IDT_GATE_INTERRUPT,
            0U
        );
    }

    /* Install syscall handler (0x80) - DPL3 so user can call */
    vos3_idt_set_entry(
        VOS3_INT_SYSCALL,
        (uintptr_t)isr_stub_128,
        VOS3_GDT_KERNEL_CODE,
        VOS3_IDT_GATE_TRAP | VOS3_IDT_DPL3,
        0U
    );

    /* Install APIC interrupt handlers */
    vos3_idt_set_entry(
        VOS3_INT_IPI_HALT,
        (uintptr_t)isr_stub_250,
        VOS3_GDT_KERNEL_CODE,
        VOS3_IDT_GATE_INTERRUPT,
        0U
    );

    vos3_idt_set_entry(
        VOS3_INT_IPI_TLB_FLUSH,
        (uintptr_t)isr_stub_251,
        VOS3_GDT_KERNEL_CODE,
        VOS3_IDT_GATE_INTERRUPT,
        0U
    );

    vos3_idt_set_entry(
        VOS3_INT_IPI_SCHEDULE,
        (uintptr_t)isr_stub_252,
        VOS3_GDT_KERNEL_CODE,
        VOS3_IDT_GATE_INTERRUPT,
        0U
    );

    vos3_idt_set_entry(
        VOS3_INT_APIC_TIMER,
        (uintptr_t)isr_stub_253,
        VOS3_GDT_KERNEL_CODE,
        VOS3_IDT_GATE_INTERRUPT,
        0U
    );

    vos3_idt_set_entry(
        VOS3_INT_APIC_ERROR,
        (uintptr_t)isr_stub_254,
        VOS3_GDT_KERNEL_CODE,
        VOS3_IDT_GATE_INTERRUPT,
        0U
    );

    vos3_idt_set_entry(
        VOS3_INT_APIC_SPURIOUS,
        (uintptr_t)isr_stub_255,
        VOS3_GDT_KERNEL_CODE,
        VOS3_IDT_GATE_INTERRUPT,
        0U
    );

    /* Set up IDT pointer */
    g_idt_ptr.limit = (uint16_t)(sizeof(g_idt) - 1U);
    g_idt_ptr.base = (uint64_t)(uintptr_t)&g_idt[0];

    /* Load IDT */
    vos3_idt_load(&g_idt_ptr);

    g_idt_initialized = 1U;

    VOS3_INFO("IDT: Loaded at 0x%016llx, %u entries",
              (unsigned long long)g_idt_ptr.base,
              VOS3_IDT_ENTRIES);
}

int vos3_int_register(uint8_t vector, vos3_int_handler_t handler)
{
    if (handler == NULL) {
        return -1;
    }

    if (g_int_handlers[vector] != NULL) {
        VOS3_WARN("IDT: Handler already registered for vector %u", vector);
        return -2;
    }

    g_int_handlers[vector] = handler;
    VOS3_DEBUG("IDT: Registered handler for vector %u", vector);

    return 0;
}

int vos3_int_unregister(uint8_t vector)
{
    if (g_int_handlers[vector] == NULL) {
        return -1;
    }

    g_int_handlers[vector] = NULL;
    VOS3_DEBUG("IDT: Unregistered handler for vector %u", vector);

    return 0;
}

/* ============================================================================
 * COMMON INTERRUPT DISPATCHER
 * ============================================================================
 * Called from ISR stubs to dispatch to registered handlers
 * ============================================================================ */

/**
 * @brief Common interrupt handler (called from assembly stubs)
 * @param[in,out] frame Pointer to interrupt frame
 */
void vos3_int_dispatch(vos3_int_frame_t* frame)
{
    uint8_t vector = (uint8_t)frame->int_num;

    /* Check for registered handler */
    if (g_int_handlers[vector] != NULL) {
        g_int_handlers[vector](frame);
        return;
    }

    /* No handler registered - handle defaults */
    if (vector < 32U) {
        /* Unhandled CPU exception */
        vos3_exception_info_t info;
        info.vector = frame->int_num;
        info.error_code = frame->error_code;
        info.rip = frame->rip;
        info.rsp = frame->rsp;
        info.rbp = frame->rbp;

        /* Read CR2 for page faults */
        if (vector == VOS3_INT_PAGE_FAULT) {
            __asm__ volatile ("movq %%cr2, %0" : "=r" (info.cr2));
        } else {
            info.cr2 = 0U;
        }

        /* Print exception info and panic */
        vos3_panic(
            "Unhandled Exception #%u\n"
            "  Error Code: 0x%016llx\n"
            "  RIP: 0x%016llx\n"
            "  RSP: 0x%016llx\n"
            "  RBP: 0x%016llx\n"
            "  CR2: 0x%016llx\n"
            "  RFLAGS: 0x%016llx",
            vector,
            (unsigned long long)info.error_code,
            (unsigned long long)info.rip,
            (unsigned long long)info.rsp,
            (unsigned long long)info.rbp,
            (unsigned long long)info.cr2,
            (unsigned long long)frame->rflags
        );
    }

    /* Unhandled IRQ - just log and return */
    VOS3_WARN("Unhandled interrupt vector %u", vector);
}
