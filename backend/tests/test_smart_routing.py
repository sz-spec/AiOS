"""
Tests for smart_routing.py — Model ID consistency with router.yaml
==================================================================

Ensures every model ID referenced in smart_routing.py and the efficiency
router actually exists in the canonical router.yaml configuration.
"""

import pathlib
import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROOT = pathlib.Path(__file__).resolve().parent.parent  # backend/
_ROUTER_YAML = _ROOT / "config" / "router.yaml"


def _load_router_yaml() -> dict:
    """Load and return the parsed router.yaml."""
    with open(_ROUTER_YAML) as f:
        return yaml.safe_load(f)


def _valid_model_ids(config: dict) -> set[str]:
    """Extract all model_id values from router.yaml models section."""
    ids = set()
    for model_entry in config.get("models", {}).values():
        mid = model_entry.get("model_id")
        if mid:
            ids.add(mid)
    return ids


def _valid_model_aliases(config: dict) -> set[str]:
    """Extract all model alias names (keys) from router.yaml models section."""
    return set(config.get("models", {}).keys())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def router_config():
    return _load_router_yaml()


@pytest.fixture(scope="module")
def valid_ids(router_config):
    return _valid_model_ids(router_config)


@pytest.fixture(scope="module")
def valid_aliases(router_config):
    return _valid_model_aliases(router_config)


# ---------------------------------------------------------------------------
# Tests — smart_routing.py model IDs exist in router.yaml
# ---------------------------------------------------------------------------


class TestSmartRoutingModelIDs:
    """Every model_id in smart_routing._ALTERNATE_MODELS must exist in router.yaml."""

    def test_alternate_models_keys_are_valid(self, valid_ids):
        from src.smart_routing import _ALTERNATE_MODELS

        for model_id in _ALTERNATE_MODELS.keys():
            assert model_id in valid_ids, (
                f"_ALTERNATE_MODELS key '{model_id}' is not a valid model_id "
                f"in router.yaml. Valid IDs: {sorted(valid_ids)}"
            )

    def test_alternate_models_values_are_valid(self, valid_ids):
        from src.smart_routing import _ALTERNATE_MODELS

        for current, alternate in _ALTERNATE_MODELS.items():
            assert alternate in valid_ids, (
                f"_ALTERNATE_MODELS['{current}'] = '{alternate}' is not a valid "
                f"model_id in router.yaml. Valid IDs: {sorted(valid_ids)}"
            )

    def test_thinking_model_is_valid(self, valid_ids):
        from src.smart_routing import THINKING_MODEL

        assert THINKING_MODEL in valid_ids, (
            f"THINKING_MODEL = '{THINKING_MODEL}' is not a valid model_id "
            f"in router.yaml. Valid IDs: {sorted(valid_ids)}"
        )

    def test_get_alternate_model_returns_valid_ids(self, valid_ids):
        from src.smart_routing import get_alternate_model, _ALTERNATE_MODELS

        # Test known models
        for model_id in _ALTERNATE_MODELS:
            result = get_alternate_model(model_id)
            assert result in valid_ids, (
                f"get_alternate_model('{model_id}') returned '{result}' "
                f"which is not in router.yaml"
            )

    def test_get_alternate_model_default_fallback_is_valid(self, valid_ids):
        """When an unknown model is passed, the default fallback must be valid."""
        from src.smart_routing import get_alternate_model

        result = get_alternate_model("nonexistent-model-xyz")
        assert result in valid_ids, (
            f"get_alternate_model('nonexistent-model-xyz') returned '{result}' "
            f"which is not in router.yaml"
        )

    def test_get_thinking_model_returns_valid_id(self, valid_ids):
        from src.smart_routing import get_thinking_model

        result = get_thinking_model()
        assert result in valid_ids, (
            f"get_thinking_model() returned '{result}' " f"which is not in router.yaml"
        )

    def test_no_phantom_model_ids(self):
        """Explicitly ensure the known phantom IDs have been removed."""
        from src.smart_routing import _ALTERNATE_MODELS, THINKING_MODEL

        phantom_ids = {
            "gpt-5.2-pro",
            "gpt-5.3-codex",
            "gemini-3-flash-preview",
            "gemini-3.1-pro",
        }

        all_referenced = (
            set(_ALTERNATE_MODELS.keys())
            | set(_ALTERNATE_MODELS.values())
            | {THINKING_MODEL}
        )

        found_phantoms = all_referenced & phantom_ids
        assert (
            not found_phantoms
        ), f"Phantom model IDs still present in smart_routing.py: {found_phantoms}"


