"""
backend/services/app_media.py — Sovereign multi-modal pipeline (P5.6).

The Sovereign App Runtime is general-purpose: any of the 50-1000
AI workers a single host may run could be a vision agent, a designer,
or a finance analyst that needs to OCR a receipt. P5.6 gives them
two capabilities — both local-first, both sandboxed:

  * `LocalMediaService.generate_image()` — synthesizes an image
    inside the calling app's filesystem sandbox.
  * `LocalMediaService.process_vision()`  — accepts image bytes,
    returns a structured transcript / OCR result.

Resiliency tiers
----------------
Each method probes for backends in priority order. The first one
that's importable AND functional is used; otherwise we fall back
to a deterministic synthetic path. The test suite always reaches
the synthetic path on hosts without ML libraries, so coverage stays
green everywhere.

  generate_image:
    1. diffusers + torch     (local Stable Diffusion; rarely present)
    2. Pillow                (lightweight PIL render — pretty common)
    3. synthetic (stdlib)    (1x1 PNG with prompt embedded as tEXt)

  process_vision:
    1. Ollama llava          (loopback HTTP; allowed by air-gap)
    2. easyocr / pytesseract (local OCR libraries)
    3. synthetic (stdlib)    (deterministic hash-keyed transcript)

Scope enforcement
-----------------
  generate_image  → PERMISSION_GATE.check(app_id, "media.write")
  process_vision  → PERMISSION_GATE.check(app_id, "media.read")

The route layer wraps both with `get_app_context` so an
unauthenticated child or a mismatched X-App-Id never reaches the
service in the first place.

Output filesystem layout
------------------------
Generated images land at `<sandbox>/media/generated/<uuid>.png`
inside the calling app's sandbox. The synthesizer writes via the
same `os.open(O_NOFOLLOW)` path as the rest of the filesystem
sandbox, so a malicious diffusers pipeline that tries to write
outside its sandbox via a symlink is still caught.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import struct
import time
import uuid
import zlib
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException

from services.app_filesystem import (
    _resolve_inside_sandbox,
)
from services.app_sandbox import (
    PERMISSION_GATE,
    AppIsolated,
    AppNotFound,
    ScopeViolation,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------


# Hard cap on uploaded image size to keep the route from being a
# memory-amplification vector. Matches the P4.3 file-write cap.
MAX_IMAGE_BYTES = int(os.getenv("VOS3_APP_MEDIA_MAX_BYTES", str(10 * 1024 * 1024)))

# Resolution bounds — keeps a synthetic-mode "generate me 65535×65535"
# request from blowing up the test machine's memory.
MIN_RESOLUTION = 16
MAX_RESOLUTION = 2048


# ---------------------------------------------------------------------------
# Backend probes — lazy + cached
# ---------------------------------------------------------------------------


def _probe_pillow() -> Any:
    """Return the PIL module if importable, else None. Cached so
    repeated calls don't pay the import cost."""
    if not hasattr(_probe_pillow, "_cache"):
        try:
            from PIL import Image as _Image  # noqa: F401

            _probe_pillow._cache = _Image  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            _probe_pillow._cache = None  # type: ignore[attr-defined]
    return _probe_pillow._cache  # type: ignore[attr-defined]


def _probe_diffusers() -> Any:
    if not hasattr(_probe_diffusers, "_cache"):
        try:
            import diffusers  # noqa: F401
            import torch  # noqa: F401

            _probe_diffusers._cache = True  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            _probe_diffusers._cache = False  # type: ignore[attr-defined]
    return _probe_diffusers._cache  # type: ignore[attr-defined]


def _probe_ocr() -> str:
    """Return the name of the available OCR backend, or "" if none."""
    if not hasattr(_probe_ocr, "_cache"):
        try:
            import easyocr  # noqa: F401

            _probe_ocr._cache = "easyocr"  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            try:
                import pytesseract  # noqa: F401

                _probe_ocr._cache = "pytesseract"  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                _probe_ocr._cache = ""  # type: ignore[attr-defined]
    return _probe_ocr._cache  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Scope plumbing — translates ScopeViolation into HTTPException(403)
# ---------------------------------------------------------------------------


def _enforce_media_scope(app_id: str, scope: str) -> None:
    try:
        PERMISSION_GATE.check(app_id, scope)
    except ScopeViolation as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "scope_violation",
                "app_id": exc.app_id,
                "scope": exc.scope,
                "reason": exc.reason,
            },
        ) from exc
    except (AppIsolated, AppNotFound) as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "app_unauthorized", "reason": str(exc)},
        ) from exc


# ---------------------------------------------------------------------------
# Synthetic PNG generator — stdlib only
# ---------------------------------------------------------------------------


