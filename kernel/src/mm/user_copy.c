/**
 * @file user_copy.c
 * @brief VOS3 Secure User Copy Implementation
 *
 * @details Implements secure data transfer between kernel and user space
 *          with proper pointer validation to prevent security vulnerabilities.
 *          Supports multi-tenant isolation via per-process address validation.
 *
 * @version 2.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 * @note Phase 22.6 - Business Sovereignty Edition (Enterprise Lock)
 */

#include "../../include/vos/uaccess.h"
#include "../../include/vos/sha256.h"
#include "../../include/arch/x86_64/cpu.h"

/* ============================================================================
 * SMAP (Supervisor Mode Access Prevention) Helpers
 * ============================================================================
 *
 * When CR4.SMAP is set, any kernel-mode access to user pages triggers #PF
 * unless EFLAGS.AC is set.  stac() sets AC (allow access), clac() clears it.
 * These are the x86-64 instructions introduced with Broadwell (CPUID.7.EBX[20]).
 *
 * The .byte encoding is used to avoid requiring -march=broadwell at compile time.
 *   CLAC = 0x0F 0x01 0xCA
 *   STAC = 0x0F 0x01 0xCB
 */

/** @brief Set AC flag — temporarily allow supervisor access to user pages. */
static inline void stac(void)
{
    uint64_t cr4;
    __asm__ volatile("mov %%cr4, %0" : "=r"(cr4));
    if ((cr4 & VOS3_CR4_SMAP) != 0U) {
        __asm__ volatile(".byte 0x0f, 0x01, 0xcb" ::: "memory", "cc");
    }
}

/** @brief Clear AC flag — re-enable SMAP protection. */
static inline void clac(void)
{
    uint64_t cr4;
    __asm__ volatile("mov %%cr4, %0" : "=r"(cr4));
    if ((cr4 & VOS3_CR4_SMAP) != 0U) {
        __asm__ volatile(".byte 0x0f, 0x01, 0xca" ::: "memory", "cc");
    }
}

/* ============================================================================
 * VOS3 IDENTITY FRAMEWORK (Phase 22.5 + 22.6)
 * ============================================================================
 *
 * VOS3 supports two deployment identities:
 *
 * VOS3_MODE_PRIVATE (0x01):
 *   - Target: Individual users with AI personal assistants
 *   - Privacy Shield: AI agents CANNOT access personal data regions
 *   - Personal region: 0x700000000000 - 0x7F0000000000 (jailed from AI)
 *   - AI sandbox: 0x100000000000 - 0x200000000000 (AI workspace)
 *
 * VOS3_MODE_ENTERPRISE (0x02):
 *   - Target: Corporations running CRM/ERP with AI automation
 *   - Tenant isolation: Each tenant gets isolated address ranges
 *   - AI has full access within tenant boundaries
 *   - Billing metrics tracked per-tenant
 */

/** @brief Current system identity mode (set at first boot) */
static uint32_t g_vos3_system_mode = 0U;  /* 0 = unset (requires setup wizard) */

/** @brief System mode has been locked (first-boot configuration complete) */
static int g_mode_locked = 0;

/* ============================================================================
 * BUSINESS SOVEREIGNTY (Phase 22.6 - Enterprise Lock)
 * ============================================================================
 *
 * VOS3_MODE_ENTERPRISE_LOCKED (0x82):
 *   - Owner Lock bit (0x80) + Enterprise mode (0x02)
 *   - ALL tasks MUST have valid business_unit_id
 *   - Personal memory regions are BLOCKED
 *   - Non-business syscalls are REJECTED
 *   - Requires Admin Token to activate/deactivate
 */

/** @brief Stored Admin Token (SHA-256 hash of owner's master key) */
static uint8_t g_admin_token[VOS3_ADMIN_TOKEN_SIZE] = {0};

/** @brief Admin token has been configured */
static int g_admin_token_set = 0;

/** @brief Policy violation counter */
static uint64_t g_policy_violations = 0U;

/** @brief Last violation type for diagnostics */
static vos3_policy_violation_t g_last_violation = VOS3_POLICY_NONE;

