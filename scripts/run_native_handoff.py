#!/usr/bin/env python3
"""Run the production context.S in a freestanding functional x86 guest.

The guest is an ISA/stack oracle, not the complete vOS kernel. It produces
no performance result. Every invocation requires a fresh output directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'scripts/native_handoff'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--limine-dir', type=Path, default=ROOT / 'kernel/build/limine/bin')
    parser.add_argument('--context-source', type=Path,
                        default=ROOT / 'kernel/src/sched/context.S',
                        help='Override only for a separately labelled mutation/diagnostic run')
    parser.add_argument('--seconds', type=int, default=30)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    record = {'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'scope': 'Functional real-stack assembly oracle; not vOS boot or performance acceptance',
              'context_source': str(args.context_source.resolve()),
              'context_sha256': sha(args.context_source),
              'diagnostic_override': args.context_source.resolve() != ROOT / 'kernel/src/sched/context.S',
              'commands': [], 'passed': False}

    def run(argv, name, timeout=90):
        record['commands'].append({'argv': [str(a) for a in argv], 'log': name})
        with (out / name).open('wb') as log:
            try:
                result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=timeout)
                record['commands'][-1]['exit_status'] = result.returncode
                return result.returncode
            except subprocess.TimeoutExpired:
                record['commands'][-1]['exit_status'] = 'timeout'
                raise

    def build(argv, name):
        if run(argv, name) != 0:
            raise RuntimeError('Build failed; see ' + str(out / name))

    try:
        flags = ['-ffreestanding', '-fno-pie', '-fno-pic', '-mcmodel=kernel',
                 '-mno-red-zone', '-mno-sse', '-mno-sse2', '-fno-stack-protector',
                 '-O2', '-Wall', '-Wextra', '-Werror']
        record['source_sha256'] = {str(p.relative_to(ROOT)): sha(p)
                                   for p in sorted(FIXTURES.iterdir()) if p.is_file()}
        for index, (source, obj) in enumerate([
            (FIXTURES / 'harness.c', 'harness.o'),
            (FIXTURES / 'boot64.S', 'boot.o'),
            (args.context_source, 'context.o'),
        ]):
            build(['x86_64-elf-gcc', *flags, '-c', str(source), '-o', str(out / obj)],
                  f'compile-{index}.log')
        build(['x86_64-elf-ld', '-nostdlib', '-z', 'max-page-size=0x1000',
               '-T', str(FIXTURES / 'link.ld'), '-o', str(out / 'handoff.elf'),
               *[str(out / obj) for obj in ('boot.o', 'harness.o', 'context.o')]], 'link.log')
        build(['x86_64-elf-objdump', '-dr', str(out / 'context.o')], 'context.disasm')
        iso_root = out / 'iso-root'
        boot = iso_root / 'boot/limine'
        efi = iso_root / 'EFI/BOOT'
        boot.mkdir(parents=True)
        efi.mkdir(parents=True)
        shutil.copyfile(out / 'handoff.elf', iso_root / 'boot/handoff.elf')
        record['limine_sha256'] = {}
        for name in ('limine-bios.sys', 'limine-bios-cd.bin', 'limine-uefi-cd.bin',
                     'BOOTX64.EFI', 'BOOTIA32.EFI'):
            source = args.limine_dir / name
            record['limine_sha256'][name] = sha(source)
            shutil.copyfile(source, (efi if name.endswith('.EFI') else boot) / name)
        (boot / 'limine.conf').write_text(
            'timeout: 0\nserial: yes\n/handoff\n    protocol: limine\n'
            '    kernel_path: boot():/boot/handoff.elf\n')
        build(['xorriso', '-as', 'mkisofs', '-R', '-r', '-J',
               '-b', 'boot/limine/limine-bios-cd.bin', '-no-emul-boot',
               '-boot-load-size', '4', '-boot-info-table', '-hfsplus',
               '-apm-block-size', '2048', '--efi-boot', 'boot/limine/limine-uefi-cd.bin',
               '-efi-boot-part', '--efi-boot-image', '--protective-msdos-label',
               str(iso_root), '-o', str(out / 'handoff.iso')], 'iso.log')
        build([str(args.limine_dir / 'limine'), 'bios-install', str(out / 'handoff.iso')],
              'limine-install.log')
        qemu = Path(shutil.which('qemu-system-x86_64')).resolve()
        record['qemu_binary'] = str(qemu)
        record['qemu_sha256'] = sha(qemu)
        run([str(qemu), '--version'], 'qemu-version.log')
        record['elf_sha256'] = sha(out / 'handoff.elf')
        record['iso_sha256'] = sha(out / 'handoff.iso')
        status = run([str(qemu), '-machine', 'q35,accel=tcg', '-cpu', 'qemu64',
                      '-m', '64', '-smp', '1', '-display', 'none',
                      '-serial', f'file:{out}/serial.log', '-monitor', 'none',
                      '-no-reboot', '-nic', 'none', '-device',
                      'isa-debug-exit,iobase=0xf4,iosize=0x04', '-cdrom',
                      str(out / 'handoff.iso')], 'qemu.log', args.seconds)
        raw = (out / 'serial.log').read_bytes()
        record['serial_sha256'] = hashlib.sha256(raw).hexdigest()
        record['context_unchanged'] = sha(args.context_source) == record['context_sha256']
        record['iso_unchanged'] = sha(out / 'handoff.iso') == record['iso_sha256']
        record['passed'] = (status == 33 and record['context_unchanged']
                            and record['iso_unchanged']
                            and b'PASS context.S real-stack handoff ack' in raw
                            and b'FAIL context.S' not in raw)
    except Exception as error:
        record['error'] = f'{type(error).__name__}: {error}'
    finally:
        (out / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps({'output': str(out), 'passed': record['passed'],
                          'error': record.get('error')}, indent=2))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
