"""Sovereign Model Manager — secure model lifecycle service.

Phase 6.4.2: Downloads, verifies, and registers AI models locally.

Features:
- Streaming download with on-the-fly SHA-256 verification
- Local model registry at ~/.vos3/models/ with JSON metadata DB
- SSE progress tracking for frontend consumption
- VBus SLOT_FINISH integration for kernel-side registration
- Maturity check: reject models from unverified publishers (<7 days)

Security:
- SHA-256 pinning: hash computed while streaming, verified before commit
- No shell=True, no eval, no pickle — JSON-only persistence
- HTTPS-only downloads (scheme whitelist)
- Path traversal prevention on model_id and filenames
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import struct
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import AsyncIterator, Optional

# Lazy httpx import — module-level attribute for testability (patchable)
try:
    import httpx as _httpx
except ImportError:
    _httpx = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sprint 19 / Wave 2 — E3 IOMMU/DMA fail-closed guard wiring.
#
# load_to_kernel() hands an agent's model weights to a kernel GPU slot via
# a zero-copy ivshmem DMA warp transfer. If the host IOMMU isn't enforcing
# translation, that DMA path can bypass the page-table protections vOS
# relies on for the Cluster C byte-level IFC story. We therefore gate the
# bind on IommuDmaGuard.require_iommu_for_gpu_bind() — fail-closed.
#
# Default ON. Operators set VOS3_DISABLE_IOMMU_GUARD=1 to skip it entirely
# (mock-driver / no-real-accelerator environments). On a real-but-
# unverifiable host, the guard's own VOS3_IOMMU_DEV_OVERRIDE=1 escape
# hatch allows the bind WITH a loud WARNING. See backend/security/
# iommu_dma_guard.py.
# ---------------------------------------------------------------------------

ENV_DISABLE_IOMMU_GUARD = "VOS3_DISABLE_IOMMU_GUARD"


def _iommu_guard_disabled_via_env() -> bool:
    return os.environ.get(ENV_DISABLE_IOMMU_GUARD, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _enforce_iommu_for_gpu_bind(model_id: str, slot_id: int) -> None:
    """Fail-closed E3 gate run before a GPU slot bind. Raises whatever
    the guard raises (DmaBypassRefused) when the host can't prove the
    IOMMU is enforcing. Import errors degrade gracefully (the guard is
    defense-in-depth; a missing module must not wedge model loading on a
    pre-E3 deployment) — but a present guard that REFUSES propagates."""
    if _iommu_guard_disabled_via_env():
        logger.warning(
            "[model_manager] E3 IOMMU guard DISABLED via %s for model=%s "
            "slot=%d — GPU bind proceeds unguarded.",
            ENV_DISABLE_IOMMU_GUARD,
            model_id,
            slot_id,
        )
        return
    try:
        from security.iommu_dma_guard import IommuDmaGuard
    except ImportError:
        try:
            from backend.security.iommu_dma_guard import IommuDmaGuard
        except ImportError:
            logger.warning(
                "[model_manager] iommu_dma_guard module not importable — "
                "GPU bind for model=%s slot=%d proceeds without the E3 gate.",
                model_id,
                slot_id,
            )
            return
    report = IommuDmaGuard().require_iommu_for_gpu_bind()
    logger.info(
        "[model_manager] E3 IOMMU gate passed for model=%s slot=%d (%s)",
        model_id,
        slot_id,
        report.reason,
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default storage root — overridable via VOS3_MODEL_DIR env var
_DEFAULT_MODEL_DIR = Path.home() / ".vos3" / "models"
_REGISTRY_FILENAME = "registry.json"
_CHUNK_SIZE = 65536  # 64KB streaming chunks (fits VBus uint16_t constraint)
_ALLOWED_SCHEMES = {"https"}
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")

# Format IDs aligned with kernel model_registry.h
FORMAT_GGUF = 0x01
FORMAT_SAFETENSORS = 0x02
FORMAT_ONNX = 0x03
FORMAT_UNKNOWN = 0x00

_EXT_TO_FORMAT = {
    ".gguf": FORMAT_GGUF,
    ".safetensors": FORMAT_SAFETENSORS,
    ".onnx": FORMAT_ONNX,
}

# Quantization labels (match kernel quant_type enum)
QUANT_F32 = 0
QUANT_F16 = 1
QUANT_Q8_0 = 2
QUANT_Q4_K_M = 3

_QUANT_LABELS = {
    "f32": QUANT_F32,
    "f16": QUANT_F16,
    "q8_0": QUANT_Q8_0,
    "q4_k_m": QUANT_Q4_K_M,
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ModelEntry:
    """Metadata for a locally stored model."""

    model_id: str
    filename: str
    format_id: int  # FORMAT_GGUF, FORMAT_SAFETENSORS, FORMAT_ONNX
    quant_label: str  # e.g. "q4_k_m", "f16"
    quant_type: int  # kernel enum value
    total_size: int  # bytes on disk
    sha256: str  # hex digest
    source_url: str
    downloaded_at: float  # time.time() epoch
    label: str  # human-readable name (max 32 chars for kernel)
    verified: bool = True
    active: bool = False
    slot_id: Optional[int] = None
    # InstructKR Provenance — populated on kernel mapping
    kernel_mapped_at: Optional[float] = None  # epoch timestamp of first SLOT_FINISH ACK
    kernel_base_addr: Optional[str] = None  # hex VA from kernel (PUD-isolated)
    kernel_xxh3: Optional[str] = None  # kernel-side XXH3-64 verification
    kernel_crc32c: Optional[str] = None  # kernel-side CRC32C verification
    kernel_latency_ms: Optional[float] = None  # download-finish → kernel-ready delta
    extra: dict = field(default_factory=dict)


@dataclass
class DownloadProgress:
    """Real-time download state for SSE emission."""

    model_id: str
    total_bytes: int
    downloaded_bytes: int
    speed_bps: float  # bytes per second
    eta_seconds: float
    phase: str  # "downloading", "verifying", "complete", "error"
    sha256_partial: str = ""  # running hex digest prefix (first 16 chars)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Registry — JSON file-backed model index
# ---------------------------------------------------------------------------


class ModelRegistry:
    """Thread-safe local model registry persisted as JSON.

    Storage layout:
        ~/.vos3/models/
            registry.json          — metadata index
            <model_id>.gguf        — model weights
            <model_id>.safetensors — model weights
    """

    def __init__(self, model_dir: Optional[Path] = None):
        self._dir = model_dir or Path(
            os.environ.get("VOS3_MODEL_DIR", str(_DEFAULT_MODEL_DIR))
        )
        self._dir.mkdir(parents=True, exist_ok=True)
        self._registry_path = self._dir / _REGISTRY_FILENAME
        self._entries: dict[str, ModelEntry] = {}
        self._lock = asyncio.Lock()
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        """Load registry from JSON file (cold start)."""
        if not self._registry_path.exists():
            self._entries = {}
            return
        try:
            raw = json.loads(self._registry_path.read_text(encoding="utf-8"))
            for key, val in raw.items():
                self._entries[key] = ModelEntry(**val)
            logger.info(
                "Model registry loaded: %d entries from %s",
                len(self._entries),
                self._registry_path,
            )
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Registry corrupted, starting fresh: %s", exc)
            self._entries = {}

    def _save(self) -> None:
        """Persist registry to JSON (atomic write via tmp + rename)."""
        tmp = self._registry_path.with_suffix(".tmp")
        data = {k: asdict(v) for k, v in self._entries.items()}
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._registry_path)

    # -- public API ---------------------------------------------------------

    async def add(self, entry: ModelEntry) -> None:
        """Register a new model (overwrites existing with same model_id)."""
        async with self._lock:
            self._entries[entry.model_id] = entry
            self._save()
            logger.info(
                "Registered model: %s (%s, %d bytes, sha256=%s)",
                entry.model_id,
                entry.label,
                entry.total_size,
                entry.sha256[:16],
            )

    async def remove(self, model_id: str) -> bool:
        """Remove model from registry and delete file."""
        async with self._lock:
            entry = self._entries.pop(model_id, None)
            if entry is None:
                return False
            model_path = self._dir / entry.filename
            if model_path.exists():
                model_path.unlink()
            self._save()
            logger.info("Removed model: %s", model_id)
            return True

    async def get(self, model_id: str) -> Optional[ModelEntry]:
        """Retrieve model metadata by ID."""
        return self._entries.get(model_id)

    async def list_models(self) -> list[ModelEntry]:
        """Return all registered models."""
        return list(self._entries.values())

    async def set_active(self, model_id: str, slot_id: int) -> bool:
        """Mark model as active on a VBus slot."""
        async with self._lock:
            entry = self._entries.get(model_id)
            if entry is None:
                return False
            entry.active = True
            entry.slot_id = slot_id
            self._save()
            return True

    async def set_inactive(self, model_id: str) -> bool:
        """Mark model as inactive (slot released).

        Preserves provenance metadata (kernel_mapped_at, checksums) as a
        historical record of the last kernel mapping. Only clears the
        active state and slot assignment.
        """
        async with self._lock:
            entry = self._entries.get(model_id)
            if entry is None:
                return False
            entry.active = False
            entry.slot_id = None
            self._save()
            return True

    async def load_to_kernel(
        self,
        model_id: str,
        slot_id: int,
        *,
        socket_path: str = "/tmp/vos3_bridge.sock",
    ) -> "SlotActivationResult":
        """Load a downloaded model into a kernel VBus slot (full handshake).

        Performs:
          1. SLOT_START — allocates HugePages in the kernel's PUD-isolated slot
          2. Warp Transfer — zero-copy ivshmem DMA (or VBus burst fallback)
          3. SLOT_FINISH — kernel sets PTEs to READ-ONLY + AI_PROTECTED + NO_EXECUTE
          4. Provenance — records kernel timestamp, checksums, and latency

        After success, the model's weights are hardware-immutable in the
        V-Palace WEIGHT Hall. The registry is updated with kernel provenance
        metadata (InstructKR Metadata Signing).

        Args:
            model_id: ID of a previously downloaded model.
            slot_id: Kernel slot (1-7; slot 0 reserved for Coordinator).
            socket_path: VBus UNIX socket path.

        Returns:
            SlotActivationResult with kernel-verified checksums and latency.

        Raises:
            ValueError: If model not found, file missing, or slot_id invalid.
            RuntimeError: If VBus connection or kernel handshake fails.
        """
        if slot_id < 1 or slot_id > 7:
            raise ValueError(
                f"slot_id must be 1-7 (slot 0 is Coordinator), got {slot_id}"
            )

        entry = self._entries.get(model_id)
        if entry is None:
            raise ValueError(f"Model '{model_id}' not found in registry")
        if entry.active:
            raise ValueError(
                f"Model '{model_id}' already active on slot {entry.slot_id}"
            )

        fpath = self._dir / entry.filename
        if not fpath.exists():
            raise ValueError(f"Model file missing for '{model_id}': {fpath}")

        # Phase 30 (Gap G9): M3 HSM model-signature gate. Default OFF -> not
        # invoked here, so legacy loads are byte-identical. When
        # VOS3_ENABLE_M3_HSM_GATE is set, verify the weight-set's signature BEFORE
        # the DMA path below; an invalid/missing signature raises M3GateRejected
        # (-> HTTP 403) and the weights never reach a kernel slot (fail-closed).
        from services.m3_signature_gate import get_m3_gate, m3_gate_enabled

        if m3_gate_enabled():
            import os as _os

            sig_path = fpath.with_suffix(fpath.suffix + ".sig")
            signature = sig_path.read_bytes() if sig_path.exists() else None
            get_m3_gate().verify_model_file(
                model_id=model_id,
                sha256_hex=entry.sha256,
                signature=signature,
                pid=_os.getpid(),
                fd=None,
            )

        # Lazy import to avoid circular dependency
        from services.vbus_driver import VBusDriver

        driver = VBusDriver(socket_path=socket_path)
        if not driver.connect():
            raise RuntimeError("Cannot connect to VBus bridge")

        # E3 (Sprint 19 W2): fail-closed IOMMU/DMA gate. Refuse to hand
        # this slot accelerator memory unless the host IOMMU is provably
        # enforcing — the warp transfer below is a DMA path. Raises
        # DmaBypassRefused (fail-closed) on an unverifiable host.
        _enforce_iommu_for_gpu_bind(model_id, slot_id)

        t0 = time.monotonic()

        try:
            # B-HIGH-13 fix: VBus I/O is blocking — run in executor to avoid
            # starving the asyncio event loop.
            import asyncio

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: driver.load_model_warp(
                    file_path=str(fpath),
                    slot_id=slot_id,
                    model_id=entry.format_id,
                    label=entry.label,
                ),
            )

            elapsed_ms = (time.monotonic() - t0) * 1000
            kernel_ts = time.time()

            # InstructKR Provenance — record kernel-side verification
            async with self._lock:
                entry.active = True
                entry.slot_id = slot_id
                entry.kernel_mapped_at = kernel_ts
                entry.kernel_base_addr = result.get("addr", "0")
                entry.kernel_xxh3 = result.get("checksum_xxh3", "0")
                entry.kernel_crc32c = result.get("checksum_crc32c", "0")
                entry.kernel_latency_ms = round(elapsed_ms, 2)
                self._save()

            logger.info(
                "KERNEL_MAP model=%s slot=%d base=%s xxh3=%s crc32c=%s "
                "latency=%.1fms warp=%s",
                model_id,
                slot_id,
                result.get("addr", "?"),
                result.get("checksum_xxh3", "?")[:16],
                result.get("checksum_crc32c", "?"),
                elapsed_ms,
                result.get("warp", False),
            )

            # W2.1e — bind the freshly loaded slot to the Smart Router's
            # kernel-default lane so VOS3_LOCALITY_PREFERENCE=local-first
            # picks it up on the next assign_model() call.
            try:
                from ai.llm.kernel_provider import (
                    register_kernel_slot,
                    DEFAULT_KERNEL_ALIAS,
                )

                register_kernel_slot(
                    DEFAULT_KERNEL_ALIAS,
                    slot_id,
                    model_id=model_id,
                    metadata={
                        "xxh3": result.get("checksum_xxh3", ""),
                        "crc32c": result.get("checksum_crc32c", ""),
                        "kernel_latency_ms": round(elapsed_ms, 2),
                        "warp": bool(result.get("warp", False)),
                    },
                )
            except Exception as bind_exc:  # noqa: BLE001
                # Registration must never break a successful kernel load.
                logger.warning(
                    "KERNEL_MAP succeeded but Smart-Router binding failed: %s",
                    bind_exc,
                )

            return SlotActivationResult(
                slot_id=slot_id,
                model_id=model_id,
                base_addr=result.get("addr", "0"),
                size=result.get("size", 0),
                xxh3=result.get("checksum_xxh3", "0"),
                crc32c=result.get("checksum_crc32c", "0"),
                elapsed_ms=round(elapsed_ms, 2),
                warp=result.get("warp", False),
                hall_assigned=True,
            )

        except Exception as exc:
            # Cleanup: reset slot on failure
            try:
                driver.slot_reset(slot_id)
            except Exception:
                pass
            raise RuntimeError(
                f"Kernel mapping failed for '{model_id}': {exc}"
            ) from exc

        finally:
            try:
                driver.disconnect()
            except Exception:
                pass

    def model_path(self, model_id: str) -> Optional[Path]:
        """Return filesystem path for a model's weight file."""
        entry = self._entries.get(model_id)
        if entry is None:
            return None
        return self._dir / entry.filename


