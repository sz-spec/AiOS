"""Run actual signal lifetime C code with allocation-failure and lock oracles."""
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class SignalLifetimeTests(unittest.TestCase):
    def test_actual_signal_ownership_and_failure_paths(self):
        source = (ROOT/'kernel/src/ipc/signal.c').read_text()
        header = (ROOT/'kernel/include/vos/ipc.h').read_text()
        structures = []
        for name in ('vos3_sigaction', 'vos3_signal_actions', 'vos3_signal_state'):
            match = re.search(r'typedef struct ' + name + r'\s*\{.*?\}\s*' + name + r'_t;', header, re.S)
            self.assertIsNotNone(match, name)
            structures.append(match[0])
        constants = '\n'.join(line for line in header.splitlines() if re.match(r'#define (VOS3_SIG_MAX|VOS3_SIG_DFL|VOS3_SIG_IGN|VOS3_IPC_OK|VOS3_IPC_ERR_NOMEM|VOS3_IPC_ERR_INVALID)\s', line))
        extracted = '\n'.join(function(source, signature) for signature in (
            'static uint64_t sig_lock(', 'static void sig_unlock(',
            'static vos3_signal_actions_t* sig_alloc_actions(',
            'int vos3_signal_task_init(', 'int vos3_signal_task_clone(',
            'void vos3_signal_task_destroy(', 'int vos3_signal_task_exec('))
        definitions = constants + '\ntypedef void (*vos3_sighandler_t)(int);\n' + '\n'.join(structures)
        harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
