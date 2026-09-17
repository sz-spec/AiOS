#!/usr/bin/env python3
"""Exercise extracted production creator-exit/put/drain code with host hooks."""
from pathlib import Path
import subprocess,tempfile,unittest,hashlib,platform
if __package__:
 from .test_shm_identity import function,ROOT
else:
 from test_shm_identity import function,ROOT
class CreatorExit(unittest.TestCase):
 def test_actual_creator_transfer(self):
  source=(ROOT/'kernel/src/ipc/shm.c').read_text()
  extracted='\n'.join(function(source,s) for s in ['static uint32_t shm_slot(', 'static vos3_shm_region_t* shm_get(', 'static int shm_release_owned(', 'void vos3_shm_owner_exit(', 'void vos3_shm_reap_creators('])
  code=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#define VOS3_SHM_MAX_REGIONS 64
#define SHM_SLOT_MASK 63U
#define VOS3_SHM_MAGIC 123U
#define VOS3_SHM_FLAG_DEVICE 1U
#define VOS3_SHM_FLAG_HUGETLB 2U
#define VOS3_LARGE_PAGE_SIZE 2097152U
#define VOS3_PAGE_SIZE 4096U
#define VOS3_IPC_ERR_NOTFOUND -2
#define VOS3_IPC_ERR_ACCESS -13
#define VOS3_IPC_ERR_INVALID -22
#define VOS3_IPC_OK 0
#define VOS3_DEBUG(...) ((void)0)
typedef uint32_t vos3_ipc_id_t;
typedef uint64_t vos3_irqflags_t;
typedef struct {uint64_t identity_cookie;} vos3_task_t;
typedef struct {uint32_t magic,id,ref_count,flags;uint64_t owner_identity;char name[8];uintptr_t kernel_addr,phys_addr;size_t size;int lock;} vos3_shm_region_t;
static vos3_shm_region_t *g_shm_table[64];
static uint8_t g_shm_creator_released[64];
static uint32_t g_shm_pending_creators[64];
static vos3_task_t caller={11};
static unsigned locked,freed,backings;
static unsigned deferred_requests;
static void check(int ok,const char *why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
static uint64_t shm_registry_lock(void){check(!locked,"nested registry lock");locked=1;return 0;}
static void shm_registry_unlock(uint64_t flags){(void)flags;check(locked,"unlock ownership");locked=0;}
static vos3_task_t *vos3_sched_current(void){return &caller;}
static void vos3_sched_request_deferred(void){deferred_requests++;}
static void vos3_vmm_unmap_pages(uintptr_t a,size_t n){(void)a;(void)n;check(!locked,"VMM outside lock");}
static void vos3_pmm_free_huge(uintptr_t p){(void)p;check(!locked,"huge outside lock");backings++;}
static void vos3_pmm_free_pages(uintptr_t p,size_t n){(void)p;check(!locked,"PMM outside lock");backings+=(unsigned)n;}
static void vos3_mutex_destroy(int *lock){(void)lock;check(!locked,"mutex outside lock");}
static void vos3_kfree(void *p){check(!locked,"free outside lock");freed++;free(p);}
''' + extracted + r'''
static uint32_t region(unsigned slot,uint64_t owner,unsigned refs){
 vos3_shm_region_t *p=calloc(1,sizeof(*p));p->id=64+slot;p->magic=VOS3_SHM_MAGIC;p->ref_count=refs;p->owner_identity=owner;p->size=4096;g_shm_table[slot]=p;return p->id;
}
int main(void){
 uint32_t a=region(1,11,1),b=region(2,11,2),other=region(3,22,1);
 vos3_shm_owner_exit(0);check(!g_shm_pending_creators[1],"zero owner ignored");
 vos3_shm_owner_exit(11);vos3_shm_owner_exit(11);
 check(deferred_requests==1,"creator release publishes deferred work once");
 check(shm_get(a)->ref_count==1&&shm_get(b)->ref_count==2&&!freed,"transfer preserves existing pins");
 check(g_shm_pending_creators[1]==a&&g_shm_pending_creators[2]==b&&!g_shm_pending_creators[3],"full handles and unrelated owner");
 check(shm_release_owned(a,1)==-22&&shm_get(a)->ref_count==1,"explicit after exit cannot steal pending pin");
 vos3_shm_reap_creators();check(!shm_get(a)&&shm_get(b)->ref_count==1&&shm_get(other)->ref_count==1&&freed==1,"drain drops one pin each");
 vos3_shm_reap_creators();check(freed==1&&shm_get(b)->ref_count==1,"repeated drain idempotent");
 check(shm_release_owned(b,0)==0&&!shm_get(b)&&freed==2,"last mapping finalizes");
 uint32_t explicit=region(4,11,2);check(shm_release_owned(explicit,1)==0,"explicit creator release");
 vos3_shm_owner_exit(11);check(!g_shm_pending_creators[4]&&shm_get(explicit)->ref_count==1,"explicit before exit not queued");
 check(shm_release_owned(explicit,0)==0,"explicit last mapping");
 caller.identity_cookie=22;check(shm_release_owned(other,1)==0,"unrelated creator retained authority");
 for(unsigned slot=1;slot<64;slot++)region(slot,33,1);
 vos3_shm_owner_exit(33);unsigned previous=freed;vos3_shm_reap_creators();check(freed==previous+63,"bounded full queue drains");
 for(unsigned slot=1;slot<64;slot++)check(!g_shm_table[slot]&&!g_shm_pending_creators[slot],"queue empty");
 check(backings==freed,"one backing release per finalization");puts("PASS actual creator transfer/drain/release");return 0;
}
'''
  with tempfile.TemporaryDirectory(prefix='vos-shm-exit-') as directory:
   p=Path(directory);(p/'test.c').write_text(code)
   r=subprocess.run(['cc']+(['-arch','x86_64'] if platform.system()=='Darwin' else [])+['-std=c11','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=10);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
   print('production-helper-sha256',hashlib.sha256(extracted.encode()).hexdigest())
if __name__=='__main__':unittest.main()
