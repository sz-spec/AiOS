"""Actual agent-pass source: ownership, child failure propagation and byte count."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function
ROOT=Path(__file__).resolve().parents[1]

class ThroughputProtocolTests(unittest.TestCase):
    def test_actual_pass_and_accounting(self):
        source=(ROOT/'user/src/bench_ai_throughput.c').read_text()
        constants='\n'.join(x for x in source.splitlines() if re.match(r'#define (SYS_(FORK|EXIT|WAIT4|SHM_MAP|SHM_UNMAP)|NUM_AGENTS|NUM_PASSES|SHM_SIZE|SLICE_SIZE|TOTAL_DATA_MB)\s',x))
        code=r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef int pid_t;
static int mode, forks, waits, maps, unmaps, pass_now, seen[8];
static unsigned char data[4*1024*1024];
static long syscall0(int n);
static long syscall1(int n,long arg);
static long syscall2(int n,long a,long b);
static long syscall4(int n,long a,long b,long c,long d);
'''+constants+'\n'+function(source,'static void agent_work(')+'\n'+function(source,'static int verify_agent_data(')+'\n'+function(source,'static int run_agent_pass(')+r'''
static long syscall0(int n){assert(n==SYS_FORK);int a=forks++;
 if(mode==6)return -95; /* Actual kernel contract for mapped parent. */
 if(mode>=7)return 0;
 if(mode==1&&a==3)return -12;
 agent_work(data,a,pass_now);return 100+a;
}
static long syscall1(int n,long arg){assert(n==SYS_EXIT&&mode>=7);
 assert(maps==1);assert(arg==(mode==8?21:mode==9?22:0));
 assert(unmaps==(mode==8?0:1));
 if(mode==7)assert(verify_agent_data(data,0,pass_now)==0);
 exit(0);
}
static long syscall2(int n,long a,long b){(void)a;(void)b;
 if(n==SYS_SHM_MAP){maps++;return mode==8?-12:(long)data;}
 assert(n==SYS_SHM_UNMAP);unmaps++;return mode==9?-22:0;
}
static long syscall4(int n,long pid,long status,long options,long unused){
 assert(n==SYS_WAIT4&&options==0&&!unused&&pid>=100&&pid<108);
 int a=(int)pid-100;assert(!seen[a]);seen[a]=1;waits++;
 if(mode!=4)*(int*)status=mode==2&&a==4?21<<8:0;
 if(mode==3&&a==4)return -10;
 if(mode==5&&a==4)return pid+1;
 return pid;
}
int main(int argc,char**argv){assert(argc==2);mode=atoi(argv[1]);
 assert(NUM_AGENTS==8&&NUM_PASSES==8);
 assert((unsigned long)NUM_AGENTS*SLICE_SIZE*NUM_PASSES==TOTAL_DATA_MB*1024UL*1024);
 assert(TOTAL_DATA_MB==32);
 for(pass_now=0;pass_now<NUM_PASSES;pass_now++){
  forks=waits=maps=unmaps=0;memset(seen,0,sizeof(seen));
  int errors=run_agent_pass(77,pass_now);
  assert(forks==8);assert(waits==(mode==1?7:mode==6?0:8));
  assert(errors==(mode==0?0:mode==4||mode==6?8:1));
  if(mode==0){for(int a=0;a<8;a++)assert(verify_agent_data(data,a,pass_now)==0);
   data[0]^=1;assert(verify_agent_data(data,0,pass_now)<0);}
 }
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-throughput-') as directory:
            p=Path(directory);(p/'test.c').write_text(code)
            r=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=undefined',str(p/'test.c'),'-o',str(p/'test')],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            for mode in range(10):
                with self.subTest(mode=mode):
                    r=subprocess.run([str(p/'test'),str(mode)],capture_output=True,text=True,timeout=10)
                    self.assertEqual(r.returncode,0,r.stderr)

if __name__=='__main__':unittest.main()
