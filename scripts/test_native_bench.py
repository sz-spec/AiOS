"""Native benchmark oracle and owned-process lifecycle regressions (no VM)."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native_bench', Path(__file__).with_name('native_bench.py'))
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class NativeBenchTests(unittest.TestCase):
    def fixture(self, names=('one', 'two'), test_only=''):
        prefix = 'TEST FILTER: "' + test_only + '"\n' if test_only else ''
        return prefix + ''.join('=== Running: ' + n + ' ===\n[PASS] actual check\n=== ' + n + ': exit=0 ===\n' for n in names) + bench.COMPLETE + '\n' + bench.HALT + '\n'

    def test_full_source_workload_and_explicit_filter(self):
        source = (bench.ROOT / 'user/src/init.c').read_text()
        names = bench.workload(source)
        self.assertGreater(len(names), 50)
        self.assertIn('bench_2026_frontier', names)
        self.assertNotIn('test_vmm_soak', names)
        self.assertEqual(bench.workload(source, 'test_vmm_soak'), ['test_vmm_soak'])
        with self.assertRaises(ValueError):
            bench.workload(source, 'no_such_benchmark')

    def test_complete_sequence_and_partial_scope(self):
        self.assertTrue(bench.classify_serial(self.fixture(), ['one', 'two'], 'suite_observed')['passed'])
        result = bench.classify_serial(self.fixture(('one',), 'one'), ['one'], 'suite_observed', 'one')
        self.assertTrue(result['passed'])
        self.assertTrue(result['partial'])
        self.assertFalse(bench.classify_serial(self.fixture(('one',), 'one'), ['one'], 'suite_observed')['passed'])

    def test_completion_cannot_hide_failures(self):
        good = self.fixture()
        mutations = [good.replace('two: exit=0', 'two: exit=1'),
                     good.replace('two: exit=0', 'two: exit=-1'),
                     good.replace('[PASS]', '[FAIL]', 1),
                     good + '\nPANIC: after completion\n',
                     good + '\nInit: ERROR - fork() failed for hidden\n',
                     good + '\nInit: ERROR - Failed to exec /bin/hidden!\n',
                     good + '\nRESULTS: 2 PASS, 1 FAIL\n',
                     good.replace('=== two: exit=0 ===\n', ''),
                     good + '\n=== two: exit=0 ===\n',
                     bench.COMPLETE + '\n' + good,
                     good.replace(bench.HALT, ''),
                     good + '\n=== SKIP: hidden ===\n',
                     good + '\n=== Running: broken',
                     good + '\n=== hidden: exit=garbled ===\n']
        for raw in mutations:
            with self.subTest(raw=raw):
                self.assertFalse(bench.classify_serial(raw, ['one', 'two'], 'suite_observed')['passed'])
        for status in ('timeout', 0, 1, -9):
            self.assertFalse(bench.classify_serial(good, ['one', 'two'], status)['passed'])

    def test_nested_expected_fault_does_not_override_top_level_result(self):
        # Security benchmarks deliberately kill nested children. Their enclosing
        # benchmark must still report success and the whole sequence must finish.
        raw = self.fixture().replace('[PASS]', 'SIGSEGV pid=123\n[PASS]', 1)
        self.assertTrue(bench.classify_serial(raw, ['one', 'two'], 'suite_observed')['passed'])

    def test_real_child_exit_timeout_and_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'serial.log'
            self.assertEqual(bench.observe([sys.executable, '-c', 'raise SystemExit(7)'], path, 3), 7)
            self.assertEqual(bench.observe([sys.executable, '-c', 'import time; time.sleep(30)'], path, 0.15), 'timeout')
            code = 'import time; print(' + repr(bench.HALT) + ', flush=True); time.sleep(30)'
            self.assertEqual(bench.observe([sys.executable, '-c', code], path, 4), 'suite_observed')


if __name__ == '__main__':
    unittest.main()
