#!/usr/bin/env python3
"""Compile extracted production ID/cookie helpers; no rewritten algorithm."""
from pathlib import Path
import hashlib
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

def function(source, signature):
    start=source.index(signature)
    brace=source.index('{',start)
    depth=0
    for index in range(brace,len(source)):
        if source[index]=='{':depth+=1
        elif source[index]=='}':
            depth-=1
            if depth==0:return source[start:index+1]
    raise ValueError('unterminated function')

class IdentityHelpers(unittest.TestCase):
    def test_actual_production_boundaries(self):
        shm=(ROOT/'kernel/src/ipc/shm.c').read_text()
        task=(ROOT/'kernel/src/sched/task.c').read_text()
        definitions='\n'.join(line for line in shm.splitlines() if line.startswith(('#define SHM_SLOT_BITS','#define SHM_SLOT_MASK','#define SHM_GENERATION_MAX')))
        extracted=definitions+'\n'+'\n'.join([function(shm,'static uint32_t shm_slot('),function(shm,'static vos3_shm_region_t* shm_get('),function(shm,'static vos3_ipc_id_t shm_alloc_id('),function(task,'static uint64_t alloc_identity_cookie(')])
        harness=r'''
#include <stdint.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define VOS3_SHM_MAX_REGIONS 64U
#define VOS3_IPC_INVALID UINT32_MAX
#define VOS3_SHM_MAGIC 0x1234U
typedef uint32_t vos3_ipc_id_t;
typedef struct {uint32_t magic,id;} vos3_shm_region_t;
static vos3_shm_region_t* g_shm_table[64];
static uint32_t g_shm_generation[64];
static uint64_t g_last_identity_cookie;
static void check(int condition,const char *why){if(!condition){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
'''+extracted+r'''
int main(void){
 uint32_t first=shm_alloc_id();check(first==65&&shm_slot(first)==1,"first generation nonzero");
 vos3_shm_region_t live={VOS3_SHM_MAGIC,first};g_shm_table[1]=&live;
 check(shm_get(first)==&live,"exact live lookup");
 check(!shm_get(0)&&!shm_get(1)&&!shm_get(UINT32_MAX)&&!shm_get(0x80000041U),"invalid/stale/wide lookup");
 check(shm_alloc_id()==66,"occupied slot skipped");
 g_shm_table[1]=NULL;uint32_t replacement=shm_alloc_id();check(replacement==129,"slot reused with newer generation");live.id=replacement;g_shm_table[1]=&live;
 check(!shm_get(first)&&shm_get(replacement)==&live,"stale handle cannot alias replacement");
 memset(g_shm_table,0,sizeof(g_shm_table));
 for(unsigned i=1;i<64;i++)g_shm_generation[i]=SHM_GENERATION_MAX;
 check(shm_alloc_id()==VOS3_IPC_INVALID,"all exhausted slots refuse wrap");
 g_shm_generation[63]=SHM_GENERATION_MAX-1;
 uint32_t maximum=shm_alloc_id();check(maximum==INT32_MAX&&shm_slot(maximum)==63,"maximum positive handle exact");
 check(shm_alloc_id()==VOS3_IPC_INVALID,"last issued generation retires slot");
 live.id=maximum;g_shm_table[63]=&live;check(shm_get(maximum)==&live,"maximum lookup");
 live.magic=0;check(!shm_get(maximum),"retiring object invisible");
 check(alloc_identity_cookie()==1&&alloc_identity_cookie()==2,"cookie uniqueness");
 g_last_identity_cookie=UINT64_MAX-1;check(alloc_identity_cookie()==UINT64_MAX,"last cookie valid");
 check(alloc_identity_cookie()==0&&alloc_identity_cookie()==0&&g_last_identity_cookie==UINT64_MAX,"cookie exhaustion never wraps");
 puts("PASS actual SHM generation and task cookie boundaries");return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-shm-identity-') as directory:
            source=Path(directory)/'test.c';binary=Path(directory)/'test';source.write_text(harness)
            built=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(source),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(built.returncode,0,built.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('PASS actual SHM',result.stdout)
            print('production-helper-sha256',hashlib.sha256(extracted.encode()).hexdigest())

if __name__=='__main__':unittest.main()
