"""Race the actual message-queue lifetime functions under ASan/UBSan."""
from pathlib import Path
import subprocess
import tempfile
import unittest

if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class MessageQueueLifetimeTests(unittest.TestCase):
    def test_task_lifetime_paths_reset_and_run_wait_cleanup(self):
        task_source = (ROOT / "kernel/src/sched/task.c").read_text()
        destroy = function(task_source, "int vos3_task_destroy(")
        defer = function(task_source, "void vos3_task_defer_destroy(")
        reap = function(task_source, "void vos3_task_reap(")
        # Published tasks always take the deferred route: wait cleanup may own
        # queue state and therefore must run only after the scheduler reaper
        # has claimed execution/stack ownership.
        self.assertIn("vos3_task_defer_destroy(task);", destroy)
        self.assertNotIn("vos3_task_run_wait_cleanup(task);", destroy)
        self.assertIn("vos3_wq_cancel(task)", defer)
        self.assertIn("vos3_sched_claim_task_reap(task)", reap)
        self.assertLess(reap.index("vos3_sched_claim_task_reap(task)"),
                        reap.index("vos3_task_run_wait_cleanup(task);"))
        for relative in ("kernel/src/exec/exec.c",
                         "kernel/src/exec/exec_syscall.c"):
            source = (ROOT / relative).read_text()
            self.assertIn("child->wait_cleanup = NULL;", source)
            self.assertIn("child->wait_cleanup_context = NULL;", source)

    def test_task_wait_cleanup_has_exactly_one_owner(self):
        source = (ROOT / "kernel/src/sched/task.c").read_text()
        actual = "\n".join(function(source, name) for name in (
            "int vos3_task_arm_wait_cleanup(",
            "int vos3_task_disarm_wait_cleanup(",
            "void vos3_task_run_wait_cleanup(",
        ))
        code = r'''
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <string.h>
#define VOS3_TASK_READY 0
#define VOS3_TASK_ZOMBIE 4
#define VOS3_TASK_DEAD 5
#define VOS3_PANIC(...) do { assert(!"panic"); } while (0)
typedef int vos3_task_state_t;
typedef void (*vos3_task_wait_cleanup_t)(void *);
typedef struct vos3_task {int state;char name[8];vos3_task_wait_cleanup_t wait_cleanup;void *wait_cleanup_context;} vos3_task_t;
int vos3_task_disarm_wait_cleanup(vos3_task_t *,vos3_task_wait_cleanup_t,void *);
''' + actual + r'''
static _Atomic int calls,go;
static void cleanup(void *p){assert(p);atomic_fetch_add(&calls,1);}
typedef struct {vos3_task_t *task;int won;} arg_t;
static void *disarm_thread(void *raw){arg_t *a=raw;while(!atomic_load(&go)){}a->won=vos3_task_disarm_wait_cleanup(a->task,cleanup,a->task);return NULL;}
static void *reap_thread(void *raw){arg_t *a=raw;while(!atomic_load(&go)){}vos3_task_run_wait_cleanup(a->task);return NULL;}
int main(void){
 vos3_task_t task={.state=VOS3_TASK_READY};strcpy(task.name,"task");
 assert(vos3_task_arm_wait_cleanup(&task,cleanup,&task)==1);assert(vos3_task_disarm_wait_cleanup(&task,cleanup,&task)==1);cleanup(&task);assert(atomic_load(&calls)==1);
 task.state=VOS3_TASK_ZOMBIE;assert(vos3_task_arm_wait_cleanup(&task,cleanup,&task)==0);task.state=VOS3_TASK_READY;
 for(int i=0;i<10000;i++){
   calls=0;go=0;assert(vos3_task_arm_wait_cleanup(&task,cleanup,&task)==1);arg_t d={&task,0},r={&task,0};pthread_t a,b;
   assert(!pthread_create(&a,NULL,disarm_thread,&d));assert(!pthread_create(&b,NULL,reap_thread,&r));atomic_store(&go,1);assert(!pthread_join(a,NULL));assert(!pthread_join(b,NULL));
   if(d.won){cleanup(&task);}assert(atomic_load(&calls)==1);assert(task.wait_cleanup==NULL);assert(task.wait_cleanup_context==NULL);
 }
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-task-wait-cleanup-") as directory:
            path = Path(directory)
            (path / "test.c").write_text(code)
            build = subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
                 "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
                 str(path / "test.c"), "-o", str(path / "test")],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(path / "test")], capture_output=True,
                                 text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_blocked_receive_and_operation_stress_destroy_safely(self):
        source = (ROOT / "kernel/src/ipc/msgqueue.c").read_text()
        names = [
            "static size_t msgq_slot(",
            "static int msgq_id_valid(",
            "static void msgq_finalize(",
            "static void msgq_put_ref(",
            "static void msgq_put_wait_ref(",
            "static vos3_msgqueue_t* msgq_get_ref(",
            "static vos3_ipc_id_t msgq_alloc_id(",
            "vos3_ipc_id_t vos3_msgq_create(",
            "int vos3_msgq_destroy(",
            "int vos3_msgq_send(",
            "int64_t vos3_msgq_recv(",
            "vos3_ipc_id_t vos3_msgq_find(",
            "int64_t vos3_msgq_count(",
        ]
        actual = "\n".join(function(source, name) for name in names)
        code = r'''
