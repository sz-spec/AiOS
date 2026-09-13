"""Regression cases for boot evidence that previously looked successful."""
import unittest

from native_boot_smoke import classify_serial


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
        self.assertEqual(result['failures'], ['PANIC'])
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

    def test_live_wizard_without_fault_is_boot_smoke_success(self):
        self.assertTrue(classify_serial('The AI-Native Operating System',
                                        'observation_timeout')['passed'])


if __name__ == '__main__':
    unittest.main()
