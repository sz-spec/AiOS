"""Exercise the actual swarm controller with failed and successful clone calls."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class SwarmControllerTests(unittest.TestCase):
    def test_partial_spawn_terminates_but_never_passes(self):
        text = (ROOT/'user/src/bench_ai_scale.c').read_text()
        production = function(text, 'static void test_agent_swarm(')
        definitions = '\n'.join(line for line in text.splitlines() if line.startswith(('#define NUM_FPU_THREADS ', '#define STACK_SIZE ', '#define SPIN_LIMIT ', '#define CLONE_', '#define SYS_CLONE ', '#define SYS_EXIT ', '#define SYS_YIELD ')))
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
''' + definitions + r'''
static struct {unsigned long long lo,hi;} g_fpu_patterns[NUM_FPU_THREADS];
static int g_fpu_results[NUM_FPU_THREADS],g_fpu_slot;
static char g_fpu_stacks[NUM_FPU_THREADS][STACK_SIZE];
static int clone_calls,spawned,allowed,yield_calls,passed,failed;
#define TEST_PASS(...) (passed++)
#define TEST_FAIL(...) (failed++)
static void check(int ok,const char *why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
static void fpu_thread_body(void){check(0,"mock returned in child");}
static long syscall1(long n,long a){(void)n;(void)a;check(0,"unexpected child exit");return 0;}
static long syscall5(long n,long a,long b,long c,long d,long e){
 (void)a;(void)b;(void)c;(void)d;(void)e;check(n==SYS_CLONE,"clone syscall");
 int index=clone_calls++;
 /* Scatter failures: successful children still claim dense result slots. */
 if(allowed==20 && index%8>=5)return -11;
 if(allowed==0)return -11;
 spawned++;return 100+index;
}
static long syscall0(long n){
 check(n==SYS_YIELD,"yield syscall");
 check(++yield_calls<=2,"waiting on result slots of children never created");
 /* Simulate every successfully created child finishing after one yield. */
 for(int i=0;i<spawned;i++)g_fpu_results[i]=1;
 return 0;
}
''' + production + r'''
int main(void){
 int cases[]={20,32,0};
 for(unsigned k=0;k<sizeof(cases)/sizeof(cases[0]);k++){
  allowed=cases[k];clone_calls=spawned=yield_calls=passed=failed=0;
  test_agent_swarm();
  check(clone_calls==NUM_FPU_THREADS,"all32 creation attempts retained");
  check(spawned==allowed,"mock successful spawn count");
  check(yield_calls==(allowed?1:0),"wait only for actual live children");
  check(passed==(allowed==32)&&failed==(allowed!=32),"partial/zero creation must remain FAIL");
 }
 puts("PASS actual swarm controller:20/32/0 spawns, bounded waits, strict32-thread success");
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-swarm-controller-') as directory:
            base=Path(directory); (base/'test.c').write_text(source)
            build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(base/'test.c'),'-o',str(base/'test')],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            result=subprocess.run([str(base/'test')],capture_output=True,text=True,timeout=10)
            print('production-function-sha256',hashlib.sha256(production.encode()).hexdigest())
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':
    unittest.main()
