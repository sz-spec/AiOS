"""Exercise the actual wait-queue, semaphore and mutex functions with forced races."""
from pathlib import Path
import subprocess
import tempfile
import unittest

if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class SyncWaitQueueProtocolTests(unittest.TestCase):
    def test_actual_wait_protocol_interleavings(self):
        source = (ROOT / "kernel/src/sched/sync.c").read_text()
        names = [
            "static void wq_enqueue_current_locked(",
            "static void wq_wake_detached(",
            "int vos3_wq_wake_one(",
            "size_t vos3_wq_wake_all(",
            "int vos3_wq_cancel(",
            "void vos3_mutex_lock(",
            "void vos3_mutex_unlock(",
            "int vos3_sem_wait_status(",
            "void vos3_sem_post(",
        ]
        actual = "\n".join(function(source, name) for name in names)
        code = r'''
#include <assert.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sched.h>

#define VOS3_TASK_BLOCKED 3
#define VOS3_TASK_READY 1
#define VOS3_MUTEX_MAGIC 0x4D555458U
#define VOS3_SEM_MAGIC 0x53454D41U
#define VOS3_MUTEX_UNLOCKED 0
#define VOS3_MUTEX_LOCKED 1
#define VOS3_SYNC_OK 0
#define VOS3_SYNC_ERR_INVALID (-1)
#define VOS3_SYNC_ERR_CLOSED (-6)
#define VOS3_WARN(...) ((void)0)
#define VOS3_PANIC(...) do { assert(!"panic"); } while (0)

typedef unsigned long vos3_irqflags_t;
typedef _Atomic int32_t vos3_atomic32_t;
typedef pthread_mutex_t vos3_spinlock_t;
struct vos3_wait_queue;
struct vos3_task;
typedef struct vos3_wait_entry {
    struct vos3_task *task;
    struct vos3_wait_entry *next;
    struct vos3_wait_queue *queue;
} vos3_wait_entry_t;
typedef struct vos3_task {
    int state;
    const char *name;
    uint64_t wake_time;
    vos3_wait_entry_t wq_entry;
    int wakes;
    int yields;
} vos3_task_t;
typedef struct vos3_wait_queue {
    vos3_wait_entry_t *head, *tail;
    size_t count;
    vos3_spinlock_t lock;
} vos3_wait_queue_t;
typedef struct {
    uint32_t magic;
    vos3_atomic32_t state;
    vos3_task_t *owner;
    uint32_t recursion;
    vos3_wait_queue_t waiters;
    const char *name;
} vos3_mutex_t;
typedef struct {
    uint32_t magic;
    vos3_atomic32_t count;
    vos3_atomic32_t closed;
    int32_t max_count;
    vos3_wait_queue_t waiters;
    const char *name;
} vos3_semaphore_t;

static _Thread_local vos3_task_t *tls_current;
static _Thread_local int irq_disabled;
static _Thread_local int waiter_thread;
static vos3_spinlock_t g_wait_membership_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t event_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t event_cv=PTHREAD_COND_INITIALIZER;
static int load_stage, mutex_stage, compete_once;
static vos3_atomic32_t *load_hook, *mutex_hook;

static vos3_irqflags_t vos3_irq_save(void){int old=irq_disabled;irq_disabled=1;return (unsigned long)old;}
static void vos3_irq_restore(vos3_irqflags_t old){assert(irq_disabled);irq_disabled=(int)old;}
static void vos3_spinlock_lock(vos3_spinlock_t *l){assert(irq_disabled);assert(!pthread_mutex_lock(l));}
static void vos3_spinlock_unlock(vos3_spinlock_t *l){assert(irq_disabled);assert(!pthread_mutex_unlock(l));}
static int32_t vos3_atomic32_load(vos3_atomic32_t *p){
    int32_t value=atomic_load(p);
    if(waiter_thread && p==load_hook && load_stage==0){
        pthread_mutex_lock(&event_lock);load_stage=1;pthread_cond_broadcast(&event_cv);
        while(load_stage==1)pthread_cond_wait(&event_cv,&event_lock);
        pthread_mutex_unlock(&event_lock);
    }
    return value;
}
static void vos3_atomic32_store(vos3_atomic32_t *p,int32_t v){atomic_store(p,v);}
static int32_t vos3_atomic32_cmpxchg(vos3_atomic32_t *p,int32_t expected,int32_t desired){
    int32_t old=expected;
    if(p==load_hook && waiter_thread && compete_once && expected==2){
        atomic_store(p,1);compete_once=0;return 1;
    }
    if(!atomic_compare_exchange_strong(p,&old,desired) &&
       waiter_thread && p==mutex_hook && mutex_stage==0){
        pthread_mutex_lock(&event_lock);mutex_stage=1;pthread_cond_broadcast(&event_cv);
        while(mutex_stage==1)pthread_cond_wait(&event_cv,&event_lock);
        pthread_mutex_unlock(&event_lock);
    }
    return old;
}
static vos3_task_t *vos3_sched_current(void){return tls_current;}
int vos3_wq_cancel(vos3_task_t *task);
static void vos3_sched_add_task(vos3_task_t *task){(void)task;pthread_mutex_lock(&event_lock);pthread_cond_broadcast(&event_cv);pthread_mutex_unlock(&event_lock);}
static void vos3_task_wake(vos3_task_t *task){
    if(task->state==VOS3_TASK_BLOCKED)(void)vos3_wq_cancel(task);
    if(task->state==VOS3_TASK_BLOCKED){task->state=VOS3_TASK_READY;task->wakes++;}
    pthread_mutex_lock(&event_lock);pthread_cond_broadcast(&event_cv);pthread_mutex_unlock(&event_lock);
}
static void vos3_sched_yield(void){
    vos3_task_t *task=tls_current;assert(task&&irq_disabled);task->yields++;
    pthread_mutex_lock(&event_lock);pthread_cond_broadcast(&event_cv);
    while(task->state==VOS3_TASK_BLOCKED)pthread_cond_wait(&event_cv,&event_lock);
    pthread_mutex_unlock(&event_lock);
}
''' + actual + r'''

static void wq_init(vos3_wait_queue_t *q){memset(q,0,sizeof(*q));assert(!pthread_mutex_init(&q->lock,NULL));}
static void sem_init(vos3_semaphore_t *s,int n){memset(s,0,sizeof(*s));s->magic=VOS3_SEM_MAGIC;atomic_store(&s->count,n);atomic_store(&s->closed,0);s->max_count=-1;wq_init(&s->waiters);s->name="test-sem";}
static void mutex_init(vos3_mutex_t *m){memset(m,0,sizeof(*m));m->magic=VOS3_MUTEX_MAGIC;wq_init(&m->waiters);m->name="test-mutex";}
static void wait_stage(int *stage,int value){pthread_mutex_lock(&event_lock);while(*stage<value)pthread_cond_wait(&event_cv,&event_lock);pthread_mutex_unlock(&event_lock);}
static void release_stage(int *stage,int value){pthread_mutex_lock(&event_lock);*stage=value;pthread_cond_broadcast(&event_cv);pthread_mutex_unlock(&event_lock);}
typedef struct {vos3_semaphore_t *sem;vos3_task_t *task;} sem_arg_t;
static void *sem_thread(void *raw){sem_arg_t *a=raw;tls_current=a->task;waiter_thread=1;assert(vos3_sem_wait_status(a->sem)==VOS3_SYNC_OK);return NULL;}
typedef struct {vos3_mutex_t *mutex;vos3_task_t *task;} mutex_arg_t;
static void *mutex_thread(void *raw){mutex_arg_t *a=raw;tls_current=a->task;waiter_thread=1;vos3_mutex_lock(a->mutex);vos3_mutex_unlock(a->mutex);return NULL;}

int main(void){
    vos3_task_t main_task={.state=VOS3_TASK_READY,.name="main"};tls_current=&main_task;
    /* post between the first zero observation and waiter publication */
    vos3_semaphore_t sem;sem_init(&sem,0);vos3_task_t a={.state=VOS3_TASK_READY,.name="a"};
    sem_arg_t sa={&sem,&a};pthread_t th;load_hook=&sem.count;load_stage=0;
    assert(!pthread_create(&th,NULL,sem_thread,&sa));wait_stage(&load_stage,1);
    vos3_sem_post(&sem);release_stage(&load_stage,2);assert(!pthread_join(th,NULL));
    assert(!atomic_load(&sem.count)&&!sem.waiters.count&&!a.yields&&!irq_disabled);

    /* An unbounded semaphore saturates without signed overflow. */
    sem_init(&sem,INT32_MAX);vos3_sem_post(&sem);
    assert(atomic_load(&sem.count)==INT32_MAX);

    /* A competing 2->1 consumer must make the checked CAS retry, not sleep. */
    sem_init(&sem,0);memset(&a,0,sizeof(a));a.state=VOS3_TASK_READY;a.name="a2";sa.sem=&sem;sa.task=&a;
    load_hook=&sem.count;load_stage=0;compete_once=1;
    assert(!pthread_create(&th,NULL,sem_thread,&sa));wait_stage(&load_stage,1);
    atomic_store(&sem.count,2);release_stage(&load_stage,2);assert(!pthread_join(th,NULL));
    assert(!atomic_load(&sem.count)&&!sem.waiters.count&&!a.yields);

    /* Real blocking wake, then an external signal wake and safe re-enqueue. */
    sem_init(&sem,0);memset(&a,0,sizeof(a));a.state=VOS3_TASK_READY;a.name="a3";sa.sem=&sem;sa.task=&a;
    load_hook=NULL;assert(!pthread_create(&th,NULL,sem_thread,&sa));
    pthread_mutex_lock(&event_lock);while(a.yields<1)pthread_cond_wait(&event_cv,&event_lock);pthread_mutex_unlock(&event_lock);
    assert(sem.waiters.count==1&&a.wq_entry.queue==&sem.waiters);vos3_task_wake(&a);
    pthread_mutex_lock(&event_lock);while(a.yields<2)pthread_cond_wait(&event_cv,&event_lock);pthread_mutex_unlock(&event_lock);
    assert(sem.waiters.count==1&&sem.waiters.head==&a.wq_entry&&sem.waiters.tail==&a.wq_entry&&a.wq_entry.next==NULL);
    vos3_sem_post(&sem);assert(!pthread_join(th,NULL));assert(!sem.waiters.count&&!a.wq_entry.queue);

    /* unlock between failed CAS and publication is recovered by checked CAS. */
    vos3_mutex_t mutex;mutex_init(&mutex);vos3_mutex_lock(&mutex);
    vos3_task_t b={.state=VOS3_TASK_READY,.name="b"};mutex_arg_t ma={&mutex,&b};
    mutex_hook=&mutex.state;mutex_stage=0;assert(!pthread_create(&th,NULL,mutex_thread,&ma));wait_stage(&mutex_stage,1);
    vos3_mutex_unlock(&mutex);release_stage(&mutex_stage,2);assert(!pthread_join(th,NULL));
    assert(!atomic_load(&mutex.state)&&!mutex.waiters.count&&!b.yields);

    /* Broadcast detaches every entry and leaves exact queue metadata. */
    vos3_wait_queue_t q;wq_init(&q);vos3_task_t x={.state=VOS3_TASK_BLOCKED,.name="x"},y={.state=VOS3_TASK_BLOCKED,.name="y"};
    x.wq_entry.task=&x;x.wq_entry.next=&y.wq_entry;x.wq_entry.queue=&q;
    y.wq_entry.task=&y;y.wq_entry.queue=&q;q.head=&x.wq_entry;q.tail=&y.wq_entry;q.count=2;
    assert(vos3_wq_wake_all(&q)==2);assert(!q.head&&!q.tail&&!q.count);
    assert(!x.wq_entry.queue&&!y.wq_entry.queue&&x.state==VOS3_TASK_READY&&y.state==VOS3_TASK_READY&&!irq_disabled);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-sync-wq-") as directory:
            path = Path(directory)
            (path / "test.c").write_text(code)
            build = subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
                 "-fsanitize=undefined", "-fno-sanitize-recover=undefined",
                 str(path / "test.c"), "-o", str(path / "test")],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(path / "test")], capture_output=True,
                                 text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_actual_cond_rwlock_and_barrier_protocols(self):
        source = (ROOT / "kernel/src/sched/sync.c").read_text()
        names = [
            "void vos3_wq_init(",
            "static void wq_enqueue_current_locked(",
            "static void wq_wake_detached(",
            "static vos3_irqflags_t wq_prepare_wait(",
            "int vos3_wq_wake_one(",
            "size_t vos3_wq_wake_all(",
            "int vos3_wq_cancel(",
            "void vos3_mutex_init(",
            "void vos3_mutex_lock(",
            "void vos3_mutex_unlock(",
            "int vos3_mutex_is_owner(",
            "void vos3_cond_init(",
            "void vos3_cond_wait(",
            "int vos3_cond_timedwait(",
            "void vos3_cond_signal(",
            "void vos3_rwlock_init(",
            "void vos3_rwlock_rdlock(",
            "int vos3_rwlock_tryrdlock(",
            "void vos3_rwlock_rdunlock(",
            "void vos3_rwlock_wrlock(",
            "int vos3_rwlock_trywrlock(",
            "void vos3_rwlock_wrunlock(",
            "void vos3_barrier_init(",
            "int vos3_barrier_wait(",
        ]
        actual = "\n".join(function(source, name) for name in names)
        code = r'''
