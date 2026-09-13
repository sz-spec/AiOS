/*
 * VOS3 Admin Management Tool
 * Phase 24: Executive Hybrid Mode with Admin Delegation
 *
 * This tool allows Organization Owners to manage delegation permissions
 * on behalf of other users in Executive Hybrid Mode.
 *
 * Usage:
 *   vos3_admin status              - Show current delegation status
 *   vos3_admin grant <perm>        - Grant a permission
 *   vos3_admin revoke <perm>       - Revoke a permission
 *   vos3_admin persona work|personal - Set active persona
 *   vos3_admin lock                - Lock policy changes
 *   vos3_admin unlock <token>      - Unlock with admin token
 *   vos3_admin audit               - Show delegation audit log
 *   vos3_admin init <token>        - Initialize delegation policy
 *
 * Version: 1.0.0
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"

/* ============================================================================
 * DELEGATION CONSTANTS (must match kernel vos3_config.h)
 * ============================================================================ */

#define VOS3_DELEGATION_MAGIC    0x44454C47    /* "DELG" */
#define VOS3_DELEGATION_VERSION  0x00010000

/* System modes */
#define VOS3_SYSMODE_UNSET             0x00
#define VOS3_SYSMODE_PRIVATE           0x01
#define VOS3_SYSMODE_ENTERPRISE        0x02
#define VOS3_SYSMODE_EXECUTIVE_HYBRID  0x03

/* Persona permissions */
#define VOS3_PERM_NONE                  0x00000000U
#define VOS3_PERM_ALLOW_PERSONAL_SPACE  0x00000001U
#define VOS3_PERM_ALLOW_PERSONAL_STORAGE 0x00000002U
#define VOS3_PERM_ALLOW_PERSONAL_AI     0x00000004U
#define VOS3_PERM_ALLOW_PERSONAL_APPS   0x00000008U
#define VOS3_PERM_ALLOW_WORKSHOP        0x00000010U
#define VOS3_PERM_ALLOW_AI_TUNING       0x00000020U
#define VOS3_PERM_ALLOW_CUSTOM_MODELS   0x00000040U
#define VOS3_PERM_ALLOW_WORKSPACE_SWITCH 0x00000100U
#define VOS3_PERM_ALLOW_MODE_QUERY      0x00000200U
#define VOS3_PERM_ALLOW_STATUS_VIEW     0x00000400U
#define VOS3_PERM_ALLOW_DELEGATION_VIEW 0x00001000U
#define VOS3_PERM_ALLOW_DELEGATION_MODIFY 0x00002000U

/* Active personas */
#define VOS3_PERSONA_NONE      0x00
#define VOS3_PERSONA_WORK      0x01
#define VOS3_PERSONA_PERSONAL  0x02

/* Delegation flags */
#define VOS3_DELEG_FLAG_LOCKED          0x0001
#define VOS3_DELEG_FLAG_AWAITING_ADMIN  0x0002
#define VOS3_DELEG_FLAG_ENROLLED        0x0004

/* ============================================================================
 * DELEGATION STRUCTURE (must match kernel vos3_config.h)
 * ============================================================================ */

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
 * SYSCALL NUMBERS (custom VOS3 syscalls)
 * ============================================================================ */

#define SYS_VOS3_CONFIG_GET      470
#define SYS_VOS3_CONFIG_SET      471
#define SYS_VOS3_DELEGATION_GET  472
#define SYS_VOS3_DELEGATION_SET  473
#define SYS_VOS3_ADMIN_AUTH      474

/* Inline syscall wrappers */
static inline long sys_vos3_config_get(int cmd, void* buf, size_t len)
{
    long ret;
    __asm__ volatile(
        "syscall"
        : "=a"(ret)
        : "a"(SYS_VOS3_CONFIG_GET), "D"(cmd), "S"(buf), "d"(len)
        : "rcx", "r11", "memory"
    );
    return ret;
}

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

static inline long sys_vos3_delegation_set(void* buf, size_t len, const void* token)
{
    long ret;
    __asm__ volatile(
        "syscall"
        : "=a"(ret)
        : "a"(SYS_VOS3_DELEGATION_SET), "D"(buf), "S"(len), "d"(token)
        : "rcx", "r11", "memory"
    );
    return ret;
}

