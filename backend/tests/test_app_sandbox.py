"""Tests for services/app_sandbox.py — Process Sandbox."""

import pytest

from services.app_sandbox import ProcessSandbox, SandboxResult, get_sandbox


class TestProcessSandbox:
    def setup_method(self):
        self.sandbox = ProcessSandbox(timeout_s=5.0, max_memory_mb=64)

    @pytest.mark.asyncio
    async def test_execute_simple_code(self):
        result = await self.sandbox.execute("result = 2 + 2", {})
        assert result.success is True
        assert result.output == 4

    @pytest.mark.asyncio
    async def test_execute_with_context(self):
        result = await self.sandbox.execute("result = context['x'] * 2", {"x": 21})
        assert result.success is True
        assert result.output == 42

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self):
        sandbox = ProcessSandbox(timeout_s=1.0)
        result = await sandbox.execute("import time; time.sleep(10)", {})
        assert result.success is False
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_capture_stderr_on_error(self):
        result = await self.sandbox.execute("raise ValueError('boom')", {})
        assert result.success is False
        assert "boom" in result.error

    @pytest.mark.asyncio
    async def test_syntax_error(self):
        result = await self.sandbox.execute("def bad(:", {})
        assert result.success is False
        assert result.error  # Should have error content

    @pytest.mark.asyncio
    async def test_empty_code(self):
        result = await self.sandbox.execute("", {})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_duration_tracked(self):
        result = await self.sandbox.execute("result = 1", {})
        assert result.duration_ms > 0


class TestSandboxResult:
    def test_success_result(self):
        r = SandboxResult(success=True, output=42, duration_ms=10.0)
        assert r.success is True
        assert r.output == 42
        assert r.error is None

    def test_error_result(self):
        r = SandboxResult(success=False, error="timeout")
        assert r.success is False
        assert r.error == "timeout"


class TestGetSandbox:
    def test_singleton(self):
        import services.app_sandbox as mod

        mod._sandbox = None
        s1 = get_sandbox()
        s2 = get_sandbox()
        assert s1 is s2
        mod._sandbox = None
