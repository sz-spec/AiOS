#!/usr/bin/env python3
"""Observe a gated adversarial user test; require correlated kernel fault evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from native_boot_smoke import bind_artifact_identity

CASES = ('foreign_read', 'foreign_write', 'kernel_read', 'kernel_write')
ADDRESS = 0x7000000000
PREFIX = 'NATIVE_ISOLATION '


def number(record, key):
    value = record[key]
    return int(value, 16 if value.startswith('0x') else 10)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def classify_serial(raw, exit_status, requested_cpus=1):
    clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw)
    result = {'passed': False, 'failures': [], 'cases_verified': 0}
    try:
        require(exit_status == 'observation_timeout', 'unexpected VM exit')
        for fatal in ('PANIC', 'uaccess address-space mismatch', 'Double Fault', 'General Protection Fault'):
            require(fatal not in clean, 'unexpected fatal condition: ' + fatal)
        for marker in ('PMM Statistics:', 'VMM: Initialization complete', 'Starting scheduler'):
            require(clean.count(marker) == 1, 'missing or repeated boot stage: ' + marker)
        online = re.findall(r'(?:Initialization complete:|SMP:)\s*(\d+) CPUs online', clean)
        result['online_cpus'] = int(online[-1]) if online else None
        require(result['online_cpus'] == requested_cpus, 'requested CPU count not observed')
        records = []
        for pos, line in enumerate(clean.splitlines()):
            if PREFIX not in line:
                continue
            parts = line.split(PREFIX, 1)[1].split()
            kind = parts.pop(0) if parts and '=' not in parts[0] else 'user'
            require(kind in ('user', 'kernel_target', 'victim_mapping', 'fault'), 'unknown/failure marker kind')
            values = {}
            for part in parts:
                require(part.count('=') == 1, 'malformed marker token')
                key, value = part.split('=')
                require(key not in values, 'duplicate marker key')
                values[key] = value
            require('fail' not in values, 'user test reported failure')
            records.append((pos, kind, values))

        def one(kind='user', **match):
            found = [r for r in records if r[1] == kind and all(r[2].get(k) == str(v) for k, v in match.items())]
            require(len(found) == 1, 'missing/duplicate marker: ' + kind + ' ' + str(match))
            return found[0]

        target = one('kernel_target')
        kernel_address = number(target[2], 'addr')
        require(kernel_address >= 0xffff800000000000 and kernel_address <= 0xffffffffffffffff,
                'kernel target must be canonical higher-half')
        require(number(target[2], 'present') == 1 and number(target[2], 'user') == 0,
                'kernel target lacks supervisor mapping proof')
        parent = one(role='parent')
        victim = one(role='victim')
        parent_pid, victim_pid = number(parent[2], 'pid'), number(victim[2], 'pid')
        require(parent_pid > 0 and victim_pid > 0 and parent_pid != victim_pid, 'invalid principal PIDs')
        require(number(parent[2], 'cpl') == number(victim[2], 'cpl') == 3, 'parent/victim CPL must be 3')
        require(number(victim[2], 'address') == ADDRESS and number(victim[2], 'ready') == 1,
                'victim readiness/address mismatch')
        mapping = one('victim_mapping')
        require(number(mapping[2], 'pid') == victim_pid and number(mapping[2], 'addr') == ADDRESS,
                'victim mapping not bound to victim')
        roots = [number(mapping[2], key) for key in ('root', 'user_root', 'phys')]
        require(all(value > 0 and value % 4096 == 0 for value in roots), 'victim roots/physical page invalid')
        require(parent[0] < victim[0] and mapping[0] < victim[0], 'victim setup ordering invalid')
        segv = []
        for pos, line in enumerate(clean.splitlines()):
            if 'SIGSEGV' in line:
                match = re.search(r"SIGSEGV: task '[^']*' \(pid=(\d+)\) addr=(0x[0-9a-fA-F]+) RIP=(0x[0-9a-fA-F]+) err=(0x[0-9a-fA-F]+)", line)
                require(match is not None and line.count('SIGSEGV') == 1, 'unexpected/unparseable SIGSEGV')
                segv.append((pos, int(match[1]), int(match[2], 16), int(match[4], 16)))
        require(len(segv) == 4, 'exactly four actual SIGSEGV records required')
        require(len([r for r in records if r[1] == 'fault']) == 4, 'exactly four diagnostic faults required')
        pids = set()
        previous = victim[0]
        for index, case in enumerate(CASES, 1):
            attempt = one(case=case, attempt=1)
            wait = one(case=case, contained=1)
            ack = one(case=case, ack=1)
            pid = number(attempt[2], 'pid')
            require(pid > 0 and pid not in pids | {parent_pid, victim_pid}, 'child PID reused or not distinct')
            pids.add(pid)
            expected_address = ADDRESS if case.startswith('foreign') else kernel_address
            write = case.endswith('write')
            expected_error = 4 + (2 if write else 0) + (1 if case.startswith('kernel') else 0)
            require(number(attempt[2], 'address') == expected_address and number(attempt[2], 'cpl') == 3,
                    case + ': attempt address/CPL mismatch')
            require(attempt[2]['operation'] == ('write' if write else 'read'), case + ': operation mismatch')
            fault = one('fault', pid=pid)
            require(number(fault[2], 'addr') == expected_address, case + ': diagnostic address mismatch')
            fault_roots = [number(fault[2], key) for key in ('root', 'user_root')]
            require(all(value > 0 and value % 4096 == 0 for value in fault_roots), case + ': invalid roots')
            require(not set(fault_roots) & set(roots[:2]), case + ': child shares victim root')
            require(number(fault[2], 'present') == int(case.startswith('kernel')) and number(fault[2], 'user') == 0,
                    case + ': effective PTE proof mismatch')
            actual = [entry for entry in segv if entry[1] == pid]
            require(len(actual) == 1, case + ': missing/duplicate actual child fault')
            actual = actual[0]
            require(actual[2] == expected_address and actual[3] == expected_error,
                    case + ': actual page fault address/error mismatch')
            require(number(wait[2], 'pid') == pid and number(wait[2], 'wait_status') == 139 << 8,
                    case + ': wait status/PID mismatch')
            require(number(ack[2], 'victim_pid') == victim_pid and number(ack[2], 'canary') == 1
                    and number(ack[2], 'challenge') == index, case + ': victim canary/challenge mismatch')
            require(previous < attempt[0] < fault[0] < actual[0] < wait[0] < ack[0], case + ': event ordering invalid')
            previous = ack[0]
            result['cases_verified'] += 1
        complete = one(complete=1)
        progress = one(progress=1)
        require(number(complete[2], 'cases') == 4 and number(complete[2], 'parent_pid') == parent_pid
                and number(complete[2], 'victim_status') == 0, 'completion proof mismatch')
        require(number(progress[2], 'parent_pid') == parent_pid and previous < complete[0] < progress[0],
                'missing post-completion parent progress')
        require(len(records) == 22, 'unexpected extra test markers')
        result.update(passed=True, parent_pid=parent_pid, victim_pid=victim_pid,
                      scope='four observed ring3 fault-containment cases; not universal process isolation')
    except (ValueError, KeyError, TypeError) as error:
        result['failures'].append(str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cpu', default='qemu64')
    parser.add_argument('--smp', type=int, default=1)
    parser.add_argument('--seconds', type=int, default=35)
    parser.add_argument('--firmware-code', type=Path)
    parser.add_argument('--firmware-vars', type=Path)
    args = parser.parse_args()
    if args.seconds <= 0 or args.smp <= 0:
        parser.error('seconds and smp must be positive')
    if bool(args.firmware_code) != bool(args.firmware_vars):
        parser.error('UEFI requires code and variables template')
    args.output.mkdir(parents=True, exist_ok=False)
    before = hashlib.sha256(args.iso.read_bytes()).hexdigest()
    command = ['qemu-system-x86_64', '-machine', 'q35,accel=tcg', '-cpu', args.cpu,
               '-smp', str(args.smp), '-m', '1024', '-display', 'none', '-serial', 'stdio',
               '-monitor', 'none', '-nic', 'none', '-no-reboot', '-cdrom', str(args.iso.resolve())]
    if args.firmware_code:
        variables = args.output.resolve() / 'vars.fd'
        shutil.copyfile(args.firmware_vars, variables)
        command += ['-drive', 'if=pflash,format=raw,readonly=on,file=' + str(args.firmware_code.resolve()),
                    '-drive', 'if=pflash,format=raw,file=' + str(variables)]
    with (args.output / 'serial.log').open('wb') as stream:
        try:
            process = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=stream,
                                     stderr=subprocess.STDOUT, timeout=args.seconds)
            status = process.returncode
        except subprocess.TimeoutExpired:
            status = 'observation_timeout'
    result = classify_serial((args.output / 'serial.log').read_text(errors='replace'), status, args.smp)
    bind_artifact_identity(result, before, hashlib.sha256(args.iso.read_bytes()).hexdigest())
    result.update(firmware='uefi' if args.firmware_code else 'bios', cpu=args.cpu,
                  smp=args.smp, observation_seconds=args.seconds, exit_status=status)
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