static inline long sys_vos3_admin_auth(const void* token, size_t len)
{
    long ret;
    __asm__ volatile(
        "syscall"
        : "=a"(ret)
        : "a"(SYS_VOS3_ADMIN_AUTH), "D"(token), "S"(len)
        : "rcx", "r11", "memory"
    );
    return ret;
}

/* ============================================================================
 * PERMISSION TABLE
 * ============================================================================ */

typedef struct {
    const char* name;
    uint32_t    value;
    const char* description;
} perm_entry_t;

static const perm_entry_t g_permissions[] = {
    { "personal-space",   VOS3_PERM_ALLOW_PERSONAL_SPACE,   "Access personal workspace" },
    { "personal-storage", VOS3_PERM_ALLOW_PERSONAL_STORAGE, "Personal file storage" },
    { "personal-ai",      VOS3_PERM_ALLOW_PERSONAL_AI,      "Personal AI assistant" },
    { "personal-apps",    VOS3_PERM_ALLOW_PERSONAL_APPS,    "Personal applications" },
    { "workshop",         VOS3_PERM_ALLOW_WORKSHOP,         "Access AI workshop" },
    { "ai-tuning",        VOS3_PERM_ALLOW_AI_TUNING,        "Tune AI parameters" },
    { "custom-models",    VOS3_PERM_ALLOW_CUSTOM_MODELS,    "Use custom AI models" },
    { "workspace-switch", VOS3_PERM_ALLOW_WORKSPACE_SWITCH, "Switch workspaces" },
    { "mode-query",       VOS3_PERM_ALLOW_MODE_QUERY,       "Query system mode" },
    { "status-view",      VOS3_PERM_ALLOW_STATUS_VIEW,      "View system status" },
    { "delegation-view",  VOS3_PERM_ALLOW_DELEGATION_VIEW,  "View delegation policy" },
    { "delegation-modify",VOS3_PERM_ALLOW_DELEGATION_MODIFY,"Modify delegation policy" },
    { NULL, 0, NULL }
};

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

static uint32_t lookup_permission(const char* name)
{
    for (int i = 0; g_permissions[i].name != NULL; i++) {
        if (strcmp(name, g_permissions[i].name) == 0) {
            return g_permissions[i].value;
        }
    }
    return 0;
}

static const char* get_permission_name(uint32_t perm)
{
    for (int i = 0; g_permissions[i].name != NULL; i++) {
        if (g_permissions[i].value == perm) {
            return g_permissions[i].name;
        }
    }
    return "unknown";
}

static void print_permissions(uint32_t perms, const char* label)
{
    printf("%s:\n", label);
    if (perms == 0) {
        printf("  (none)\n");
        return;
    }

    for (int i = 0; g_permissions[i].name != NULL; i++) {
        if (perms & g_permissions[i].value) {
            printf("  [+] %-18s - %s\n",
                   g_permissions[i].name,
                   g_permissions[i].description);
        }
    }
}

static const char* persona_name(uint32_t persona)
{
    switch (persona) {
        case VOS3_PERSONA_WORK:     return "WORK";
        case VOS3_PERSONA_PERSONAL: return "PERSONAL";
        default:                    return "NONE";
    }
}

static void print_hex(const uint8_t* data, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        printf("%02x", data[i]);
        if (i < len - 1 && (i + 1) % 8 == 0) printf(" ");
    }
}

static int parse_hex_token(const char* hex, uint8_t* out, size_t len)
{
    size_t hex_len = strlen(hex);
    if (hex_len != len * 2) {
        return -1;
    }

    for (size_t i = 0; i < len; i++) {
        unsigned int val;
        char byte[3] = { hex[i*2], hex[i*2+1], '\0' };

        if (byte[0] >= '0' && byte[0] <= '9') val = (byte[0] - '0') << 4;
        else if (byte[0] >= 'a' && byte[0] <= 'f') val = (byte[0] - 'a' + 10) << 4;
        else if (byte[0] >= 'A' && byte[0] <= 'F') val = (byte[0] - 'A' + 10) << 4;
        else return -1;

        if (byte[1] >= '0' && byte[1] <= '9') val |= (byte[1] - '0');
        else if (byte[1] >= 'a' && byte[1] <= 'f') val |= (byte[1] - 'a' + 10);
        else if (byte[1] >= 'A' && byte[1] <= 'F') val |= (byte[1] - 'A' + 10);
        else return -1;

        out[i] = (uint8_t)val;
    }

    return 0;
}

