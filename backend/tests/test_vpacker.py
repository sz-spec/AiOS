"""
Phase 4.0 QA — V-Packer: Layered Manifest + Pack/Unpack
========================================================
Tests:
  1. VPKSystemManifest validation (name, version, resources)
  2. VPKIntentManifest + DesignContract integration
  3. VPKDataRetentionPolicy lifecycle
  4. pack_project / unpack_vpk round-trip + integrity
  5. Size limit enforcement
"""

import pytest
import json

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vpacker import (
    VPKSystemResources,
    VPKSystemManifest,
    VPKIntentManifest,
    VPKDataRetentionPolicy,
    VPKManifest,
    pack_project,
    unpack_vpk,
    VPK_MAX_FILE_BYTES,
)
from pydantic import ValidationError

# ═════════════════════════════════════════════════════════════════════════
# 1. VPKSystemManifest Validation
# ═════════════════════════════════════════════════════════════════════════


class TestSystemManifest:
    def test_valid_manifest(self):
        m = VPKSystemManifest(name="hello-app", version="1.0.0", entry="main.c")
        assert m.name == "hello-app"
        assert m.version == "1.0.0"

    def test_invalid_name_uppercase(self):
        with pytest.raises(ValidationError):
            VPKSystemManifest(name="HelloApp", version="1.0.0", entry="main.c")

    def test_invalid_name_spaces(self):
        with pytest.raises(ValidationError):
            VPKSystemManifest(name="hello app", version="1.0.0", entry="main.c")

    def test_invalid_version_format(self):
        with pytest.raises(ValidationError):
            VPKSystemManifest(name="app", version="1.0", entry="main.c")

    def test_name_max_length(self):
        long_name = "a" * 64
        m = VPKSystemManifest(name=long_name, version="1.0.0", entry="main.c")
        assert len(m.name) == 64

    def test_name_too_long(self):
        with pytest.raises(ValidationError):
            VPKSystemManifest(name="a" * 65, version="1.0.0", entry="main.c")

    def test_default_resources(self):
        m = VPKSystemManifest(name="app", version="1.0.0", entry="main.c")
        assert m.resources.inference_memory_mb == 8
        assert m.resources.scratchpad_memory_mb == 8
        assert m.resources.total_memory_mb == 16
        assert m.resources.max_open_files == 32
        assert len(m.resources.allowed_syscalls) == 15

    def test_custom_resources(self):
        r = VPKSystemResources(inference_memory_mb=12, scratchpad_memory_mb=4)
        m = VPKSystemManifest(name="app", version="1.0.0", entry="main.c", resources=r)
        assert m.resources.inference_memory_mb == 12
        assert m.resources.scratchpad_memory_mb == 4
        assert m.resources.total_memory_mb == 16

    def test_resource_bounds(self):
        with pytest.raises(ValidationError):
            VPKSystemResources(inference_memory_mb=-1)
        with pytest.raises(ValidationError):
            VPKSystemResources(inference_memory_mb=129)
        with pytest.raises(ValidationError):
            VPKSystemResources(scratchpad_memory_mb=0)


# ═════════════════════════════════════════════════════════════════════════
# 2. VPKIntentManifest + DesignContract
# ═════════════════════════════════════════════════════════════════════════


