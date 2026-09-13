/**
 * @file user.h
 * @brief VOS3 User Mode Support
 *
 * @details Structures and functions for user mode task creation
 *          and management.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_USER_H
#define VOS3_USER_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>
#include "task.h"

/* ============================================================================
 * USER SPACE CONFIGURATION
 * ============================================================================ */

/** @brief User space start address (canonical lower half) */
#define VOS3_USER_BASE          ((uint64_t)0x0000000000400000ULL)

/** @brief User space end address */
#define VOS3_USER_END           ((uint64_t)0x00007FFFFFFFFFFFULL)

/** @brief Default user stack address (grows down from here) */
#define VOS3_USER_STACK_TOP     ((uint64_t)0x00007FFFFFF00000ULL)

/** @brief Default user stack size (8 MB) */
#define VOS3_USER_STACK_SIZE    ((size_t)(8ULL * 1024ULL * 1024ULL))

/** @brief User heap start address */
#define VOS3_USER_HEAP_START    ((uint64_t)0x0000000010000000ULL)

/** @brief Initial user heap size (16 MB) */
#define VOS3_USER_HEAP_SIZE     ((size_t)(16ULL * 1024ULL * 1024ULL))

/** @brief Dynamic linker / interpreter load base (1 GiB) */
#define VOS3_INTERP_BASE        ((uint64_t)0x0000000040000000ULL)

/* ============================================================================
 * SEGMENT SELECTORS
 * ============================================================================ */

/** @brief Kernel code segment (ring 0, 64-bit) */
#define VOS3_KERNEL_CS          ((uint16_t)0x08U)

/** @brief Kernel data segment (ring 0) */
#define VOS3_KERNEL_DS          ((uint16_t)0x10U)

/** @brief User code segment (ring 3, 64-bit) - GDT index 3, selector 0x18 | RPL 3 */
#define VOS3_USER_CS            ((uint16_t)0x1BU)

/** @brief User data segment (ring 3) - GDT index 4, selector 0x20 | RPL 3 */
#define VOS3_USER_DS            ((uint16_t)0x23U)

/* ============================================================================
 * IRET FRAME
 * ============================================================================ */

/**
 * @brief IRET frame for returning to user mode
 *
 * Stack layout expected by IRETQ:
 *   [RSP+32] SS
 *   [RSP+24] RSP (user stack)
 *   [RSP+16] RFLAGS
 *   [RSP+8]  CS
 *   [RSP+0]  RIP (user entry point)
 */
typedef struct vos3_iret_frame {
    uint64_t rip;           /**< User instruction pointer */
    uint64_t cs;            /**< User code segment */
    uint64_t rflags;        /**< User RFLAGS */
    uint64_t rsp;           /**< User stack pointer */
    uint64_t ss;            /**< User stack segment */
} __attribute__((packed)) vos3_iret_frame_t;

/** @brief Default user RFLAGS (IF=1 for interrupts enabled) */
#define VOS3_USER_RFLAGS        ((uint64_t)0x0000000000000202ULL)

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Initialize user mode support
 * @return 0 on success, negative error code on failure
 */
int vos3_user_init(void);

/**
 * @brief Create a user mode task
 * @param[in] name Task name
 * @param[in] entry User entry point (virtual address in user space)
 * @param[in] priority Task priority
 * @return Task pointer, or NULL on failure
 *
 * This creates a task that will start executing at the specified
 * entry point in ring 3 (user mode).
 */
vos3_task_t* vos3_user_task_create(const char* name,
                                    uint64_t entry,
                                    vos3_task_priority_t priority);

/**
 * @brief Create a user mode task with custom stack
 * @param[in] name Task name
 * @param[in] entry User entry point
 * @param[in] stack_top User stack top address
 * @param[in] stack_size User stack size
 * @param[in] priority Task priority
 * @return Task pointer, or NULL on failure
 */
vos3_task_t* vos3_user_task_create_ex(const char* name,
                                       uint64_t entry,
                                       uint64_t stack_top,
                                       size_t stack_size,
                                       vos3_task_priority_t priority);

/**
 * @brief Jump to user mode
 * @param[in] entry User entry point
 * @param[in] user_stack User stack pointer
 * @note This function does not return
 *
 * Sets up the stack for IRETQ and jumps to user mode.
 */
__attribute__((noreturn))
void vos3_jump_to_user(uint64_t entry, uint64_t user_stack);

/**
 * @brief Fork child return trampoline
 *
 * Entry point for forked child when first scheduled.
 * Returns to user mode at fork() call site with RAX=0.
 * @note This function does not return
 */
__attribute__((noreturn))
void vos3_fork_child_return(void);

/**
 * @brief Check if address is in user space
 * @param[in] addr Address to check
 * @return 1 if user space, 0 if kernel space
 */
static inline int vos3_is_user_address(uint64_t addr)
{
    return (addr < VOS3_USER_END) ? 1 : 0;
}

/**
 * @brief Copy data from user space
 * @param[out] dst Kernel destination
 * @param[in] src User source
 * @param[in] len Number of bytes
 * @return 0 on success, negative error on failure
 */
int vos3_copy_from_user(void* dst, const void* src, size_t len);

/**
 * @brief Copy data to user space
 * @param[out] dst User destination
 * @param[in] src Kernel source
 * @param[in] len Number of bytes
 * @return 0 on success, negative error on failure
 */
int vos3_copy_to_user(void* dst, const void* src, size_t len);

/**
 * @brief Copy string from user space
 * @param[out] dst Kernel destination
 * @param[in] src User source string
 * @param[in] max Maximum length
 * @return String length, or negative error
 */
int64_t vos3_strncpy_from_user(char* dst, const char* src, size_t max);

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_USER_OK            (0)
#define VOS3_USER_ERR_NOMEM     (-1)
#define VOS3_USER_ERR_INVALID   (-2)
#define VOS3_USER_ERR_FAULT     (-14)

#ifdef __cplusplus
}
#endif

#endif /* VOS3_USER_H */
