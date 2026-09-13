"""Model management API routes — Phase 6.4.2.

Endpoints:
    POST   /api/models/download    — Start model download (returns SSE stream)
    GET    /api/models/             — List all registered models
    GET    /api/models/{model_id}   — Get model metadata
    DELETE /api/models/{model_id}   — Remove model from registry + disk
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from middleware.auth import get_current_user, AuthenticatedUser
from services.model_manager import (
    activate_on_slot,
    download_model,
    get_model_registry,
    progress_to_sse,
    ModelEntry,
    build_catalog_view,
    probe_host_capability,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/models", tags=["models"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class DownloadRequest(BaseModel):
    """Request body for model download."""

    url: str = Field(
        ...,
        description="HTTPS URL to the model file (e.g. HuggingFace direct link)",
        max_length=2048,
    )
    model_id: str = Field(
        ...,
        description="Unique identifier for this model (alnum, dots, dashes, underscores)",
        pattern=r"^[a-zA-Z0-9._-]{1,128}$",
        max_length=128,
    )
    expected_sha256: Optional[str] = Field(
        None,
        description="Expected SHA-256 hex digest for verification",
        pattern=r"^[a-fA-F0-9]{64}$",
    )
    label: Optional[str] = Field(
        None,
        description="Human-readable model name (max 32 chars)",
        max_length=32,
    )


class ModelResponse(BaseModel):
    """Model metadata response."""

    model_id: str
    filename: str
    format_id: int
    quant_label: str
    total_size: int
    sha256: str
    source_url: str
    downloaded_at: float
    label: str
    verified: bool
    active: bool
    slot_id: Optional[int]
    # InstructKR Provenance (populated after kernel mapping)
    kernel_mapped_at: Optional[float] = None
    kernel_base_addr: Optional[str] = None
    kernel_xxh3: Optional[str] = None
    kernel_crc32c: Optional[str] = None
    kernel_latency_ms: Optional[float] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry_to_response(e: ModelEntry) -> ModelResponse:
    """Map a ModelEntry dataclass to the API response schema."""
    return ModelResponse(
        model_id=e.model_id,
        filename=e.filename,
        format_id=e.format_id,
        quant_label=e.quant_label,
        total_size=e.total_size,
        sha256=e.sha256,
        source_url=e.source_url,
        downloaded_at=e.downloaded_at,
        label=e.label,
        verified=e.verified,
        active=e.active,
        slot_id=e.slot_id,
        kernel_mapped_at=e.kernel_mapped_at,
        kernel_base_addr=e.kernel_base_addr,
        kernel_xxh3=e.kernel_xxh3,
        kernel_crc32c=e.kernel_crc32c,
        kernel_latency_ms=e.kernel_latency_ms,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/download",
    summary="Download model with SHA-256 verification",
    description=(
        "Streams model from HTTPS URL with on-the-fly SHA-256 hashing. "
        "Returns SSE progress events. On success, model is registered locally."
    ),
)
async def start_download(
    request: DownloadRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Start a model download and return SSE progress stream."""
    registry = get_model_registry()

    # Check if model_id already exists
    existing = await registry.get(request.model_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Model '{request.model_id}' already registered. "
            f"Delete it first or use a different model_id.",
        )

    async def generate():
        async for progress in download_model(
            url=request.url,
            model_id=request.model_id,
            registry=registry,
            expected_sha256=request.expected_sha256,
            label=request.label,
        ):
            yield progress_to_sse(progress)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
    )


