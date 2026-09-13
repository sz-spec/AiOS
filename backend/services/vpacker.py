"""
V-Packer — VOS3 Application Packaging Service
===============================================
Phase 4.0: Layered manifest (SystemManifest + IntentManifest) with
data retention policy and DesignContract integration.

Package format: ZIP-based .vpk with manifest.json + project files.
Max package: 512KB total, 65KB per file.
"""

import hashlib
import io
import json
import zipfile
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field

# ── Constants ──────────────────────────────────────────────────────

VPK_MAX_TOTAL_BYTES = 512 * 1024  # 512KB max package size
VPK_MAX_FILE_BYTES = 65 * 1024  # 65KB max per file (vos3fs limit ~70KB)
VPK_MANIFEST_NAME = "manifest.json"


# ── Layer 1: SystemManifest (Enforced by Kernel) ──────────────────


class VPKFileEntry(BaseModel):
    """Single file within the package."""

    path: str
    size: int
    sha256: str
    executable: bool = False


class VPKSystemResources(BaseModel):
    """Kernel-enforced resource limits. Maps to AI Guard quota."""

    inference_memory_mb: int = Field(default=8, ge=0, le=128)
    scratchpad_memory_mb: int = Field(default=8, ge=1, le=128)
    max_open_files: int = Field(default=32, ge=1, le=128)
    allowed_syscalls: List[str] = Field(
        default_factory=lambda: [
            "read",
            "write",
            "exit",
            "exit_group",
            "mmap",
            "munmap",
            "brk",
            "openat",
            "close",
            "fstat",
            "stat",
            "lseek",
            "getpid",
            "getcwd",
            "getrandom",
        ]
    )

    @property
    def total_memory_mb(self) -> int:
        return self.inference_memory_mb + self.scratchpad_memory_mb


class VPKSystemManifest(BaseModel):
    """Static manifest enforced by kernel AI Guard at APPLOAD time."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    entry: str
    entry_type: str = "static"
    resources: VPKSystemResources = Field(default_factory=VPKSystemResources)
    files: List[VPKFileEntry] = Field(default_factory=list)


# ── Layer 2: IntentManifest (Enforced by AI Guard) ────────────────


class VPKDataRetentionPolicy(str, Enum):
    """Controls inference_memory (KV-cache) lifecycle on APPKILL."""

    SCRUB = "scrub"
    PERSIST = "persist"
    SNAPSHOT = "snapshot"


class VPKIntentManifest(BaseModel):
    """Semantic manifest enforced by AI Guard policy engine."""

    visual_style: str = "minimal"
    page_type: str = "dashboard"
    authorized_components: List[str] = Field(default_factory=list)
    authorized_tokens: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    authorized_models: List[str] = Field(default_factory=list)
    data_access_patterns: List[str] = Field(default_factory=list)
    data_retention_policy: VPKDataRetentionPolicy = VPKDataRetentionPolicy.SCRUB

    @classmethod
    def from_design_contract(cls, contract_dict: Dict[str, Any]) -> "VPKIntentManifest":
        """Build IntentManifest from a Phase 3.5 DesignContract.model_dump()."""
        confidence = contract_dict.get("confidence", 0.0)
        retention = (
            VPKDataRetentionPolicy.PERSIST
            if confidence >= 0.75
            else VPKDataRetentionPolicy.SCRUB
        )
        return cls(
            visual_style=contract_dict.get("overall_style", "minimal"),
            page_type=contract_dict.get("page_type", "dashboard"),
            authorized_components=[
                m["shadcn_component"]
                for m in contract_dict.get("component_map", [])
                if "shadcn_component" in m
            ],
            authorized_tokens=[
                t["name"] for t in contract_dict.get("color_tokens", []) if "name" in t
            ],
            confidence=confidence,
            data_retention_policy=retention,
        )


# ── Combined VPK Manifest ────────────────────────────────────────


class VPKManifest(BaseModel):
    """VOS3 Package manifest — two-layer architecture."""

    system: VPKSystemManifest
    intent: VPKIntentManifest = Field(default_factory=VPKIntentManifest)
    description: str = Field(default="", max_length=256)


# ── Pack / Unpack ─────────────────────────────────────────────────


def pack_project(
    files: Dict[str, str],
    manifest: VPKManifest,
) -> bytes:
    """Pack project files into a VPK archive (ZIP-based).

    Args:
        files: Mapping of relative path → file content (text).
        manifest: VPK manifest with system + intent layers.

    Returns:
        Raw bytes of the ZIP archive.

    Raises:
        ValueError: If any file exceeds size limits or total exceeds 512KB.
    """
    buf = io.BytesIO()
    file_entries: List[VPKFileEntry] = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            content_bytes = content.encode("utf-8")
            if len(content_bytes) > VPK_MAX_FILE_BYTES:
                raise ValueError(
                    f"File {path} ({len(content_bytes)} bytes) exceeds "
                    f"max {VPK_MAX_FILE_BYTES} bytes"
                )
            sha = hashlib.sha256(content_bytes).hexdigest()
            file_entries.append(
                VPKFileEntry(
                    path=path,
                    size=len(content_bytes),
                    sha256=sha,
                    executable=path.endswith((".sh", ".elf")),
                )
            )
            zf.writestr(path, content_bytes)

        # Update manifest with file entries and write it
        manifest.system.files = file_entries
        manifest_json = manifest.model_dump(mode="json")
        manifest_bytes = json.dumps(manifest_json, indent=2).encode("utf-8")
        zf.writestr(VPK_MANIFEST_NAME, manifest_bytes)

    result = buf.getvalue()
    if len(result) > VPK_MAX_TOTAL_BYTES:
        raise ValueError(
            f"VPK archive ({len(result)} bytes) exceeds "
            f"max {VPK_MAX_TOTAL_BYTES} bytes"
        )
    return result


def unpack_vpk(data: bytes) -> tuple[VPKManifest, Dict[str, str]]:
    """Unpack a VPK archive.

    Args:
        data: Raw bytes of the ZIP archive.

    Returns:
        Tuple of (manifest, files_dict).

    Raises:
        ValueError: If manifest is missing, invalid, or integrity check fails.
    """
    buf = io.BytesIO(data)
    files: Dict[str, str] = {}

    with zipfile.ZipFile(buf, "r") as zf:
        names = zf.namelist()
        if VPK_MANIFEST_NAME not in names:
            raise ValueError("VPK archive missing manifest.json")

        manifest_bytes = zf.read(VPK_MANIFEST_NAME)
        manifest_dict = json.loads(manifest_bytes)
        manifest = VPKManifest(**manifest_dict)

        # Build a lookup for integrity checks
        entry_lookup = {e.path: e for e in manifest.system.files}

        for name in names:
            if name == VPK_MANIFEST_NAME:
                continue
            content_bytes = zf.read(name)
            # Integrity check
            if name in entry_lookup:
                expected_sha = entry_lookup[name].sha256
                actual_sha = hashlib.sha256(content_bytes).hexdigest()
                if actual_sha != expected_sha:
                    raise ValueError(
                        f"Integrity check failed for {name}: "
                        f"expected {expected_sha[:16]}..., got {actual_sha[:16]}..."
                    )
            try:
                files[name] = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                # Binary file (e.g. ELF) — store as base64 string
                import base64

                files[name] = base64.b64encode(content_bytes).decode("ascii")

    return manifest, files
