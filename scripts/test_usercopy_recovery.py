"""Actual C usercopy predicates/wrappers with synthetic page tables and SMAP hooks.

No real page fault, CR3, IRET or hardware SMAP execution is claimed.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile
import unittest

if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class UsercopyRecoveryTests(unittest.TestCase):
    def test_actual_fixup_range_permissions_and_cleanup(self):
        source = (ROOT / 'kernel/src/mm/user_copy.c').read_text()
        code = r'''
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
#define EFAULT 14
#define VOS3_PTE_PRESENT 1ULL
#define VOS3_PTE_WRITABLE 2ULL
#define VOS3_PTE_USER 4ULL
#define COW VOS3_PTE_COW
typedef uint64_t vos3_pte_t;
typedef struct {vos3_pte_t* pml4;} vos3_address_space_t;
static vos3_pte_t tables[4][512];
static vos3_address_space_t space={tables[0]};
static int no_space, bounds_error, ac, raw_calls, raw_error, string_mode;
static uint64_t bound_start=0x1000, bound_end=0x800000000000ULL;
static const char vos3_usercopy_from_fault[16]={1};
static const char vos3_usercopy_from_fixup[16]={2};
static const char vos3_usercopy_to_fault[16]={3};
static const char vos3_usercopy_to_fixup[16]={4};
static int get_process_bounds(uint64_t*a,uint64_t*b) {*a=bound_start;*b=bound_end;return bounds_error;}
static vos3_address_space_t* vos3_vmm_get_current_space(void) {return no_space?NULL:&space;}
static void* vos3_phys_to_virt(uintptr_t p) {assert(p>=4096&&p<=12288&&!(p&4095));return tables[p/4096];}
static void stac(void) {assert(!ac);ac=1;}
static void clac(void) {ac=0;}
static int vos3_usercopy_from_raw(void*d,const void*s,size_t n) {(void)d;(void)s;(void)n;assert(ac);raw_calls++;if(string_mode&&!raw_error){assert(n==1);*(char*)d='a';}return raw_error;}
static int vos3_usercopy_to_raw(void*d,const void*s,size_t n) {return vos3_usercopy_from_raw(d,s,n);}
'''
        header = (ROOT / 'kernel/include/vos/vmm.h').read_text()
        code += '\n' + '\n'.join(line for line in header.splitlines() if re.match(r'#define VOS3_PTE_(COW|ADDR_MASK)\s', line))
        code += '\n' + function(header, 'static inline int vos3_pte_is_cow(')
        code += '\n' + function(header, 'static inline uintptr_t vos3_pte_get_addr(')
        for signature in ['int access_ok(', 'uintptr_t vos3_usercopy_fault_fixup(',
                          'static int user_pages_accessible(', 'int copy_from_user(', 'int copy_to_user(', 'int64_t strncpy_from_user(']:
            # The forward declaration precedes the actual definition.
            search = source
            if signature.startswith('static int user_pages'):
                search = source[source.index('static int user_pages_accessible(const void* addr, size_t size, int write)\n{'):]
            code += '\n' + function(search, signature)
        wrapper_source = (ROOT / 'kernel/src/arch/x86_64/user.c').read_text()
        code += '\n#define VOS3_USER_ERR_INVALID (-2)\n'
        for signature in ['int vos3_copy_from_user(', 'int vos3_copy_to_user(', 'int64_t vos3_strncpy_from_user(']:
            code += '\n' + function(wrapper_source, signature)
        code += r'''
static void mapping(void) {
 memset(tables,0,sizeof(tables));no_space=0;space.pml4=tables[0];
 for(unsigned level=0;level<3;level++) tables[level][0]=(level+1)*4096|7;
 tables[3][4]=0x9000|7;
}
static uintptr_t fix(int to,uint64_t err,uint64_t addr,uint64_t count) {
 return vos3_usercopy_fault_fixup((uintptr_t)(to?vos3_usercopy_to_fault:vos3_usercopy_from_fault),8,err,addr,to?0xffff800000001000ULL:addr,to?addr:0xffff800000001000ULL,count);
}
int main(void) {
 for(int to=0;to<2;to++) {
  uintptr_t pc=(uintptr_t)(to?vos3_usercopy_to_fault:vos3_usercopy_from_fault);
  uintptr_t result=(uintptr_t)(to?vos3_usercopy_to_fixup:vos3_usercopy_from_fixup);
  for(unsigned present=0;present<2;present++) {
   uint64_t err=present|(to?2:0);
   assert(fix(to,err,0x4000,32)==result);
   assert(fix(to,err^2,0x4000,32)==0);
   for(unsigned bit=2;bit<64;bit++) assert(fix(to,err|(1ULL<<bit),0x4000,32)==0);
   for(unsigned cpl=1;cpl<4;cpl++) assert(!vos3_usercopy_fault_fixup(pc,8|cpl,err,0x4000,0x4000,0x4000,32));
   assert(!vos3_usercopy_fault_fixup(pc-1,8,err,0x4000,0x4000,0x4000,32));
   assert(!vos3_usercopy_fault_fixup(pc+1,8,err,0x4000,0x4000,0x4000,32));
   assert(!vos3_usercopy_fault_fixup(result,8,err,0x4000,0x4000,0x4000,32));
   assert(!vos3_usercopy_fault_fixup(pc,8,err,0x4001,0x4000,0x4000,32));
   assert(!fix(to,err,0x4000,0));
   const uint64_t invalid[]={0,0xfff,0x800000000000ULL,0xffff800000000000ULL,UINT64_MAX};
   for(unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++) assert(!fix(to,err,invalid[i],1));
   assert(!fix(to,err,0x7fffffffffffULL,2));
   assert(!fix(to,err,0x4000,UINT64_MAX));
   assert(fix(to,err,0x7fffffffffffULL,1)==result);
   bounds_error=1;assert(!fix(to,err,0x4000,32));bounds_error=0;
   bound_start=0x5000;assert(!fix(to,err,0x4000,32));bound_start=0x1000;
   bound_end=0x4010;assert(!fix(to,err,0x4000,32));bound_end=0x800000000000ULL;
  }
 }
 mapping();assert(user_pages_accessible((void*)0x4000,4096,0));assert(user_pages_accessible((void*)0x4000,4096,1));
 for(unsigned level=0;level<4;level++) {
  unsigned idx=level==3?4:0;
  mapping();tables[level][idx]&=~VOS3_PTE_USER;
  assert(!user_pages_accessible((void*)0x4000,1,0));assert(!user_pages_accessible((void*)0x4000,1,1));
  mapping();tables[level][idx]&=~VOS3_PTE_WRITABLE;
  assert(user_pages_accessible((void*)0x4000,1,0));assert(!user_pages_accessible((void*)0x4000,1,1));
  mapping();tables[level][idx]=0;assert(user_pages_accessible((void*)0x4000,1,0));assert(user_pages_accessible((void*)0x4000,1,1));
 }
 mapping();tables[3][4]=(0x9000|5|COW);assert(user_pages_accessible((void*)0x4000,1,1));
 tables[2][0]&=~2ULL;assert(!user_pages_accessible((void*)0x4000,1,1));
 for(unsigned level=1;level<=2;level++) {
  mapping();tables[level][0]=0x200000|7|128;assert(user_pages_accessible((void*)0x4000,1,1));
  tables[level][0]&=~4ULL;assert(!user_pages_accessible((void*)0x4000,1,0));
  tables[level][0]|=4ULL;tables[level][0]&=~2ULL;tables[level][0]|=COW;
  assert(user_pages_accessible((void*)0x4000,1,1)); /* Existing huge-leaf COW retry. */
  tables[level-1][0]&=~2ULL;tables[level-1][0]|=COW;
  assert(!user_pages_accessible((void*)0x4000,1,1)); /* Nonleaf COW cannot grant write. */
 }
 mapping();tables[3][5]=0xA000|3;assert(!user_pages_accessible((void*)0x4fff,2,0));
 mapping();no_space=1;assert(!user_pages_accessible((void*)0x4000,1,0));no_space=0;
 space.pml4=NULL;assert(!user_pages_accessible((void*)0x4000,1,0));mapping();
 for(int error=0;error<2;error++) {
  raw_error=error?-14:0;int before=raw_calls;
  assert(copy_from_user((void*)0x9000,(void*)0x4000,8)==raw_error && !ac);
  assert(copy_to_user((void*)0x4000,(void*)0x9000,8)==raw_error && !ac);
  assert(raw_calls==before+2);
 }
 int before=raw_calls;tables[3][4]&=~4ULL;
 assert(copy_from_user((void*)0x9000,(void*)0x4000,8)==-14);
 assert(copy_to_user((void*)0x4000,(void*)0x9000,8)==-14);
 assert(raw_calls==before&&!ac);
 assert(copy_from_user((void*)0x9000,(void*)UINT64_MAX,8)==-14);
 assert(copy_to_user((void*)UINT64_MAX,(void*)0x9000,8)==-14);
 assert(raw_calls==before&&!ac);
 mapping();raw_error=-14;
 assert(vos3_copy_from_user((void*)0x9000,(void*)0x4000,8)==-14&&!ac);
 assert(vos3_copy_to_user((void*)0x4000,(void*)0x9000,8)==-14&&!ac);
 raw_error=0;
 assert(vos3_copy_from_user((void*)0x9000,(void*)0x4000,8)==0&&!ac);
 assert(vos3_copy_to_user((void*)0x4000,(void*)0x9000,8)==0&&!ac);
 before=raw_calls;
 assert(vos3_copy_from_user(NULL,(void*)0x4000,8)==-2);
 assert(vos3_copy_from_user((void*)0x9000,NULL,8)==-2);
 assert(vos3_copy_to_user(NULL,(void*)0x9000,8)==-2);
 assert(vos3_copy_to_user((void*)0x4000,NULL,8)==-2);
 assert(raw_calls==before);
 char dest[4]={0}; string_mode=1;
 assert(vos3_strncpy_from_user(NULL,(void*)0x4000,4)==-2);
 assert(vos3_strncpy_from_user(dest,NULL,4)==-2);
 assert(vos3_strncpy_from_user(dest,(void*)0x4000,0)==-2);
 assert(vos3_strncpy_from_user(dest,(void*)0x4000,4)==3&&!ac);
 assert(memcmp(dest,"aaa",4)==0);
 raw_error=-14;assert(vos3_strncpy_from_user(dest,(void*)0x4000,4)==-14&&!ac);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-usercopy-recovery-') as directory:
            base = Path(directory)
            (base/'test.c').write_text(code)
            result = subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=undefined',str(base/'test.c'),'-o',str(base/'test')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(base/'test')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        print('user_copy.c sha256='+hashlib.sha256(source.encode()).hexdigest())


if __name__ == '__main__':
    unittest.main()
