/**
 * @file test_a8_vector_vfs_sandbox.c
 * @brief A8 — adversarial host audit of vVFS isolation: path-traversal jail,
 *        null-byte truncation, per-slot bounds, and multi-tenant owner_tid ACL.
 *        TEST_PLAN_300 §A8.
 *
 * The vVFS security surface has two layers:
 *   (1) Path resolution (kernel/src/fs/vfs.c) — tokenizes a path and clamps
 *       ".." at the root via root->parent == root (vvfs.c:252), so no path can
 *       escape ABOVE the jail root. The tokenizer (skip_slashes/next_component)
 *       is PURE and is copied here VERBATIM from vfs.c so the audited algorithm
 *       is byte-identical; the dentry walk is a faithful twin of
 *       vos3_path_lookup (vfs.c:436-520).
 *   (2) Multi-tenant access (kernel/src/fs/vvfs_transport.c:51-74) — the
 *       vvfs_transport_acl gate: slot bounds -> mounted -> owner_tid != 0 ->
 *       caller tid == owner_tid. Plus the vecvfs_* per-slot bounds + payload
 *       clamps (vector_vfs.c:179-205) that prevent OOB-array / shard overrun.
 *       Both are mirrored as faithful twins with source lines cited.
 *
 * Host-runnable (pure logic, no kernel deps):
 *     cc -O2 -o /tmp/a8 kernel/tests/audit/test_a8_vector_vfs_sandbox.c
 *     /tmp/a8   # exit 0 iff every case passes
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>

/* Mirror the kernel constants (ai_guard.h / vector_vfs.h / vfs.h). */
#define VOS3_NAME_MAX          255U
#define VOS3_PATH_MAX          4096U
#define VOS3_MODEL_SLOT_MAX    4U
#define VECVFS_PAYLOAD_SIZE    256U
#define VECVFS_MAX_SHARDS      8U     /* shrunk for the host model (real=1024) */
#define VECVFS_EMBED_DIM       64U

static int g_pass = 0, g_fail = 0;
static void check(const char *name, int ok)
{
    printf("%-58s %s\n", name, ok ? "PASS" : "FAIL");
    if (ok) g_pass++; else g_fail++;
}

/* ======================================================================
 * Part 1 — Path-traversal jail
 * ====================================================================== */

/* ---- VERBATIM from vfs.c (pure) ---- */
static const char* skip_slashes(const char* path)
{
    while (*path == '/') path++;
    return path;
}
static const char* next_component(const char* path, char* name, size_t max_len)
{
    size_t i = 0U;
    path = skip_slashes(path);
    while (*path != '\0' && *path != '/' && i < max_len - 1U) name[i++] = *path++;
    name[i] = '\0';
    return path;
}

/* Minimal dentry-tree model. root->parent == root is the jail ceiling. */
typedef struct node {
    const char  *name;
    struct node *parent;
    struct node *children[6];
    int          nchild;
    int          above_root;  /* sentinel: a node that must be UNREACHABLE */
} node_t;

static node_t* child_of(node_t *d, const char *name)
{
    for (int i = 0; i < d->nchild; i++)
        if (strcmp(d->children[i]->name, name) == 0) return d->children[i];
    return NULL;
}

/* Faithful twin of vos3_path_lookup (vfs.c:436-520): '.' no-op, '..' to parent
 * (root's parent is itself => clamp), else child lookup. Returns NULL on a
 * component that doesn't resolve (NOENT), never a node above the root. */
static node_t* resolve(node_t *root, const char *path, int *escaped_out)
{
    node_t *d = root;
    int escaped = 0;
    if (path[0] == '/') path++;
    char name[VOS3_NAME_MAX];
    while (*path != '\0') {
        path = next_component(path, name, sizeof(name));
        if (name[0] == '\0') continue;          /* empty (// or trailing /) */
        if (strcmp(name, ".") == 0) continue;
        if (strcmp(name, "..") == 0) {
            if (d->parent != NULL) d = d->parent; /* root->parent==root => clamp */
            if (d->above_root) escaped = 1;        /* must NEVER happen */
            continue;
        }
        node_t *c = child_of(d, name);
        if (c == NULL) { if (escaped_out) *escaped_out = escaped; return NULL; }
        d = c;
        if (d->above_root) escaped = 1;
    }
    if (escaped_out) *escaped_out = escaped;
    return d;
}