/* Simple SHA256-like hash for admin token (simplified for embedded use) */
static void hash_token(const uint8_t* input, size_t len, uint8_t* output)
{
    /* Simple hash - XOR with position and rotate */
    memset(output, 0, 32);

    for (size_t i = 0; i < len; i++) {
        output[i % 32] ^= input[i];
        output[(i + 1) % 32] ^= (input[i] << 4) | (input[i] >> 4);
        output[(i + 7) % 32] ^= (input[i] << 2) | (input[i] >> 6);
    }

    /* Mix rounds */
    for (int round = 0; round < 16; round++) {
        for (int j = 0; j < 32; j++) {
            output[j] ^= output[(j + 13) % 32];
            output[j] = (output[j] << 3) | (output[j] >> 5);
        }
    }
}

static uint32_t calc_checksum(const vos3_delegation_t* d)
{
    uint32_t sum = 0;
    const uint8_t* p = (const uint8_t*)d;
    size_t len = sizeof(vos3_delegation_t) - sizeof(uint32_t);

    for (size_t i = 0; i < len; i++) {
        sum = (sum << 5) ^ (sum >> 27) ^ p[i];
    }

    return sum;
}

/* ============================================================================
 * COMMAND HANDLERS
 * ============================================================================ */

static int cmd_status(void)
{
    vos3_delegation_t deleg;
    long ret;

    printf("\n");
    printf("=======================================================\n");
    printf("  VOS3 DELEGATION STATUS\n");
    printf("=======================================================\n");
    printf("\n");

    ret = sys_vos3_delegation_get(&deleg, sizeof(deleg));
    if (ret < 0) {
        printf("ERROR: Failed to read delegation policy (err=%ld)\n", ret);
        printf("\n");
        printf("This may indicate:\n");
        printf("  - System is not in Executive Hybrid mode\n");
        printf("  - Delegation policy not yet initialized\n");
        printf("  - Insufficient permissions to read policy\n");
        printf("\n");
        return 1;
    }

    /* Verify magic */
    if (deleg.magic != VOS3_DELEGATION_MAGIC) {
        printf("ERROR: Invalid delegation policy (bad magic: 0x%08X)\n", deleg.magic);
        return 1;
    }

    /* Basic info */
    printf("Policy Version: %u.%u.%u\n",
           (deleg.version >> 16) & 0xFF,
           (deleg.version >> 8) & 0xFF,
           deleg.version & 0xFF);
    printf("\n");

    /* Active persona */
    printf("Active Persona: %s\n", persona_name(deleg.active_persona));
    printf("\n");

    /* Flags */
    printf("Policy Flags:\n");
    printf("  Locked:         %s\n", (deleg.flags & VOS3_DELEG_FLAG_LOCKED) ? "YES" : "NO");
    printf("  Awaiting Admin: %s\n", (deleg.flags & VOS3_DELEG_FLAG_AWAITING_ADMIN) ? "YES" : "NO");
    printf("  Enrolled:       %s\n", (deleg.flags & VOS3_DELEG_FLAG_ENROLLED) ? "YES" : "NO");
    printf("\n");

    /* Organization ID */
    printf("Organization ID: ");
    print_hex(deleg.org_id, 16);
    printf("\n");

    printf("User ID:         ");
    print_hex(deleg.user_id, 16);
    printf("\n\n");

    /* Permissions */
    print_permissions(deleg.granted_permissions, "GRANTED Permissions");
    printf("\n");
    print_permissions(deleg.denied_permissions, "DENIED Permissions");
    printf("\n");

    /* Modification info */
    printf("Modifications: %u changes\n", deleg.modification_count);
    printf("\n");

    /* Checksum */
    uint32_t expected = calc_checksum(&deleg);
    printf("Checksum: 0x%08X (%s)\n",
           deleg.checksum,
           (deleg.checksum == expected) ? "VALID" : "INVALID");
    printf("\n");

    return 0;
}

