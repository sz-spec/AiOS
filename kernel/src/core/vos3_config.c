/**
 * @file vos3_config.c
 * @brief VOS3 System Configuration Implementation
 *
 * @details Implements the Persona & Workspace logic for VOS3.
 *          Manages system identity, workspace switching, and syscall restrictions.
 *          Phase 24: Executive Hybrid Mode with Admin Delegation.
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

#include "../../include/vos/vos3_config.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/string.h"
#include "../../include/vos/sha256.h"
#include "../../include/vos/console.h"

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

/** @brief Current loaded configuration */
static vos3_config_t g_config;

/** @brief Configuration loaded flag */
static int g_config_loaded = 0;

/** @brief Provisioning status */
static vos3_provisioning_status_t g_prov_status = VOS3_PROV_NOT_STARTED;

/** @brief Delegation policy (Phase 24) */
static vos3_delegation_t g_delegation;

/** @brief Delegation loaded flag */
static int g_delegation_loaded = 0;

/** @brief Admin token initialized flag */
static int g_admin_token_set = 0;

/* ============================================================================
 * CRC32 IMPLEMENTATION (for checksum)
 * ============================================================================ */

static uint32_t crc32_table[256];
static int crc32_initialized = 0;

static void crc32_init(void)
{
    if (crc32_initialized) return;

    for (uint32_t i = 0; i < 256; i++) {
        uint32_t crc = i;
        for (int j = 0; j < 8; j++) {
            if (crc & 1) {
                crc = (crc >> 1) ^ 0xEDB88320U;
            } else {
                crc >>= 1;
            }
        }
        crc32_table[i] = crc;
    }
    crc32_initialized = 1;
}

static uint32_t crc32_compute(const uint8_t* data, size_t len)
{
    crc32_init();
    uint32_t crc = 0xFFFFFFFFU;
    for (size_t i = 0; i < len; i++) {
        uint8_t idx = (uint8_t)((crc ^ data[i]) & 0xFF);
        crc = (crc >> 8) ^ crc32_table[idx];
    }
    return crc ^ 0xFFFFFFFFU;
}

/* Constant-time comparison: vos3_ct_equal() from sha256.h / crypto_helpers.c */

/* ============================================================================
 * STRING HELPERS
 * ============================================================================ */

const char* vos3_config_mode_str(vos3_system_mode_t mode)
{
    switch (mode) {
        case VOS3_SYSMODE_PRIVATE:         return "PRIVATE";
        case VOS3_SYSMODE_ENTERPRISE:      return "ENTERPRISE";
        case VOS3_SYSMODE_EXECUTIVE_HYBRID: return "EXECUTIVE_HYBRID";
        default:                            return "UNSET";
    }
}

const char* vos3_config_workspace_str(vos3_workspace_t ws)
{
    switch (ws) {
        case VOS3_WORKSPACE_WORKSHOP: return "WORKSHOP";
        case VOS3_WORKSPACE_OFFICE:   return "OFFICE";
        case VOS3_WORKSPACE_PERSONAL: return "PERSONAL";
        default:                       return "UNKNOWN";
    }
}

static const char* persona_str(vos3_active_persona_t p)
{
    switch (p) {
        case VOS3_PERSONA_WORK:     return "WORK";
        case VOS3_PERSONA_PERSONAL: return "PERSONAL";
        default:                     return "UNKNOWN";
    }
}

/* ============================================================================
 * PROVISIONING API
 * ============================================================================ */

vos3_provisioning_status_t vos3_get_provisioning_status(void)
{
    return g_prov_status;
}

void vos3_set_provisioning_status(vos3_provisioning_status_t status)
{
    g_prov_status = status;
}

int vos3_provisioning_needed(void)
{
    /* Check if config file exists via VFS */
    if (g_config_loaded && g_config.magic == VOS3_CONFIG_MAGIC) {
        /* Check if hybrid mode awaiting admin */
        if (g_config.system_mode == VOS3_SYSMODE_EXECUTIVE_HYBRID &&
            g_config.is_managed && !g_delegation_loaded) {
            g_prov_status = VOS3_PROV_AWAITING_ADMIN;
            return 1;  /* Need admin to complete setup */
        }
        return 0;  /* Already provisioned */
    }

    /* Try to load config */
    if (vos3_config_load() == 0) {
        return 0;  /* Successfully loaded */
    }

    return 1;  /* Provisioning needed */
}

