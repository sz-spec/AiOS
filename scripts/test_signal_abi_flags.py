"""Compile the real signal ABI decoder; test musl signed flags and rejection."""
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


class SignalABIFlagsTests(unittest.TestCase):
    def test_actual_decoder(self):
        source = (ROOT / "kernel/src/ipc/signal_syscall.c").read_text()
        header = (ROOT / "kernel/include/vos/ipc.h").read_text()
        constants = "\n".join(
            line for line in header.splitlines()
            if re.match(r"#define VOS3_SA_(RESTART|NODEFER|RESETHAND|RESTORER)\s", line)
        )
        flags = re.search(r"typedef enum vos3_sigaction_flags\s*\{.*?\}\s*vos3_sigaction_flags_t;", header, re.S)
        self.assertIsNotNone(flags)
        constants += "\n" + flags[0]
        code = "#include <stdint.h>\n#include <assert.h>\n" + constants + "\n"
        code += function(source, "static int sig_decode_user_flags(")
        code += r'''
int main(void) {
    uint32_t decoded;
    const uint32_t external[4] = {0x10000000U,0x40000000U,0x80000000U,0x04000000U};
    const uint32_t internal[4] = {VOS3_SA_RESTART,VOS3_SA_NODEFER,VOS3_SA_RESETHAND,VOS3_SA_RESTORER};
    for (unsigned bits=0; bits<16; bits++) {
        uint32_t flags=0,want=0;
        for(unsigned n=0;n<4;n++) if(bits&(1U<<n)){flags|=external[n];want|=internal[n];}
        decoded=0xdeadbeefU;
        assert(sig_decode_user_flags(flags,&decoded)==0 && decoded==want);
        decoded=0xdeadbeefU;
        assert(sig_decode_user_flags((uint64_t)(int64_t)(int32_t)flags,&decoded)==0 && decoded==want);
    }
    for(unsigned bit=0;bit<64;bit++) {
        if(bit==26||bit==28||bit==30||bit==31) continue;
        decoded=0xdeadbeefU;
        assert(sig_decode_user_flags(UINT64_C(1)<<bit,&decoded)==-22);
        assert(decoded==0xdeadbeefU);
    }
    decoded=0xdeadbeefU;
    assert(sig_decode_user_flags(UINT64_C(0xffffffff10000000),&decoded)==-22);
    assert(sig_decode_user_flags(UINT64_C(0xfffffffe80000000),&decoded)==-22);
    assert(sig_decode_user_flags(UINT64_C(0xffffffff80000004),&decoded)==-22);
    assert(decoded==0xdeadbeefU);
    return 0;
}
'''
        with tempfile.TemporaryDirectory(prefix="vos-signal-abi-") as directory:
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
