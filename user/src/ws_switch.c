/**
 * @file ws_switch.c
 * @brief VOS3 Workspace Switcher Utility
 *
 * @details Allows switching between WORKSHOP (Dev), OFFICE (Daily),
 *          and PERSONAL workspaces at runtime. In Executive Hybrid mode,
 *          workspace switching is controlled by admin delegation policy.
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

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"

/* ============================================================================
 * CONFIGURATION DEFINITIONS (must match kernel vos3_config.h)
 * ============================================================================ */

#define VOS3_CONFIG_PATH        "/etc/vos3.conf"
#define VOS3_CONFIG_MAGIC       0x564F5333U

/* System modes */
#define VOS3_SYSMODE_UNSET            0x00
#define VOS3_SYSMODE_PRIVATE          0x01
#define VOS3_SYSMODE_ENTERPRISE       0x02
#define VOS3_SYSMODE_EXECUTIVE_HYBRID 0x03

/* Workspaces */
#define VOS3_WORKSPACE_WORKSHOP  0x10
#define VOS3_WORKSPACE_OFFICE    0x20
#define VOS3_WORKSPACE_PERSONAL  0x30

/* Delegation permissions (Phase 24) */
#define VOS3_PERM_ALLOW_PERSONAL_SPACE    0x00000001U
#define VOS3_PERM_ALLOW_WORKSHOP          0x00000010U
#define VOS3_PERM_ALLOW_WORKSPACE_SWITCH  0x00000100U

/* Delegation flags */
#define VOS3_DELEG_FLAG_LOCKED          0x0001
#define VOS3_DELEG_FLAG_AWAITING_ADMIN  0x0002
#define VOS3_DELEG_FLAG_ENROLLED        0x0004

/* Delegation magic */
#define VOS3_DELEGATION_MAGIC    0x44454C47    /* "DELG" */

/* Error codes */
#define EPERM   1
#define EACCES  13

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint32_t version;
    uint32_t system_mode;
    uint32_t workspace;
    uint32_t first_boot_ts;
    uint32_t last_boot_ts;
    uint32_t boot_count;
    uint32_t owner_id;
    char     owner_name[32];
    char     org_name[64];
    uint32_t reserved[8];
    uint32_t checksum;
} vos3_config_t;

/* Delegation policy structure (Phase 24) */
typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint32_t version;
    uint32_t granted_permissions;
    uint32_t denied_permissions;
    uint32_t active_persona;
    uint8_t  admin_token_hash[32];
    uint8_t  org_id[16];
    uint8_t  user_id[16];
    uint32_t flags;
    uint64_t created_timestamp;
    uint64_t modified_timestamp;
    uint32_t modification_count;
    uint32_t reserved[8];
    uint32_t checksum;
} vos3_delegation_t;

/* ============================================================================
 * SYSCALL INTERFACE FOR DELEGATION
 * ============================================================================ */

#define SYS_VOS3_DELEGATION_GET  472

static inline long sys_vos3_delegation_get(void* buf, size_t len)
{
    long ret;
    __asm__ volatile(
        "syscall"
        : "=a"(ret)
        : "a"(SYS_VOS3_DELEGATION_GET), "D"(buf), "S"(len)
        : "rcx", "r11", "memory"
    );
    return ret;
}

/* ============================================================================
 * CRC32 IMPLEMENTATION
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

/* ============================================================================
 * WORKSPACE STRINGS
 * ============================================================================ */

static const char* workspace_name(uint32_t ws)
{
    switch (ws) {
        case VOS3_WORKSPACE_WORKSHOP: return "WORKSHOP";
        case VOS3_WORKSPACE_OFFICE:   return "OFFICE";
        case VOS3_WORKSPACE_PERSONAL: return "PERSONAL";
        default:                       return "UNKNOWN";
    }
}

static const char* mode_name(uint32_t mode)
{
    switch (mode) {
        case VOS3_SYSMODE_PRIVATE:          return "PRIVATE";
        case VOS3_SYSMODE_ENTERPRISE:       return "ENTERPRISE";
        case VOS3_SYSMODE_EXECUTIVE_HYBRID: return "EXECUTIVE HYBRID";
        default:                             return "UNSET";
    }
}

/* ============================================================================
 * DELEGATION PERMISSION CHECKING (Phase 24)
 * ============================================================================ */

static vos3_delegation_t g_delegation;
static int g_delegation_loaded = 0;

static int load_delegation(void)
{
    if (g_delegation_loaded) return 0;

    long ret = sys_vos3_delegation_get(&g_delegation, sizeof(g_delegation));
    if (ret < 0) {
        return -1;
    }

    if (g_delegation.magic != VOS3_DELEGATION_MAGIC) {
        return -1;
    }

    g_delegation_loaded = 1;
    return 0;
}

