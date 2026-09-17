"""Run the actual mmap syscall with simultaneous shared-AS callers."""
from pathlib import Path
import re,subprocess,tempfile,unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function
ROOT=Path(__file__).resolve().parents[1]
class SharedMmapCursor(unittest.TestCase):
 def test_concurrent_actual_syscall(self):
  source=(ROOT/'kernel/src/exec/exec_syscall.c').read_text()
  constants='\n'.join(line for line in source.splitlines() if re.match(r'#define (MAP_SHARED|MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED|VOS3_MMAP_BASE)\s',line))
  actual=function(source,'static int user_mapping_range(')+'\n'+function(source,'static int64_t sys_mmap(')
  code=r'''#include <stdint.h>
#include <stddef.h>
#include <pthread.h>
#include <stdatomic.h>
#include <assert.h>
#include <string.h>
#include <sched.h>
#define VOS3_PAGE_SIZE 4096ULL
#define VOS3_USER_END 0x0000800000000000ULL
#define VOS3_MAX_VMAS 64
#define VOS3_O_ACCMODE 3
#define VOS3_O_WRONLY 1
#define VOS3_DEBUG(...) ((void)0)
typedef uint64_t vos3_irqflags_t;
typedef struct {void *read,*lseek;} ops_t;
typedef struct {int flags;ops_t *ops;int refs;} vos3_file_t;
typedef struct {uint64_t vm_start,vm_end;int vm_prot,vm_flags;vos3_file_t *vm_file;uint64_t vm_offset;int valid;} vos3_vma_t;
typedef struct {pthread_mutex_t lock;uint64_t mmap_next,brk_start,brk;vos3_vma_t vmas[64];unsigned num_vmas;} vos3_address_space_t;
typedef struct {vos3_address_space_t *address_space;void *fd_table,*user_stack;uint64_t user_stack_size,mmap_next;} vos3_task_t;
static _Thread_local vos3_task_t *current;
static _Thread_local int irq_off,lock_held;
static atomic_int ready,start;
static vos3_address_space_t shared={.lock=PTHREAD_MUTEX_INITIALIZER};
static ops_t operations={(void*)1,(void*)1};static vos3_file_t file={0,&operations,1};
static vos3_task_t *vos3_sched_current(void){return current;}
static uint64_t vos3_irq_save(void){int old=irq_off;irq_off=1;return old;}
static void vos3_irq_restore(uint64_t old){assert(!lock_held);irq_off=(int)old;}
static void vos3_spinlock_acquire(pthread_mutex_t *lock){assert(irq_off&&!lock_held);assert(!pthread_mutex_lock(lock));lock_held=1;}
static void vos3_spinlock_release(pthread_mutex_t *lock){assert(lock_held&&irq_off);lock_held=0;assert(!pthread_mutex_unlock(lock));}
static vos3_file_t *vos3_fd_get(void *table,int fd){assert(!lock_held);if(!table||fd!=3)return NULL;file.refs++;return &file;}
static void vos3_fd_put(vos3_file_t *f){assert(!lock_held);assert(f==&file&&file.refs>1);file.refs--;}
static int scan_error;
static int vos3_vmm_check_unmapped_locked(vos3_address_space_t *as,uintptr_t p,size_t n){(void)as;(void)p;(void)n;assert(lock_held&&irq_off);return scan_error;}
'''+constants+'\n'+actual+r'''
static int64_t results[8][8];
static void *worker(void *arg){uintptr_t id=(uintptr_t)arg;vos3_task_t task={.address_space=&shared,.mmap_next=VOS3_MMAP_BASE};current=&task;atomic_fetch_add(&ready,1);while(!atomic_load(&start))sched_yield();for(unsigned i=0;i<8;i++){results[id][i]=sys_mmap(0,(i%4+1)*4096,3,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(results[id][i]>=0);assert(!irq_off&&!lock_held);sched_yield();}return NULL;}
int main(void){
 pthread_t threads[8];for(uintptr_t i=0;i<8;i++)assert(!pthread_create(&threads[i],NULL,worker,(void*)i));while(atomic_load(&ready)!=8)sched_yield();atomic_store(&start,1);for(int i=0;i<8;i++)assert(!pthread_join(threads[i],NULL));
 assert(shared.num_vmas==64&&shared.mmap_next==VOS3_MMAP_BASE+8*20*4096);
 for(int i=0;i<64;i++){assert(shared.vmas[i].valid);for(int j=i+1;j<64;j++)assert(shared.vmas[i].vm_end<=shared.vmas[j].vm_start||shared.vmas[j].vm_end<=shared.vmas[i].vm_start);}
 vos3_task_t task={.address_space=&shared,.fd_table=(void*)1};current=&task;uint64_t cursor=shared.mmap_next;
 assert(sys_mmap(0,4096,3,MAP_PRIVATE,3,0)==-12&&file.refs==1&&shared.mmap_next==cursor&&!lock_held&&!irq_off);
 vos3_address_space_t separate={.lock=PTHREAD_MUTEX_INITIALIZER,.mmap_next=cursor};task.address_space=&separate;
 assert(sys_mmap(0,4096,3,MAP_PRIVATE|MAP_ANONYMOUS,-1,0)==(int64_t)cursor);assert(shared.mmap_next==cursor&&separate.mmap_next==cursor+4096);
 assert(sys_mmap(cursor,4096,3,MAP_PRIVATE|MAP_FIXED,3,0)==-17&&file.refs==1&&separate.mmap_next==cursor+4096&&!irq_off);
 assert(sys_mmap(cursor+8192,4096,3,MAP_PRIVATE|MAP_FIXED,3,0)==(int64_t)(cursor+8192)&&file.refs==2&&separate.mmap_next==cursor+4096);vos3_fd_put(&file);
 scan_error=-11;unsigned before=separate.num_vmas;assert(sys_mmap(cursor+16384,4096,1,MAP_PRIVATE|MAP_FIXED,3,0)==-11&&file.refs==1&&separate.num_vmas==before&&separate.mmap_next==cursor+4096&&!irq_off&&!lock_held);scan_error=0;
 file.flags=VOS3_O_WRONLY;assert(sys_mmap(0,4096,1,MAP_PRIVATE,3,0)==-13&&file.refs==1);file.flags=0;
 assert(sys_mmap(0,4096,1,MAP_PRIVATE,4,0)==-9&&file.refs==1);
 separate.mmap_next=VOS3_USER_END;assert(sys_mmap(0,4096,1,MAP_PRIVATE,3,0)==-22&&file.refs==1&&!irq_off);
 return 0;
}
'''
  with tempfile.TemporaryDirectory(prefix='vos-shared-mmap-') as d:
   p=Path(d);(p/'test.c').write_text(code)
   build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-pthread','-fsanitize=undefined','-fno-sanitize-recover=undefined',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
   self.assertEqual(build.returncode,0,build.stderr)
   run=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=15)
   self.assertEqual(run.returncode,0,run.stdout+run.stderr)
 def test_cursor_lifecycle_binding(self):
  source=(ROOT/'kernel/src/mm/vmm.c').read_text()
  create=function(source,'vos3_address_space_t* vos3_vmm_create_address_space(')
  clone=function(source,'vos3_address_space_t* vos3_vmm_clone_cow(')
  self.assertIn('as->mmap_next = 0x0000000030000000ULL;',create)
  self.assertLess(clone.index('vos3_spinlock_acquire(&src->lock)'),clone.index('dst->mmap_next = src->mmap_next;'))
  self.assertLess(clone.index('dst->mmap_next = src->mmap_next;'),clone.index('vos3_spinlock_release(&src->lock)'))
  mmap=function((ROOT/'kernel/src/exec/exec_syscall.c').read_text(),'static int64_t sys_mmap(')
  self.assertNotIn('task->mmap_next',mmap)
if __name__=='__main__':unittest.main()
