/**
 * @file vos3_config.h
 * @brief VOS3 System Configuration & Workspace Definitions
 *
 * @details Defines the Persona & Workspace Matrix for VOS3.
 *          This controls the system's identity mode and active workspace.
 *
 * @version 2.0.0
 * @date 2026-02-17
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 23 - First-Boot Provisioning & Workspace Logic
 * @note Phase 24 - Executive Hybrid Mode with Admin Delegation
 */

#ifndef VOS3_CONFIG_H
#define VOS3_CONFIG_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * CONFIGURATION FILE PATHS
 * ============================================================================ */

/** @brief System configuration file path */
#define VOS3_CONFIG_PATH        "/etc/vos3.conf"

/** @brief Persona delegation policy file path */
#define VOS3_DELEGATION_PATH    "/etc/vos3_delegation.conf"

/** @brief Configuration magic number */
#define VOS3_CONFIG_MAGIC       0x564F5333U  /* "VOS3" */

/** @brief Delegation config magic number */
#define VOS3_DELEGATION_MAGIC   0x56443234U  /* "VD24" */

/** @brief Configuration version */
#define VOS3_CONFIG_VERSION     2U

/** @brief Admin Token size (256-bit) */
#define VOS3_ADMIN_TOKEN_SIZE   32

/* ============================================================================
 * SYSTEM MODE (Persona) - Immutable after first boot
 * ============================================================================ */

/**
 * @brief System Mode defines the fundamental identity of the OS
 *
 * PRIVATE: Individual user with AI personal assistants
 *   - Privacy Shield enforced
 *   - Personal folders: /home/user, /personal
 *   - AI has restricted access to personal data
 *
 * ENTERPRISE: Corporate deployment with CRM/ERP
 *   - Tenant isolation enforced
 *   - Business folders: /org/data, /org/apps
 *   - AI has full access within tenant boundaries
 *
 * EXECUTIVE_HYBRID (Phase 24): Fleet-managed mixed mode
 *   - Organization Owner controls user capabilities
 *   - Can grant/revoke Personal Space access
 *   - Can grant/revoke Workshop (Dev) access
 *   - Requires Admin Token for policy changes
 */
typedef enum {
    VOS3_SYSMODE_UNSET           = 0x00,  /**< Not configured (first boot) */
    VOS3_SYSMODE_PRIVATE         = 0x01,  /**< Private Individual Mode */
    VOS3_SYSMODE_ENTERPRISE      = 0x02,  /**< Enterprise Corporation Mode */
    VOS3_SYSMODE_EXECUTIVE_HYBRID = 0x03,  /**< Executive Hybrid (Admin-controlled) */
} vos3_system_mode_t;

/* ============================================================================
 * WORKSPACE MODE - Switchable at runtime (subject to delegation)
 * ============================================================================ */

/**
 * @brief Workspace Mode defines the current operating environment
 *
 * WORKSHOP (Development):
 *   - Full access to AI tuning tools
 *   - Development syscalls enabled
 *   - Debug features active
 *   - Intended for: coding, AI training, experimentation
 *
 * OFFICE (Daily):
 *   - AI tuning tools RESTRICTED
 *   - Development syscalls BLOCKED
 *   - Focus mode enabled
 *   - Intended for: productivity, business tasks, meetings
 *
 * PERSONAL (Hybrid mode only):
 *   - User's personal space active
 *   - Privacy Shield protecting personal data
 *   - Separate from business context
 */
typedef enum {
    VOS3_WORKSPACE_WORKSHOP = 0x10,  /**< Development/Workshop mode */
    VOS3_WORKSPACE_OFFICE   = 0x20,  /**< Daily/Office mode */
    VOS3_WORKSPACE_PERSONAL = 0x30,  /**< Personal Space (Hybrid only) */
} vos3_workspace_t;

/* ============================================================================
 * PERSONA DELEGATION FLAGS (Phase 24)
 * ============================================================================ */

/**
 * @brief Persona Permission Flags
 *
 * These flags control what capabilities the Admin has granted to users
 * on managed (Executive Hybrid) machines.
 */
