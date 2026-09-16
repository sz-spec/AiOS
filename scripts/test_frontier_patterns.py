"""Validate actual benchmark pattern expressions under undefined-behavior checks."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FrontierPatterns(unittest.TestCase):
    def test_production_patterns_are_defined_and_unique(self):
        source = (ROOT / 'user/src/bench_2026_frontier.c').read_text()
        assignments = re.findall(r'^\s*g_t3_patterns\[i\]\.(?:lo|hi)\s*=.*;', source, re.M)
        self.assertEqual(len(assignments), 2)
        count = re.search(r'#define\s+T3_NUM_AGENTS\s+(\d+)', source)
        self.assertIsNotNone(count)
        program = '''#include <stdint.h>
#include <assert.h>
struct pattern { uint64_t lo, hi; } g_t3_patterns[%s];
int main(void) {
 for (int i=0; i<%s; ++i) {
 %s
 assert(g_t3_patterns[i].lo == (UINT64_C(0xDEADBEEF00000000) | ((uint64_t)i * UINT64_C(0x11111111) & UINT64_C(0xffffffff))));
 assert(g_t3_patterns[i].hi == (UINT64_C(0xCAFEBABE00000000) | ((uint64_t)i * UINT64_C(0x22222222) & UINT64_C(0xffffffff))));
 for (int j=0; j<i; ++j) {
  assert(g_t3_patterns[i].lo != g_t3_patterns[j].lo);
  assert(g_t3_patterns[i].hi != g_t3_patterns[j].hi);
 }
 }
 return 0;
}
''' % (count[1], count[1], '\n'.join(assignments))
        with tempfile.TemporaryDirectory(prefix='vos-frontier-patterns-') as directory:
            base = Path(directory)
            (base / 'test.c').write_text(program)
            compiled = subprocess.run(['cc', '-std=c11', '-O1', '-fsanitize=undefined',
                                       '-fno-sanitize-recover=undefined', str(base / 'test.c'),
                                       '-o', str(base / 'test')], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(base / 'test')], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
