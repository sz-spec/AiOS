"""
Vision Analysis Routes — Phase 3.5
====================================
SSE-streaming vision analysis endpoint.

Streams phase-by-phase insights as the pipeline runs:
  validate → analyze → contract → theme → complete
"""

import base64
import json
import logging
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from typing import Optional

from api.deps import get_current_user, AuthenticatedUser

logger = logging.getLogger("vos3.vision_routes")

router = APIRouter(prefix="/api/v1/vision", tags=["Vision"])


def sse_event(data: dict) -> str:
    """Format a dict as an SSE event."""
    return f"data: {json.dumps(data)}\n\n"


@router.post("/analyze")
async def analyze_design(
    image: Optional[UploadFile] = File(None),
    requirements: str = Form(""),
    style: Optional[str] = Form(None),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Stream vision analysis insights as SSE events.

    Accepts either an uploaded image or a style preset name.
    Returns a streaming SSE response with phase-by-phase insights.
    """

    async def generate():
        from ai.agents.vision_agent import (
            VisionAgent,
            VisionAnalysis,
            VisionAnalysisError,
            DesignContract,
            ColorPalette,
            Typography,
        )
        from services.theme_engine import ThemeEngine

        agent = VisionAgent()
        image_b64 = None

        # Phase 1: Validate
        yield sse_event({"phase": "validate", "message": "Validating input..."})

        if image and image.filename:
            try:
                raw_bytes = await image.read()
                if len(raw_bytes) > agent.MAX_IMAGE_BYTES:
                    yield sse_event(
                        {
                            "phase": "error",
                            "message": f"Image too large ({len(raw_bytes)} bytes, max {agent.MAX_IMAGE_BYTES})",
                        }
                    )
                    return
                image_b64 = base64.b64encode(raw_bytes).decode("ascii")
                yield sse_event({"phase": "validate", "message": "Image validated"})
            except Exception as e:
                yield sse_event(
                    {"phase": "error", "message": f"Image read failed: {e}"}
                )
                return
        elif style:
            yield sse_event(
                {"phase": "validate", "message": f"Using style preset: {style}"}
            )
        else:
            yield sse_event({"phase": "error", "message": "No image or style provided"})
            return

        # Phase 2: Analyze
        yield sse_event({"phase": "analyze", "message": "Extracting layout regions..."})

        analysis = None
        if image_b64:
            try:
                analysis = await agent.analyze(image_b64, requirements)
                yield sse_event(
                    {
                        "phase": "analyze",
                        "message": f"Found {len(analysis.components)} components, {len(analysis.layout_regions)} regions",
                    }
                )
            except (VisionAnalysisError, ValueError) as e:
                logger.warning("[VISION_ROUTE] Analysis failed: %s", e)
                yield sse_event(
                    {"phase": "analyze", "message": f"Analysis failed: {e}"}
                )
                # Fall through to style-based generation

        # If no image analysis, build a default VisionAnalysis from the style preset
        if analysis is None and style:
            analysis = VisionAnalysis(
                overall_style=style,
                page_type="dashboard",
                confidence=0.5,
                palette=ColorPalette(),
                typography=Typography(),
            )
            yield sse_event(
                {"phase": "analyze", "message": f"Built from style preset: {style}"}
            )

        if analysis is None:
            yield sse_event({"phase": "error", "message": "No analysis produced"})
            return

        # Phase 3: Contract
        yield sse_event({"phase": "contract", "message": "Building design contract..."})
        try:
            contract = DesignContract.from_vision_analysis(analysis)
            yield sse_event(
                {
                    "phase": "contract",
                    "message": f"Mapped {len(contract.component_map)} components to shadcn/ui, {len(contract.color_tokens)} tokens",
                }
            )
        except Exception as e:
            logger.warning("[VISION_ROUTE] Contract build failed: %s", e)
            yield sse_event(
                {"phase": "error", "message": f"Contract validation failed: {e}"}
            )
            return

        # Phase 4: Theme
        yield sse_event({"phase": "theme", "message": "Generating Tailwind theme..."})
        try:
            theme = ThemeEngine.from_analysis(analysis)
            yield sse_event({"phase": "theme", "message": "Theme generated"})
        except Exception as e:
            logger.warning("[VISION_ROUTE] Theme generation failed: %s", e)
            yield sse_event(
                {"phase": "theme", "message": f"Theme generation failed: {e}"}
            )
            # Continue without theme
            theme = None

        # Phase 5: Complete
        result = {
            "phase": "complete",
            "contract": contract.model_dump(),
            "analysis": analysis.to_dict(),
        }
        if theme:
            result["theme"] = theme.to_dict()

        yield sse_event(result)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
