/**
 * @file uaccess.h
 * @brief VOS3 Unified User Access API
 *
 * @details Provides secure functions for copying data between kernel
 *          and user space with proper pointer validation.
 *
 * @version 1.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 * @note Phase 22 - Secure User Copy Infrastructure
 */

#ifndef VOS3_UACCESS_H
#define VOS3_UACCESS_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stddef.h>
#include <stdint.h>

/* ============================================================================
 * VOS3 SYSTEM IDENTITY MODES (Phase 22.5 + Phase 24)
 * ============================================================================ */

/** @brief System mode: Private Individual (Privacy-first) */
#define VOS3_MODE_PRIVATE       ((uint32_t)0x01U)

/** @brief System mode: Enterprise Corporation (Throughput-first) */
#define VOS3_MODE_ENTERPRISE    ((uint32_t)0x02U)

/** @brief System mode: Executive Hybrid (Admin-controlled delegation) */
#define VOS3_MODE_EXECUTIVE_HYBRID  ((uint32_t)0x03U)

/** @brief Enterprise mode with Owner Lock (Business-Only enforcement) */
#define VOS3_MODE_ENTERPRISE_LOCKED ((uint32_t)0x82U)  /* 0x80 | 0x02 */

/** @brief Owner Lock bit - when set, enforces Business-Only policy */
#define VOS3_MODE_OWNER_LOCK_BIT    ((uint32_t)0x80U)

/* ============================================================================
 * BUSINESS SOVEREIGNTY (Phase 22.6 - Enterprise Lock)
 * ============================================================================ */

/** @brief Admin Token size (256-bit cryptographic token) */
#define VOS3_ADMIN_TOKEN_SIZE       32

/** @brief Policy violation error code */
#define EPOLICY     200

/** @brief Invalid business unit ID */
#define VOS3_INVALID_BUSINESS_UNIT  ((uint32_t)0x00000000U)

/** @brief Enterprise-approved memory region marker */
#define VOS3_ENTERPRISE_APPROVED_START  ((uint64_t)0x0000200000000000ULL)
#define VOS3_ENTERPRISE_APPROVED_END    ((uint64_t)0x0000600000000000ULL)

/** @brief Personal/non-business memory region (blocked in LOCKED mode) */
#define VOS3_PERSONAL_BLOCK_START       ((uint64_t)0x0000600000000000ULL)
#define VOS3_PERSONAL_BLOCK_END         ((uint64_t)0x00007F0000000000ULL)

/**
 * @brief Policy violation types for logging
 */
typedef enum {
    VOS3_POLICY_NONE = 0,
    VOS3_POLICY_NO_BUSINESS_UNIT,      /**< Task has no business_unit_id */
    VOS3_POLICY_PERSONAL_MEMORY,       /**< Attempted personal memory access */
    VOS3_POLICY_BLOCKED_SYSCALL,       /**< Non-business syscall attempted */
    VOS3_POLICY_INVALID_TOKEN,         /**< Invalid admin token presented */
    VOS3_POLICY_UNAUTHORIZED_TASK      /**< Task creation without business context */
} vos3_policy_violation_t;

/** @brief Get current system mode */
uint32_t vos3_get_system_mode(void);

/** @brief Check if enterprise lock is active */
int vos3_is_enterprise_locked(void);

/** @brief Set system mode (first-boot only) */
void vos3_set_system_mode(uint32_t mode);

/**
 * @brief Activate Enterprise Owner Lock (requires Admin Token)
 *
 * Once activated, the system enforces Business-Only policy:
 * - All tasks must have valid business_unit_id
 * - Personal memory regions are blocked
 * - Non-business syscalls are rejected
 *
 * @param[in] admin_token  32-byte cryptographic admin token
 * @return 0 on success, -EPOLICY on invalid token, -EINVAL if not enterprise mode
 */
int vos3_activate_owner_lock(const uint8_t* admin_token);