typedef enum {
    VOS3_PERM_NONE              = 0x00000000U,

    /* Personal Space Permissions */
    VOS3_PERM_ALLOW_PERSONAL_SPACE   = 0x00000001U,  /**< Can switch to Personal Persona */
    VOS3_PERM_ALLOW_PERSONAL_STORAGE = 0x00000002U,  /**< Can access personal storage */
    VOS3_PERM_ALLOW_PERSONAL_APPS    = 0x00000004U,  /**< Can run personal applications */

    /* Workshop/Development Permissions */
    VOS3_PERM_ALLOW_WORKSHOP         = 0x00000010U,  /**< Can switch to Workshop mode */
    VOS3_PERM_ALLOW_AI_TUNING        = 0x00000020U,  /**< Can use AI tuning tools */
    VOS3_PERM_ALLOW_DEBUG            = 0x00000040U,  /**< Can use debug tools */
    VOS3_PERM_ALLOW_RAW_DEVICE       = 0x00000080U,  /**< Can access raw devices */

    /* Administrative Permissions */
    VOS3_PERM_ALLOW_SELF_CONFIG      = 0x00000100U,  /**< Can modify own config */
    VOS3_PERM_ALLOW_INSTALL_APPS     = 0x00000200U,  /**< Can install applications */
    VOS3_PERM_ALLOW_NETWORK_ADMIN    = 0x00000400U,  /**< Can modify network settings */

    /* Combined Permission Sets */
    VOS3_PERM_PERSONAL_FULL = (VOS3_PERM_ALLOW_PERSONAL_SPACE |
                               VOS3_PERM_ALLOW_PERSONAL_STORAGE |
                               VOS3_PERM_ALLOW_PERSONAL_APPS),

    VOS3_PERM_WORKSHOP_FULL = (VOS3_PERM_ALLOW_WORKSHOP |
                               VOS3_PERM_ALLOW_AI_TUNING |
                               VOS3_PERM_ALLOW_DEBUG |
                               VOS3_PERM_ALLOW_RAW_DEVICE),

    VOS3_PERM_ALL = 0xFFFFFFFFU,
} vos3_persona_perm_t;

/* ============================================================================
 * CURRENT PERSONA (for Hybrid mode)
 * ============================================================================ */

/**
 * @brief Active Persona in Executive Hybrid mode
 *
 * In Hybrid mode, the user can switch between Work and Personal personas
 * (if permitted by admin delegation policy).
 */
typedef enum {
    VOS3_PERSONA_WORK     = 0x01,  /**< Work/Business persona active */
    VOS3_PERSONA_PERSONAL = 0x02,  /**< Personal persona active */
} vos3_active_persona_t;

/* ============================================================================
 * WORKSPACE RESTRICTIONS
 * ============================================================================ */

/** @brief Syscall categories that can be restricted */
typedef enum {
    VOS3_SYSCALL_CAT_CORE       = 0x01,  /**< Core syscalls (always allowed) */
    VOS3_SYSCALL_CAT_FILE       = 0x02,  /**< File operations */
    VOS3_SYSCALL_CAT_PROCESS    = 0x04,  /**< Process management */
    VOS3_SYSCALL_CAT_MEMORY     = 0x08,  /**< Memory management */
    VOS3_SYSCALL_CAT_AI_DEV     = 0x10,  /**< AI development/tuning (Workshop only) */
    VOS3_SYSCALL_CAT_DEBUG      = 0x20,  /**< Debug operations (Workshop only) */
    VOS3_SYSCALL_CAT_RAW_DEVICE = 0x40,  /**< Raw device access (Workshop only) */
    VOS3_SYSCALL_CAT_NETWORK    = 0x80,  /**< Network operations */
} vos3_syscall_category_t;

/**
 * @brief Syscalls allowed in OFFICE mode
 */
#define VOS3_OFFICE_ALLOWED_SYSCALLS \
    (VOS3_SYSCALL_CAT_CORE | VOS3_SYSCALL_CAT_FILE | \
     VOS3_SYSCALL_CAT_PROCESS | VOS3_SYSCALL_CAT_MEMORY | \
     VOS3_SYSCALL_CAT_NETWORK)

/**
 * @brief Syscalls allowed in WORKSHOP mode (all enabled)
 */
#define VOS3_WORKSHOP_ALLOWED_SYSCALLS (0xFFU)

/* ============================================================================
 * DELEGATION POLICY STRUCTURE (Phase 24)
 * ============================================================================ */

/**
 * @brief Persona Delegation Policy (stored in /etc/vos3_delegation.conf)
 *
 * This structure defines what capabilities the Admin has granted to users
 * on this managed machine.
 */