/* ============================================================================
 * CONFIGURATION FILE I/O
 * ============================================================================ */

int vos3_config_is_provisioned(void)
{
    if (g_config_loaded && g_config.magic == VOS3_CONFIG_MAGIC) {
        return 1;
    }

    /* Attempt to load */
    return (vos3_config_load() == 0);
}

int vos3_config_load(void)
{
    /* Try to open config file */
    int fd = vos3_open(VOS3_CONFIG_PATH, VOS3_O_RDONLY, 0);
    if (fd < 0) {
        VOS3_DEBUG("[CONFIG] Cannot open %s (fd=%d)", VOS3_CONFIG_PATH, fd);
        return -1;
    }

    /* Read configuration */
    int64_t bytes = vos3_read(fd, &g_config, sizeof(g_config));
    vos3_close(fd);

    if (bytes != sizeof(g_config)) {
        VOS3_DEBUG("[CONFIG] Invalid config size: %lld", (long long)bytes);
        return -1;
    }

    /* Validate magic */
    if (g_config.magic != VOS3_CONFIG_MAGIC) {
        VOS3_DEBUG("[CONFIG] Invalid magic: 0x%08X", g_config.magic);
        return -1;
    }

    /* Validate checksum */
    uint32_t stored_checksum = g_config.checksum;
    g_config.checksum = 0;
    uint32_t computed = crc32_compute((const uint8_t*)&g_config,
                                       sizeof(g_config) - sizeof(uint32_t));
    g_config.checksum = stored_checksum;

    if (computed != stored_checksum) {
        VOS3_DEBUG("[CONFIG] Checksum mismatch: 0x%08X vs 0x%08X",
                   computed, stored_checksum);
        return -1;
    }

    g_config_loaded = 1;
    g_prov_status = VOS3_PROV_COMPLETE;

    /* Check if admin token is set */
    uint8_t zero_token[VOS3_ADMIN_TOKEN_SIZE] = {0};
    if (!vos3_ct_equal(g_config.admin_token, zero_token, VOS3_ADMIN_TOKEN_SIZE)) {
        g_admin_token_set = 1;
    }

    VOS3_INFO("[CONFIG] Loaded: Mode=%s, Workspace=%s",
              vos3_config_mode_str(g_config.system_mode),
              vos3_config_workspace_str(g_config.workspace));

    /* If hybrid mode, also load delegation */
    if (g_config.system_mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        VOS3_INFO("[CONFIG] Hybrid mode - loading delegation policy");
        vos3_delegation_load();
    }

    return 0;
}

int vos3_config_save(const vos3_config_t* config)
{
    if (config == NULL) {
        return -1;
    }

    /* Create /etc directory if needed */
    (void)vos3_mkdir("/etc", 0755);

    /* Open config file for writing */
    int fd = vos3_open(VOS3_CONFIG_PATH,
                       VOS3_O_WRONLY | VOS3_O_CREAT | VOS3_O_TRUNC, 0644);
    if (fd < 0) {
        VOS3_ERROR("[CONFIG] Cannot create %s (fd=%d)", VOS3_CONFIG_PATH, fd);
        return -1;
    }

    /* Prepare config with checksum */
    vos3_config_t save_config;
    memcpy(&save_config, config, sizeof(save_config));
    save_config.checksum = 0;
    save_config.checksum = crc32_compute((const uint8_t*)&save_config,
                                          sizeof(save_config) - sizeof(uint32_t));

    /* Write configuration */
    int64_t bytes = vos3_write(fd, &save_config, sizeof(save_config));
    vos3_close(fd);

    if (bytes != sizeof(save_config)) {
        VOS3_ERROR("[CONFIG] Write failed: %lld bytes", (long long)bytes);
        return -1;
    }

    /* Update global state */
    memcpy(&g_config, &save_config, sizeof(g_config));
    g_config_loaded = 1;
    g_prov_status = VOS3_PROV_COMPLETE;

    VOS3_INFO("[CONFIG] Saved: Mode=%s, Workspace=%s",
              vos3_config_mode_str(g_config.system_mode),
              vos3_config_workspace_str(g_config.workspace));

    return 0;
}

