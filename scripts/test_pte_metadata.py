"""Compile real PTE helpers/tagging with simulated PTE storage, no privileged ops."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    start = source.index('int ' + name + '(')
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


class PTEMetadataTests(unittest.TestCase):
    def test_actual_cognitive_helpers_and_permission_update(self):
        source = (ROOT / 'kernel/src/mm/vmm.c').read_text()
        update_start = source.index('    uint64_t ai_bits =', source.index('int vos3_vmm_update_flags('))
        update_end = source.index('\n    vos3_vmm_invlpg', update_start)
        # Execute the production flag reconstruction, excluding privileged TLB work.
        update = source[update_start:update_end]
        cow_start = source.index('        uint64_t new_pte =', source.index('int vos3_vmm_handle_cow_fault('))
        cow_end = source.index('\n', source.index('        new_pte =', cow_start))
        cow_copy = source[cow_start:cow_end]
        program = r'''
#include <assert.h>
#include "vos/vmm.h"
static vos3_pte_t stored;
static int conflict;
int vos3_vmm_get_pte(uintptr_t addr, vos3_pte_t *out) {
    (void)addr; *out = stored; return 0;
}
int vos3_vmm_cas_pte(uintptr_t addr, vos3_pte_t *expected, vos3_pte_t desired) {
    (void)addr;
    if (conflict) { conflict = 0; stored |= VOS3_PTE_COW; *expected = stored; return -1; }
    assert(*expected == stored); stored = desired; return 0;
}
''' + function(source, 'vos3_vmm_set_cognitive_priority') + '\n' + function(source, 'vos3_vmm_is_cognitive_priority') + r'''
static uint64_t cow_copy_flags(uint64_t entry, uintptr_t new_phys) {
''' + cow_copy + r'''
    return new_pte;
}
static void permissions(uint64_t pte_flags) {
    vos3_pte_t *pte = &stored;
    uintptr_t addr = vos3_pte_get_addr(stored);
''' + update + r'''
}
int main(void) {
    const uint64_t phys = 0x0001234567800000ULL;
    const uint64_t base = phys | VOS3_PTE_PRESENT | VOS3_PTE_USER;
    stored = base;
    assert(vos3_vmm_set_cognitive_priority(0x4000) == 0);
    assert(vos3_vmm_is_cognitive_priority(0x4000) == 1);
    assert(!vos3_pte_is_cow(stored));
    assert(vos3_pte_get_addr(stored) == phys);
    stored = base | VOS3_PTE_COW;
    assert(!vos3_vmm_is_cognitive_priority(0x4000));
    assert(vos3_pte_is_cow(stored));
    stored = base;
    conflict = 1;
    assert(vos3_vmm_set_cognitive_priority(0x4000) == 0);
    assert((stored & (VOS3_PTE_COW | VOS3_PTE_COGNITIVE)) ==
           (VOS3_PTE_COW | VOS3_PTE_COGNITIVE));
    stored |= VOS3_PTE_AI_MASK;
    permissions(VOS3_PTE_PRESENT | VOS3_PTE_USER | VOS3_PTE_WRITABLE | VOS3_PTE_NO_EXECUTE);
    assert(!(stored & VOS3_PTE_WRITABLE));
    assert(vos3_pte_is_cow(stored));
    assert(vos3_vmm_is_cognitive_priority(0x4000));
    assert((stored & VOS3_PTE_AI_MASK) == VOS3_PTE_AI_MASK);
    assert(vos3_pte_get_addr(stored) == phys);
    assert(stored & VOS3_PTE_NO_EXECUTE);
    uint64_t copied = cow_copy_flags(stored, 0x800000);
    assert(vos3_pte_get_addr(copied) == 0x800000);
    assert(!vos3_pte_is_cow(copied));
    assert(copied & VOS3_PTE_WRITABLE);
    assert(copied & VOS3_PTE_COGNITIVE);
    assert((copied & VOS3_PTE_AI_MASK) == VOS3_PTE_AI_MASK);
    assert(copied & VOS3_PTE_NO_EXECUTE);
    stored &= ~VOS3_PTE_COW;
    permissions(VOS3_PTE_PRESENT | VOS3_PTE_USER | VOS3_PTE_WRITABLE);
    assert(stored & VOS3_PTE_WRITABLE);
    assert(!(stored & VOS3_PTE_NO_EXECUTE));
    assert(vos3_vmm_is_cognitive_priority(0x4000));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            cfile = Path(directory) / 'pte.c'
            binary = Path(directory) / 'pte'
            cfile.write_text(program)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(ROOT / 'kernel/include'), str(cfile), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