@router.get(
    "/",
    summary="List registered models",
    response_model=list[ModelResponse],
)
async def list_models(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return all locally registered models."""
    registry = get_model_registry()
    entries = await registry.list_models()
    return [_entry_to_response(e) for e in entries]


@router.get(
    "/catalog",
    summary="Curated catalog with hardware-aware status",
)
async def get_catalog(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """W7.2 — Return the curated open-weight catalog with a per-entry
    `status` and the live `host` snapshot used to compute it.

    Per-entry `status` values:
      * `local_ready`         — file present on disk + size matches.
      * `available_lan`       — host can run + at least one workspace peer.
      * `available_https`     — host can run, no peer, HTTPS pullable.
      * `cloud_fallback_only` — host tier < entry tier (cloud only).

    The frontend's `useChat.ts` and the chat model selector consume
    this endpoint and render a colored badge per option.
    """
    try:
        return build_catalog_view()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/host_capability",
    summary="Host hardware/VRAM snapshot",
)
async def get_host_capability(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """W7.2 — Return just the host probe (no catalog). Useful for the
    UI's hardware-status panel when it doesn't need the full model list."""
    return probe_host_capability().to_dict()


@router.get(
    "/{model_id}",
    summary="Get model details",
    response_model=ModelResponse,
)
async def get_model(
    model_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return metadata for a specific model."""
    registry = get_model_registry()
    entry = await registry.get(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    return _entry_to_response(entry)


@router.delete(
    "/{model_id}",
    summary="Remove model",
)
async def delete_model(
    model_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Remove model from registry and delete weight file from disk."""
    registry = get_model_registry()
    entry = await registry.get(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    if entry.active:
        raise HTTPException(
            status_code=409,
            detail=f"Model '{model_id}' is active on slot {entry.slot_id}. "
            "Deactivate it first.",
        )
    removed = await registry.remove(model_id)
    if not removed:
        raise HTTPException(status_code=500, detail="Failed to remove model")
    return {"status": "ok", "model_id": model_id, "message": "Model removed"}


# ---------------------------------------------------------------------------
# Slot activation — SLOT_START → Warp Transfer → SLOT_FINISH
# ---------------------------------------------------------------------------


class ActivateRequest(BaseModel):
    """Request body for model activation on a VBus slot."""

    slot_id: int = Field(
        ...,
        ge=1,
        le=7,
        description="Kernel slot ID (1-7; slot 0 is reserved for Coordinator)",
    )


class ActivateResponse(BaseModel):
    """Response after successful model activation."""

    model_id: str
    slot_id: int
    base_addr: str
    size: int
    xxh3: str
    crc32c: str
    elapsed_ms: float
    warp: bool
    hall_assigned: bool


@router.post(
    "/{model_id}/activate",
    summary="Load model into kernel VBus slot",
    description=(
        "Transfers a downloaded model into a kernel AI slot via ivshmem Warp Drive "
        "(or VBus burst fallback). After SLOT_FINISH, the kernel sets PTEs to "
        "READ-ONLY + AI_PROTECTED + NO_EXECUTE — model weights become "
        "hardware-immutable in the V-Palace WEIGHT Hall."
    ),
    response_model=ActivateResponse,
)
async def activate_model(
    model_id: str,
    request: ActivateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Activate a model on a VBus kernel slot."""
    registry = get_model_registry()

    entry = await registry.get(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    if entry.active:
        raise HTTPException(
            status_code=409,
            detail=f"Model '{model_id}' already active on slot {entry.slot_id}",
        )

    try:
        result = await activate_on_slot(
            model_id=model_id,
            slot_id=request.slot_id,
            registry=registry,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return ActivateResponse(
        model_id=result.model_id,
        slot_id=result.slot_id,
        base_addr=result.base_addr,
        size=result.size,
        xxh3=result.xxh3,
        crc32c=result.crc32c,
        elapsed_ms=result.elapsed_ms,
        warp=result.warp,
        hall_assigned=result.hall_assigned,
    )


@router.post(
    "/{model_id}/deactivate",
    summary="Release model from kernel slot",
)
async def deactivate_model(
    model_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Release a model's VBus slot and mark it inactive."""
    registry = get_model_registry()

    entry = await registry.get(model_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    if not entry.active:
        raise HTTPException(status_code=409, detail=f"Model '{model_id}' is not active")

    # Reset slot in kernel
    try:
        from services.vbus_driver import VBusDriver

        driver = VBusDriver()
        if driver.connect():
            driver.slot_reset(entry.slot_id)
            driver.disconnect()
    except Exception as exc:
        logger.warning(
            "Slot reset failed for %s (slot %d): %s", model_id, entry.slot_id, exc
        )

    await registry.set_inactive(model_id)
    return {"status": "ok", "model_id": model_id, "message": "Model deactivated"}
