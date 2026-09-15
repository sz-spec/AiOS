#!/usr/bin/env python3
"""Compile the production TLB protocol with host architecture hooks.

These checks exercise actual C protocol logic, not hardware TLB behavior.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCENARIOS=('empty','unaligned','top','overflow','hole','sparse','duplicate','timeout','nested','pre_smp','duplicate_apic','old_ack','send_failure','partial_send_failure')

class ProductionTLBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory(prefix='vos-tlb-host-')
        cls.binary=Path(cls.directory.name)/'tlb-test'
        command=[os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror',
                 '-I',str(ROOT/'kernel/include'),str(ROOT/'tests/native/tlb_shootdown_host.c'),
                 str(ROOT/'kernel/src/mm/tlb_shootdown.c'),'-o',str(cls.binary)]
        subprocess.run(command,check=True,capture_output=True,text=True)
    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()
    def test_protocol_scenarios(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                result=subprocess.run([str(self.binary),scenario],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertIn('PASS '+scenario,result.stdout)

if __name__=='__main__':unittest.main()
