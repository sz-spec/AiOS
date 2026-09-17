"""Exercise the actual user-copy page-table permission preflight."""
from pathlib import Path
import re, subprocess, tempfile, unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function
ROOT=Path(__file__).resolve().parents[1]
class UsercopyPermissions(unittest.TestCase):
    def test_actual_effective_permissions(self):
        source=(ROOT/'kernel/src/mm/user_copy.c').read_text()
        header=(ROOT/'kernel/include/vos/vmm.h').read_text()
        definitions='\n'.join(line for line in header.splitlines() if re.match(r'#define VOS3_PTE_(PRESENT|WRITABLE|USER|COW|ADDR_MASK)\s',line))
        actual=function(source,'static int user_pages_accessible(const void* addr, size_t size, int write)\n{')
        helpers=function(header,'static inline uintptr_t vos3_pte_get_addr(')+'\n'+function(header,'static inline int vos3_pte_is_cow(')
        code=r'''#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#include <string.h>
'''+definitions+r'''
typedef uint64_t vos3_pte_t;
typedef struct {vos3_pte_t* pml4;} vos3_address_space_t;
static vos3_pte_t tables[4][512] __attribute__((aligned(4096)));
static vos3_address_space_t as={tables[0]},*current=&as;
static vos3_address_space_t* vos3_vmm_get_current_space(void){return current;}
static void* vos3_phys_to_virt(uintptr_t p){return (void*)p;}
'''+helpers+'\n'+actual+r'''
static void reset(void){memset(tables,0,sizeof(tables));for(int i=0;i<3;i++)tables[i][0]=(uintptr_t)tables[i+1]|7;tables[3][1]=0x100007;tables[3][2]=0x200007;current=&as;as.pml4=tables[0];}
int main(void){
 reset();assert(user_pages_accessible((void*)4096,8192,0));assert(user_pages_accessible((void*)4096,8192,1));
 for(int level=0;level<4;level++){
  reset();unsigned slot=level==3?1:0;tables[level][slot]&=~VOS3_PTE_USER;
  assert(!user_pages_accessible((void*)4096,1,0));assert(!user_pages_accessible((void*)4096,1,1));
  reset();tables[level][slot]&=~VOS3_PTE_WRITABLE;
  assert(user_pages_accessible((void*)4096,1,0));assert(!user_pages_accessible((void*)4096,1,1));
 }
 reset();tables[3][1]=(tables[3][1]&~VOS3_PTE_WRITABLE)|VOS3_PTE_COW;assert(user_pages_accessible((void*)4096,1,1));
 tables[1][0]&=~VOS3_PTE_WRITABLE;assert(!user_pages_accessible((void*)4096,1,1));
 reset();tables[3][2]&=~VOS3_PTE_USER;assert(!user_pages_accessible((void*)8190,4,0));
 for(int level=0;level<4;level++){reset();tables[level][level==3?1:0]=0;assert(user_pages_accessible((void*)4096,1,0));assert(user_pages_accessible((void*)4096,1,1));}
 for(int level=1;level<=2;level++){reset();tables[level][0]=0x87;assert(user_pages_accessible((void*)4096,1,1));tables[level][0]&=~VOS3_PTE_USER;assert(!user_pages_accessible((void*)4096,1,0));tables[level][0]|=VOS3_PTE_USER|VOS3_PTE_COW;tables[level][0]&=~VOS3_PTE_WRITABLE;assert(user_pages_accessible((void*)4096,1,1));}
 reset();current=NULL;assert(!user_pages_accessible((void*)4096,1,0));current=&as;as.pml4=NULL;assert(!user_pages_accessible((void*)4096,1,0));return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-usercopy-permissions-') as directory:
            base=Path(directory);(base/'test.c').write_text(code)
            build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=undefined',str(base/'test.c'),'-o',str(base/'test')],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            run=subprocess.run([str(base/'test')],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stdout+run.stderr)
if __name__=='__main__':unittest.main()
