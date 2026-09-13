"""Regression cases for boot evidence that previously looked successful."""
import unittest

from native_boot_smoke import classify_serial, bind_artifact_identity

VALID = ('PMM Statistics:\nVMM: Initialization complete\nStarting scheduler\n'
         'SMP: 1 CPUs online\nThe AI-Native Operating System\n')


class NativeBootEvidenceTests(unittest.TestCase):
    def test_kernel_creating_wizard_is_not_user_space_execution(self):
        result = classify_serial('Starting scheduler\nFirst task: setup_wizard\n'
                                 'SetupWizard: Executing /bin/setup_wizard',
                                 'observation_timeout')
        self.assertTrue(result['scheduler'])
        self.assertFalse(result['passed'])

    def test_uefi_cursor_sequences_cannot_hide_panic(self):
        # EDK2/Limine can interleave cursor positioning between every letter.
        panic = ''.join(f'\x1b[2;{i}H{letter}' for i, letter in enumerate('PANIC', 1))
        result = classify_serial('The AI-Native Operating System\n' + panic,
                                 'observation_timeout')
        self.assertIn('PANIC', result['failures'])
        self.assertFalse(result['passed'])

    def test_unexpected_vm_exit_after_banner_is_failure(self):
        # -no-reboot may exit after a triple fault, even with exit code zero.
        for code in (0, 1, -9):
            with self.subTest(code=code):
                self.assertFalse(classify_serial('The AI-Native Operating System', code)['passed'])

    def test_process_fault_after_banner_is_failure(self):
        result = classify_serial('The AI-Native Operating System\nSIGSEGV',
                                 'observation_timeout')
        self.assertFalse(result['passed'])

    def test_complete_boot_evidence_is_required(self):
        self.assertTrue(classify_serial(VALID, 'observation_timeout')['passed'])
        for line in VALID.splitlines():
            with self.subTest(missing=line):
                self.assertFalse(classify_serial(VALID.replace(line, ''), 'observation_timeout')['passed'])

    def test_exact_cpu_count_including_single_cpu(self):
        for requested in (1, 2, 4):
            for actual in (0, 1, 2, 4, 8):
                with self.subTest(requested=requested, actual=actual):
                    raw = VALID.replace('1 CPUs online', f'{actual} CPUs online')
                    self.assertEqual(classify_serial(raw, 'observation_timeout', requested)['passed'], actual == requested)

    def test_ansi_cpu_summary_and_last_summary(self):
        decorated = ''.join('\x1b[1;1H' + char for char in 'SMP: 1 CPUs online')
        result = classify_serial(VALID.replace('SMP: 1 CPUs online', decorated), 'observation_timeout')
        self.assertTrue(result['passed'])
        self.assertFalse(classify_serial(VALID + 'SMP: 2 CPUs online', 'observation_timeout')['passed'])

    def test_fault_before_or_after_complete_boot(self):
        for fault in ('PANIC', 'SIGSEGV', 'SetupWizard: Failed to exec', 'uaccess address-space mismatch'):
            for raw in (fault + VALID, VALID + fault):
                self.assertFalse(classify_serial(raw, 'observation_timeout')['passed'])

    def test_changed_iso_invalidates_otherwise_valid_observation(self):
        for before, after in [('abc', 'abc'), ('abc', 'def')]:
            result = bind_artifact_identity(classify_serial(VALID, 'observation_timeout'), before, after)
            self.assertEqual(result['passed'], before == after)
            self.assertEqual(result['iso_sha256'], before)
            self.assertEqual(result['iso_sha256_after'], after)


if __name__ == '__main__':
    unittest.main()
