"""Exercise actual native saturation child-drain code with deterministic waits."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ReapTests(unittest.TestCase):
    def test_all_owned_children_and_failure_paths(self):
        source = (ROOT / 'user/src/test_max_saturation.c').read_text()
        start = source.index('static int reap_owned_children(')
        end = source.index('\n/* Logical removal', start)
        harness = r'''
#include <stdlib.h>
typedef int pid_t;
#define SYS_WAIT4 61
#define SYS_SCHED_YIELD 24
static int mode, calls, ticks, seen[1100];
static unsigned long get_uptime_ms(void) { return (unsigned long)(ticks++ * 1000); }
static long syscall0(int n) { (void)n; return 0; }
static long syscall4(int n,long pid,long status,long options,long unused) {
 (void)n;(void)unused;
 if(options!=1 || pid<=0 || pid>=1100 || seen[pid]) abort();
 calls++;
 if(mode==3) return 0;
 if(mode==4 && calls<4) return 0;
 seen[pid]=1;
 *(int*)status=mode==1 ? 256 : 0;
 return mode==2 ? -10 : pid;
}
'''
        harness += source[start:end]
        harness += r'''
int main(int argc,char**argv) {
 if(argc!=2) return 8;
 mode=atoi(argv[1]);
 pid_t children[1023];
 for(int i=0;i<1023;i++) children[i]=i==2 ? -1 : i+1;
 int reaped=-1;
 int bad=reap_owned_children(children,1023,&reaped);
 if(mode==0 || mode==4) {
   if(bad || reaped!=1022) return 1;
   for(int i=0;i<1023;i++) if(children[i]>0) return 2;
   return seen[1023] ? 0 : 3;
 }
 if(mode==1) return bad==1022 && reaped==1022 ? 0 : 4;
 if(mode==2) return bad==1022 && reaped==0 ? 0 : 5;
 return bad==1022 && reaped==0 && calls<100000 ? 0 : 6;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-saturation-reap-') as directory:
            path = Path(directory)
            (path / 'test.c').write_text(harness)
            compiler = shutil.which('cc')
            self.assertIsNotNone(compiler, 'C compiler required')
            subprocess.run([compiler, '-Wall', '-Wextra', '-Werror', str(path/'test.c'),
                            '-o', str(path/'test')], check=True, capture_output=True)
            for mode in range(5):
                with self.subTest(mode=mode):
                    result = subprocess.run([str(path/'test'), str(mode)], timeout=5)
                    self.assertEqual(result.returncode, 0)

    def test_cleanup_requires_physical_and_logical_readiness(self):
        source = (ROOT / 'user/src/test_max_saturation.c').read_text()
        start = source.index('static int await_owned_cleanup(')
        end = source.index('\n/* ═', start)
        harness = r'''
#include <stdlib.h>
#define SYS_SCHED_YIELD 24
typedef struct { unsigned long free_pages; unsigned int nr_tasks, nr_zombies; } vos3_sysinfo_t;
static int mode, calls, yields, clock_ticks;
static unsigned long get_uptime_ms(void) { return (unsigned long)(clock_ticks++ * 1000); }
static long syscall0(int n) { (void)n; yields++; return 0; }
static int get_sysinfo(vos3_sysinfo_t* info) {
 calls++; info->nr_tasks=4; info->nr_zombies=0; info->free_pages=10000;
 if(mode==0 && calls<3) info->free_pages=8000;
 if(mode==1) info->free_pages=8000;
 if(mode==2) return -1;
 if(mode==3) info->nr_tasks=5;
 if(mode==4) info->nr_zombies=1;
 if(mode==5) info->free_pages=10001;
 if(mode==6) info->free_pages=8001;
 return 0;
}
''' + source[start:end] + r'''
int main(int argc,char**argv) {
 if(argc!=2) return 9; mode=atoi(argv[1]);
 vos3_sysinfo_t info={10000,4,0};
 int result=await_owned_cleanup(&info,4,0,10000);
 if(mode==0) return result==1 && calls==3 && yields==2 ? 0:1;
 if(mode==5 || mode==6) return result==1 && yields==0 ? 0:2;
 if(mode==2) return result==0 && calls==1 ? 0:3;
 return result==0 && yields>0 && calls<10 ? 0:4;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-saturation-cleanup-') as directory:
            path = Path(directory)
            (path / 'test.c').write_text(harness)
            compiler = shutil.which('cc')
            self.assertIsNotNone(compiler)
            subprocess.run([compiler, '-Wall', '-Wextra', str(path/'test.c'),
                            '-o', str(path/'test')], check=True, capture_output=True)
            for mode in range(7):
                with self.subTest(mode=mode):
                    self.assertEqual(subprocess.run([str(path/'test'), str(mode)], timeout=5).returncode, 0)

if __name__ == '__main__':
    unittest.main()
