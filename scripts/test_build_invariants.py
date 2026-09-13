"""Independent embedding invariants: atomic rejection and exact byte identity."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('embed', ROOT / 'kernel/tools/embed_binaries.py')
embed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(embed)


class EmbeddingInvariants(unittest.TestCase):
    def test_identity_noop_and_rejection_preserve_previous_output(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            binary = root / 'app'
            output = root / 'embedded.c'
            binary.write_bytes(bytes(range(256)))
            embed.generate(output, root, ['app'])
            original = output.read_bytes()
            stamp = output.stat().st_mtime_ns
            for byte in range(256):
                self.assertIn(f'0x{byte:02x}'.encode(), original)
            embed.generate(output, root, ['app'])
            self.assertEqual(output.stat().st_mtime_ns, stamp)
            for programs, error in [(['../app'], ValueError), (['missing'], FileNotFoundError)]:
                with self.assertRaises(error):
                    embed.generate(output, root, programs)
                self.assertEqual(output.read_bytes(), original)
            (root / 'a-b').write_bytes(b'a')
            (root / 'a.b').write_bytes(b'b')
            with self.assertRaises(ValueError):
                embed.generate(output, root, ['a-b', 'a.b'])
            self.assertEqual(output.read_bytes(), original)
            binary.write_bytes(b'changed')
            embed.generate(output, root, ['app'])
            self.assertNotEqual(output.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