/* ============================================================================
 * CONFIGURATION GETTERS
 * ============================================================================ */

vos3_system_mode_t vos3_config_get_system_mode(void)
{
    if (!g_config_loaded) {
        return VOS3_SYSMODE_UNSET;
    }
    return (vos3_system_mode_t)g_config.system_mode;
}

vos3_workspace_t vos3_config_get_workspace(void)
{
    if (!g_config_loaded) {
        return VOS3_WORKSPACE_WORKSHOP;  /* Default to Workshop */
    }
    return (vos3_workspace_t)g_config.workspace;
}

vos3_active_persona_t vos3_config_get_active_persona(void)
{
    if (!g_config_loaded) {
        return VOS3_PERSONA_WORK;
    }
    return (vos3_active_persona_t)g_config.active_persona;
}

/* ============================================================================
 * WORKSPACE SWITCHING (with delegation enforcement)
 * ============================================================================ */

int vos3_config_set_workspace(vos3_workspace_t workspace)
{
    if (!g_config_loaded) {
        return -1;
    }

    /* Validate workspace value */
    if (workspace != VOS3_WORKSPACE_WORKSHOP &&
        workspace != VOS3_WORKSPACE_OFFICE &&
        workspace != VOS3_WORKSPACE_PERSONAL) {
        return -1;
    }

    /* Phase 24: Enforce delegation policy in Hybrid mode */
    if (g_config.system_mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {

        /* Check WORKSHOP permission */
        if (workspace == VOS3_WORKSPACE_WORKSHOP) {
            if (!vos3_delegation_check_permission(VOS3_PERM_ALLOW_WORKSHOP)) {
                VOS3_WARN("[CONFIG] Workshop switch DENIED by delegation policy");
                vos3_delegation_audit_log("WORKSPACE_DENIED", "Workshop blocked by policy");
                return -EPERM_WORKSPACE;
            }
        }

        /* Check PERSONAL permission */
        if (workspace == VOS3_WORKSPACE_PERSONAL) {
            if (!vos3_delegation_check_permission(VOS3_PERM_ALLOW_PERSONAL_SPACE)) {
                VOS3_WARN("[CONFIG] Personal space switch DENIED by delegation policy");
                vos3_delegation_audit_log("WORKSPACE_DENIED", "Personal space blocked by policy");
                return -EPERM_WORKSPACE;
            }
        }
    }

    /* Enterprise mode: No personal workspace allowed */
    if (g_config.system_mode == VOS3_SYSMODE_ENTERPRISE) {
        if (workspace == VOS3_WORKSPACE_PERSONAL) {
            VOS3_WARN("[CONFIG] Personal workspace not available in Enterprise mode");
            return -EPERM_WORKSPACE;
        }
    }

    g_config.workspace = workspace;

    /* Persist change */
    return vos3_config_save(&g_config);
}

/* ============================================================================
 * PERSONA SWITCHING (Phase 24)
 * ============================================================================ */

int vos3_config_set_active_persona(vos3_active_persona_t persona)
{
    if (!g_config_loaded) {
        return -1;
    }

    /* Persona switching only meaningful in Hybrid mode */
    if (g_config.system_mode != VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        VOS3_WARN("[CONFIG] Persona switching only available in Hybrid mode");
        return -1;
    }

    /* Check delegation permission */
    if (persona == VOS3_PERSONA_PERSONAL) {
        if (!vos3_delegation_check_permission(VOS3_PERM_ALLOW_PERSONAL_SPACE)) {
            VOS3_WARN("[CONFIG] Personal persona DENIED by delegation policy");
            vos3_delegation_audit_log("PERSONA_DENIED", "Personal persona blocked by policy");
            return -EPERM_PERSONA;
        }
    }

    g_config.active_persona = persona;

    VOS3_INFO("[CONFIG] Active persona switched to: %s", persona_str(persona));
    vos3_delegation_audit_log("PERSONA_SWITCH", persona_str(persona));

    return vos3_config_save(&g_config);
}

/* ============================================================================
 * SYSCALL RESTRICTION LOGIC
 * ============================================================================ */

int vos3_config_syscall_allowed(vos3_syscall_category_t category)
{
    vos3_workspace_t ws = vos3_config_get_workspace();
    uint32_t allowed;

    if (ws == VOS3_WORKSPACE_OFFICE) {
        allowed = VOS3_OFFICE_ALLOWED_SYSCALLS;
    } else if (ws == VOS3_WORKSPACE_PERSONAL) {
        /* Personal workspace: same as office, but with personal storage access */
        allowed = VOS3_OFFICE_ALLOWED_SYSCALLS;
    } else {
        /* Workshop mode: check delegation for AI/debug restrictions */
        allowed = VOS3_WORKSHOP_ALLOWED_SYSCALLS;

        /* Phase 24: Apply delegation restrictions even in Workshop */
        if (g_config.system_mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
            if (!vos3_delegation_check_permission(VOS3_PERM_ALLOW_AI_TUNING)) {
                allowed &= ~VOS3_SYSCALL_CAT_AI_DEV;
            }
            if (!vos3_delegation_check_permission(VOS3_PERM_ALLOW_DEBUG)) {
                allowed &= ~VOS3_SYSCALL_CAT_DEBUG;
            }
            if (!vos3_delegation_check_permission(VOS3_PERM_ALLOW_RAW_DEVICE)) {
                allowed &= ~VOS3_SYSCALL_CAT_RAW_DEVICE;
            }
        }
    }

    return ((allowed & category) != 0) ? 1 : 0;
}

/* ============================================================================
 * DELEGATION POLICY I/O (Phase 24)
 * ============================================================================ */

int vos3_delegation_load(void)
{
    int fd = vos3_open(VOS3_DELEGATION_PATH, VOS3_O_RDONLY, 0);
    if (fd < 0) {
        VOS3_DEBUG("[DELEGATION] Cannot open %s", VOS3_DELEGATION_PATH);
        return -1;
    }

    int64_t bytes = vos3_read(fd, &g_delegation, sizeof(g_delegation));
    vos3_close(fd);

    if (bytes != sizeof(g_delegation)) {
        VOS3_DEBUG("[DELEGATION] Invalid policy size: %lld", (long long)bytes);
        return -1;
    }

    if (g_delegation.magic != VOS3_DELEGATION_MAGIC) {
        VOS3_DEBUG("[DELEGATION] Invalid magic: 0x%08X", g_delegation.magic);
        return -1;
    }

    /* Validate checksum */
    uint32_t stored = g_delegation.checksum;
    g_delegation.checksum = 0;
    uint32_t computed = crc32_compute((const uint8_t*)&g_delegation,
                                       sizeof(g_delegation) - sizeof(uint32_t));
    g_delegation.checksum = stored;

    if (computed != stored) {
        VOS3_DEBUG("[DELEGATION] Checksum mismatch");
        return -1;
    }

    g_delegation_loaded = 1;

    VOS3_INFO("[DELEGATION] Loaded policy: granted=0x%08X, denied=0x%08X",
              g_delegation.granted_permissions, g_delegation.denied_permissions);

    return 0;
}

int vos3_delegation_save(const vos3_delegation_t* policy, const uint8_t* admin_token)
{
    if (policy == NULL || admin_token == NULL) {
        return -1;
    }

    /* Validate admin token */
    if (!vos3_admin_token_validate(admin_token)) {
        VOS3_WARN("[DELEGATION] Invalid admin token - save denied");
        vos3_delegation_audit_log("SAVE_DENIED", "Invalid admin token");
        return -EPERM_ADMIN_ONLY;
    }

    (void)vos3_mkdir("/etc", 0755);

    int fd = vos3_open(VOS3_DELEGATION_PATH,
                       VOS3_O_WRONLY | VOS3_O_CREAT | VOS3_O_TRUNC, 0600);
    if (fd < 0) {
        VOS3_ERROR("[DELEGATION] Cannot create %s", VOS3_DELEGATION_PATH);
        return -1;
    }

    /* Prepare with checksum */
    vos3_delegation_t save_policy;
    memcpy(&save_policy, policy, sizeof(save_policy));
    save_policy.checksum = 0;
    save_policy.checksum = crc32_compute((const uint8_t*)&save_policy,
                                          sizeof(save_policy) - sizeof(uint32_t));

    int64_t bytes = vos3_write(fd, &save_policy, sizeof(save_policy));
    vos3_close(fd);

    if (bytes != sizeof(save_policy)) {
        return -1;
    }

    memcpy(&g_delegation, &save_policy, sizeof(g_delegation));
    g_delegation_loaded = 1;

    /* Also update cached flags in main config */
    g_config.delegation_flags = policy->granted_permissions;
    vos3_config_save(&g_config);

    VOS3_INFO("[DELEGATION] Policy saved by admin");
    vos3_delegation_audit_log("POLICY_UPDATED", "Admin updated delegation policy");

    return 0;
}

int vos3_delegation_update(uint32_t grant_mask, uint32_t deny_mask,
                            const uint8_t* admin_token)
{
    if (admin_token == NULL) {
        return -EPERM_ADMIN_ONLY;
    }

    if (!vos3_admin_token_validate(admin_token)) {
        VOS3_WARN("[DELEGATION] Invalid admin token - update denied");
        return -EPERM_ADMIN_ONLY;
    }

    if (!g_delegation_loaded) {
        /* Initialize new delegation */
        memset(&g_delegation, 0, sizeof(g_delegation));
        g_delegation.magic = VOS3_DELEGATION_MAGIC;
        g_delegation.version = 1;
    }

    g_delegation.granted_permissions = grant_mask;
    g_delegation.denied_permissions = deny_mask;

    return vos3_delegation_save(&g_delegation, admin_token);
}

/* ============================================================================
 * DELEGATION PERMISSION CHECK
 * ============================================================================ */

int vos3_delegation_check_permission(vos3_persona_perm_t perm)
{
    /* In non-hybrid modes, all permissions are granted */
    if (g_config.system_mode != VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        return 1;
    }

    /* If no delegation loaded, deny by default in hybrid mode */
    if (!g_delegation_loaded) {
        return 0;
    }

    /* Check explicit deny first */
    if ((g_delegation.denied_permissions & perm) != 0) {
        return 0;
    }

    /* Then check if granted */
    if ((g_delegation.granted_permissions & perm) != 0) {
        return 1;
    }

    /* Default deny for unspecified permissions in hybrid mode */
    return 0;
}

/**
 * @brief Check if system is in awaiting-admin state
 *
 * Used by access_ok_identity to restrict access while awaiting
 * admin enrollment in Executive Hybrid mode.
 */
int vos3_delegation_is_awaiting_admin(void)
{
    /* Only applies to Executive Hybrid mode */
    if (g_config.system_mode != VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        return 0;
    }

    /* Check provisioning status */
    if (g_prov_status == VOS3_PROV_AWAITING_ADMIN) {
        return 1;
    }

    /* Check if delegation is loaded */
    if (!g_delegation_loaded) {
        return 1;  /* No delegation = awaiting admin */
    }

    return 0;
}

/* ============================================================================
 * ADMIN TOKEN MANAGEMENT
 * ============================================================================ */

int vos3_admin_token_validate(const uint8_t* token)
{
    if (token == NULL) {
        return 0;
    }

    if (!g_admin_token_set) {
        return 0;  /* No token set yet */
    }

    /* Constant-time comparison */
    return vos3_ct_equal(g_config.admin_token, token, VOS3_ADMIN_TOKEN_SIZE);
}

int vos3_admin_token_init(const uint8_t* token)
{
    if (token == NULL) {
        return -1;
    }

    if (g_admin_token_set) {
        VOS3_WARN("[ADMIN] Token already set - init denied");
        return -1;
    }

    memcpy(g_config.admin_token, token, VOS3_ADMIN_TOKEN_SIZE);
    g_admin_token_set = 1;

    /* Store hash in delegation policy too */
    if (g_delegation_loaded) {
        memcpy(g_delegation.admin_token_hash, token, VOS3_ADMIN_TOKEN_SIZE);
    }

    VOS3_INFO("[ADMIN] Admin token initialized");
    return vos3_config_save(&g_config);
}

/* ============================================================================
 * FLEET MANAGEMENT HELPERS
 * ============================================================================ */

int vos3_is_fleet_managed(void)
{
    if (!g_config_loaded) {
        return 0;
    }

    return (g_config.system_mode == VOS3_SYSMODE_EXECUTIVE_HYBRID &&
            g_config.is_managed != 0);
}

const vos3_delegation_t* vos3_delegation_get_policy(void)
{
    if (!g_delegation_loaded) {
        return NULL;
    }
    return &g_delegation;
}

void vos3_delegation_audit_log(const char* event_type, const char* details)
{
    /* In production, this would write to a secure audit log */
    VOS3_INFO("[AUDIT] %s: %s", event_type, details);
}

/* ============================================================================
 * KERNEL INITIALIZATION HELPER
 * ============================================================================ */

/**
 * @brief Initialize configuration subsystem (called from kmain)
 */
void vos3_config_init(void)
{
    memset(&g_config, 0, sizeof(g_config));
    memset(&g_delegation, 0, sizeof(g_delegation));
    g_config_loaded = 0;
    g_delegation_loaded = 0;
    g_admin_token_set = 0;
    g_prov_status = VOS3_PROV_NOT_STARTED;

    /* Attempt to load existing config */
    if (vos3_config_load() == 0) {
        VOS3_INFO("[CONFIG] System provisioned: %s mode",
                  vos3_config_mode_str(g_config.system_mode));

        if (g_config.system_mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
            VOS3_INFO("[CONFIG] Fleet-managed: %s",
                      g_config.is_managed ? "YES" : "NO");
            VOS3_INFO("[CONFIG] Active persona: %s",
                      persona_str(g_config.active_persona));
        }
    } else {
        VOS3_INFO("[CONFIG] System not provisioned - wizard required");
    }
}

/* ============================================================================
 * SYSCALL HANDLERS (Phase 24)
 * ============================================================================ */

#include "../../include/vos/syscall.h"
#include "../../include/vos/uaccess.h"

/**
 * @brief SYS_CONFIG_GET - Get VOS3 configuration
 */
int64_t vos3_sys_config_get(vos3_syscall_frame_t* frame)
{
    int cmd = (int)frame->rdi;
    void* buf = (void*)frame->rsi;
    size_t len = (size_t)frame->rdx;

    (void)cmd;  /* Reserved for future sub-commands */

    if (!access_ok(buf, len)) {
        return -14;  /* -EFAULT */
    }

    if (len < sizeof(vos3_config_t)) {
        return -22;  /* -EINVAL */
    }

    if (!g_config_loaded) {
        return -2;  /* -ENOENT */
    }

    if (copy_to_user(buf, &g_config, sizeof(vos3_config_t)) != 0) {
        return -14;  /* -EFAULT */
    }

    return 0;
}

/**
 * @brief SYS_CONFIG_SET - Set VOS3 configuration
 */
int64_t vos3_sys_config_set(vos3_syscall_frame_t* frame)
{
    void* buf = (void*)frame->rdi;
    size_t len = (size_t)frame->rsi;

    if (!access_ok(buf, len)) {
        return -14;  /* -EFAULT */
    }

    if (len < sizeof(vos3_config_t)) {
        return -22;  /* -EINVAL */
    }

    vos3_config_t new_config;
    if (copy_from_user(&new_config, buf, sizeof(vos3_config_t)) != 0) {
        return -14;  /* -EFAULT */
    }

    /* Validate magic */
    if (new_config.magic != VOS3_CONFIG_MAGIC) {
        return -22;  /* -EINVAL */
    }

    /* Copy to global config */
    memcpy(&g_config, &new_config, sizeof(vos3_config_t));
    g_config_loaded = 1;
    g_prov_status = VOS3_PROV_COMPLETE;

    /* Save to persistent storage */
    return vos3_config_save(&g_config);
}

/**
 * @brief SYS_DELEGATION_GET - Get delegation policy
 */
int64_t vos3_sys_delegation_get(vos3_syscall_frame_t* frame)
{
    void* buf = (void*)frame->rdi;
    size_t len = (size_t)frame->rsi;

    if (!access_ok(buf, len)) {
        return -14;  /* -EFAULT */
    }

    if (len < sizeof(vos3_delegation_t)) {
        return -22;  /* -EINVAL */
    }

    /* Executive Hybrid mode required for delegation */
    if (g_config.system_mode != VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        return -1;  /* -EPERM */
    }

    if (!g_delegation_loaded) {
        return -2;  /* -ENOENT */
    }

    if (copy_to_user(buf, &g_delegation, sizeof(vos3_delegation_t)) != 0) {
        return -14;  /* -EFAULT */
    }

    return 0;
}

/**
 * @brief SYS_DELEGATION_SET - Set delegation policy
 */
int64_t vos3_sys_delegation_set(vos3_syscall_frame_t* frame)
{
    void* buf = (void*)frame->rdi;
    size_t len = (size_t)frame->rsi;
    void* token = (void*)frame->rdx;

    if (!access_ok(buf, len)) {
        return -14;  /* -EFAULT */
    }

    if (len < sizeof(vos3_delegation_t)) {
        return -22;  /* -EINVAL */
    }

    /* If token provided, validate it */
    if (token != NULL) {
        if (!access_ok(token, VOS3_ADMIN_TOKEN_SIZE)) {
            return -14;  /* -EFAULT */
        }
        uint8_t token_buf[VOS3_ADMIN_TOKEN_SIZE];
        if (copy_from_user(token_buf, token, VOS3_ADMIN_TOKEN_SIZE) != 0) {
            return -14;  /* -EFAULT */
        }
        if (!vos3_admin_token_validate(token_buf)) {
            /* Token required but invalid */
            if (g_admin_token_set) {
                return -1;  /* -EPERM */
            }
        }
    }

    vos3_delegation_t new_delegation;
    if (copy_from_user(&new_delegation, buf, sizeof(vos3_delegation_t)) != 0) {
        return -14;  /* -EFAULT */
    }

    /* Copy to global delegation */
    memcpy(&g_delegation, &new_delegation, sizeof(vos3_delegation_t));
    g_delegation_loaded = 1;

    VOS3_INFO("[DELEGATION] Policy updated: granted=0x%08X, denied=0x%08X",
              g_delegation.granted_permissions,
              g_delegation.denied_permissions);

    return 0;
}

/**
 * @brief SYS_ADMIN_AUTH - Admin token authentication
 */
int64_t vos3_sys_admin_auth(vos3_syscall_frame_t* frame)
{
    void* token = (void*)frame->rdi;
    size_t len = (size_t)frame->rsi;

    if (len != VOS3_ADMIN_TOKEN_SIZE) {
        return -22;  /* -EINVAL */
    }

    if (!access_ok(token, len)) {
        return -14;  /* -EFAULT */
    }

    uint8_t token_buf[VOS3_ADMIN_TOKEN_SIZE];
    if (copy_from_user(token_buf, token, VOS3_ADMIN_TOKEN_SIZE) != 0) {
        return -14;  /* -EFAULT */
    }

    /* First-time token set */
    if (!g_admin_token_set) {
        return vos3_admin_token_init(token_buf);
    }

    /* Validate existing token */
    if (vos3_admin_token_validate(token_buf)) {
        return 0;  /* Success */
    }

    return -1;  /* -EPERM */
}

/**
 * @brief Register delegation syscalls (called from syscall_init)
 */
void vos3_config_register_syscalls(void)
{
    vos3_syscall_register(VOS3_SYS_CONFIG_GET, vos3_sys_config_get);
    vos3_syscall_register(VOS3_SYS_CONFIG_SET, vos3_sys_config_set);
    vos3_syscall_register(VOS3_SYS_DELEGATION_GET, vos3_sys_delegation_get);
    vos3_syscall_register(VOS3_SYS_DELEGATION_SET, vos3_sys_delegation_set);
    vos3_syscall_register(VOS3_SYS_ADMIN_AUTH, vos3_sys_admin_auth);

    VOS3_INFO("[CONFIG] Delegation syscalls registered (200-212)");
}
