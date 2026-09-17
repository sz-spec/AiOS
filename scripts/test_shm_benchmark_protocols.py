"""Actual benchmark functions over host fork/shared backing; not native qualification."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

def function(text, name):
    start = text.index('static ', text.index(name) - 30)
    opening = text.index('{', text.index(name, start))
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]

HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <sched.h>
static int g_pass, g_fail, active, fork_count, mode, child_role;
static FILE *backing;
static size_t backing_size;
#define TEST_PASS(n) (++g_pass)
#define TEST_FAIL(n) (++g_fail)
#define SYS_SHM_CREATE 410
#define SYS_SHM_DESTROY 411
#define SYS_SHM_MAP 412
#define SYS_SHM_UNMAP 413
#define VOS3_SHM_FLAG_PUBLIC 64
#define VOS3_SYS_YIELD 24
#define NUM_AGENTS 5
#define CHUNK_SIZE (1024UL*1024UL)
#define SHM_256MB (256UL*1024UL*1024UL)
#define SHM_256MB_PAGES 65536
#define SHM_4MB (4UL*1024UL*1024UL)
static long read_free_pages(void) { return 500000; }
static long syscall0(int n) { (void)n; return sched_yield(); }
static long syscall3(int n,long name,long size,long flags) {
 (void)n;(void)name;(void)flags;
 backing=tmpfile(); if(!backing) return -1;
 backing_size=size; return ftruncate(fileno(backing),size)==0 ? 1:-1;
}
static long syscall1(int n,long id) {
 (void)n;(void)id; int result=fclose(backing); backing=NULL; return result;
}
static long syscall2(int n,long id,long arg) {
 (void)id;
 if(n==SYS_SHM_MAP) {
   void *p=mmap(NULL,backing_size,PROT_READ|PROT_WRITE,MAP_SHARED,fileno(backing),0);
   if(p==MAP_FAILED) return 0;
   active++; return (long)p;
 }
 if(n==SYS_SHM_UNMAP) { active--; return munmap((void*)arg,backing_size); }
 abort();
}
static pid_t checked_fork(void) {
 if(active) { g_fail++; return -1; }
 fork_count++;
 if(mode==1 && fork_count==2) return -1;
 pid_t p=fork(); if(p==0) child_role=1; return p;
}
static void checked_exit(int status) {
 _exit(mode==2 && child_role ? 9 : status);
}
#define fork checked_fork
#define _exit checked_exit
'''

class ProtocolTests(unittest.TestCase):
    def test_actual_crossprocess_functions(self):
        dispatch = (ROOT/'user/src/test_shm_dispatch.c').read_text()
        diag = (ROOT/'user/src/diag_shm_stress.c').read_text()
        names = ['test_cross_process_context', 'test_multi_agent_concurrent',
                 'test_giant_context_window', 'test_tlb_stress']
        pieces = [function(dispatch, n) for n in names[:2]]
        pieces += [function(diag, 'compute_checksum')]
        pieces += [function(diag, n) for n in names[2:]]
        with tempfile.TemporaryDirectory(prefix='vos-shm-protocol-') as directory:
            path=Path(directory)
            body=HARNESS+'\n'.join(pieces)+r'''
int main(int argc,char**argv) {
 if(argc!=3) return 90;
 mode=atoi(argv[2]);
 switch(atoi(argv[1])) {
 case 0:test_cross_process_context();break;
 case 1:test_multi_agent_concurrent();break;
 case 2:test_giant_context_window();break;
 case 3:test_tlb_stress();break;
 }
 if(active || backing) return 91;
 return mode==0 ? (g_fail || !g_pass) : !g_fail;
}
'''
            (path/'test.c').write_text(body)
            compiler=shutil.which('cc')
            self.assertIsNotNone(compiler)
            built=subprocess.run([compiler,'-O1','-Wall','-Wextra',str(path/'test.c'),'-o',str(path/'test')],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stderr)
            for cohort in range(4):
                for mode in (0, 2, *(() if cohort==0 else (1,))):
                    with self.subTest(cohort=cohort, mode=mode):
                        result=subprocess.run([str(path/'test'),str(cohort),str(mode)],capture_output=True,text=True,timeout=30)
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__':
    unittest.main()
