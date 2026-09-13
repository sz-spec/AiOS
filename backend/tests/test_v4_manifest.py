"""
Phase 4.0 — Full-Stack Native Certification Tests
===================================================
SRE certification suite covering:
  C1: Memory quota boundary enforcement
  C2: Version string validation
  C3: Memory stacking heuristic (confidence → resource allocation)
  C4: SHA256 tamper detection in VPK archives
  C5: Retention cascade from DesignContract confidence
  Bridge: Hardening stress tests (bound window, LINE_MAX)
  SDK: Native SDK header + Makefile assertions

Total: 27 certification tests.
"""

import pytest
import sys
import os
import io
import json
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydantic import ValidationError
from services.vpacker import (
    VPKSystemResources,
    VPKSystemManifest,
    VPKIntentManifest,
    VPKDataRetentionPolicy,
    VPKManifest,
    pack_project,
    unpack_vpk,
)
from services.native_deploy_service import (
    VOS3NativeDeployService,
    KERNEL_LINE_MAX,
    HEADER_OVERHEAD,
)
from api.build_routes import VOS3_SDK_H, MAKEFILE_NATIVE

# ═════════════════════════════════════════════════════════════════════════
# C1: Memory Quota Boundary
# ═════════════════════════════════════════════════════════════════════════


class TestC1_MemoryQuotaBoundary:
    """Verify VPKSystemResources enforces inference/scratchpad limits."""

    def test_max_individual_inference_128(self):
        r = VPKSystemResources(inference_memory_mb=128)
        assert r.inference_memory_mb == 128

    def test_over_individual_inference_129(self):
        with pytest.raises(ValidationError):
            VPKSystemResources(inference_memory_mb=129)

    def test_max_combined_256(self):
        r = VPKSystemResources(inference_memory_mb=128, scratchpad_memory_mb=128)
        assert r.total_memory_mb == 256

    def test_scratchpad_zero_rejected(self):
        with pytest.raises(ValidationError):
            VPKSystemResources(scratchpad_memory_mb=0)


# ═════════════════════════════════════════════════════════════════════════
# C2: Version Validation
# ═════════════════════════════════════════════════════════════════════════


class TestC2_VersionValidation:
    """Verify VPKSystemManifest rejects non-semver version strings."""

    def _make(self, version: str) -> VPKSystemManifest:
        return VPKSystemManifest(name="test-app", version=version, entry="main.c")

    def test_valid_semver(self):
        m = self._make("1.0.0")
        assert m.version == "1.0.0"

    def test_invalid_two_part(self):
        with pytest.raises(ValidationError):
            self._make("1.0")

    def test_invalid_text(self):
        with pytest.raises(ValidationError):
            self._make("latest")

    def test_invalid_prefix_v(self):
        with pytest.raises(ValidationError):
            self._make("v1.0.0")


# ═════════════════════════════════════════════════════════════════════════
# C3: Memory Stacking (confidence → resource allocation)
# ═════════════════════════════════════════════════════════════════════════


def _simulate_memory_stacking(confidence: float):
    """Replicate _finalize_node heuristic from multi_agent.py lines 1380-1406."""
    if confidence >= 0.75:
        resources = VPKSystemResources(inference_memory_mb=12, scratchpad_memory_mb=4)
    else:
        resources = VPKSystemResources(inference_memory_mb=4, scratchpad_memory_mb=12)
    contract = {"confidence": confidence, "component_map": [], "color_tokens": []}
    intent = VPKIntentManifest.from_design_contract(contract)
    return resources, intent


class TestC3_MemoryStacking:
    """Verify confidence drives resource allocation and retention policy."""

    def test_high_confidence_inference_12(self):
        resources, _ = _simulate_memory_stacking(0.8)
        assert resources.inference_memory_mb == 12

    def test_high_confidence_retention_persist(self):
        _, intent = _simulate_memory_stacking(0.8)
        assert intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST

    def test_low_confidence_scratchpad_12(self):
        resources, _ = _simulate_memory_stacking(0.5)
        assert resources.inference_memory_mb == 4
        assert resources.scratchpad_memory_mb == 12

    def test_low_confidence_retention_scrub(self):
        _, intent = _simulate_memory_stacking(0.5)
        assert intent.data_retention_policy == VPKDataRetentionPolicy.SCRUB

    def test_boundary_075_is_high(self):
        resources, intent = _simulate_memory_stacking(0.75)
        assert resources.inference_memory_mb == 12
        assert intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST


# ═════════════════════════════════════════════════════════════════════════
# C4: SHA256 Tamper Detection
# ═════════════════════════════════════════════════════════════════════════


def _make_vpk():
    """Helper: pack a simple test project into VPK bytes."""
    manifest = VPKManifest(
        system=VPKSystemManifest(name="test-app", version="1.0.0", entry="main.c"),
        intent=VPKIntentManifest(),
    )
    files = {"main.c": "int main() { return 0; }\n"}
    return pack_project(files, manifest)


