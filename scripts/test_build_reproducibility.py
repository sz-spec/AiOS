"""Host-only controls for reproducibility inputs and bootloader cache identity."""
import argparse
import importlib.util
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class BuildReproducibilityTests(unittest.TestCase):
    def test_epoch_and_nested_tree_timestamps(self):
        code = module('check_native_reproducibility')
        self.assertEqual(code.source_epoch('1700000000'), 1700000000)
        for invalid in ('-1', 'abc', '1.5', '253402300800'):
            with self.assertRaises(argparse.ArgumentTypeError):
                code.source_epoch(invalid)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            leaf = root / 'nested' / 'source.c'
            leaf.parent.mkdir()
            leaf.write_bytes(b'int example;\n')
            leaf.chmod(0o751)
            expected_hash = code.digest(leaf)
            code.normalize_tree_times(root, 1700000000)
            for path in (root, leaf.parent, leaf):
                self.assertEqual(path.stat().st_mtime_ns, 1700000000000000000)
                self.assertEqual(path.stat().st_atime_ns, 1700000000000000000)
            self.assertEqual(leaf.stat().st_mode & 0o777, 0o751)
            self.assertEqual(code.digest(leaf), expected_hash)

    def test_target_flags_invalidate_actual_limine_state(self):
        code = module('limine_build_state')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name in ('kernel/boot/limine/version', 'infra/build_limine.sh',
                         'scripts/limine_build_state.py'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            output = root / 'output'
            output.mkdir()
            for name in code.ARTIFACTS:
                (output / name).write_bytes(b'unchanged artifact')
            with patch.object(code, 'ROOT', root), patch.object(code, '__file__',
                    str(root / 'scripts/limine_build_state.py')), patch.dict(os.environ, {}, clear=True), \
                    patch.object(code.subprocess, 'run', return_value=types.SimpleNamespace(stdout='tool 1\n')):
                before = code.state(output)
                for key in ('CC', 'CC_FOR_BUILD', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS',
                            'LIBS', 'CFLAGS_FOR_BUILD', 'CFLAGS_FOR_TARGET',
                            'CPPFLAGS_FOR_TARGET', 'LDFLAGS_FOR_TARGET',
                            'NASMFLAGS_FOR_TARGET', 'NASMENV', 'SOURCE_DATE_EPOCH',
                            'CC_FOR_TARGET', 'LD_FOR_TARGET', 'OBJCOPY_FOR_TARGET',
                            'OBJDUMP_FOR_TARGET', 'READELF_FOR_TARGET', 'STRIP',
                            'INSTALL', 'SED', 'GREP', 'AWK'):
                    with patch.dict(os.environ, {key: 'changed'}):
                        after = code.state(output)
                        self.assertNotEqual(before, after, key)
                        self.assertEqual(before['artifacts'], after['artifacts'])
                self.assertEqual(before, code.state(output))


if __name__ == '__main__':
    unittest.main()
