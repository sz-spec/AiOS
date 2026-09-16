#!/usr/bin/env python3
import unittest
from native_memory_smoke import CASES, ERRORS, BASE, classify_serial, bind_artifact_identity

def trace(cpus=1):
    lines=['PMM Statistics:','VMM: Initialization complete','Starting scheduler',
             '[SHM-EXIT] PASS: unrelated identity retained, duplicate mark, IRQ-off drain deferred, two creator backings freed, mapped survivor, explicit-close no double release',
             '[SHM-IDENTITY] PASS: zero-cookie denied, same-TID different-cookie denied, owner restored, backing intact, final release',
             '[VM-FILE-REFS] PASS: checked retain, clone rollback, partial release, CPU pin, exactly-once close',
             '[PMM-CONTIGUOUS] PASS: pages=64 initial_refs=1 final_refs=0 exact_free_accounting=1',
            '[PROCESS-ROOTS] PASS: create, clone, independent roots, allocation failures, owner refs, CPU pins, release, accounting',
             '[VM-BACKING] PASS: tracking cap, foreign unmap, unsupported SHM fork, surviving owner, final mapping cleanup, creator-first explicit/reap, duplicate creator close',
             '[VM-METADATA] PASS: distinct tags, real COW copy, parent integrity, mprotect, flag updates, final accounting',
             f'SMP: {cpus} CPUs online','NATIVE_MEMORY role=parent pid=1 cpl=3','NATIVE_MEMORY guard=kernel_mprotect rejected=1','NATIVE_MEMORY api_guards=1','NATIVE_MEMORY restoration=1 canary=1','NATIVE_MEMORY rx_control=1 result=42']
    for i,case in enumerate(CASES):
        pid=100+i;addr=BASE+(16+i*8)*4096+(4096 if i in (3,10) else 8192 if i==11 else 0)
        op='read' if i in (5,6,9,10,11) else 'execute' if i==7 else 'write'
        lines.append(f'NATIVE_MEMORY case={case} pid={pid} address={hex(addr)} operation={op} cpl=3 attempt=1')
        lines.append(f'NATIVE_MEMORY case={case} pid={pid} private=1' if i<2 else f"SIGSEGV: task 'memory' (pid={pid}) addr={hex(addr)} RIP=0x400000 err={hex(ERRORS[i])}")
        lines.append(f'NATIVE_MEMORY case={case} pid={pid} wait_status={0 if i<2 else 35584} parent_pid=1 canary=1 ack={i+1}')
    lines += ['NATIVE_MEMORY lifetime=failed_exec pid=200 cpl=3 shared=1 rollback=1',
              'NATIVE_MEMORY lifetime=failed_exec pid=200 parent_pid=1 wait_status=0 verified=1',
              'NATIVE_MEMORY lifetime=owner_waited pid=201 survivor_pid=202 parent_pid=1 wait_status=0',
              'NATIVE_MEMORY lifetime=parent_exit pid=202 cpl=3 canary=1 survivor=1',
              'NATIVE_MEMORY lifetime=parent_exit pid=202 parent_pid=1 wait_status=0 verified=1',
              'NATIVE_MEMORY lifetime=exec_loaded pid=203 cpl=3 loaded=1',
              'NATIVE_MEMORY lifetime=exec_owner_waited pid=203 survivor_pid=204 parent_pid=1 wait_status=0',
              'NATIVE_MEMORY lifetime=exec_detach pid=204 cpl=3 canary=1 survivor=1',
              'NATIVE_MEMORY lifetime=exec_detach pid=204 parent_pid=1 wait_status=0 verified=1',
              'NATIVE_MEMORY backing=owned pid=1 closed_fd=1 reused_fd=1 split=1 clone=1 contents=1',
              'NATIVE_MEMORY shm_auth=private pid=205 parent_pid=1 cpl=3 destroy_denied=1 alias_denied=1 map_denied=1 clone_denied=1 wait_status=0',
              'NATIVE_MEMORY shm_auth=public pid=206 parent_pid=1 cpl=3 destroy_denied=1 alias_denied=1 owner_closed=1 survivor=1 wait_status=0',
              'NATIVE_MEMORY shm_auth=stale pid=1 cpl=3 old=65 new=129 slot=1 canonical=1 alias=1 unsupported=1 wide=1 canary=1',
              'NATIVE_MEMORY shm_exit=unmapped pid=207 parent_pid=1 cpl=3 wait_status=0 retired=1',
              'NATIVE_MEMORY shm_exit=mapped pid=208 parent_pid=1 cpl=3 wait_status=0 survivor=1 retired=1',
              'NATIVE_MEMORY shm_exit=explicit pid=209 parent_pid=1 cpl=3 wait_status=0 survivor=1 retired=1',
              'NATIVE_MEMORY shm_exit=fault pid=210 address=0x7400000000 operation=read cpl=3 attempt=1',
              "SIGSEGV: task 'memory' (pid=210) addr=0x7400000000 RIP=0x400000 err=0x4",
              'NATIVE_MEMORY shm_exit=fault pid=210 parent_pid=1 cpl=3 wait_status=35584 retired=1']
    return '\n'.join(lines+['NATIVE_MEMORY complete=1 cases=12 parent_pid=1','NATIVE_MEMORY progress=1 parent_pid=1 canary=1'])