static int cmd_grant(const char* perm_name)
{
    uint32_t perm = lookup_permission(perm_name);
    if (perm == 0) {
        printf("ERROR: Unknown permission '%s'\n", perm_name);
        printf("\nAvailable permissions:\n");
        for (int i = 0; g_permissions[i].name != NULL; i++) {
            printf("  %-18s - %s\n",
                   g_permissions[i].name,
                   g_permissions[i].description);
        }
        return 1;
    }

    vos3_delegation_t deleg;
    long ret = sys_vos3_delegation_get(&deleg, sizeof(deleg));
    if (ret < 0) {
        printf("ERROR: Failed to read delegation policy\n");
        return 1;
    }

    if (deleg.flags & VOS3_DELEG_FLAG_LOCKED) {
        printf("ERROR: Policy is locked. Use 'vos3_admin unlock <token>' first.\n");
        return 1;
    }

    /* Grant permission */
    deleg.granted_permissions |= perm;
    deleg.denied_permissions &= ~perm;
    deleg.modification_count++;
    deleg.checksum = calc_checksum(&deleg);

    ret = sys_vos3_delegation_set(&deleg, sizeof(deleg), NULL);
    if (ret < 0) {
        printf("ERROR: Failed to update delegation policy (err=%ld)\n", ret);
        return 1;
    }

    printf("SUCCESS: Granted permission '%s'\n", perm_name);
    return 0;
}

static int cmd_revoke(const char* perm_name)
{
    uint32_t perm = lookup_permission(perm_name);
    if (perm == 0) {
        printf("ERROR: Unknown permission '%s'\n", perm_name);
        return 1;
    }

    vos3_delegation_t deleg;
    long ret = sys_vos3_delegation_get(&deleg, sizeof(deleg));
    if (ret < 0) {
        printf("ERROR: Failed to read delegation policy\n");
        return 1;
    }

    if (deleg.flags & VOS3_DELEG_FLAG_LOCKED) {
        printf("ERROR: Policy is locked. Use 'vos3_admin unlock <token>' first.\n");
        return 1;
    }

    /* Revoke permission */
    deleg.granted_permissions &= ~perm;
    deleg.denied_permissions |= perm;
    deleg.modification_count++;
    deleg.checksum = calc_checksum(&deleg);

    ret = sys_vos3_delegation_set(&deleg, sizeof(deleg), NULL);
    if (ret < 0) {
        printf("ERROR: Failed to update delegation policy (err=%ld)\n", ret);
        return 1;
    }

    printf("SUCCESS: Revoked permission '%s'\n", perm_name);
    return 0;
}

static int cmd_persona(const char* persona_str)
{
    uint32_t persona;

    if (strcmp(persona_str, "work") == 0) {
        persona = VOS3_PERSONA_WORK;
    } else if (strcmp(persona_str, "personal") == 0) {
        persona = VOS3_PERSONA_PERSONAL;
    } else {
        printf("ERROR: Invalid persona '%s'. Use 'work' or 'personal'.\n", persona_str);
        return 1;
    }

    vos3_delegation_t deleg;
    long ret = sys_vos3_delegation_get(&deleg, sizeof(deleg));
    if (ret < 0) {
        printf("ERROR: Failed to read delegation policy\n");
        return 1;
    }

    /* Check if personal persona is allowed */
    if (persona == VOS3_PERSONA_PERSONAL) {
        if (deleg.denied_permissions & VOS3_PERM_ALLOW_PERSONAL_SPACE) {
            printf("ERROR: Personal space access is denied by admin policy\n");
            return 1;
        }
        if (!(deleg.granted_permissions & VOS3_PERM_ALLOW_PERSONAL_SPACE)) {
            printf("ERROR: Personal space access has not been granted\n");
            return 1;
        }
    }

    deleg.active_persona = persona;
    deleg.modification_count++;
    deleg.checksum = calc_checksum(&deleg);

    ret = sys_vos3_delegation_set(&deleg, sizeof(deleg), NULL);
    if (ret < 0) {
        printf("ERROR: Failed to update persona (err=%ld)\n", ret);
        return 1;
    }

    printf("SUCCESS: Active persona set to %s\n", persona_name(persona));
    return 0;
}