#include <assert.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define VOS3_TASK_DEAD 6
#define VOS3_TASK_BLOCKED 3
#define VOS3_TASK_READY 1
#define VOS3_MUTEX_MAGIC 0x4D555458U
#define VOS3_COND_MAGIC 0x434F4E44U
#define VOS3_RWLOCK_MAGIC 0x52574C4BU
#define VOS3_BARRIER_MAGIC 0x42415252U
#define VOS3_MUTEX_UNLOCKED 0
#define VOS3_MUTEX_LOCKED 1
#define VOS3_SYNC_ERR_INVALID (-1)
#define VOS3_SYNC_ERR_UNSUPPORTED (-5)
#define VOS3_WARN(...) ((void)0)
#define VOS3_PANIC(...) do { assert(!"panic"); } while (0)

typedef unsigned long vos3_irqflags_t;
typedef pthread_mutex_t vos3_spinlock_t;
typedef struct { volatile uint32_t value; } vos3_atomic32_t;
struct vos3_wait_queue;
struct vos3_task;
typedef struct vos3_wait_entry {
    struct vos3_task *task;
    struct vos3_wait_entry *next;
    struct vos3_wait_queue *queue;
} vos3_wait_entry_t;
typedef struct vos3_task {
    int state;
    const char *name;
    uint64_t wake_time;
    vos3_wait_entry_t wq_entry;
    unsigned yields;
} vos3_task_t;
typedef struct vos3_wait_queue {
    vos3_wait_entry_t *head, *tail;
    size_t count;
    vos3_spinlock_t lock;
} vos3_wait_queue_t;
typedef struct {
    uint32_t magic;
    vos3_atomic32_t state;
    vos3_task_t *owner;
    uint32_t recursion;
    vos3_wait_queue_t waiters;
    const char *name;
} vos3_mutex_t;
typedef struct {
    uint32_t magic;
    vos3_wait_queue_t waiters;
    const char *name;
} vos3_condvar_t;
typedef struct {
    uint32_t magic;
    vos3_spinlock_t state_lock;
    vos3_atomic32_t readers;
    vos3_atomic32_t writers;
    vos3_task_t *writer;
    vos3_wait_queue_t read_waiters;
    vos3_wait_queue_t write_waiters;
    const char *name;
} vos3_rwlock_t;
typedef struct {
    uint32_t magic;
    uint32_t threshold;
    vos3_spinlock_t state_lock;
    vos3_atomic32_t count;
    vos3_atomic32_t generation;
    vos3_wait_queue_t waiters;
    const char *name;
} vos3_barrier_t;

