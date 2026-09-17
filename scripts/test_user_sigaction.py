"""Exercise the actual libc wrapper's signal-return ABI with a captured syscall."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
if __package__:
    from .test_shm_identity import function
else:
    from test_shm_identity import function

ROOT = Path(__file__).resolve().parents[1]


class UserSigactionTests(unittest.TestCase):
    def test_real_wrapper_supplies_user_restorer_without_mutating_caller(self):
        source = (ROOT / 'user/lib/signal.c').read_text()
        code = '#include ' + json.dumps(str(ROOT / 'user/include/signal.h')) + '\n'
        code += r'''
#include <assert.h>
#include <string.h>
#define SYS_RT_SIGACTION 13
static struct sigaction captured;
static int was_null;
static long result;
static void __restore_rt(void) {}
static void custom_restorer(void) {}
static void handler(int sig) {(void)sig;}
static long syscall4(long nr,long sig,long act,long old,long size) {
 assert(nr==13 && sig==SIGUSR1 && size==8);
 was_null=act==0;
 if(act) captured=*(const struct sigaction*)act;
 if(old) ((struct sigaction*)old)->sa_handler=SIG_IGN;
 return result;
}
''' + function(source, 'int sigaction(') + r'''
int main(void) {
 struct sigaction original={0},old={0};
 original.sa_handler=handler; original.sa_flags=SA_RESTART;
 original.sa_mask[0]=0x12; original.sa_mask[1]=0x34;
 struct sigaction saved=original;
 assert(sigaction(SIGUSR1,&original,&old)==0);
 assert(!was_null && captured.sa_handler==handler);
 assert(captured.sa_flags==(SA_RESTART|SA_RESTORER));
 assert(captured.sa_restorer==__restore_rt);
 assert(captured.sa_mask[0]==0x12 && captured.sa_mask[1]==0x34);
 assert(memcmp(&original,&saved,sizeof(saved))==0 && old.sa_handler==SIG_IGN);
 original.sa_flags|=SA_RESTORER;original.sa_restorer=custom_restorer;
 assert(sigaction(SIGUSR1,&original,0)==0 && captured.sa_restorer==custom_restorer);
 assert(sigaction(SIGUSR1,0,&old)==0 && was_null);
 for(int i=0;i<2;i++){
  original.sa_handler=i?SIG_IGN:SIG_DFL; original.sa_flags=0;original.sa_restorer=0;
  assert(sigaction(SIGUSR1,&original,0)==0 && captured.sa_flags==0 && !captured.sa_restorer);
 }
 result=-22;assert(sigaction(SIGUSR1,0,0)==-22);
 return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix='vos-sigaction-') as directory:
            p = Path(directory)
            (p/'test.c').write_text(code)
            r = subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(p/'test.c'), '-o', str(p/'test')], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = subprocess.run([str(p/'test')], capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stdout+r.stderr)


if __name__ == '__main__':
    unittest.main()
