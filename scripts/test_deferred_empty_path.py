"""Actual drain, deterministic atomic-boundary interleavings; no SMP proof."""
from pathlib import Path
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function
ROOT = Path(__file__).resolve().parents[1]

class DeferredEmptyPath(unittest.TestCase):
    def test_publication_and_reentry(self):
        source = (ROOT/'kernel/src/sched/scheduler.c').read_text()
        drain = function(source, 'void vos3_sched_process_deferred(')
        instruction = '__asm__ volatile ("pushfq; popq %0" : "=r"(rflags));'
        self.assertEqual(drain.count(instruction), 1)
        drain = drain.replace(instruction, 'rflags = mock_flags;')
        code = r'''
#include <stdint.h>
#include <assert.h>
static uint32_t g_deferred_active[256],g_deferred_pending[256],cpu;
static int g_reap_pending,g_ai_guard_dirty,g_tcp_work_pending;
static volatile int g_ai_monitor_pending;
static uint64_t mock_flags=512;
static unsigned exchanges,loads,drains,reentry,late_publish;
static uint32_t get_cpu_id(void){return cpu;}
static uint32_t observed_load(uint32_t *p,int order){
 uint32_t value=__atomic_load_n(p,order);loads++;
 if(late_publish){late_publish=0;__atomic_store_n(p,1,__ATOMIC_RELEASE);}
 return value;
}
#define __atomic_load_n observed_load
/* Keep each production operand's type/volatile qualifier intact. The macro
 * does not recursively expand its own name, so this still calls the builtin. */
#define __atomic_exchange_n(p,value,order) (++exchanges, __atomic_exchange_n((p),(value),(order)))
void vos3_sched_request_deferred_cpu(uint32_t);
void vos3_sched_process_deferred(void);
static void vos3_shm_reap_creators(void){
 drains++;
 if(reentry){reentry=0;vos3_sched_request_deferred_cpu(cpu);
  vos3_sched_process_deferred();assert(g_deferred_pending[cpu]==1);}
}
static void vos3_vmm_reap_address_spaces(void){}
static void vos3_ai_guard_reap_contexts(void){}
static void vos3_task_reap(void){}
static void vos3_ai_monitor_tick(void){}
static void vos3_ai_guard_reprotect_tick(void){}
static void vos3_tcp_timer_tick(void){}
''' + function(source,'void vos3_sched_request_deferred_cpu(') + '\n' + drain + r'''
int main(void){
 /* Empty path performs no exchange, preserving guard and pending state. */
 vos3_sched_process_deferred();assert(loads==1&&exchanges==0&&drains==0);
 /* Producer publishes immediately after a zero read. No work is erased. */
 late_publish=1;vos3_sched_process_deferred();
 assert(g_deferred_pending[0]==1&&exchanges==0&&drains==0);
 vos3_sched_process_deferred();assert(drains==1&&!g_deferred_pending[0]&&!g_deferred_active[0]);
 /* Publication during active drain survives attempted recursive consumption. */
 vos3_sched_request_deferred_cpu(0);reentry=1;vos3_sched_process_deferred();
 assert(drains==2&&g_deferred_pending[0]==1&&!g_deferred_active[0]);
 vos3_sched_process_deferred();assert(drains==3&&!g_deferred_pending[0]);
 /* Remote publication is consumed only by that CPU; IF-off consumes nothing. */
 vos3_sched_request_deferred_cpu(3);vos3_sched_process_deferred();assert(drains==3);
 cpu=3;mock_flags=0;vos3_sched_process_deferred();assert(g_deferred_pending[3]==1);
 mock_flags=512;vos3_sched_process_deferred();assert(drains==4&&!g_deferred_pending[3]);
 unsigned before=loads;cpu=256;vos3_sched_process_deferred();assert(loads==before);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-deferred-empty-') as directory:
            p=Path(directory); (p/'test.c').write_text(code)
            for flags in ([], ['-DNATIVE_SMP_TEST']):
                build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined',*flags,str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
                self.assertEqual(build.returncode,0,build.stderr)
                run=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=10)
                self.assertEqual(run.returncode,0,run.stderr)

if __name__ == '__main__': unittest.main()