static int check_delegation_permission(uint32_t perm)
{
    if (load_delegation() != 0) {
        /* No delegation policy = allow by default (non-hybrid mode) */
        return 1;
    }

    /* Check if explicitly denied */
    if ((g_delegation.denied_permissions & perm) != 0) {
        return 0;
    }

    /* Check if explicitly granted */
    if ((g_delegation.granted_permissions & perm) != 0) {
        return 1;
    }

    /* Default deny in hybrid mode */
    return 0;
}

static int is_awaiting_admin(void)
{
    if (load_delegation() != 0) {
        return 0;
    }
    return (g_delegation.flags & VOS3_DELEG_FLAG_AWAITING_ADMIN) != 0;
}

/* ============================================================================
 * CONFIGURATION I/O
 * ============================================================================ */

static int load_config(vos3_config_t *config)
{
    int fd = open(VOS3_CONFIG_PATH, O_RDONLY, 0);
    if (fd < 0) {
        return -1;
    }

    ssize_t n = read(fd, config, sizeof(*config));
    close(fd);

    if (n != sizeof(*config)) {
        return -1;
    }

    if (config->magic != VOS3_CONFIG_MAGIC) {
        return -1;
    }

    /* Verify checksum */
    uint32_t stored = config->checksum;
    config->checksum = 0;
    uint32_t computed = crc32_compute((const uint8_t*)config,
                                       sizeof(*config) - sizeof(uint32_t));
    config->checksum = stored;

    if (computed != stored) {
        return -1;
    }

    return 0;
}

static int save_config(const vos3_config_t *config)
{
    vos3_config_t save_cfg;
    memcpy(&save_cfg, config, sizeof(save_cfg));

    /* Recompute checksum */
    save_cfg.checksum = 0;
    save_cfg.checksum = crc32_compute((const uint8_t*)&save_cfg,
                                       sizeof(save_cfg) - sizeof(uint32_t));

    int fd = open(VOS3_CONFIG_PATH, O_WRONLY | O_TRUNC, 0);
    if (fd < 0) {
        return -1;
    }

    ssize_t n = write(fd, &save_cfg, sizeof(save_cfg));
    close(fd);

    return (n == sizeof(save_cfg)) ? 0 : -1;
}

/* ============================================================================
 * WORKSPACE DISPLAY
 * ============================================================================ */

static void print_status(const vos3_config_t *config)
{
    printf("\n");
    printf("================================================\n");
    printf("          VOS3 WORKSPACE STATUS                 \n");
    printf("================================================\n");
    printf("\n");

    printf("  System Mode:     %s\n", mode_name(config->system_mode));
    printf("  Owner:           %s\n", config->owner_name);

    if (config->system_mode == VOS3_SYSMODE_ENTERPRISE ||
        config->system_mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        printf("  Organization:    %s\n", config->org_name);
    }

    printf("\n");
    printf("  Current Workspace: %s\n", workspace_name(config->workspace));
    printf("\n");

    /* Executive Hybrid mode - show delegation status */
    if (config->system_mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        printf("  ┌─────────────────────────────────────────────┐\n");
        printf("  │  EXECUTIVE HYBRID - Admin Controlled        │\n");
        printf("  └─────────────────────────────────────────────┘\n");
        printf("\n");

        if (is_awaiting_admin()) {
            printf("  STATUS: Awaiting admin enrollment\n");
            printf("\n");
            printf("  Permissions will be granted after admin enrolls\n");
            printf("  this device into fleet management.\n");
        } else {
            printf("  Admin Delegation Policy:\n");
            if (check_delegation_permission(VOS3_PERM_ALLOW_PERSONAL_SPACE)) {
                printf("    [+] Personal Space - ALLOWED\n");
            } else {
                printf("    [-] Personal Space - BLOCKED\n");
            }
            if (check_delegation_permission(VOS3_PERM_ALLOW_WORKSHOP)) {
                printf("    [+] Workshop Access - ALLOWED\n");
            } else {
                printf("    [-] Workshop Access - BLOCKED\n");
            }
            if (check_delegation_permission(VOS3_PERM_ALLOW_WORKSPACE_SWITCH)) {
                printf("    [+] Workspace Switch - ALLOWED\n");
            } else {
                printf("    [-] Workspace Switch - BLOCKED\n");
            }
        }
        printf("\n");
    }

    if (config->workspace == VOS3_WORKSPACE_WORKSHOP) {
        printf("  ┌─────────────────────────────────────────────┐\n");
        printf("  │  WORKSHOP MODE - Development Environment   │\n");
        printf("  └─────────────────────────────────────────────┘\n");
        printf("\n");
        printf("  Access Enabled:\n");
        printf("    [+] AI tuning and training tools\n");
        printf("    [+] Development syscalls\n");
        printf("    [+] Debug features\n");
        printf("    [+] Raw device access\n");
    } else if (config->workspace == VOS3_WORKSPACE_PERSONAL) {
        printf("  ┌─────────────────────────────────────────────┐\n");
        printf("  │  PERSONAL MODE - Private Space              │\n");
        printf("  └─────────────────────────────────────────────┘\n");
        printf("\n");
        printf("  Personal Access:\n");
        printf("    [+] Personal files and storage\n");
        printf("    [+] Personal AI assistant\n");
        printf("    [+] Personal applications\n");
        printf("    [-] Work resources - BLOCKED\n");
    } else {
        printf("  ┌─────────────────────────────────────────────┐\n");
        printf("  │  OFFICE MODE - Daily Productivity          │\n");
        printf("  └─────────────────────────────────────────────┘\n");
        printf("\n");
        printf("  Access Restrictions:\n");
        printf("    [-] AI tuning tools - BLOCKED\n");
        printf("    [-] Development syscalls - BLOCKED\n");
        printf("    [-] Debug features - BLOCKED\n");
        printf("    [+] Standard file/process ops - ENABLED\n");
    }

    printf("\n");
}

