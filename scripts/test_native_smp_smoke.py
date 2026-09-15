#!/usr/bin/env python3
import unittest
from native_smp_smoke import classify_serial,expected_values,STEPS

def trace(cpus=4,one_cpu=False):
 lines=['PMM Statistics:','VMM: Initialization complete','Starting scheduler',f'SMP: {cpus} CPUs online']
 lines += [f'NATIVE_SMP topology cpu={i} apic={i} online=1' for i in range(cpus)]
 lines += ['NATIVE_SMP role=parent pid=1 cpl=3 apic=0']
 for w in range(2):
  apic=0 if one_cpu or cpus==1 else w
  lines += [f'NATIVE_SMP schedule pid={100+w} cpu={apic} apic={apic}']
  for n,v in enumerate(expected_values(w),1):lines += [f'NATIVE_SMP worker={w} pid={100+w} checkpoint={n} steps={STEPS} value={v} cpl=3 apic={apic}']
  lines += [f'NATIVE_SMP worker={w} pid={100+w} wait_status=0 parent_pid=1']
 return '\n'.join(lines+['NATIVE_SMP complete=1 workers=2 checkpoints=16 parent_pid=1','NATIVE_SMP progress=1 parent_pid=1 cpl=3 apic=0'])

class SMPTests(unittest.TestCase):
 def test_valid(self):
  for n in (1,2,4):self.assertTrue(classify_serial(trace(n),'observation_timeout',n)['passed'])
 def test_baseline_only_bsp_fails(self):self.assertFalse(classify_serial(trace(4,True),'observation_timeout',4)['passed'])
 def test_all_lines_required(self):
  lines=trace().splitlines()
  for i in range(len(lines)):
   with self.subTest(line=lines[i]):self.assertFalse(classify_serial('\n'.join(lines[:i]+lines[i+1:]),'observation_timeout',4)['passed'])
 def test_corruptions(self):
  for old,new in [('cpl=3','cpl=0'),('checkpoint=2','checkpoint=1'),('steps=200000','steps=1'),('wait_status=0','wait_status=35584'),('cpu=1 apic=1','cpu=1 apic=9'),('value='+str(expected_values(0)[0]),'value=0')]:
   with self.subTest(old=old):self.assertFalse(classify_serial(trace().replace(old,new,1),'observation_timeout',4)['passed'])
 def test_observed_mid_record_interleaving_rejected(self):
  original=trace()
  line=next(x for x in original.splitlines() if 'worker=0' in x and 'checkpoint=1 ' in x)
  # Actual observed failure: kernel schedule output interrupts the APIC token.
  broken=line.replace('apic=0','api[INFO]  NATIVE_SMP schedule pid=101 cpu=0 apic=0\nc=0')
  result=classify_serial(original.replace(line,broken),'observation_timeout',4)
  self.assertFalse(result['passed'])
  self.assertIn('malformed marker',result['failures'])
 def test_truncated_schedule_rejected(self):
  original=trace()
  for fragment in ('NATIVE_SMP schedule pid=100 cpu=0 api',
                   'NATIVE_SMP schedule pid=100 cpu=0 apic=',
                   'NATIVE_SMP schedule pid=100'):
   with self.subTest(fragment=fragment):
    self.assertFalse(classify_serial(original+'\n'+fragment,'observation_timeout',4)['passed'])
 def test_failures(self):
  for extra in ('PANIC','SIGSEGV','NATIVE_SMP FAIL reason=late','NATIVE_SMP progress=1 parent_pid=1 cpl=3 apic=0'):
   self.assertFalse(classify_serial(trace()+'\n'+extra,'observation_timeout',4)['passed'])
  self.assertFalse(classify_serial(trace(),0,4)['passed'])
if __name__=='__main__':unittest.main()