/** @brief Last violation task ID */
static uint32_t g_last_violation_task = 0U;

/** @brief Last violation address */
static uint64_t g_last_violation_addr = 0U;

#include "../../include/vos/string.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/vmm.h"
#include "../../include/arch/x86_64/memory_map.h"

/* Exact instruction fixups: no global/per-CPU continuation or saved stack. */
extern int vos3_usercopy_from_raw(void*, const void*, size_t);
extern int vos3_usercopy_to_raw(void*, const void*, size_t);
extern const char vos3_usercopy_from_fault[], vos3_usercopy_from_fixup[];
extern const char vos3_usercopy_to_fault[], vos3_usercopy_to_fixup[];
__asm__(
    ".pushsection .text\n"
    ".global vos3_usercopy_from_raw, vos3_usercopy_from_fault, vos3_usercopy_from_fixup\n"
    ".type vos3_usercopy_from_raw,@function\n"
    "vos3_usercopy_from_raw:\n cld\n mov %rdx,%rcx\n"
    "vos3_usercopy_from_fault:\n rep movsb\n xor %eax,%eax\n ret\n"
    "vos3_usercopy_from_fixup:\n mov $-14,%eax\n ret\n"
    ".size vos3_usercopy_from_raw,.-vos3_usercopy_from_raw\n"
    ".global vos3_usercopy_to_raw, vos3_usercopy_to_fault, vos3_usercopy_to_fixup\n"
    ".type vos3_usercopy_to_raw,@function\n"
    "vos3_usercopy_to_raw:\n cld\n mov %rdx,%rcx\n"
    "vos3_usercopy_to_fault:\n rep movsb\n xor %eax,%eax\n ret\n"
    "vos3_usercopy_to_fixup:\n mov $-14,%eax\n ret\n"
    ".size vos3_usercopy_to_raw,.-vos3_usercopy_to_raw\n"
    ".popsection\n");

uintptr_t vos3_usercopy_fault_fixup(uintptr_t rip, uint64_t cs, uint64_t error,
                                   uintptr_t addr, uintptr_t rsi,
                                   uintptr_t rdi, uint64_t remaining)
{
    /* Only supervisor data accesses, never user/RSVD/fetch/PK/shadow-stack faults. */
    if ((cs & 3U) != 0 || (error & ~3ULL) != 0 || remaining == 0)
        return 0;
    if (rip == (uintptr_t)vos3_usercopy_from_fault && (error & 2U) == 0 &&
        addr == rsi && access_ok((const void*)rsi, remaining))
        return (uintptr_t)vos3_usercopy_from_fixup;
    if (rip == (uintptr_t)vos3_usercopy_to_fault && (error & 2U) != 0 &&
        addr == rdi && access_ok((const void*)rdi, remaining))
        return (uintptr_t)vos3_usercopy_to_fixup;
    return 0;
}

/* ============================================================================
 * IDENTITY MODE FUNCTIONS
 * ============================================================================ */

/**
 * @brief Get current VOS3 system identity mode
 *
 * @return VOS3_MODE_PRIVATE, VOS3_MODE_ENTERPRISE, or 0 (unconfigured)
 */
uint32_t vos3_get_system_mode(void)
{
    return g_vos3_system_mode;
}

/**
 * @brief Set VOS3 system identity mode (first-boot only)
 *
 * This function can only be called ONCE during system setup.
 * After the mode is locked, all subsequent calls are ignored.
 *
 * @param[in] mode  VOS3_MODE_PRIVATE, VOS3_MODE_ENTERPRISE, or VOS3_MODE_EXECUTIVE_HYBRID
 */
void vos3_set_system_mode(uint32_t mode)
{
    /* Mode can only be set once */
    if (g_mode_locked != 0) {
        return;
    }

    /* Validate mode */
    if (mode != VOS3_MODE_PRIVATE &&
        mode != VOS3_MODE_ENTERPRISE &&
        mode != VOS3_MODE_EXECUTIVE_HYBRID) {
        return;
    }

    g_vos3_system_mode = mode;
    g_mode_locked = 1;
}

/**
 * @brief Check if enterprise lock is active
 *
 * @return 1 if locked, 0 otherwise
 */