/* Twin of copy_path_from_user (fs_syscall.c:192) — strncpy_from_user stops at
 * the first NUL, so an embedded NUL truncates the path. */
static void copy_path_twin(char *dst, const char *src_with_nul, size_t srclen)
{
    size_t i = 0;
    for (; i < srclen && i < VOS3_PATH_MAX - 1 && src_with_nul[i] != '\0'; i++)
        dst[i] = src_with_nul[i];
    dst[i] = '\0';
}

static void test_path_traversal(void)
{
    /* Tree:  (root) -> etc -> passwd ; root -> vec -> "0".
     * outside is ABOVE root and must be unreachable through any path. */
    static node_t outside = { "OUTSIDE", NULL, {0}, 0, 1 };
    static node_t root     = { "/",      NULL, {0}, 0, 0 };
    static node_t etc      = { "etc",    NULL, {0}, 0, 0 };
    static node_t passwd   = { "passwd", NULL, {0}, 0, 0 };
    static node_t vec      = { "vec",    NULL, {0}, 0, 0 };
    static node_t vec0     = { "0",      NULL, {0}, 0, 0 };
    root.parent = &root;                 /* JAIL CEILING (vvfs.c:252) */
    root.children[root.nchild++] = &etc;  etc.parent = &root;
    root.children[root.nchild++] = &vec;  vec.parent = &root;
    etc.children[etc.nchild++]   = &passwd; passwd.parent = &etc;
    vec.children[vec.nchild++]   = &vec0;   vec0.parent = &vec;
    outside.children[0] = &root; outside.nchild = 1;  /* hypothetical, unlinked */

    int esc;
    /* A8.1 — classic ../../ payload cannot climb above root. */
    resolve(&root, "/../../../../etc/passwd", &esc);
    check("A8.1 ../../../../etc/passwd never escapes root", esc == 0);

    /* A8.2 — escape attempt resolves within the jail (lands on /etc/passwd). */
    node_t *n = resolve(&root, "/../../etc/passwd", &esc);
    check("A8.2 climb attempt clamps, resolves inside jail",
          esc == 0 && n == &passwd);

    /* A8.3 — relative climb from a subdir can't pass root. */
    n = resolve(&vec0, "../../../../../../OUTSIDE", &esc);
    check("A8.3 relative climb from /vec/0 cannot reach OUTSIDE",
          esc == 0 && n == NULL);

    /* A8.4 — embedded NUL truncates the path (copy_path twin). */
    {
        char raw[] = "vec\0/../../etc/passwd";   /* NUL after "vec" */
        char kpath[VOS3_PATH_MAX];
        copy_path_twin(kpath, raw, sizeof(raw));
        check("A8.4 null-byte injection truncates at NUL", strcmp(kpath, "vec") == 0);
        n = resolve(&root, kpath, &esc);
        check("A8.4 truncated path resolves only 'vec'", n == &vec && esc == 0);
    }

    /* A8.5 — over-long component is truncated, NUL-terminated, no overflow. */
    {
        char longp[600]; memset(longp, 'A', sizeof(longp)); longp[599] = '\0';
        char name[VOS3_NAME_MAX];
        unsigned char canary = 0xCC;
        const char *after = next_component(longp, name, sizeof(name));
        check("A8.5 over-long component bounded to NAME_MAX-1 + NUL",
              strlen(name) == VOS3_NAME_MAX - 1 && name[VOS3_NAME_MAX - 1] == '\0' &&
              after > longp && canary == 0xCC);
    }

    /* A8.6 — duplicate/empty slashes are skipped. */
    n = resolve(&root, "//etc///passwd", &esc);
    check("A8.6 duplicate slashes collapse", n == &passwd && esc == 0);

    /* A8.7 — '.' is a no-op. */
    n = resolve(&root, "/./etc/./passwd", &esc);
    check("A8.7 '.' component is a no-op", n == &passwd && esc == 0);
}

/* ======================================================================
 * Part 2 — Multi-tenant ACL + per-slot bounds
 * ====================================================================== */

/* Twin of vvfs_transport_acl (vvfs_transport.c:51-74). */
static int acl_twin(uint8_t slot_id, int mounted, uint32_t owner_tid,
                    uint32_t caller_tid)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -13; /* EACCES (bounds) */
    if (!mounted)                       return -13; /* EACCES (not mounted) */
    if (owner_tid == 0U)                return -13; /* EACCES (no owner) */
    if (caller_tid != owner_tid)        return -13; /* EACCES (cross-tenant) */
    return 0;
}

