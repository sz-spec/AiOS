"""Compile the production AI-context lifecycle and verify detach-before-free."""
from pathlib import Path
import subprocess
import tempfile
import unittest

if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class AIContextLifetimeTests(unittest.TestCase):
    def test_global_and_app_registry_detach_before_free(self):
        source = (ROOT / "kernel/src/mm/ai_guard.c").read_text()
        # Skip the forward declaration: only compile the actual definition.
        register = "static void register_global_ctx(vos3_ai_guard_ctx_t* ctx)\n{"
        actual = function(source, register) + "\n" + "\n".join(function(source, signature) for signature in (
            "vos3_ai_guard_ctx_t* vos3_ai_guard_ctx_create(",
            "void vos3_ai_guard_ctx_destroy(",
            "int vos3_ai_guard_create_app_ctx(",
            "int vos3_ai_guard_destroy_app_ctx(",
        ))
        code = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>

#define VOS3_MAX_APP_CONTEXTS 8U
#define VOS3_PAGE_SIZE 4096U
#define VOS3_AI_GUARD_MAX_REGIONS 64U
#define VOS3_AI_GUARD_OK 0
#define VOS3_AI_GUARD_ERR_INVALID (-1)
#define VOS3_AI_GUARD_ERR_NOMEM (-2)
#define VOS3_AI_GUARD_ERR_NOTFOUND (-3)
#define vos3_console_printf(...) ((void)0)

typedef struct vos3_ai_guard_region {
    struct vos3_ai_guard_region *next;
    uintptr_t base, guard_lo, guard_hi;
    size_t size;
} vos3_ai_guard_region_t;
typedef struct vos3_ai_guard_ctx {
    int lock;
    vos3_ai_guard_region_t *regions;
    size_t region_count, total_protected;
    uint64_t total_faults, total_violations;
    uint32_t flags;
    size_t max_regions, quota_limit, quota_used;
    int quota_enforce;
    uint64_t quota_violations;
} vos3_ai_guard_ctx_t;

static volatile int g_ai_guard_initialized = 1;
static struct {
    uint64_t total_contexts, total_regions, total_allocs, total_frees;
    uint64_t total_faults, total_violations;
} g_ai_guard_stats;
vos3_ai_guard_ctx_t *g_global_ctx;
vos3_ai_guard_ctx_t *g_app_contexts[VOS3_MAX_APP_CONTEXTS];
uint8_t g_active_app_id;
static int frees, region_frees;
static vos3_ai_guard_ctx_t *destroying;

static int vos3_ai_guard_init(void) { return 0; }
static void *vos3_kzalloc(size_t size) { return calloc(1, size); }
static int vos3_ai_guard_has_red_zones(vos3_ai_guard_region_t *region) {(void)region;return 0;}
static void remove_guard_pages(uintptr_t lo, uintptr_t hi) {(void)lo;(void)hi;}
static size_t align_to_page(size_t size) { return (size + 4095U) & ~4095U; }
static uintptr_t get_phys_addr(uintptr_t addr) {(void)addr;return 0;}
static void vos3_vmm_unmap(uintptr_t addr) {(void)addr;}
static void vos3_pmm_free(uintptr_t addr) {(void)addr;}
static void assert_detached(void *ptr) {
    assert(__atomic_load_n(&g_global_ctx, __ATOMIC_ACQUIRE) != ptr);
    for (unsigned i = 0; i < VOS3_MAX_APP_CONTEXTS; i++)
        assert(__atomic_load_n(&g_app_contexts[i], __ATOMIC_ACQUIRE) != ptr);
}
static void region_free(vos3_ai_guard_region_t *region) {
    assert_detached(destroying);
    region_frees++;
    free(region);
}
static void vos3_kfree(void *ptr) {
    assert_detached(ptr);
    frees++;
    free(ptr);
}
''' + actual + r'''

int main(void) {
    assert(vos3_ai_guard_create_app_ctx(7) == VOS3_AI_GUARD_OK);
    vos3_ai_guard_ctx_t *first = g_app_contexts[7];
    assert(first != NULL && g_global_ctx == first && g_ai_guard_stats.total_contexts == 1);
    assert(vos3_ai_guard_create_app_ctx(7) == VOS3_AI_GUARD_ERR_INVALID);
    first->regions = calloc(1, sizeof(*first->regions));
    assert(first->regions != NULL);
    first->regions->next = calloc(1, sizeof(*first->regions));
    assert(first->regions->next != NULL);
    destroying = first;
    g_active_app_id = 7;
    assert(vos3_ai_guard_destroy_app_ctx(7) == VOS3_AI_GUARD_OK);
    assert(g_app_contexts[7] == NULL && g_global_ctx == NULL);
    assert(g_active_app_id == 0 && g_ai_guard_stats.total_contexts == 0 && frees == 1);
    assert(region_frees == 2);
    assert(vos3_ai_guard_destroy_app_ctx(7) == VOS3_AI_GUARD_ERR_NOTFOUND);

    assert(vos3_ai_guard_create_app_ctx(3) == VOS3_AI_GUARD_OK);
    assert(vos3_ai_guard_create_app_ctx(4) == VOS3_AI_GUARD_OK);
    vos3_ai_guard_ctx_t *global = g_global_ctx;
    assert(global == g_app_contexts[3] && g_app_contexts[4] != global);
    assert(vos3_ai_guard_destroy_app_ctx(4) == VOS3_AI_GUARD_OK);
    assert(g_global_ctx == global);
    assert(vos3_ai_guard_destroy_app_ctx(3) == VOS3_AI_GUARD_OK);
    assert(g_global_ctx == NULL && g_ai_guard_stats.total_contexts == 0 && frees == 3);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-ai-context-lifetime-") as directory:
            path = Path(directory)
            (path / "test.c").write_text(code)
            build = subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 str(path / "test.c"), "-o", str(path / "test")],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(path / "test")], capture_output=True,
                                 text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