class TestIntentManifest:
    def test_default_values(self):
        intent = VPKIntentManifest()
        assert intent.visual_style == "minimal"
        assert intent.page_type == "dashboard"
        assert intent.confidence == 0.0
        assert intent.data_retention_policy == VPKDataRetentionPolicy.SCRUB

    def test_from_design_contract_full(self):
        contract = {
            "overall_style": "corporate",
            "page_type": "form",
            "component_map": [
                {
                    "detected_type": "button",
                    "shadcn_component": "Button",
                    "variant": "default",
                },
                {
                    "detected_type": "input",
                    "shadcn_component": "Input",
                    "variant": "default",
                },
            ],
            "color_tokens": [
                {"name": "primary", "hsl": "220 90% 56%", "hex_fallback": "#3B82F6"},
                {"name": "background", "hsl": "0 0% 100%", "hex_fallback": "#FFFFFF"},
            ],
            "confidence": 0.88,
        }
        intent = VPKIntentManifest.from_design_contract(contract)
        assert intent.visual_style == "corporate"
        assert intent.page_type == "form"
        assert intent.authorized_components == ["Button", "Input"]
        assert intent.authorized_tokens == ["primary", "background"]
        assert intent.confidence == 0.88
        assert intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST

    def test_from_design_contract_low_confidence(self):
        contract = {
            "overall_style": "minimal",
            "confidence": 0.3,
            "component_map": [],
            "color_tokens": [],
        }
        intent = VPKIntentManifest.from_design_contract(contract)
        assert intent.data_retention_policy == VPKDataRetentionPolicy.SCRUB

    def test_from_design_contract_boundary_075(self):
        contract = {"confidence": 0.75, "component_map": [], "color_tokens": []}
        intent = VPKIntentManifest.from_design_contract(contract)
        assert intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST

    def test_from_design_contract_boundary_074(self):
        contract = {"confidence": 0.74, "component_map": [], "color_tokens": []}
        intent = VPKIntentManifest.from_design_contract(contract)
        assert intent.data_retention_policy == VPKDataRetentionPolicy.SCRUB

    def test_from_design_contract_empty(self):
        intent = VPKIntentManifest.from_design_contract({})
        assert intent.visual_style == "minimal"
        assert intent.confidence == 0.0

    def test_from_design_contract_missing_keys(self):
        contract = {
            "component_map": [{"no_shadcn_key": True}],
            "color_tokens": [{"no_name": True}],
        }
        intent = VPKIntentManifest.from_design_contract(contract)
        assert intent.authorized_components == []
        assert intent.authorized_tokens == []


# ═════════════════════════════════════════════════════════════════════════
# 3. VPKDataRetentionPolicy
# ═════════════════════════════════════════════════════════════════════════


class TestDataRetentionPolicy:
    def test_enum_values(self):
        assert VPKDataRetentionPolicy.SCRUB.value == "scrub"
        assert VPKDataRetentionPolicy.PERSIST.value == "persist"
        assert VPKDataRetentionPolicy.SNAPSHOT.value == "snapshot"

    def test_json_serialization(self):
        intent = VPKIntentManifest(data_retention_policy=VPKDataRetentionPolicy.PERSIST)
        dumped = intent.model_dump(mode="json")
        assert dumped["data_retention_policy"] == "persist"

    def test_json_deserialization(self):
        data = {"data_retention_policy": "persist"}
        intent = VPKIntentManifest(**data)
        assert intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST


# ═════════════════════════════════════════════════════════════════════════
# 4. Pack / Unpack Round-Trip
# ═════════════════════════════════════════════════════════════════════════


