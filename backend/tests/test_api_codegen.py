"""
Tests for Code Generation API Routes
=====================================

Covers: POST /api/codegen/generate, POST /api/codegen/generate/project,
        POST /api/codegen/validate, POST /api/codegen/refactor,
        POST /api/codegen/explain, GET /api/codegen/os-pipeline/info,
        POST /api/codegen/test/generate
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def no_code_generator():
    """Ensure get_code_generator returns None so dev-mode fallback is exercised."""
    with patch("api.codegen_routes.get_code_generator", return_value=None), patch(
        "api.codegen_routes.assign_model_with_tracking"
    ) as mock_track:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(
            return_value=MagicMock(tokens_in=0, tokens_out=0, success=True)
        )
        ctx.__exit__ = MagicMock(return_value=False)
        mock_track.return_value = ("claude-sonnet-4-6", ctx)
        yield


# ---------------------------------------------------------------------------
# POST /api/codegen/generate
# ---------------------------------------------------------------------------


class TestGenerateCode:
    @pytest.mark.parametrize(
        "language", ["python", "typescript", "javascript", "react"]
    )
    def test_generate_returns_200_for_language(self, client, language):
        payload = {"prompt": "Write a hello world function", "language": language}
        response = client.post("/api/codegen/generate", json=payload)
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "language", ["python", "typescript", "javascript", "react"]
    )
    def test_generate_response_contains_code(self, client, language):
        payload = {"prompt": "Write a hello world function", "language": language}
        data = client.post("/api/codegen/generate", json=payload).json()
        assert "code" in data
        assert isinstance(data["code"], str)
        assert len(data["code"]) > 0

    @pytest.mark.parametrize(
        "language", ["python", "typescript", "javascript", "react"]
    )
    def test_generate_response_contains_explanation(self, client, language):
        payload = {"prompt": "Write a hello world function", "language": language}
        data = client.post("/api/codegen/generate", json=payload).json()
        assert "explanation" in data

    def test_generate_response_contains_validation(self, client):
        payload = {"prompt": "Write a function", "language": "python"}
        data = client.post("/api/codegen/generate", json=payload).json()
        assert "validation" in data
        assert "valid" in data["validation"]

    def test_generate_invalid_language_returns_422(self, client):
        payload = {"prompt": "Do something", "language": "cobol"}
        response = client.post("/api/codegen/generate", json=payload)
        assert response.status_code == 422

    def test_generate_python_code_contains_python_marker(self, client):
        payload = {"prompt": "create a sum function", "language": "python"}
        data = client.post("/api/codegen/generate", json=payload).json()
        # Dev mode template wraps the prompt in a Python comment
        assert (
            "python" in data["code"].lower()
            or "def" in data["code"]
            or "#" in data["code"]
        )

    def test_generate_with_framework_field(self, client):
        payload = {
            "prompt": "Build a REST endpoint",
            "language": "python",
            "framework": "fastapi",
        }
        response = client.post("/api/codegen/generate", json=payload)
        assert response.status_code == 200

    def test_generate_dependencies_is_list(self, client):
        payload = {"prompt": "Write a function", "language": "python"}
        data = client.post("/api/codegen/generate", json=payload).json()
        assert isinstance(data["dependencies"], list)


# ---------------------------------------------------------------------------
# POST /api/codegen/generate/project
# ---------------------------------------------------------------------------


class TestGenerateProject:
    def test_project_returns_200(self, client):
        payload = {
            "prompt": "A simple TODO API",
            "project_type": "api",
            "language": "python",
        }
        response = client.post("/api/codegen/generate/project", json=payload)
        assert response.status_code == 200

    def test_project_response_has_name(self, client):
        payload = {"prompt": "A CLI tool", "project_type": "cli"}
        data = client.post("/api/codegen/generate/project", json=payload).json()
        assert "name" in data

    def test_project_response_has_files(self, client):
        payload = {"prompt": "A web app", "project_type": "web"}
        data = client.post("/api/codegen/generate/project", json=payload).json()
        assert "files" in data
        assert isinstance(data["files"], list)
        assert len(data["files"]) > 0

    def test_project_files_have_path_and_content(self, client):
        payload = {"prompt": "A simple service"}
        data = client.post("/api/codegen/generate/project", json=payload).json()
        for f in data["files"]:
            assert "path" in f
            assert "content" in f

    @pytest.mark.parametrize("language", ["typescript", "javascript", "python"])
    def test_project_accepts_different_languages(self, client, language):
        payload = {"prompt": "A REST API", "language": language}
        response = client.post("/api/codegen/generate/project", json=payload)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/codegen/validate  (query-parameter endpoint)
# ---------------------------------------------------------------------------


class TestValidateCode:
    def test_validate_valid_python_returns_200(self, client):
        response = client.post(
            "/api/codegen/validate",
            params={"code": "def foo():\n    return 1", "language": "python"},
        )
        assert response.status_code == 200

    def test_validate_empty_code_is_invalid(self, client):
        response = client.post(
            "/api/codegen/validate",
            params={"code": "   ", "language": "python"},
        )
        data = response.json()
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_validate_response_has_warnings(self, client):
        response = client.post(
            "/api/codegen/validate",
            params={"code": "import os", "language": "python"},
        )
        data = response.json()
        assert "warnings" in data

    def test_validate_javascript_var_triggers_warning(self, client):
        response = client.post(
            "/api/codegen/validate",
            params={"code": "var x = 1;", "language": "javascript"},
        )
        data = response.json()
        assert any(
            "var" in w.lower() or "const" in w.lower() for w in data.get("warnings", [])
        )

    @pytest.mark.parametrize("language", ["python", "typescript", "javascript"])
    def test_validate_accepts_known_languages(self, client, language):
        response = client.post(
            "/api/codegen/validate",
            params={"code": "// code", "language": language},
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/codegen/refactor  (query-parameter endpoint)
# ---------------------------------------------------------------------------


class TestRefactorCode:
    def test_refactor_returns_200(self, client):
        response = client.post(
            "/api/codegen/refactor",
            params={
                "code": "def foo(): return 1",
                "instructions": "Add type hints",
                "language": "python",
            },
        )
        assert response.status_code == 200

    def test_refactor_response_has_code(self, client):
        response = client.post(
            "/api/codegen/refactor",
            params={
                "code": "function foo() {}",
                "instructions": "Convert to arrow function",
                "language": "javascript",
            },
        )
        data = response.json()
        assert "code" in data
        assert len(data["code"]) > 0

    @pytest.mark.parametrize("language", ["python", "typescript", "javascript"])
    def test_refactor_preserves_original_code_in_dev_mode(self, client, language):
        original = "x = 1" if language == "python" else "const x = 1;"
        response = client.post(
            "/api/codegen/refactor",
            params={
                "code": original,
                "instructions": "Rename x to value",
                "language": language,
            },
        )
        data = response.json()
        # Dev-mode fallback prepends instruction comment and includes original code
        assert original in data["code"]


# ---------------------------------------------------------------------------
# POST /api/codegen/explain  (query-parameter endpoint)
# ---------------------------------------------------------------------------


class TestExplainCode:
    def test_explain_returns_200(self, client):
        response = client.post(
            "/api/codegen/explain",
            params={"code": "def add(a, b): return a + b", "language": "python"},
        )
        assert response.status_code == 200

    def test_explain_response_has_explanation_key(self, client):
        response = client.post(
            "/api/codegen/explain",
            params={"code": "const x = 1;", "language": "typescript"},
        )
        data = response.json()
        assert "explanation" in data

    @pytest.mark.parametrize(
        "language", ["python", "typescript", "javascript", "react"]
    )
    def test_explain_mentions_language_in_dev_mode(self, client, language):
        response = client.post(
            "/api/codegen/explain",
            params={"code": "// some code", "language": language},
        )
        data = response.json()
        assert language in data["explanation"].lower()


# ---------------------------------------------------------------------------
# GET /api/codegen/os-pipeline/info
# ---------------------------------------------------------------------------


class TestOSPipelineInfo:
    def test_pipeline_info_returns_200(self, client):
        with patch("api.codegen_routes.get_os_pipeline", return_value=(None, None)):
            response = client.get("/api/codegen/os-pipeline/info")
        assert response.status_code == 200

    def test_pipeline_info_has_stages(self, client):
        with patch("api.codegen_routes.get_os_pipeline", return_value=(None, None)):
            data = client.get("/api/codegen/os-pipeline/info").json()
        assert "stages" in data
        assert isinstance(data["stages"], list)
        assert len(data["stages"]) > 0

    def test_pipeline_info_has_name_and_version(self, client):
        with patch("api.codegen_routes.get_os_pipeline", return_value=(None, None)):
            data = client.get("/api/codegen/os-pipeline/info").json()
        assert "name" in data
        assert "version" in data


# ---------------------------------------------------------------------------
# POST /api/codegen/test/generate  (query-parameter endpoint)
# ---------------------------------------------------------------------------


class TestGenerateTests:
    def test_generate_tests_returns_200(self, client):
        response = client.post(
            "/api/codegen/test/generate",
            params={"code": "def add(a, b): return a + b", "language": "python"},
        )
        assert response.status_code == 200

    def test_generate_tests_returns_test_code(self, client):
        response = client.post(
            "/api/codegen/test/generate",
            params={"code": "def foo(): pass", "language": "python"},
        )
        data = response.json()
        assert "test_code" in data
        assert len(data["test_code"]) > 0

    def test_generate_tests_returns_framework(self, client):
        response = client.post(
            "/api/codegen/test/generate",
            params={"code": "def foo(): pass", "language": "python"},
        )
        data = response.json()
        assert "framework" in data
        assert data["framework"] == "pytest"

    @pytest.mark.parametrize(
        "language,expected_framework",
        [
            ("python", "pytest"),
            ("typescript", "vitest"),
            ("javascript", "jest"),
        ],
    )
    def test_generate_tests_uses_correct_framework(
        self, client, language, expected_framework
    ):
        response = client.post(
            "/api/codegen/test/generate",
            params={"code": "// some code", "language": language},
        )
        data = response.json()
        assert data["framework"] == expected_framework
