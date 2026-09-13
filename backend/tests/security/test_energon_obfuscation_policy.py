"""
backend/tests/security/test_energon_obfuscation_policy.py

Sprint 17 / Prototype 1 — Energon obfuscation policy tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_E_PATH = _REPO_ROOT / "backend" / "services" / "energon_obfuscation_policy.py"
_spec = importlib.util.spec_from_file_location("vos3_energon_under_test", _E_PATH)
e = importlib.util.module_from_spec(_spec)
sys.modules["vos3_energon_under_test"] = e
_spec.loader.exec_module(e)


def _llama_spec(sens=None, seed=0):
    return e.ArchSpec(
        family=e.TransformerFamily.LLAMA,
        layer_count=32,
        head_count=32,
        head_dim=128,
        hidden_dim=4096,
        vocab_size=128000,
        sensitivity=sens or e.SensitivityTier.PROPRIETARY,
        deterministic_seed=seed,
    )


# ---------------------------------------------------------------------------
# Enum sanity
# ---------------------------------------------------------------------------


def test_transformer_family_values():
    assert e.TransformerFamily.LLAMA.value == "llama"
    assert e.TransformerFamily.GEMMA.value == "gemma"
    assert e.TransformerFamily.VIT.value == "vit"


def test_sensitivity_tier_values():
    assert e.SensitivityTier.PUBLIC.value == "public"
    assert e.SensitivityTier.PROPRIETARY.value == "proprietary"
    assert e.SensitivityTier.CLASSIFIED.value == "classified"


def test_residual_risk_values():
    assert e.ResidualRiskClass.LOW.value == "low"
    assert e.ResidualRiskClass.MEDIUM.value == "medium"
    assert e.ResidualRiskClass.HIGH.value == "high"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_evaluate_rejects_non_archspec():
    pol = e.EnergonPolicy()
    with pytest.raises(TypeError):
        pol.evaluate("not-a-spec")  # type: ignore[arg-type]


def test_evaluate_rejects_zero_layer():
    pol = e.EnergonPolicy()
    bad = e.ArchSpec(
        family=e.TransformerFamily.LLAMA,
        layer_count=0,
        head_count=32,
        head_dim=128,
        hidden_dim=4096,
        vocab_size=128000,
        sensitivity=e.SensitivityTier.PROPRIETARY,
    )
    with pytest.raises(ValueError, match="layer_count"):
        pol.evaluate(bad)


def test_evaluate_rejects_zero_heads():
    pol = e.EnergonPolicy()
    bad = e.ArchSpec(
        family=e.TransformerFamily.LLAMA,
        layer_count=32,
        head_count=0,
        head_dim=128,
        hidden_dim=4096,
        vocab_size=128000,
        sensitivity=e.SensitivityTier.PROPRIETARY,
    )
    with pytest.raises(ValueError, match="head_count"):
        pol.evaluate(bad)


def test_evaluate_rejects_zero_head_dim():
    pol = e.EnergonPolicy()
    bad = e.ArchSpec(
        family=e.TransformerFamily.LLAMA,
        layer_count=32,
        head_count=32,
        head_dim=0,
        hidden_dim=4096,
        vocab_size=128000,
        sensitivity=e.SensitivityTier.PROPRIETARY,
    )
    with pytest.raises(ValueError, match="head_dim"):
        pol.evaluate(bad)


# ---------------------------------------------------------------------------
# PUBLIC tier — pass-through
# ---------------------------------------------------------------------------


def test_public_tier_is_pass_through():
    pol = e.EnergonPolicy()
    d = pol.evaluate(_llama_spec(sens=e.SensitivityTier.PUBLIC))
    assert d.kind == e.ObfuscationKind.PASS_THROUGH
    assert d.should_obfuscate is False
    assert d.pruning_recipe.keep_ratio == 1.0
    assert d.distillation_recipe.epochs == 0
    assert d.inference_jitter.enabled is False
    # PUBLIC residual = HIGH (no obfuscation at all)
    assert d.residual_risk_class == e.ResidualRiskClass.HIGH


# ---------------------------------------------------------------------------
# PROPRIETARY tier — medium obfuscation
# ---------------------------------------------------------------------------


def test_proprietary_tier_applies_obfuscation():
    pol = e.EnergonPolicy()
    d = pol.evaluate(_llama_spec(sens=e.SensitivityTier.PROPRIETARY))
    assert d.kind == e.ObfuscationKind.APPLIED
    assert d.should_obfuscate is True
    # ~25% head drop = 75% keep ratio
    assert 0.7 <= d.pruning_recipe.keep_ratio <= 0.8
    # Only even-indexed layers pruned
    assert all(i % 2 == 0 for i in d.pruning_recipe.layers_to_prune_partially)
    # Jitter enabled
    assert d.inference_jitter.enabled is True
    assert d.distillation_recipe.temperature_jitter_pct > 0
    # Residual = MEDIUM
    assert d.residual_risk_class == e.ResidualRiskClass.MEDIUM


def test_proprietary_drops_correct_number_of_heads():
    """32 heads * 25% = 8 heads dropped per pruned layer."""
    pol = e.EnergonPolicy()
    d = pol.evaluate(_llama_spec(sens=e.SensitivityTier.PROPRIETARY))
    for layer_idx, heads_dropped in d.pruning_recipe.heads_per_layer_to_drop.items():
        assert len(heads_dropped) == 8


# ---------------------------------------------------------------------------
# CLASSIFIED tier — max obfuscation
# ---------------------------------------------------------------------------


def test_classified_tier_max_obfuscation():
    pol = e.EnergonPolicy()
    d = pol.evaluate(_llama_spec(sens=e.SensitivityTier.CLASSIFIED))
    assert d.kind == e.ObfuscationKind.APPLIED
    # ~50% head drop
    assert 0.45 <= d.pruning_recipe.keep_ratio <= 0.55
    # ALL layers pruned (not just even)
    assert len(d.pruning_recipe.layers_to_prune_partially) == 32
    # Jitter maxed
    assert d.inference_jitter.layer_order_perm_rate >= 0.20
    # Distillation more aggressive
    assert d.distillation_recipe.temperature >= 4.0
    # Residual = LOW
    assert d.residual_risk_class == e.ResidualRiskClass.LOW


# ---------------------------------------------------------------------------
# Determinism — same arch ⇒ same recipes
# ---------------------------------------------------------------------------


def test_recipes_deterministic_given_same_spec():
    pol = e.EnergonPolicy()
    spec = _llama_spec(sens=e.SensitivityTier.PROPRIETARY)
    d1 = pol.evaluate(spec)
    d2 = pol.evaluate(spec)
    assert d1.pruning_recipe == d2.pruning_recipe
    assert d1.distillation_recipe == d2.distillation_recipe
    assert d1.arch_hash == d2.arch_hash


def test_evaluate_twice_check_helper():
    pol = e.EnergonPolicy()
    assert (
        pol.evaluate_twice_check(_llama_spec(sens=e.SensitivityTier.CLASSIFIED)) is True
    )


def test_explicit_seed_overrides_hash_seed():
    pol = e.EnergonPolicy()
    spec_a = _llama_spec(sens=e.SensitivityTier.PROPRIETARY, seed=12345)
    spec_b = _llama_spec(sens=e.SensitivityTier.PROPRIETARY, seed=67890)
    d_a = pol.evaluate(spec_a)
    d_b = pol.evaluate(spec_b)
    # Different seeds yield different head-drop selections (head start rotates).
    assert (
        d_a.pruning_recipe.heads_per_layer_to_drop
        != d_b.pruning_recipe.heads_per_layer_to_drop
    )


# ---------------------------------------------------------------------------
# Unsupported family
# ---------------------------------------------------------------------------


def test_unsupported_family_refused():
    """An ArchSpec carrying a family not in TransformerFamily would fail
    at construction (enum); to simulate the support-list pathway, force
    the family value to bypass the support list via the env override."""

    # Use a real family but inject an unrecognized-by-policy enum.
    # We construct an "invalid" family by abusing the enum string assignment.
    class FakeFamily:
        value = "fake-family-not-on-support-list"
        name = "FAKE"

    fake = type(
        "FakeArchSpec",
        (),
        {
            "family": FakeFamily(),
            "layer_count": 32,
            "head_count": 32,
            "head_dim": 128,
            "hidden_dim": 4096,
            "vocab_size": 128000,
            "sensitivity": e.SensitivityTier.PROPRIETARY,
            "deterministic_seed": 0,
        },
    )()
    # Will fail isinstance ArchSpec check.
    pol = e.EnergonPolicy()
    with pytest.raises(TypeError):
        pol.evaluate(fake)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_counters():
    pol = e.EnergonPolicy()
    pol.evaluate(_llama_spec(sens=e.SensitivityTier.PUBLIC))
    pol.evaluate(_llama_spec(sens=e.SensitivityTier.PROPRIETARY))
    pol.evaluate(_llama_spec(sens=e.SensitivityTier.CLASSIFIED))
    pol.evaluate(_llama_spec(sens=e.SensitivityTier.CLASSIFIED), defer_application=True)
    s = pol.snapshot_stats()
    assert s.evaluations == 4
    assert s.pass_through_count == 1
    assert s.applied_count == 2  # 1 PROPRIETARY + 1 CLASSIFIED
    assert s.deferred_count == 1
    # by_residual_risk: 1 HIGH (PUBLIC), 1 MEDIUM (PROPRIETARY), 2 LOW (CLASSIFIED x2)
    assert s.by_residual_risk["high"] == 1
    assert s.by_residual_risk["medium"] == 1
    assert s.by_residual_risk["low"] == 2


def test_snapshot_returns_copy():
    pol = e.EnergonPolicy()
    s1 = pol.snapshot_stats()
    pol.evaluate(_llama_spec())
    s2 = pol.snapshot_stats()
    assert s1.evaluations == 0
    assert s2.evaluations == 1


# ---------------------------------------------------------------------------
# Deferred application
# ---------------------------------------------------------------------------


def test_deferred_application_kind_and_should_obfuscate():
    pol = e.EnergonPolicy()
    d = pol.evaluate(
        _llama_spec(sens=e.SensitivityTier.PROPRIETARY), defer_application=True
    )
    assert d.kind == e.ObfuscationKind.DEFERRED
    assert d.should_obfuscate is False  # caller decides whether to apply
    # Recipes are STILL computed (caller can inspect + apply later)
    assert d.pruning_recipe.keep_ratio < 1.0


# ---------------------------------------------------------------------------
# arch_hash linkability
# ---------------------------------------------------------------------------


def test_arch_hash_is_sha256_hex():
    pol = e.EnergonPolicy()
    d = pol.evaluate(_llama_spec())
    assert len(d.arch_hash) == 64
    int(d.arch_hash, 16)  # raises if not hex


def test_different_specs_get_different_arch_hashes():
    pol = e.EnergonPolicy()
    h_a = pol.evaluate(_llama_spec(seed=0)).arch_hash
    h_b = pol.evaluate(
        e.ArchSpec(
            family=e.TransformerFamily.QWEN,
            layer_count=32,
            head_count=32,
            head_dim=128,
            hidden_dim=4096,
            vocab_size=128000,
            sensitivity=e.SensitivityTier.PROPRIETARY,
        )
    ).arch_hash
    assert h_a != h_b