typedef struct __attribute__((packed)) {
    uint32_t magic;                     /**< Delegation magic (VOS3_DELEGATION_MAGIC) */
    uint32_t version;                   /**< Policy version */
    uint32_t granted_permissions;       /**< Bitmask of vos3_persona_perm_t */
    uint32_t denied_permissions;        /**< Explicitly denied permissions */
    uint32_t active_persona;            /**< Current active persona */
    uint32_t default_workspace;         /**< Default workspace on boot */
    uint32_t policy_timestamp;          /**< When policy was last updated */
    uint32_t policy_expires;            /**< Policy expiration (0 = never) */
    uint8_t  admin_token_hash[32];      /**< SHA-256 of admin token for verification */
    char     admin_name[32];            /**< Name of admin who set policy */
    char     machine_id[64];            /**< Machine identifier */
    char     user_id[32];               /**< User this policy applies to */
    uint32_t audit_flags;               /**< What to log */
    uint32_t reserved[4];               /**< Reserved for future use */
    uint32_t checksum;                  /**< CRC32 checksum */
} vos3_delegation_t;

/* ============================================================================
 * CONFIGURATION STRUCTURE (Updated for Phase 24)
 * ============================================================================ */

/**
 * @brief VOS3 System Configuration (stored in /etc/vos3.conf)
 */
typedef struct __attribute__((packed)) {
    uint32_t magic;             /**< Configuration magic (VOS3_CONFIG_MAGIC) */
    uint32_t version;           /**< Configuration version */
    uint32_t system_mode;       /**< System mode (PRIVATE/ENTERPRISE/HYBRID) */
    uint32_t workspace;         /**< Current workspace (WORKSHOP/OFFICE/PERSONAL) */
    uint32_t first_boot_ts;     /**< First boot timestamp */
    uint32_t last_boot_ts;      /**< Last boot timestamp */
    uint32_t boot_count;        /**< Total boot count */
    uint32_t owner_id;          /**< Owner user ID */
    char     owner_name[32];    /**< Owner display name */
    char     org_name[64];      /**< Organization name (Enterprise/Hybrid only) */

    /* Phase 24: Executive Hybrid additions */
    uint32_t active_persona;    /**< Current persona (Work/Personal) */
    uint32_t delegation_flags;  /**< Cached delegation permissions */
    uint8_t  admin_token[32];   /**< Admin token (for HYBRID mode) */
    uint32_t is_managed;        /**< 1 if fleet-managed, 0 if standalone */

    uint32_t reserved[4];       /**< Reserved for future use */
    uint32_t checksum;          /**< CRC32 checksum */
} vos3_config_t;

/* ============================================================================
 * PROVISIONING STATE
 * ============================================================================ */

/** @brief Provisioning status */
typedef enum {
    VOS3_PROV_NOT_STARTED   = 0,  /**< Provisioning not started */
    VOS3_PROV_IN_PROGRESS   = 1,  /**< Wizard is running */
    VOS3_PROV_COMPLETE      = 2,  /**< Successfully completed */
    VOS3_PROV_FAILED        = 3,  /**< Failed to complete */
    VOS3_PROV_AWAITING_ADMIN = 4, /**< Hybrid mode: waiting for admin config */
} vos3_provisioning_status_t;

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define EPERM_DELEGATION    201  /**< Permission denied by delegation policy */
#define EPERM_ADMIN_ONLY    202  /**< Operation requires admin token */
#define EPERM_PERSONA       203  /**< Persona switch not allowed */
#define EPERM_WORKSPACE     204  /**< Workspace switch not allowed */

/* ============================================================================
 * KERNEL CONFIGURATION API
 * ============================================================================ */

/**
 * @brief Check if system is provisioned
 * @return 1 if /etc/vos3.conf exists and is valid, 0 otherwise
 */
int vos3_config_is_provisioned(void);

/**
 * @brief Get current system mode
 * @return System mode (PRIVATE, ENTERPRISE, or EXECUTIVE_HYBRID)
 */
vos3_system_mode_t vos3_config_get_system_mode(void);

/**
 * @brief Get current workspace
 * @return Workspace (WORKSHOP, OFFICE, or PERSONAL)
 */
vos3_workspace_t vos3_config_get_workspace(void);

/**
 * @brief Set workspace (runtime switchable, subject to delegation)
 * @param[in] workspace  New workspace mode
 * @return 0 on success, -EPERM_* on permission denied, -1 on error
 */