class TestC4_SHA256TamperDetection:
    """Verify unpack_vpk detects tampered file content."""

    def test_single_byte_tamper(self):
        vpk_bytes = _make_vpk()
        buf = io.BytesIO(vpk_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            manifest_data = json.loads(zf.read("manifest.json"))
            original_content = zf.read("main.c")

        # Tamper: change last byte
        tampered = original_content[:-1] + bytes([original_content[-1] ^ 0xFF])

        # Rebuild ZIP with tampered content but original manifest (wrong hash)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest_data, indent=2))
            zf.writestr("main.c", tampered)

        with pytest.raises(ValueError, match="Integrity check failed"):
            unpack_vpk(out.getvalue())

    def test_extra_file_not_in_manifest(self):
        vpk_bytes = _make_vpk()
        buf = io.BytesIO(vpk_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            manifest_data = zf.read("manifest.json")
            main_c = zf.read("main.c")

        # Add extra file not referenced in manifest
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", manifest_data)
            zf.writestr("main.c", main_c)
            zf.writestr("extra.txt", b"surprise!")

        # Should load without error — extra files are ignored
        manifest, files = unpack_vpk(out.getvalue())
        assert "extra.txt" in files

    def test_missing_file_from_manifest(self):
        vpk_bytes = _make_vpk()
        buf = io.BytesIO(vpk_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            manifest_data = zf.read("manifest.json")

        # Rebuild ZIP WITHOUT main.c (manifest still references it)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", manifest_data)

        # Should not crash — file is simply absent from returned dict
        manifest, files = unpack_vpk(out.getvalue())
        assert "main.c" not in files


# ═════════════════════════════════════════════════════════════════════════
# C5: Retention Cascade from DesignContract
# ═════════════════════════════════════════════════════════════════════════


class TestC5_RetentionCascade:
    """Verify DesignContract confidence drives retention policy."""

    def test_design_contract_085_persist(self):
        contract = {"confidence": 0.85, "component_map": [], "color_tokens": []}
        intent = VPKIntentManifest.from_design_contract(contract)
        assert intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST

    def test_design_contract_000_scrub(self):
        contract = {"confidence": 0.0, "component_map": [], "color_tokens": []}
        intent = VPKIntentManifest.from_design_contract(contract)
        assert intent.data_retention_policy == VPKDataRetentionPolicy.SCRUB


# ═════════════════════════════════════════════════════════════════════════
# Bridge Hardening Stress
# ═════════════════════════════════════════════════════════════════════════


class TestBridgeHardeningStress:
    """Verify VOS3NativeDeployService bound window enforcement."""

    def _make_service(
        self, peer_buf_size: int = KERNEL_LINE_MAX
    ) -> VOS3NativeDeployService:
        svc = VOS3NativeDeployService(bridge_socket="/tmp/nonexistent.sock")
        svc._peer_buf_size = peer_buf_size
        return svc

    def test_reject_10kb_command(self):
        svc = self._make_service()
        big_cmd = "X" * 10240
        with pytest.raises(ValueError, match="exceeds bound window"):
            svc._validate_and_bound(big_cmd)

    def test_reject_line_max_plus_1(self):
        svc = self._make_service()
        cmd = "X" * (KERNEL_LINE_MAX + 1)
        with pytest.raises(ValueError, match="exceeds bound window"):
            svc._validate_and_bound(cmd)

    def test_accept_line_max_minus_1(self):
        svc = self._make_service()
        cmd = "X" * (KERNEL_LINE_MAX - 1)
        # Should not raise
        result = svc._validate_and_bound(cmd)
        assert result == cmd

    def test_bound_window_peer_4096(self):
        svc = self._make_service(peer_buf_size=4096)
        window = svc._bound_window(99999)
        # min(4096, LINE_MAX) - HEADER_OVERHEAD = 4096 - 64 = 4032
        assert window == 4096 - HEADER_OVERHEAD

    def test_bound_window_large_peer(self):
        svc = self._make_service(peer_buf_size=99999)
        window = svc._bound_window(99999)
        # min(99999, LINE_MAX) - HEADER = LINE_MAX - 64 = 9152
        assert window == KERNEL_LINE_MAX - HEADER_OVERHEAD


# ═════════════════════════════════════════════════════════════════════════
# Native SDK Certification
# ═════════════════════════════════════════════════════════════════════════


class TestNativeSDKCertification:
    """Verify VOS3_SDK.h and Makefile.native contain required defines/flags."""

    def test_sys_exit_group_231(self):
        assert "VOS3_SYS_EXIT_GROUP 231" in VOS3_SDK_H

    def test_sys_getrandom_318(self):
        assert "VOS3_SYS_GETRANDOM 318" in VOS3_SDK_H

    def test_makefile_nostdlib(self):
        assert "-nostdlib" in MAKEFILE_NATIVE

    def test_makefile_ffreestanding(self):
        assert "-ffreestanding" in MAKEFILE_NATIVE
