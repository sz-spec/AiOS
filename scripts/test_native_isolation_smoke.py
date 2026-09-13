#!/usr/bin/env python3
"""Negative controls for the isolation evidence oracle; no VM required."""
import unittest
from native_isolation_smoke import CASES, classify_serial, bind_artifact_identity


def trace(cpus=1):
    lines = ['PMM Statistics:', 'VMM: Initialization complete', 'Starting scheduler',
             f'SMP: {cpus} CPUs online',
             'NATIVE_ISOLATION kernel_target addr=0xffffffff82000000 present=1 user=0',
             'NATIVE_ISOLATION role=parent pid=10 cpl=3',
             'NATIVE_ISOLATION victim_mapping pid=11 addr=0x7000000000 root=0x1000 user_root=0x2000 phys=0x3000',
             'NATIVE_ISOLATION role=victim pid=11 cpl=3 address=0x7000000000 ready=1']
    for i, case in enumerate(CASES):
        pid = 12 + i
        kernel = case.startswith('kernel')
        write = case.endswith('write')
        addr = '0xffffffff82000000' if kernel else '0x7000000000'
        lines.extend([
            f'NATIVE_ISOLATION case={case} pid={pid} address={addr} operation={"write" if write else "read"} cpl=3 attempt=1',
            f'NATIVE_ISOLATION fault pid={pid} addr={addr} root=0x4000 user_root=0x5000 present={int(kernel)} user=0',
            f"SIGSEGV: task 'isolation' (pid={pid}) addr={addr} RIP=0x401000 err={hex(4 + 2 * write + kernel)}",
            f'NATIVE_ISOLATION case={case} pid={pid} wait_status=35584 contained=1',
            f'NATIVE_ISOLATION case={case} victim_pid=11 ack=1 canary=1 challenge={i+1}',
        ])
    lines.extend(['NATIVE_ISOLATION complete=1 cases=4 parent_pid=10 victim_status=0',
                  'NATIVE_ISOLATION progress=1 parent_pid=10'])
    return '\n'.join(lines)


class IsolationOracleTests(unittest.TestCase):
    def classify(self, text, cpus=1):
        return classify_serial(text, 'observation_timeout', cpus)

    def test_valid_single_and_multi_cpu_ansi(self):
        for cpus in (1, 4):
            with self.subTest(cpus=cpus):
                result = self.classify('\x1b[32m' + trace(cpus) + '\x1b[0m', cpus)
                self.assertTrue(result['passed'], result)
                self.assertEqual(result['cases_verified'], 4)

    def test_every_evidence_line_is_required(self):
        lines = trace().splitlines()
        for index in range(len(lines)):
            with self.subTest(missing=lines[index]):
                self.assertFalse(self.classify('\n'.join(lines[:index] + lines[index+1:]))['passed'])

    def test_corrupted_identity_permissions_liveness(self):
        changes = [
            ('cpl=3', 'cpl=0'), ('pid=12', 'pid=11'),
            ('(pid=12)', '(pid=99)'), ('err=0x4', 'err=0x5'),
            ('err=0x7', 'err=0x6'), ('wait_status=35584', 'wait_status=139'),
            ('canary=1', 'canary=0'), ('challenge=2', 'challenge=1'),
            ('root=0x4000', 'root=0x1000'), ('phys=0x3000', 'phys=0x3001'),
            ('present=0 user=0', 'present=1 user=0'),
            ('present=1 user=0', 'present=1 user=1'),
            ('victim_status=0', 'victim_status=35584'),
            ('case=kernel_read pid=14 address=0xffffffff82000000', 'case=kernel_read pid=14 address=0x7000000000'),
        ]
        for old, new in changes:
            with self.subTest(change=(old, new)):
                self.assertIn(old, trace())
                self.assertFalse(self.classify(trace().replace(old, new, 1))['passed'])

    def test_reordered_ack_and_fault_rejected(self):
        lines = trace().splitlines()
        lines[9], lines[12] = lines[12], lines[9]
        self.assertFalse(self.classify('\n'.join(lines))['passed'])

    def test_unexpected_fault_failure_or_duplicate_rejected(self):
        for extra in ('PANIC', 'General Protection Fault', 'SIGSEGV: unknown',
                      'NATIVE_ISOLATION fail=bad pid=10', 'NATIVE_ISOLATION FAIL reason=bad pid=10',
                      'Starting scheduler',
                      'NATIVE_ISOLATION progress=1 parent_pid=10'):
            with self.subTest(extra=extra):
                self.assertFalse(self.classify(trace() + '\n' + extra)['passed'])

    def test_wrong_cpu_and_early_exit_rejected(self):
        self.assertFalse(self.classify(trace(1), 4)['passed'])
        self.assertFalse(classify_serial(trace(), 0, 1)['passed'])

    def test_mutated_artifact_invalidates_complete_trace(self):
        result = self.classify(trace())
        bind_artifact_identity(result, 'a' * 64, 'b' * 64)
        self.assertFalse(result['passed'])
        self.assertFalse(result['artifact_unchanged'])


if __name__ == '__main__':
    unittest.main()
