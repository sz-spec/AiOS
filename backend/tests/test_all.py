"""
Test Suite for AI App Builder
=============================
Comprehensive tests for all components.
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

# Import components to test
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config, ModelConfig
from src.errors import AIBuilderError, ModelError, ValidationError, RateLimitError
from src.cache import Cache, CacheEntry, MemoryCache, FileCache
from src.code_generator import (
    CodeParser,
    Language,
    HTMLValidator,
    PythonValidator,
    CodeGenerator,
    GeneratedProject,
)

# =============================================================================
# Config Tests
# =============================================================================


class TestConfig:
    """Tests for configuration management."""

    def test_default_config(self):
        """Test default configuration values."""
        config = Config()

        assert config.primary_model.name == "gpt-4o"
        assert config.primary_model.temperature == 0.7
        assert config.cache.enabled == True
        assert config.git.enabled == True

    def test_config_validation(self):
        """Test configuration validation."""
        config = Config(openai_api_key="test-key")
        result = config.validate()

        assert result["valid"] == True
        assert len(result["issues"]) == 0

    def test_config_validation_no_keys(self):
        """Test validation fails without API keys."""
        config = Config(openai_api_key="", anthropic_api_key="")
        result = config.validate()

        assert result["valid"] == False
        assert any("API key" in issue for issue in result["issues"])

    def test_model_config(self):
        """Test model configuration."""
        model = ModelConfig(name="gpt-4o", temperature=0.5, max_tokens=2048)

        assert model.name == "gpt-4o"
        assert model.temperature == 0.5
        assert model.max_tokens == 2048


# =============================================================================
# Error Tests
# =============================================================================


class TestErrors:
    """Tests for error handling."""

    def test_base_error(self):
        """Test base error class."""
        error = AIBuilderError("Test error", "TEST_CODE", {"key": "value"})

        assert error.message == "Test error"
        assert error.code == "TEST_CODE"
        assert error.details == {"key": "value"}

        error_dict = error.to_dict()
        assert error_dict["error"] == "TEST_CODE"
        assert error_dict["message"] == "Test error"

    def test_model_error(self):
        """Test model error."""
        error = ModelError("Model failed", "gpt-4o")

        assert error.code == "MODEL_ERROR"
        assert error.model_name == "gpt-4o"

    def test_validation_error(self):
        """Test validation error."""
        error = ValidationError("Validation failed", "syntax", ["Error 1", "Error 2"])

        assert error.code == "VALIDATION_ERROR"
        assert error.validation_type == "syntax"
        assert len(error.errors) == 2

    def test_rate_limit_error(self):
        """Test rate limit error."""
        error = RateLimitError("Rate limited", retry_after=60)

        assert error.code == "RATE_LIMIT"
        assert error.retry_after == 60


# =============================================================================
# Cache Tests
# =============================================================================


class TestCache:
    """Tests for caching system."""

    def test_memory_cache_set_get(self):
        """Test memory cache basic operations."""
        cache = MemoryCache(max_size=100)

        entry = CacheEntry(
            key="test", value="test_value", created_at=0, expires_at=float("inf")
        )

        cache.set(entry)
        result = cache.get("test")

        assert result is not None
        assert result.value == "test_value"

    def test_memory_cache_expiration(self):
        """Test cache expiration."""
        cache = MemoryCache()

        entry = CacheEntry(
            key="expired", value="test", created_at=0, expires_at=0  # Already expired
        )

        cache.set(entry)
        result = cache.get("expired")

        assert result is None

    def test_memory_cache_eviction(self):
        """Test LRU eviction."""
        cache = MemoryCache(max_size=2)

        for i in range(3):
            entry = CacheEntry(
                key=f"key_{i}",
                value=f"value_{i}",
                created_at=i,
                expires_at=float("inf"),
            )
            cache.set(entry)

        # First entry should be evicted
        assert cache.get("key_0") is None
        assert cache.get("key_1") is not None
        assert cache.get("key_2") is not None

    def test_cache_key_generation(self):
        """Test cache key generation."""
        key1 = Cache.generate_key("arg1", "arg2", kwarg1="value1")
        key2 = Cache.generate_key("arg1", "arg2", kwarg1="value1")
        key3 = Cache.generate_key("arg1", "arg2", kwarg1="value2")

        assert key1 == key2
        assert key1 != key3

    def test_file_cache(self):
        """Test file-based cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = FileCache(Path(tmpdir), max_size=100)

            entry = CacheEntry(
                key="file_test",
                value={"data": "test"},
                created_at=0,
                expires_at=float("inf"),
            )

            cache.set(entry)
            result = cache.get("file_test")

            assert result is not None
            assert result.value == {"data": "test"}


# =============================================================================
# Code Parser Tests
# =============================================================================


