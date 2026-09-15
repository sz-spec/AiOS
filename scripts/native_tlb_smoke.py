#!/usr/bin/env python3
"""Require the native warm/remap TLB probe plus actual SMP workload evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from native_boot_smoke import bind_artifact_identity
from native_isolation_smoke import require
from native_smp_smoke import classify_serial as classify_smp


def classify_serial(raw, exit_status, requested_cpus=1):
    result=classify_smp(raw,exit_status,requested_cpus)
    if not result['passed']:return result
    result['passed']=False
    clean=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',raw)
    try:
        records=[];topology={};parent=None
        for pos,line in enumerate(clean.splitlines()):
            if 'NATIVE_SMP topology ' in line:
                d=dict(t.split('=') for t in line.split('NATIVE_SMP topology ',1)[1].split())
                topology[int(d['cpu'])]=(pos,int(d['apic']))
            if 'NATIVE_SMP role=parent ' in line:parent=pos
            if 'NATIVE_TLB ' not in line:continue
            d={}
            for token in line.split('NATIVE_TLB ',1)[1].split():
                require(token.count('=')==1,'malformed TLB marker')
                key,value=token.split('=');require(key not in d,'duplicate TLB key');d[key]=value
            records.append((pos,d))
        require(len(records)==2*requested_cpus+1,'TLB record count mismatch')
        def one(**match):
            found=[r for r in records if all(r[1].get(k)==str(v) for k,v in match.items())]
            require(len(found)==1,'missing/duplicate TLB '+str(match));return found[0]
        positions={'warm':[],'remap':[]}
        for phase in positions:
            for cpu in range(requested_cpus):
                pos,d=one(phase=phase,cpu=cpu)
                require(set(d)=={'phase','cpu','apic','before0','before1','after0','after1'},'unexpected TLB fields')
                require(int(d['apic'])==topology[cpu][1],'TLB APIC mismatch')
                require(int(d['before0'])==17 and int(d['before1'])==34,'TLB pre-flush value mismatch')
                require(int(d['after0'])==17 and int(d['after1'])==(34 if phase=='warm' else 51),'TLB post-flush value mismatch')
                positions[phase].append(pos)
        end,d=one(complete=1,cpus=requested_cpus,rounds=2)
        require(set(d)=={'complete','cpus','rounds'},'unexpected TLB completion fields')
        require(max(t[0] for t in topology.values())<min(positions['warm']) and max(positions['warm'])<min(positions['remap']) and max(positions['remap'])<end<parent,'TLB phase ordering mismatch')
        result.update(passed=True,tlb_cpus_verified=requested_cpus,tlb_rounds=2,scope='observed two-page kernel TLB remap on each CPU plus SMP workload; no general shared-address-space proof')
    except (ValueError,KeyError,TypeError) as error:result['failures'].append(str(error))
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
