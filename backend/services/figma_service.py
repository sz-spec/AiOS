"""
Figma Service
=============
Figma API client for importing designs and converting to code.
"""

from typing import Dict, List, Optional
import re


class FigmaService:
    """Figma API integration for design-to-code conversion."""

    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token

    def parse_url(self, url: str) -> Dict:
        """Extract file key and node IDs from Figma URL."""
        match = re.search(r"figma\.com/(?:file|design)/([a-zA-Z0-9]+)", url)
        file_key = match.group(1) if match else None
        return {"file_key": file_key, "url": url}

    async def get_frames(self, url: str) -> List[Dict]:
        """Get top-level frames from a Figma file."""
        parsed = self.parse_url(url)
        if not parsed["file_key"]:
            return []

        # In production: call Figma API
        # GET https://api.figma.com/v1/files/{file_key}
        # Headers: X-Figma-Token: {access_token}

        # Return mock frames for dev mode
        return [
            {"id": "frame-1", "name": "Homepage", "thumbnail": ""},
            {"id": "frame-2", "name": "About", "thumbnail": ""},
            {"id": "frame-3", "name": "Contact", "thumbnail": ""},
        ]

    async def convert_frames(self, url: str, frame_ids: List[str]) -> Dict[str, str]:
        """Convert selected Figma frames to React/Tailwind code."""
        # In production: This calls the DesignerAgent which:
        # 1. Fetches frame JSON from Figma API
        # 2. Analyzes layout, colors, typography, spacing
        # 3. Generates React + Tailwind components

        files = {}
        for frame_id in frame_ids:
            component_name = f"FigmaComponent_{frame_id.replace('-', '_')}"
            files[
                f"src/components/{component_name}.tsx"
            ] = f"""import React from 'react';

export default function {component_name}() {{
  return (
    <div className="min-h-screen bg-white">
      <div className="max-w-6xl mx-auto px-6 py-12">
        <h1 className="text-4xl font-bold mb-4">Imported from Figma</h1>
        <p className="text-gray-600">This component was generated from frame {frame_id}.</p>
      </div>
    </div>
  );
}}
"""
        return files


_service: Optional[FigmaService] = None


def get_figma_service() -> FigmaService:
    global _service
    if _service is None:
        import os

        _service = FigmaService(access_token=os.environ.get("FIGMA_ACCESS_TOKEN"))
    return _service