static void print_help(const char *prog)
{
    printf("\n");
    printf("VOS3 Workspace Switcher v2.0 (Phase 24)\n");
    printf("\n");
    printf("Usage: %s [COMMAND]\n", prog);
    printf("\n");
    printf("Commands:\n");
    printf("  status      Show current workspace status\n");
    printf("  workshop    Switch to WORKSHOP (Development) mode\n");
    printf("  office      Switch to OFFICE (Daily) mode\n");
    printf("  personal    Switch to PERSONAL mode (Hybrid only)\n");
    printf("  toggle      Toggle between WORKSHOP/OFFICE\n");
    printf("  help        Show this help message\n");
    printf("\n");
    printf("Workspaces:\n");
    printf("\n");
    printf("  WORKSHOP - Development Environment\n");
    printf("    Full access to AI tuning tools, development syscalls,\n");
    printf("    debug features, and raw device access.\n");
    printf("\n");
    printf("  OFFICE - Daily Productivity\n");
    printf("    Restricted environment for focus work. AI tuning,\n");
    printf("    development syscalls, and debug features are blocked\n");
    printf("    to ensure a stable, distraction-free experience.\n");
    printf("\n");
    printf("  PERSONAL - Private Space (Executive Hybrid only)\n");
    printf("    Personal workspace for non-work activities. Requires\n");
    printf("    admin permission in Executive Hybrid mode.\n");
    printf("\n");
    printf("Executive Hybrid Mode:\n");
    printf("  In this mode, workspace switching is controlled by admin\n");
    printf("  delegation policy. Use 'vos3_admin' to manage permissions.\n");
    printf("\n");
}

/* ============================================================================
 * WORKSPACE SWITCHING (with Phase 24 delegation enforcement)
 * ============================================================================ */

