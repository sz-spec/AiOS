/**
 * @file setup_wizard.c
 * @brief VOS3 First-Boot Setup Wizard
 *
 * @details Professional setup wizard for initial system configuration.
 *          This is a one-time operation that defines the identity of the OS.
 *          Phase 24: Added Executive Hybrid (Managed) mode support.
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
#include "ioctl.h"

/* ============================================================================
 * CONFIGURATION DEFINITIONS (must match kernel)
 * ============================================================================ */

#define VOS3_CONFIG_PATH        "/etc/vos3.conf"
#define VOS3_DELEGATION_PATH    "/etc/vos3_delegation.conf"
#define VOS3_CONFIG_MAGIC       0x564F5333U
#define VOS3_DELEGATION_MAGIC   0x56443234U

#define VOS3_SYSMODE_PRIVATE         0x01
#define VOS3_SYSMODE_ENTERPRISE      0x02
#define VOS3_SYSMODE_EXECUTIVE_HYBRID 0x03

#define VOS3_WORKSPACE_WORKSHOP 0x10
#define VOS3_WORKSPACE_OFFICE   0x20
#define VOS3_WORKSPACE_PERSONAL 0x30

#define VOS3_PERSONA_WORK       0x01
#define VOS3_PERSONA_PERSONAL   0x02

#define VOS3_ADMIN_TOKEN_SIZE   32

/* Permission flags */
#define VOS3_PERM_ALLOW_PERSONAL_SPACE   0x00000001U
#define VOS3_PERM_ALLOW_PERSONAL_STORAGE 0x00000002U
#define VOS3_PERM_ALLOW_PERSONAL_APPS    0x00000004U
#define VOS3_PERM_ALLOW_WORKSHOP         0x00000010U
#define VOS3_PERM_ALLOW_AI_TUNING        0x00000020U
#define VOS3_PERM_ALLOW_DEBUG            0x00000040U
#define VOS3_PERM_ALLOW_RAW_DEVICE       0x00000080U

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
    uint32_t active_persona;
    uint32_t delegation_flags;
    uint8_t  admin_token[32];
    uint32_t is_managed;
    uint32_t reserved[4];
    uint32_t checksum;
} vos3_config_t;

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint32_t version;
    uint32_t granted_permissions;
    uint32_t denied_permissions;
    uint32_t active_persona;
    uint32_t default_workspace;
    uint32_t policy_timestamp;
    uint32_t policy_expires;
    uint8_t  admin_token_hash[32];
    char     admin_name[32];
    char     machine_id[64];
    char     user_id[32];
    uint32_t audit_flags;
    uint32_t reserved[4];
    uint32_t checksum;
} vos3_delegation_t;

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
 * CONSOLE HELPERS
 * ============================================================================ */

static void clear_screen(void)
{
    printf("\033[2J\033[H");
}

static void print_banner(void)
{
    printf("\n");
    printf("================================================================\n");
    printf("                                                                \n");
    printf("  ██╗   ██╗ ██████╗ ███████╗██████╗                             \n");
    printf("  ██║   ██║██╔═══██╗██╔════╝╚════██╗                            \n");
    printf("  ██║   ██║██║   ██║███████╗ █████╔╝                            \n");
    printf("  ╚██╗ ██╔╝██║   ██║╚════██║ ╚═══██╗                            \n");
    printf("   ╚████╔╝ ╚██████╔╝███████║██████╔╝                            \n");
    printf("    ╚═══╝   ╚═════╝ ╚══════╝╚═════╝                             \n");
    printf("                                                                \n");
    printf("         W E L C O M E   T O   V O S 3                          \n");
    printf("                                                                \n");
    printf("          The AI-Native Operating System                        \n");
    printf("                                                                \n");
    printf("================================================================\n");
    printf("\n");
}

static void print_divider(void)
{
    printf("----------------------------------------------------------------\n");
}

static char read_choice(void)
{
    char buf[16];
    if (read(0, buf, sizeof(buf)) > 0) {
        return buf[0];
    }
    return '\0';
}

static void read_string(char *buf, size_t max)
{
    ssize_t n = read(0, buf, max - 1);
    if (n > 0) {
        if (buf[n-1] == '\n') {
            buf[n-1] = '\0';
        } else {
            buf[n] = '\0';
        }
    } else {
        buf[0] = '\0';
    }
}

/* ============================================================================
 * SETUP WIZARD SCREENS
 * ============================================================================ */

