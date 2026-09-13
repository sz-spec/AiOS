"""
backend/tests/security/test_dra_mig_validator.py

Sprint 16 / Item E7 — NVIDIA DRA + MIG manifest validator tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DMV_PATH = _REPO_ROOT / "backend" / "services" / "dra_mig_validator.py"
_spec = importlib.util.spec_from_file_location("vos3_dmv_under_test", _DMV_PATH)
dmv = importlib.util.module_from_spec(_spec)
sys.modules["vos3_dmv_under_test"] = dmv
_spec.loader.exec_module(dmv)


# ---------------------------------------------------------------------------
# MIG profile enum
# ---------------------------------------------------------------------------


def test_mig_profile_values():
    assert dmv.MIGProfile.M_1g_10gb.value == "1g.10gb"
    assert dmv.MIGProfile.M_3g_40gb.value == "3g.40gb"
    assert dmv.MIGProfile.M_7g_80gb.value == "7g.80gb"


# ---------------------------------------------------------------------------
# Shipped manifest passes validation
# ---------------------------------------------------------------------------


def test_shipped_manifest_validates_clean():
    validator = dmv.DraManifestValidator()
    manifest_path = str(
        _REPO_ROOT / "infra" / "k8s" / "sandboxes" / "dra-gpu-mig-class.yaml"
    )
    entries = validator.load_manifest(manifest_path)
    report = validator.validate(entries)
    assert report.is_ok, f"shipped manifest failed validation: {report.violations}"
    # 3 DeviceClass + 1 ResourceClaimTemplate = 4 entries.
    assert len(report.entries) == 4
    dcs = [e for e in report.entries if e.kind == dmv.ManifestKind.DEVICE_CLASS]
    rcts = [
        e for e in report.entries if e.kind == dmv.ManifestKind.RESOURCE_CLAIM_TEMPLATE
    ]
    assert len(dcs) == 3
    assert len(rcts) == 1


# ---------------------------------------------------------------------------
# Validation failure paths — synthesize bad manifests inline
# ---------------------------------------------------------------------------


_GOOD_DEVICE_CLASS_YAML = """
apiVersion: resource.k8s.io/v1beta1
kind: DeviceClass
metadata:
  name: vos3-mig-3g.40gb
  labels:
    vos3.dev/mig-profile: "3g.40gb"
spec:
  config:
  - opaque:
      driver: gpu.nvidia.com
      parameters:
        persistMig: true
        qos:
          maxComputeSm: 42
          maxMemoryMiB: 40960
"""


def test_validator_flags_old_api_version():
    bad = _GOOD_DEVICE_CLASS_YAML.replace(
        "resource.k8s.io/v1beta1", "extensions/v1beta1"
    )
    validator = dmv.DraManifestValidator()
    entries = validator.load_manifest(bad)
    report = validator.validate(entries)
    assert not report.is_ok
    assert any("apiVersion" in v.field_path for v in report.violations)


def test_validator_flags_missing_mig_profile_label():
    bad = """
apiVersion: resource.k8s.io/v1beta1
kind: DeviceClass
metadata:
  name: vos3-some-class
spec:
  config:
  - opaque:
      driver: gpu.nvidia.com
      parameters:
        persistMig: true
        qos:
          maxComputeSm: 42
          maxMemoryMiB: 40960
"""
    validator = dmv.DraManifestValidator()
    entries = validator.load_manifest(bad)
    report = validator.validate(entries)
    assert not report.is_ok
    assert any("mig-profile" in v.field_path for v in report.violations)


def test_validator_flags_unknown_profile():
    bad = _GOOD_DEVICE_CLASS_YAML.replace('"3g.40gb"', '"99g.999gb"')
    validator = dmv.DraManifestValidator()
    entries = validator.load_manifest(bad)
    report = validator.validate(entries)
    assert not report.is_ok
    assert any("unknown profile" in v.message for v in report.violations)


def test_validator_flags_persistmig_false():
    bad = _GOOD_DEVICE_CLASS_YAML.replace("persistMig: true", "persistMig: false")
    validator = dmv.DraManifestValidator()
    entries = validator.load_manifest(bad)
    report = validator.validate(entries)
    assert not report.is_ok
    assert any("persistMig" in v.field_path for v in report.violations)


def test_validator_flags_qos_mismatch():
    bad = _GOOD_DEVICE_CLASS_YAML.replace("maxComputeSm: 42", "maxComputeSm: 100")
    validator = dmv.DraManifestValidator()
    entries = validator.load_manifest(bad)
    report = validator.validate(entries)
    assert not report.is_ok
    assert any("maxComputeSm" in v.field_path for v in report.violations)


def test_validator_flags_missing_config_block():
    bad = """
apiVersion: resource.k8s.io/v1beta1
kind: DeviceClass
metadata:
  name: vos3-mig-3g.40gb
  labels:
    vos3.dev/mig-profile: "3g.40gb"
spec:
  selectors: []
"""
    validator = dmv.DraManifestValidator()
    entries = validator.load_manifest(bad)
    report = validator.validate(entries)
    assert not report.is_ok
    assert any("config" in v.field_path for v in report.violations)


# ---------------------------------------------------------------------------
# ResourceClaimTemplate cross-reference
# ---------------------------------------------------------------------------


def test_validator_flags_dangling_device_class_reference():
    bad = """
apiVersion: resource.k8s.io/v1beta1
kind: ResourceClaimTemplate
metadata:
  name: my-claim
spec:
  spec:
    devices:
      requests:
      - name: agent-gpu
        deviceClassName: does-not-exist
        allocationMode: ExactCount
        count: 1
"""
    validator = dmv.DraManifestValidator()
    entries = validator.load_manifest(bad)
    report = validator.validate(entries)
    assert not report.is_ok
    assert any("undefined DeviceClass" in v.message for v in report.violations)


# ---------------------------------------------------------------------------
# Env-var override of QoS table
# ---------------------------------------------------------------------------


def test_env_var_overrides_qos_expectations(monkeypatch):
    """If operator overrides VOS3_MIG_QOS_TABLE_M_3g_40gb_SM=100, then
    manifests with maxComputeSm=100 (which would otherwise violate)
    should now pass."""
    monkeypatch.setenv("VOS3_MIG_QOS_TABLE_M_3g_40gb_SM", "100")
    # Re-load validator with fresh env.
    import importlib

    fresh_spec = importlib.util.spec_from_file_location("vos3_dmv_env_test", _DMV_PATH)
    fresh = importlib.util.module_from_spec(fresh_spec)
    sys.modules["vos3_dmv_env_test"] = fresh
    fresh_spec.loader.exec_module(fresh)
    validator = fresh.DraManifestValidator()
    custom = _GOOD_DEVICE_CLASS_YAML.replace("maxComputeSm: 42", "maxComputeSm: 100")
    entries = validator.load_manifest(custom)
    report = validator.validate(entries)
    # maxComputeSm satisfies now; maxMemoryMiB still 40960 = default.
    assert report.is_ok, f"unexpected violations: {report.violations}"


# ---------------------------------------------------------------------------
# Type safety
# ---------------------------------------------------------------------------


def test_load_manifest_rejects_non_string():
    validator = dmv.DraManifestValidator()
    with pytest.raises(TypeError):
        validator.load_manifest(123)  # type: ignore[arg-type]
