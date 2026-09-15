#!/usr/bin/env python3
import unittest
from test_native_smp_smoke import trace as smp_trace
from native_tlb_smoke import classify_serial

def trace(cpus=4):
    smp=smp_trace(cpus);marker='NATIVE_SMP role=parent'
    start=smp.index(marker);records=[]
    for phase in ('warm','remap'):
        for cpu in range(cpus):records.append(f'NATIVE_TLB phase={phase} cpu={cpu} apic={cpu} before0=17 before1=34 after0=17 after1={34 if phase=="warm" else 51}')
    records.append(f'NATIVE_TLB complete=1 cpus={cpus} rounds=2')
    return smp[:start]+'\n'.join(records)+'\n'+smp[start:]

class TLBObserverTests(unittest.TestCase):
    def test_valid(self):
        for n in (1,2,4):self.assertTrue(classify_serial(trace(n),'observation_timeout',n)['passed'])
    def test_every_probe_record_required(self):
        lines=trace().splitlines()
        for i,line in enumerate(lines):
            if 'NATIVE_TLB ' in line:
                with self.subTest(line=line):self.assertFalse(classify_serial('\n'.join(lines[:i]+lines[i+1:]),'observation_timeout',4)['passed'])
    def test_flush_skip_negative(self):
        self.assertFalse(classify_serial(trace().replace('after1=51','after1=34'),'observation_timeout',4)['passed'])
    def test_corruptions(self):
        for a,b in [('before1=34','before1=51'),('before0=17','before0=0'),('after0=17','after0=0'),('rounds=2','rounds=1'),('phase=warm cpu=1 apic=1','phase=warm cpu=1 apic=3')]:
            with self.subTest(a=a):self.assertFalse(classify_serial(trace().replace(a,b,1),'observation_timeout',4)['passed'])
    def test_order_duplicate_truncation(self):
        lines=trace().splitlines();a=next(i for i,l in enumerate(lines) if 'phase=warm' in l);b=next(i for i,l in enumerate(lines) if 'phase=remap' in l);lines[a],lines[b]=lines[b],lines[a]
        self.assertFalse(classify_serial('\n'.join(lines),'observation_timeout',4)['passed'])
        for extra in ('NATIVE_TLB complete=1 cpus=4 rounds=2','NATIVE_TLB phase=remap cpu=','NATIVE_TLB FAIL reason=late'):
            self.assertFalse(classify_serial(trace()+'\n'+extra,'observation_timeout',4)['passed'])
    def test_smp_failure_not_hidden(self):
        self.assertFalse(classify_serial(trace().replace('value=3783823169','value=0'),'observation_timeout',4)['passed'])
if __name__=='__main__':unittest.main()