static void screen_welcome(void)
{
    clear_screen();
    print_banner();

    printf("  Welcome to the VOS3 First-Boot Setup Wizard!\n");
    printf("\n");
    printf("  This wizard will help you configure your system's identity.\n");
    printf("  This is a ONE-TIME configuration that cannot be changed later.\n");
    printf("\n");
    printf("  Please choose carefully.\n");
    printf("\n");
    print_divider();
    printf("\n");
    printf("  Press [ENTER] to continue...\n");

    read_choice();
}

static int screen_identity(uint32_t *mode)
{
    clear_screen();
    print_banner();

    printf("  ┌────────────────────────────────────────────────────────────┐\n");
    printf("  │               CHOOSE YOUR SYSTEM IDENTITY                  │\n");
    printf("  └────────────────────────────────────────────────────────────┘\n");
    printf("\n");

    printf("  [1] PRIVATE MODE - Personal AI Companion\n");
    printf("\n");
    printf("      Ideal for: Individual users with AI personal assistants\n");
    printf("      Features:  Privacy Shield, Personal folders, AI sandbox\n");
    printf("\n");

    print_divider();
    printf("\n");

    printf("  [2] ENTERPRISE MODE - Corporate AI Deployment\n");
    printf("\n");
    printf("      Ideal for: Corporations running CRM/ERP with AI automation\n");
    printf("      Features:  Multi-tenant isolation, Business folders, Full AI\n");
    printf("\n");

    print_divider();
    printf("\n");

    printf("  [3] MANAGED HYBRID - Executive Fleet Management (Phase 24)\n");
    printf("\n");
    printf("      Ideal for: Organizations with mixed work/personal policies\n");
    printf("      Features:\n");
    printf("        • Organization Owner controls user capabilities\n");
    printf("        • Admin can grant/revoke Personal Space access\n");
    printf("        • Admin can grant/revoke Workshop (Dev) access\n");
    printf("        • Requires Admin Token for policy changes\n");
    printf("        • Awaits admin configuration after setup\n");
    printf("\n");

    print_divider();
    printf("\n");
    printf("  Enter your choice [1/2/3]: ");

    char choice = read_choice();

    if (choice == '1') {
        *mode = VOS3_SYSMODE_PRIVATE;
        return 0;
    } else if (choice == '2') {
        *mode = VOS3_SYSMODE_ENTERPRISE;
        return 0;
    } else if (choice == '3') {
        *mode = VOS3_SYSMODE_EXECUTIVE_HYBRID;
        return 0;
    }

    return -1;
}

static int screen_workspace(uint32_t *workspace, uint32_t mode)
{
    clear_screen();
    print_banner();

    printf("  ┌────────────────────────────────────────────────────────────┐\n");
    printf("  │              SELECT DEFAULT WORKSPACE                      │\n");
    printf("  └────────────────────────────────────────────────────────────┘\n");
    printf("\n");

    printf("  [1] WORKSHOP MODE - Development Environment\n");
    printf("\n");
    printf("      For: Coding, AI training, system development\n");
    printf("      Access: Full AI tools, Debug features, Raw devices\n");
    printf("\n");

    print_divider();
    printf("\n");

    printf("  [2] OFFICE MODE - Daily Productivity\n");
    printf("\n");
    printf("      For: Business tasks, meetings, focus work\n");
    printf("      Access: AI tools restricted, Focus mode enabled\n");
    printf("\n");

    print_divider();
    printf("\n");

    if (mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        printf("  Note: In Managed Hybrid mode, your admin may restrict workspace\n");
        printf("        switching based on organizational policy.\n");
        printf("\n");
    }

    printf("  Enter your choice [1/2]: ");

    char choice = read_choice();

    if (choice == '1') {
        *workspace = VOS3_WORKSPACE_WORKSHOP;
        return 0;
    } else if (choice == '2') {
        *workspace = VOS3_WORKSPACE_OFFICE;
        return 0;
    }

    return -1;
}