#include <assert.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define VOS3_IPC_MAX_OBJECTS 256U
#define VOS3_IPC_INVALID 0xFFFFFFFFU
#define VOS3_IPC_EEXIST 0xFFFFFFFEU
#define VOS3_MSG_QUEUE_MAX 256U
#define VOS3_MSG_MAX_SIZE 4096U
#define VOS3_MSGQ_MAGIC 0x4D534751U
#define VOS3_MSGQ_SLOT_BITS 8U
#define VOS3_MSGQ_SLOT_MASK ((vos3_ipc_id_t)0xFFU)
#define VOS3_MSGQ_GENERATION_MAX ((uint32_t)0x007FFFFFU)
#define VOS3_SEM_MAGIC 0x53454D41U
#define VOS3_MSGQ_FLAG_NONBLOCK (1U<<0)
#define VOS3_SYNC_OK 0
#define VOS3_SYNC_ERR_CLOSED (-6)
#define VOS3_IPC_OK 0
#define VOS3_IPC_ERR_NOMEM (-1)
#define VOS3_IPC_ERR_INVALID (-2)
#define VOS3_IPC_ERR_NOTFOUND (-3)
#define VOS3_IPC_ERR_EMPTY (-5)
#define VOS3_IPC_ERR_WOULDBLOCK (-11)
#define VOS3_IPC_ERR_INTR (-12)
#define VOS3_IPC_ERR_ACCESS (-13)
#define VOS3_DEBUG(...) ((void)0)
#define VOS3_PANIC(...) do { assert(!"panic"); } while (0)
#define VOS3_SPINLOCK_INIT PTHREAD_MUTEX_INITIALIZER

typedef uint32_t vos3_ipc_id_t;
typedef uint32_t vos3_tid_t;
typedef unsigned long vos3_irqflags_t;
typedef pthread_mutex_t vos3_spinlock_t;
typedef struct { _Atomic int32_t value; } vos3_atomic32_t;
typedef void (*vos3_task_wait_cleanup_t)(void *);
typedef struct vos3_task {
    vos3_tid_t tid;uint64_t identity_cookie;
    vos3_task_wait_cleanup_t wait_cleanup;void *wait_cleanup_context;
} vos3_task_t;
typedef struct {
    uint32_t magic;
    vos3_atomic32_t count;
    vos3_atomic32_t closed;
    int32_t max_count;
    pthread_mutex_t lock;
    pthread_cond_t cv;
    size_t waiters;
    const char *name;
} vos3_semaphore_t;
typedef struct vos3_msg_header {vos3_tid_t sender;uint32_t type;size_t size;uint64_t timestamp;} vos3_msg_header_t;
typedef struct vos3_message {vos3_msg_header_t header;uint8_t data[];} vos3_message_t;
typedef struct vos3_msgq_entry {struct vos3_msgq_entry *next;size_t total_size;vos3_message_t msg;} vos3_msgq_entry_t;
typedef struct vos3_msgqueue {
    uint32_t magic;vos3_ipc_id_t id;char name[32];
    vos3_msgq_entry_t *head,*tail;size_t count,max_count,max_msg_size;
    vos3_spinlock_t lock;vos3_semaphore_t sem_space,sem_msgs;
    vos3_atomic32_t active_ops;volatile uint32_t closing;
    vos3_tid_t owner;uint64_t owner_identity;uint32_t flags;
} vos3_msgqueue_t;

static vos3_msgqueue_t *g_msgq_table[VOS3_IPC_MAX_OBJECTS];
static uint32_t g_msgq_generation[VOS3_IPC_MAX_OBJECTS];
static vos3_spinlock_t g_msgq_lock=VOS3_SPINLOCK_INIT;
static _Thread_local vos3_task_t tls_task={.tid=1,.identity_cookie=1};
static _Thread_local int tls_has_task=1;
static _Thread_local unsigned irq_depth;
static _Atomic int freed_while_active;
static _Atomic int watched_frees;
static vos3_msgqueue_t *watched_mq;

