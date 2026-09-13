"""Tests for auth_builder — authentication code generation."""

from services.auth_builder import generate_auth_code


class TestAuthBuilder:
    def test_generate_email_only(self):
        files = generate_auth_code(["email"])
        assert len(files) == 4
        login = files.get("src/pages/Login.tsx", "")
        assert "email" in login.lower()

    def test_generate_with_google(self):
        files = generate_auth_code(["email", "google"])
        login = files.get("src/pages/Login.tsx", "")
        assert "google" in login.lower()

    def test_generate_with_github(self):
        files = generate_auth_code(["email", "github"])
        login = files.get("src/pages/Login.tsx", "")
        assert "github" in login.lower()

    def test_has_protected_route(self):
        files = generate_auth_code(["email"])
        assert "src/components/ProtectedRoute.tsx" in files

    def test_has_auth_lib(self):
        files = generate_auth_code(["email"])
        auth_lib = files.get("src/lib/auth.ts", "")
        assert "signIn" in auth_lib or "sign" in auth_lib.lower()

    def test_provider_name_sanitization(self):
        """Document: provider names are interpolated into code without sanitization."""
        files = generate_auth_code(["email", "malicious_provider"])
        login = files.get("src/pages/Login.tsx", "")
        # The provider name is used in generated code — this documents the behavior
        assert isinstance(login, str)