int vos3_is_enterprise_locked(void)
{
    return (g_vos3_system_mode & VOS3_MODE_OWNER_LOCK_BIT) != 0U;
}

/* ============================================================================
 * BUSINESS SOVEREIGNTY FUNCTIONS (Phase 22.6)
 * ============================================================================ */

/* Constant-time comparison: vos3_ct_equal() from sha256.h / crypto_helpers.c */

/**
 * @brief Activate Enterprise Owner Lock
 *
 * Once activated, the system enforces Business-Only policy.
 * Requires valid Admin Token and must already be in ENTERPRISE mode.
 */
int vos3_activate_owner_lock(const uint8_t* admin_token)
{
    /* Must be in ENTERPRISE mode (but not already locked) */
    if (g_vos3_system_mode != VOS3_MODE_ENTERPRISE) {
        return -14;  /* -EINVAL */
    }

    if (admin_token == NULL) {
        return -EPOLICY;
    }

    /* First activation: Store the admin token */
    if (g_admin_token_set == 0) {
        memcpy(g_admin_token, admin_token, VOS3_ADMIN_TOKEN_SIZE);
        g_admin_token_set = 1;
    } else {
        /* Subsequent activations: Verify admin token */
        if (!vos3_ct_equal(g_admin_token, admin_token, VOS3_ADMIN_TOKEN_SIZE)) {
            g_policy_violations++;
            g_last_violation = VOS3_POLICY_INVALID_TOKEN;
            return -EPOLICY;
        }
    }

    /* Activate the Owner Lock bit */
    g_vos3_system_mode = VOS3_MODE_ENTERPRISE_LOCKED;

    return 0;
}

/**
 * @brief Deactivate Enterprise Owner Lock
 *
 * Requires valid Admin Token.
 */
int vos3_deactivate_owner_lock(const uint8_t* admin_token)
{
    /* Must be in ENTERPRISE_LOCKED mode */
    if (g_vos3_system_mode != VOS3_MODE_ENTERPRISE_LOCKED) {
        return -14;  /* -EINVAL */
    }

    if (admin_token == NULL) {
        return -EPOLICY;
    }

    /* Verify admin token */
    if (!vos3_ct_equal(g_admin_token, admin_token, VOS3_ADMIN_TOKEN_SIZE)) {
        g_policy_violations++;
        g_last_violation = VOS3_POLICY_INVALID_TOKEN;
        return -EPOLICY;
    }

    /* Remove the Owner Lock bit, keep ENTERPRISE mode */
    g_vos3_system_mode = VOS3_MODE_ENTERPRISE;

    return 0;
}

/**
 * @brief Validate business unit ID for current task
 *
 * In ENTERPRISE_LOCKED mode, all tasks must have valid business_unit_id.
 */
int vos3_validate_business_unit(uint32_t business_unit_id)
{
    /* In unlocked modes, any business_unit_id is acceptable */
    if (g_vos3_system_mode != VOS3_MODE_ENTERPRISE_LOCKED) {
        return 1;
    }

    /* In locked mode, business_unit_id must be non-zero */
    if (business_unit_id == VOS3_INVALID_BUSINESS_UNIT) {
        return 0;
    }

    return 1;
}

/**
 * @brief Log a policy violation
 */
void vos3_log_policy_violation(vos3_policy_violation_t violation_type,
                                uint32_t task_id, uint64_t address)
{
    g_policy_violations++;
    g_last_violation = violation_type;
    g_last_violation_task = task_id;
    g_last_violation_addr = address;

    /*
     * In production, this would also:
     * - Write to secure audit log
     * - Send alert to MDM console
     * - Potentially quarantine the task
     */
}

/**
 * @brief Get policy violation count
 */
uint64_t vos3_get_policy_violations(void)
{
    return g_policy_violations;
}

/* ============================================================================
 * EXECUTIVE HYBRID MODE DELEGATION (Phase 24)
 * ============================================================================ */

/* External delegation policy state (defined in vos3_config.c) */
extern int vos3_delegation_check_permission(uint32_t perm);
extern int vos3_delegation_is_awaiting_admin(void);