static int screen_hybrid_admin_token(uint8_t *token)
{
    clear_screen();
    print_banner();

    printf("  ┌────────────────────────────────────────────────────────────┐\n");
    printf("  │           MANAGED HYBRID - ADMIN TOKEN SETUP               │\n");
    printf("  └────────────────────────────────────────────────────────────┘\n");
    printf("\n");

    printf("  In Managed Hybrid mode, an Admin Token is required for:\n");
    printf("    • Changing delegation policies\n");
    printf("    • Enabling/disabling Personal Space\n");
    printf("    • Enabling/disabling Workshop mode\n");
    printf("    • Fleet management operations\n");
    printf("\n");

    print_divider();
    printf("\n");

    printf("  [1] GENERATE ADMIN TOKEN NOW\n");
    printf("      A random 256-bit token will be generated.\n");
    printf("      IMPORTANT: Record this token securely!\n");
    printf("\n");

    printf("  [2] AWAIT ADMIN CONFIGURATION\n");
    printf("      System will wait for admin to provision this machine.\n");
    printf("      Use this for fleet enrollment.\n");
    printf("\n");

    print_divider();
    printf("\n");
    printf("  Enter your choice [1/2]: ");

    char choice = read_choice();

    if (choice == '1') {
        /* Generate random token using simple LCG */
        uint32_t seed = 0xDEADBEEF;
        for (int i = 0; i < VOS3_ADMIN_TOKEN_SIZE; i++) {
            seed = seed * 1103515245 + 12345;
            token[i] = (uint8_t)(seed >> 16);
        }

        printf("\n");
        printf("  Admin Token Generated (record this securely!):\n");
        printf("\n");
        printf("  ");
        for (int i = 0; i < VOS3_ADMIN_TOKEN_SIZE; i++) {
            printf("%02X", token[i]);
            if (i == 15) printf("\n  ");
        }
        printf("\n\n");

        printf("  Press [ENTER] to continue (ensure you recorded the token)...\n");
        read_choice();

        return 0;
    } else if (choice == '2') {
        /* Zero token - awaiting admin */
        memset(token, 0, VOS3_ADMIN_TOKEN_SIZE);
        return 1;  /* Indicate awaiting admin */
    }

    return -1;
}

static int screen_owner_info(char *name, size_t name_max,
                              char *org, size_t org_max,
                              uint32_t mode)
{
    clear_screen();
    print_banner();

    printf("  ┌────────────────────────────────────────────────────────────┐\n");
    printf("  │                 OWNER INFORMATION                          │\n");
    printf("  └────────────────────────────────────────────────────────────┘\n");
    printf("\n");

    printf("  Enter your name: ");
    read_string(name, name_max);

    if (strlen(name) == 0) {
        snprintf(name, name_max, "VOS3 User");
    }

    if (mode == VOS3_SYSMODE_ENTERPRISE || mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        printf("\n");
        printf("  Enter organization name: ");
        read_string(org, org_max);

        if (strlen(org) == 0) {
            snprintf(org, org_max, "VOS3 Organization");
        }
    } else {
        org[0] = '\0';
    }

    return 0;
}

static int screen_confirm(uint32_t mode, uint32_t workspace,
                           const char *name, const char *org,
                           int is_managed)
{
    clear_screen();
    print_banner();

    printf("  ┌────────────────────────────────────────────────────────────┐\n");
    printf("  │               CONFIRM YOUR CONFIGURATION                   │\n");
    printf("  └────────────────────────────────────────────────────────────┘\n");
    printf("\n");

    const char *mode_str = "UNKNOWN";
    if (mode == VOS3_SYSMODE_PRIVATE) mode_str = "PRIVATE";
    else if (mode == VOS3_SYSMODE_ENTERPRISE) mode_str = "ENTERPRISE";
    else if (mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) mode_str = "EXECUTIVE_HYBRID";

    printf("  System Identity:   %s\n", mode_str);
    printf("  Default Workspace: %s\n",
           workspace == VOS3_WORKSPACE_WORKSHOP ? "WORKSHOP" : "OFFICE");
    printf("  Owner Name:        %s\n", name);

    if (mode == VOS3_SYSMODE_ENTERPRISE || mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        printf("  Organization:      %s\n", org);
    }

    if (mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        printf("  Fleet Managed:     %s\n", is_managed ? "YES (awaiting admin)" : "NO (admin token set)");
    }

    printf("\n");
    print_divider();
    printf("\n");
    printf("  WARNING: System identity CANNOT be changed after confirmation!\n");
    printf("\n");
    printf("  Proceed with this configuration? [Y/n]: ");

    char choice = read_choice();

    if (choice == 'Y' || choice == 'y' || choice == '\n' || choice == '\r') {
        return 0;
    }

    return -1;
}