# ---------------------------------------------------------------------------
# Tests — efficiency/router.py OS pipeline stages use valid aliases
# ---------------------------------------------------------------------------


class TestOSPipelineStages:
    """Every model alias in _OS_PIPELINE_STAGES must exist in router.yaml."""

    def test_pipeline_stages_use_valid_aliases(self, valid_aliases):
        from src.efficiency.router import get_os_pipeline_stages

        stages = get_os_pipeline_stages()
        for stage, alias in stages.items():
            assert alias in valid_aliases, (
                f"OS pipeline stage '{stage}' references alias '{alias}' "
                f"which is not defined in router.yaml models. "
                f"Valid aliases: {sorted(valid_aliases)}"
            )

    def test_no_phantom_aliases_in_pipeline(self):
        """Ensure known phantom aliases have been removed from OS pipeline."""
        from src.efficiency.router import get_os_pipeline_stages

        phantom_aliases = {
            "gpt-5.2-pro",
            "gpt-5.2-precision",
            "gemini-3-pro",
        }

        stages = get_os_pipeline_stages()
        all_aliases = set(stages.values())

        found_phantoms = all_aliases & phantom_aliases
        assert (
            not found_phantoms
        ), f"Phantom model aliases still present in _OS_PIPELINE_STAGES: {found_phantoms}"


# ---------------------------------------------------------------------------
# Tests — assign_model returns valid aliases for all roles
# ---------------------------------------------------------------------------


class TestAssignModel:
    """assign_model() must return aliases that exist in router.yaml for every role."""

    ALL_ROLES = [
        "architect",
        "frontend",
        "backend",
        "tester",
        "reviewer",
        "coding",
        "coding-complex",
        "researcher",
        "researcher-deep",
    ]

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_assign_model_returns_valid_alias(self, role, valid_aliases):
        from src.efficiency.router import assign_model

        for complexity in (1, 5, 9, 10):
            result = assign_model(role, complexity, track=False)
            assert result in valid_aliases, (
                f"assign_model('{role}', {complexity}) returned '{result}' "
                f"which is not a valid model alias in router.yaml. "
                f"Valid aliases: {sorted(valid_aliases)}"
            )

    def test_assign_model_unknown_role_raises(self, valid_aliases):
        from src.efficiency.router import assign_model

        with pytest.raises(KeyError, match="unknown-role-xyz"):
            assign_model("unknown-role-xyz", 5, track=False)


# ---------------------------------------------------------------------------
# Tests — router.yaml role_mappings reference valid model aliases
# ---------------------------------------------------------------------------


class TestRouterYamlConsistency:
    """router.yaml role_mappings must only reference defined model aliases."""

    def test_role_mappings_reference_valid_aliases(self, router_config, valid_aliases):
        role_mappings = router_config.get("role_mappings", {})
        for role, alias in role_mappings.items():
            assert alias in valid_aliases, (
                f"router.yaml role_mappings['{role}'] = '{alias}' "
                f"is not a defined model alias. "
                f"Valid aliases: {sorted(valid_aliases)}"
            )

    def test_fallback_chain_references_valid_aliases(
        self, router_config, valid_aliases
    ):
        fallback_chain = router_config.get("fallback_chain", [])
        for alias in fallback_chain:
            assert alias in valid_aliases, (
                f"router.yaml fallback_chain contains '{alias}' "
                f"which is not a defined model alias. "
                f"Valid aliases: {sorted(valid_aliases)}"
            )

    def test_yaml_comments_do_not_contain_phantom_ids(self):
        """Read raw YAML and check that comments don't reference phantom IDs."""
        raw = _ROUTER_YAML.read_text()

        phantom_ids = [
            "gpt-5.2-pro",
            "gpt-5.3-codex",
            "gemini-3-flash-preview",
            "gemini-3.1-pro",
        ]

        for phantom in phantom_ids:
            assert phantom not in raw, (
                f"router.yaml still contains phantom model ID '{phantom}' "
                f"(possibly in a comment)"
            )