def _build_synthetic_png(*, width: int, height: int, prompt: str) -> bytes:
    """Hand-roll a minimal PNG with a single solid color + a tEXt
    chunk carrying the prompt.

    The color is derived from `sha256(prompt)[:3]` so the same
    prompt always produces the same image — perfect for snapshot
    tests. The tEXt chunk lets downstream tools read the prompt
    back without our metadata header."""
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    r, g, b = digest[0], digest[1], digest[2]

    # Build a row of pixels then repeat for each scanline.
    row = bytes([0]) + (bytes([r, g, b]) * width)  # filter byte + RGB
    raw = row * height

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),  # RGB8
    )
    idat = chunk(b"IDAT", zlib.compress(raw, 6))
    # tEXt: "prompt\0<prompt text>"; truncate to 1024 for safety.
    text_body = b"prompt\x00" + prompt.encode("utf-8", errors="replace")[:1024]
    text = chunk(b"tEXt", text_body)
    iend = chunk(b"IEND", b"")
    return signature + ihdr + idat + text + iend


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageGenerationResult:
    app_id: str
    prompt: str
    resolution: tuple
    output_path: str  # relative inside the app's sandbox
    size_bytes: int
    backend: str  # "diffusers" | "pillow" | "synthetic"
    generated_at_ms: int

    def to_dict(self) -> dict:
        return {
            "app_id": self.app_id,
            "prompt": self.prompt,
            "resolution": list(self.resolution),
            "output_path": self.output_path,
            "size_bytes": self.size_bytes,
            "backend": self.backend,
            "generated_at_ms": self.generated_at_ms,
        }


@dataclass(frozen=True)
class VisionResult:
    app_id: str
    task: str  # "ocr" | "describe" | "classify"
    transcript: str
    confidence: float
    backend: str  # "ollama" | "easyocr" | "pytesseract" | "synthetic"
    processed_at_ms: int
    image_sha256: str

    def to_dict(self) -> dict:
        return {
            "app_id": self.app_id,
            "task": self.task,
            "transcript": self.transcript,
            "confidence": self.confidence,
            "backend": self.backend,
            "processed_at_ms": self.processed_at_ms,
            "image_sha256": self.image_sha256,
        }


# ---------------------------------------------------------------------------
# LocalMediaService — the directive's surface
# ---------------------------------------------------------------------------


