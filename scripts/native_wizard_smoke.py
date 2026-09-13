#!/usr/bin/env python3
"""Drive a disposable diskless native setup VM through its emulated keyboard."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import socket
import subprocess
import tempfile
import time

STEPS = [
    ('Press [ENTER] to continue...', ['ret']),
    ('Enter your choice [1/2/3]:', ['1', 'ret']),
    ('Enter your choice [1/2]:', ['1', 'ret']),
    ('Enter your name:', ['v', 'o', 's', '5', 'ret']),
    ('Proceed with this configuration? [Y/n]:', ['y', 'ret']),
    ('Press [ENTER] to start VOS3...', ['ret']),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=35)
    parser.add_argument('--smp', type=int, default=1)
    args = parser.parse_args()
    if args.seconds <= 0 or args.smp <= 0:
        parser.error('seconds and smp must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    log = args.output / 'serial.log'
    index = offset = 0
    error = None
    with tempfile.TemporaryDirectory(prefix='vos5-qmp-') as temporary, log.open('wb') as stream:
        socket_path = str(Path(temporary) / 'qmp.sock')
        command = ['qemu-system-x86_64', '-machine', 'q35,accel=tcg',
                   '-cpu', 'qemu64', '-m', '1024', '-smp', str(args.smp),
                   '-display', 'none', '-serial', 'stdio', '-monitor', 'none',
                   '-nic', 'none', '-no-reboot',
                   '-qmp', 'unix:' + socket_path + ',server=on,wait=off',
                   '-cdrom', str(args.iso.resolve())]
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                                stderr=subprocess.STDOUT)
        start = time.monotonic()
        try:
            while not Path(socket_path).exists():
                if proc.poll() is not None or time.monotonic() - start > 5:
                    raise RuntimeError('QEMU control socket unavailable; inspect serial.log')
                time.sleep(.05)
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(2)
                connection.connect(socket_path)
                with connection.makefile('rwb', buffering=0) as control:
                    json.loads(control.readline())

                    def rpc(name, arguments=None):
                        packet = {'execute': name}
                        if arguments is not None:
                            packet['arguments'] = arguments
                        control.write((json.dumps(packet) + '\n').encode())
                        while True:
                            response = json.loads(control.readline())
                            if 'error' in response:
                                raise RuntimeError(str(response['error']))
                            if 'return' in response:
                                return response['return']

                    rpc('qmp_capabilities')
                    while time.monotonic() - start < args.seconds and proc.poll() is None:
                        text = log.read_text(errors='replace')
                        if any(s in text for s in ('PANIC', 'SIGSEGV', 'VOS3 Setup Complete.')):
                            break
                        if index < len(STEPS) and STEPS[index][0] in text[offset:]:
                            offset = text.index(STEPS[index][0], offset) + len(STEPS[index][0])
                            for key in STEPS[index][1]:
                                rpc('human-monitor-command', {'command-line': 'sendkey ' + key})
                                time.sleep(.12)
                            index += 1
                        time.sleep(.1)
        except (OSError, RuntimeError, ValueError) as exc:
            error = str(exc)
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    text = log.read_text(errors='replace')
    fault = any(s in text for s in ('PANIC', 'SIGSEGV', 'uaccess address-space mismatch'))
    complete = 'VOS3 Setup Complete.' in text
    online = re.findall(r'Initialization complete:\s*(\d+) CPUs online', text)
    online_cpus = int(online[-1]) if online else None
    cpu_coverage = args.smp == 1 or online_cpus == args.smp
    result = dict(scope='Ephemeral diskless VM setup; not persistent installation or physical hardware',
                  smp=args.smp,
                  online_cpus=online_cpus,
                  iso_sha256=hashlib.sha256(args.iso.read_bytes()).hexdigest(),
                  prompts_answered=index, setup_completed=complete, fault=fault,
                  error=error, passed=complete and index == len(STEPS) and not fault
                  and error is None and cpu_coverage)
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