static int switch_workspace(vos3_config_t *config, uint32_t new_ws)
{
    if (config->workspace == new_ws) {
        printf("Already in %s mode.\n", workspace_name(new_ws));
        return 0;
    }

    /* Phase 24: Executive Hybrid Mode - enforce delegation policy */
    if (config->system_mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        /* Check if awaiting admin enrollment */
        if (is_awaiting_admin()) {
            printf("\n");
            printf("ERROR: System is awaiting admin enrollment.\n");
            printf("Contact your organization administrator to complete setup.\n");
            printf("\n");
            return EPERM;
        }

        /* Check workspace switch permission */
        if (!check_delegation_permission(VOS3_PERM_ALLOW_WORKSPACE_SWITCH)) {
            printf("\n");
            printf("ERROR: Workspace switching is disabled by admin policy.\n");
            printf("Contact your organization administrator for access.\n");
            printf("\n");
            return EPERM;
        }

        /* Check specific workspace permissions */
        if (new_ws == VOS3_WORKSPACE_PERSONAL) {
            if (!check_delegation_permission(VOS3_PERM_ALLOW_PERSONAL_SPACE)) {
                printf("\n");
                printf("================================================\n");
                printf("          ACCESS DENIED                         \n");
                printf("================================================\n");
                printf("\n");
                printf("  ERROR: Personal Space access is blocked by admin.\n");
                printf("\n");
                printf("  This device is managed in Executive Hybrid mode.\n");
                printf("  Personal Space access has not been granted by\n");
                printf("  your organization administrator.\n");
                printf("\n");
                printf("  Use 'vos3_admin status' to view permissions.\n");
                printf("\n");
                return EPERM;
            }
        }

        if (new_ws == VOS3_WORKSPACE_WORKSHOP) {
            if (!check_delegation_permission(VOS3_PERM_ALLOW_WORKSHOP)) {
                printf("\n");
                printf("================================================\n");
                printf("          ACCESS DENIED                         \n");
                printf("================================================\n");
                printf("\n");
                printf("  ERROR: Workshop access is blocked by admin.\n");
                printf("\n");
                printf("  This device is managed in Executive Hybrid mode.\n");
                printf("  Workshop access has not been granted by\n");
                printf("  your organization administrator.\n");
                printf("\n");
                printf("  Use 'vos3_admin status' to view permissions.\n");
                printf("\n");
                return EPERM;
            }
        }
    }

    uint32_t old_ws = config->workspace;
    config->workspace = new_ws;

    if (save_config(config) != 0) {
        printf("ERROR: Failed to save configuration!\n");
        config->workspace = old_ws;
        return -1;
    }

    printf("\n");
    printf("================================================\n");
    printf("          WORKSPACE SWITCHED                    \n");
    printf("================================================\n");
    printf("\n");
    printf("  From: %s\n", workspace_name(old_ws));
    printf("  To:   %s\n", workspace_name(new_ws));
    printf("\n");

    if (new_ws == VOS3_WORKSPACE_OFFICE) {
        printf("  OFFICE MODE ACTIVE:\n");
        printf("    - AI tuning tools are now BLOCKED\n");
        printf("    - Development syscalls are now BLOCKED\n");
        printf("    - Focus mode enabled for productivity\n");
    } else if (new_ws == VOS3_WORKSPACE_PERSONAL) {
        printf("  PERSONAL MODE ACTIVE:\n");
        printf("    - Personal files and storage ENABLED\n");
        printf("    - Personal AI assistant ENABLED\n");
        printf("    - Work resources now BLOCKED\n");
    } else {
        printf("  WORKSHOP MODE ACTIVE:\n");
        printf("    - All AI tuning tools ENABLED\n");
        printf("    - Development syscalls ENABLED\n");
        printf("    - Full development access restored\n");
    }

    printf("\n");

    return 0;
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char *argv[])
{
    vos3_config_t config;

    /* Load configuration */
    if (load_config(&config) != 0) {
        printf("ERROR: VOS3 is not provisioned.\n");
        printf("Run 'setup_wizard' first to configure the system.\n");
        return 1;
    }

    /* No arguments - show status */
    if (argc < 2) {
        print_status(&config);
        printf("Use '%s help' for usage information.\n\n", argv[0]);
        return 0;
    }

    /* Parse command */
    const char *cmd = argv[1];

    if (strcmp(cmd, "status") == 0 || strcmp(cmd, "-s") == 0) {
        print_status(&config);
        return 0;
    }

    if (strcmp(cmd, "workshop") == 0 || strcmp(cmd, "dev") == 0 ||
        strcmp(cmd, "-w") == 0) {
        return switch_workspace(&config, VOS3_WORKSPACE_WORKSHOP);
    }

    if (strcmp(cmd, "office") == 0 || strcmp(cmd, "daily") == 0 ||
        strcmp(cmd, "-o") == 0) {
        return switch_workspace(&config, VOS3_WORKSPACE_OFFICE);
    }

    if (strcmp(cmd, "personal") == 0 || strcmp(cmd, "private") == 0 ||
        strcmp(cmd, "--to-private") == 0 || strcmp(cmd, "-p") == 0) {
        /* Personal workspace only available in Executive Hybrid mode */
        if (config.system_mode != VOS3_SYSMODE_EXECUTIVE_HYBRID) {
            printf("ERROR: Personal workspace is only available in Executive Hybrid mode.\n");
            return 1;
        }
        return switch_workspace(&config, VOS3_WORKSPACE_PERSONAL);
    }

    if (strcmp(cmd, "toggle") == 0 || strcmp(cmd, "-t") == 0) {
        uint32_t new_ws = (config.workspace == VOS3_WORKSPACE_WORKSHOP) ?
                          VOS3_WORKSPACE_OFFICE : VOS3_WORKSPACE_WORKSHOP;
        return switch_workspace(&config, new_ws);
    }

    if (strcmp(cmd, "help") == 0 || strcmp(cmd, "-h") == 0 ||
        strcmp(cmd, "--help") == 0) {
        print_help(argv[0]);
        return 0;
    }

    printf("Unknown command: %s\n", cmd);
    printf("Use '%s help' for usage information.\n", argv[0]);
    return 1;
}
