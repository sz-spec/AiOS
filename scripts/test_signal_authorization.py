"""Exercise the actual shared permission check and legacy syscall 450 wrapper."""
from pathlib import Path
import subprocess
import tempfile
import unittest

if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class SignalAuthorizationTests(unittest.TestCase):
    def test_actual_legacy_wrapper_and_shared_policy(self):
        signal = (ROOT / "kernel/src/ipc/signal.c").read_text()
        ipc = (ROOT / "kernel/src/ipc/ipc.c").read_text()
        extracted = function(signal, "int vos3_signal_check_permission(")
        extracted += "\n" + function(ipc, "static int64_t sys_kill(")
        code = r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#define VOS3_SIGCONT 18
#define VOS3_SIG_MAX 31
typedef uint32_t vos3_tid_t;
typedef struct {uint32_t uid,sid,tid;} vos3_task_t;
typedef struct {uint64_t rdi,rsi;} vos3_syscall_frame_t;
static vos3_task_t sender={100,10,1},target={200,20,5000};
static vos3_task_t *current=&sender,*resolved=&target;
static int sends,last_sig,send_result;
static uint32_t last_tid;
static vos3_task_t *vos3_sched_current(void){return current;}
static vos3_task_t *vos3_task_get(uint32_t tid){return resolved&&resolved->tid==tid?resolved:NULL;}
static int vos3_signal_send(uint32_t tid,int sig){sends++;last_tid=tid;last_sig=sig;return send_result;}
''' + extracted + r'''
static void rejected(vos3_syscall_frame_t *f,int error){int before=sends;assert(sys_kill(f)==error);assert(sends==before);}
int main(void){
 vos3_syscall_frame_t f={5000,10};
 rejected(&f,-1); /* Foreign UID/session cannot send. */
 f.rsi=VOS3_SIGCONT;rejected(&f,-1);
 sender.sid=target.sid;f.rsi=10;rejected(&f,-1);
 f.rsi=VOS3_SIGCONT;assert(sys_kill(&f)==0&&sends==1&&last_sig==18&&last_tid==5000);
 sender.uid=target.uid;f.rsi=10;assert(sys_kill(&f)==0&&sends==2);
 sender.uid=0;sender.sid=999;assert(sys_kill(&f)==0&&sends==3);
 send_result=-7;assert(sys_kill(&f)==-7&&sends==4);send_result=0;
 current=NULL;rejected(&f,-3);current=&sender;
 resolved=NULL;rejected(&f,-3);resolved=&target;
 f.rdi=5001;rejected(&f,-3);
 f.rdi=(UINT64_C(1)<<32)|5000;rejected(&f,-22);
 f.rdi=5000;f.rsi=0;rejected(&f,-22);
 f.rsi=32;rejected(&f,-22);f.rsi=UINT64_MAX;rejected(&f,-22);
 f.rsi=(UINT64_C(1)<<32)|10;rejected(&f,-22);
 /* The same policy also protects signal-zero permission probes. */
 sender.uid=100;sender.sid=10;
 assert(vos3_signal_check_permission(&sender,&target,0)==-1);
 sender.uid=target.uid;assert(vos3_signal_check_permission(&sender,&target,0)==0);
 assert(vos3_signal_check_permission(NULL,&target,18)==-3);
 assert(vos3_signal_check_permission(&sender,NULL,18)==-3);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-signal-auth-") as directory:
            base = Path(directory)
            (base / "test.c").write_text(code)
            build = subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=undefined",
                 "-fno-sanitize-recover=undefined", str(base / "test.c"), "-o", str(base / "test")],
                capture_output=True, text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(base / "test")], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
