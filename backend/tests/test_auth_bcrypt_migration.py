"""Offline bcrypt API migration checks, independent of optional tools imports."""
import importlib.util
from pathlib import Path
import sys
import unittest

from pydantic import ValidationError

spec = importlib.util.spec_from_file_location('vos_bcrypt_auth_under_test', Path(__file__).resolve().parents[1]/'tools/auth.py')
auth = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = auth
spec.loader.exec_module(auth)

# Independently generated with Passlib 1.7.4 pure-Python raw_bcrypt, not bcrypt 5.
LEGACY = '$2b$04$abcdefghijklmnopqrstuuHQRMHradWrjjbPcbpK37RVvfSYCXoLy'

class BcryptMigrationTests(unittest.TestCase):
    def test_legacy_bcrypt_variants_and_wrong_password(self):
        for prefix in ['$2a$', '$2b$', '$2y$']:
            hashed = prefix + LEGACY[4:]
            self.assertTrue(auth.verify_password('legacy-password', hashed))
            self.assertFalse(auth.verify_password('wrong-password', hashed))

    def test_new_hash_cost_random_salt_and_verification(self):
        first = auth.hash_password('valid-password')
        second = auth.hash_password('valid-password')
        self.assertTrue(first.startswith('$2b$12$'))
        self.assertNotEqual(first, second)
        self.assertTrue(auth.verify_password('valid-password', first))

    def test_unicode_uses_byte_boundary_and_never_truncates(self):
        password = 'é' * 36
        hashed = auth.hash_password(password)
        self.assertTrue(auth.verify_password(password, hashed))
        for invalid in [password + 'a', 'a' * 73, 'x\x00y', '\ud800']:
            with self.assertRaises(ValueError):
                auth.hash_password(invalid)
            self.assertFalse(auth.verify_password(invalid, hashed))

    def test_bad_stored_hashes_fail_without_exceptions(self):
        for hashed in ['', 'not-bcrypt', '$2b$99$bad', 'é', None, LEGACY[:-1]]:
            self.assertFalse(auth.verify_password('legacy-password', hashed))

    def test_models_reject_oversize_utf8_passwords(self):
        for model in [auth.UserCreate, auth.UserLogin]:
            self.assertEqual(model(email='user@example.com', password='é'*36).password, 'é'*36)
            with self.assertRaises(ValidationError):
                model(email='user@example.com', password='é'*37)

    def test_http_model_boundary_returns_422_for_oversize_password(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        calls = []

        @app.post("/register")
        def register(body: auth.UserCreate):
            calls.append(body.email)
            return {"accepted": True}

        with TestClient(app) as client:
            response = client.post("/register", json={"email": "user@example.com", "password": "é" * 37})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(calls, [])

    def test_store_rejects_invalid_login_and_update_without_mutation(self):
        store = auth.UserStore()
        user = store.create_user('user@example.com', 'original-password')
        previous = user.hashed_password
        self.assertIsNone(store.authenticate(user.email, 'x'*73))
        self.assertFalse(store.update_password(user.id, 'x'*73))
        self.assertEqual(user.hashed_password, previous)
        self.assertIs(store.authenticate(user.email, 'original-password'), user)
        with self.assertRaises(ValueError):
            store.create_user('invalid@example.com', 'x'*73)
        self.assertIsNone(store.get_user_by_email('invalid@example.com'))

if __name__ == '__main__': unittest.main()
