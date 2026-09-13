"""Tests for FigmaService — Figma import and conversion."""

import pytest
from services.figma_service import get_figma_service


class TestFigmaService:
    def _svc(self):
        return get_figma_service()

    def test_parse_valid_url(self):
        result = self._svc().parse_url("https://figma.com/file/ABC123/my-design")
        assert result["file_key"] == "ABC123"

    def test_parse_design_url(self):
        result = self._svc().parse_url("https://figma.com/design/XYZ789/test")
        assert result["file_key"] == "XYZ789"

    def test_parse_invalid_url(self):
        result = self._svc().parse_url("https://example.com")
        assert result["file_key"] is None

    @pytest.mark.asyncio
    async def test_get_frames(self):
        frames = await self._svc().get_frames("https://figma.com/file/ABC/test")
        assert len(frames) == 3
        assert all("id" in f for f in frames)

    @pytest.mark.asyncio
    async def test_get_frames_invalid(self):
        frames = await self._svc().get_frames("https://example.com")
        assert frames == []

    @pytest.mark.asyncio
    async def test_convert_frames(self):
        files = await self._svc().convert_frames(
            "https://figma.com/file/ABC/test", ["frame-1", "frame-2"]
        )
        assert len(files) == 2
        assert all(".tsx" in k for k in files.keys())

    @pytest.mark.asyncio
    async def test_convert_empty_frames(self):
        files = await self._svc().convert_frames("https://figma.com/file/ABC/test", [])
        assert files == {}