static vos3_irqflags_t vos3_irq_save(void){return irq_depth++;}
static void vos3_irq_restore(vos3_irqflags_t flags){assert(irq_depth);irq_depth=(unsigned)flags;}
static void vos3_spinlock_init(vos3_spinlock_t *l){assert(!pthread_mutex_init(l,NULL));}
static void vos3_spinlock_lock(vos3_spinlock_t *l){assert(!pthread_mutex_lock(l));}
static void vos3_spinlock_unlock(vos3_spinlock_t *l){assert(!pthread_mutex_unlock(l));}
static int32_t vos3_atomic32_load(const vos3_atomic32_t *a){return atomic_load(&a->value);}
static void vos3_atomic32_store(vos3_atomic32_t *a,int32_t v){atomic_store(&a->value,v);}
static int32_t vos3_atomic32_fetch_add(vos3_atomic32_t *a,int32_t v){return atomic_fetch_add(&a->value,v);}
static int32_t vos3_atomic32_fetch_sub(vos3_atomic32_t *a,int32_t v){return atomic_fetch_sub(&a->value,v);}
static void *vos3_kzalloc(size_t n){return calloc(1,n);}
static void *vos3_kmalloc(size_t n){return malloc(n);}
static void vos3_kfree(void *p){
    if(p==watched_mq){if(atomic_load(&watched_mq->active_ops.value)!=0)atomic_store(&freed_while_active,1);atomic_fetch_add(&watched_frees,1);}
    free(p);
}
static vos3_task_t *vos3_sched_current(void){return tls_has_task?&tls_task:NULL;}
static int vos3_task_arm_wait_cleanup(vos3_task_t *task,vos3_task_wait_cleanup_t fn,void *ctx){
    if(!task||task->wait_cleanup)return 0;task->wait_cleanup_context=ctx;task->wait_cleanup=fn;return 1;
}
static int vos3_task_disarm_wait_cleanup(vos3_task_t *task,vos3_task_wait_cleanup_t fn,void *ctx){
    if(!task||task->wait_cleanup!=fn||task->wait_cleanup_context!=ctx)return 0;task->wait_cleanup=NULL;task->wait_cleanup_context=NULL;return 1;
}
static void vos3_task_run_wait_cleanup(vos3_task_t *task){
    vos3_task_wait_cleanup_t fn=task->wait_cleanup;void *ctx=task->wait_cleanup_context;task->wait_cleanup=NULL;task->wait_cleanup_context=NULL;if(fn)fn(ctx);
}
static uint64_t vos3_timer_get_ticks(void){return 1;}
static void vos3_sem_init(vos3_semaphore_t *s,const char *name,uint32_t count){
    memset(s,0,sizeof(*s));s->magic=VOS3_SEM_MAGIC;atomic_store(&s->count.value,(int32_t)count);s->max_count=-1;s->name=name;
    assert(!pthread_mutex_init(&s->lock,NULL));assert(!pthread_cond_init(&s->cv,NULL));
}
static void vos3_sem_close(vos3_semaphore_t *s){
    assert(!pthread_mutex_lock(&s->lock));atomic_store(&s->closed.value,1);pthread_cond_broadcast(&s->cv);assert(!pthread_mutex_unlock(&s->lock));
}
static int vos3_sem_wait_status(vos3_semaphore_t *s){
    assert(!pthread_mutex_lock(&s->lock));
    while(atomic_load(&s->count.value)==0&&!atomic_load(&s->closed.value)){s->waiters++;pthread_cond_wait(&s->cv,&s->lock);s->waiters--;}
    if(atomic_load(&s->closed.value)){pthread_mutex_unlock(&s->lock);return VOS3_SYNC_ERR_CLOSED;}
    atomic_fetch_sub(&s->count.value,1);pthread_mutex_unlock(&s->lock);return VOS3_SYNC_OK;
}
static int vos3_sem_trywait(vos3_semaphore_t *s){
    assert(!pthread_mutex_lock(&s->lock));int ok=!atomic_load(&s->closed.value)&&atomic_load(&s->count.value)>0;
    if(ok)atomic_fetch_sub(&s->count.value,1);pthread_mutex_unlock(&s->lock);return ok;
}
static void vos3_sem_post(vos3_semaphore_t *s){
    assert(!pthread_mutex_lock(&s->lock));if(!atomic_load(&s->closed.value)){atomic_fetch_add(&s->count.value,1);pthread_cond_signal(&s->cv);}pthread_mutex_unlock(&s->lock);
}
static void vos3_sem_destroy(vos3_semaphore_t *s){assert(!s->waiters);s->magic=0;assert(!pthread_cond_destroy(&s->cv));assert(!pthread_mutex_destroy(&s->lock));}
''' + actual + r'''