static int cmd_lock(void)
{
    vos3_delegation_t deleg;
    long ret = sys_vos3_delegation_get(&deleg, sizeof(deleg));
    if (ret < 0) {
        printf("ERROR: Failed to read delegation policy\n");
        return 1;
    }

    if (deleg.flags & VOS3_DELEG_FLAG_LOCKED) {
        printf("Policy is already locked.\n");
        return 0;
    }

    deleg.flags |= VOS3_DELEG_FLAG_LOCKED;
    deleg.modification_count++;
    deleg.checksum = calc_checksum(&deleg);

    ret = sys_vos3_delegation_set(&deleg, sizeof(deleg), NULL);
    if (ret < 0) {
        printf("ERROR: Failed to lock policy\n");
        return 1;
    }

    printf("SUCCESS: Policy locked. Admin token required for changes.\n");
    return 0;
}

static int cmd_unlock(const char* token_hex)
{
    uint8_t token[32];

    if (strlen(token_hex) != 64) {
        printf("ERROR: Token must be 64 hex characters (256-bit)\n");
        return 1;
    }

    if (parse_hex_token(token_hex, token, 32) != 0) {
        printf("ERROR: Invalid hex token format\n");
        return 1;
    }

    long ret = sys_vos3_admin_auth(token, 32);
    if (ret < 0) {
        printf("ERROR: Authentication failed (err=%ld)\n", ret);
        return 1;
    }

    vos3_delegation_t deleg;
    ret = sys_vos3_delegation_get(&deleg, sizeof(deleg));
    if (ret < 0) {
        printf("ERROR: Failed to read delegation policy\n");
        return 1;
    }

    deleg.flags &= ~VOS3_DELEG_FLAG_LOCKED;
    deleg.modification_count++;
    deleg.checksum = calc_checksum(&deleg);

    ret = sys_vos3_delegation_set(&deleg, sizeof(deleg), token);
    if (ret < 0) {
        printf("ERROR: Failed to unlock policy\n");
        return 1;
    }

    printf("SUCCESS: Policy unlocked.\n");
    return 0;
}

static int cmd_init(const char* token_hex)
{
    uint8_t token[32];
    uint8_t token_hash[32];

    if (strlen(token_hex) != 64) {
        printf("ERROR: Token must be 64 hex characters (256-bit)\n");
        return 1;
    }

    if (parse_hex_token(token_hex, token, 32) != 0) {
        printf("ERROR: Invalid hex token format\n");
        return 1;
    }

    /* Hash the token */
    hash_token(token, 32, token_hash);

    /* Create new delegation policy */
    vos3_delegation_t deleg;
    memset(&deleg, 0, sizeof(deleg));

    deleg.magic = VOS3_DELEGATION_MAGIC;
    deleg.version = VOS3_DELEGATION_VERSION;

    /* Default: work persona with basic permissions */
    deleg.active_persona = VOS3_PERSONA_WORK;
    deleg.granted_permissions = VOS3_PERM_ALLOW_MODE_QUERY |
                                VOS3_PERM_ALLOW_STATUS_VIEW |
                                VOS3_PERM_ALLOW_WORKSPACE_SWITCH;
    deleg.denied_permissions = 0;

    /* Store hashed token */
    memcpy(deleg.admin_token_hash, token_hash, 32);

    /* Mark as enrolled */
    deleg.flags = VOS3_DELEG_FLAG_ENROLLED;
    deleg.modification_count = 1;

    /* Calculate checksum */
    deleg.checksum = calc_checksum(&deleg);

    long ret = sys_vos3_delegation_set(&deleg, sizeof(deleg), token);
    if (ret < 0) {
        printf("ERROR: Failed to initialize delegation policy (err=%ld)\n", ret);
        return 1;
    }

    printf("SUCCESS: Delegation policy initialized.\n");
    printf("\n");
    printf("Default permissions granted:\n");
    printf("  - mode-query\n");
    printf("  - status-view\n");
    printf("  - workspace-switch\n");
    printf("\n");
    printf("Use 'vos3_admin grant <perm>' to add more permissions.\n");
    printf("Use 'vos3_admin lock' to protect the policy.\n");

    return 0;
}

