#!/usr/bin/env python3
"""Prepare diagnostic-only E7 tree and separate E6 overlay; never compile/run."""
import difflib, hashlib, json, shutil
from pathlib import Path
BASE=Path('/private/tmp/vos-sysinfo-qualified-20260918/source')
OUT=Path('/private/tmp/vos-council-instrument-20260918/source')
HERE=Path(__file__).resolve().parent
if OUT.exists(): raise SystemExit('Refusing existing diagnostic tree')
# Frozen source has no Git metadata. Exclude outputs at every depth.
shutil.copytree(BASE, OUT, ignore=shutil.ignore_patterns('build','dist','.git','__pycache__','node_modules','.venv','*.pyc'))
original={}
def edit(name, old, new):
 p=OUT/name; s=p.read_text(); original.setdefault(name,s)
 if s.count(old)!=1: raise RuntimeError((name,old[:90],s.count(old)))
 p.write_text(s.replace(old,new))
# Diagnostic-only ABI declarations, never added to canonical headers.
edit('kernel/include/vos/scheduler.h','#define VOS3_TIMER_FREQ', '''/* E7 DIAGNOSTIC ONLY: CPU0, one vCPU, process-owned recorder. */
int64_t vos3_diag_control(uint64_t op);
void vos3_diag_tick(void);
void vos3_diag_reschedule(unsigned reason);
#define VOS3_TIMER_FREQ''')
kernel=r'''
/* E7 diagnostic recorder. UP only. Fixed buffers, no allocation/printing armed.
 * Events count actual next!=prev selections, not expired time slices.
 * reason: 0 other, 1 timer IRQ, 2 voluntary yield. */
#define DIAG_N 2048U
#define DIAG_CENSUS 64U
static unsigned d_on, d_used, d_lost;
static uint32_t d_owner;
static uint64_t d_irq, d_calls[3], d_switch[3], d_begin, d_end;
struct d_event { uint64_t tsc, slice, granted; uint32_t prev, next, reason, priority; };
static struct d_event d_events[DIAG_N];
struct d_member { uint32_t tid, pid, state, priority; uint64_t slice; };
static struct d_member d_members[DIAG_CENSUS];
static unsigned d_members_n, d_members_lost;
static struct d_member d_seen[DIAG_CENSUS];
static unsigned d_seen_n,d_seen_drop,d_scan_drop,d_samples,d_run_min,d_run_max;
static uint32_t d_scan_tids[DIAG_CENSUS];
static unsigned d_scan_n;
static void d_scan_add(vos3_task_t *t) {
    if(!t) return;
    for(unsigned i=0;i<d_scan_n;i++) if(d_scan_tids[i]==t->tid) return;
    if(d_scan_n==DIAG_CENSUS) { d_scan_drop++; return; }
    d_scan_tids[d_scan_n++]=t->tid;
    for(unsigned i=0;i<d_seen_n;i++) if(d_seen[i].tid==t->tid) return;
    if(d_seen_n==DIAG_CENSUS) { d_seen_drop++; return; }
    d_seen[d_seen_n++]=(struct d_member){t->tid,t->pid,t->state,t->priority,t->time_slice};
}
static void d_sample_runqueue(vos3_task_t *next) {
    d_scan_n=0; d_scan_add(next);
    for(int q=-1;q<(int)VOS3_PRIORITY_COUNT;q++) {
        vos3_task_t *t=q<0?g_interactive_rq.head:g_run_queues[q].head;
        unsigned walked=0;
        while(t && walked++<DIAG_CENSUS) { d_scan_add(t); t=t->next; }
        if(t) d_scan_drop++;
    }
    if(!d_samples || d_scan_n<d_run_min) d_run_min=d_scan_n;
    if(!d_samples || d_scan_n>d_run_max) d_run_max=d_scan_n;
    d_samples++;
}
static uint64_t d_clock(void) {
    uint32_t lo, hi;
    __asm__ volatile("lfence; rdtsc" : "=a"(lo), "=d"(hi) :: "memory");
    return ((uint64_t)hi << 32) | lo;
}
static void d_member_add(vos3_task_t *t) {
    if (!t) return;
    if (d_members_n == DIAG_CENSUS) { d_members_lost++; return; }
    d_members[d_members_n++] = (struct d_member){t->tid,t->pid,t->state,t->priority,t->time_slice};
}
static int d_record(vos3_task_t *prev, vos3_task_t *next, unsigned reason) {
    if (!d_on) return -1;
    d_sample_runqueue(next);
    if (reason > 2) reason=0;
    d_calls[reason]++;
    if (prev == next) return -1;
    d_switch[reason]++;
    if (d_used == DIAG_N) { d_lost++; return -1; }
    d_events[d_used] = (struct d_event){d_clock(),next->time_slice,0,
        prev ? prev->tid : 0,next->tid,reason,next->priority};
    return (int)d_used++;
}
void vos3_diag_tick(void) { if (d_on) d_irq++; }
static void d_reschedule_impl(unsigned diag_reason);
void vos3_diag_reschedule(unsigned reason) {
    d_reschedule_impl(reason);
}
int64_t vos3_diag_control(uint64_t op) {
    vos3_task_t *cur=vos3_sched_current();
    if (!cur || get_cpu_id()!=0 || vos3_smp_cpu_count()>1) return -22;
    vos3_irqflags_t f=vos3_irq_save();
    if (op==1) {
        if (d_on) { vos3_irq_restore(f); return -16; }
        d_seen_n=d_seen_drop=d_scan_drop=d_samples=d_run_min=d_run_max=0;
        d_owner=cur->tid; d_used=d_lost=d_members_n=d_members_lost=0;
        d_irq=0; for (unsigned i=0;i<3;i++) d_calls[i]=d_switch[i]=0;
        vos3_spinlock_lock(&g_sched_lock);
        d_member_add(cur);
        for (int q=-1;q<(int)VOS3_PRIORITY_COUNT;q++) {
            vos3_task_t *t=(q<0 ? g_interactive_rq.head : g_run_queues[q].head);
            unsigned walked=0;
            while(t && walked++<DIAG_CENSUS) { d_member_add(t); t=t->next; }
            if(t) d_members_lost++;
        }
        vos3_spinlock_unlock(&g_sched_lock);
        d_begin=d_clock(); d_on=1;
        vos3_irq_restore(f); return 0;
    }
    if (op!=2 || !d_on || cur->tid!=d_owner) { vos3_irq_restore(f); return -22; }
    d_end=d_clock(); d_on=0;
    vos3_irq_restore(f);
    VOS3_INFO("SPSC_DIAG kernel begin=%llu end=%llu irq=%llu events=%u lost=%u census=%u census_lost=%u",
       (unsigned long long)d_begin,(unsigned long long)d_end,(unsigned long long)d_irq,d_used,d_lost,d_members_n,d_members_lost);
    for(unsigned i=0;i<3;i++) VOS3_INFO("SPSC_DIAG reason=%u calls=%llu switches=%llu",i,(unsigned long long)d_calls[i],(unsigned long long)d_switch[i]);
    for(unsigned i=0;i<d_members_n;i++) { struct d_member *m=&d_members[i];
       VOS3_INFO("SPSC_DIAG member tid=%u pid=%u state=%u priority=%u slice=%llu",m->tid,m->pid,m->state,m->priority,(unsigned long long)m->slice); }
    VOS3_INFO("SPSC_DIAG runnable samples=%u min=%u max=%u scan_drop=%u seen=%u seen_drop=%u",d_samples,d_run_min,d_run_max,d_scan_drop,d_seen_n,d_seen_drop);
    for(unsigned i=0;i<d_seen_n;i++) { struct d_member *m=&d_seen[i];
       VOS3_INFO("SPSC_DIAG seen tid=%u pid=%u state=%u priority=%u first_slice=%llu",m->tid,m->pid,m->state,m->priority,(unsigned long long)m->slice); }
    for(unsigned i=0;i<d_used;i++) { struct d_event *e=&d_events[i];
       VOS3_INFO("SPSC_DIAG switch i=%u tsc=%llu prev=%u next=%u reason=%u prior_slice=%llu granted_slice=%llu priority=%u",i,(unsigned long long)e->tsc,e->prev,e->next,e->reason,(unsigned long long)e->slice,(unsigned long long)e->granted,e->priority); }
    return 0;
}
'''
# All referenced globals already defined above this insertion.
edit('kernel/src/sched/scheduler.c','static void rq_init(vos3_run_queue_t* rq)',kernel+'\nstatic void rq_init(vos3_run_queue_t* rq)')
edit('kernel/src/sched/scheduler.c','    vos3_task_t* next = pick_next_task();\n\n    vos3_spinlock_unlock(&g_sched_lock);','    vos3_task_t* next = pick_next_task();\n    int diag_event=d_record(prev,next,diag_reason);\n\n    vos3_spinlock_unlock(&g_sched_lock);')
edit('kernel/src/sched/scheduler.c','static void do_context_switch(vos3_task_t* prev, vos3_task_t* next)','static void do_context_switch(vos3_task_t* prev, vos3_task_t* next, int diag_event)')
edit('kernel/src/sched/scheduler.c','        do_context_switch(prev, next);','        do_context_switch(prev, next, diag_event);')
edit('kernel/src/sched/scheduler.c','    next->context_switches++;','    if (diag_event >= 0) d_events[diag_event].granted=next->time_slice;\n    next->context_switches++;')
edit('kernel/src/sched/scheduler.c','void vos3_sched_reschedule(void)\n{','void vos3_sched_reschedule(void) { d_reschedule_impl(0); }\nstatic void d_reschedule_impl(unsigned diag_reason)\n{')
edit('kernel/src/sched/scheduler.c','    vos3_sched_reschedule();\n}\n\nvos3_task_t* vos3_sched_current','    vos3_diag_reschedule(2);\n}\n\nvos3_task_t* vos3_sched_current')
edit('kernel/src/arch/x86_64/interrupts.c','    vos3_timer_irq_handler();','    vos3_diag_tick();\n    vos3_timer_irq_handler();')
edit('kernel/src/arch/x86_64/interrupts.c','if (vos3_sched_is_running() != 0 && vos3_sched_need_reschedule() != 0) {\n        vos3_sched_reschedule();','if (vos3_sched_is_running() != 0 && vos3_sched_need_reschedule() != 0) {\n        vos3_diag_reschedule(1);')
edit('kernel/src/arch/x86_64/syscall.c','    int64_t result = handler(frame);','    /* Diagnostic-only GETPID control; retain permission and return paths. */\n    int64_t result = (syscall_num == 39 && frame->rdi == 0x5350534344494147ULL && frame->rdx == 0x453745364f4e4c59ULL) ? vos3_diag_control(frame->rsi) : handler(frame);')
user=r'''
#define D_N 512U
struct d_user_event { uint64_t tsc,head,tail; unsigned kind; };
static struct d_user_event d_prod[D_N],d_cons[D_N];
static unsigned d_np,d_nc,d_lp,d_lc;
static uint64_t d_full,d_empty;
static int d_was_full,d_was_empty;
static volatile int d_user_on;
static uint64_t d_clock(void) { unsigned lo,hi; __asm__ volatile("lfence; rdtsc":"=a"(lo),"=d"(hi)::"memory"); return ((uint64_t)hi<<32)|lo; }
static void d_user_event(int consumer,unsigned kind) {
    unsigned *n=consumer?&d_nc:&d_np,*lost=consumer?&d_lc:&d_lp;
    if(*n==D_N) { (*lost)++; return; }
    struct d_user_event *e=consumer?&d_cons[(*n)++]:&d_prod[(*n)++];
    e->tsc=d_clock(); e->head=__atomic_load_n(&g_hc_ring.head,__ATOMIC_ACQUIRE);
    e->tail=__atomic_load_n(&g_hc_ring.tail,__ATOMIC_ACQUIRE); e->kind=kind;
}
static long d_control(unsigned op) { return syscall3(39,0x5350534344494147ULL,op,0x453745364f4e4c59ULL); }
static void d_dump(void) {
    printf("SPSC_DIAG user full_yields=%lu empty_periods=%lu prod_events=%u cons_events=%u prod_lost=%u cons_lost=%u\n",d_full,d_empty,d_np,d_nc,d_lp,d_lc);
    for(unsigned c=0;c<2;c++) for(unsigned i=0;i<(c?d_nc:d_np);i++) {
        struct d_user_event *e=c?&d_cons[i]:&d_prod[i];
        printf("SPSC_DIAG transition role=%u i=%u kind=%u tsc=%lu head=%lu tail=%lu\n",c,i,e->kind,e->tsc,e->head,e->tail);
    }
}
'''
edit('user/src/test_health_check.c','static void consumer_thread(void)',user+'\nstatic void consumer_thread(void)')
edit('user/src/test_health_check.c','        if (spsc_pop(&g_hc_ring, &slot) == 0) {','        if (spsc_pop(&g_hc_ring, &slot) == 0) {\n            if(d_user_on && d_was_empty) { d_user_event(1,2); d_was_empty=0; }')
edit('user/src/test_health_check.c','            count++;\n        }','            count++;\n        } else if(d_user_on && !d_was_empty) { d_empty++; d_user_event(1,1); d_was_empty=1; }')
edit('user/src/test_health_check.c','    /* Producer: push for 1 second */','    d_user_on=1;\n    long d_arm=d_control(1);\n    /* Producer: push for 1 second */')
edit('user/src/test_health_check.c','            pushed++;','            pushed++;\n            if(d_was_full) { d_user_event(0,2); d_was_full=0; }')
edit('user/src/test_health_check.c','            /* Ring full — yield to let consumer drain */','            d_full++; if(!d_was_full) { d_user_event(0,1); d_was_full=1; }\n            /* Ring full — yield to let consumer drain */')
edit('user/src/test_health_check.c','    unsigned long elapsed = t_end - t_start;','    d_user_on=0;\n    long d_stop=d_control(2);\n    printf("SPSC_DIAG control arm=%ld stop=%ld\\n",d_arm,d_stop);\n    /* Dump only after canonical t_end. */\n    if(g_consumer_done) d_dump();\n    unsigned long elapsed = t_end - t_start;')
# E7 patch includes no init order change. E6 is a separate overlay.
patch=''.join(''.join(difflib.unified_diff(s.splitlines(True),(OUT/n).read_text().splitlines(True),fromfile='a/'+n,tofile='b/'+n)) for n,s in original.items())
(HERE/'e7.patch').write_text(patch)
init=(OUT/'user/src/init.c').read_text()
start=init.index('    /* Run each benchmark */'); end=init.index('    #undef RUN_BENCH',start)
e6=init[:start]+'    /* E6 diagnostic only: health is the first benchmark. */\n    RUN_BENCH("test_health_check", TEST_HEALTH_CHECK_PATH);\n\n'+init[end:]
h=(OUT/'user/src/test_health_check.c').read_text()
extra=r'''
static void d_getpid_probe(void) {
    for(unsigned i=0;i<100;i++) (void)syscall0(39);
    uint64_t total=0,lo=~0ULL,hi=0;
    for(unsigned i=0;i<10000;i++) { uint64_t a=d_clock(); (void)syscall0(39); uint64_t v=d_clock()-a; total+=v; if(v<lo)lo=v;if(v>hi)hi=v; }
    printf("SPSC_DIAG early_getpid warmup=100 n=10000 min=%lu mean=%lu max=%lu units=raw_tsc\n",lo,total/10000,hi);
}
'''
e6h=h.replace('static void spsc_responsive(void)',extra+'\nstatic void spsc_responsive(void)').replace('    d_user_on=1;','    d_getpid_probe();\n    d_user_on=1;')
(HERE/'e6-overlay.patch').write_text(''.join(difflib.unified_diff(init.splitlines(True),e6.splitlines(True),fromfile='a/user/src/init.c',tofile='b/user/src/init.c'))+''.join(difflib.unified_diff(h.splitlines(True),e6h.splitlines(True),fromfile='a/user/src/test_health_check.c',tofile='b/user/src/test_health_check.c')))
(HERE/'prepared.json').write_text(json.dumps({'base':str(BASE),'output':str(OUT),'mode':'E7; apply e6-overlay.patch only for E6','changed':{n:{'before':hashlib.sha256(s.encode()).hexdigest(),'after':hashlib.sha256((OUT/n).read_bytes()).hexdigest()} for n,s in original.items()},'compiled':False,'executed':False},indent=2)+'\n')
print(OUT)