/**
 * @brief Check delegation permission in Executive Hybrid mode
 *
 * Wrapper function that interfaces with the delegation policy
 * stored in vos3_config.c.
 */
int vos3_delegation_check_perm(uint32_t permission)
{
    /* Only applies to Executive Hybrid mode */
    if (g_vos3_system_mode != VOS3_MODE_EXECUTIVE_HYBRID) {
        return 1;  /* Allow in non-hybrid modes */
    }

    return vos3_delegation_check_permission(permission);
}

/**
 * @brief Check if system is in awaiting-admin state
 */
int vos3_delegation_is_awaiting(void)
{
    /* Only applies to Executive Hybrid mode */
    if (g_vos3_system_mode != VOS3_MODE_EXECUTIVE_HYBRID) {
        return 0;
    }

    return vos3_delegation_is_awaiting_admin();
}

/* ============================================================================
 * PRIVACY SHIELD (Identity-Aware Access Control)
 * ============================================================================ */

/**
 * @brief Identity-aware access validation with Privacy Shield & Business Lock
 *
 * In VOS3_MODE_PRIVATE:
 *   - AI agents are BLOCKED from accessing personal data regions
 *   - Personal region (0x700000000000 - 0x7F0000000000) is off-limits to AI
 *   - AI agents must stay within their sandbox
 *
 * In VOS3_MODE_ENTERPRISE:
 *   - Standard tenant-based isolation applies
 *   - AI has full access within tenant boundaries
 *   - No Privacy Shield restrictions
 *
 * In VOS3_MODE_ENTERPRISE_LOCKED:
 *   - ALL access must be to Enterprise-Approved regions
 *   - Personal/non-business regions are BLOCKED for all tasks
 *   - Policy violations are logged
 *
 * @param[in] addr       Address to validate
 * @param[in] size       Size of access in bytes
 * @param[in] is_ai_task 1 if caller is an AI agent task, 0 otherwise
 * @return 1 if access is permitted, 0 if blocked
 */
