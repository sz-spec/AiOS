"""Compile real user programs in a disposable source copy; no donor edits."""
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class NativeBuildConfigTests(unittest.TestCase):
    def test_flags_headers_and_library_changes_reach_binary(self):
        with tempfile.TemporaryDirectory(prefix='vos-build-test-') as directory:
            root=Path(directory); user=root/'user';user.mkdir()
            for name in ['Makefile','programs.mk']:shutil.copyfile(ROOT/'user'/name,user/name)
            for name in ['src','lib','include']:shutil.copytree(ROOT/'user'/name,user/name,ignore=shutil.ignore_patterns('*.o','*.d','*.a'))
            (root/'kernel/tools').mkdir(parents=True)
            shutil.copyfile(ROOT/'kernel/tools/build_config.py',root/'kernel/tools/build_config.py')
            binary=user/'build/bin/init'
            def build(*args):
                result=subprocess.run(['make','build/bin/init',*args],cwd=user,capture_output=True,text=True,timeout=90)
                self.assertEqual(result.returncode,0,result.stdout[-2000:]+result.stderr[-2000:])
            def sha():return hashlib.sha256(binary.read_bytes()).hexdigest()
            build(); normal=sha();mtime=binary.stat().st_mtime_ns
            build();self.assertEqual(binary.stat().st_mtime_ns,mtime,'no-op rebuilt init')
            build('BENCH_MODE=1','TEST_ONLY=vos_config_probe')
            self.assertNotEqual(sha(),normal)
            self.assertIn(b'vos_config_probe',binary.read_bytes())
            build('BENCH_MODE=0');self.assertEqual(sha(),normal,'zero must disable benchmark mode')
            config=json.loads((user/'build/build-config.json').read_text())
            self.assertNotIn('HEADLESS_BENCH_SUITE',config['VOS_USER_CFLAGS'])
            before=binary.stat().st_mtime_ns;time.sleep(1.05)
            header=user/'include/stdio.h';header.write_text(header.read_text()+'\n/* dependency probe */\n')
            build();self.assertNotEqual(binary.stat().st_mtime_ns,before,'header did not relink program')
            archive=user/'build/libc.a';before=archive.stat().st_mtime_ns;time.sleep(1.05)
            library=user/'lib/string.c';library.write_text(library.read_text()+'\n/* library dependency probe */\n')
            build();self.assertNotEqual(archive.stat().st_mtime_ns,before)
            before=binary.stat().st_mtime_ns;build();self.assertEqual(binary.stat().st_mtime_ns,before)

    def test_crt_only_change_relinks_musl_program(self):
        with tempfile.TemporaryDirectory(prefix='vos-musl-build-test-') as directory:
            root = Path(directory)
            user = root / 'user'
            shutil.copytree(ROOT / 'user', user, ignore=shutil.ignore_patterns('build', 'obj', '*.o', '*.d', '*.a', '*.so', '*.debug'))
            (root / 'kernel/tools').mkdir(parents=True)
            shutil.copyfile(ROOT / 'kernel/tools/build_config.py', root / 'kernel/tools/build_config.py')
            binary = user / 'build/bin/hello_musl'
            loader = user / 'build/bin/ld-musl-x86_64.so.1'
            stamp = user / 'build/musl-link-config.json'
            def build():
                result = subprocess.run(['make', '-j4', 'build/bin/hello_musl'], cwd=user,
                                        capture_output=True, text=True, timeout=240)
                self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
            build()
            old = (binary.read_bytes(), binary.stat().st_mtime_ns, loader.stat().st_mtime_ns, stamp.read_bytes())
            build()
            self.assertEqual(binary.stat().st_mtime_ns, old[1], 'no-op rebuilt musl program')
            time.sleep(1.05)
            crt = user / 'musl/crt/x86_64/crti.s'
            crt.write_text(crt.read_text() + '\n.section .init\n nop\n')
            build()
            self.assertEqual(loader.stat().st_mtime_ns, old[2], 'CRT-only change rebuilt shared loader')
            self.assertNotEqual(stamp.read_bytes(), old[3], 'CRT change missing from link stamp')
            self.assertNotEqual(binary.read_bytes(), old[0], 'CRT-only change did not reach executable')
            final = binary.stat().st_mtime_ns
            build()
            self.assertEqual(binary.stat().st_mtime_ns, final, 'post-change no-op relinked executable')

    def test_iso_tracks_packaging_inputs_and_build_flavor(self):
        # Exercise the actual packaging rules with a deterministic lightweight
        # writer; this tests dependency selection, not the ISO format itself.
        with tempfile.TemporaryDirectory(prefix='vos-iso-input-test-') as directory:
            root = Path(directory); kernel = root / 'kernel'
            (kernel / 'tools').mkdir(parents=True); (root / 'infra').mkdir()
            shutil.copyfile(ROOT / 'kernel/tools/build_config.py', kernel / 'tools/build_config.py')
            source = (ROOT / 'kernel/Makefile').read_text()
            section = source[source.index('ISO           :='):source.index('# QEMU\n', source.index('ISO           :='))]
            (kernel / 'Makefile').write_text('all: iso\nBUILD_DIR ?= build/a\nKERNEL := vos3.elf\n' + section)
            for flavor in ['a', 'b']:
                path = kernel / 'build' / flavor; path.mkdir(parents=True)
                (path / 'vos3.elf').write_text(flavor)
            boot = kernel / 'build/limine/bin'; boot.mkdir(parents=True)
            for name in ['limine','limine-bios.sys','limine-bios-cd.bin','limine-uefi-cd.bin','BOOTX64.EFI','BOOTIA32.EFI']:
                (boot / name).write_text(name)
            writer = root / 'infra/build_iso.sh'
            writer.write_text('set -eu\nmkdir -p "$(dirname "$OUTPUT_ISO")"\ncat "$KERNEL_ELF" > "$OUTPUT_ISO"\n')
            image = root / 'dist/vos5.iso'
            def build(flavor='a'):
                result = subprocess.run(['make', 'iso', 'BUILD_DIR=build/' + flavor], cwd=kernel,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            build(); before = image.stat().st_mtime_ns
            build(); self.assertEqual(image.stat().st_mtime_ns, before)
            (boot / 'BOOTX64.EFI').write_text('updated firmware loader')
            build(); self.assertNotEqual(image.stat().st_mtime_ns, before)
            before = image.stat().st_mtime_ns
            writer.write_text(writer.read_text() + '# packaging revision\n')
            build(); self.assertNotEqual(image.stat().st_mtime_ns, before)
            build('b'); self.assertEqual(image.read_text(), 'b')
            build('a'); self.assertEqual(image.read_text(), 'a', 'older flavor left wrong shared ISO')
            before = image.stat().st_mtime_ns
            build(); self.assertEqual(image.stat().st_mtime_ns, before)

if __name__=='__main__':unittest.main()
