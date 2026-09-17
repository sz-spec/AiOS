#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=500)
    args = parser.parse_args()
    source = args.source.resolve()
    iso = args.iso.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)

    classifier_path = source / 'scripts/native_bench.py'
    spec = importlib.util.spec_from_file_location('frozen_native_bench', classifier_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    firmware = Path('/opt/homebrew/share/qemu/edk2-x86_64-code.fd')
    vars_template = Path('/opt/homebrew/share/qemu/edk2-i386-vars.fd')
    vars_copy = output / 'vars.fd'
    shutil.copyfile(vars_template, vars_copy)
    expected = module.workload((source / 'user/src/init.c').read_text())
    iso_before = sha(iso)
    init_before = sha(source / 'user/src/init.c')
    vars_before = sha(vars_copy)
    cmd = [
        'qemu-system-x86_64', '-machine', 'q35,accel=tcg', '-m', '3072',
        '-cpu', 'qemu64,+rdrand,+rdseed', '-smp', '1', '-display', 'none',
        '-serial', 'stdio', '-monitor', 'none', '-no-reboot', '-boot', 'd',
        '-nic', 'user,model=virtio-net-pci', '-cdrom', str(iso), '-snapshot',
        '-drive', f'if=pflash,format=raw,readonly=on,file={firmware}',
        '-drive', f'if=pflash,format=raw,file={vars_copy}',
    ]
    invocation = {
        'command': cmd, 'iso': str(iso), 'iso_sha256': iso_before,
        'firmware_sha256': sha(firmware),
        'vars_template_sha256': sha(vars_template),
        'vars_copy_sha256_before': vars_before,
        'init_source_sha256': init_before,
        'classifier_sha256': sha(classifier_path),
        'expected_programs': expected,
        'firmware': 'OVMF/EDK2 UEFI; Secure Boot not qualified',
        'disk': 'No disk attached', 'seconds': args.seconds,
    }
    (output / 'invocation.json').write_text(json.dumps(invocation, indent=2) + '\n')
    started = time.monotonic()
    status = module.observe(cmd, output / 'serial.log', args.seconds)
    elapsed = time.monotonic() - started
    result = module.classify_serial(
        (output / 'serial.log').read_text(errors='replace'), expected, status)
    result.update({
        'command': cmd, 'iso': str(iso), 'iso_sha256': iso_before,
        'iso_sha256_after': sha(iso), 'exit_status': status,
        'elapsed_seconds': elapsed, 'init_source_sha256': init_before,
        'init_source_sha256_after': sha(source / 'user/src/init.c'),
        'classifier_sha256': sha(classifier_path),
        'firmware_sha256': sha(firmware),
        'vars_template_sha256': sha(vars_template),
        'vars_copy_sha256_before': vars_before,
        'vars_copy_sha256_after': sha(vars_copy),
        'firmware': 'OVMF/EDK2 UEFI; Secure Boot not qualified',
        'disk': 'No disk attached',
    })
    if result['iso_sha256_after'] != iso_before:
        result['passed'] = False
        result['failures'].append('ISO changed during qualification')
    if result['init_source_sha256_after'] != init_before:
        result['passed'] = False
        result['failures'].append('Workload source changed during qualification')
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