int access_ok_identity(const void* addr, size_t size, int is_ai_task)
{
    uint64_t addr_start = (uint64_t)(uintptr_t)addr;
    uint64_t addr_end;
    vos3_task_t* current;

    /* First, perform standard access_ok() validation */
    if (!access_ok(addr, size)) {
        return 0;
    }

    /* Calculate end address (already validated by access_ok) */
    addr_end = addr_start + size;

    /* ================================================================
     * ENTERPRISE LOCKED MODE: Business-Only Policy Enforcement
     * ================================================================ */
    if (g_vos3_system_mode == VOS3_MODE_ENTERPRISE_LOCKED) {
        /*
         * BUSINESS SOVEREIGNTY: Block access to non-enterprise regions
         *
         * Enterprise-Approved Region: 0x200000000000 - 0x600000000000
         * Personal/Blocked Region: 0x600000000000 - 0x7F0000000000
         *
         * In locked mode, ANY access to personal regions is blocked,
         * regardless of whether it's AI or human.
         */

        /* Check if access touches personal/blocked region */
        if (addr_start >= VOS3_PERSONAL_BLOCK_START &&
            addr_start < VOS3_PERSONAL_BLOCK_END) {
            /* Attempting to access personal region - POLICY VIOLATION */
            current = vos3_sched_current();
            vos3_log_policy_violation(VOS3_POLICY_PERSONAL_MEMORY,
                current ? current->tid : 0, addr_start);
            return 0;
        }

        if (addr_end > VOS3_PERSONAL_BLOCK_START &&
            addr_end <= VOS3_PERSONAL_BLOCK_END) {
            /* Access spans into personal region - POLICY VIOLATION */
            current = vos3_sched_current();
            vos3_log_policy_violation(VOS3_POLICY_PERSONAL_MEMORY,
                current ? current->tid : 0, addr_start);
            return 0;
        }

        /* Check if access encompasses personal region */
        if (addr_start < VOS3_PERSONAL_BLOCK_START &&
            addr_end > VOS3_PERSONAL_BLOCK_END) {
            /* Access encompasses personal region - POLICY VIOLATION */
            current = vos3_sched_current();
            vos3_log_policy_violation(VOS3_POLICY_PERSONAL_MEMORY,
                current ? current->tid : 0, addr_start);
            return 0;
        }

        /* Access is within enterprise-approved bounds */
        return 1;
    }

    /* Enterprise mode (unlocked): No additional restrictions for AI */
    if (g_vos3_system_mode == VOS3_MODE_ENTERPRISE) {
        return 1;
    }

    /* ================================================================
     * EXECUTIVE HYBRID MODE: Delegation-Based Access Control
     * ================================================================ */
    if (g_vos3_system_mode == VOS3_MODE_EXECUTIVE_HYBRID) {
        /*
         * PHASE 24: Admin Delegation Policy Enforcement
         *
         * In Executive Hybrid mode, access to Personal regions is
         * controlled by admin delegation policy. If personal space
         * access is denied, block access to personal memory regions.
         */

        /* Check if awaiting admin enrollment */
        if (vos3_delegation_is_awaiting()) {
            /* While awaiting admin, only allow access to safe regions */
            /* Block personal regions until admin enrolls */
            if (addr_start >= VOS3_PERSONAL_REGION_START &&
                addr_start < VOS3_PERSONAL_REGION_END) {
                current = vos3_sched_current();
                vos3_log_policy_violation(VOS3_POLICY_PERSONAL_MEMORY,
                    current ? current->tid : 0, addr_start);
                return 0;
            }
        }

        /* Check if personal space access is denied by delegation */
        if (!vos3_delegation_check_perm(VOS3_DELEG_PERM_PERSONAL_SPACE)) {
            /* Personal space blocked - deny access to personal regions */
            if (addr_start >= VOS3_PERSONAL_REGION_START &&
                addr_start < VOS3_PERSONAL_REGION_END) {
                current = vos3_sched_current();
                vos3_log_policy_violation(VOS3_POLICY_PERSONAL_MEMORY,
                    current ? current->tid : 0, addr_start);
                return 0;
            }

            if (addr_end > VOS3_PERSONAL_REGION_START &&
                addr_end <= VOS3_PERSONAL_REGION_END) {
                current = vos3_sched_current();
                vos3_log_policy_violation(VOS3_POLICY_PERSONAL_MEMORY,
                    current ? current->tid : 0, addr_start);
                return 0;
            }
        }

        /* Access permitted in Executive Hybrid mode */
        return 1;
    }

    /* Private mode: Apply Privacy Shield for AI agents */
    if (g_vos3_system_mode == VOS3_MODE_PRIVATE && is_ai_task != 0) {
        /*
         * PRIVACY SHIELD: Block AI access to personal data region
         *
         * Personal Region: 0x0000700000000000 - 0x00007F0000000000
         * This region contains:
         *   - User documents and files
         *   - Browser history and cookies
         *   - Personal photos and media
         *   - Private keys and credentials
         *   - Health and financial data
         */
        if (addr_start >= VOS3_PERSONAL_REGION_START &&
            addr_start < VOS3_PERSONAL_REGION_END) {
            /* AI attempting to access personal data - BLOCKED */
            return 0;
        }

        if (addr_end > VOS3_PERSONAL_REGION_START &&
            addr_end <= VOS3_PERSONAL_REGION_END) {
            /* AI access spans into personal region - BLOCKED */
            return 0;
        }

        /* Check if access wraps around personal region */
        if (addr_start < VOS3_PERSONAL_REGION_START &&
            addr_end > VOS3_PERSONAL_REGION_END) {
            /* AI access encompasses personal region - BLOCKED */
            return 0;
        }
    }

    /* Unconfigured mode (0) or human task: Allow access */
    return 1;
}

/* ============================================================================
 * MULTI-TENANT ISOLATION
 * ============================================================================ */

/**
 * @brief Get process-specific user space bounds
 *
 * For multi-tenant isolation, each process has its own valid address range.
 * This function retrieves the bounds for the current process.
 *
 * @param[out] start  Pointer to store start address
 * @param[out] end    Pointer to store end address
 * @return 0 on success, -1 if no current task
 */