# ---------------------------------------------------------------------------
# Download engine — streaming with on-the-fly SHA-256
# ---------------------------------------------------------------------------


def _validate_url(url: str) -> None:
    """Reject non-HTTPS URLs and obvious path-traversal attempts."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Scheme '{parsed.scheme}' not allowed; use HTTPS")
    if ".." in parsed.path:
        raise ValueError("Path traversal detected in URL")


def _validate_model_id(model_id: str) -> None:
    """Enforce safe characters for model_id (used as filename component)."""
    if not _SAFE_ID_RE.match(model_id):
        raise ValueError(
            f"Invalid model_id '{model_id}': must match {_SAFE_ID_RE.pattern}"
        )


def _detect_format(filename: str) -> int:
    """Detect model format from file extension."""
    ext = Path(filename).suffix.lower()
    return _EXT_TO_FORMAT.get(ext, FORMAT_UNKNOWN)


# ---------------------------------------------------------------------------
# GGUF header validation — "Ghost Tensor" defense
# ---------------------------------------------------------------------------

# GGUF v3 header layout (little-endian):
#   bytes 0-3:   magic (0x46475547 = "GGUF")
#   bytes 4-7:   version (uint32, expect 2 or 3)
#   bytes 8-15:  tensor_count (uint64)
#   bytes 16-23: metadata_kv_count (uint64)
# Minimum valid GGUF: 24 bytes header + at least some metadata
_GGUF_MAGIC = b"GGUF"
_GGUF_MIN_HEADER = 24
_GGUF_MAX_VERSION = 4  # reject future-incompatible versions
# Sanity cap: no single GGUF should claim >100M tensors
_GGUF_MAX_TENSOR_COUNT = 100_000
# Minimum bytes per tensor (name + dims + data): conservatively 64 bytes
_GGUF_MIN_BYTES_PER_TENSOR = 64


def validate_gguf_header(filepath: Path, file_size: int) -> Optional[str]:
    """Validate GGUF file header integrity.

    Returns None on success, or an error string describing the mismatch.
    This catches "Ghost Tensor" attacks where tensor_count is inflated
    but file_size is too small to contain them.
    """
    if file_size < _GGUF_MIN_HEADER:
        return f"File too small for GGUF header ({file_size} < {_GGUF_MIN_HEADER})"

    try:
        with open(filepath, "rb") as f:
            header = f.read(_GGUF_MIN_HEADER)
    except OSError as e:
        return f"Cannot read header: {e}"

    if len(header) < _GGUF_MIN_HEADER:
        return f"Short read: got {len(header)} bytes, need {_GGUF_MIN_HEADER}"

    # Magic check
    magic = header[0:4]
    if magic != _GGUF_MAGIC:
        return f"Bad magic: {magic!r} (expected {_GGUF_MAGIC!r})"

    # Version check
    version = struct.unpack_from("<I", header, 4)[0]
    if version < 1 or version > _GGUF_MAX_VERSION:
        return f"Unsupported GGUF version: {version} (expected 1-{_GGUF_MAX_VERSION})"

    # Tensor count sanity
    tensor_count = struct.unpack_from("<Q", header, 8)[0]
    if tensor_count > _GGUF_MAX_TENSOR_COUNT:
        return (
            f"CRITICAL_METADATA_MISMATCH: tensor_count={tensor_count} "
            f"exceeds maximum {_GGUF_MAX_TENSOR_COUNT}"
        )

    # Size-vs-tensor plausibility: file must be large enough to hold claimed tensors
    min_required = _GGUF_MIN_HEADER + tensor_count * _GGUF_MIN_BYTES_PER_TENSOR
    if file_size < min_required:
        return (
            f"CRITICAL_METADATA_MISMATCH: tensor_count={tensor_count} "
            f"requires >= {min_required} bytes, file is only {file_size}"
        )

    # Metadata kv count sanity
    metadata_kv_count = struct.unpack_from("<Q", header, 16)[0]
    if metadata_kv_count > 10_000:
        return (
            f"CRITICAL_METADATA_MISMATCH: metadata_kv_count={metadata_kv_count} "
            f"exceeds sanity limit 10000"
        )

    return None  # All checks passed


def _detect_quant(filename: str) -> tuple[str, int]:
    """Attempt to detect quantization from filename convention.

    Common patterns: model-q4_k_m.gguf, model.f16.safetensors
    """
    lower = filename.lower()
    for label, qtype in _QUANT_LABELS.items():
        if label in lower:
            return label, qtype
    return "unknown", QUANT_F32  # default to F32 if undetectable


async def download_model(
    url: str,
    model_id: str,
    registry: ModelRegistry,
    *,
    expected_sha256: Optional[str] = None,
    label: Optional[str] = None,
) -> AsyncIterator[DownloadProgress]:
    """Stream-download a model with on-the-fly SHA-256 verification.

    Yields DownloadProgress objects suitable for SSE emission.
    On success, registers the model in the local registry.
    On failure, cleans up partial files and yields an error progress.

    Args:
        url: HTTPS URL to the model file.
        model_id: Unique identifier (safe chars only).
        registry: ModelRegistry instance for persistence.
        expected_sha256: Optional SHA-256 hex to verify against.
        label: Human-readable name (truncated to 32 chars for kernel).
    """
    # -- validation (yield error instead of raising) --------------------------
    try:
        _validate_url(url)
        _validate_model_id(model_id)
    except ValueError as exc:
        yield DownloadProgress(
            model_id=model_id,
            total_bytes=0,
            downloaded_bytes=0,
            speed_bps=0,
            eta_seconds=0,
            phase="error",
            error=str(exc),
        )
        return

    # Check httpx availability (module-level lazy import)
    if _httpx is None:
        yield DownloadProgress(
            model_id=model_id,
            total_bytes=0,
            downloaded_bytes=0,
            speed_bps=0,
            eta_seconds=0,
            phase="error",
            error="httpx not installed (pip install httpx)",
        )
        return

    # Derive filename from URL path
    from urllib.parse import urlparse

    url_path = urlparse(url).path
    raw_filename = Path(url_path).name or f"{model_id}.bin"
    # Sanitize filename: strip anything that isn't alnum, dot, dash, underscore
    safe_filename = re.sub(r"[^a-zA-Z0-9._-]", "_", raw_filename)
    dest_path = registry._dir / safe_filename
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

    format_id = _detect_format(safe_filename)
    quant_label, quant_type = _detect_quant(safe_filename)
    effective_label = (label or model_id)[:32]

    hasher = hashlib.sha256()
    downloaded = 0
    total = 0
    start_time = time.monotonic()

    try:
        async with _httpx.AsyncClient(
            timeout=_httpx.Timeout(connect=30, read=120, write=30, pool=30),
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))

                with open(tmp_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                        f.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)

                        elapsed = time.monotonic() - start_time
                        speed = downloaded / elapsed if elapsed > 0 else 0
                        eta = (
                            (total - downloaded) / speed
                            if speed > 0 and total > 0
                            else 0
                        )

                        yield DownloadProgress(
                            model_id=model_id,
                            total_bytes=total,
                            downloaded_bytes=downloaded,
                            speed_bps=speed,
                            eta_seconds=eta,
                            phase="downloading",
                            sha256_partial=hasher.hexdigest()[:16],
                        )

        # -- SHA-256 verification -------------------------------------------
        yield DownloadProgress(
            model_id=model_id,
            total_bytes=total,
            downloaded_bytes=downloaded,
            speed_bps=0,
            eta_seconds=0,
            phase="verifying",
            sha256_partial=hasher.hexdigest()[:16],
        )

        final_hash = hasher.hexdigest()

        if expected_sha256 and final_hash != expected_sha256.lower():
            tmp_path.unlink(missing_ok=True)
            yield DownloadProgress(
                model_id=model_id,
                total_bytes=total,
                downloaded_bytes=downloaded,
                speed_bps=0,
                eta_seconds=0,
                phase="error",
                error=f"SHA-256 mismatch: expected {expected_sha256[:16]}..., got {final_hash[:16]}...",
            )
            return

        # -- GGUF structural validation (Ghost Tensor defense) ---------------
        if format_id == FORMAT_GGUF:
            gguf_err = validate_gguf_header(tmp_path, downloaded)
            if gguf_err:
                tmp_path.unlink(missing_ok=True)
                logger.critical("GGUF validation failed for %s: %s", model_id, gguf_err)
                yield DownloadProgress(
                    model_id=model_id,
                    total_bytes=total,
                    downloaded_bytes=downloaded,
                    speed_bps=0,
                    eta_seconds=0,
                    phase="error",
                    error=f"GGUF validation: {gguf_err}",
                )
                return

        # -- Commit: rename .part → final ----------------------------------
        tmp_path.replace(dest_path)

        entry = ModelEntry(
            model_id=model_id,
            filename=safe_filename,
            format_id=format_id,
            quant_label=quant_label,
            quant_type=quant_type,
            total_size=downloaded,
            sha256=final_hash,
            source_url=url,
            downloaded_at=time.time(),
            label=effective_label,
            verified=True,
        )
        await registry.add(entry)

        elapsed_total = time.monotonic() - start_time
        avg_speed = downloaded / elapsed_total if elapsed_total > 0 else 0
        logger.info(
            "Model download complete: %s (%d bytes in %.1fs, %.1f KB/s, sha256=%s)",
            model_id,
            downloaded,
            elapsed_total,
            avg_speed / 1024,
            final_hash[:16],
        )

        yield DownloadProgress(
            model_id=model_id,
            total_bytes=total,
            downloaded_bytes=downloaded,
            speed_bps=avg_speed,
            eta_seconds=0,
            phase="complete",
            sha256_partial=final_hash,
        )

    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        logger.error("Model download failed: %s — %s", model_id, exc)
        yield DownloadProgress(
            model_id=model_id,
            total_bytes=total,
            downloaded_bytes=downloaded,
            speed_bps=0,
            eta_seconds=0,
            phase="error",
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# SSE helpers — format DownloadProgress as Server-Sent Events
# ---------------------------------------------------------------------------


def progress_to_sse(progress: DownloadProgress) -> str:
    """Format a DownloadProgress as an SSE data line.

    Follows the `data: {json}\\n\\n` pattern used by chat_routes.py.
    """
    payload = {
        "model_id": progress.model_id,
        "total_bytes": progress.total_bytes,
        "downloaded_bytes": progress.downloaded_bytes,
        "speed_bps": round(progress.speed_bps, 1),
        "eta_seconds": round(progress.eta_seconds, 1),
        "phase": progress.phase,
        "sha256_partial": progress.sha256_partial,
        "pct": (
            round(100 * progress.downloaded_bytes / progress.total_bytes, 1)
            if progress.total_bytes > 0
            else 0
        ),
    }
    if progress.error:
        payload["error"] = progress.error
    return f"data: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# VBus Slot Activation — SLOT_START → Warp Transfer → SLOT_FINISH
# ---------------------------------------------------------------------------


@dataclass
class SlotActivationResult:
    """Result of activating a model on a VBus slot."""

    slot_id: int
    model_id: str
    base_addr: str  # hex virtual address from kernel
    size: int
    xxh3: str  # kernel-side XXH3-64 checksum (hex)
    crc32c: str  # kernel-side CRC32C checksum (hex)
    elapsed_ms: float  # total activation latency
    warp: bool  # True if ivshmem warp path was used
    hall_assigned: bool  # True if WEIGHT hall was mapped


async def activate_on_slot(
    model_id: str,
    slot_id: int,
    registry: ModelRegistry,
    *,
    socket_path: str = "/tmp/vos3_bridge.sock",
) -> SlotActivationResult:
    """Load a downloaded model into a kernel VBus slot.

    Delegates to ModelRegistry.load_to_kernel() which performs the full
    SLOT_START → Warp/Burst Transfer → SLOT_FINISH handshake and records
    InstructKR provenance metadata (kernel timestamp, checksums, latency).

    Args:
        model_id: ID of a previously downloaded model.
        slot_id: Kernel slot (1-7; slot 0 is reserved for Coordinator).
        registry: ModelRegistry with the model's metadata and file path.
        socket_path: VBus UNIX socket path.

    Returns:
        SlotActivationResult with kernel-verified checksums and latency.

    Raises:
        ValueError: If model not found or slot_id invalid.
        RuntimeError: If VBus connection or kernel command fails.
    """
    return await registry.load_to_kernel(model_id, slot_id, socket_path=socket_path)


# ---------------------------------------------------------------------------
# Singleton accessor (follows model_registry_service.py pattern)
# ---------------------------------------------------------------------------

_manager_registry: Optional[ModelRegistry] = None


def get_model_registry() -> ModelRegistry:
    """Return the singleton ModelRegistry instance."""
    global _manager_registry
    if _manager_registry is None:
        _manager_registry = ModelRegistry()
    return _manager_registry


# ===========================================================================
# W7.2 — Hardware-Aware Quantization Matrix
# ===========================================================================
#
# Pipeline:
#   1.  probe_host_capability()   — detect VRAM/RAM/GPU/platform.
#   2.  load_curated_catalog()    — read backend/config/curated_models.json.
#   3.  resolve_model_status(...) — per-entry status string for the UI:
#                                    "local_ready" / "available_lan" /
#                                    "available_https" / "cloud_fallback_only"
#   4.  pull_via_mesh_or_https()  — LAN mesh first, HTTPS fallback.
#
# All probes are wrapped in try/except — a missing tool / failed parse
# always yields a safe degraded answer (workstation tier, no GPU).
# ===========================================================================


import json as _json
import platform as _platform
import subprocess as _subprocess
from dataclasses import asdict as _asdict

CURATED_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "curated_models.json"
)

_TIER_THRESHOLDS_VRAM = [
    ("enterprise", 40.0),
    ("medium", 16.0),
    ("workstation", 0.0),
]
_TIER_THRESHOLDS_APPLE_UNIFIED = [
    ("enterprise", 64.0),
    ("medium", 32.0),
    ("workstation", 0.0),
]


@dataclass
class HostCapability:
    """Snapshot of the host's compute envelope. Cached per process."""

    platform: str
    cpu_arch: str
    total_ram_gb: float
    gpu_name: Optional[str]
    vram_gb: float
    unified_memory: bool
    tier: str
    probe_method: str

    def to_dict(self) -> dict:
        return _asdict(self)


