"""Compile actual gated first-placement and enqueue code with topology/IRQ mocks."""
from pathlib import Path
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class InitialPlacementTests(unittest.TestCase):
    def test_actual_assignment_and_enqueue(self):
        source = (ROOT / "kernel/src/sched/scheduler.c").read_text()
        actual = function(source, "static uint32_t assign_initial_cpu_locked(")
        actual += "\n" + function(source, "void vos3_sched_add_task(")
        code = r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#include <string.h>
#define NATIVE_SMP_TEST 1
#define VOS3_TASK_FLAG_QUEUED 1U
#define VOS3_TASK_READY 2
#define VOS3_LC_INTERACTIVE 3
#define VOS3_DEBUG(...) ((void)0)
typedef uint64_t vos3_irqflags_t;
typedef struct {uint32_t sched_owner_plus_one,cpu_id,flags;int state,latency_class,priority;char name[8];} vos3_task_t;
typedef struct {volatile uint32_t started;} vos3_smp_cpu_info_t;
static vos3_smp_cpu_info_t topology[256];
static unsigned absent[256],topology_count,lookups;
static uint32_t g_initial_cpu_cursor;
static int g_sched_running,g_sched_lock,irq_off,queue_count,queue;
static struct {uint32_t task_count;} g_cpu_sched_state[256];
static struct {uint32_t task_count,runnable_count;} g_sched_stats;
static uint32_t vos3_smp_cpu_count(void){assert(g_sched_lock&&irq_off);return topology_count;}
static const vos3_smp_cpu_info_t* vos3_smp_get_cpu_info(uint32_t id){assert(g_sched_lock&&irq_off&&id<256);lookups++;return absent[id]?NULL:&topology[id];}
static uint64_t vos3_irq_save(void){uint64_t old=irq_off;irq_off=1;return old;}
static void vos3_irq_restore(uint64_t old){assert(!g_sched_lock);irq_off=(int)old;}
static void vos3_spinlock_lock(int* lock){assert(irq_off&&!*lock);*lock=1;}
static void vos3_spinlock_unlock(int* lock){assert(irq_off&&*lock);*lock=0;}
static int* rq_for_task(vos3_task_t* task){(void)task;return &queue;}
static void rq_enqueue(int* q,vos3_task_t* task){assert(q==&queue&&g_sched_lock&&irq_off&&task->sched_owner_plus_one==task->cpu_id+1);task->flags|=VOS3_TASK_FLAG_QUEUED;queue_count++;}
static void vos3_sched_ipi_preempt_hook(uint32_t cpu,vos3_task_t* task){assert(!g_sched_lock&&!irq_off&&cpu==task->cpu_id);}
''' + actual + r'''
static vos3_task_t fresh(void){vos3_task_t t={0};return t;}
static void topology_reset(unsigned count){memset(topology,0,sizeof(topology));memset(absent,0,sizeof(absent));topology_count=count;g_initial_cpu_cursor=0;}
int main(void){
 topology_reset(4);for(unsigned i=0;i<4;i++)topology[i].started=1;
 vos3_task_t bootstrap=fresh();vos3_sched_add_task(&bootstrap);
 assert(bootstrap.sched_owner_plus_one==1&&g_initial_cpu_cursor==0&&lookups==0);
 g_sched_running=1;
 for(unsigned i=0;i<12;i++){vos3_task_t t=fresh();vos3_sched_add_task(&t);assert(t.cpu_id==i%4&&t.sched_owner_plus_one==i%4+1);}
 unsigned cursor=g_initial_cpu_cursor;int before=queue_count;
 vos3_task_t sibling=fresh();sibling.sched_owner_plus_one=4;sibling.cpu_id=0;
 vos3_sched_add_task(&sibling);assert(sibling.cpu_id==3&&g_initial_cpu_cursor==cursor);
 vos3_sched_add_task(&sibling);assert(queue_count==before+1&&g_initial_cpu_cursor==cursor);
 sibling.flags=0;irq_off=1;vos3_sched_add_task(&sibling);assert(irq_off&&sibling.cpu_id==3&&g_initial_cpu_cursor==cursor);irq_off=0;
 topology_reset(4);topology[0].started=topology[3].started=1;absent[1]=1;
 for(unsigned i=0;i<8;i++){vos3_task_t t=fresh();vos3_sched_add_task(&t);assert(t.cpu_id==(i%2?3:0));}
 topology_reset(1);topology[0].started=1;
 for(unsigned i=0;i<4;i++){vos3_task_t t=fresh();vos3_sched_add_task(&t);assert(t.cpu_id==0&&g_initial_cpu_cursor==0);}
 topology_reset(999);topology[255].started=1;g_initial_cpu_cursor=UINT32_MAX;
 vos3_task_t last=fresh();vos3_sched_add_task(&last);assert(last.cpu_id==255&&g_initial_cpu_cursor==0);
 topology_reset(0);vos3_task_t empty=fresh();vos3_sched_add_task(&empty);assert(empty.cpu_id==0);
 topology_reset(4);topology[0].started=1;vos3_task_t offline=fresh();vos3_sched_add_task(&offline);assert(offline.cpu_id==0);
 topology_reset(4);vos3_task_t unavailable=fresh();vos3_sched_add_task(&unavailable);assert(unavailable.cpu_id==0);
 assert(!irq_off&&!g_sched_lock);vos3_sched_add_task(NULL);return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-initial-placement-") as directory:
            base = Path(directory)
            (base / "test.c").write_text(code)
            build = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                                    "-fsanitize=undefined", "-fno-sanitize-recover=undefined",
                                    str(base / "test.c"), "-o", str(base / "test")], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(base / "test")], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
