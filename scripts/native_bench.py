#!/usr/bin/env python3
"""Build and observe the complete native BENCH_MODE suite through a Limine ISO."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
COMPLETE = 'WEEK 2 BENCHMARK SUITE: COMPLETE'
HALT = 'Init: All benchmarks executed. System halting.'


def workload(source, test_only=''):
    section = source.split('/* Run each benchmark */', 1)[1].split('#undef RUN_BENCH', 1)[0]
    enabled = True
    names = []
    for line in section.splitlines():
        if line.strip() == '#ifdef VOS3_TEST_ONLY':
            enabled = bool(test_only)
        elif line.strip() == '#endif':
            enabled = True
        elif line.lstrip().startswith('#'):
            raise ValueError('Unrecognized benchmark workload conditional: ' + line)
        match = re.search(r'RUN_BENCH\("([a-z0-9_]+)"\s*,', line)
        if match and enabled and (not test_only or test_only in match[1]):
            names.append(match[1])
    if not names or len(names) != len(set(names)):
        raise ValueError('Empty or duplicate benchmark selection')
    return names


def classify_serial(raw, expected, exit_status, test_only=''):
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw)
    failures = []
    running = re.findall(r'^=== Running: ([a-z0-9_]+) ===\s*$', clean, re.M)
    exits = re.findall(r'^=== ([a-z0-9_]+): exit=(-?\d+) ===\s*$', clean, re.M)
    if len(re.findall(r'^=== Running:', clean, re.M)) != len(running) or len(re.findall(r'^=== .*: exit=', clean, re.M)) != len(exits):
        failures.append('Malformed benchmark start/exit record')
    if running != expected:
        failures.append('Started benchmark sequence differs from selected full workload')
    if exits != [(name, '0') for name in expected]:
        failures.append('Missing, nonzero, duplicated or reordered benchmark exit status')
    records = re.findall(r'^=== (?:Running: ([a-z0-9_]+)|([a-z0-9_]+): exit=-?\d+) ===\s*$', clean, re.M)
    if records != [pair for name in expected for pair in ((name, ''), ('', name))]:
        failures.append('Benchmark starts/exits do not alternate in order')
    if clean.count(COMPLETE) != 1 or clean.count(HALT) != 1:
        failures.append('Missing or duplicated suite completion/halting marker')
    elif not exits or clean.index(COMPLETE) < clean.rfind('=== ' + expected[-1] + ': exit=') or clean.index(HALT) < clean.index(COMPLETE):
        failures.append('Completion precedes final benchmark result')
    for pattern in (r'PANIC', r'\[FAIL\]', r'Init: ERROR', r'\[MISS\]',
                    r'\[ERROR\] Cannot open /bin', r'uaccess address-space mismatch',
                    r'\b[1-9][0-9]* FAIL\b'):
        if re.search(pattern, clean):
            failures.append('Failure diagnostic: ' + pattern)
    filters = re.findall(r'TEST FILTER: "([^"]*)"', clean)
    if filters != ([test_only] if test_only else []):
        failures.append('Observed filter differs from requested selection')
    if not test_only and '=== SKIP:' in clean:
        failures.append('Unexpected skipped benchmark in full suite')
    if exit_status != 'suite_observed':
        failures.append('QEMU exited or timed out before bounded post-completion observation')
    return dict(passed=not failures, failures=failures, expected_programs=expected,
                started_programs=running, exits=exits, partial=bool(test_only),
                scope='Selected benchmark subset' if test_only else 'Complete BENCH_MODE program sequence')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observe(cmd, log_path, seconds):
    """Own a direct Popen child; never read a shared PID file or signal other VMs."""
    status = 'timeout'
    with log_path.open('wb') as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + seconds
        completed_at = None
        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    return proc.returncode
                raw = log_path.read_text(errors='replace')
                if HALT in raw and completed_at is None:
                    completed_at = time.monotonic()
                if completed_at is not None and time.monotonic() - completed_at >= 2:
                    status = 'suite_observed'
                    break
                time.sleep(0.1)
        finally:
            stopped = proc.poll()
            if stopped is not None:
                status = stopped
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, help='Observe an already-built BENCH_MODE ISO; otherwise build it')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seconds', type=int, default=int(os.environ.get('TIMEOUT', '500')))
    parser.add_argument('--test-only', default=os.environ.get('TEST_ONLY', ''))
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--disk', type=Path, help='Optional virtio disk; QEMU snapshot protects its contents')
    args = parser.parse_args()
    if args.seconds <= 0 or args.jobs <= 0 or not re.fullmatch(r'[a-z0-9_]*', args.test_only):
        parser.error('positive seconds/jobs and alphanumeric underscore test filter required')
    expected = workload((ROOT / 'user/src/init.c').read_text(), args.test_only)
    output = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='vos-native-bench-'))
    if args.output:
        output.mkdir(parents=True, exist_ok=False)
    result = {'passed': False, 'failures': [], 'test_only': args.test_only, 'expected_programs': expected}
    print('Benchmark evidence: ' + str(output), flush=True)
    try:
        if not shutil.which('qemu-system-x86_64'):
            raise RuntimeError('Missing required qemu-system-x86_64')
        source_before = sha(ROOT / 'user/src/init.c')
        iso = args.iso.resolve() if args.iso else output / 'bench.iso'
        if not args.iso:
            build_dir = 'build/native-bench-' + uuid.uuid4().hex
            command = ['make', '-j' + str(args.jobs), 'native', 'BENCH_MODE=1', 'HEADLESS_AUDIT=1',
                       'BUILD_DIR=' + build_dir, 'INSTALLER_ISO=' + str(iso)]
            if args.test_only:
                command.append('TEST_ONLY=' + args.test_only)
            result['build_command'] = command
            with (output / 'build.log').open('wb') as log:
                subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800)
        before = sha(iso)
        cmd = ['qemu-system-x86_64', '-machine', 'q35,accel=tcg', '-m', '3072',
               '-cpu', 'qemu64,+rdrand,+rdseed', '-smp', '1', '-display', 'none',
               '-serial', 'stdio', '-monitor', 'none', '-no-reboot', '-boot', 'd',
               '-nic', 'user,model=virtio-net-pci', '-cdrom', str(iso), '-snapshot']
        disk = args.disk or (ROOT / 'kernel/disk.img')
        if disk.exists():
            cmd += ['-drive', 'file=' + str(disk.resolve()) + ',format=raw,if=none,id=disk0',
                    '-device', 'virtio-blk-pci,drive=disk0']
        elif args.disk:
            raise RuntimeError('Explicit disk does not exist')
        result.update(command=cmd, iso=str(iso), iso_sha256=before, init_source_sha256=source_before)
        status = observe(cmd, output / 'serial.log', args.seconds)
        result.update(classify_serial((output / 'serial.log').read_text(errors='replace'), expected, status, args.test_only))
        result.update(exit_status=status, iso_sha256_after=sha(iso))
        if before != result['iso_sha256_after'] or source_before != sha(ROOT / 'user/src/init.c'):
            result['passed'] = False
            result['failures'].append('ISO or workload source changed during qualification')
    except Exception as exc:
        result['passed'] = False
        result['failures'].append(type(exc).__name__ + ': ' + str(exc))
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