/**
 * @brief Deactivate Enterprise Owner Lock (requires Admin Token)
 *
 * @param[in] admin_token  32-byte cryptographic admin token
 * @return 0 on success, -EPOLICY on invalid token
 */
int vos3_deactivate_owner_lock(const uint8_t* admin_token);

/**
 * @brief Validate business unit ID for current task
 *
 * In ENTERPRISE_LOCKED mode, all tasks must have valid business_unit_id.
 *
 * @param[in] business_unit_id  The business unit identifier
 * @return 1 if valid, 0 if invalid or missing
 */
int vos3_validate_business_unit(uint32_t business_unit_id);

/**
 * @brief Log a policy violation
 *
 * @param[in] violation_type  Type of policy violation
 * @param[in] task_id         Task that caused the violation
 * @param[in] address         Memory address involved (if applicable)
 */
void vos3_log_policy_violation(vos3_policy_violation_t violation_type,
                                uint32_t task_id, uint64_t address);

/**
 * @brief Get policy violation count
 *
 * @return Number of policy violations since boot
 */
uint64_t vos3_get_policy_violations(void);

/* ============================================================================
 * EXECUTIVE HYBRID MODE (Phase 24 - Admin Delegation)
 * ============================================================================ */

/** @brief Delegation permission: Allow personal space access */
#define VOS3_DELEG_PERM_PERSONAL_SPACE    ((uint32_t)0x00000001U)

/** @brief Delegation permission: Allow workshop access */
#define VOS3_DELEG_PERM_WORKSHOP          ((uint32_t)0x00000010U)

/** @brief Delegation permission: Allow workspace switching */
#define VOS3_DELEG_PERM_WORKSPACE_SWITCH  ((uint32_t)0x00000100U)

/**
 * @brief Check delegation permission in Executive Hybrid mode
 *
 * In VOS3_MODE_EXECUTIVE_HYBRID, this checks if the given permission
 * has been granted by the admin delegation policy.
 *
 * @param[in] permission  The permission flag to check
 * @return 1 if permitted, 0 if denied
 */
int vos3_delegation_check_perm(uint32_t permission);

/**
 * @brief Check if system is in awaiting-admin state
 *
 * @return 1 if awaiting admin enrollment, 0 otherwise
 */
int vos3_delegation_is_awaiting(void);

/* ============================================================================
 * PRIVACY SHIELD BOUNDARIES (Identity-Aware)
 * ============================================================================ */

/** @brief Private mode: Personal data region start */
#define VOS3_PERSONAL_REGION_START  ((uint64_t)0x0000700000000000ULL)

/** @brief Private mode: Personal data region end */
#define VOS3_PERSONAL_REGION_END    ((uint64_t)0x00007F0000000000ULL)

/** @brief AI sandbox region (jailed in PRIVATE mode) */
#define VOS3_AI_SANDBOX_START       ((uint64_t)0x0000100000000000ULL)
#define VOS3_AI_SANDBOX_END         ((uint64_t)0x0000200000000000ULL)

/* ============================================================================
 * USER SPACE BOUNDARIES
 * ============================================================================ */

/** @brief User space upper boundary (canonical lower half) */
#define USER_SPACE_END      ((uint64_t)0x00007FFFFFFFFFFFULL)

/** @brief User space lower boundary (avoid NULL page) */
#define USER_SPACE_START    ((uint64_t)0x0000000000001000ULL)

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

/** @brief Bad address (fault) */
#define EFAULT  14

/* ============================================================================
 * CORE FUNCTIONS
 * ============================================================================ */

/**
 * @brief Check if a user pointer range is valid
 *
 * Validates that the entire range [addr, addr+size) is within
 * the user space boundaries and doesn't overflow.
 *
 * @param[in] addr  Start address to check
 * @param[in] size  Size of the range in bytes
 * @return 1 if access is OK, 0 if invalid
 */
