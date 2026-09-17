"""Compile the real native benchmark function against deterministic I/O faults.

This checks its oracle/protocol, not host or native scheduler performance.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'user/src/bench_csw_1ms.c'
PREFIX = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#define SYS_FORK 57
#define SYS_EXIT 60
#define SYS_WAIT4 61
static int g_pass, g_fail, mode, reads, writes, exit_code;
static jmp_buf child_exit;
#define TEST_PASS(n) (++g_pass)
#define TEST_FAIL(n,m) (++g_fail)
static unsigned long long rdtsc(void) { static unsigned long long n; return n+=100; }
static int pipe(int *fds) { static int n=3; fds[0]=n++; fds[1]=n++; return 0; }
static int close(int fd) { (void)fd; return 0; }
static long syscall0(int n) { (void)n; return mode>=10 ? 0 : 42; }
static long syscall1(int n, long status) { (void)n; exit_code=status; longjmp(child_exit,1); }
static long syscall4(int n,long pid,long status,long a,long b) {
 (void)n;(void)a;(void)b; *(int*)status=mode==4 ? 256 : 0;
 return mode==5 ? -1 : pid;
}
static long read(int fd,void *p,unsigned long n) {
 (void)fd;(void)n; reads++;
 if ((mode==1 && reads==20)||(mode==2 && reads==100)||(mode==11 && reads==100)) return 0;
 *(char*)p = mode==3 ? 0 : mode>=10 ? 0x42 : 0x43; return 1;
}
static long write(int fd,const void *p,unsigned long n) {
 (void)fd;(void)n; writes++;
 if (mode>=10 && *(const char*)p!=0x43) abort();
 return ((mode==6 || mode==12) && writes==100) ? 0 : 1;
}
'''
SUFFIX = r'''
int main(int argc,char **argv) {
 if(argc!=2) return 90;
 mode=atoi(argv[1]);
 if(setjmp(child_exit)==0) test_yield_csw_cost();
 if(mode>=10) {
   if(mode==10) return exit_code==0 && reads==2050 && writes==2050 ? 0 : 1;
   return exit_code==1 ? 0 : 1;
 }
 if(mode==0) return g_pass==1 && g_fail==0 && reads==2050 && writes==2050 ? 0 : 1;
 return g_pass==0 && g_fail==1 ? 0 : 1;
}
'''


class ExchangeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for actual-function regression')
        cls.temp = tempfile.TemporaryDirectory(prefix='vos-csw-exchange-')
        cls.addClassCleanup(cls.temp.cleanup)
        text = SOURCE.read_text()
        start = text.index('static void test_yield_csw_cost(void)')
        end = text.index('\n/* ===', start)
        function = text[start:end]
        cls.binaries = []
        for name, body in [('current', function), ('old-count', function.replace(
                'i < ITERS + WARMUP', 'i < ITERS', 1))]:
            source = Path(cls.temp.name) / (name + '.c')
            binary = Path(cls.temp.name) / name
            source.write_text(PREFIX + body + SUFFIX)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', str(source),
                            '-o', str(binary)], check=True, capture_output=True, text=True)
            cls.binaries.append(binary)

    def run_case(self, mode, binary=0):
        return subprocess.run([str(self.binaries[binary]), str(mode)],
                              capture_output=True, text=True, timeout=5)

    def test_complete_parent_and_child_exchange_counts(self):
        for mode in (0, 10):
            with self.subTest(mode=mode):
                result = self.run_case(mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_parent_short_io_bad_payload_and_failed_wait_cannot_pass(self):
        for mode in (1, 2, 3, 4, 5, 6):
            with self.subTest(mode=mode):
                self.assertEqual(self.run_case(mode).returncode, 0)

    def test_child_short_read_or_write_returns_failure(self):
        for mode in (11, 12):
            with self.subTest(mode=mode):
                self.assertEqual(self.run_case(mode).returncode, 0)

    def test_restoring_old_child_count_is_detected(self):
        self.assertNotEqual(self.run_case(10, binary=1).returncode, 0)


if __name__ == '__main__':
    unittest.main()
