#!/usr/bin/env python3
"""Strict observer for the gated native memory transition workload."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from native_boot_smoke import bind_artifact_identity
from native_isolation_smoke import require
CASES = ('cow_private','cow_mprotect_rw','ro_before_fork','ro_after_fork','lazy_ro_write','none_lazy_read','none_populated_read','nx_execute','lazy_mprotect_ro','unmap_single','unmap_middle','unmap_multiple')
ERRORS = (None,None,7,7,6,4,5,21,6,4,4,4)
BASE = 0x7100000000


def classify_serial(raw, exit_status, requested_cpus=1):
    clean=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',raw)
    result={'passed':False,'failures':[],'cases_verified':0}
    try:
        require(exit_status=='observation_timeout','unexpected VM exit')
        for marker in (
            '[SHM-EXIT] PASS: unrelated identity retained, duplicate mark, IRQ-off drain deferred, two creator backings freed, mapped survivor, explicit-close no double release',
            '[SHM-IDENTITY] PASS: zero-cookie denied, same-TID different-cookie denied, owner restored, backing intact, final release',
            '[VM-FILE-REFS] PASS: checked retain, clone rollback, partial release, CPU pin, exactly-once close','PMM Statistics:','VMM: Initialization complete','Starting scheduler'):
            require(clean.count(marker)==1,'missing/repeated boot stage')
        require(not any(x in clean for x in ('PANIC','General Protection Fault','Double Fault','uaccess address-space mismatch','NATIVE_MEMORY FAIL','NATIVE_ISOLATION FAIL')),'unexpected failure')
        for marker in (
            '[PROCESS-ROOTS] PASS: create, clone, independent roots, allocation failures, owner refs, CPU pins, release, accounting',
            '[VM-BACKING] PASS: tracking cap, foreign unmap, unsupported SHM fork, surviving owner, final mapping cleanup, creator-first explicit/reap, duplicate creator close',
            '[VM-METADATA] PASS: distinct tags, real COW copy, parent integrity, mprotect, flag updates, final accounting',
        ):
            require(clean.count(marker)==1,'missing/duplicate native lifecycle marker: '+marker)
        counts=re.findall(r'(?:Initialization complete:|SMP:)\s*(\d+) CPUs online',clean)
        require(bool(counts) and int(counts[-1])==requested_cpus,'CPU count mismatch')
        records=[]; faults=[]
        for pos,line in enumerate(clean.splitlines()):
            if 'NATIVE_MEMORY ' in line:
                values={}
                for token in line.split('NATIVE_MEMORY ',1)[1].split():
                    require(token.count('=')==1,'malformed marker')
                    key,value=token.split('=');require(key not in values,'duplicate key');values[key]=value
                records.append((pos,values))
            if 'SIGSEGV' in line:
                m=re.search(r"SIGSEGV: task '[^']*' \(pid=(\d+)\) addr=(0x[0-9a-fA-F]+) RIP=(0x[0-9a-fA-F]+) err=(0x[0-9a-fA-F]+)",line)
                require(m is not None and line.count('SIGSEGV')==1,'unparseable fault')
                faults.append((pos,int(m[1]),int(m[2],16),int(m[4],16)))
        def one(**match):
            found=[r for r in records if all(r[1].get(k)==str(v) for k,v in match.items())]
            require(len(found)==1,'missing/duplicate '+str(match));return found[0]
        parent=one(role='parent');pid=int(parent[1]['pid']);require(pid>0 and parent[1]['cpl']=='3','parent identity')
        guard=one(guard='kernel_mprotect',rejected=1);restore=one(restoration=1,canary=1)
        api=one(api_guards=1)
        rx=one(rx_control=1,result=42)
        require(parent[0]<guard[0]<api[0]<restore[0]<rx[0],'setup order');previous=rx[0];pids={pid}
        require(len(faults)==11,'exactly eleven faults required')
        for i,case in enumerate(CASES):
            attempt=one(case=case,attempt=1);ack=one(case=case,ack=i+1)
            child=int(attempt[1]['pid']);require(child>0 and child not in pids,'child identity');pids.add(child)
            addr=BASE+(16+i*8)*4096+(4096 if i in (3,10) else 8192 if i==11 else 0)
            op='read' if i in (5,6,9,10,11) else 'execute' if i==7 else 'write'
            require(int(attempt[1]['address'],16)==addr and attempt[1]['operation']==op and attempt[1]['cpl']=='3','attempt mismatch')
            require(ack[1]['pid']==str(child) and ack[1]['parent_pid']==str(pid) and ack[1]['canary']=='1' and int(ack[1]['wait_status'])==(0 if i<2 else 35584),'witness/status mismatch')
            if i<2:
                observed=one(case=case,pid=child,private=1)[0]
            else:
                found=[f for f in faults if f[1]==child];require(len(found)==1,'missing/duplicate child fault')
                observed,_,actual,error=found[0];require(actual==addr and error==ERRORS[i],'fault address/error mismatch')
            require(previous<attempt[0]<observed<ack[0],'case ordering');previous=ack[0];result['cases_verified']+=1
        shared=one(lifetime='failed_exec',shared=1,rollback=1,cpl=3)
        shared_pid=int(shared[1]['pid']);require(shared_pid>0 and shared_pid not in pids,'shared child identity');pids.add(shared_pid)
        shared_wait=one(lifetime='failed_exec',pid=shared_pid,parent_pid=pid,wait_status=0,verified=1)
        owner=one(lifetime='owner_waited',parent_pid=pid,wait_status=0)
        owner_pid=int(owner[1]['pid']);survivor_pid=int(owner[1]['survivor_pid'])
        require(owner_pid>0 and survivor_pid>0 and owner_pid!=survivor_pid and owner_pid not in pids and survivor_pid not in pids,'survivor identities')
        survivor=one(lifetime='parent_exit',pid=survivor_pid,cpl=3,canary=1,survivor=1)
        survivor_wait=one(lifetime='parent_exit',pid=survivor_pid,parent_pid=pid,wait_status=0,verified=1)
        require(previous<shared[0]<shared_wait[0]<owner[0]<survivor[0]<survivor_wait[0],'lifetime ordering')
        loaded=one(lifetime='exec_loaded',cpl=3,loaded=1)
        exec_owner=one(lifetime='exec_owner_waited',parent_pid=pid,wait_status=0)
        exec_pid=int(exec_owner[1]['pid']);exec_survivor=int(exec_owner[1]['survivor_pid'])
        require(exec_pid>0 and exec_survivor>0 and exec_pid!=exec_survivor and not {exec_pid,exec_survivor}&(pids|{owner_pid,survivor_pid}),'exec lifecycle identities')
        require(int(loaded[1]['pid'])==exec_pid,'exec-loaded identity mismatch')
        detached=one(lifetime='exec_detach',pid=exec_survivor,cpl=3,canary=1,survivor=1)
        detached_wait=one(lifetime='exec_detach',pid=exec_survivor,parent_pid=pid,wait_status=0,verified=1)
        require(survivor_wait[0]<loaded[0]<exec_owner[0]<detached[0]<detached_wait[0],'exec lifecycle ordering')
        backing=one(backing='owned',pid=pid,closed_fd=1,reused_fd=1,split=1,clone=1,contents=1)
        require(detached_wait[0]<backing[0],'backing test order')
        auth_private=one(shm_auth='private',parent_pid=pid,cpl=3,destroy_denied=1,alias_denied=1,map_denied=1,clone_denied=1,wait_status=0)
        auth_public=one(shm_auth='public',parent_pid=pid,cpl=3,destroy_denied=1,alias_denied=1,owner_closed=1,survivor=1,wait_status=0)
        auth_pids={int(auth_private[1]['pid']),int(auth_public[1]['pid'])}
        require(len(auth_pids)==2 and all(x>0 for x in auth_pids) and not auth_pids&(pids|{owner_pid,survivor_pid,exec_pid,exec_survivor}),'SHM authorization child identities')
        require(set(auth_private[1])=={'shm_auth','pid','parent_pid','cpl','destroy_denied','alias_denied','map_denied','clone_denied','wait_status'},'unexpected private authorization fields')
        require(set(auth_public[1])=={'shm_auth','pid','parent_pid','cpl','destroy_denied','alias_denied','owner_closed','survivor','wait_status'},'unexpected public authorization fields')
        stale=one(shm_auth='stale',pid=pid,cpl=3,canonical=1,alias=1,unsupported=1,wide=1,canary=1)
        require(set(stale[1])=={'shm_auth','pid','cpl','old','new','slot','canonical','alias','unsupported','wide','canary'},'unexpected stale authorization fields')
        old_handle,new_handle,slot=(int(stale[1][key]) for key in ('old','new','slot'))
        require(0<old_handle<=0x7fffffff and 0<new_handle<=0x7fffffff and 1<=slot<64,'SHM handle bounds')
        require(old_handle&63==new_handle&63==slot and old_handle>>6<new_handle>>6,'SHM generation/slot identity')
        require(backing[0]<auth_private[0]<auth_public[0]<stale[0],'SHM authorization ordering')
        previous=stale[0]
        used=pids|{owner_pid,survivor_pid,exec_pid,exec_survivor}|auth_pids
        for exit_case in ('unmapped','mapped','explicit'):
            done=one(shm_exit=exit_case,parent_pid=pid,cpl=3,wait_status=0,retired=1)
            child=int(done[1]['pid']);require(child>0 and child not in used,'SHM exit identity');used.add(child)
            fields={'shm_exit','pid','parent_pid','cpl','wait_status','retired'}
            if exit_case!='unmapped':
                fields.add('survivor');require(done[1].get('survivor')=='1','SHM exit lost survivor')
            require(set(done[1])==fields and previous<done[0],'SHM exit fields/order');previous=done[0]
        attempted=one(shm_exit='fault',operation='read',cpl=3,attempt=1)
        child=int(attempted[1]['pid']);require(child>0 and child not in used,'SHM fault identity')
        address=int(attempted[1]['address'],16);require(address==0x7400000000,'SHM terminal fault address')
        actual=[f for f in faults if f[1]==child];require(len(actual)==1,'SHM terminal fault correlation')
        require(actual[0][2]==address and actual[0][3]==4,'SHM terminal fault error')
        done=one(shm_exit='fault',pid=child,parent_pid=pid,cpl=3,wait_status=35584,retired=1)
        require(set(attempted[1])=={'shm_exit','pid','address','operation','cpl','attempt'} and set(done[1])=={'shm_exit','pid','parent_pid','cpl','wait_status','retired'},'SHM fault fields')
        require(previous<attempted[0]<actual[0][0]<done[0],'SHM fault ordering');previous=done[0]
        complete=one(complete=1,cases=12,parent_pid=pid);progress=one(progress=1,parent_pid=pid,canary=1)
        require(previous<complete[0]<progress[0],'completion/progress ordering')
        require(len(records)==51,'unexpected marker count')
        result.update(passed=True,online_cpus=requested_cpus,scope='12 observed sequential memory transition cases; no remote TLB/concurrent COW proof')
    except (ValueError,KeyError,TypeError) as error:
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