static _Thread_local vos3_task_t *tls_current;
static _Thread_local int irq_disabled;
static vos3_spinlock_t g_wait_membership_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t event_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t event_cv=PTHREAD_COND_INITIALIZER;
static vos3_task_t *held_task;
static int release_held;

static vos3_irqflags_t vos3_irq_save(void){int old=irq_disabled;irq_disabled=1;return (unsigned long)old;}
static void vos3_irq_restore(vos3_irqflags_t old){assert(irq_disabled);irq_disabled=(int)old;}
static void vos3_spinlock_init(vos3_spinlock_t *l){assert(!pthread_mutex_init(l,NULL));}
static void vos3_spinlock_lock(vos3_spinlock_t *l){assert(irq_disabled);assert(!pthread_mutex_lock(l));}
static void vos3_spinlock_unlock(vos3_spinlock_t *l){assert(irq_disabled);assert(!pthread_mutex_unlock(l));}
static int32_t vos3_atomic32_load(const vos3_atomic32_t *p){return (int32_t)__atomic_load_n(&p->value,__ATOMIC_SEQ_CST);}
static void vos3_atomic32_store(vos3_atomic32_t *p,int32_t v){__atomic_store_n(&p->value,(uint32_t)v,__ATOMIC_SEQ_CST);}
static int32_t vos3_atomic32_cmpxchg(vos3_atomic32_t *p,int32_t expected,int32_t desired){
    uint32_t old=(uint32_t)expected;
    (void)__atomic_compare_exchange_n(&p->value,&old,(uint32_t)desired,0,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);
    return (int32_t)old;
}
static uint32_t vos3_atomic_load32(const volatile uint32_t *p){return __atomic_load_n(p,__ATOMIC_SEQ_CST);}
static void vos3_atomic_store32(volatile uint32_t *p,uint32_t v){__atomic_store_n(p,v,__ATOMIC_SEQ_CST);}
static vos3_task_t *vos3_sched_current(void){return tls_current;}
static void vos3_sched_add_task(vos3_task_t *task){
    (void)task;pthread_mutex_lock(&event_lock);pthread_cond_broadcast(&event_cv);pthread_mutex_unlock(&event_lock);
}
static void vos3_sched_yield(void){
    vos3_task_t *task=tls_current;assert(task&&irq_disabled);task->yields++;
    pthread_mutex_lock(&event_lock);pthread_cond_broadcast(&event_cv);
    while(task->state==VOS3_TASK_BLOCKED || (task==held_task&&!release_held))
        pthread_cond_wait(&event_cv,&event_lock);
    int dead=task->state==VOS3_TASK_DEAD;
    pthread_mutex_unlock(&event_lock);
    if(dead)pthread_exit(NULL);
}
''' + actual + r'''