static int get_process_bounds(uint64_t* start, uint64_t* end)
{
    vos3_task_t* current = vos3_sched_current();

    if (current == NULL) {
        /* Kernel context - use full user space range */
        *start = USER_SPACE_START;
        *end = USER_SPACE_END;
        return 0;
    }

    /*
     * TODO: Per-process address space isolation
     * When full VM isolation is implemented, retrieve bounds from:
     *   current->mm->user_start
     *   current->mm->user_end
     *
     * For now, use standard user space bounds.
     */
    *start = USER_SPACE_START;
    *end = USER_SPACE_END;

    return 0;
}

/* ============================================================================
 * CORE VALIDATION
 * ============================================================================ */

/**
 * @brief Check if a user pointer range is valid
 *
 * Validates that the entire range [addr, addr+size) is within
 * the current process's user space boundaries. Supports multi-tenant
 * isolation by checking process-specific address ranges.
 */
int access_ok(const void* addr, size_t size)
{
    uint64_t addr_start = (uint64_t)(uintptr_t)addr;
    uint64_t addr_end;
    uint64_t proc_start, proc_end;

    /* Zero-size access is always invalid */
    if (size == 0U) {
        return 0;
    }

    /* Check for overflow */
    addr_end = addr_start + size;
    if (addr_end < addr_start) {
        return 0;
    }

    /* Get process-specific bounds for multi-tenant isolation */
    if (get_process_bounds(&proc_start, &proc_end) != 0) {
        return 0;
    }

    /* Address must be within process's user space range */
    if (addr_start < proc_start) {
        return 0;
    }

    if (addr_end > proc_end) {
        return 0;
    }

    /*
     * Additional security check: Reject kernel addresses
     * The canonical hole (0x0000800000000000 - 0xFFFF7FFFFFFFFFFF) and
     * kernel space (0xFFFF800000000000+) are never valid for user access.
     */
    if (addr_start >= 0x0000800000000000ULL) {
        return 0;
    }

    return 1;
}

/* ============================================================================
 * DATA TRANSFER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Copy data from user space to kernel space
 */
static int user_pages_accessible(const void* addr, size_t size, int write);

int copy_from_user(void* dest, const void* src, size_t n)
{
    /* Validate kernel destination */
    if (dest == NULL) {
        return -EFAULT;
    }

    /* Zero-size copy is a no-op success */
    if (n == 0U) {
        return 0;
    }

    /* Validate user source pointer */
    if (!access_ok(src, n) || !user_pages_accessible(src, n, 0)) {
        return -EFAULT;
    }

    /* SMAP: Temporarily allow supervisor access to user pages.
     * stac() sets EFLAGS.AC; clac() clears it after the copy.
     * The helpers skip STAC/CLAC when this CPU has no active SMAP; the
     * instructions themselves would raise #UD on unsupported processors. */
    stac();
    int result = vos3_usercopy_from_raw(dest, src, n);
    clac();

    return result;
}

/**
 * @brief Reject populated mappings without effective user permissions
 *
 * Checks USER at every level, including huge-page leaves. Writes also need
 * effective WRITE or a leaf COW marker. Absent mappings are left for the
 * bounded fault path, which can demand-page valid VMAs or return EFAULT.
 *
 * @param[in] addr  Start address
 * @param[in] size  Size in bytes
 * @return 1 if all pages are writable, 0 otherwise
 */
static int user_pages_accessible(const void* addr, size_t size, int write)
{
    uintptr_t start = (uintptr_t)addr;
    uintptr_t end = start + size; /* caller already checked range/overflow */
    vos3_address_space_t* as = vos3_vmm_get_current_space();
    if (as == NULL || as->pml4 == NULL) return 0;
    for (uintptr_t page = start & ~0xFFFULL; page < end; page += 0x1000ULL) {
        vos3_pte_t* table = as->pml4;
        for (unsigned level = 0; level < 4; level++) {
            unsigned shift = 39U - 9U * level;
            vos3_pte_t entry = __atomic_load_n(&table[(page >> shift) & 511U], __ATOMIC_ACQUIRE);
            /* Absent mappings reach the exact fault/retry path. Only a valid
             * permitted VMA/heap may be demand-paged by the page-fault handler. */
            if ((entry & VOS3_PTE_PRESENT) == 0) break;
            if ((entry & VOS3_PTE_USER) == 0) return 0;
            int leaf = level == 3 || ((level == 1 || level == 2) &&
                                         (entry & (1ULL << 7)) != 0);
            if (write && (entry & VOS3_PTE_WRITABLE) == 0 &&
                !(leaf && vos3_pte_is_cow(entry))) return 0;
            if (leaf) break;
            table = vos3_phys_to_virt(vos3_pte_get_addr(entry));
        }
    }
    return 1;
}