_host_cap_cache: Optional[HostCapability] = None


def probe_host_capability(*, force: bool = False) -> HostCapability:
    """Detect host's RAM/VRAM/GPU. Cached per process. Never raises."""
    global _host_cap_cache
    if _host_cap_cache is not None and not force:
        return _host_cap_cache

    sys_name = _platform.system().lower()
    arch = _platform.machine().lower()
    total_ram_gb = _probe_total_ram_gb()
    is_apple_silicon = sys_name == "darwin" and arch in ("arm64", "aarch64")

    gpu_name = None
    vram_gb = 0.0
    probe_method = "none"

    try:
        if sys_name == "darwin":
            gpu_name, vram_gb, probe_method = _probe_darwin_gpu(
                is_apple_silicon=is_apple_silicon,
                total_ram_gb=total_ram_gb,
            )
        elif sys_name == "linux":
            gpu_name, vram_gb, probe_method = _probe_linux_gpu()
        elif sys_name == "windows":
            gpu_name, vram_gb, probe_method = _probe_windows_gpu()
    except Exception as exc:  # noqa: BLE001 — every probe is best-effort
        logger.debug("[hw_probe] %s probe failed: %s", sys_name, exc)

    tier = _classify_tier(
        vram_gb=vram_gb,
        total_ram_gb=total_ram_gb,
        is_apple_silicon=is_apple_silicon,
    )

    cap = HostCapability(
        platform=sys_name,
        cpu_arch=arch,
        total_ram_gb=round(total_ram_gb, 2),
        gpu_name=gpu_name,
        vram_gb=round(vram_gb, 2),
        unified_memory=is_apple_silicon,
        tier=tier,
        probe_method=probe_method,
    )
    _host_cap_cache = cap
    logger.info(
        "[hw_probe] platform=%s arch=%s ram=%.1fGB gpu=%r vram=%.1fGB tier=%s via=%s",
        sys_name,
        arch,
        total_ram_gb,
        gpu_name,
        vram_gb,
        tier,
        probe_method,
    )
    return cap