''' + definitions + r'''
typedef struct {uint32_t tid; vos3_signal_state_t *signal_state;} vos3_task_t;
static int g_sig_lock,irq_off,alloc_attempts,fail_at,live;
static void *owned[64];
static void check(int ok,const char *why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
static uint64_t vos3_irq_save(void){uint64_t old=irq_off;irq_off=1;return old;}
static void vos3_irq_restore(uint64_t old){check(!g_sig_lock,"restore while lock held");irq_off=(int)old;}
static void vos3_spinlock_lock(int *lock){check(irq_off&&!*lock,"IRQ-safe nonnested lock");*lock=1;}
static void vos3_spinlock_unlock(int *lock){check(irq_off&&*lock,"unlock ownership");*lock=0;}
static void *vos3_kzalloc(size_t n){
 check(!g_sig_lock,"allocation under signal lock");
 if(++alloc_attempts==fail_at)return NULL;
 void *p=calloc(1,n);check(p!=NULL,"host allocation");
 for(unsigned i=0;i<64;i++)if(!owned[i]){owned[i]=p;live++;return p;}
 check(0,"allocation oracle capacity");return NULL;
}
static void vos3_kfree(void *p){
 check(!g_sig_lock,"free under signal lock");if(!p)return;
 for(unsigned i=0;i<64;i++)if(owned[i]==p){owned[i]=NULL;live--;free(p);return;}
 check(0,"double free or foreign ownership");
}
static void failures(int nth){alloc_attempts=0;fail_at=nth;}
static void caught(int sig){(void)sig;}
''' + extracted + r'''
int main(void){
 vos3_task_t parent={UINT32_MAX,NULL},child={1000000,NULL},sibling={300,NULL};
 check(vos3_signal_task_init(NULL)==VOS3_IPC_ERR_INVALID,"null init rejected");
 for(int n=1;n<=2;n++){
  failures(n);check(vos3_signal_task_init(&parent)==VOS3_IPC_ERR_NOMEM,"init allocation failure returned");
  check(!parent.signal_state&&!live&&alloc_attempts==n,"init failure exact rollback");
 }
 failures(0);check(vos3_signal_task_init(&parent)==0&&live==2&&alloc_attempts==2,"high TID init has two owned objects");
 check(vos3_signal_task_init(&parent)==VOS3_IPC_ERR_INVALID&&live==2,"duplicate init preserves owner");
 vos3_signal_state_t *ps=parent.signal_state;vos3_signal_actions_t *original=ps->handlers;
 check(original->refs==1&&!ps->blocked&&!ps->pending,"initial defaults");
 ps->blocked=0x12345678U;ps->pending=0x0400U;
 original->actions[10]=(vos3_sigaction_t){caught,0x1111U,0x2222U,UINT64_C(0x123456789abc)};
 original->actions[12]=(vos3_sigaction_t){VOS3_SIG_IGN,0x3333U,0x4444U,UINT64_C(0x987654321)};
 for(int share=0;share<=1;share++)for(int n=1;n<=(share?1:2);n++){
  failures(n);check(vos3_signal_task_clone(&child,&parent,share)==VOS3_IPC_ERR_NOMEM,"clone allocation failure returned");
  check(!child.signal_state&&live==2&&original->refs==1&&alloc_attempts==n,"clone failure exact rollback and parent retained");
 }
 failures(0);check(vos3_signal_task_clone(&child,&parent,0)==0&&live==4&&alloc_attempts==2,"fork owns copied state and handlers");
 check(child.signal_state!=ps&&child.signal_state->handlers!=original,"fork does not alias ownership");
 check(!child.signal_state->pending&&child.signal_state->blocked==ps->blocked,"fork clears pending and inherits mask");
 check(!memcmp(child.signal_state->handlers->actions,original->actions,sizeof(original->actions)),"fork copies every action field");
 child.signal_state->handlers->actions[10].mask=9;child.signal_state->blocked=7;child.signal_state->pending=2;
 check(original->actions[10].mask==0x1111U&&ps->blocked==0x12345678U&&ps->pending==0x0400U,"fork mutation isolation");
 vos3_signal_task_destroy(&child);vos3_signal_task_destroy(&child);check(live==2&&!child.signal_state,"repeated destroy idempotent");
 original->refs=UINT32_MAX;failures(0);
 check(vos3_signal_task_clone(&child,&parent,1)==VOS3_IPC_ERR_INVALID,"shared reference overflow refused");
 check(!child.signal_state&&original->refs==UINT32_MAX&&live==2,"overflow no mutation or leak");original->refs=1;
 failures(0);check(vos3_signal_task_clone(&child,&parent,1)==0&&live==3&&alloc_attempts==1,"shared clone only allocates private pending state");
 check(child.signal_state!=ps&&child.signal_state->handlers==original&&original->refs==2,"only handlers shared");
 child.signal_state->blocked=3;child.signal_state->pending=4;original->actions[10].flags=8;
 check(ps->blocked==0x12345678U&&ps->pending==0x0400U&&child.signal_state->handlers->actions[10].flags==8,"private masks/pending shared dispositions");
 failures(1);check(vos3_signal_task_exec(&child)==VOS3_IPC_ERR_NOMEM,"exec allocation failure surfaced");
 check(child.signal_state->handlers==original&&original->refs==2&&live==3,"failed exec leaves shared table intact");
 failures(0);check(vos3_signal_task_exec(&child)==0&&live==4&&alloc_attempts==1,"exec detaches with one allocation");
 vos3_signal_actions_t *detached=child.signal_state->handlers;
 check(detached!=original&&detached->refs==1&&original->refs==1,"exec ownership split");
 check(detached->actions[10].handler==VOS3_SIG_DFL&&!detached->actions[10].mask&&!detached->actions[10].flags&&!detached->actions[10].sa_restorer,"exec clears caught action and ancillary state");
 check(detached->actions[12].handler==VOS3_SIG_IGN,"exec preserves ignored disposition");
 check(child.signal_state->blocked==3&&child.signal_state->pending==4,"exec retains private blocked and pending");
 check(original->actions[10].handler==caught&&original->actions[10].flags==8,"exec preserves sibling caught action");
 vos3_signal_task_destroy(&parent);check(live==2&&child.signal_state->handlers==detached,"parent release does not free detached child");
 failures(0);check(vos3_signal_task_exec(&child)==0&&live==2,"exclusive exec replaces and frees old table");
 vos3_signal_task_destroy(&child);check(live==0,"final owners release all allocations");
 failures(0);check(vos3_signal_task_clone(&child,&sibling,0)==VOS3_IPC_ERR_INVALID&&!live&&!child.signal_state,"missing parent state rolls back copied allocations");
 failures(0);check(vos3_signal_task_exec(&sibling)==VOS3_IPC_ERR_INVALID&&!live,"missing exec state frees candidate");
 for(unsigned i=0;i<300;i++){
  sibling.tid=UINT32_MAX;failures(0);
  check(vos3_signal_task_init(&sibling)==0&&live==2,"reused high TID has fresh owned storage");
  check(!sibling.signal_state->pending&&!sibling.signal_state->blocked&&sibling.signal_state->handlers->actions[10].handler==VOS3_SIG_DFL,"retired task signal state never resurfaces");
  irq_off=1;vos3_signal_task_destroy(&sibling);check(irq_off==1,"caller IRQ-disabled state restored");irq_off=0;
  vos3_signal_task_destroy(&sibling);check(!live,"repeated lifecycle has zero allocation drift");
 }
 vos3_signal_task_destroy(NULL);vos3_signal_task_destroy(&sibling);
 check(!live&&!irq_off&&!g_sig_lock,"all ownership and locks restored");
 puts("PASS actual signal init/clone/share/exec/destroy and every allocation failure");
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-signal-lifetime-') as directory:
            base=Path(directory); (base/'test.c').write_text(harness)
            build=subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-fno-sanitize-recover=undefined',str(base/'test.c'),'-o',str(base/'test')],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            result=subprocess.run([str(base/'test')],capture_output=True,text=True,timeout=10)
            print('production-lifetime-sha256',hashlib.sha256((definitions+extracted).encode()).hexdigest())
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__':
    unittest.main()