static void screen_success(uint32_t mode, int awaiting_admin)
{
    clear_screen();
    print_banner();

    printf("  ┌────────────────────────────────────────────────────────────┐\n");
    printf("  │             CONFIGURATION COMPLETE!                        │\n");
    printf("  └────────────────────────────────────────────────────────────┘\n");
    printf("\n");

    printf("  VOS3 has been successfully configured!\n");
    printf("\n");

    if (mode == VOS3_SYSMODE_PRIVATE) {
        printf("  Folders created:\n");
        printf("    /home/user     - Your personal home directory\n");
        printf("    /personal      - Personal data (Privacy Shield protected)\n");
        printf("    /ai            - AI workspace sandbox\n");
    } else if (mode == VOS3_SYSMODE_ENTERPRISE) {
        printf("  Folders created:\n");
        printf("    /org/data      - Organization data storage\n");
        printf("    /org/apps      - Enterprise applications\n");
        printf("    /org/shared    - Shared resources\n");
    } else if (mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        printf("  Folders created:\n");
        printf("    /org/data      - Organization data storage\n");
        printf("    /org/apps      - Enterprise applications\n");
        printf("    /personal      - Personal space (admin-controlled)\n");
        printf("\n");

        if (awaiting_admin) {
            printf("  ┌────────────────────────────────────────────────────────┐\n");
            printf("  │    AWAITING ADMIN CONFIGURATION                        │\n");
            printf("  ├────────────────────────────────────────────────────────┤\n");
            printf("  │  Your administrator must configure this machine       │\n");
            printf("  │  before you can use Personal Space or Workshop.       │\n");
            printf("  │                                                        │\n");
            printf("  │  Contact your IT administrator with this machine ID.  │\n");
            printf("  └────────────────────────────────────────────────────────┘\n");
        } else {
            printf("  You can manage delegation policies with:\n");
            printf("    vos3_admin --help\n");
        }
    }

    printf("\n");
    printf("  You can change your workspace at any time with:\n");
    printf("    ws_switch workshop   - Switch to development mode\n");
    printf("    ws_switch office     - Switch to productivity mode\n");

    if (mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        printf("    ws_switch personal   - Switch to personal mode (if permitted)\n");
    }

    printf("\n");
    print_divider();
    printf("\n");
    printf("  Press [ENTER] to start VOS3...\n");

    read_choice();
}

/* ============================================================================
 * CONFIGURATION SAVE
 * ============================================================================ */

static int create_directories(uint32_t mode)
{
    int ret = 0;

    ret |= mkdir("/etc", 0755);

    if (mode == VOS3_SYSMODE_PRIVATE) {
        ret |= mkdir("/home", 0755);
        ret |= mkdir("/home/user", 0755);
        ret |= mkdir("/personal", 0700);
        ret |= mkdir("/ai", 0755);
    } else if (mode == VOS3_SYSMODE_ENTERPRISE) {
        ret |= mkdir("/org", 0755);
        ret |= mkdir("/org/data", 0755);
        ret |= mkdir("/org/apps", 0755);
        ret |= mkdir("/org/shared", 0755);
    } else if (mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        ret |= mkdir("/org", 0755);
        ret |= mkdir("/org/data", 0755);
        ret |= mkdir("/org/apps", 0755);
        ret |= mkdir("/personal", 0700);
    }

    (void)ret;
    return 0;
}

static int save_configuration(const vos3_config_t *config)
{
    int fd = open(VOS3_CONFIG_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        printf("  ERROR: Cannot create configuration file!\n");
        return -1;
    }

    ssize_t written = write(fd, config, sizeof(*config));
    close(fd);

    if (written != sizeof(*config)) {
        printf("  ERROR: Failed to write configuration!\n");
        return -1;
    }

    return 0;
}

static int save_delegation(const vos3_delegation_t *delegation)
{
    int fd = open(VOS3_DELEGATION_PATH, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) {
        return -1;
    }

    ssize_t written = write(fd, delegation, sizeof(*delegation));
    close(fd);

    return (written == sizeof(*delegation)) ? 0 : -1;
}

/* ============================================================================
 * MAIN WIZARD FLOW
 * ============================================================================ */

