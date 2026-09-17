"""Actual VMA transaction routines with pthread contention and synthetic PTEs."""
from pathlib import Path
import subprocess,tempfile,unittest,re
if __package__:
 from .test_shm_identity import function
else:
 from test_shm_identity import function
ROOT=Path(__file__).resolve().parents[1]
class Transactions(unittest.TestCase):
 def test_actual_transactions(self):
  v=(ROOT/'kernel/src/mm/vmm.c').read_text();s=(ROOT/'kernel/src/exec/exec_syscall.c').read_text()
  constants='\n'.join(x for x in s.splitlines() if re.match(r'#define (MAP_SHARED|MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED|VOS3_MMAP_BASE)\s',x))
  code=r'''#include <stdint.h>
#include <stdlib.h>
#include <stddef.h>
#include <pthread.h>
#include <stdatomic.h>
#include <assert.h>
#include <string.h>
#include <sched.h>
#define VOS3_PAGE_SIZE 4096ULL
#define VOS3_USER_END 0x800000000000ULL
#define VOS3_USER_SPACE_END (VOS3_USER_END-1)
#define VOS3_MAX_VMAS 64
#define VOS3_O_ACCMODE 3
#define VOS3_O_WRONLY 1
#define VOS3_PTE_USER 4ULL
#define VOS3_PTE_WRITABLE 2ULL
#define VOS3_PTE_NO_EXECUTE (1ULL<<63)
#define VOS3_PTE_COW (1ULL<<53)
#define VOS3_DEBUG(...) ((void)0)
#define VOS3_ERROR(...) ((void)0)
#define VOS3_SPINLOCK_INIT (pthread_mutex_t)PTHREAD_MUTEX_INITIALIZER
#define VOS3_PMM_FLAG_ZERO 1
#define VOS3_SPINLOCK_TYPE pthread_mutex_t
typedef uint64_t vos3_pte_t,vos3_irqflags_t;
typedef struct {void *read,*lseek;} ops_t;
typedef struct {int flags;ops_t *ops;atomic_int refs;} vos3_file_t;
typedef struct {uint64_t vm_start,vm_end;int vm_prot,vm_flags;vos3_file_t *vm_file;uint64_t vm_offset;int valid;} vos3_vma_t;
typedef struct {pthread_mutex_t lock;uint64_t mmap_next,brk_start,brk;vos3_vma_t vmas[64];unsigned num_vmas,shm_count,flags,ref_count;vos3_pte_t *pml4,*user_pml4;uintptr_t pml4_phys,user_pml4_phys;} vos3_address_space_t;
typedef struct {vos3_address_space_t *address_space;void *fd_table,*user_stack;uint64_t user_stack_size;} vos3_task_t;
static _Thread_local vos3_task_t *current;
static _Thread_local int irq_off,lock_held;
static int fail_alloc,fail_retain,fail_clone,walk_mode,freed;
static unsigned walks;
static uint64_t ptes[5000],empty;
static ops_t operations={(void*)1,(void*)1};static vos3_file_t file={0,&operations,1};
static int g_vmm_initialized=1;
static struct {uint64_t pages_mapped;} g_vmm_stats;
static vos3_task_t *vos3_sched_current(void){return current;}
static uint64_t vos3_irq_save(void){int old=irq_off;irq_off=1;return old;}
static void vos3_irq_restore(uint64_t old){assert(!lock_held);irq_off=(int)old;}
static void vos3_spinlock_acquire(pthread_mutex_t *lock){assert(irq_off&&!lock_held);assert(!pthread_mutex_lock(lock));lock_held=1;}
static void vos3_spinlock_release(pthread_mutex_t *lock){assert(lock_held&&irq_off);lock_held=0;assert(!pthread_mutex_unlock(lock));}
static vos3_file_t *vos3_fd_get(void *table,int fd){assert(!lock_held);if(!table||fd!=3)return NULL;atomic_fetch_add(&file.refs,1);return &file;}
static void vos3_fd_put(vos3_file_t *f){assert(!lock_held&&!irq_off);assert(f==&file&&atomic_fetch_sub(&f->refs,1)>1);}
static int vos3_file_retain(vos3_file_t *f){assert(lock_held);if(fail_retain)return -75;atomic_fetch_add(&f->refs,1);return 0;}
static void *vos3_kmalloc(size_t n){assert(!lock_held&&!irq_off);return fail_alloc?NULL:malloc(n);}
static void vos3_kfree(void *p){assert(!lock_held&&!irq_off);free(p);}
static int vos3_pte_is_present(uint64_t p){return p&1;}
static uintptr_t vos3_pte_get_addr(uint64_t p){return p&0x000ffffffffff000ULL;}
static vos3_pte_t *walk_page_tables(vos3_pte_t *root,uintptr_t va,int create,int user,int *level){(void)root;assert(lock_held&&!create&&user);walks++;if(!walk_mode){*level=1;return &empty;}*level=walk_mode==2?3:4;return &ptes[(va/4096)%5000];}
static int address_in_shm(vos3_address_space_t *as,uintptr_t va){(void)as;(void)va;return 0;}
static void vos3_atomic_fetch_sub64(uint64_t *p,uint64_t n){*p-=n;}
static void vos3_vmm_invlpg(uintptr_t va){(void)va;assert(lock_held);}
static void vos3_pmm_free(uintptr_t p){assert(!lock_held&&!irq_off);if(p>=0x100000000ULL)free((void*)p);else freed++;}
static unsigned vos3_pmm_ref_get(uintptr_t p){(void)p;return 2;}
static uintptr_t vos3_pmm_alloc(int flags){(void)flags;assert(!lock_held);return (uintptr_t)calloc(1,sizeof(vos3_address_space_t));}
static void *vos3_phys_to_virt(uintptr_t p){return (void*)p;}
static uintptr_t vos3_virt_to_phys(const void *p){return (uintptr_t)p;}
static vos3_pte_t *alloc_page_table(void){return calloc(512,8);}
static void free_page_table(void *p){assert(!lock_held);free(p);}
static vos3_pte_t *clone_pt_level_cow(vos3_pte_t *src,int level,int kernel){(void)src;assert(lock_held&&level==4&&!kernel);return fail_clone?NULL:calloc(512,8);}
static void vos3_vmm_flush_tlb(void){assert(!lock_held);}
'''+constants+'\n'+function(v,'vos3_vma_t* vos3_vmm_find_vma(')+'\n'+function(v,'int vos3_vmm_check_unmapped_locked(')+'\n'+function(s,'static int user_mapping_range(')+'\n'+function(s,'static int64_t sys_mmap(')+'\n'+function(v,'int vos3_vmm_munmap_range(')+'\n'+function(v,'int vos3_vmm_mprotect_range(')+'\n'+function(v,'vos3_address_space_t* vos3_vmm_clone_cow(')+r'''
static vos3_address_space_t shared={.lock=PTHREAD_MUTEX_INITIALIZER,.pml4=&empty};
static void drop_clone(vos3_address_space_t *as){for(unsigned i=0;i<64;i++)if(as->vmas[i].valid&&as->vmas[i].vm_file)vos3_fd_put(as->vmas[i].vm_file);free(as->pml4);free(as->user_pml4);free(as);}
static void *worker(void *arg){(void)arg;vos3_task_t task={.address_space=&shared,.fd_table=(void*)1};current=&task;
 for(int i=0;i<100;i++){int64_t a=sys_mmap(0,12288,3,MAP_PRIVATE,3,0);assert(a>0);assert(!vos3_vmm_munmap_range(&shared,a+4096,4096));vos3_address_space_t *child=vos3_vmm_clone_cow(&shared);assert(child);for(int j=0;j<64;j++)if(child->vmas[j].valid){assert(child->vmas[j].vm_end<=child->mmap_next);for(int k=j+1;k<64;k++)if(child->vmas[k].valid)assert(child->vmas[j].vm_end<=child->vmas[k].vm_start||child->vmas[k].vm_end<=child->vmas[j].vm_start);}drop_clone(child);assert(!vos3_vmm_munmap_range(&shared,a,12288));assert(!irq_off&&!lock_held);}return NULL;}
int main(void){
 pthread_t threads[4];for(int i=0;i<4;i++)assert(!pthread_create(&threads[i],NULL,worker,NULL));for(int i=0;i<4;i++)assert(!pthread_join(threads[i],NULL));assert(shared.num_vmas==0&&file.refs==1);
 vos3_task_t task={.address_space=&shared,.fd_table=(void*)1};current=&task;
 uint64_t base=0x200000000ULL;assert(sys_mmap(base,12288,3,MAP_FIXED|MAP_PRIVATE,3,0)==(int64_t)base);
 fail_retain=1;assert(vos3_vmm_munmap_range(&shared,base+4096,4096)==-75&&shared.num_vmas==1&&file.refs==2&&!irq_off);assert(!vos3_vmm_clone_cow(&shared)&&file.refs==2&&!irq_off);fail_retain=0;
 fail_retain=1;assert(vos3_vmm_mprotect_range(base+4096,4096,1)==-75&&shared.num_vmas==1&&shared.vmas[0].vm_prot==3&&file.refs==2&&!irq_off);fail_retain=0;
 fail_clone=1;assert(!vos3_vmm_clone_cow(&shared)&&file.refs==2&&!irq_off);fail_clone=0;
 fail_alloc=1;assert(vos3_vmm_munmap_range(&shared,base,12288)==-12&&shared.num_vmas==1);fail_alloc=0;
 assert(!vos3_vmm_munmap_range(&shared,base,12288)&&file.refs==1);
 assert(sys_mmap(0,1ULL<<40,3,MAP_PRIVATE|MAP_ANONYMOUS,-1,0)>0);walks=0;assert(!vos3_vmm_mprotect_range(shared.vmas[0].vm_start,1ULL<<40,1)&&walks<=6);assert(shared.vmas[0].vm_prot==1);assert(!vos3_vmm_munmap_range(&shared,shared.vmas[0].vm_start,1ULL<<40));
 assert(sys_mmap(0x10000000,4097ULL*4096,3,MAP_FIXED|MAP_PRIVATE|MAP_ANONYMOUS,-1,0)>0);walk_mode=1;walks=0;
 assert(vos3_vmm_mprotect_range(0x10000000,4097ULL*4096,1)==-11&&shared.vmas[0].vm_prot==3&&walks==4096&&!irq_off);
 assert(vos3_vmm_munmap_range(&shared,0x10000000,4097ULL*4096)==-11&&shared.num_vmas==1&&!irq_off);
 walk_mode=2;ptes[(0x10000000/4096)%5000]=129;
 assert(vos3_vmm_munmap_range(&shared,0x10000000,4096)==-95&&shared.num_vmas==1&&!irq_off);
 assert(vos3_vmm_mprotect_range(0x10000000,4096,1)==-95&&shared.vmas[0].vm_prot==3&&!irq_off);
 ptes[(0x10000000/4096)%5000]=0;
 walk_mode=0;assert(!vos3_vmm_munmap_range(&shared,0x10000000,4097ULL*4096));
 assert(sys_mmap(0x200000,12288,3,MAP_FIXED|MAP_PRIVATE|MAP_ANONYMOUS,-1,0)>0);walk_mode=1;unsigned idx=0x200000/4096;ptes[idx]=0x1001;ptes[idx+1]=0x2001;ptes[idx+2]=0x3001;
 assert(!vos3_vmm_mprotect_range(0x200000,12288,3));assert(ptes[idx]&VOS3_PTE_COW);assert(!(ptes[idx]&VOS3_PTE_WRITABLE));assert(!vos3_vmm_munmap_range(&shared,0x201000,4096));assert(freed==1&&!ptes[idx+1]&&ptes[idx]&&ptes[idx+2]&&shared.num_vmas==2);assert(!vos3_vmm_munmap_range(&shared,0x200000,12288)&&freed==3);
 walk_mode=0;assert(sys_mmap(0x400000,4096,3,MAP_FIXED|MAP_PRIVATE|MAP_ANONYMOUS,-1,0)>0);assert(sys_mmap(0x402000,4096,3,MAP_FIXED|MAP_PRIVATE|MAP_ANONYMOUS,-1,0)>0);assert(vos3_vmm_mprotect_range(0x400000,12288,1)==-12&&shared.vmas[0].vm_prot==3);
 return 0;}
'''
  with tempfile.TemporaryDirectory(prefix='vos-vma-transactions-') as d:
   p=Path(d);(p/'test.c').write_text(code)
   b=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-pthread','-fsanitize=undefined',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
   self.assertEqual(b.returncode,0,b.stderr)
   r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=30)
   self.assertEqual(r.returncode,0,r.stdout+r.stderr)
if __name__=='__main__':unittest.main()
