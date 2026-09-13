"""Exercise JWT configuration in fresh interpreters, as separate workers load it."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

BACKEND = Path(__file__).resolve().parents[1]


class ProductionJWTConfiguration(unittest.TestCase):
    def worker(self, environment, secret=None, code="from tools.auth import JWT_SECRET; print('loaded')"):
        env = dict(os.environ, ENVIRONMENT=environment, PYTHONPATH=str(BACKEND))
        env.pop('JWT_SECRET', None)
        if secret is not None:
            env['JWT_SECRET'] = secret
        return subprocess.run([sys.executable, '-c', code], env=env, cwd=BACKEND,
                              capture_output=True, text=True, timeout=20)

    def test_production_missing_or_blank_secret_fails_before_service_creation(self):
        for environment in ['production', 'PRODUCTION', ' prod ']:
            for secret in [None, '', '   ']:
                with self.subTest(environment=environment, secret=secret):
                    result = self.worker(environment, secret)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('JWT_SECRET is required in production', result.stderr)
                    self.assertNotIn('ephemeral', result.stdout)

    def test_development_keeps_ephemeral_fallback(self):
        result = self.worker('development')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('loaded', result.stdout)

    def test_separate_workers_use_same_configured_secret_without_logging_it(self):
        secret = 'test-only-shared-worker-key-' + 'x' * 40
        code = "from tools.auth import JWT_SECRET; import hashlib; print(hashlib.sha256(JWT_SECRET.encode()).hexdigest())"
        first = self.worker('production', secret, code)
        second = self.worker('production', secret, code)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertNotIn(secret, first.stdout + first.stderr + second.stdout + second.stderr)


if __name__ == '__main__':
    unittest.main()