int access_ok(const void* addr, size_t size);

/* Exact kernel usercopy instruction fault matcher. Returns its static recovery
 * address, or zero for every fault outside the dedicated copy instructions. */
uintptr_t vos3_usercopy_fault_fixup(uintptr_t rip, uint64_t cs, uint64_t error,
                                   uintptr_t addr, uintptr_t rsi,
                                   uintptr_t rdi, uint64_t remaining);

/**
 * @brief Identity-aware access check (Privacy Shield)
 *
 * In VOS3_MODE_PRIVATE: Applies stricter policies to protect
 * personal data regions from AI agent access.
 *
 * In VOS3_MODE_ENTERPRISE: Standard tenant-based isolation.
 *
 * @param[in] addr       Address to check
 * @param[in] size       Size of access
 * @param[in] is_ai_task 1 if caller is an AI agent task
 * @return 1 if access is OK, 0 if blocked by Privacy Shield
 */
int access_ok_identity(const void* addr, size_t size, int is_ai_task);

/**
 * @brief Copy data from user space to kernel space
 *
 * Safely copies @p n bytes from user space address @p src to
 * kernel space address @p dest. Validates the source pointer
 * before copying.
 *
 * @param[out] dest  Kernel destination buffer
 * @param[in]  src   User source buffer
 * @param[in]  n     Number of bytes to copy
 * @return 0 on success, -EFAULT on invalid pointer
 */
int copy_from_user(void* dest, const void* src, size_t n);

/**
 * @brief Copy data from kernel space to user space
 *
 * Safely copies @p n bytes from kernel space address @p src to
 * user space address @p dest. Validates the destination pointer
 * before copying.
 *
 * @param[out] dest  User destination buffer
 * @param[in]  src   Kernel source buffer
 * @param[in]  n     Number of bytes to copy
 * @return 0 on success, -EFAULT on invalid pointer
 */
int copy_to_user(void* dest, const void* src, size_t n);

/**
 * @brief Copy string from user space
 *
 * Copies a NUL-terminated string from user space to kernel space,
 * up to @p max bytes (including NUL terminator). Safe for handling
 * ERP file paths and configuration strings.
 *
 * @param[out] dest  Kernel destination buffer
 * @param[in]  src   User source string
 * @param[in]  max   Maximum bytes to copy (including NUL)
 * @return String length on success, -EFAULT on invalid pointer
 */
int64_t strncpy_from_user(char* dest, const char* src, size_t max);

/**
 * @brief Get length of a user space string
 *
 * Safely determines the length of a NUL-terminated string in user space,
 * up to a maximum of @p max bytes. For ERP configuration validation.
 *
 * @param[in] src  User source string
 * @param[in] max  Maximum length to scan
 * @return Length including NUL on success, -EFAULT on invalid pointer,
 *         or @p max if no NUL found within limit
 */
int64_t strnlen_user(const char* src, size_t max);

/* ============================================================================
 * CONVENIENCE MACROS
 * ============================================================================ */

/**
 * @brief Get a single value from user space
 *
 * Copies sizeof(x) bytes from user space pointer @p ptr to @p x.
 *
 * @param x    Variable to store the value
 * @param ptr  User space pointer to read from
 * @return 0 on success, -EFAULT on invalid pointer
 */
#define get_user(x, ptr)  copy_from_user(&(x), (ptr), sizeof(x))

/**
 * @brief Put a single value to user space
 *
 * Copies sizeof(x) bytes from @p x to user space pointer @p ptr.
 *
 * @param x    Value to copy
 * @param ptr  User space pointer to write to
 * @return 0 on success, -EFAULT on invalid pointer
 */
#define put_user(x, ptr)  ({ \
    __typeof__(x) __pu_val = (x); \
    copy_to_user((ptr), &__pu_val, sizeof(__pu_val)); \
})

#ifdef __cplusplus
}
#endif

#endif /* VOS3_UACCESS_H */
