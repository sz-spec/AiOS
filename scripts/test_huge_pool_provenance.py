"""Actual PMM huge ownership/contiguous reservation and bitmap rollback helpers.

Host allocator/locks/bitmap stubs are deterministic; no hardware/SMP proof.
"""
from pathlib import Path
import re, subprocess, tempfile, unittest
if __package__:
 from .test_shm_identity import function
else:
 from test_shm_identity import function
ROOT=Path(__file__).resolve().parents[1]
class HugePoolTests(unittest.TestCase):
 def test_actual_pool_fallback_and_bitmap_ownership(self):
  source=(ROOT/'kernel/src/mm/pmm.c').read_text()
  structure=re.search(r'typedef struct huge_fallback\s*\{.*?\}\s*huge_fallback_t;',source,re.S)[0]
  code=r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#include <string.h>
#include <stdlib.h>
#define VOS3_PAGE_SIZE 4096U
#define VOS3_LARGE_PAGE_SIZE (512ULL*4096)
#define VOS3_HUGEPAGE_POOL_MAX 8
#define VOS3_WARN(...) ((void)0)
#define VOS3_INFO(...) ((void)0)
typedef uint64_t vos3_irqflags_t;
typedef int vos3_pmm_zone_t;
static uint64_t g_hugepage_pool[8],g_hugepage_release_tick[8];
static uint32_t g_hugepage_pool_count,g_hugepage_pool_used,g_buddy_initialized;
static int g_hugepage_lock,irq_off,metadata_live,metadata_fail,buddy_frees,buddy_calls,bitmap_frees;
static uint64_t buddy_result=0x4000000,buddy_freed_phys;
static size_t buddy_alloc_count,buddy_free_count;
static unsigned char bits[8192];
static uint32_t refs[8192];
static int steal=-1;
static struct {int initialized,lock;size_t total_pages,refcount_size;uint32_t*refcount;struct {uint64_t free_memory;}stats;}g_pmm;
static vos3_irqflags_t vos3_irq_save(void){int old=irq_off;irq_off=1;return old;}
static void vos3_irq_restore(vos3_irqflags_t f){irq_off=f;}
static void vos3_spinlock_acquire(int*p){assert(!*p);*p=1;}
static void vos3_spinlock_release(int*p){assert(*p);*p=0;}
static uint64_t vos3_timer_get_ticks(void){return 100;}
static void* vos3_kmalloc(size_t n){assert(!g_hugepage_lock);if(metadata_fail)return NULL;metadata_live++;return malloc(n);}
static void vos3_kfree(void*p){assert(!g_hugepage_lock&&p);metadata_live--;free(p);}
static uintptr_t vos3_pmm_buddy_alloc(size_t n,int f){(void)f;assert(!g_hugepage_lock);buddy_calls++;buddy_alloc_count=n;return buddy_result;}
static void vos3_pmm_buddy_free(uintptr_t p,size_t n){assert(!g_hugepage_lock);buddy_frees++;buddy_freed_phys=p;buddy_free_count=n;}
static void vos3_pmm_free_pages(uint64_t p,size_t n){assert(!g_hugepage_lock&&p&&n);bitmap_frees++;}
static int pmm_bitmap_test(size_t p){assert(p<8192);return bits[p];}
static int pmm_bitmap_set(size_t p){assert(p<8192);if((int)p==steal){bits[p]=1;refs[p]=9;steal=-1;g_pmm.stats.free_memory-=4096;}int old=bits[p];bits[p]=1;return old;}
static int pmm_bitmap_clear(size_t p){assert(p<8192);int old=bits[p];bits[p]=0;return old;}
static int pmm_page_to_zone(size_t p){(void)p;return 0;}
static void pmm_stats_alloc(int z){(void)z;g_pmm.stats.free_memory-=4096;}
static void pmm_stats_free(int z){(void)z;g_pmm.stats.free_memory+=4096;}
static uint64_t vos3_atomic_load64(uint64_t*p){return *p;}
static void vos3_atomic_store32(uint32_t*p,uint32_t v){*p=v;}
static uint64_t vos3_pmm_page_to_addr(size_t p){return p*4096;}
'''+structure+'\nstatic huge_fallback_t* g_huge_fallbacks;\n'
  for signature in ['void vos3_pmm_reserve_hugepages(', 'static uint64_t pmm_huge_fallback_alloc(', 'uint64_t vos3_pmm_alloc_huge(', 'static void pmm_huge_swap(', 'static void pmm_huge_sift(', 'uint64_t vos3_pmm_alloc_huge_contiguous(', 'void vos3_pmm_free_huge(', 'static int buddy_range_alloc(', 'static void buddy_range_free(']:
   code+='\n'+function(source,signature)
  code+=r'''