static void external_wake(vos3_task_t *task){
    (void)vos3_wq_cancel(task);
    pthread_mutex_lock(&event_lock);task->state=VOS3_TASK_READY;
    pthread_cond_broadcast(&event_cv);pthread_mutex_unlock(&event_lock);
}
static void kill_waiter(vos3_task_t *task){
    (void)vos3_wq_cancel(task);
    pthread_mutex_lock(&event_lock);task->state=VOS3_TASK_DEAD;
    pthread_cond_broadcast(&event_cv);pthread_mutex_unlock(&event_lock);
}
static void wait_yields(vos3_task_t *task,unsigned count){
    pthread_mutex_lock(&event_lock);while(task->yields<count)pthread_cond_wait(&event_cv,&event_lock);pthread_mutex_unlock(&event_lock);
}
typedef struct {vos3_rwlock_t *lock;vos3_task_t *task;_Atomic int *done;} rwarg_t;
static void *reader_once(void *raw){rwarg_t *a=raw;tls_current=a->task;vos3_rwlock_rdlock(a->lock);atomic_fetch_add(a->done,1);vos3_rwlock_rdunlock(a->lock);return NULL;}
static void *writer_once(void *raw){rwarg_t *a=raw;tls_current=a->task;vos3_rwlock_wrlock(a->lock);atomic_fetch_add(a->done,1);vos3_rwlock_wrunlock(a->lock);return NULL;}
typedef struct {vos3_barrier_t *barrier;vos3_task_t *task;int loops;_Atomic int *serials;} barg_t;
static void *barrier_loop(void *raw){barg_t *a=raw;tls_current=a->task;for(int i=0;i<a->loops;i++)if(vos3_barrier_wait(a->barrier))atomic_fetch_add(a->serials,1);return NULL;}
typedef struct {vos3_condvar_t *cond;vos3_mutex_t *mutex;vos3_task_t *task;int *predicate;} carg_t;
static void *cond_once(void *raw){carg_t *a=raw;tls_current=a->task;vos3_mutex_lock(a->mutex);while(!*a->predicate)vos3_cond_wait(a->cond,a->mutex);vos3_mutex_unlock(a->mutex);return NULL;}