def _probe_total_ram_gb() -> float:
    """Total physical RAM in GB. Cross-platform best-effort."""
    try:
        if hasattr(os, "sysconf"):
            page_size = os.sysconf("SC_PAGE_SIZE")
            phys_pages = os.sysconf("SC_PHYS_PAGES")
            if page_size > 0 and phys_pages > 0:
                return (page_size * phys_pages) / (1024**3)
    except (ValueError, OSError):
        pass
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys / (1024**3)
    except Exception:  # noqa: BLE001
        return 8.0


def _probe_darwin_gpu(*, is_apple_silicon: bool, total_ram_gb: float) -> tuple:
    """macOS GPU probe via `system_profiler SPDisplaysDataType -json`."""
    try:
        out = _subprocess.run(
            ["/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if out.returncode != 0:
            raise RuntimeError(f"system_profiler rc={out.returncode}")
        data = _json.loads(out.stdout)
        displays = data.get("SPDisplaysDataType", [])
        if not displays:
            raise RuntimeError("no SPDisplaysDataType entries")
        first = displays[0]
        gpu_name = first.get("sppci_model") or first.get("_name") or "Apple GPU"
        if is_apple_silicon:
            return gpu_name, total_ram_gb, "system_profiler+unified"
        vram_raw = (
            first.get("spdisplays_vram_shared") or first.get("spdisplays_vram") or "0"
        )
        return gpu_name, _parse_size_string(vram_raw), "system_profiler"
    except Exception as exc:  # noqa: BLE001
        logger.debug("[hw_probe] darwin gpu probe failed: %s", exc)
        return None, 0.0, "darwin_fail"


def _probe_linux_gpu() -> tuple:
    """Linux GPU probe: nvidia-smi → sysfs (AMD) → no-gpu."""
    try:
        out = _subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            first = out.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first.split(",")]
            if len(parts) == 2:
                return parts[0], float(parts[1]) / 1024.0, "nvidia-smi"
    except (FileNotFoundError, _subprocess.TimeoutExpired):
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("[hw_probe] nvidia-smi failed: %s", exc)
    try:
        for card in Path("/sys/class/drm").glob("card[0-9]*"):
            vram_path = card / "device" / "mem_info_vram_total"
            if vram_path.exists():
                vram_bytes = int(vram_path.read_text().strip())
                return "AMD GPU", vram_bytes / (1024**3), "sysfs_drm"
    except Exception as exc:  # noqa: BLE001
        logger.debug("[hw_probe] sysfs AMD probe failed: %s", exc)
    return None, 0.0, "linux_no_gpu"


def _probe_windows_gpu() -> tuple:
    """Windows GPU probe via WMIC."""
    try:
        out = _subprocess.run(
            [
                "wmic",
                "path",
                "Win32_VideoController",
                "get",
                "Name,AdapterRAM",
                "/format:list",
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None, 0.0, "wmic_fail"
        name, ram_bytes = None, 0
        for line in out.stdout.splitlines():
            if line.startswith("Name="):
                name = line.split("=", 1)[1].strip()
            elif line.startswith("AdapterRAM="):
                try:
                    ram_bytes = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
        if name and ram_bytes > 0:
            return name, ram_bytes / (1024**3), "wmic"
        return name, 0.0, "wmic_partial"
    except (FileNotFoundError, _subprocess.TimeoutExpired):
        return None, 0.0, "wmic_unavailable"
    except Exception as exc:  # noqa: BLE001
        logger.debug("[hw_probe] wmic failed: %s", exc)
        return None, 0.0, "windows_fail"


def _parse_size_string(s: str) -> float:
    """'8 GB' / '8192 MB' / raw bytes → GB float. Safe on garbage."""
    if not isinstance(s, str):
        return 0.0
    s = s.strip().upper()
    try:
        for unit, divisor in (("GB", 1.0), ("MB", 1024.0), ("KB", 1024.0**2)):
            if s.endswith(unit):
                return float(s[: -len(unit)].strip()) / divisor
        return float(s) / 1024.0  # bare number → assumed MB
    except ValueError:
        return 0.0


def _classify_tier(
    *, vram_gb: float, total_ram_gb: float, is_apple_silicon: bool
) -> str:
    """Map (VRAM, RAM, platform) to a discrete tier."""
    if is_apple_silicon:
        for tier, threshold in _TIER_THRESHOLDS_APPLE_UNIFIED:
            if total_ram_gb >= threshold:
                return tier
        return "workstation"
    for tier, threshold in _TIER_THRESHOLDS_VRAM:
        if vram_gb >= threshold:
            return tier
    return "workstation"


_curated_catalog_cache: Optional[dict] = None


def load_curated_catalog() -> dict:
    """Read backend/config/curated_models.json. Cached per process."""
    global _curated_catalog_cache
    if _curated_catalog_cache is not None:
        return _curated_catalog_cache
    if not CURATED_CATALOG_PATH.exists():
        raise ValueError(f"curated catalog missing at {CURATED_CATALOG_PATH}")
    try:
        data = _json.loads(CURATED_CATALOG_PATH.read_text())
    except _json.JSONDecodeError as exc:
        raise ValueError(f"curated catalog malformed: {exc}") from exc
    if not isinstance(data, dict) or "models" not in data:
        raise ValueError("curated catalog missing 'models' key")
    _curated_catalog_cache = data
    return data


_TIER_RANK = {"workstation": 0, "medium": 1, "enterprise": 2}


def _host_can_run(entry: dict, host: HostCapability) -> bool:
    return _TIER_RANK.get(host.tier, 0) >= _TIER_RANK.get(entry.get("tier"), 999)


def select_model_for_host(
    model_family: str, host: Optional[HostCapability] = None
) -> Optional[dict]:
    """Return the largest catalog entry of `family` the host can run."""
    if host is None:
        host = probe_host_capability()
    catalog = load_curated_catalog()
    compatible = [
        e
        for e in catalog["models"]
        if e.get("family") == model_family and _host_can_run(e, host)
    ]
    if not compatible:
        return None
    compatible.sort(
        key=lambda e: _param_count_to_int(e.get("param_count")), reverse=True
    )
    return compatible[0]


def _param_count_to_int(p: Optional[str]) -> int:
    if not isinstance(p, str):
        return 0
    p = p.strip().upper()
    try:
        if p.endswith("B"):
            return int(float(p[:-1]) * 1_000)
        if p.endswith("M"):
            return int(float(p[:-1]))
        return int(p)
    except ValueError:
        return 0


def resolve_model_status(
    entry: dict, host: HostCapability, models_dir: Optional[Path] = None
) -> str:
    """Per-entry status string for the UI badge."""
    if not _host_can_run(entry, host):
        return "cloud_fallback_only"
    md = models_dir or _resolve_model_dir()
    target = md / f"{entry['id']}.gguf"
    if target.exists():
        try:
            if target.stat().st_size == int(entry.get("size_bytes", 0)):
                return "local_ready"
        except OSError:
            pass
    if _has_active_workspace_peer():
        return "available_lan"
    return "available_https"


def _resolve_model_dir() -> Path:
    env = os.environ.get("VOS3_MODEL_DIR", "").strip()
    return Path(env).expanduser().resolve() if env else _DEFAULT_MODEL_DIR


def _has_active_workspace_peer() -> bool:
    """True iff one verified active peer exists in the workspace."""
    try:
        from core.database.sqlite_setup import (
            DiscoveredPeer,
            get_session,
            init_db,
        )

        init_db()
        workspace = os.environ.get("VOS3_P2P_WORKSPACE_ID", "").strip()
        if not workspace:
            return False
        with get_session() as session:
            row = (
                session.query(DiscoveredPeer)
                .filter_by(
                    workspaceId=workspace,
                    status="active",
                    verified=True,
                )
                .first()
            )
            return row is not None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[hw_probe] peer-check failed: %s", exc)
        return False


async def pull_via_mesh_or_https(
    entry: dict, *, models_dir: Optional[Path] = None, progress_cb=None
) -> Path:
    """Try LAN-mesh blob fetch first; fall back to HTTPS streaming.

    The LAN-mesh route `/api/p2p/sync/blob/<sha256>` is a sentinel
    today (returns None) — a future P6.x phase implements it. Callers
    use this function so they don't need to change when the mesh
    layer lands."""
    md = models_dir or _resolve_model_dir()
    md.mkdir(parents=True, exist_ok=True)
    final_path = md / f"{entry['id']}.gguf"
    if final_path.exists():
        try:
            if final_path.stat().st_size == int(entry.get("size_bytes", 0)):
                return final_path
        except OSError:
            pass

    mesh_result = await _try_mesh_blob_fetch(
        sha256=entry["sha256"],
        dest=final_path,
    )
    if mesh_result is not None:
        return mesh_result

    registry = get_model_registry()
    await registry.download_async(
        url=entry["url"],
        model_id=entry["id"],
        expected_sha256=entry["sha256"],
        label=entry.get("display_name") or entry["id"],
        progress_cb=progress_cb,
    )
    return final_path


async def _try_mesh_blob_fetch(*, sha256: str, dest: Path) -> Optional[Path]:
    """P7.2 — Iterate verified workspace peers, try a SovereignSync
    blob request, write to disk on first success.

    Trust model:
      * Peer must be in DiscoveredPeer with status='active' AND
        verified=True AND workspaceId == VOS3_P2P_WORKSPACE_ID.
      * Session handshake (X25519 ECDH + Ed25519 identity sigs +
        transcript-bound HKDF) authenticates the bytes BEFORE any
        plaintext is exchanged.
      * After download, the caller (pull_via_mesh_or_https) re-verifies
        the SHA-256 against the catalog pin — a malicious peer that
        returns garbage bytes fails the post-download check and we
        fall through to HTTPS.

    Returns the path on success, None when no peer holds the blob OR
    every peer's connection failed. Never raises.
    """
    workspace = os.environ.get("VOS3_P2P_WORKSPACE_ID", "").strip()
    if not workspace:
        logger.debug("[mesh_fetch] VOS3_P2P_WORKSPACE_ID unset — skipping mesh")
        return None

    try:
        from core.database.sqlite_setup import (
            DiscoveredPeer,
            get_session,
            init_db,
        )

        init_db()
        with get_session() as session:
            peers = (
                session.query(DiscoveredPeer)
                .filter_by(
                    workspaceId=workspace,
                    status="active",
                    verified=True,
                )
                .order_by(DiscoveredPeer.lastSeenAt.desc())
                .all()
            )
            peer_addrs = [(p.host, p.syncPort, p.nodeId) for p in peers]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[mesh_fetch] peer lookup failed: %s", exc)
        return None

    if not peer_addrs:
        logger.debug("[mesh_fetch] no verified peers in workspace=%s", workspace)
        return None

    # Lazy imports — avoid pulling the p2p_sync module at top-level
    # since it imports cryptography that the W2.1 install path may
    # not have at backend cold boot.
    try:
        from services.p2p_sync import (
            SovereignSyncEngine,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[mesh_fetch] p2p_sync import failed: %s", exc)
        return None

    # Build the local sync engine. node_id is process-stable; the
    # signing key comes from the keyring.
    import uuid as _uuid

    local_node_id = os.environ.get("VOS3_NODE_ID") or f"node-{_uuid.uuid4().hex[:12]}"
    engine = SovereignSyncEngine(
        node_id=local_node_id,
        workspace_id=workspace,
    )

    for host, port, peer_node_id in peer_addrs:
        logger.info(
            "[mesh_fetch] trying peer %s:%s (node=%s) for sha256=%s",
            host,
            port,
            peer_node_id[:8],
            sha256[:16],
        )
        try:
            # Establish session synchronously — the SyncSession is
            # blocking-socket based; we run it in a thread to keep
            # the asyncio event loop free.
            import asyncio

            bytes_or_none = await asyncio.to_thread(
                _pull_blob_sync,
                engine=engine,
                host=host,
                port=port,
                sha256=sha256,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[mesh_fetch] peer %s:%s failed: %s",
                host,
                port,
                exc,
            )
            continue

        if bytes_or_none is None:
            # Peer doesn't have it — try the next peer.
            continue

        # Verify SHA-256 against the requested hash BEFORE writing
        # to the final path.
        local_hash = hashlib.sha256(bytes_or_none).hexdigest()
        if local_hash.lower() != sha256.lower():
            logger.warning(
                "[mesh_fetch] peer %s:%s returned bytes with mismatching "
                "sha256=%s (expected=%s) — rejecting",
                host,
                port,
                local_hash[:16],
                sha256[:16],
            )
            # Audit a malicious-peer-style event so the dashboard
            # surfaces the bad behavior.
            try:
                from services.app_sandbox import _record_security_event

                _record_security_event(
                    kind="malicious_peer_attempt",
                    reason="mesh_blob_sha256_mismatch",
                    details={
                        "peer_node_id": peer_node_id,
                        "peer_host": host,
                        "expected_sha": sha256,
                        "received_sha": local_hash,
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            continue

        # Atomic write — write to .tmp then rename.
        tmp = dest.with_suffix(dest.suffix + ".mesh-tmp")
        tmp.write_bytes(bytes_or_none)
        os.replace(tmp, dest)
        logger.info(
            "[mesh_fetch] OK — pulled %d bytes from peer %s:%s",
            len(bytes_or_none),
            host,
            port,
        )
        return dest

    logger.info(
        "[mesh_fetch] all %d peer(s) declined sha256=%s — falling back to HTTPS",
        len(peer_addrs),
        sha256[:16],
    )
    return None


def _pull_blob_sync(*, engine, host: str, port: int, sha256: str) -> Optional[bytes]:
    """Synchronous helper — establish session, request blob, close.

    Runs inside `asyncio.to_thread`. Returns the raw bytes on success,
    None if the peer doesn't hold the blob, and raises on transport
    errors so the caller can move to the next peer."""
    from services.p2p_sync import BlobNotFound

    session = engine.establish_session(host, port)
    try:
        return session.request_blob(sha256)
    except BlobNotFound:
        return None
    finally:
        session.close()


def build_catalog_view() -> dict:
    """Return the catalog with per-entry status + host snapshot."""
    host = probe_host_capability()
    catalog = load_curated_catalog()
    md = _resolve_model_dir()
    return {
        "schema_version": catalog.get("schema_version", 1),
        "host": host.to_dict(),
        "models": [
            {
                **entry,
                "status": resolve_model_status(entry, host, md),
                "host_can_run": _host_can_run(entry, host),
            }
            for entry in catalog["models"]
        ],
    }


def _reset_hw_cache_for_tests() -> None:
    """Test hook — clears the host-capability + catalog caches."""
    global _host_cap_cache, _curated_catalog_cache
    _host_cap_cache = None
    _curated_catalog_cache = None