static void reset(void){assert(!metadata_live&&!g_huge_fallbacks);memset(g_hugepage_pool,0,sizeof(g_hugepage_pool));memset(g_hugepage_release_tick,0,sizeof(g_hugepage_release_tick));g_hugepage_pool_count=g_hugepage_pool_used=0;g_buddy_initialized=0;metadata_fail=buddy_frees=buddy_calls=0;buddy_result=0x4000000;}
int main(void){
 reset();g_hugepage_pool_count=3;g_hugepage_pool_used=2;
 g_hugepage_pool[0]=0x200000;g_hugepage_pool[1]=0x400000;g_hugepage_pool[2]=0x600000;
 uint64_t before[8];memcpy(before,g_hugepage_pool,sizeof(before));
 vos3_pmm_free_huge(0x800000);assert(g_hugepage_pool_used==2&&!memcmp(before,g_hugepage_pool,sizeof(before))&&!buddy_frees);
 vos3_pmm_free_huge(0x200000);assert(g_hugepage_pool_used==1&&g_hugepage_pool[0]==0x400000);
 memcpy(before,g_hugepage_pool,sizeof(before));vos3_pmm_free_huge(0x200000);
 assert(g_hugepage_pool_used==1&&!memcmp(before,g_hugepage_pool,sizeof(before))&&!buddy_frees);
 vos3_pmm_free_huge(0x400000);vos3_pmm_free_huge(0x400000);assert(!g_hugepage_pool_used&&!buddy_frees);
 /* A scrambled prefix must not hide a contiguous free run. */
 reset();g_hugepage_pool_count=6;g_hugepage_pool_used=1;
 uint64_t entries[]={20,8,2,7,3,6};for(int i=0;i<6;i++)g_hugepage_pool[i]=entries[i]*VOS3_LARGE_PAGE_SIZE;
 assert(vos3_pmm_alloc_huge_contiguous(3)==6*VOS3_LARGE_PAGE_SIZE);
 assert(g_hugepage_pool_used==4&&g_hugepage_pool[0]==20*VOS3_LARGE_PAGE_SIZE);
 for(int i=0;i<3;i++)vos3_pmm_free_huge((6+i)*VOS3_LARGE_PAGE_SIZE);
 assert(g_hugepage_pool_used==1);assert(!vos3_pmm_alloc_huge_contiguous(0)&&!vos3_pmm_alloc_huge_contiguous(17));
 assert(!vos3_pmm_alloc_huge_contiguous(6)&&g_hugepage_pool_used==1);
 /* One hundred deterministic permutations exercise free-partition heapsort and selection. */
 for(unsigned seed=1;seed<=100;seed++){
  reset();g_hugepage_pool_count=8;for(int i=0;i<8;i++)g_hugepage_pool[i]=(i+1)*VOS3_LARGE_PAGE_SIZE;
  unsigned r=seed;for(int i=7;i>0;i--){r=r*1664525U+1013904223U;unsigned j=r%(i+1);pmm_huge_swap(i,j);}
  assert(vos3_pmm_alloc_huge_contiguous(8)==VOS3_LARGE_PAGE_SIZE&&g_hugepage_pool_used==8);
 }
 reset();g_buddy_initialized=1;
 assert(vos3_pmm_alloc_huge_contiguous(3)==buddy_result&&buddy_alloc_count==2048&&metadata_live==1);
 vos3_pmm_free_huge(buddy_result+VOS3_LARGE_PAGE_SIZE);assert(!buddy_frees&&metadata_live==1);
 vos3_pmm_free_huge(buddy_result+VOS3_LARGE_PAGE_SIZE);assert(!buddy_frees);
 vos3_pmm_free_huge(buddy_result+3*VOS3_LARGE_PAGE_SIZE);assert(!buddy_frees);
 vos3_pmm_free_huge(buddy_result+2*VOS3_LARGE_PAGE_SIZE);assert(!buddy_frees);
 vos3_pmm_free_huge(buddy_result);assert(buddy_frees==1&&buddy_freed_phys==buddy_result&&buddy_free_count==2048&&!metadata_live);
 vos3_pmm_free_huge(buddy_result);assert(buddy_frees==1);
 reset();g_buddy_initialized=1;metadata_fail=1;assert(!vos3_pmm_alloc_huge()&&!buddy_calls&&!metadata_live);
 metadata_fail=0;buddy_result=0;assert(!vos3_pmm_alloc_huge()&&!metadata_live);
 buddy_result=0x4001000;assert(!vos3_pmm_alloc_huge()&&bitmap_frees==1&&!buddy_frees&&!metadata_live);
 reset();g_pmm.initialized=1;g_pmm.total_pages=g_pmm.refcount_size=8192;g_pmm.refcount=refs;g_pmm.stats.free_memory=8192ULL*4096;
 steal=514;vos3_pmm_reserve_hugepages(3);
 assert(g_hugepage_pool_count==3&&!bits[512]&&!bits[513]&&refs[512]==0&&refs[513]==0&&refs[514]==9);
 for(unsigned h=0;h<3;h++){assert(g_hugepage_pool[h]%VOS3_LARGE_PAGE_SIZE==0);for(unsigned j=0;j<512;j++){size_t p=g_hugepage_pool[h]/4096+j;assert(bits[p]&&refs[p]==1);}}
 /* Real buddy claim rolls back a competitor and initializes only full success. */
 steal=34;assert(buddy_range_alloc(32,8)==-1);assert(!bits[32]&&!bits[33]&&refs[34]==9);
 assert(buddy_range_alloc(64,8)==0);for(unsigned i=64;i<72;i++)assert(bits[i]&&refs[i]==1);
 buddy_range_free(64,8);for(unsigned i=64;i<72;i++)assert(!bits[i]&&!refs[i]);
 assert(!g_hugepage_lock&&!irq_off&&!metadata_live);return 0;
}
'''
  with tempfile.TemporaryDirectory(prefix='vos-huge-pool-') as tmp:
   p=Path(tmp);(p/'test.c').write_text(code)
   r=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=undefined',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
   self.assertEqual(r.returncode,0,r.stderr)
   r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=10)
   self.assertEqual(r.returncode,0,r.stderr)
if __name__=='__main__':unittest.main()