class TestCodeParser:
    """Tests for code parsing."""

    def test_parse_markdown_code_block(self):
        """Test parsing markdown code blocks."""
        text = """
Here's some code:

```javascript
function hello() {
    console.log("Hello!");
}
```

And more text.
"""
        blocks = CodeParser.parse(text)

        assert len(blocks) == 1
        assert blocks[0].language == Language.JAVASCRIPT
        assert "function hello" in blocks[0].content

    def test_parse_multiple_blocks(self):
        """Test parsing multiple code blocks."""
        text = """
```html
<div>Test</div>
```

```css
.test { color: red; }
```

```javascript
console.log("test");
```
"""
        blocks = CodeParser.parse(text)

        assert len(blocks) == 3
        assert blocks[0].language == Language.HTML
        assert blocks[1].language == Language.CSS
        assert blocks[2].language == Language.JAVASCRIPT

    def test_parse_python_block(self):
        """Test parsing Python code."""
        text = """
```python
def main():
    print("Hello")

if __name__ == "__main__":
    main()
```
"""
        blocks = CodeParser.parse(text)

        assert len(blocks) == 1
        assert blocks[0].language == Language.PYTHON

    def test_parse_html_fallback(self):
        """Test HTML detection when no code blocks."""
        text = """
<html>
<head><title>Test</title></head>
<body>Hello</body>
</html>
"""
        blocks = CodeParser.parse(text)

        assert len(blocks) == 1
        assert blocks[0].language == Language.HTML


# =============================================================================
# Validation Tests
# =============================================================================


class TestValidation:
    """Tests for code validation."""

    def test_html_validation_valid(self):
        """Test valid HTML validation."""
        html = """
<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body><p>Hello</p></body>
</html>
"""
        validator = HTMLValidator()
        result = validator.validate(html)

        assert result.valid == True
        assert len(result.errors) == 0

    def test_html_validation_missing_doctype(self):
        """Test HTML missing DOCTYPE."""
        html = "<html><body>Test</body></html>"

        validator = HTMLValidator()
        result = validator.validate(html)

        assert "DOCTYPE" in " ".join(result.warnings)

    def test_python_validation_valid(self):
        """Test valid Python validation."""
        code = """
def hello():
    print("Hello, World!")

if __name__ == "__main__":
    hello()
"""
        validator = PythonValidator()
        result = validator.validate(code)

        assert result.valid == True

    def test_python_validation_syntax_error(self):
        """Test Python with syntax error."""
        code = """
def broken(
    print("missing paren"
"""
        validator = PythonValidator()
        result = validator.validate(code)

        assert result.valid == False
        assert len(result.errors) > 0

    def test_python_validation_warnings(self):
        """Test Python best practice warnings."""
        code = """
from os import *

try:
    x = 1
except:
    pass
"""
        validator = PythonValidator()
        result = validator.validate(code)

        assert len(result.warnings) > 0


# =============================================================================
# Generated Project Tests
# =============================================================================


class TestGeneratedProject:
    """Tests for GeneratedProject."""

    def test_project_save(self):
        """Test saving project to disk."""
        project = GeneratedProject(
            files={
                "index.html": "<html><body>Test</body></html>",
                "style.css": "body { color: red; }",
                "app.js": "console.log('test');",
            },
            entry_point="index.html",
            language=Language.HTML,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test_project"
            project.save(output_path)

            assert (output_path / "index.html").exists()
            assert (output_path / "style.css").exists()
            assert (output_path / "app.js").exists()

    def test_project_nested_files(self):
        """Test saving nested file structure."""
        project = GeneratedProject(
            files={
                "src/components/Button.jsx": "export const Button = () => <button/>;",
                "src/App.jsx": "import { Button } from './components/Button';",
                "public/index.html": "<html></html>",
            },
            entry_point="src/App.jsx",
            language=Language.REACT,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "react_project"
            project.save(output_path)

            assert (output_path / "src" / "components" / "Button.jsx").exists()
            assert (output_path / "src" / "App.jsx").exists()
            assert (output_path / "public" / "index.html").exists()


# =============================================================================
# Integration Tests
# =============================================================================


class TestCodeGeneratorIntegration:
    """Integration tests for CodeGenerator (requires mocking LLM)."""

    @patch("src.code_generator.LLM")
    def test_generate_simple_html(self, mock_llm_class):
        """Test generating simple HTML."""
        # Mock LLM response
        mock_llm = Mock()
        mock_llm.generate.return_value = Mock(content="""
```html
<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body><h1>Hello</h1></body>
</html>
```
""")
        mock_llm_class.return_value = mock_llm

        generator = CodeGenerator(llm=mock_llm)
        project = generator.generate(
            "Create a simple hello world page", target_language=Language.HTML
        )

        assert len(project.files) > 0
        assert any("html" in f for f in project.files)

    @patch("src.code_generator.LLM")
    def test_generate_with_validation_fix(self, mock_llm_class):
        """Test generation with validation and fix loop."""
        # First response has error, second is fixed
        mock_llm = Mock()
        mock_llm.generate.side_effect = [
            Mock(content="```html\n<html><body>Missing doctype</body></html>\n```"),
            Mock(
                content="```html\n<!DOCTYPE html>\n<html><head><title>Fixed</title></head><body>Fixed</body></html>\n```"
            ),
        ]
        mock_llm_class.return_value = mock_llm

        generator = CodeGenerator(llm=mock_llm, max_iterations=2)
        project = generator.generate("Create a page", target_language=Language.HTML)

        assert project is not None


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
