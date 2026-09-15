#!/usr/bin/env python3
"""Require deterministic user checkpoints bound to kernel APIC topology."""
from functools import lru_cache
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from native_boot_smoke import bind_artifact_identity
from native_isolation_smoke import require
STEPS=200000
ROUNDS=8

@lru_cache(maxsize=2)
def expected_values(worker):
    value=worker+1
    out=[]
    for _ in range(ROUNDS):
        for _ in range(STEPS):value=(value*1664525+1013904223)&0xffffffff
        out.append(value)
    return out

def classify_serial(raw,exit_status,requested_cpus=1):
    clean=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',raw)
    result={'passed':False,'failures':[]}
    try:
        require(exit_status=='observation_timeout','unexpected VM exit')
        require(1<=requested_cpus<=4,'legacy APIC oracle supports 1..4 CPUs')
        for text in ('PANIC','SIGSEGV','General Protection Fault','Double Fault','NATIVE_SMP FAIL','uaccess address-space mismatch'):
            require(text not in clean,'unexpected failure '+text)
        for text in ('PMM Statistics:','VMM: Initialization complete','Starting scheduler'):
            require(clean.count(text)==1,'missing/repeated boot stage')
        counts=re.findall(r'(?:Initialization complete:|SMP:)\s*(\d+) CPUs online',clean)
        require(bool(counts) and int(counts[-1])==requested_cpus,'online count mismatch')
        records=[]
        for pos,line in enumerate(clean.splitlines()):
            if 'NATIVE_SMP ' not in line:continue
            tokens=line.split('NATIVE_SMP ',1)[1].split();kind='user'
            if tokens and '=' not in tokens[0]:kind=tokens.pop(0)
            require(kind in ('user','topology','schedule'),'unknown marker')
            d={}
            for t in tokens:
                require(t.count('=')==1,'malformed marker');k,v=t.split('=');require(k not in d,'duplicate key');d[k]=v
            records.append((pos,kind,d))
        def one(kind='user',**match):
            matches=[r for r in records if r[1]==kind and all(r[2].get(k)==str(v) for k,v in match.items())]
            require(len(matches)==1,'missing/duplicate '+str(match));return matches[0]
        topology=[one('topology',cpu=i,online=1) for i in range(requested_cpus)]
        ids={int(t[2]['apic']) for t in topology}
        require(len(ids)==requested_cpus and all(0<=i<256 for i in ids),'ambiguous APIC topology')
        parent=one(role='parent');pid=int(parent[2]['pid']);require(pid>0 and parent[2]['cpl']=='3' and int(parent[2]['apic']) in ids,'parent identity')
        require(all(t[0]<parent[0] for t in topology),'topology must precede workload')
        schedules=[r for r in records if r[1]=='schedule']
        mapping={int(t[2]['cpu']):int(t[2]['apic']) for t in topology}
        for r in schedules:
            require(int(r[2]['pid'])>0 and mapping.get(int(r[2]['cpu']))==int(r[2]['apic']),'schedule topology mismatch')
        seen=set();children=set();last=[]
        for worker in range(2):
            previous=parent[0];child=None
            for checkpoint,value in enumerate(expected_values(worker),1):
                r=one(worker=worker,checkpoint=checkpoint);d=r[2];current=int(d['pid'])
                if child is None:child=current;require(child>0 and child!=pid and child not in children,'worker identity');children.add(child)
                require(current==child and d['cpl']=='3' and int(d['steps'])==STEPS and int(d['value'])==value,'checkpoint identity/value mismatch')
                apic=int(d['apic']);require(apic in ids,'unknown hardware APIC');seen.add(apic)
                require(any(s[0]<r[0] and int(s[2]['pid'])==child and int(s[2]['apic'])==apic for s in schedules),'missing kernel schedule correlation')
                require(previous<r[0],'checkpoint order');previous=r[0]
            wait=one(worker=worker,wait_status=0,parent_pid=pid)
            require(int(wait[2]['pid'])==child and previous<wait[0],'worker completion mismatch');last.append(wait[0])
        complete=one(complete=1,workers=2,checkpoints=16,parent_pid=pid)
        progress=one(progress=1,parent_pid=pid,cpl=3)
        require(int(progress[2]['apic']) in ids and max(last)<complete[0]<progress[0],'parent final progress')
        require(len(records)-len(schedules)==requested_cpus+21,'unexpected marker count')
        if requested_cpus>1:require(len(seen)>=2,'workers did not demonstrate multiple hardware CPUs')
        result.update(passed=True,worker_apic_ids=sorted(seen),online_cpus=requested_cpus,scope='actual user execution across CPU identities; no simultaneous execution/fairness proof' if requested_cpus>1 else 'single-CPU deterministic workload control')
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