int vos3_config_set_workspace(vos3_workspace_t workspace);

/**
 * @brief Check if syscall category is allowed in current workspace
 * @param[in] category  Syscall category to check
 * @return 1 if allowed, 0 if blocked
 */
int vos3_config_syscall_allowed(vos3_syscall_category_t category);

/**
 * @brief Load configuration from file
 * @return 0 on success, -1 on error
 */
int vos3_config_load(void);

/**
 * @brief Save configuration to file
 * @param[in] config  Configuration to save
 * @return 0 on success, -1 on error
 */
int vos3_config_save(const vos3_config_t* config);

/**
 * @brief Get human-readable system mode string
 * @param[in] mode  System mode
 * @return Mode string
 */
const char* vos3_config_mode_str(vos3_system_mode_t mode);

/**
 * @brief Get human-readable workspace string
 * @param[in] ws  Workspace
 * @return Workspace string
 */
const char* vos3_config_workspace_str(vos3_workspace_t ws);

/* ============================================================================
 * PROVISIONING GATE
 * ============================================================================ */

/**
 * @brief Get provisioning status
 * @return Current provisioning status
 */
vos3_provisioning_status_t vos3_get_provisioning_status(void);

/**
 * @brief Set provisioning status
 * @param[in] status  New status
 */
void vos3_set_provisioning_status(vos3_provisioning_status_t status);

/**
 * @brief Check if setup wizard should run
 * @return 1 if wizard needed, 0 if already provisioned
 */
int vos3_provisioning_needed(void);

/* ============================================================================
 * PERSONA DELEGATION API (Phase 24)
 * ============================================================================ */

/**
 * @brief Get active persona (for Hybrid mode)
 * @return VOS3_PERSONA_WORK or VOS3_PERSONA_PERSONAL
 */
vos3_active_persona_t vos3_config_get_active_persona(void);

/**
 * @brief Switch active persona (requires delegation permission)
 * @param[in] persona  Target persona
 * @return 0 on success, -EPERM_PERSONA if not allowed
 */
int vos3_config_set_active_persona(vos3_active_persona_t persona);

/**
 * @brief Check if a specific permission is granted
 * @param[in] perm  Permission to check
 * @return 1 if granted, 0 if denied
 */
int vos3_delegation_check_permission(vos3_persona_perm_t perm);

/**
 * @brief Load delegation policy from file
 * @return 0 on success, -1 on error
 */
int vos3_delegation_load(void);

/**
 * @brief Save delegation policy to file (requires admin token)
 * @param[in] policy       Policy to save
 * @param[in] admin_token  Admin token for authorization
 * @return 0 on success, -EPERM_ADMIN_ONLY if unauthorized, -1 on error
 */
int vos3_delegation_save(const vos3_delegation_t* policy,
                          const uint8_t* admin_token);

/**
 * @brief Update delegation permissions (requires admin token)
 * @param[in] grant_mask   Permissions to grant
 * @param[in] deny_mask    Permissions to deny
 * @param[in] admin_token  Admin token for authorization
 * @return 0 on success, -EPERM_ADMIN_ONLY if unauthorized
 */
int vos3_delegation_update(uint32_t grant_mask, uint32_t deny_mask,
                            const uint8_t* admin_token);

/**
 * @brief Validate admin token
 * @param[in] token  Token to validate
 * @return 1 if valid, 0 if invalid
 */
int vos3_admin_token_validate(const uint8_t* token);

/**
 * @brief Set initial admin token (first-time setup only)
 * @param[in] token  Admin token to set
 * @return 0 on success, -1 if already set
 */
int vos3_admin_token_init(const uint8_t* token);

/**
 * @brief Check if machine is fleet-managed
 * @return 1 if managed (Hybrid mode with delegation), 0 if standalone
 */
int vos3_is_fleet_managed(void);

/**
 * @brief Get current delegation policy (read-only)
 * @return Pointer to current delegation policy, or NULL if not hybrid mode
 */
const vos3_delegation_t* vos3_delegation_get_policy(void);

/**
 * @brief Log a delegation policy event
 * @param[in] event_type  Type of event
 * @param[in] details     Event details
 */
void vos3_delegation_audit_log(const char* event_type, const char* details);

/**
 * @brief Register configuration syscalls
 * @note Called from syscall init to register syscalls 200-212
 */
void vos3_config_register_syscalls(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_CONFIG_H */
