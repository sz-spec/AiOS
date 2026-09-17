"""Actual scheduler drain and syscall dispatcher, with IRQ/destructor mocks."""
from pathlib import Path
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function
ROOT=Path(__file__).resolve().parents[1]

class DeferredSyscallTests(unittest.TestCase):
    def test_actual_safe_point_and_dispatch_order(self):
        scheduler=(ROOT/'kernel/src/sched/scheduler.c').read_text()
        source=(ROOT/'kernel/src/arch/x86_64/syscall.c').read_text()
        shm=(ROOT/'kernel/src/ipc/shm.c').read_text()
        vmm=(ROOT/'kernel/src/mm/vmm.c').read_text()
        task=(ROOT/'kernel/src/sched/task.c').read_text()
        self.assertIn('vos3_sched_request_deferred()',
                      function(shm,'void vos3_shm_owner_exit('))
        self.assertIn('vos3_sched_request_deferred()',
                      function(vmm,'void vos3_vmm_destroy_address_space('))
        defer = function(task,'void vos3_task_defer_destroy(')
        self.assertIn('vos3_sched_request_deferred_cpu(owner_cpu)', defer)
        self.assertIn('uint32_t owner_cpu = task->cpu_id', defer)
        self.assertIn('task->sched_owner_plus_one - 1U', defer)
        request_cpu=function(scheduler,'void vos3_sched_request_deferred_cpu(')
        request=function(scheduler,'void vos3_sched_request_deferred(')
        drain=function(scheduler,'void vos3_sched_process_deferred(')
        asm='__asm__ volatile ("pushfq; popq %0" : "=r"(rflags));'
        self.assertEqual(drain.count(asm),1)
        drain=drain.replace(asm,'rflags = mock_rflags;') # Only architecture read mocked.
        code=r'''
#include <stdint.h>
#include <assert.h>
#define VOS3_SYS_MAX 512
#define VOS3_SYS_BLKDEV_TEST 220
#define VOS3_SYS_BLKDEV_INFO 221
#define VOS3_SYSCALL_ENOSYS (-38)
#define VOS3_WARN(...) ((void)0)
#define VOS3_INFO(...) ((void)0)
#define VOS3_DEBUG(...) ((void)0)
typedef struct {uint64_t rax,rdi;} vos3_syscall_frame_t;
typedef int64_t (*vos3_syscall_handler_t)(vos3_syscall_frame_t*);
static vos3_syscall_handler_t g_syscall_table[VOS3_SYS_MAX];
static uint64_t mock_rflags=512;
static uint32_t g_deferred_active[256],g_deferred_pending[256],cpu;
static int g_reap_pending,g_ai_guard_dirty,g_tcp_work_pending;
static int shm,spaces,tasks,ai,contexts,tcp,recursion,handler_done,signals,lock_held;
static uint32_t get_cpu_id(void){return cpu;}
void vos3_sched_process_deferred(void);
static void vos3_shm_reap_creators(void){assert(!lock_held);shm++;if(recursion){recursion=0;vos3_sched_process_deferred();}}
static void vos3_vmm_reap_address_spaces(void){assert(!lock_held);spaces++;}
static void vos3_ai_guard_reap_contexts(void){assert(!lock_held&&(mock_rflags&512));contexts++;}
static void vos3_task_reap(void){assert(!lock_held);tasks++;}
static void vos3_ai_guard_reprotect_tick(void){assert(!lock_held);ai++;}
static void vos3_tcp_timer_tick(void){assert(!lock_held);tcp++;}
static void vos3_console_puts(const char*s){(void)s;}
static int64_t check_app_syscall_permission(uint64_t n){(void)n;return 0;}
static int64_t handler(vos3_syscall_frame_t*f){(void)f;assert(!handler_done);lock_held=1;handler_done=1;lock_held=0;return 42;}
void vos3_signal_deliver(vos3_syscall_frame_t*f,int64_t result){(void)f;assert(handler_done&&!lock_held&&result==42);assert(shm==((mock_rflags&512)?1:0));signals++;}
''' + request_cpu+'\n'+request+'\n'+drain+'\n'+function(source,'int64_t vos3_syscall_dispatch(')+r'''
static void reset(void){shm=spaces=tasks=ai=contexts=tcp=signals=handler_done=0;cpu=0;mock_rflags=512;for(int i=0;i<256;i++)g_deferred_active[i]=g_deferred_pending[i]=0;}
int main(void){
 reset();mock_rflags=0;g_reap_pending=g_ai_guard_dirty=g_tcp_work_pending=1;
 vos3_sched_request_deferred();
 vos3_sched_process_deferred();assert(!(shm|spaces|tasks|ai|contexts|tcp));assert(g_reap_pending&&g_ai_guard_dirty&&g_tcp_work_pending);
 mock_rflags=512;recursion=1;vos3_sched_process_deferred();
 assert(shm==1&&spaces==1&&contexts==1&&ai==1&&tcp==1&&!g_deferred_active[0]);
#ifdef NATIVE_SMP_TEST
 assert(tasks==2); /* Existing owner-local pass plus tick-pending BSP pass. */
#else
 assert(tasks==1);
#endif
 assert(!g_reap_pending&&!g_ai_guard_dirty&&!g_tcp_work_pending);
 reset();vos3_sched_process_deferred();assert(!shm&&!spaces&&!contexts&&!ai&&!tcp);
 vos3_sched_request_deferred();vos3_sched_process_deferred();assert(shm==1&&spaces==1&&contexts==1&&!ai&&!tcp);
#ifdef NATIVE_SMP_TEST
 assert(tasks==1);
#else
 assert(!tasks);
#endif
 reset();vos3_sched_request_deferred_cpu(3);vos3_sched_process_deferred();assert(!(shm|spaces|tasks|ai|tcp));
 cpu=3;vos3_sched_process_deferred();assert(shm==1&&spaces==1&&contexts==1);
 reset();cpu=256;vos3_sched_process_deferred();assert(!(shm|spaces|tasks|ai|tcp));
#ifdef NATIVE_SMP_TEST
 reset();cpu=3;g_reap_pending=g_ai_guard_dirty=g_tcp_work_pending=1;vos3_sched_request_deferred();vos3_sched_process_deferred();
 assert(shm==1&&spaces==1&&tasks==1&&!ai&&!tcp&&!g_deferred_active[3]);
 assert(g_reap_pending&&g_ai_guard_dirty&&g_tcp_work_pending);
#endif
 reset();g_reap_pending=g_ai_guard_dirty=g_tcp_work_pending=1;recursion=1;vos3_sched_request_deferred();
 g_syscall_table[1]=handler;vos3_syscall_frame_t frame={1,0};
 assert(vos3_syscall_dispatch(&frame)==42);assert(signals==1&&shm==1&&spaces==1&&ai==1&&tcp==1);
 reset();mock_rflags=0;g_reap_pending=1;vos3_sched_request_deferred();
 assert(vos3_syscall_dispatch(&frame)==42);assert(signals==1&&!shm&&!spaces&&!tasks&&g_reap_pending);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-deferred-') as directory:
            p=Path(directory);(p/'test.c').write_text(code)
            for native in (False,True):
                with self.subTest(native_smp=native):
                    flags=['-DNATIVE_SMP_TEST'] if native else []
                    r=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=undefined',*flags,str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
                    self.assertEqual(r.returncode,0,r.stderr)
                    r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=10)
                    self.assertEqual(r.returncode,0,r.stderr)

if __name__=='__main__':unittest.main()
