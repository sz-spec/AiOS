"""Actual contiguous allocation/free C functions with deterministic race hooks."""
from pathlib import Path
import hashlib
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class PMMContiguousTests(unittest.TestCase):
    def test_contiguous_refs_and_competing_claim_rollback(self):
        source = (ROOT / 'kernel/src/mm/pmm.c').read_text()
        extracted = '\n'.join(function(source, sig) for sig in (
            'uintptr_t vos3_pmm_alloc_pages(', 'uint32_t vos3_pmm_ref_dec(',
            'void vos3_pmm_free(', 'void vos3_pmm_free_pages('))
        harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define VOS3_PAGE_SIZE 4096U
#define VOS3_PMM_FLAG_ZERO 1U
#define VOS3_ERROR(...) ((void)0)
#define VOS3_WARN(...) ((void)0)
typedef unsigned vos3_pmm_flags_t;
typedef unsigned vos3_pmm_zone_t;
static struct {unsigned initialized; size_t total_pages,refcount_size; uint32_t refcount[128]; int lock;} g_pmm;
static unsigned char bits[128],memory[128*4096];
static size_t used,collision;
static void check(int ok,const char *why){if(!ok){fprintf(stderr,"FAIL: %s\n",why);exit(1);}}
static void vos3_spinlock_acquire(int *p){check(!*p,"lock acquired once");*p=1;}
static void vos3_spinlock_release(int *p){check(*p,"lock held before release");*p=0;}
static int pmm_bitmap_test(size_t p){return bits[p];}
static int pmm_bitmap_set(size_t p){
 if(p==collision){collision=0;bits[p]=1;g_pmm.refcount[p]=7;used++;}
 int old=bits[p];bits[p]=1;return old;
}
static int pmm_bitmap_clear(size_t p){int old=bits[p];bits[p]=0;return old;}
static unsigned pmm_page_to_zone(size_t p){(void)p;return 0;}
static void pmm_stats_alloc(unsigned z){(void)z;used++;}
static void pmm_stats_free(unsigned z){(void)z;check(used>0,"statistics underflow");used--;}
static uintptr_t vos3_pmm_page_to_addr(size_t p){return p*4096;}
static size_t vos3_pmm_addr_to_page(uintptr_t p){return p/4096;}
static void *vos3_phys_to_virt(uintptr_t p){check(p<sizeof(memory),"physical range");return memory+p;}
static void vos3_atomic_store32(uint32_t *p,uint32_t v){*p=v;}
static uint32_t vos3_atomic_load32(uint32_t *p){return *p;}
static uint32_t vos3_atomic_cas32(uint32_t *p,uint32_t old,uint32_t value){uint32_t was=*p;if(was==old)*p=value;return was;}
static void vos3_cpu_relax(unsigned n){(void)n;}
static uintptr_t vos3_pmm_alloc(unsigned flags){(void)flags;check(0,"unexpected single-page stub");return 0;}
''' + extracted + r'''
static void reset(size_t pages){memset(&g_pmm,0,sizeof(g_pmm));memset(bits,0,sizeof(bits));memset(memory,0xa5,sizeof(memory));g_pmm.initialized=1;g_pmm.total_pages=pages;g_pmm.refcount_size=128;bits[0]=1;used=0;collision=0;}
int main(void){
 reset(128);
 for(size_t n=2;n<=64;n*=2){
  uintptr_t p=vos3_pmm_alloc_pages(n,VOS3_PMM_FLAG_ZERO);
  check(p==4096,"contiguous allocation returns first free range");
  for(size_t j=1;j<=n;j++)check(bits[j]&&g_pmm.refcount[j]==1,"each allocated page owns exactly one reference");
  for(size_t j=p;j<p+n*4096;j++)check(memory[j]==0,"zero flag covers entire allocation");
  check(used==n&&!g_pmm.lock,"allocation statistics and lock");
  vos3_pmm_free_pages(p,n);
  check(used==0,"free restores all allocation statistics");
  for(size_t j=1;j<=n;j++)check(!bits[j]&&!g_pmm.refcount[j],"free restores bitmap and references");
 }
 reset(8);collision=3;
 uintptr_t p=vos3_pmm_alloc_pages(3,0);
 check(p==4*4096,"retry skips competitor and allocates later complete run");
 check(!bits[1]&&!bits[2]&&!g_pmm.refcount[1]&&!g_pmm.refcount[2],"partial own claim rolled back");
 check(bits[3]&&g_pmm.refcount[3]==7,"competitor ownership reference untouched");
 check(used==4,"only competitor and completed allocation accounted");
 vos3_pmm_free_pages(p,3);
 check(used==1&&bits[3]&&g_pmm.refcount[3]==7,"free own run preserves competitor");
 reset(4);collision=3;
 check(vos3_pmm_alloc_pages(3,0)==0,"no remaining complete run returns failure");
 check(used==1&&!bits[1]&&!bits[2]&&bits[3]&&g_pmm.refcount[3]==7,"exhausted retry preserves competitor and rolls back all own pages");
 check(!g_pmm.lock,"failure releases lock");
 puts("PASS actual contiguous PMM references/free and competing-claim rollback");
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-pmm-contiguous-') as directory:
            base=Path(directory); (base/'test.c').write_text(harness)
            built=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Wno-unused-function','-fsanitize=undefined','-fno-sanitize-recover=undefined',str(base/'test.c'),'-o',str(base/'test')],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stderr)
            result=subprocess.run([str(base/'test')],capture_output=True,text=True,timeout=10)
            print('production-functions-sha256',hashlib.sha256(extracted.encode()).hexdigest())
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':
    unittest.main()
