"""Compile and exercise the production clear_child_tid release helper."""

from pathlib import Path
import subprocess
import tempfile
import unittest

if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function


ROOT = Path(__file__).resolve().parents[1]


class ChildTidRelease(unittest.TestCase):
    def test_actual_tid_allocator_is_unique_and_fails_closed(self):
        source = (ROOT / "kernel/src/sched/task.c").read_text()
        actual = function(source, "vos3_tid_t vos3_task_alloc_tid(")
        code = r'''
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#define VOS3_TID_INVALID UINT32_MAX
typedef uint32_t vos3_tid_t;
typedef struct { volatile uint32_t value; } vos3_atomic32_t;
static vos3_atomic32_t g_next_tid = {0};
static uint32_t vos3_atomic_load32(const volatile uint32_t *p) {
    return __atomic_load_n(p, __ATOMIC_ACQUIRE);
}
static uint32_t vos3_atomic_cas32(volatile uint32_t *p, uint32_t expected,
                                  uint32_t desired) {
    __atomic_compare_exchange_n(p, &expected, desired, 0,
                                __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
    return expected;
}
''' + actual + r'''
enum { THREADS=8, EACH=1000 };
static uint32_t ids[THREADS][EACH];
static void *worker(void *arg) {
    uintptr_t n=(uintptr_t)arg;
    for (unsigned i=0;i<EACH;i++) ids[n][i]=vos3_task_alloc_tid();
    return 0;
}
int main(void) {
    pthread_t threads[THREADS];
    for (uintptr_t i=0;i<THREADS;i++) assert(!pthread_create(&threads[i],0,worker,(void*)i));
    for (unsigned i=0;i<THREADS;i++) assert(!pthread_join(threads[i],0));
    unsigned char seen[THREADS*EACH]={0};
    for (unsigned i=0;i<THREADS;i++) for (unsigned j=0;j<EACH;j++) {
        assert(ids[i][j] < THREADS*EACH); assert(!seen[ids[i][j]]); seen[ids[i][j]]=1;
    }
    __atomic_store_n(&g_next_tid.value, UINT32_MAX-1U, __ATOMIC_RELEASE);
    assert(vos3_task_alloc_tid()==UINT32_MAX-1U);
    assert(vos3_task_alloc_tid()==VOS3_TID_INVALID);
    assert(vos3_task_alloc_tid()==VOS3_TID_INVALID);
    assert(g_next_tid.value==UINT32_MAX);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-tid-allocator-") as directory:
            path = Path(directory)
            (path / "test.c").write_text(code)
            build = subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
                 str(path / "test.c"), "-o", str(path / "test")],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(path / "test")], capture_output=True,
                                 text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_clone_initializes_tid_state_before_publication(self):
        source = (ROOT / "kernel/src/exec/exec_syscall.c").read_text()
        clone = function(source, "static int64_t sys_clone(")
        inherited_clear = clone.index("child->clear_child_tid =")
        publication = clone.index("vos3_task_register(child)")
        parent_store = clone.index("copy_to_user(parent_tidptr")
        self.assertLess(inherited_clear, publication)
        self.assertLess(parent_store, publication)
        self.assertIn("if ((flags & CLONE_CHILD_SETTID) != 0U) return -95", clone)
        self.assertIn("if (copy_to_user(parent_tidptr", clone)
        self.assertIn("child->tid = vos3_task_alloc_tid()", clone)
        self.assertNotIn("s_thread_pid", clone)

    def test_actual_helper_uses_fault_safe_usercopy_once(self):
        source = (ROOT / "kernel/src/sched/task.c").read_text()
        actual = function(source, "void vos3_task_release_clear_child_tid(")
        code = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

typedef struct {
    uint32_t tid;
    volatile uint32_t *clear_child_tid;
} vos3_task_t;

static int readable = 1;
static int copy_reads;
static int copy_writes;
static int wakes;
static volatile uint32_t *last_wake;

static int access_ok(const void *ptr, size_t size) {
    uintptr_t addr = (uintptr_t)ptr;
    return ptr != NULL && size == sizeof(uint32_t) && addr < 0x800000000000ULL;
}
static int copy_from_user(void *dst, const void *src, size_t size) {
    copy_reads++;
    if (!readable) return -14;
    memcpy(dst, src, size);
    return 0;
}
static int copy_to_user(void *dst, const void *src, size_t size) {
    copy_writes++;
    memcpy(dst, src, size);
    return 0;
}
static int64_t vos3_futex_wake_addr(volatile uint32_t *uaddr, int count) {
    assert(count == 0x7fffffff);
    wakes++;
    last_wake = uaddr;
    return 1;
}
''' + actual + r'''

static void reset(void) {
    readable = 1;
    copy_reads = copy_writes = wakes = 0;
    last_wake = NULL;
}

int main(void) {
    uint32_t word = 71;
    vos3_task_t task = {.tid = 71, .clear_child_tid = &word};

    reset();
    vos3_task_release_clear_child_tid(&task, 1);
    assert(word == 0 && task.clear_child_tid == NULL);
    assert(copy_reads == 1 && copy_writes == 1 && wakes == 1 && last_wake == &word);
    vos3_task_release_clear_child_tid(&task, 1);
    assert(copy_reads == 1 && copy_writes == 1 && wakes == 1);

    reset();
    word = 2;
    task.clear_child_tid = &word;
    vos3_task_release_clear_child_tid(&task, 1);
    assert(word == 2 && copy_reads == 1 && copy_writes == 0 && wakes == 1);

    reset();
    word = 71;
    task.clear_child_tid = &word;
    readable = 0;
    vos3_task_release_clear_child_tid(&task, 1);
    assert(word == 71 && copy_reads == 1 && copy_writes == 0 && wakes == 1);

    reset();
    task.clear_child_tid = &word;
    vos3_task_release_clear_child_tid(&task, 0);
    assert(copy_reads == 0 && copy_writes == 0 && wakes == 1);

    reset();
    task.clear_child_tid = (volatile uint32_t *)((uintptr_t)&word + 1U);
    vos3_task_release_clear_child_tid(&task, 1);
    assert(task.clear_child_tid == NULL && copy_reads == 0 && copy_writes == 0 && wakes == 0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-child-tid-release-") as directory:
            path = Path(directory)
            (path / "test.c").write_text(code)
            build = subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 str(path / "test.c"), "-o", str(path / "test")],
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run(
                [str(path / "test")], capture_output=True, text=True, timeout=10
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
