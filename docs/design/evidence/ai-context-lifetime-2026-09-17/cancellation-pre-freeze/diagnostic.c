
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
static vos3_irqflags_t ai_ctx_registry_lock(void)
{
    vos3_irqflags_t flags = vos3_irq_save();
    vos3_spinlock_lock(&g_ai_ctx_registry_lock);
    return flags;
}
static void ai_ctx_registry_unlock(vos3_irqflags_t flags)
{
    vos3_spinlock_unlock(&g_ai_ctx_registry_lock);
    vos3_irq_restore(flags);
}
static vos3_ai_guard_ctx_t* ai_ctx_retain_locked(vos3_ai_guard_ctx_t* ctx)
{
    if (ctx == NULL || ctx->closing || ctx->lifetime_refs == 0U ||
        ctx->lifetime_refs == UINT32_MAX)
        return NULL;
    ctx->lifetime_refs++;
    return ctx;
}
static void ai_ctx_put_locked(vos3_ai_guard_ctx_t* ctx)
{
    if (ctx->lifetime_refs == 0U ||
        (ctx->lifetime_refs == 1U && !ctx->closing)) {
        VOS3_PANIC("AI context reference ownership violation");
        return;
    }
    if (--ctx->lifetime_refs == 0U) {
        ctx->retired_next = g_retired_contexts;
        g_retired_contexts = ctx;
        /* This helper only stores a per-CPU pending bit; no locks/callbacks. */
        vos3_sched_request_deferred();
    }
}
static void ai_ctx_close_locked(vos3_ai_guard_ctx_t* ctx)
{
    if (ctx->closing) return;
    ctx->closing = 1U;
    if (g_global_ctx == ctx) g_global_ctx = NULL;
    for (uint8_t id = 0; id < VOS3_MAX_APP_CONTEXTS; id++) {
        if (g_app_contexts[id] != ctx) continue;
        g_app_contexts[id] = NULL;
        if (g_active_app_id == id) g_active_app_id = 0U;
    }
    ai_ctx_put_locked(ctx); /* Consume the creator/app registry owner. */
}
static vos3_ai_guard_ctx_t* ai_ctx_alloc_unpublished(void)
{
    if (!g_ai_guard_initialized) {
        vos3_ai_guard_init();
    }

    vos3_ai_guard_ctx_t* ctx = vos3_kzalloc(sizeof(vos3_ai_guard_ctx_t));
    if (ctx == NULL) {
        return NULL;
    }

    ctx->lock = 0;
    ctx->lifetime_refs = 1U;
    ctx->closing = 0U;
    ctx->retired_next = NULL;
    ctx->regions = NULL;
    ctx->region_count = 0;
    ctx->total_protected = 0;
    ctx->total_faults = 0;
    ctx->total_violations = 0;
    ctx->flags = 0;
    ctx->max_regions = VOS3_AI_GUARD_MAX_REGIONS;

    /* Initialize quota fields (Phase 17.4) */
    ctx->quota_limit = 0;       /* 0 = unlimited */
    ctx->quota_used = 0;
    ctx->quota_enforce = 0;     /* Default: warn only */
    ctx->quota_violations = 0;

    __atomic_fetch_add(&g_ai_guard_stats.total_contexts, 1, __ATOMIC_RELAXED);

    return ctx;
}
vos3_ai_guard_ctx_t* vos3_ai_guard_ctx_create(void)
{
    vos3_ai_guard_ctx_t* ctx = ai_ctx_alloc_unpublished();
    if (ctx == NULL) return NULL;
    vos3_irqflags_t flags = ai_ctx_registry_lock();
    if (g_global_ctx == NULL) g_global_ctx = ctx;
    ai_ctx_registry_unlock(flags);
    return ctx;
}
vos3_ai_guard_ctx_t* vos3_ai_guard_acquire_global_ctx(void)
{
    vos3_irqflags_t flags = ai_ctx_registry_lock();
    vos3_ai_guard_ctx_t* ctx = ai_ctx_retain_locked(g_global_ctx);
    ai_ctx_registry_unlock(flags);
    return ctx;
}
vos3_ai_guard_ctx_t* vos3_ai_guard_acquire_app_ctx(uint8_t app_id)
{
    if (app_id >= VOS3_MAX_APP_CONTEXTS) return NULL;
    vos3_irqflags_t flags = ai_ctx_registry_lock();
    vos3_ai_guard_ctx_t* ctx = ai_ctx_retain_locked(g_app_contexts[app_id]);
    ai_ctx_registry_unlock(flags);
    return ctx;
}
void vos3_ai_guard_ctx_put(vos3_ai_guard_ctx_t* ctx)
{
    if (ctx == NULL) return;
    vos3_irqflags_t flags = ai_ctx_registry_lock();
    ai_ctx_put_locked(ctx);
    ai_ctx_registry_unlock(flags);
}
void vos3_ai_guard_ctx_destroy(vos3_ai_guard_ctx_t* ctx)
{
    if (ctx == NULL) return;
    vos3_irqflags_t flags = ai_ctx_registry_lock();
    ai_ctx_close_locked(ctx);
    ai_ctx_registry_unlock(flags);
}
static void ai_ctx_finalize(vos3_ai_guard_ctx_t* ctx)
{
    /* Free all regions */
    vos3_ai_guard_region_t* region = ctx->regions;
    while (region != NULL) {
        vos3_ai_guard_region_t* next = region->next;

        /* Remove guard pages if present */
        if (vos3_ai_guard_has_red_zones(region)) {
            remove_guard_pages(region->guard_lo, region->guard_hi);
        }

        /* Free the region memory */
        if (region->base != 0) {
            size_t pages = align_to_page(region->size) / VOS3_PAGE_SIZE;
            for (size_t i = 0; i < pages; i++) {
                uintptr_t addr = region->base + (i * VOS3_PAGE_SIZE);
                uintptr_t phys = get_phys_addr(addr);
                vos3_vmm_unmap(addr);
                if (phys != 0) {
                    vos3_pmm_free(phys);
                }
            }
        }

        region_free(region);
        region = next;
    }

    vos3_kfree(ctx);
    __atomic_fetch_sub(&g_ai_guard_stats.total_contexts, 1, __ATOMIC_RELAXED);
#ifdef AI_CONTEXT_LIFETIME_TEST
    __atomic_fetch_add(&g_finalized_contexts, 1U, __ATOMIC_RELAXED);
#endif
}
void vos3_ai_guard_reap_contexts(void)
{
    /* Last puts may occur in an ISR. Only safe task continuations reclaim. */
    vos3_irqflags_t flags = vos3_irq_save();
    if (!(flags & (1ULL << 9)) || vos3_sched_current() == NULL) {
        vos3_irq_restore(flags);
        return;
    }
    vos3_spinlock_lock(&g_ai_ctx_registry_lock);
    vos3_ai_guard_ctx_t* list = g_retired_contexts;
    g_retired_contexts = NULL;
    vos3_spinlock_unlock(&g_ai_ctx_registry_lock);
    vos3_irq_restore(flags);
    while (list != NULL) {
        vos3_ai_guard_ctx_t* next = list->retired_next;
        ai_ctx_finalize(list);
        list = next;
    }
}
int vos3_ai_guard_create_app_ctx(uint8_t app_id)
{
    if (app_id >= VOS3_MAX_APP_CONTEXTS) {
        vos3_console_printf("[AI-GUARD] Invalid app_id %u (max %u)\n",
                            app_id, VOS3_MAX_APP_CONTEXTS - 1U);
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    vos3_irqflags_t flags = ai_ctx_registry_lock();
    if (g_app_contexts[app_id] != NULL) {
        ai_ctx_registry_unlock(flags);
        return VOS3_AI_GUARD_ERR_INVALID;
    }
    ai_ctx_registry_unlock(flags);

    vos3_ai_guard_ctx_t* ctx = ai_ctx_alloc_unpublished();
    if (ctx == NULL) {
        return VOS3_AI_GUARD_ERR_NOMEM;
    }

    flags = ai_ctx_registry_lock();
    if (g_app_contexts[app_id] != NULL) {
        /* A concurrent creator won. This candidate was never published. */
        ai_ctx_close_locked(ctx);
        ai_ctx_registry_unlock(flags);
        return VOS3_AI_GUARD_ERR_INVALID;
    }
    g_app_contexts[app_id] = ctx;
    if (g_global_ctx == NULL) g_global_ctx = ctx;
    ai_ctx_registry_unlock(flags);

    vos3_console_printf("[AI-GUARD] Created app context %u\n", app_id);
    return VOS3_AI_GUARD_OK;
}
int vos3_ai_guard_destroy_app_ctx(uint8_t app_id)
{
    if (app_id >= VOS3_MAX_APP_CONTEXTS) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    vos3_irqflags_t flags = ai_ctx_registry_lock();
    vos3_ai_guard_ctx_t* ctx = g_app_contexts[app_id];
    if (ctx == NULL) {
        ai_ctx_registry_unlock(flags);
        return VOS3_AI_GUARD_ERR_NOTFOUND;
    }
    ai_ctx_close_locked(ctx);
    ai_ctx_registry_unlock(flags);

    vos3_console_printf("[AI-GUARD] Destroyed app context %u\n", app_id);
    return VOS3_AI_GUARD_OK;
}
int vos3_ai_guard_switch_ctx(uint8_t app_id)
{
    if (app_id >= VOS3_MAX_APP_CONTEXTS) {
        return VOS3_AI_GUARD_ERR_INVALID;
    }

    vos3_irqflags_t flags = ai_ctx_registry_lock();
    if (g_app_contexts[app_id] == NULL) {
        ai_ctx_registry_unlock(flags);
        return VOS3_AI_GUARD_ERR_NOTFOUND;
    }

    g_active_app_id = app_id;
    ai_ctx_registry_unlock(flags);

    vos3_console_printf("[AI-GUARD] Switched to app context %u\n", app_id);
    return VOS3_AI_GUARD_OK;
}
uint8_t vos3_ai_guard_get_active_app_id(void)
{
    vos3_irqflags_t flags = ai_ctx_registry_lock();
    uint8_t id = g_active_app_id;
    ai_ctx_registry_unlock(flags);
    return id;
}
int vos3_ai_guard_check_app_access(uintptr_t fault_addr, uint8_t current_app_id)
{
    /*
     * Cross-app memory barrier check.
     * If a page has been tagged with an app_id (via PTE bits 9-11),
     * only the matching app context may access it.
     *
     * NOTE: In a full implementation, we would read the actual PTE from the
     * page table. Here we check against all app contexts' region lists
     * to determine ownership, which is the safe approach for our current
     * memory management model.
     */
    for (uint8_t id = 0; id < VOS3_MAX_APP_CONTEXTS; id++) {
        if (id == current_app_id) {
            continue;  /* Skip our own context */
        }

        vos3_irqflags_t flags = ai_ctx_registry_lock();
        vos3_ai_guard_ctx_t* published = g_app_contexts[id];
        vos3_ai_guard_ctx_t* ctx = ai_ctx_retain_locked(published);
        ai_ctx_registry_unlock(flags);
        /* A present context that cannot be pinned must fail closed. */
        if (published != NULL && ctx == NULL) return -1;
        if (ctx == NULL) {
            continue;
        }

        /* Check if fault_addr falls within any region of a different app */
        vos3_ai_guard_region_t* region = ctx->regions;
        while (region != NULL) {
            uintptr_t start = region->base;
            uintptr_t end = region->base + region->size;
            if (fault_addr >= start && fault_addr < end) {
                /* Cross-app violation! */
                __atomic_fetch_add(&g_ai_guard_stats.total_violations, 1,
                                   __ATOMIC_RELAXED);
                ctx->total_violations++;

                vos3_console_printf(
                    "[AI-GUARD] CROSS-APP VIOLATION: app %u tried to access "
                    "app %u memory at %p\n",
                    current_app_id, id, (void*)fault_addr);

                vos3_ai_guard_ctx_put(ctx);
                return -1;  /* -EPERM */
            }
            region = region->next;
        }
        vos3_ai_guard_ctx_put(ctx);
    }

    return 0;  /* Access allowed */
}
#include <stdio.h>
int main(void){
 assert(vos3_ai_guard_create_app_ctx(7)==0);
 vos3_ai_guard_ctx_t *abandoned=vos3_ai_guard_acquire_app_ctx(7);
 assert(abandoned && abandoned->lifetime_refs==2);
 assert(vos3_ai_guard_destroy_app_ctx(7)==0);
 assert(abandoned->closing && abandoned->lifetime_refs==1);
 /* Model abandoned continuation: no task ledger/ctx_put is invoked.
  * This does not execute production task_kill or task_reap. */
 vos3_ai_guard_reap_contexts();
 assert(frees==0 && g_ai_guard_stats.total_contexts==1);
 printf("BLOCKING_GAP owner_refs=0 abandoned_reader_refs=%u allocations=%d frees=%d live_contexts=%llu discoverable=%d\n",abandoned->lifetime_refs,allocs,frees,(unsigned long long)g_ai_guard_stats.total_contexts,g_app_contexts[7]!=NULL);
 return 0;
}
