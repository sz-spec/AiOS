#!/usr/bin/env python3
"""Observe native ISO boot; require actual user-space output, not task creation."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def classify_serial(raw, exit_status):
    """Do not confuse kernel progress messages or a reboot with a live wizard."""
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw)
    failures = [marker for marker in ('PANIC', 'SIGSEGV', 'SetupWizard: Failed to exec',
                                      'uaccess address-space mismatch')
                if marker in clean]
    wizard_output = 'The AI-Native Operating System' in clean
    return dict(scheduler='Starting scheduler' in clean,
                wizard_output=wizard_output, failures=failures,
                passed=(exit_status == 'observation_timeout' and wizard_output
                        and not failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cpu', default='qemu64')
    parser.add_argument('--smp', type=int, default=1)
    parser.add_argument('--seconds', type=int, default=25)
    parser.add_argument('--firmware-code', type=Path)
    parser.add_argument('--firmware-vars', type=Path)
    args = parser.parse_args()
    if bool(args.firmware_code) != bool(args.firmware_vars):
        parser.error('UEFI requires both firmware code and a variables template')
    if args.seconds <= 0 or args.smp <= 0:
        parser.error('seconds and smp must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    cmd = ['qemu-system-x86_64', '-machine', 'q35,accel=tcg',
           '-cpu', args.cpu, '-smp', str(args.smp), '-m', '1024',
           '-display', 'none', '-serial', 'stdio', '-monitor', 'none',
           '-nic', 'none', '-no-reboot', '-cdrom', str(args.iso.resolve())]
    if args.firmware_code:
        variables = args.output.resolve() / 'vars.fd'
        shutil.copyfile(args.firmware_vars, variables)
        cmd += ['-drive', 'if=pflash,format=raw,readonly=on,file=' + str(args.firmware_code.resolve()),
                '-drive', 'if=pflash,format=raw,file=' + str(variables)]
    with (args.output / 'serial.log').open('wb') as log:
        try:
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=args.seconds)
            exit_status = proc.returncode
        except subprocess.TimeoutExpired:
            exit_status = 'observation_timeout'
    raw = (args.output / 'serial.log').read_text(errors='replace')
    result = dict(firmware='uefi' if args.firmware_code else 'bios',
                  cpu=args.cpu, smp=args.smp, ram_mb=1024, acceleration='tcg',
                  iso_sha256=hashlib.sha256(args.iso.read_bytes()).hexdigest(),
                  observation_seconds=args.seconds, exit_status=exit_status,
                  **classify_serial(raw, exit_status),
                  scope='VM boot to user-space setup; not installation, AI inference, or physical hardware qualification')
    online = re.findall(r'(?:Initialization complete:|SMP:)\s*(\d+) CPUs online', raw)
    result['online_cpus'] = int(online[-1]) if online else None
    if args.smp > 1 and result['online_cpus'] != args.smp:
        result['passed'] = False
        result['failures'].append('requested CPUs did not all report online')
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