int main(void){
    vos3_task_t main_task={.state=VOS3_TASK_READY,.name="main"};tls_current=&main_task;

    /* A condition waiter is published before it drops the predicate mutex. */
    vos3_condvar_t cond;vos3_cond_init(&cond,"cond");vos3_mutex_t mutex;vos3_mutex_init(&mutex,"mutex");
    int predicate=0;vos3_task_t ct={.state=VOS3_TASK_READY,.name="cond-task"};carg_t ca={&cond,&mutex,&ct,&predicate};pthread_t cth;
    assert(!pthread_create(&cth,NULL,cond_once,&ca));wait_yields(&ct,1);
    tls_current=&main_task;vos3_mutex_lock(&mutex);predicate=1;vos3_cond_signal(&cond);vos3_mutex_unlock(&mutex);
    assert(!pthread_join(cth,NULL));
    vos3_mutex_lock(&mutex);assert(vos3_cond_timedwait(&cond,&mutex,1)==VOS3_SYNC_ERR_UNSUPPORTED);assert(vos3_mutex_is_owner(&mutex));vos3_mutex_unlock(&mutex);

    /* Killing a queued writer cannot strand a reader. */
    vos3_rwlock_t rw;vos3_rwlock_init(&rw,"rw");vos3_rwlock_wrlock(&rw);
    _Atomic int done=0;vos3_task_t wt={.state=VOS3_TASK_READY,.name="writer"},rt={.state=VOS3_TASK_READY,.name="reader"};
    rwarg_t wa={&rw,&wt,&done},ra={&rw,&rt,&done};pthread_t wth,rth;
    assert(!pthread_create(&wth,NULL,writer_once,&wa));assert(!pthread_create(&rth,NULL,reader_once,&ra));
    wait_yields(&wt,1);wait_yields(&rt,1);kill_waiter(&wt);assert(!pthread_join(wth,NULL));
    tls_current=&main_task;vos3_rwlock_wrunlock(&rw);assert(!pthread_join(rth,NULL));assert(atomic_load(&done)==1);

    /* If a woken writer is killed before acquisition, the simultaneously
       woken reader still progresses. */
    vos3_rwlock_wrlock(&rw);memset(&wt,0,sizeof(wt));memset(&rt,0,sizeof(rt));wt.state=rt.state=VOS3_TASK_READY;wt.name="held-writer";rt.name="surviving-reader";done=0;
    assert(!pthread_create(&wth,NULL,writer_once,&wa));assert(!pthread_create(&rth,NULL,reader_once,&ra));wait_yields(&wt,1);wait_yields(&rt,1);
    held_task=&wt;release_held=0;tls_current=&main_task;vos3_rwlock_wrunlock(&rw);assert(!pthread_join(rth,NULL));
    pthread_mutex_lock(&event_lock);wt.state=VOS3_TASK_DEAD;release_held=1;pthread_cond_broadcast(&event_cv);pthread_mutex_unlock(&event_lock);
    assert(!pthread_join(wth,NULL));held_task=NULL;assert(atomic_load(&done)==1);

    /* Foreign writer unlock cannot release the owner. */
    vos3_rwlock_wrlock(&rw);vos3_task_t foreign={.state=VOS3_TASK_READY,.name="foreign"};tls_current=&foreign;vos3_rwlock_wrunlock(&rw);
    assert(vos3_atomic32_load(&rw.writers)==1&&rw.writer==&main_task);tls_current=&main_task;vos3_rwlock_wrunlock(&rw);

    /* Reader acquisition refuses the signed counter boundary. */
    vos3_atomic32_store(&rw.readers,INT32_MAX);
    assert(vos3_rwlock_tryrdlock(&rw)==0);
    assert(vos3_atomic32_load(&rw.readers)==INT32_MAX);
    vos3_atomic32_store(&rw.readers,0);

    /* Repeated barrier generations, a spurious wake, and UINT32 wrap. */
    vos3_barrier_t barrier;vos3_barrier_init(&barrier,"barrier",2);_Atomic int serials=0;vos3_task_t bt={.state=VOS3_TASK_READY,.name="barrier-task"};barg_t ba={&barrier,&bt,100,&serials};pthread_t bth;
    assert(!pthread_create(&bth,NULL,barrier_loop,&ba));for(int i=0;i<100;i++)if(vos3_barrier_wait(&barrier))atomic_fetch_add(&serials,1);assert(!pthread_join(bth,NULL));assert(atomic_load(&serials)==100);

    vos3_atomic_store32(&barrier.generation.value,UINT32_MAX);vos3_atomic_store32(&barrier.count.value,0);memset(&bt,0,sizeof(bt));bt.state=VOS3_TASK_READY;bt.name="wrap-task";ba.loops=1;serials=0;
    assert(!pthread_create(&bth,NULL,barrier_loop,&ba));wait_yields(&bt,1);assert(vos3_barrier_wait(&barrier)==1);assert(!pthread_join(bth,NULL));assert(vos3_atomic_load32(&barrier.generation.value)==0);

    vos3_barrier_init(&barrier,"spurious",2);memset(&bt,0,sizeof(bt));bt.state=VOS3_TASK_READY;bt.name="spurious-task";ba.loops=1;serials=0;
    assert(!pthread_create(&bth,NULL,barrier_loop,&ba));wait_yields(&bt,1);external_wake(&bt);wait_yields(&bt,2);assert(vos3_barrier_wait(&barrier)==1);assert(!pthread_join(bth,NULL));
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-sync-primitives-") as directory:
            path = Path(directory)
            (path / "test.c").write_text(code)
            build = subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
                 "-fsanitize=undefined", "-fno-sanitize-recover=undefined",
                 str(path / "test.c"), "-o", str(path / "test")],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(path / "test")], capture_output=True,
                                 text=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_task_lifetime_paths_use_membership_protocol(self):
        task_source = (ROOT / "kernel/src/sched/task.c").read_text()
        wake = function(task_source, "void vos3_task_wake(")
        unblock = function(task_source, "void vos3_task_unblock(")
        destroy = function(task_source, "int vos3_task_destroy(")
        defer = function(task_source, "void vos3_task_defer_destroy(")
        kill = function(task_source, "void vos3_task_kill(")
        self.assertLess(wake.index("vos3_wq_cancel(task)"),
                        wake.index("task->state = VOS3_TASK_READY"))
        self.assertLess(wake.index("vos3_irq_save()"),
                        wake.index("vos3_wq_cancel(task)"))
        self.assertLess(wake.index("task->state = VOS3_TASK_READY"),
                        wake.index("vos3_irq_restore(flags)"))
        self.assertIn("vos3_task_wake(task);", unblock)
        self.assertNotIn("task->state = VOS3_TASK_READY", unblock)
        # Direct destruction is now a strict deferred wrapper.  Membership is
        # cancelled by the deferred route; terminal kill still cancels before
        # publishing/removing a blocked task.
        self.assertIn("vos3_task_defer_destroy(task);", destroy)
        self.assertNotIn("vos3_wq_cancel(task)", destroy)
        for body in (defer, kill):
            self.assertIn("vos3_wq_cancel(task)", body)
        self.assertLess(kill.index("vos3_irq_save()"),
                        kill.index("vos3_wq_cancel(task)"))
        self.assertLess(kill.index("VOS3_TASK_ZOMBIE, __ATOMIC_RELEASE"),
                        kill.index("vos3_sched_remove_task(task)"))
        self.assertLess(kill.index("vos3_sched_remove_task(task)"),
                        kill.rindex("vos3_irq_restore(transition_flags)"))

        for relative in ("kernel/src/exec/exec.c",
                         "kernel/src/exec/exec_syscall.c"):
            clone_source = (ROOT / relative).read_text()
            self.assertIn("child->wq_entry.task = NULL;", clone_source)
            self.assertIn("child->wq_entry.next = NULL;", clone_source)
            self.assertIn("child->wq_entry.queue = NULL;", clone_source)

    def test_semaphore_initialization_rejects_signed_overflow(self):
        source = (ROOT / "kernel/src/sched/sync.c").read_text()
        init = function(source, "void vos3_sem_init(")
        bounded = function(source, "void vos3_sem_init_bounded(")
        post = function(source, "void vos3_sem_post(")
        self.assertIn("initial_count > (uint32_t)INT32_MAX", init)
        self.assertIn("max_count > (uint32_t)INT32_MAX", bounded)
        self.assertIn("initial_count > max_count", bounded)
        self.assertLess(bounded.index("if (sem == NULL)"),
                        bounded.index("vos3_sem_init(sem"))
        self.assertIn("count >= INT32_MAX", post)


if __name__ == "__main__":
    unittest.main()