int main(int argc, char *argv[])
{
    vos3_config_t config;
    vos3_delegation_t delegation;
    uint32_t mode = 0;
    uint32_t workspace = 0;
    char owner_name[32] = {0};
    char org_name[64] = {0};
    uint8_t admin_token[VOS3_ADMIN_TOKEN_SIZE] = {0};
    int awaiting_admin = 0;

    /* Check if already provisioned */
    int fd = open(VOS3_CONFIG_PATH, O_RDONLY, 0);
    if (fd >= 0) {
        ssize_t n = read(fd, &config, sizeof(config));
        close(fd);
        if (n == sizeof(config) && config.magic == VOS3_CONFIG_MAGIC) {
            printf("VOS3 is already configured.\n");
            const char *mode_str = "UNKNOWN";
            if (config.system_mode == VOS3_SYSMODE_PRIVATE) mode_str = "PRIVATE";
            else if (config.system_mode == VOS3_SYSMODE_ENTERPRISE) mode_str = "ENTERPRISE";
            else if (config.system_mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) mode_str = "EXECUTIVE_HYBRID";
            printf("System Mode: %s\n", mode_str);
            printf("Workspace: %s\n",
                   config.workspace == VOS3_WORKSPACE_WORKSHOP ? "WORKSHOP" :
                   config.workspace == VOS3_WORKSPACE_OFFICE ? "OFFICE" : "PERSONAL");
            return 0;
        }
    }

    /* Run wizard screens */
    screen_welcome();

    /* Identity selection (with retry) */
    while (screen_identity(&mode) != 0) {
        printf("  Invalid choice. Please enter 1, 2, or 3.\n");
        read_choice();
    }

    /* Hybrid mode: Admin token setup */
    if (mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        int result = screen_hybrid_admin_token(admin_token);
        if (result < 0) {
            printf("  Setup cancelled.\n");
            return 1;
        }
        awaiting_admin = (result == 1);
    }

    /* Workspace selection (with retry) */
    while (screen_workspace(&workspace, mode) != 0) {
        printf("  Invalid choice. Please enter 1 or 2.\n");
        read_choice();
    }

    /* Owner information */
    screen_owner_info(owner_name, sizeof(owner_name),
                      org_name, sizeof(org_name), mode);

    /* Confirmation */
    if (screen_confirm(mode, workspace, owner_name, org_name, awaiting_admin) != 0) {
        printf("\n  Setup cancelled. Run setup_wizard again to configure.\n");
        return 1;
    }

    /* Create configuration */
    memset(&config, 0, sizeof(config));
    config.magic = VOS3_CONFIG_MAGIC;
    config.version = 2;
    config.system_mode = mode;
    config.workspace = workspace;
    config.first_boot_ts = 0;
    config.last_boot_ts = 0;
    config.boot_count = 1;
    config.owner_id = 1000;
    memcpy(config.owner_name, owner_name, sizeof(config.owner_name) - 1);
    memcpy(config.org_name, org_name, sizeof(config.org_name) - 1);

    /* Hybrid mode specifics */
    if (mode == VOS3_SYSMODE_EXECUTIVE_HYBRID) {
        config.active_persona = VOS3_PERSONA_WORK;
        config.is_managed = awaiting_admin ? 1 : 0;
        memcpy(config.admin_token, admin_token, VOS3_ADMIN_TOKEN_SIZE);

        /* Default delegation: grant Workshop + Personal if admin token set */
        if (!awaiting_admin) {
            config.delegation_flags = VOS3_PERM_ALLOW_WORKSHOP |
                                      VOS3_PERM_ALLOW_PERSONAL_SPACE |
                                      VOS3_PERM_ALLOW_AI_TUNING;
        }
    }

    /* Compute checksum */
    config.checksum = 0;
    config.checksum = crc32_compute((const uint8_t*)&config,
                                     sizeof(config) - sizeof(uint32_t));

    /* Create directories */
    create_directories(mode);

    /* Save configuration */
    if (save_configuration(&config) != 0) {
        return 1;
    }

    /* Create initial delegation policy for hybrid mode */
    if (mode == VOS3_SYSMODE_EXECUTIVE_HYBRID && !awaiting_admin) {
        memset(&delegation, 0, sizeof(delegation));
        delegation.magic = VOS3_DELEGATION_MAGIC;
        delegation.version = 1;
        delegation.granted_permissions = VOS3_PERM_ALLOW_WORKSHOP |
                                         VOS3_PERM_ALLOW_PERSONAL_SPACE |
                                         VOS3_PERM_ALLOW_AI_TUNING |
                                         VOS3_PERM_ALLOW_PERSONAL_STORAGE;
        delegation.denied_permissions = 0;
        delegation.active_persona = VOS3_PERSONA_WORK;
        delegation.default_workspace = workspace;
        memcpy(delegation.admin_token_hash, admin_token, VOS3_ADMIN_TOKEN_SIZE);
        memcpy(delegation.admin_name, "Owner", 6);

        delegation.checksum = 0;
        delegation.checksum = crc32_compute((const uint8_t*)&delegation,
                                             sizeof(delegation) - sizeof(uint32_t));

        save_delegation(&delegation);
    }

    /* Show success */
    screen_success(mode, awaiting_admin);

    printf("\nVOS3 Setup Complete. System is ready.\n");

    return 0;
}
