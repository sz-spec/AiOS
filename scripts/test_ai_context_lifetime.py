"""Actual production context lifetime, controlled interleavings; mocks are not SMP qualification."""
from pathlib import Path
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function
ROOT=Path(__file__).resolve().parents[1]
SIGNATURES=['static vos3_irqflags_t ai_ctx_registry_lock(', 'static void ai_ctx_registry_unlock(', 'static vos3_ai_guard_ctx_t* ai_ctx_retain_locked(', 'static void ai_ctx_put_locked(', 'static void ai_ctx_close_locked(', 'static vos3_ai_guard_ctx_t* ai_ctx_alloc_unpublished(', 'vos3_ai_guard_ctx_t* vos3_ai_guard_ctx_create(', 'vos3_ai_guard_ctx_t* vos3_ai_guard_acquire_global_ctx(', 'vos3_ai_guard_ctx_t* vos3_ai_guard_acquire_app_ctx(', 'void vos3_ai_guard_ctx_put(', 'void vos3_ai_guard_ctx_destroy(', 'static void ai_ctx_finalize(', 'void vos3_ai_guard_reap_contexts(', 'int vos3_ai_guard_create_app_ctx(', 'int vos3_ai_guard_destroy_app_ctx(', 'int vos3_ai_guard_switch_ctx(', 'uint8_t vos3_ai_guard_get_active_app_id(', 'int vos3_ai_guard_check_app_access(']
STUB = r'''
#include <assert.h>

#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <pthread.h>

#define VOS3_MAX_APP_CONTEXTS 8U
#define VOS3_PAGE_SIZE 4096U
#define VOS3_AI_GUARD_MAX_REGIONS 64U
#define VOS3_AI_GUARD_OK 0
#define VOS3_AI_GUARD_ERR_INVALID (-1)
#define VOS3_AI_GUARD_ERR_NOMEM (-2)
#define VOS3_AI_GUARD_ERR_NOTFOUND (-3)
#define vos3_console_printf(...) ((void)0)

typedef struct vos3_ai_guard_region {
    struct vos3_ai_guard_region *next;
    uintptr_t base, guard_lo, guard_hi;
    size_t size;
} vos3_ai_guard_region_t;
typedef struct vos3_ai_guard_ctx {
    int lock;
    uint32_t lifetime_refs, closing;
    struct vos3_ai_guard_ctx *retired_next;
    vos3_ai_guard_region_t *regions;
    size_t region_count, total_protected;
    uint64_t total_faults, total_violations;
    uint32_t flags;
    size_t max_regions, quota_limit, quota_used;
    int quota_enforce;
    uint64_t quota_violations;
} vos3_ai_guard_ctx_t;

static volatile int g_ai_guard_initialized = 1;
static struct {
    uint64_t total_contexts, total_regions, total_allocs, total_frees;
    uint64_t total_faults, total_violations;
} g_ai_guard_stats;
vos3_ai_guard_ctx_t *g_global_ctx;
vos3_ai_guard_ctx_t *g_app_contexts[VOS3_MAX_APP_CONTEXTS];
uint8_t g_active_app_id;

static int frees, region_frees, allocs, requests;
static vos3_ai_guard_ctx_t *destroying, *g_retired_contexts;
static pthread_mutex_t g_ai_ctx_registry_lock=PTHREAD_MUTEX_INITIALIZER;
typedef uint64_t vos3_irqflags_t;
static _Thread_local int irq=1, held=0;
static int current_task=1, race_alloc=0;
static pthread_mutex_t barrier_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t barrier_cond=PTHREAD_COND_INITIALIZER;
static int arrived;
#define VOS3_PANIC(...) abort()
static vos3_irqflags_t vos3_irq_save(void){int old=irq;irq=0;return old?512:0;}
static void vos3_irq_restore(vos3_irqflags_t f){irq=!!(f&512);}
static void vos3_spinlock_lock(pthread_mutex_t *m){assert(!irq&&!held);pthread_mutex_lock(m);held=1;}
static void vos3_spinlock_unlock(pthread_mutex_t *m){assert(held);held=0;pthread_mutex_unlock(m);}
static void vos3_sched_request_deferred(void){assert(held&&!irq);requests++;}
static void *vos3_sched_current(void){return current_task?(void*)1:NULL;}
static int vos3_ai_guard_init(void){return 0;}
static void *vos3_kzalloc(size_t n){
 assert(!held);void *p=calloc(1,n);assert(p);__atomic_fetch_add(&allocs,1,__ATOMIC_RELAXED);
 if(race_alloc){pthread_mutex_lock(&barrier_lock);arrived++;pthread_cond_broadcast(&barrier_cond);while(arrived<2)pthread_cond_wait(&barrier_cond,&barrier_lock);pthread_mutex_unlock(&barrier_lock);}return p;
}
static void safe(void){assert(irq&&!held&&current_task);}
static int vos3_ai_guard_has_red_zones(vos3_ai_guard_region_t *r){(void)r;safe();return 0;}
static void remove_guard_pages(uintptr_t a,uintptr_t b){(void)a;(void)b;safe();}
static size_t align_to_page(size_t n){return(n+4095)&~(size_t)4095;}
static uintptr_t get_phys_addr(uintptr_t a){safe();return a;}
static void vos3_vmm_unmap(uintptr_t a){(void)a;safe();}
static void vos3_pmm_free(uintptr_t a){(void)a;safe();}
static void detached(void *p){assert(g_global_ctx!=p);for(int i=0;i<8;i++)assert(g_app_contexts[i]!=p);}
static void region_free(vos3_ai_guard_region_t *r){safe();detached(destroying);region_frees++;free(r);}
static void vos3_kfree(void *p){safe();detached(p);frees++;free(p);}
'''
MAIN = r'''
static void *creator(void *arg){int *result=arg;*result=vos3_ai_guard_create_app_ctx(5);return NULL;}
static void *putter(void *arg){vos3_ai_guard_ctx_put(arg);return NULL;}
int main(void){
 assert(vos3_ai_guard_create_app_ctx(7)==0);
 vos3_ai_guard_ctx_t *pin=vos3_ai_guard_acquire_app_ctx(7);assert(pin&&pin->lifetime_refs==2);
 assert(vos3_ai_guard_switch_ctx(7)==0&&vos3_ai_guard_get_active_app_id()==7);
 pin->regions=calloc(1,sizeof(*pin->regions));pin->regions->next=calloc(1,sizeof(*pin->regions));destroying=pin;
 assert(vos3_ai_guard_destroy_app_ctx(7)==0);assert(pin->closing&&pin->lifetime_refs==1);
 assert(!vos3_ai_guard_acquire_app_ctx(7)&&!vos3_ai_guard_acquire_global_ctx());
 vos3_ai_guard_reap_contexts();assert(frees==0);pin->total_protected=123;assert(pin->regions->next);
 assert(vos3_ai_guard_create_app_ctx(7)==0);vos3_ai_guard_ctx_t *replacement=g_app_contexts[7];assert(replacement!=pin);
 vos3_ai_guard_ctx_put(pin);irq=0;vos3_ai_guard_reap_contexts();assert(frees==0&&!irq);irq=1;current_task=0;vos3_ai_guard_reap_contexts();assert(frees==0);current_task=1;
 vos3_ai_guard_reap_contexts();assert(frees==1&&region_frees==2&&g_app_contexts[7]==replacement);
 assert(vos3_ai_guard_destroy_app_ctx(7)==0);vos3_ai_guard_reap_contexts();assert(frees==2);
 vos3_ai_guard_ctx_t *direct=vos3_ai_guard_ctx_create();pin=vos3_ai_guard_acquire_global_ctx();assert(pin==direct);vos3_ai_guard_ctx_destroy(direct);vos3_ai_guard_reap_contexts();assert(frees==2);vos3_ai_guard_ctx_put(pin);vos3_ai_guard_reap_contexts();assert(frees==3);
 race_alloc=1;pthread_t a,b;int x=99,y=99;pthread_create(&a,NULL,creator,&x);pthread_create(&b,NULL,creator,&y);pthread_join(a,NULL);pthread_join(b,NULL);race_alloc=0;
 assert((x==0&&y==VOS3_AI_GUARD_ERR_INVALID)||(y==0&&x==VOS3_AI_GUARD_ERR_INVALID));assert(g_global_ctx==g_app_contexts[5]);vos3_ai_guard_reap_contexts();assert(frees==4);
 pin=g_app_contexts[5];pin->lifetime_refs=UINT32_MAX;assert(!vos3_ai_guard_acquire_app_ctx(5));assert(!vos3_ai_guard_acquire_global_ctx());assert(vos3_ai_guard_check_app_access(0x1000,0)==-1);assert(pin->lifetime_refs==UINT32_MAX);pin->lifetime_refs=1;
 vos3_ai_guard_ctx_t *p1=vos3_ai_guard_acquire_app_ctx(5),*p2=vos3_ai_guard_acquire_global_ctx();assert(p1==pin&&p2==pin&&pin->lifetime_refs==3);assert(vos3_ai_guard_destroy_app_ctx(5)==0);
 pthread_create(&a,NULL,putter,p1);pthread_create(&b,NULL,putter,p2);pthread_join(a,NULL);pthread_join(b,NULL);vos3_ai_guard_reap_contexts();assert(frees==5);vos3_ai_guard_reap_contexts();assert(frees==5&&allocs==5&&g_ai_guard_stats.total_contexts==0&&requests==5);
 return 0;
}
'''
class AIContextLifetimeTests(unittest.TestCase):
    def test_retained_readers_and_competing_creators(self):
        source=(ROOT/'kernel/src/mm/ai_guard.c').read_text()
        actual='\n'.join(function(source,s) for s in SIGNATURES)
        for mutant in (False,True):
            body=actual.replace('ctx->lifetime_refs++;','/* negative control: missing reader pin */') if mutant else actual
            with tempfile.TemporaryDirectory(prefix='vos-ai-pins-') as d:
                p=Path(d);(p/'test.c').write_text(STUB+body+MAIN)
                build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-pthread',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
                self.assertEqual(build.returncode,0,build.stderr)
                run=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=15)
                if mutant:self.assertNotEqual(run.returncode,0,'missing retain mutation escaped')
                else:self.assertEqual(run.returncode,0,run.stdout+run.stderr)
if __name__=='__main__':unittest.main()