typedef struct {vos3_ipc_id_t id;int64_t result;} recv_arg_t;
static void *blocked_recv(void *raw){recv_arg_t *a=raw;uint8_t byte;a->result=vos3_msgq_recv(a->id,NULL,&byte,1,0);return NULL;}
typedef struct {vos3_ipc_id_t id;int result;} destroy_arg_t;
static void *destroy_queue(void *raw){destroy_arg_t *a=raw;a->result=vos3_msgq_destroy(a->id);return NULL;}
typedef struct {vos3_ipc_id_t id;int which;_Atomic int *stop;} stress_arg_t;
static void *stress_op(void *raw){
    stress_arg_t *a=raw;uint8_t byte=7;
    while(!atomic_load(a->stop)){
        if(a->which==0)(void)vos3_msgq_send(a->id,1,&byte,1,VOS3_MSGQ_FLAG_NONBLOCK);
        else if(a->which==1)(void)vos3_msgq_recv(a->id,NULL,&byte,1,VOS3_MSGQ_FLAG_NONBLOCK);
        else if(a->which==2)(void)vos3_msgq_count(a->id);
        else (void)vos3_msgq_find("stress");
    }
    return NULL;
}
static void wait_for_receiver(vos3_msgqueue_t *mq){
    for(int i=0;i<100000;i++){pthread_mutex_lock(&mq->sem_msgs.lock);size_t n=mq->sem_msgs.waiters;pthread_mutex_unlock(&mq->sem_msgs.lock);if(n)return;sched_yield();}
    assert(!"receiver did not block");
}

int main(void){
    vos3_ipc_id_t id=vos3_msgq_create("lifetime",8,32);assert(id!=VOS3_IPC_INVALID);watched_mq=g_msgq_table[id&0xFFU];
    recv_arg_t ra={.id=id};pthread_t receiver;assert(!pthread_create(&receiver,NULL,blocked_recv,&ra));wait_for_receiver(watched_mq);
    destroy_arg_t da={.id=id};pthread_t destroyer;assert(!pthread_create(&destroyer,NULL,destroy_queue,&da));
    assert(!pthread_join(receiver,NULL));assert(!pthread_join(destroyer,NULL));assert(ra.result==VOS3_IPC_ERR_NOTFOUND);assert(da.result==VOS3_IPC_OK);assert(!atomic_load(&freed_while_active));

    watched_mq=NULL;vos3_ipc_id_t stale=id;id=vos3_msgq_create("replacement",8,32);assert(id!=stale);assert(vos3_msgq_count(stale)==VOS3_IPC_ERR_NOTFOUND);
    tls_task.identity_cookie=2;assert(vos3_msgq_destroy(id)==VOS3_IPC_ERR_ACCESS);assert(vos3_msgq_count(id)==0);tls_task.identity_cookie=1;assert(vos3_msgq_destroy(id)==VOS3_IPC_OK);

    /* Ownerless queues are kernel-owned: user task callers cannot destroy them. */
    tls_has_task=0;id=vos3_msgq_create("kernel",8,32);assert(id!=VOS3_IPC_INVALID);tls_has_task=1;
    assert(vos3_msgq_destroy(id)==VOS3_IPC_ERR_ACCESS);tls_has_task=0;assert(vos3_msgq_destroy(id)==VOS3_IPC_OK);tls_has_task=1;

    /* A quiesced killed waiter leaves final ownership to task cleanup. */
    id=vos3_msgq_create("killed",8,32);assert(id!=VOS3_IPC_INVALID);watched_mq=g_msgq_table[id&0xFFU];int frees_before=atomic_load(&watched_frees);
    vos3_msgqueue_t *pin=msgq_get_ref(id);assert(pin==watched_mq);assert(vos3_task_arm_wait_cleanup(&tls_task,msgq_put_wait_ref,pin)==1);
    assert(vos3_msgq_destroy(id)==VOS3_IPC_OK);assert(atomic_load(&watched_frees)==frees_before);vos3_task_run_wait_cleanup(&tls_task);assert(atomic_load(&watched_frees)==frees_before+1);

    for(int round=0;round<100;round++){
        id=vos3_msgq_create("stress",8,32);assert(id!=VOS3_IPC_INVALID);watched_mq=g_msgq_table[id&0xFFU];_Atomic int stop=0;
        pthread_t workers[12];stress_arg_t args[12];for(int i=0;i<12;i++){args[i]=(stress_arg_t){id,i%4,&stop};assert(!pthread_create(&workers[i],NULL,stress_op,&args[i]));}
        for(int i=0;i<100;i++)sched_yield();assert(vos3_msgq_destroy(id)==VOS3_IPC_OK);atomic_store(&stop,1);
        for(int i=0;i<12;i++)assert(!pthread_join(workers[i],NULL));assert(!atomic_load(&freed_while_active));
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-msgq-lifetime-") as directory:
            path = Path(directory)
            (path / "test.c").write_text(code)
            build = subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
                 "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
                 str(path / "test.c"), "-o", str(path / "test")],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(path / "test")], capture_output=True,
                                 text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
