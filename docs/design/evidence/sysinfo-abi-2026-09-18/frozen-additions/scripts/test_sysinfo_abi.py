"""Actual telemetry copyout and dispatcher, mocked userspace copy boundaries."""
from pathlib import Path
import subprocess
import tempfile
import unittest

if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function
ROOT = Path(__file__).resolve().parents[1]


class SysinfoABI(unittest.TestCase):
    def test_versioned_copy_and_legacy_no_write(self):
        source = (ROOT / 'kernel/src/fs/posix_syscall.c').read_text()
        actual = function(source, 'static int64_t sys_vos_sysinfo(')
        dispatch = function(source, 'static int64_t posix_syscall_handler(')
        code = r'''
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
#include "uapi/vos_sysinfo.h"
_Static_assert(sizeof(vos3_sysinfo_t)==40,"wire size");
_Static_assert(offsetof(vos3_sysinfo_t,free_pages)==0,"free offset");
_Static_assert(offsetof(vos3_sysinfo_t,total_pages)==8,"total offset");
_Static_assert(offsetof(vos3_sysinfo_t,nr_tasks)==16,"task offset");
_Static_assert(offsetof(vos3_sysinfo_t,nr_zombies)==20,"zombie offset");
_Static_assert(offsetof(vos3_sysinfo_t,uptime_ms)==24,"time offset");
_Static_assert(offsetof(vos3_sysinfo_t,hugepage_total)==32,"huge total offset");
_Static_assert(offsetof(vos3_sysinfo_t,hugepage_used)==36,"huge used offset");
_Static_assert(VOS3_SYSINFO_SYSCALL==483 && VOS3_SYSINFO_VERSION==1,"number/version");
#define SYS_GETRUSAGE 98
#define SYS_GETRLIMIT 97
#define SYS_SETRLIMIT 160
#define SYS_SETITIMER 38
#define SYS_GETITIMER 36
#define SYS_SYSINFO 99
#define VOS3_SYS_SYSINFO VOS3_SYSINFO_SYSCALL
#define SYS_VOS_SYSINFO VOS3_SYSINFO_SYSCALL
/* Other dispatcher arms are stubbed; their behavior is not under test. */
typedef int posix_rusage_t;
typedef int posix_rlimit_t;
typedef int posix_itimerval_t;
typedef struct {uint64_t rax,rdi,rsi,rdx;} vos3_syscall_frame_t;
static int sys_getrusage(int a,posix_rusage_t*b){(void)a;(void)b;return 77;}
static int sys_getrlimit(unsigned a,posix_rlimit_t*b){(void)a;(void)b;return 77;}
static int sys_setrlimit(unsigned a,const posix_rlimit_t*b){(void)a;(void)b;return 77;}
static int sys_setitimer(int a,const posix_itimerval_t*b,posix_itimerval_t*c){(void)a;(void)b;(void)c;return 77;}
static int sys_getitimer(int a,posix_itimerval_t*b){(void)a;(void)b;return 77;}
static unsigned collects,copies;
static int fail_copy;
static unsigned char *allowed;
static size_t allowed_size;
static uint64_t vos3_pmm_free_pages_count(void){collects++;return UINT64_C(0x123456789);}
static uint64_t vos3_pmm_total_pages_count(void){return UINT64_C(0x223456789);}
static void vos3_task_count_stats(uint32_t*a,uint32_t*b){*a=19;*b=3;}
static uint64_t vos3_timer_get_uptime_ms(void){return UINT64_C(0x102030405);}
static void vos3_pmm_hugepage_stats(uint32_t*a,uint32_t*b){*a=128;*b=17;}
static int copy_to_user(void *dst,const void *src,size_t n){
 copies++;
 if(fail_copy || dst!=allowed || n>allowed_size)return -14;
 memcpy(dst,src,n);return 0;
}
''' + actual + '\n' + dispatch + r'''
int main(void){
 unsigned char storage[56];memset(storage,0xA5,sizeof(storage));allowed=storage+8;allowed_size=40;
 vos3_syscall_frame_t f={.rax=VOS3_SYSINFO_SYSCALL,.rdi=(uintptr_t)allowed,.rsi=40,.rdx=1};
 assert(posix_syscall_handler(&f)==0);assert(copies==1&&collects==1);
 vos3_sysinfo_t got;memcpy(&got,allowed,40);
 assert(got.free_pages==UINT64_C(0x123456789)&&got.total_pages==UINT64_C(0x223456789));
 assert(got.nr_tasks==19&&got.nr_zombies==3&&got.uptime_ms==UINT64_C(0x102030405));
 assert(got.hugepage_total==128&&got.hugepage_used==17);
 for(unsigned i=0;i<8;i++)assert(storage[i]==0xA5&&storage[48+i]==0xA5);
 unsigned char before[56];memcpy(before,storage,56);
 const uint64_t sizes[]={0,1,31,32,39,41,112,UINT64_MAX};
 for(unsigned i=0;i<sizeof(sizes)/sizeof(*sizes);i++){f.rsi=sizes[i];assert(posix_syscall_handler(&f)==-22);}
 f.rsi=40;f.rdx=0;assert(posix_syscall_handler(&f)==-22);f.rdx=UINT64_MAX;assert(posix_syscall_handler(&f)==-22);
 assert(copies==1&&collects==1&&!memcmp(before,storage,56));
 f.rdx=1;fail_copy=1;assert(posix_syscall_handler(&f)==-14);fail_copy=0;
 f.rdi=0;assert(posix_syscall_handler(&f)==-14);
 f.rdi=(uintptr_t)allowed;allowed_size=39;assert(posix_syscall_handler(&f)==-14);
 assert(!memcmp(before,storage,56));
 unsigned previous=copies;f.rax=99;f.rsi=UINT64_MAX;f.rdx=UINT64_MAX;
 assert(posix_syscall_handler(&f)==-38&&copies==previous&&!memcmp(before,storage,56));
 f.rdi=UINT64_MAX;assert(posix_syscall_handler(&f)==-38&&copies==previous);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-sysinfo-abi-') as directory:
            p = Path(directory)
            (p / 'test.c').write_text(code)
            build = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                    '-fsanitize=undefined', '-I', str(ROOT / 'kernel/include'),
                                    str(p / 'test.c'), '-o', str(p / 'test')],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(p / 'test')], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