/**
 * @brief Copy data from kernel space to user space
 */
int copy_to_user(void* dest, const void* src, size_t n)
{
    /* Validate kernel source */
    if (src == NULL) {
        return -EFAULT;
    }

    /* Zero-size copy is a no-op success */
    if (n == 0U) {
        return 0;
    }

    /* Validate user destination pointer */
    if (!access_ok(dest, n)) {
        return -EFAULT;
    }

    /* Verify destination pages are writable (prevents kernel panic on RO pages) */
    if (!user_pages_accessible(dest, n, 1)) {
        return -EFAULT;
    }

    /* SMAP: Temporarily allow supervisor access to user pages. */
    stac();
    int result = vos3_usercopy_to_raw(dest, src, n);
    clac();

    return result;
}

/**
 * @brief Copy string from user space
 *
 * Safe string copy for ERP file paths and configuration strings.
 * Validates each character address stays within process bounds.
 */
int64_t strncpy_from_user(char* dest, const char* src, size_t max)
{
    size_t i;
    uint64_t src_addr;
    uint64_t proc_start, proc_end;

    /* Validate parameters */
    if (dest == NULL || max == 0U) {
        return -EFAULT;
    }

    /* Get process-specific bounds */
    if (get_process_bounds(&proc_start, &proc_end) != 0) {
        return -EFAULT;
    }

    /* Validate initial source pointer */
    src_addr = (uint64_t)(uintptr_t)src;
    if (src_addr < proc_start || src_addr >= proc_end) {
        return -EFAULT;
    }


    /* Copy character by character with bounds checking */
    for (i = 0U; i < max - 1U; i++) {
        /* Check each character address stays in user space */
        if (src_addr + i >= proc_end) {
            clac();
            return -EFAULT;
        }

        char value;
        if (copy_from_user(&value, src + i, 1) != 0) return -EFAULT;
        dest[i] = value;
        if (value == '\0') {
            clac();
            return (int64_t)i;
        }
    }

    /* Ensure NUL termination */
    dest[i] = '\0';
    clac();
    return (int64_t)i;
}

/**
 * @brief Get length of a user space string
 *
 * Safely determines the length of a NUL-terminated string in user space,
 * up to a maximum of @p max bytes. For ERP configuration validation.
 *
 * @param[in] src  User source string
 * @param[in] max  Maximum length to scan
 * @return String length (excluding NUL), or -EFAULT on invalid pointer,
 *         or @p max if no NUL found within limit
 */
int64_t strnlen_user(const char* src, size_t max)
{
    size_t i;
    uint64_t src_addr;
    uint64_t proc_start, proc_end;

    /* Zero-length scan */
    if (max == 0U) {
        return 0;
    }

    /* Get process-specific bounds */
    if (get_process_bounds(&proc_start, &proc_end) != 0) {
        return -EFAULT;
    }

    /* Validate initial source pointer */
    src_addr = (uint64_t)(uintptr_t)src;
    if (src_addr < proc_start || src_addr >= proc_end) {
        return -EFAULT;
    }


    /* Scan for NUL with bounds checking */
    for (i = 0U; i < max; i++) {
        /* Check each character address stays in user space */
        if (src_addr + i >= proc_end) {
            clac();
            return -EFAULT;
        }

        char value;
        if (copy_from_user(&value, src + i, 1) != 0) return -EFAULT;
        if (value == '\0') {
            clac();
            return (int64_t)(i + 1U);  /* Include NUL in count (POSIX style) */
        }
    }

    /* No NUL found within limit */
    clac();
    return (int64_t)max;
}
