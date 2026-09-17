"""Actual signal dispatcher forged-frame checks; uaccess/CPU return are mocked."""
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


class SignalReturnTests(unittest.TestCase):
    def test_actual_dispatcher_rejects_addresses_and_sanitizes_privileges(self):
        source = (ROOT / 'kernel/src/ipc/signal_syscall.c').read_text()
        header = (ROOT / 'kernel/include/vos/syscall.h').read_text()
        frame = re.search(r'typedef struct vos3_syscall_frame\s*\{.*?\}\s*vos3_syscall_frame_t;', header, re.S)
        self.assertIsNotNone(frame)
        constants = '\n'.join(line for line in source.splitlines() if re.match(r'#define SYS_\w+\s+\d+', line))
        code = r'''
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
#define VOS3_WARN(...) ((void)0)
#define VOS3_INFO(...) ((void)0)
#define VOS3_SIG_MAX 31
#define VOS3_SIG_SETMASK 2
#define VOS3_SIGCONT 18
typedef uint32_t vos3_tid_t;
typedef struct { uint32_t tgid,uid,sid; } vos3_task_t;
static vos3_task_t current_task, target_task;
static int sends;
static vos3_task_t* vos3_sched_current(void) { return &current_task; }
static vos3_task_t* vos3_task_get(vos3_tid_t tid) { (void)tid; return &target_task; }
static int vos3_signal_send(vos3_tid_t tid,int sig) { (void)tid;(void)sig;sends++;return 0; }
static int64_t sys_kill(int32_t p,int s) {(void)p;(void)s;return -38;}
static int64_t sys_rt_sigaction(int s,const void*a,void*b,size_t n) {(void)s;(void)a;(void)b;(void)n;return -38;}
static int64_t sys_rt_sigprocmask(int s,const void*a,void*b,size_t n) {(void)s;(void)a;(void)b;(void)n;return -38;}
static int64_t sys_pause(void) {return -38;}
static int64_t sys_alarm(uint32_t s) {(void)s;return -38;}
static int access_ok(const void*p,size_t n) {
    uintptr_t a=(uintptr_t)p;
    return a < UINT64_C(0x800000000000) && n <= UINT64_C(0x800000000000)-a;
}
static uint64_t wire[18];
static int copy_error,mask_error,mask_calls;
static uint32_t restored_mask;
static int vos3_copy_from_user(void*d,const void*s,size_t n) {
    assert((uintptr_t)s==0x10000 && n==sizeof(wire));
    if(copy_error) return -14;
    memcpy(d,wire,n);return 0;
}
static int vos3_sigprocmask(int how,const uint32_t*set,uint32_t*old) {
    assert(how==VOS3_SIG_SETMASK && set && !old); mask_calls++;
    if(mask_error) return -22;
    restored_mask=*set;return 0;
}
'''
        # Include the real permission helper too so authorization changes in
        # other dispatcher branches do not need a fake success implementation.
        signal_source = (ROOT / 'kernel/src/ipc/signal.c').read_text()
        code += function(signal_source, 'int vos3_signal_check_permission(')
        code += '\n' + constants + '\n' + frame[0] + '\n'
        code += function(source, 'static int64_t signal_syscall_handler(')
        code += r'''
static vos3_syscall_frame_t initial(void) {
    vos3_syscall_frame_t f;
    memset(&f,0x5a,sizeof(f)); f.rax=SYS_RT_SIGRETURN; f.user_rsp=0x10000;
    for(unsigned i=0;i<18;i++) wire[i]=0x2000+i;
    wire[7]=0x400000; wire[15]=0x700000; wire[17]=0x12345678;
    copy_error=mask_error=mask_calls=0; restored_mask=0;
    return f;
}
int main(void) {
    const uint64_t bad[]={0,UINT64_C(0x800000000000),UINT64_C(0xffff800000000000),UINT64_MAX};
    for(unsigned field=0;field<2;field++) for(unsigned i=0;i<4;i++) {
        vos3_syscall_frame_t f=initial(),before=f;
        wire[field?15:7]=bad[i];
        assert(signal_syscall_handler(&f)==-14);
        assert(memcmp(&f,&before,sizeof(f))==0 && mask_calls==0);
    }
    vos3_syscall_frame_t f=initial(),before=f;
    copy_error=1;
    assert(signal_syscall_handler(&f)==-14 && memcmp(&f,&before,sizeof(f))==0 && mask_calls==0);
    f=initial(); before=f; mask_error=1;
    assert(signal_syscall_handler(&f)==-22 && memcmp(&f,&before,sizeof(f))==0 && mask_calls==1);
    const uint64_t forbidden=(UINT64_C(3)<<12)|(UINT64_C(1)<<14)|(UINT64_C(1)<<17)|(UINT64_C(1)<<18);
    for(unsigned bit=0;bit<=64;bit++) {
        f=initial(); wire[8]=bit==64?UINT64_MAX:(UINT64_C(1)<<bit);
        assert(signal_syscall_handler(&f)==(int64_t)wire[0]);
        assert((f.r11&forbidden)==0 && (f.r11&0x202)==0x202);
        assert((f.r11&~UINT64_C(0xed7))==0);
        assert(f.rdi==wire[1] && f.rsi==wire[2] && f.rdx==wire[3]);
        assert(f.r10==wire[4] && f.r8==wire[5] && f.r9==wire[6]);
        assert(f.rcx==wire[7] && f.rbx==wire[9] && f.rbp==wire[10]);
        assert(f.r12==wire[11] && f.r13==wire[12] && f.r14==wire[13] && f.r15==wire[14]);
        assert(f.user_rsp==wire[15] && mask_calls==1 && restored_mask==wire[17]);
    }
    /* Actual dispatcher authorization, including signal-zero probes. */
    current_task.uid=100;current_task.sid=7;
    target_task.uid=200;target_task.sid=8;target_task.tgid=23;
    for(unsigned syscall=0;syscall<2;syscall++) for(unsigned probe=0;probe<2;probe++) {
        f=initial(); f.rax=syscall?SYS_TGKILL:SYS_TKILL;
        f.rdi=syscall?23:42; f.rsi=syscall?42:(probe?10:0); f.rdx=probe?10:0;
        sends=0;assert(signal_syscall_handler(&f)==-1 && sends==0);
        target_task.uid=100;
        assert(signal_syscall_handler(&f)==0 && sends==(probe?1:0));
        target_task.uid=200;
    }
    f=initial();f.rax=SYS_TGKILL;f.rdi=24;f.rsi=42;f.rdx=10;sends=0;
    assert(signal_syscall_handler(&f)==-3 && sends==0);
    f.rdi=0;assert(signal_syscall_handler(&f)<0 && sends==0);
    f.rdi=23;f.rsi=0;assert(signal_syscall_handler(&f)<0 && sends==0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-sigreturn-') as directory:
            base = Path(directory)
            (base / 'test.c').write_text(code)
            result = subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=undefined','-fno-sanitize-recover=undefined',str(base/'test.c'),'-o',str(base/'test')], capture_output=True, text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result = subprocess.run([str(base/'test')],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        print('signal_syscall.c sha256='+hashlib.sha256(source.encode()).hexdigest())
        print('signal.c sha256='+hashlib.sha256(signal_source.encode()).hexdigest())


if __name__ == '__main__':
    unittest.main()
