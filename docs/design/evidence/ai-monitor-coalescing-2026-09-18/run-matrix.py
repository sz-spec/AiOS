import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/Users/sz/Desktop/vos/vos 5')
OUT = Path('/private/tmp/vos-monitor-coalescing-fedcfc8-matrix')
MODE = sys.argv[1]
assert MODE in ('bios1', 'uefi1', 'bios4', 'uefi4')
RUN = OUT / MODE
RUN.mkdir(parents=True, exist_ok=False)
ISO = ROOT / 'dist/vos5.iso'
QEMU = Path(shutil.which('qemu-system-x86_64')).resolve()
CODE = Path('/opt/homebrew/Cellar/qemu/10.2.2/share/qemu/edk2-x86_64-code.fd')
VARS = Path('/opt/homebrew/Cellar/qemu/10.2.2/share/qemu/edk2-i386-vars.fd')

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def command(argv):
    try:
        r = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=10)
        return {'argv': argv, 'status': r.returncode, 'output': r.stdout}
    except Exception as e:
        return {'argv': argv, 'unavailable': str(e)}

def snapshot(stage):
    data = {'time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'iso_sha256': digest(ISO), 'qemu_binary': str(QEMU),
            'qemu_sha256': digest(QEMU),
            'checks': [command(c) for c in [
                [str(QEMU), '--version'], ['uptime'],
                ['ps', '-axo', 'pid,ppid,etime,pcpu,comm'],
                ['pmset', '-g', 'batt'], ['pmset', '-g', 'therm'],
                ['pmset', '-g', 'custom'], ['sw_vers'],
                ['sysctl', 'machdep.cpu.brand_string', 'hw.ncpu'],
            ]]}
    (RUN / f'host-{stage}.json').write_text(json.dumps(data, indent=2) + '\n')

cpus = MODE[-1]
obs = RUN / 'observation'
argv = [sys.executable, 'scripts/native_boot_smoke.py', '--iso', str(ISO),
        '--output', str(obs), '--smp', cpus, '--seconds', '60']
qemu_argv = ['qemu-system-x86_64', '-machine', 'q35,accel=tcg', '-cpu', 'qemu64',
             '-smp', cpus, '-m', '1024', '-display', 'none', '-serial', 'stdio',
             '-monitor', 'none', '-nic', 'none', '-no-reboot', '-cdrom', str(ISO)]
if MODE.startswith('uefi'):
    argv += ['--firmware-code', str(CODE), '--firmware-vars', str(VARS)]
    qemu_argv += ['-drive', f'if=pflash,format=raw,readonly=on,file={CODE}',
                  '-drive', f'if=pflash,format=raw,file={obs}/vars.fd']
metadata = {'source_commit': command(['git', 'rev-parse', 'HEAD'])['output'].strip(),
            'harness_argv': argv, 'qemu_argv': qemu_argv,
            'harness_sha256': digest(ROOT / 'scripts/native_boot_smoke.py'),
            'firmware_code_sha256': digest(CODE),
            'firmware_vars_template_sha256': digest(VARS),
            'scope': 'Boot observation only; not a performance or isolation gate',
            'expectation': 'PMM, VMM, scheduler, requested CPUs online, actual Setup Wizard user output, no classified failure; ISO unchanged'}
(RUN / 'invocation.json').write_text(json.dumps(metadata, indent=2) + '\n')
snapshot('before')
with (RUN / 'harness.log').open('wb') as f:
    result = subprocess.run(argv, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
snapshot('after')
print((RUN / 'harness.log').read_text())
raise SystemExit(result.returncode)
