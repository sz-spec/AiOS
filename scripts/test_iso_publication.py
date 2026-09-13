"""Exercise the real ISO wrapper with controlled tools and copy failures."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class IsoPublicationTests(unittest.TestCase):
    def test_failed_copy_preserves_previous_iso_and_success_replaces_it(self):
        with tempfile.TemporaryDirectory(prefix='vos-iso-publication-') as directory:
            root = Path(directory); tools = root/'tools'; tools.mkdir()
            boot = root/'limine/bin'; boot.mkdir(parents=True)
            for name in ['limine-bios.sys','limine-bios-cd.bin','limine-uefi-cd.bin','BOOTX64.EFI','BOOTIA32.EFI']:
                (boot/name).write_text(name)
            def executable(path, content):
                path.write_text('#!/bin/bash\nset -eu\n' + content); path.chmod(0o755)
            executable(boot/'limine', 'exit 0\n')
            executable(tools/'xorriso', '''if [[ "$1" == -indev ]]; then
 echo 'El Torito boot img BIOS'
 [[ "${MISSING_UEFI:-0}" == 1 ]] || echo 'El Torito boot img UEFI'
 exit 0
fi
while (( $# )); do
 if [[ "$1" == -o ]]; then printf 'verified-image' > "$2"; exit 0; fi
 shift
done
exit 1
''')
            executable(tools/'cp', '''if [[ "${FAIL_PUBLICATION:-0}" == 1 && "$2" == *.publish.*/image.iso ]]; then
 printf 'partial' > "$2"
 exit 73
fi
exec /bin/cp "$@"
''')
            kernel = root/'kernel.elf'; kernel.write_text('kernel')
            iso = root/'result.iso'; iso.write_text('previous-good-image')
            env = {**os.environ, 'PATH':str(tools)+os.pathsep+os.environ['PATH'],
                   'KERNEL_ELF':str(kernel), 'OUTPUT_ISO':str(iso), 'LIMINE_BUILD_DIR':str(boot.parent)}
            def run(**flags):
                return subprocess.run(['bash', str(ROOT/'infra/build_iso.sh')], env={**env, **flags}, capture_output=True, text=True, timeout=30)
            failed = run(FAIL_PUBLICATION='1')
            self.assertEqual(failed.returncode, 73, failed.stderr)
            self.assertEqual(iso.read_text(), 'previous-good-image')
            self.assertEqual(list(root.glob('*.publish.*')), [])
            missing = run(MISSING_UEFI='1')
            self.assertNotEqual(missing.returncode, 0)
            self.assertEqual(iso.read_text(), 'previous-good-image')
            success = run()
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertEqual(iso.read_text(), 'verified-image')
            self.assertIn('UEFI', Path(str(iso)+'.boot-catalog.txt').read_text())
            self.assertEqual(list(root.glob('*.publish.*')), [])

if __name__ == '__main__': unittest.main()
