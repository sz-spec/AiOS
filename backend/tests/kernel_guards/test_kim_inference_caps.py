"""
Stage 3 · KIM-style inference ceilings + AI safety guards.

The vOS3 kernel KIM (Kernel Inference Module) enforces hard ceilings:
  - VOCAB         (the v20.0 universal-model lift bumped this)
  - DIM
  - SEQ_LEN
  - KV_PAGES

This Python suite asserts the BACKEND-side ceilings that prevent a
request from ever reaching the kernel with hostile values:
  * Model catalog entry size_bytes / context limits
  * Curated catalog kernel_fit flag honored
  * `select_model_for_host` refuses hosts whose tier can't carry the
    catalog's largest match
"""

from __future__ import annotations


import pytest

from services.model_manager import HostCapability


def _host(
    *,
    tier: str = "workstation",
    ram_gb: float = 8,
    vram_gb: float = 0,
    apple: bool = False,
):
    return HostCapability(
        platform="darwin" if apple else "linux",
        cpu_arch="arm64" if apple else "x86_64",
        total_ram_gb=ram_gb,
        gpu_name=None,
        vram_gb=vram_gb,
        unified_memory=apple,
        tier=tier,
        probe_method="test",
    )


# ---------------------------------------------------------------------------
# Catalog must have at least one entry per tier
# ---------------------------------------------------------------------------


def test_catalog_has_workstation_tier_entries(kernel_env):
    from services.model_manager import load_curated_catalog

    cat = load_curated_catalog()
    assert any(e.get("tier") == "workstation" for e in cat["models"])


def test_catalog_has_enterprise_tier_entries(kernel_env):
    from services.model_manager import load_curated_catalog

    cat = load_curated_catalog()
    assert any(e.get("tier") == "enterprise" for e in cat["models"])


def test_catalog_entries_have_required_fields(kernel_env):
    """Every model entry must have id, family, sha256, size_bytes, tier."""
    from services.model_manager import load_curated_catalog

    cat = load_curated_catalog()
    required = {"id", "family", "sha256", "size_bytes", "tier"}
    for entry in cat["models"]:
        missing = required - set(entry.keys())
        assert not missing, f"entry {entry.get('id')} missing {missing}"


def test_catalog_sha256_is_64_hex_chars(kernel_env):
    """No truncated or placeholder SHAs in the catalog."""
    from services.model_manager import load_curated_catalog

    cat = load_curated_catalog()
    for entry in cat["models"]:
        sha = entry.get("sha256", "")
        assert len(sha) == 64, f"{entry['id']} sha={sha!r}"
        assert all(c in "0123456789abcdef" for c in sha.lower())


def test_catalog_size_bytes_plausible(kernel_env):
    """No model can claim 0 bytes or >1 TB."""
    from services.model_manager import load_curated_catalog

    cat = load_curated_catalog()
    for entry in cat["models"]:
        s = entry.get("size_bytes", 0)
        assert 1 < s < 1_000_000_000_000


def test_catalog_tier_field_values_are_canonical(kernel_env):
    """Tier ∈ {workstation, medium, enterprise} — no typos."""
    from services.model_manager import load_curated_catalog

    cat = load_curated_catalog()
    valid = {"workstation", "medium", "enterprise"}
    for entry in cat["models"]:
        assert entry.get("tier") in valid


# ---------------------------------------------------------------------------
# Host-fit logic — workstation host refuses enterprise model
# ---------------------------------------------------------------------------


def test_workstation_host_blocked_from_enterprise_models():
    """A workstation tier host cannot run an enterprise-tier model."""
    from services.model_manager import _host_can_run

    h = _host(tier="workstation")
    assert _host_can_run({"tier": "enterprise"}, h) is False


def test_enterprise_host_runs_everything():
    from services.model_manager import _host_can_run

    h = _host(tier="enterprise", ram_gb=128, vram_gb=80)
    assert _host_can_run({"tier": "workstation"}, h) is True
    assert _host_can_run({"tier": "medium"}, h) is True
    assert _host_can_run({"tier": "enterprise"}, h) is True


def test_select_model_picks_largest_compatible(kernel_env):
    """For a family with multiple sizes, select returns the BIGGEST the
    host can run."""
    from services.model_manager import (
        load_curated_catalog,
        select_model_for_host,
    )

    h = _host(tier="enterprise", ram_gb=128, vram_gb=80)
    cat = load_curated_catalog()
    families = {e["family"] for e in cat["models"]}
    if not families:
        pytest.skip("empty catalog")
    fam = next(iter(families))
    pick = select_model_for_host(fam, h)
    if pick is None:
        pytest.skip(f"no entries for family {fam}")
    # The pick is the largest param_count entry of this family.
    eligible = [e for e in cat["models"] if e["family"] == fam]
    from services.model_manager import _param_count_to_int

    eligible_sorted = sorted(
        eligible,
        key=lambda e: _param_count_to_int(e.get("param_count")),
        reverse=True,
    )
    assert pick["id"] == eligible_sorted[0]["id"]


def test_select_model_returns_none_for_unknown_family(kernel_env):
    from services.model_manager import select_model_for_host

    h = _host(tier="enterprise", ram_gb=128, vram_gb=80)
    assert select_model_for_host("definitely-not-real", h) is None


# ---------------------------------------------------------------------------
# resolve_model_status — every catalog entry produces a recognized status
# ---------------------------------------------------------------------------


def test_resolve_model_status_recognized_values(kernel_env):
    from services.model_manager import (
        load_curated_catalog,
        resolve_model_status,
    )

    h = _host(tier="workstation", ram_gb=8)
    cat = load_curated_catalog()
    valid = {"local_ready", "available_lan", "available_https", "cloud_fallback_only"}
    for entry in cat["models"]:
        status = resolve_model_status(entry, h)
        assert status in valid


def test_resolve_model_status_workstation_cant_run_enterprise(kernel_env):
    """A workstation host always sees enterprise-tier entries as
    cloud_fallback_only — kernel won't load them at all."""
    from services.model_manager import (
        load_curated_catalog,
        resolve_model_status,
    )

    h = _host(tier="workstation", ram_gb=8)
    cat = load_curated_catalog()
    enterprise = [e for e in cat["models"] if e.get("tier") == "enterprise"]
    if not enterprise:
        pytest.skip("no enterprise entries in catalog")
    for entry in enterprise:
        assert resolve_model_status(entry, h) == "cloud_fallback_only"


# ---------------------------------------------------------------------------
# build_catalog_view — the dashboard API helper
# ---------------------------------------------------------------------------


def test_build_catalog_view_returns_models(kernel_env):
    from services.model_manager import build_catalog_view

    view = build_catalog_view()
    assert "models" in view
    assert isinstance(view["models"], list)
    # Every entry has a `status` field annotated.
    for entry in view["models"]:
        assert "status" in entry
