"""Actual bounded VMM scanner and actual four-level walker; synthetic tables only."""
from pathlib import Path
import subprocess,tempfile,unittest
if __package__:
 from .test_shm_identity import function
else:
 from test_shm_identity import function
ROOT=Path(__file__).resolve().parents[1]
class SparseScan(unittest.TestCase):
 def test_actual_scanner(self):
  source=(ROOT/'kernel/src/mm/vmm.c').read_text()
  code=r'''#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#include <string.h>
#define VOS3_USER_SPACE_END 0x7fffffffffffULL
#define VOS3_DEBUG(...) ((void)0)
#define VOS3_PML4_INDEX(v) (((v)>>39)&511)
#define VOS3_PDPT_INDEX(v) (((v)>>30)&511)
#define VOS3_PD_INDEX(v) (((v)>>21)&511)
#define VOS3_PT_INDEX(v) (((v)>>12)&511)
typedef uint64_t vos3_pte_t;
typedef struct {vos3_pte_t *pml4;} vos3_address_space_t;
static unsigned visits;
static int vos3_pte_is_present(uint64_t p){return p&1;}
static int vos3_pte_is_large(uint64_t p){return p&128;}
static vos3_pte_t *get_or_create_table(vos3_pte_t *t,size_t i,int create,int user){assert(!create&&user);visits++;return (t[i]&1)?(vos3_pte_t*)(uintptr_t)(t[i]&~4095ULL):NULL;}
static _Alignas(4096) uint64_t root[512],pdpt[512],pd[512],pt[512];
'''+function(source,'static vos3_pte_t* walk_page_tables(')+'\n'+function(source,'int vos3_vmm_check_unmapped_locked(')+r'''
int main(void){vos3_address_space_t as={root};
 assert(vos3_vmm_check_unmapped_locked(&as,0,1ULL<<40)==0&&visits==2);
 root[0]=(uintptr_t)pdpt|1;pdpt[0]=129;
 assert(vos3_vmm_check_unmapped_locked(&as,4096,4096)==-17);
 pdpt[0]=(uintptr_t)pd|1;pd[0]=129;
 assert(vos3_vmm_check_unmapped_locked(&as,4096,4096)==-17);
 pd[0]=(uintptr_t)pt|1;pt[1]=1;
 assert(vos3_vmm_check_unmapped_locked(&as,4096,4096)==-17);
 memset(pt,0,sizeof(pt));for(int i=0;i<9;i++)pd[i]=(uintptr_t)pt|1;
 visits=0;assert(vos3_vmm_check_unmapped_locked(&as,0,4097ULL*4096)==-11&&visits==4096*3);
 visits=0;assert(vos3_vmm_check_unmapped_locked(&as,0,4096ULL*4096)==0&&visits==4096*3);
 assert(vos3_vmm_check_unmapped_locked(&as,0,UINT64_MAX)==-22);
 assert(vos3_vmm_check_unmapped_locked(&as,0x800000000000ULL,4096)==-22);
 assert(vos3_vmm_check_unmapped_locked(&as,1,4096)==-22);
 return 0;}
'''
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'test.c').write_text(code)
   b=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
   self.assertEqual(b.returncode,0,b.stderr)
   r=subprocess.run([str(p/'test')],capture_output=True,text=True,timeout=10)
   self.assertEqual(r.returncode,0,r.stderr)
if __name__=='__main__':unittest.main()
