"""Compile actual MMIO policy and managed-RAM classifier; no device emulation."""
from pathlib import Path
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function
ROOT = Path(__file__).resolve().parents[1]

class MMIOPolicy(unittest.TestCase):
    def test_actual_policy_and_managed_ram(self):
        guard = (ROOT/'kernel/src/mm/ai_guard.c').read_text()
        pmm = (ROOT/'kernel/src/mm/pmm.c').read_text()
        shm = function((ROOT/'kernel/src/ipc/shm.c').read_text(), 'vos3_ipc_id_t vos3_shm_create_device(')
        self.assertLess(shm.index('vos3_pmm_is_device_range'), shm.index('vos3_ai_guard_check_hardware_access'))
        self.assertLess(shm.index('vos3_ai_guard_check_hardware_access'), shm.index('vos3_kzalloc'))
        self.assertIn('return VOS3_IPC_INVALID;', shm[shm.index('vos3_ai_guard_check_hardware_access'):shm.index('vos3_kzalloc')])
        code = '''#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#define VOS3_AI_GUARD_ERR_PERM (-1)
static struct { unsigned initialized; uintptr_t base_addr, end_addr; } g_pmm;
''' + function(guard, 'int vos3_ai_guard_check_hardware_access(') + '\n' + function(pmm, 'int vos3_pmm_is_device_range(') + '''
int main(void) {
 uintptr_t probes[] = {0, 0x1000, 0x80000000, 0xfd000000, UINTPTR_MAX};
 size_t sizes[] = {0, 1, 4096, SIZE_MAX};
 for (unsigned i=0;i<5;i++) for(unsigned j=0;j<4;j++)
   assert(vos3_ai_guard_check_hardware_access(probes[i],sizes[j])==VOS3_AI_GUARD_ERR_PERM);
 g_pmm.initialized=1;g_pmm.base_addr=0x1000;g_pmm.end_addr=0x80000000;
 assert(!vos3_pmm_is_device_range(0x1000));
 assert(!vos3_pmm_is_device_range(0x7fffffff));
 assert(vos3_pmm_is_device_range(0xfd000000));
 assert(vos3_ai_guard_check_hardware_access(0xfd000000,4096)!=0);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.c').write_text(code)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True,timeout=5)

if __name__ == '__main__': unittest.main()