class OracleTests(unittest.TestCase):
    def check(self,s):return classify_serial(s,'observation_timeout')
    def test_valid(self):
        for cpus in (1,4):self.assertTrue(classify_serial('\x1b[32m'+trace(cpus)+'\x1b[0m','observation_timeout',cpus)['passed'])
    def test_every_line_required(self):
        lines=trace().splitlines()
        for i in range(len(lines)):
            with self.subTest(line=lines[i]):self.assertFalse(self.check('\n'.join(lines[:i]+lines[i+1:]))['passed'])
    def test_corruptions(self):
        for a,b in [('cpl=3','cpl=0'),('err=0x15','err=0x5'),('err=0x6','err=0x7'),('canary=1','canary=0'),('wait_status=35584','wait_status=139'),('ack=5','ack=4'),('(pid=102)','(pid=103)'),('private=1','private=0'),('result=42','result=0')]:
            with self.subTest(a=a):self.assertFalse(self.check(trace().replace(a,b,1))['passed'])
    def test_extra_failure_and_reboot(self):
        for s in ['NATIVE_MEMORY FAIL reason=late','NATIVE_ISOLATION FAIL kernel_canary','SIGSEGV invalid','PANIC','Starting scheduler','NATIVE_MEMORY progress=1 parent_pid=1 canary=1']:
            self.assertFalse(self.check(trace()+'\n'+s)['passed'])
    def test_status_cpu_and_artifact(self):
        self.assertFalse(classify_serial(trace(),0)['passed'])
        self.assertFalse(classify_serial(trace(),'observation_timeout',4)['passed'])
        r=self.check(trace());bind_artifact_identity(r,'a','b');self.assertFalse(r['passed'])
    def test_contiguous_marker_exact_and_unique(self):
        marker = "[PMM-CONTIGUOUS] PASS: pages=64 initial_refs=1 final_refs=0 exact_free_accounting=1"
        for bad in (trace().replace('pages=64', 'pages=63'),
                    trace().replace('initial_refs=1', 'initial_refs=0'),
                    trace().replace('final_refs=0', 'final_refs=1'),
                    trace().replace('exact_free_accounting=1', 'exact_free_accounting=0'),
                    trace() + '\n' + marker):
            self.assertFalse(self.check(bad)['passed'])

    def test_old_backing_marker_is_insufficient(self):
        self.assertFalse(self.check(trace().replace(', creator-first explicit/reap, duplicate creator close',''))['passed'])
    def test_shm_authorization_mutations(self):
        for old,new in [('destroy_denied=1','destroy_denied=0'),('alias_denied=1','alias_denied=0'),('map_denied=1','map_denied=0'),('clone_denied=1','clone_denied=0'),('owner_closed=1','owner_closed=0'),('new=129','new=65'),('new=129','new=130'),('new=129','new=2147483649'),('slot=1','slot=0'),('pid=205','pid=1'),('pid=206','pid=205'),('wide=1','wide=0')]:
            with self.subTest(old=old,new=new):self.assertFalse(self.check(trace().replace(old,new,1))['passed'])
    def test_shm_exit_mutations(self):
        for old,new in [('retired=1','retired=0'),('shm_exit=mapped pid=208','shm_exit=mapped pid=207'),('address=0x7400000000','address=0x7400001000'),('(pid=210)','(pid=211)'),('addr=0x7400000000 RIP=0x400000 err=0x4','addr=0x7400000000 RIP=0x400000 err=0x5')]:
            with self.subTest(old=old):self.assertFalse(self.check(trace().replace(old,new,1))['passed'])
    def test_order(self):
        l=trace().splitlines();a=next(i for i,x in enumerate(l) if 'case=cow_private' in x and 'attempt=1' in x);l[a],l[a+1]=l[a+1],l[a];self.assertFalse(self.check('\n'.join(l))['passed'])
if __name__=='__main__':unittest.main()