class TestPackUnpack:
    def test_simple_roundtrip(self):
        system = VPKSystemManifest(name="test", version="1.0.0", entry="main.c")
        intent = VPKIntentManifest(visual_style="dark", confidence=0.5)
        manifest = VPKManifest(system=system, intent=intent, description="test app")
        files = {"main.c": "int main() { return 0; }"}

        vpk = pack_project(files, manifest)
        assert isinstance(vpk, bytes)
        assert len(vpk) > 0

        m2, f2 = unpack_vpk(vpk)
        assert m2.system.name == "test"
        assert m2.intent.visual_style == "dark"
        assert m2.description == "test app"
        assert f2["main.c"] == "int main() { return 0; }"

    def test_integrity_check(self):
        system = VPKSystemManifest(name="test", version="1.0.0", entry="main.c")
        manifest = VPKManifest(system=system)
        files = {"main.c": "int main() { return 0; }"}

        vpk = pack_project(files, manifest)
        m2, f2 = unpack_vpk(vpk)
        assert len(m2.system.files) == 1
        assert m2.system.files[0].path == "main.c"
        assert len(m2.system.files[0].sha256) == 64  # SHA-256 hex

    def test_multiple_files(self):
        system = VPKSystemManifest(name="multi", version="2.0.0", entry="index.js")
        manifest = VPKManifest(system=system)
        files = {
            "index.js": "console.log('hello');",
            "style.css": "body { margin: 0; }",
            "README.md": "# My App",
        }
        vpk = pack_project(files, manifest)
        m2, f2 = unpack_vpk(vpk)
        assert len(f2) == 3
        assert f2["README.md"] == "# My App"

    def test_retention_survives_roundtrip(self):
        system = VPKSystemManifest(name="test", version="1.0.0", entry="main.c")
        intent = VPKIntentManifest(data_retention_policy=VPKDataRetentionPolicy.PERSIST)
        manifest = VPKManifest(system=system, intent=intent)
        files = {"main.c": "int main() {}"}

        vpk = pack_project(files, manifest)
        m2, _ = unpack_vpk(vpk)
        assert m2.intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST

    def test_executable_flag(self):
        system = VPKSystemManifest(name="test", version="1.0.0", entry="run.sh")
        manifest = VPKManifest(system=system)
        files = {"run.sh": "#!/bin/sh\necho hello", "app.elf": "binary"}

        vpk = pack_project(files, manifest)
        m2, _ = unpack_vpk(vpk)
        entry_map = {f.path: f for f in m2.system.files}
        assert entry_map["run.sh"].executable is True
        assert entry_map["app.elf"].executable is True

    def test_model_dump_json_roundtrip(self):
        system = VPKSystemManifest(name="test", version="1.0.0", entry="main.c")
        intent = VPKIntentManifest(
            visual_style="corporate",
            confidence=0.9,
            authorized_components=["Button", "Card"],
            data_retention_policy=VPKDataRetentionPolicy.PERSIST,
        )
        manifest = VPKManifest(system=system, intent=intent)
        dumped = manifest.model_dump(mode="json")
        serialized = json.dumps(dumped)
        roundtrip = json.loads(serialized)
        m2 = VPKManifest(**roundtrip)
        assert m2.intent.data_retention_policy == VPKDataRetentionPolicy.PERSIST
        assert m2.intent.authorized_components == ["Button", "Card"]


# ═════════════════════════════════════════════════════════════════════════
# 5. Size Limits
# ═════════════════════════════════════════════════════════════════════════


class TestSizeLimits:
    def test_file_too_large(self):
        system = VPKSystemManifest(name="big", version="1.0.0", entry="main.c")
        manifest = VPKManifest(system=system)
        files = {"main.c": "x" * (VPK_MAX_FILE_BYTES + 1)}
        with pytest.raises(ValueError, match="exceeds max"):
            pack_project(files, manifest)

    def test_file_at_limit(self):
        system = VPKSystemManifest(name="big", version="1.0.0", entry="main.c")
        manifest = VPKManifest(system=system)
        files = {"main.c": "x" * VPK_MAX_FILE_BYTES}
        vpk = pack_project(files, manifest)
        assert len(vpk) > 0

    def test_missing_manifest_on_unpack(self):
        import io, zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("hello.txt", "world")
        with pytest.raises(ValueError, match="missing manifest"):
            unpack_vpk(buf.getvalue())

    def test_corrupted_file_integrity(self):
        import io, zipfile

        system = VPKSystemManifest(name="test", version="1.0.0", entry="main.c")
        manifest = VPKManifest(system=system)
        files = {"main.c": "int main() {}"}
        vpk = pack_project(files, manifest)

        # Tamper: rebuild ZIP with different content but same manifest
        buf = io.BytesIO(vpk)
        with zipfile.ZipFile(buf, "r") as z:
            manifest_bytes = z.read("manifest.json")

        buf2 = io.BytesIO()
        with zipfile.ZipFile(buf2, "w") as z:
            z.writestr("manifest.json", manifest_bytes)
            z.writestr("main.c", "TAMPERED CONTENT!!!")

        with pytest.raises(ValueError, match="Integrity check failed"):
            unpack_vpk(buf2.getvalue())