class LocalMediaService:
    """Sovereign multi-modal pipeline.

    Stateless — every call re-runs the gate + path-confinement
    checks. Backend selection is recomputed lazily so a host that
    later `pip install`s diffusers picks up the upgrade without a
    restart.
    """

    DEFAULT_RESOLUTION = (256, 256)
    GENERATED_DIR = "media/generated"

    # --- generate_image -----------------------------------------------

    def generate_image(
        self,
        app_id: str,
        prompt: str,
        resolution: Optional[tuple] = None,
    ) -> ImageGenerationResult:
        """Synthesize an image into the calling app's sandbox.

        Raises:
          HTTPException(403) — gate denied OR app isolated.
          ValueError — malformed prompt / resolution / oversized output.
          PathTraversalAttempt — should never trigger here (we own
                                  the output path), but the
                                  containment helper is used so
                                  ANY future change keeps the
                                  guarantee.
        """
        _enforce_media_scope(app_id, "media.write")

        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        prompt = prompt.strip()[:2048]  # avoid sending huge prompts through

        w, h = resolution or self.DEFAULT_RESOLUTION
        if not (
            isinstance(w, int)
            and isinstance(h, int)
            and MIN_RESOLUTION <= w <= MAX_RESOLUTION
            and MIN_RESOLUTION <= h <= MAX_RESOLUTION
        ):
            raise ValueError(
                f"resolution must be (int,int) with "
                f"{MIN_RESOLUTION}-{MAX_RESOLUTION} bounds"
            )

        backend, png_bytes = self._render_png(prompt=prompt, width=w, height=h)
        if len(png_bytes) > MAX_IMAGE_BYTES:
            raise ValueError(
                f"generated image size {len(png_bytes)} exceeds cap "
                f"{MAX_IMAGE_BYTES}"
            )

        # Write into the sandbox via the SAME O_NOFOLLOW path the
        # rest of the filesystem manager uses — no symlink trick
        # can redirect us outside the sandbox.
        rel_path = f"{self.GENERATED_DIR}/{uuid.uuid4().hex}.png"
        target = _resolve_inside_sandbox(app_id, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(target), flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(png_bytes)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

        logger.info(
            "[media] generated app=%s backend=%s prompt_hash=%s",
            app_id,
            backend,
            hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12],
        )
        return ImageGenerationResult(
            app_id=app_id,
            prompt=prompt,
            resolution=(w, h),
            output_path=rel_path,
            size_bytes=len(png_bytes),
            backend=backend,
            generated_at_ms=int(time.time() * 1000),
        )

    def _render_png(self, *, prompt: str, width: int, height: int) -> tuple:
        """Pick a backend in priority order and emit PNG bytes.
        Returns (backend_name, png_bytes).

        Each tier wraps the actual call in try/except so a transient
        backend failure on tier N falls through to tier N+1 cleanly."""

        if _probe_diffusers():
            try:
                import torch
                from diffusers import StableDiffusionPipeline

                pipe = StableDiffusionPipeline.from_pretrained(
                    "runwayml/stable-diffusion-v1-5",
                    safety_checker=None,
                )
                pipe = pipe.to("cpu")
                with torch.no_grad():
                    out = pipe(
                        prompt,
                        height=height,
                        width=width,
                        num_inference_steps=4,
                    )
                img = out.images[0]
                import io

                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return "diffusers", buf.getvalue()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[media] diffusers tier failed: %s", exc)

        pil = _probe_pillow()
        if pil is not None:
            try:
                import io

                digest = hashlib.sha256(prompt.encode("utf-8")).digest()
                color = (digest[0], digest[1], digest[2])
                img = pil.new("RGB", (width, height), color=color)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return "pillow", buf.getvalue()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[media] pillow tier failed: %s", exc)

        return "synthetic", _build_synthetic_png(
            width=width,
            height=height,
            prompt=prompt,
        )

    # --- process_vision -----------------------------------------------

    def process_vision(
        self,
        app_id: str,
        image_bytes: bytes,
        task: str = "ocr",
    ) -> VisionResult:
        """Convert image bytes to text via OCR / vision LLM.

        Raises:
          HTTPException(403) — gate denied.
          ValueError — empty image, oversized image, unknown task.
        """
        _enforce_media_scope(app_id, "media.read")

        if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
            raise ValueError("image_bytes must be non-empty bytes")
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError(
                f"image size {len(image_bytes)} exceeds cap {MAX_IMAGE_BYTES}"
            )
        if task not in ("ocr", "describe", "classify"):
            raise ValueError("task must be 'ocr', 'describe', or 'classify'")

        backend, transcript, confidence = self._run_vision(
            image_bytes=bytes(image_bytes),
            task=task,
        )
        image_sha = hashlib.sha256(image_bytes).hexdigest()
        logger.info(
            "[media] processed_vision app=%s backend=%s task=%s img=%s",
            app_id,
            backend,
            task,
            image_sha[:12],
        )
        return VisionResult(
            app_id=app_id,
            task=task,
            transcript=transcript,
            confidence=confidence,
            backend=backend,
            processed_at_ms=int(time.time() * 1000),
            image_sha256=image_sha,
        )

    def _run_vision(self, *, image_bytes: bytes, task: str) -> tuple:
        """Backend dispatch for vision. Returns (backend, transcript, confidence).

        Synthetic mode emits a deterministic transcript keyed off the
        SHA-256 of the input + the task — useful for snapshot tests.
        The "confidence" returned in synthetic mode is fixed at 1.0
        so consumers can detect the synthetic path by checking the
        backend name AND the perfect confidence as a tell."""

        # Tier 1: Ollama llava on loopback (allowed by the air-gap
        # kill-switch since it's 127.0.0.1). We probe with a 1s
        # connect timeout so an absent daemon doesn't stall the call.
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "").strip()
        if ollama_url and ollama_url.startswith(
            ("http://127.0.0.1", "http://localhost")
        ):
            try:
                import urllib.request

                payload = json.dumps(
                    {
                        "model": os.environ.get("VOS3_VISION_MODEL", "llava"),
                        "prompt": f"task: {task}",
                        "images": [base64.b64encode(image_bytes).decode("ascii")],
                        "stream": False,
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    ollama_url.rstrip("/") + "/api/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1.0) as resp:  # nosec
                    body = json.loads(resp.read().decode("utf-8"))
                text = body.get("response", "").strip()
                if text:
                    return "ollama", text, 0.9
            except Exception as exc:  # noqa: BLE001
                logger.debug("[media] ollama vision tier skipped: %s", exc)

        ocr_backend = _probe_ocr()
        if ocr_backend == "easyocr":
            try:
                import easyocr  # type: ignore
                import tempfile

                reader = easyocr.Reader(["en"], gpu=False)
                with tempfile.NamedTemporaryFile(
                    suffix=".png",
                    delete=False,
                ) as fh:
                    fh.write(image_bytes)
                    tmp = fh.name
                try:
                    raw = reader.readtext(tmp, detail=0)
                finally:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                transcript = "\n".join(raw)
                return "easyocr", transcript, 0.85
            except Exception as exc:  # noqa: BLE001
                logger.warning("[media] easyocr tier failed: %s", exc)
        elif ocr_backend == "pytesseract":
            try:
                import pytesseract  # type: ignore
                from PIL import Image as _Image
                import io

                img = _Image.open(io.BytesIO(image_bytes))
                transcript = pytesseract.image_to_string(img).strip()
                return "pytesseract", transcript, 0.8
            except Exception as exc:  # noqa: BLE001
                logger.warning("[media] pytesseract tier failed: %s", exc)

        # Synthetic — deterministic on the input.
        digest = hashlib.sha256(image_bytes).hexdigest()
        transcript = (
            f"[vOS synthetic vision] task={task} "
            f"image_sha256={digest[:12]} "
            f"bytes={len(image_bytes)}"
        )
        return "synthetic", transcript, 1.0


# Module-level singleton — the FastAPI routes import this directly.
MEDIA_SERVICE = LocalMediaService()


__all__ = [
    "ImageGenerationResult",
    "LocalMediaService",
    "MEDIA_SERVICE",
    "MAX_IMAGE_BYTES",
    "VisionResult",
]
