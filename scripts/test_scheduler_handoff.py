"""Exercise actual scheduler reservation/ack code at handoff interleavings.

This does not qualify task reclamation or asynchronous cancellation. The
assembly stack switch has a separate execution harness.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class HandoffTests(unittest.TestCase):
    def test_actual_selection_reservation_and_ack(self):
        source = (ROOT / 'kernel/src/sched/scheduler.c').read_text()
        actual = '\n'.join(function(source, signature) for signature in (
            'static void rq_remove(',
            'static int sched_task_selectable_locked(',
            'static void sched_reserve_switch_locked(',
            'void vos3_sched_switch_stack_ack(',
            'int vos3_sched_claim_task_reap(',
            'static vos3_task_t* pick_next_task(',
        ))
        code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#define VOS3_TASK_FLAG_IDLE 1U
#define VOS3_TASK_FLAG_PINNED 2U
#define VOS3_TASK_FLAG_QUEUED 4U
#define VOS3_PRIORITY_COUNT 5U
typedef enum { VOS3_TASK_READY, VOS3_TASK_RUNNING, VOS3_TASK_BLOCKED,
               VOS3_TASK_SLEEPING, VOS3_TASK_ZOMBIE, VOS3_TASK_DEAD } vos3_task_state_t;
typedef struct task {
    vos3_task_state_t state;
    uint32_t cpu_id, flags, sched_execution_owner, sched_owner_plus_one;
    struct task *next, *prev;
} vos3_task_t;
typedef struct { vos3_task_t *head, *tail; uint32_t count; } vos3_run_queue_t;
static vos3_task_t *g_current_task[256], *g_switch_outgoing[256], *g_idle_task[256];
static vos3_run_queue_t g_run_queues[5], g_interactive_rq;
static uint32_t cpu;
static int irq_off, sched_lock, fpu_owned;
static int g_sched_lock;
typedef unsigned vos3_irqflags_t;
static unsigned vos3_irq_save(void) { unsigned old=irq_off; irq_off=1; return old; }
static void vos3_irq_restore(unsigned old) { assert(!sched_lock); irq_off=old; }
static void vos3_spinlock_lock(int *lock) { assert(lock==&g_sched_lock && irq_off && !sched_lock); sched_lock=1; }
static void vos3_spinlock_unlock(int *lock) { assert(lock==&g_sched_lock && sched_lock); sched_lock=0; }
static int vos3_fpu_task_owned(vos3_task_t *t) { (void)t; assert(sched_lock); return fpu_owned; }
static int expected_panic;
static jmp_buf panic_target;
static uint32_t get_cpu_id(void) { return cpu; }
static void panic(void) {
    assert(expected_panic); longjmp(panic_target, 1);
}
#define VOS3_PANIC(...) panic()
''' + actual + r'''
static void queue(vos3_run_queue_t *rq, vos3_task_t *task) {
    assert(!(task->flags & VOS3_TASK_FLAG_QUEUED));
    task->next = NULL; task->prev = rq->tail;
    if (rq->tail) rq->tail->next = task; else rq->head = task;
    rq->tail = task; rq->count++; task->flags |= VOS3_TASK_FLAG_QUEUED;
}
int main(void) {
    vos3_task_t idle[2] = {{0}}, a = {0}, b = {0}, c = {0}, pinned = {0};
    for (unsigned i = 0; i < 2; i++) {
        idle[i].flags = VOS3_TASK_FLAG_IDLE; idle[i].cpu_id = i;
        g_idle_task[i] = &idle[i];
    }
    /* First dispatch reserves the incoming task; NULL ack never releases it. */
    cpu = 0; queue(&g_interactive_rq, &a);
    assert(pick_next_task() == &a);
    sched_reserve_switch_locked(NULL, &a, 0);
    g_current_task[0] = &a; a.state = VOS3_TASK_RUNNING;
    assert(a.sched_execution_owner == 1 && g_switch_outgoing[0] == NULL);
    vos3_sched_switch_stack_ack(); assert(a.sched_execution_owner == 1);
    /* AP bootstrap idle identity owns its bootstrap continuation. */
    cpu = 1; sched_reserve_switch_locked(NULL, &idle[1], 1);
    g_current_task[1] = &idle[1]; idle[1].state = VOS3_TASK_RUNNING;
    vos3_sched_switch_stack_ack(); assert(idle[1].sched_execution_owner == 2);
    /* Queued outgoing A cannot execute on CPU1 before the context is saved. */
    cpu = 0; a.state = VOS3_TASK_READY; queue(&g_run_queues[2], &a);
    queue(&g_interactive_rq, &b); assert(pick_next_task() == &b);
    sched_reserve_switch_locked(&a, &b, 0);
    assert(a.sched_execution_owner == 1 && b.sched_execution_owner == 1);
    assert(g_switch_outgoing[0] == &a);
    g_current_task[0] = &b; b.state = VOS3_TASK_RUNNING;
    a.state = VOS3_TASK_DEAD;
    assert(!vos3_sched_claim_task_reap(&a)); /* old stack is still executing */
    assert(a.sched_execution_owner == 1);
    a.state = VOS3_TASK_READY;
    cpu = 1; queue(&g_run_queues[1], &c);
    assert(pick_next_task() == &c); assert(g_run_queues[2].head == &a);
    sched_reserve_switch_locked(&idle[1], &c, 1); g_current_task[1] = &c;
    c.state = VOS3_TASK_RUNNING; vos3_sched_switch_stack_ack();
    assert(idle[1].sched_execution_owner == 0);
    /* CPU0 halted before ack: ownership stays held, independently of current. */
    for (unsigned i = 0; i < 100; i++) assert(!sched_task_selectable_locked(&a, 1));
    cpu = 0; vos3_sched_switch_stack_ack();
    assert(g_switch_outgoing[0] == NULL && a.sched_execution_owner == 0);
    cpu = 1;
#ifdef NATIVE_SMP_TEST
    /* Test-only immutable assignment remains a separate restriction. */
    assert(!sched_task_selectable_locked(&a, 1));
    a.sched_owner_plus_one = 0;
#endif
    assert(pick_next_task() == &a);
    sched_reserve_switch_locked(&c, &a, 1); g_current_task[1] = &a;
    a.state = VOS3_TASK_RUNNING; vos3_sched_switch_stack_ack();
    assert(a.sched_execution_owner == 2 && c.sched_execution_owner == 0);
    /* Same-task reselection neither creates a new token nor releases owner. */
    a.state = VOS3_TASK_READY; queue(&g_interactive_rq, &a);
    assert(pick_next_task() == &a); sched_reserve_switch_locked(&a, &a, 1);
    assert(g_switch_outgoing[1] == NULL && a.sched_execution_owner == 2);
    /* Another owned task on this CPU is not the no-switch exception. */
    c.sched_execution_owner = 2; assert(!sched_task_selectable_locked(&c, 1));
    c.sched_execution_owner = UINT32_MAX; assert(!sched_task_selectable_locked(&c, 1));
    c.sched_execution_owner = 0; c.state = VOS3_TASK_DEAD;
    assert(!sched_task_selectable_locked(&c, 1));
    c.state = VOS3_TASK_ZOMBIE; assert(!sched_task_selectable_locked(&c, 1));
    pinned.flags = VOS3_TASK_FLAG_PINNED; pinned.cpu_id = 0;
    assert(!sched_task_selectable_locked(&pinned, 1));
    assert(sched_task_selectable_locked(&pinned, 0));
    /* Reservation cannot be duplicated while a switch is in progress. */
    vos3_task_t final = {0};
    cpu = 0;
    sched_reserve_switch_locked(&b, &final, 0); g_current_task[0] = &final;
    expected_panic = 1;
    if (!setjmp(panic_target)) {
        sched_reserve_switch_locked(&final, &idle[0], 0); assert(0);
    }
    expected_panic = 0; vos3_sched_switch_stack_ack();
    assert(b.sched_execution_owner == 0 && final.sched_execution_owner == 1);
    /* A terminal task is not reclaimable while any hardware/queue alias remains. */
    assert(!vos3_sched_claim_task_reap(&b)); /* not terminal yet */
    b.state = VOS3_TASK_DEAD; fpu_owned = 1;
    assert(!vos3_sched_claim_task_reap(&b)); fpu_owned = 0;
    b.flags |= VOS3_TASK_FLAG_QUEUED;
    assert(!vos3_sched_claim_task_reap(&b)); b.flags &= ~VOS3_TASK_FLAG_QUEUED;
    assert(vos3_sched_claim_task_reap(&b));
    assert(b.sched_execution_owner == UINT32_MAX);
    assert(!vos3_sched_claim_task_reap(&b));
    assert(!sched_task_selectable_locked(&b, 0));
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-handoff-host-') as directory:
            base = Path(directory)
            (base / 'test.c').write_text(code)
            for native in (False, True):
                with self.subTest(native=native):
                    flags = ['-DNATIVE_SMP_TEST'] if native else []
                    build = subprocess.run(
                        ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                         '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                         *flags, str(base / 'test.c'), '-o', str(base / 'test')],
                        capture_output=True, text=True)
                    self.assertEqual(build.returncode, 0, build.stderr)
                    run = subprocess.run([str(base / 'test')], capture_output=True,
                                         text=True, timeout=10)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_actual_fpu_detach_before_execution_release(self):
        source = (ROOT / 'kernel/src/arch/x86_64/interrupts.c').read_text()
        actual = '\n'.join(function(source, signature) for signature in (
            'int vos3_fpu_task_owned(',
            'void vos3_fpu_switch_out(',
            'void vos3_fpu_release_owner(',
        ))
        code = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <setjmp.h>
#define VOS3_MAX_CPUS 4U
typedef struct { uint8_t *fpu_state; int fpu_initialized; } vos3_task_t;
static vos3_task_t *g_fpu_owner[4];
static uint32_t cpu;
static uint64_t cr0;
static unsigned saves, writes;
static int expect_panic;
static jmp_buf panic_target;
static uint32_t get_cpu_id(void) { return cpu; }
static uint64_t vos3_read_cr0(void) { return cr0; }
static void vos3_write_cr0(uint64_t value) { cr0=value; writes++; }
static void fpu_save(uint8_t *p) {
    assert(!(cr0 & 8));
    assert(g_fpu_owner[cpu] && g_fpu_owner[cpu]->fpu_state==p);
    assert(!g_fpu_owner[cpu]->fpu_initialized);
    *p=0x5a; saves++;
}
static void panic(void) { assert(expect_panic); longjmp(panic_target,1); }
#define VOS3_PANIC(...) panic()
''' + actual + r'''
int main(void) {
    uint8_t a_state=0,b_state=0;
    vos3_task_t a={&a_state,0}, b={&b_state,0};
    cpu=0; cr0=0x80000009ULL;
    vos3_fpu_switch_out(&a); assert(!saves && !writes);
    g_fpu_owner[0]=&a; assert(vos3_fpu_task_owned(&a));
    vos3_fpu_switch_out(&a);
    assert(saves==1 && writes==2 && cr0==0x80000009ULL);
    assert(a_state==0x5a && a.fpu_initialized && !g_fpu_owner[0]);
    assert(!vos3_fpu_task_owned(&a)); vos3_fpu_release_owner(&a);
    /* Reclamation cannot clear a remote CPU's slot as a substitute for ack. */
    g_fpu_owner[1]=&b; expect_panic=1;
    if (!setjmp(panic_target)) { vos3_fpu_release_owner(&b); assert(0); }
    expect_panic=0; assert(g_fpu_owner[1]==&b && b_state==0);
    /* Wrong outgoing identity is rejected before touching hardware or memory. */
    cpu=1; expect_panic=1;
    if (!setjmp(panic_target)) { vos3_fpu_switch_out(&a); assert(0); }
    expect_panic=0; assert(g_fpu_owner[1]==&b && saves==1);
    vos3_fpu_switch_out(&b);
    assert(!g_fpu_owner[1] && b.fpu_initialized && b_state==0x5a);
    vos3_fpu_release_owner(&b); assert(saves==2 && writes==4);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-handoff-fpu-') as directory:
            base = Path(directory)
            (base / 'test.c').write_text(code)
            build = subprocess.run(
                ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                 '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                 str(base / 'test.c'), '-o', str(base / 'test')],
                capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(base / 'test')], capture_output=True,
                                 text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
