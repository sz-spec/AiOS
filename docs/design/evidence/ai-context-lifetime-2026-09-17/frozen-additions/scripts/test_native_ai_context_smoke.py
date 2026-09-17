import importlib.util
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).parent))
import native_ai_context_smoke as observer

class AIContextOracle(unittest.TestCase):
    def fixture(self):
        return ('PMM Statistics:\nVMM: Initialization complete\nStarting scheduler\n'
                'SMP: 1 CPUs online\n[INFO] ' + observer.MARKER + '\n'
                'The AI-Native Operating System\n')

    def test_complete_and_failure_diagnostics(self):
        raw = self.fixture()
        self.assertTrue(observer.classify_serial(raw, 'observation_timeout')['passed'])
        for diagnostic in ('PANIC', '[FAIL]', 'AI-CTX-LIFETIME: early reclaim'):
            self.assertFalse(observer.classify_serial(raw + diagnostic, 'observation_timeout')['passed'])
        self.assertFalse(observer.classify_serial(raw, 0)['passed'])

    def test_missing_malformed_duplicate_or_premature_progress(self):
        raw = self.fixture()
        for changed in (raw.replace(observer.MARKER, ''),
                        raw.replace('finalized=2', 'finalized=1'),
                        raw + observer.MARKER,
                        raw.replace('The AI-Native Operating System', ''),
                        'The AI-Native Operating System\n' + raw):
            self.assertFalse(observer.classify_serial(changed, 'observation_timeout')['passed'])

if __name__ == '__main__': unittest.main()
