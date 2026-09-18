"""Run the production KV allocate/free functions against a confidentiality oracle."""

from pathlib import Path
import subprocess
import tempfile
import unittest

if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]
SIGNATURES = (
    "int vos3_ai_kv_cache_alloc(",
    "void vos3_ai_kv_cache_free(",
)

STUB = r"""
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#define VOS3_MODEL_SLOT_MAX 4U
#define VOS3_SLOT_FREE 0U
#define VOS3_PAGE_SIZE_2M (2U * 1024U * 1024U)
#define VOS3_VMM_FLAG_WRITE 1U
#define VOS3_VMM_FLAG_LARGE 2U
#define VOS3_WARN(...) ((void)0)
#define VOS3_INFO(...) ((void)0)

typedef uint32_t vos3_vmm_flags_t;
typedef struct {
    uint8_t status;
    uint32_t hp_count;
    uint32_t owner_tid;
    uintptr_t base;
    uintptr_t kv_base;
    uint32_t kv_hp_count;
    uint64_t kv_hp_phys[4];
    uint32_t kv_size;
    uint8_t kv_pinned;
    uint8_t kv_pinned_stable;
    int lock;
} vos3_ai_model_slot_t;

typedef struct { int efi_present; } boot_info_t;
static boot_info_t g_uefi_boot_info;
static vos3_ai_model_slot_t g_model_slots[VOS3_MODEL_SLOT_MAX];
static unsigned scrub_calls, unmap_calls, free_calls;
static int page_is_scrubbed;

static void vos3_spinlock_lock(int *lock) { assert(*lock == 0); *lock = 1; }
static void vos3_spinlock_unlock(int *lock) { assert(*lock == 1); *lock = 0; }
static uint8_t vos3_ai_room_color_for_tid(uint32_t tid) { return (uint8_t)tid; }
static uint32_t kv_uefi_mem_quality(uint64_t phys) { (void)phys; return 0; }
static uint64_t vos3_pmm_alloc_colored_hugepage(uint8_t color) {
    (void)color;
    return UINT64_C(0x200000);
}
static int vos3_vmm_map(uintptr_t va, uintptr_t pa, vos3_vmm_flags_t flags) {
    (void)va; (void)pa; (void)flags;
    return 0;
}
static void scrub_zero_fill(void *address, size_t size) {
    assert(size == VOS3_PAGE_SIZE_2M);
    memset(address, 0, size);
    scrub_calls++;
    page_is_scrubbed = 1;
}
static void vos3_vmm_unmap_large(uintptr_t va) {
    (void)va;
    assert(page_is_scrubbed);
    unmap_calls++;
}
static void vos3_pmm_free_huge(uint64_t phys) {
    assert(phys != 0 && page_is_scrubbed && unmap_calls == free_calls + 1U);
    free_calls++;
}
"""

MAIN = r"""
int main(void) {
    const size_t arena_size = 3U * VOS3_PAGE_SIZE_2M;
    uint8_t *arena = malloc(arena_size);
    assert(arena != NULL);
    memset(arena, 0xa5, arena_size);

    vos3_ai_model_slot_t *slot = &g_model_slots[1];
    slot->status = 1U;
    slot->base = (uintptr_t)arena;
    assert(vos3_ai_kv_cache_alloc(1U, 1U) == 0);
    assert(slot->kv_base == (uintptr_t)(arena + VOS3_PAGE_SIZE_2M));
    for (size_t i = 0; i < VOS3_PAGE_SIZE_2M; i++)
        assert(arena[VOS3_PAGE_SIZE_2M + i] == 0);
    assert(scrub_calls == 1U && slot->kv_pinned == 1U);

    memset((void *)slot->kv_base, 0x5a, VOS3_PAGE_SIZE_2M);
    page_is_scrubbed = 0;
    vos3_ai_kv_cache_free(1U);
    assert(scrub_calls == 2U && unmap_calls == 1U && free_calls == 1U);
    assert(slot->kv_pinned == 0U && slot->kv_hp_count == 0U);
    free(arena);
    return 0;
}
"""


class AIKVZeroingTests(unittest.TestCase):
    def test_full_page_scrub_before_publication_and_return(self):
        source = (ROOT / "kernel/src/mm/ai_slots.c").read_text()
        production = "\n".join(function(source, signature) for signature in SIGNATURES)
        code = STUB + production + MAIN
        with tempfile.TemporaryDirectory(prefix="vos-ai-kv-zero-") as directory:
            path = Path(directory)
            source_path = path / "test.c"
            binary = path / "test"
            source_path.write_text(code)
            build = subprocess.run(
                [
                    "cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", str(source_path), "-o", str(binary),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run(
                [str(binary)], capture_output=True, text=True, timeout=15
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