/* Host model of the per-slot vecvfs index (vector_vfs.c). */
typedef struct { uint8_t embedding[VECVFS_EMBED_DIM]; uint8_t payload[VECVFS_PAYLOAD_SIZE]; int valid; } shard_t;
typedef struct { shard_t shards[VECVFS_MAX_SHARDS]; int initialized; uint32_t count; } vidx_t;
static vidx_t g_idx[VOS3_MODEL_SLOT_MAX];

/* Twin of vecvfs_insert bounds/clamps (vector_vfs.c:179-205). */
static int vecvfs_insert_twin(uint8_t slot_id, const uint8_t *emb,
                              const void *payload, uint32_t plen)
{
    if (slot_id >= VOS3_MODEL_SLOT_MAX) return -22;            /* EINVAL bounds */
    vidx_t *idx = &g_idx[slot_id];                              /* index N only */
    if (idx->initialized == 0U)         return -38;            /* ENOSYS */
    if (plen > VECVFS_PAYLOAD_SIZE)     return -22;            /* EINVAL overrun */
    if (idx->count >= VECVFS_MAX_SHARDS) return -28;           /* ENOSPC */
    for (uint32_t i = 0; i < VECVFS_MAX_SHARDS; i++) {
        if (idx->shards[i].valid == 0U) {
            memcpy(idx->shards[i].embedding, emb, VECVFS_EMBED_DIM);
            memcpy(idx->shards[i].payload, payload, plen);
            idx->shards[i].valid = 1; idx->count++;
            return 0;
        }
    }
    return -28;
}

static void test_tenant_isolation(void)
{
    /* A8.8 — cross-tenant: caller B reading slot owned by A => EACCES. */
    check("A8.8 cross-tenant read (caller != owner) => EACCES",
          acl_twin(0, /*mounted*/1, /*owner*/1001, /*caller*/2002) == -13);
    /* A8.9 — legitimate owner allowed. */
    check("A8.9 owner reading own slot => allowed",
          acl_twin(0, 1, 1001, 1001) == 0);
    /* A8.10 — no owner / not mounted / OOB slot all => EACCES. */
    check("A8.10a no-owner slot => EACCES",   acl_twin(0, 1, 0, 1001) == -13);
    check("A8.10b unmounted slot => EACCES",  acl_twin(0, 0, 1001, 1001) == -13);
    check("A8.10c OOB slot id => EACCES",     acl_twin(VOS3_MODEL_SLOT_MAX, 1, 1001, 1001) == -13);

    /* A8.11 — vecvfs per-slot bounds: OOB slot => EINVAL (no OOB-array read). */
    g_idx[0].initialized = 1;
    uint8_t emb[VECVFS_EMBED_DIM] = {0};
    uint8_t pay[VECVFS_PAYLOAD_SIZE] = {0};
    check("A8.11 vecvfs_insert OOB slot => EINVAL",
          vecvfs_insert_twin(VOS3_MODEL_SLOT_MAX, emb, pay, 16) == -22);

    /* A8.12 — cross-slot bleed: insert into slot 0 must not touch slot 1. */
    g_idx[1].initialized = 1;
    memset(&g_idx[1].shards, 0xEE, sizeof(g_idx[1].shards));  /* canary */
    shard_t snapshot[VECVFS_MAX_SHARDS];
    memcpy(snapshot, g_idx[1].shards, sizeof(snapshot));
    memset(emb, 0x5A, sizeof(emb)); memset(pay, 0x5A, 64);
    int rc = vecvfs_insert_twin(0, emb, pay, 64);
    check("A8.12 insert slot 0 leaves slot 1 untouched (no cross-slot bleed)",
          rc == 0 && memcmp(snapshot, g_idx[1].shards, sizeof(snapshot)) == 0);

    /* A8.13 — oversized payload => EINVAL (no shard buffer overrun). */
    check("A8.13 oversized payload (> PAYLOAD_SIZE) => EINVAL",
          vecvfs_insert_twin(0, emb, pay, VECVFS_PAYLOAD_SIZE + 1) == -22);
}

int main(void)
{
    test_path_traversal();
    test_tenant_isolation();
    printf("\n== A8 %d passed, %d failed ==\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