static int cmd_audit(void)
{
    printf("\n");
    printf("=======================================================\n");
    printf("  VOS3 DELEGATION AUDIT LOG\n");
    printf("=======================================================\n");
    printf("\n");

    /* In a full implementation, this would read from kernel audit log */
    printf("Audit logging not yet implemented in this version.\n");
    printf("Modification count is available via 'vos3_admin status'.\n");
    printf("\n");

    return 0;
}

static void print_usage(const char* prog)
{
    printf("\n");
    printf("VOS3 Admin Management Tool v1.0.0\n");
    printf("Phase 24: Executive Hybrid Mode with Admin Delegation\n");
    printf("\n");
    printf("Usage: %s <command> [args]\n", prog);
    printf("\n");
    printf("Commands:\n");
    printf("  status              Show current delegation status\n");
    printf("  grant <perm>        Grant a permission to user\n");
    printf("  revoke <perm>       Revoke a permission from user\n");
    printf("  persona work|personal  Set active persona\n");
    printf("  lock                Lock policy (require token for changes)\n");
    printf("  unlock <token>      Unlock policy with admin token\n");
    printf("  init <token>        Initialize delegation policy\n");
    printf("  audit               Show delegation audit log\n");
    printf("\n");
    printf("Permissions:\n");
    for (int i = 0; g_permissions[i].name != NULL; i++) {
        printf("  %-18s - %s\n",
               g_permissions[i].name,
               g_permissions[i].description);
    }
    printf("\n");
    printf("Examples:\n");
    printf("  %s status\n", prog);
    printf("  %s grant personal-space\n", prog);
    printf("  %s revoke workshop\n", prog);
    printf("  %s persona work\n", prog);
    printf("  %s init <64-char-hex-token>\n", prog);
    printf("\n");
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(int argc, char** argv)
{
    if (argc < 2) {
        print_usage(argv[0]);
        return 1;
    }

    const char* cmd = argv[1];

    if (strcmp(cmd, "status") == 0) {
        return cmd_status();
    }
    else if (strcmp(cmd, "grant") == 0) {
        if (argc < 3) {
            printf("ERROR: 'grant' requires a permission name\n");
            return 1;
        }
        return cmd_grant(argv[2]);
    }
    else if (strcmp(cmd, "revoke") == 0) {
        if (argc < 3) {
            printf("ERROR: 'revoke' requires a permission name\n");
            return 1;
        }
        return cmd_revoke(argv[2]);
    }
    else if (strcmp(cmd, "persona") == 0) {
        if (argc < 3) {
            printf("ERROR: 'persona' requires 'work' or 'personal'\n");
            return 1;
        }
        return cmd_persona(argv[2]);
    }
    else if (strcmp(cmd, "lock") == 0) {
        return cmd_lock();
    }
    else if (strcmp(cmd, "unlock") == 0) {
        if (argc < 3) {
            printf("ERROR: 'unlock' requires admin token\n");
            return 1;
        }
        return cmd_unlock(argv[2]);
    }
    else if (strcmp(cmd, "init") == 0) {
        if (argc < 3) {
            printf("ERROR: 'init' requires admin token\n");
            return 1;
        }
        return cmd_init(argv[2]);
    }
    else if (strcmp(cmd, "audit") == 0) {
        return cmd_audit();
    }
    else if (strcmp(cmd, "help") == 0 || strcmp(cmd, "-h") == 0 || strcmp(cmd, "--help") == 0) {
        print_usage(argv[0]);
        return 0;
    }
    else {
        printf("ERROR: Unknown command '%s'\n", cmd);
        print_usage(argv[0]);
        return 1;
    }
}
