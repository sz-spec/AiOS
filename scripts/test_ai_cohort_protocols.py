"""Run extracted native benchmark protocols with real host fork/pipe/shared memory.

The adapter models registry/dispatch policy; it does not qualify native IPC.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import re

def function(text, name):
    match = re.search(r"^static[^\n]*\b" + re.escape(name) + r"\([^;]*?\)\s*\{", text, re.M)
    if match is None: raise ValueError(name)
    start = match.start(); end = match.end(); depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <sched.h>
#include <time.h>
#include <signal.h>
#define COLLISION_CHILDREN 10
#define BFS_NODES 256
#define BFS_AGENTS 32
#define EEXIST 17
#define SYS_YIELD 24
#define SYS_SHM_CREATE 410
#define SYS_SHM_DESTROY 411
#define SYS_SHM_MAP 412
#define SYS_SHM_UNMAP 413
#define VOS3_SHM_FLAG_PUBLIC 64
#define CAP_SEARCH 1
#define TEST_PASS(...) (++g_pass)
#define TEST_FAIL(...) (++g_fail)
typedef struct { uint64_t item_id,payload_addr; int type,priority,payload_size; } dispatch_item_t;
typedef struct { uint32_t adj[256]; volatile uint32_t visited[8],result_count,done_agents,processed_items; } graph_state_t;
typedef struct { int lock,alive; pid_t owner; int head,tail,nextslot; dispatch_item_t queue[1024]; char data[4096]; } shared_t;
static shared_t *sh;
static int g_pass,g_fail,mode,mapped,forks,child;
static void lock(void) { while(__atomic_exchange_n(&sh->lock,1,__ATOMIC_ACQUIRE)) sched_yield(); }
static void unlock(void) { __atomic_store_n(&sh->lock,0,__ATOMIC_RELEASE); }
static unsigned long get_uptime_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000UL+t.tv_nsec/1000000; }
static long syscall0(int n) { (void)n;return sched_yield(); }
static long syscall3(int n,long name,long size,long flags) {
 (void)n;(void)name;(void)size;(void)flags;lock();
 if(sh->alive) { unlock();return -17; }
 sh->alive=1;sh->owner=getpid();unlock();return 65;
}
static long syscall1(int n,long id) {
 (void)id;
 if(n==414) return sh->alive ? 4096:0;
 if(n==SYS_SHM_DESTROY) { sh->alive=0;return 0; } abort();
}
static long syscall2(int n,long id,long addr) {
 (void)id;(void)addr;
 if(n==SYS_SHM_MAP) { if(mode==3 && child) return 0; mapped++;return (long)sh->data; }
 if(n==SYS_SHM_UNMAP) { mapped--;return mode==4 && child ? -1:0; } abort();
}
static pid_t checked_fork(void) {
 if(mapped) { g_fail++;return -1; }
 if(mode==1 && ++forks==2) return -1;
 pid_t p=fork();if(!p) child=1;return p;
}
static void checked_exit(int code) {
 if(sh->owner==getpid() && mode!=6) sh->alive=0;
 _exit(mode==2 ? 7:code);
}
static long agent_register(char*n,int c) { (void)n;(void)c;return __atomic_fetch_add(&sh->nextslot,1,__ATOMIC_SEQ_CST); }
static long agent_deregister(long s) { (void)s;return 0; }
static long dispatch_submit(dispatch_item_t*i) {
 if(mode==5)return -1;
 lock(); if(sh->tail>=1024) {unlock();return -1;} sh->queue[sh->tail++]=*i;unlock();return 0;
}
static long dispatch_pull(long s,dispatch_item_t*i) {
 (void)s;lock();if(sh->head==sh->tail){unlock();return -1;}*i=sh->queue[sh->head++];unlock();return 0;
}
static long dispatch_complete(long s,long i) { (void)s;(void)i;return 0; }
#define fork checked_fork
#define _exit checked_exit
'''

class CohortTests(unittest.TestCase):
    def test_real_process_protocols_and_failures(self):
        advanced=(ROOT/'user/src/test_advanced_chaos.c').read_text()
        cluster=(ROOT/'user/src/test_agent_cluster.c').read_text()
        body=HARNESS+function(advanced,'shm_id_collision')
        for name in ('generate_graph','reference_bfs','distributed_bfs_32_agents'):
            body+=function(cluster,name)
        body+=r'''
int main(int argc,char**argv) {
 if(argc!=3)return 90;mode=atoi(argv[2]);
 sh=mmap(NULL,sizeof(*sh),PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANON,-1,0);
 if(sh==MAP_FAILED)return 91;
 if(atoi(argv[1])==0) shm_id_collision(); else distributed_bfs_32_agents();
 if(mapped)return 92;
 return mode==0 ? (g_fail || !g_pass) : !g_fail;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-ai-cohort-') as directory:
            p=Path(directory);(p/'test.c').write_text(body)
            compiler=shutil.which('cc');self.assertIsNotNone(compiler)
            result=subprocess.run([compiler,'-O1',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            for cohort,modes in [(0,(0,1,2,3,4,6)),(1,(0,1,2,3,4,5))]:
                for mode in modes:
                    with self.subTest(cohort=cohort,mode=mode):
                        result=subprocess.run([str(p/'test'),str(cohort),str(mode)],capture_output=True,text=True,timeout=12)
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

if __name__=='__main__': unittest.main()
