"""
Auth Injection Tests — Phase 3.0 Subsystem 3.1
===============================================
13 tests covering:
- Keyword detection (explicit providers, implicit auth keywords, no match)
- Manifest loading and strategy lookup
- Template file loading from disk
- Auth injection into frontend/backend code dicts (template overwrites)
- Pipeline integration: _architect_node, _frontend_node, _backend_node, _tester_node
- TesterAgent prompt includes auth test scenarios
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure backend root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.agents.auth_injector import (
    detect_auth_strategy,
    get_auth_dependencies,
    get_auth_env_vars,
    get_test_scenarios,
    inject_auth_into_state,
    load_manifest,
    load_template_files,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def manifest():
    return load_manifest()


@pytest.fixture
def custom_jwt_scenarios():
    return get_test_scenarios("custom_jwt")


# =============================================================================
# 1. Keyword Detection Tests
# =============================================================================


class TestDetectAuthStrategy:
    """Tests for detect_auth_strategy() — 5 tests."""

    def test_clerk_explicit(self):
        """Explicit 'Clerk' mention → clerk strategy."""
        assert (
            detect_auth_strategy("Build a todo app with Clerk authentication")
            == "clerk"
        )

    def test_nextauth_explicit(self):
        """Explicit 'NextAuth' mention → nextauth strategy."""
        assert detect_auth_strategy("Use next-auth for the project") == "nextauth"

    def test_authjs_explicit(self):
        """auth.js alias → nextauth strategy."""
        assert (
            detect_auth_strategy("Integrate auth.js with Google provider") == "nextauth"
        )

    def test_implicit_login_keyword(self):
        """Implicit auth keyword 'login' → custom_jwt (default)."""
        assert detect_auth_strategy("Build a todo app with login") == "custom_jwt"

    def test_implicit_register_keyword(self):
        """Implicit 'register' keyword → custom_jwt."""
        assert (
            detect_auth_strategy("I need user registration and password reset")
            == "custom_jwt"
        )

    def test_no_auth_keywords(self):
        """No auth keywords → None."""
        assert detect_auth_strategy("Build a calculator app") is None

    def test_clerk_priority_over_implicit(self):
        """Clerk + implicit keywords → clerk wins (explicit priority)."""
        assert detect_auth_strategy("Todo app with Clerk login and signup") == "clerk"

    def test_case_insensitive(self):
        """Detection is case-insensitive."""
        assert detect_auth_strategy("USE CLERK FOR AUTH") == "clerk"
        assert detect_auth_strategy("Add NEXTAUTH integration") == "nextauth"


# =============================================================================
# 2. Manifest & Template Loading Tests
# =============================================================================


class TestManifestLoading:
    """Tests for manifest loading — 3 tests."""

    def test_manifest_loads(self, manifest):
        """manifest.json loads and has all 3 strategies."""
        assert "strategies" in manifest
        strategies = manifest["strategies"]
        assert "clerk" in strategies
        assert "nextauth" in strategies
        assert "custom_jwt" in strategies

    def test_strategy_manifest_has_required_keys(self, manifest):
        """Each strategy has files, dependencies, env_vars, test_scenarios."""
        for name, strategy in manifest["strategies"].items():
            assert "files" in strategy, f"{name} missing 'files'"
            assert "dependencies" in strategy, f"{name} missing 'dependencies'"
            assert "env_vars" in strategy, f"{name} missing 'env_vars'"
            assert "test_scenarios" in strategy, f"{name} missing 'test_scenarios'"
            assert len(strategy["test_scenarios"]) > 0, f"{name} has 0 test_scenarios"

    def test_custom_jwt_has_6_test_scenarios(self, manifest):
        """custom_jwt strategy has exactly 6 test scenarios (per TIS)."""
        scenarios = manifest["strategies"]["custom_jwt"]["test_scenarios"]
        assert len(scenarios) == 6


class TestTemplateFileLoading:
    """Tests for load_template_files() — 2 tests."""

    def test_custom_jwt_loads_all_files(self):
        """custom_jwt template loads all 4 frontend + 4 backend files."""
        files = load_template_files("custom_jwt")
        assert (
            len(files["frontend"]) == 4
        ), f"Expected 4 frontend files, got {len(files['frontend'])}"
        assert (
            len(files["backend"]) == 4
        ), f"Expected 4 backend files, got {len(files['backend'])}"

    def test_clerk_loads_all_files(self):
        """Clerk template loads all 5 frontend + 2 backend files."""
        files = load_template_files("clerk")
        assert (
            len(files["frontend"]) == 5
        ), f"Expected 5 frontend files, got {len(files['frontend'])}"
        assert (
            len(files["backend"]) == 2
        ), f"Expected 2 backend files, got {len(files['backend'])}"

    def test_nonexistent_strategy_returns_empty(self):
        """Unknown strategy returns empty dicts."""
        files = load_template_files("does_not_exist")
        assert files == {"frontend": {}, "backend": {}}


# =============================================================================
# 3. Injection Tests
# =============================================================================


class TestAuthInjection:
    """Tests for inject_auth_into_state() — 3 tests."""

    def test_template_overwrites_generated_files(self):
        """Template files overwrite AI-generated files at same path (HIGH priority)."""
        # Simulate AI-generated auth file at same dest path as template
        frontend_code = {
            "src/components/auth/LoginForm.tsx": "// AI-generated bad login form",
            "src/App.tsx": "// App component",
        }
        result = inject_auth_into_state("custom_jwt", frontend_code, {})
        updated = result["frontend_code"]

        # LoginForm.tsx should be overwritten by template
        assert "AI-generated" not in updated["src/components/auth/LoginForm.tsx"]
        # App.tsx should be untouched
        assert updated["src/App.tsx"] == "// App component"

    def test_injection_adds_new_files(self):
        """Auth injection adds template files that don't exist yet."""
        result = inject_auth_into_state("custom_jwt", {}, {})
        assert len(result["frontend_code"]) == 4
        assert len(result["backend_code"]) == 4

    def test_injection_preserves_existing_files(self):
        """Existing non-auth files are preserved after injection."""
        frontend_code = {"src/pages/Home.tsx": "// home"}
        backend_code = {"routes/items.py": "# items api"}
        result = inject_auth_into_state("custom_jwt", frontend_code, backend_code)
        assert "src/pages/Home.tsx" in result["frontend_code"]
        assert "routes/items.py" in result["backend_code"]


# =============================================================================
# 4. Utility Function Tests
# =============================================================================


class TestUtilityFunctions:
    """Tests for get_auth_dependencies, get_auth_env_vars, get_test_scenarios."""

    def test_custom_jwt_dependencies(self):
        """custom_jwt has pyjwt + bcrypt backend deps, no frontend deps."""
        deps = get_auth_dependencies("custom_jwt")
        assert deps["frontend"] == {}
        assert "pyjwt" in deps["backend"]
        assert "bcrypt" in deps["backend"]

    def test_clerk_env_vars(self):
        """Clerk requires NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY etc."""
        env_vars = get_auth_env_vars("clerk")
        assert "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" in env_vars
        assert "CLERK_SECRET_KEY" in env_vars

    def test_nonexistent_strategy_returns_empty(self):
        """Unknown strategy returns empty deps/env/scenarios."""
        assert get_auth_dependencies("fake") == {"frontend": {}, "backend": {}}
        assert get_auth_env_vars("fake") == []
        assert get_test_scenarios("fake") == []


# =============================================================================
# 5. Pipeline Integration Tests (multi_agent.py nodes)
# =============================================================================


class TestPipelineIntegration:
    """Tests for _architect_node, _frontend_node, _backend_node, _tester_node."""

    def _make_builder(self):
        """Create a MultiAgentBuilder with mock agents."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        builder.agents = {}
        builder.config = {}
        builder.memory_saver = None
        return builder

    def test_architect_node_sets_auth_strategy(self):
        """_architect_node sets auth_strategy in architecture when keywords match."""
        builder = self._make_builder()
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"architecture": {"stack": "React"}}
        builder.agents["architect"] = mock_agent

        state = {
            "requirements": "Build a SaaS app with Clerk authentication",
            "messages": [],
        }

        # Call the unwrapped function (bypass @monitor_node)
        result = builder._architect_node.__wrapped__(builder, state)

        assert result["architecture"]["auth_strategy"] == "clerk"
        assert result["architecture"]["stack"] == "React"  # original data preserved

    def test_architect_node_no_auth_keywords(self):
        """_architect_node does NOT set auth_strategy when no keywords match."""
        builder = self._make_builder()
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"architecture": {"stack": "React"}}
        builder.agents["architect"] = mock_agent

        state = {"requirements": "Build a calculator", "messages": []}
        result = builder._architect_node.__wrapped__(builder, state)

        assert "auth_strategy" not in result["architecture"]

    def test_frontend_node_injects_clerk_files(self):
        """_frontend_node injects Clerk frontend template files."""
        builder = self._make_builder()
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {
            "frontend_code": {"src/App.tsx": "// app"},
        }
        builder.agents["frontend"] = mock_agent

        state = {
            "architecture": {"auth_strategy": "clerk"},
            "messages": [],
        }

        result = builder._frontend_node.__wrapped__(builder, state)

        # Should have original + 5 clerk frontend template files
        assert "src/App.tsx" in result["frontend_code"]
        assert "src/components/auth/SignIn.tsx" in result["frontend_code"]
        assert "src/components/auth/SignUp.tsx" in result["frontend_code"]
        assert "src/components/auth/UserButton.tsx" in result["frontend_code"]
        assert "middleware.ts" in result["frontend_code"]

    def test_backend_node_injects_custom_jwt_files(self):
        """_backend_node injects custom_jwt backend template files."""
        builder = self._make_builder()
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {
            "backend_code": {"routes/items.py": "# items"},
        }
        builder.agents["backend"] = mock_agent

        state = {
            "architecture": {"auth_strategy": "custom_jwt"},
            "messages": [],
        }

        result = builder._backend_node.__wrapped__(builder, state)

        # Should have original + 4 custom_jwt backend template files
        assert "routes/items.py" in result["backend_code"]
        assert "middleware/auth.py" in result["backend_code"]
        assert "routes/auth.py" in result["backend_code"]
        assert "models/user.py" in result["backend_code"]
        assert "utils/tokens.py" in result["backend_code"]

    def test_tester_node_injects_auth_scenarios(self):
        """_tester_node passes auth_test_scenarios to TesterAgent when strategy set."""
        builder = self._make_builder()
        mock_agent = MagicMock()

        # Capture the state passed to invoke
        captured_state = {}

        def capture_invoke(state):
            captured_state.update(state)
            return {
                "messages": [],
                "tests": {},
                "current_phase": "reviewer",
            }

        mock_agent.invoke.side_effect = capture_invoke
        builder.agents["tester"] = mock_agent

        state = {
            "architecture": {"auth_strategy": "custom_jwt"},
            "frontend_code": {},
            "backend_code": {},
            "messages": [],
        }

        builder._tester_node.__wrapped__(builder, state)

        # Verify auth_test_scenarios were injected
        assert "auth_test_scenarios" in captured_state
        scenarios = captured_state["auth_test_scenarios"]
        assert len(scenarios) == 6  # custom_jwt has 6 scenarios
        assert any("Login with valid credentials" in s for s in scenarios)

    def test_tester_node_no_scenarios_without_auth(self):
        """_tester_node does NOT inject scenarios when no auth_strategy."""
        builder = self._make_builder()
        mock_agent = MagicMock()

        captured_state = {}

        def capture_invoke(state):
            captured_state.update(state)
            return {
                "messages": [],
                "tests": {},
                "current_phase": "reviewer",
            }

        mock_agent.invoke.side_effect = capture_invoke
        builder.agents["tester"] = mock_agent

        state = {
            "architecture": {},
            "frontend_code": {},
            "backend_code": {},
            "messages": [],
        }

        builder._tester_node.__wrapped__(builder, state)

        assert "auth_test_scenarios" not in captured_state


# =============================================================================
# 6. TesterAgent Prompt Integration
# =============================================================================


class TestTesterAgentPrompt:
    """Tests that TesterAgent.invoke() includes auth scenarios in prompt."""

    def test_tester_prompt_includes_auth_scenarios(self):
        """TesterAgent prompt includes MANDATORY auth scenarios when present."""
        from ai.agents.multi_agent import TesterAgent

        agent = TesterAgent.__new__(TesterAgent)
        agent.system_prompt = "You are a tester."

        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(content="No test files generated")
        agent.llm = mock_llm

        state = {
            "frontend_code": {"src/App.tsx": "export default function App() {}"},
            "backend_code": {},
            "auth_test_scenarios": [
                "Login with valid credentials returns JWT token",
                "Login with invalid credentials returns 401",
            ],
        }

        agent.invoke(state)

        # Verify the prompt passed to llm.generate contains auth scenarios
        call_args = mock_llm.generate.call_args
        prompt = call_args[0][0]  # first positional arg
        assert "MANDATORY Auth Test Scenarios" in prompt
        assert "Login with valid credentials returns JWT token" in prompt
        assert "Login with invalid credentials returns 401" in prompt

    def test_tester_prompt_no_auth_section_without_scenarios(self):
        """TesterAgent prompt does NOT include auth section when no scenarios."""
        from ai.agents.multi_agent import TesterAgent

        agent = TesterAgent.__new__(TesterAgent)
        agent.system_prompt = "You are a tester."

        mock_llm = MagicMock()
        mock_llm.generate.return_value = MagicMock(content="No test files generated")
        agent.llm = mock_llm

        state = {
            "frontend_code": {"src/App.tsx": "export default function App() {}"},
            "backend_code": {},
        }

        agent.invoke(state)

        call_args = mock_llm.generate.call_args
        prompt = call_args[0][0]
        assert "MANDATORY Auth Test Scenarios" not in prompt


# =============================================================================
# 7. End-to-End: "todo app with login" → custom_jwt
# =============================================================================


class TestEndToEnd:
    """E2E test: full detection → injection flow."""

    def test_todo_app_with_login_triggers_custom_jwt(self):
        """
        'Build a todo app with login' → custom_jwt strategy.
        'login' is an implicit keyword → defaults to custom_jwt.
        Verifies the full chain: detect → load → inject.
        """
        requirements = "Build a todo app with login"

        # Step 1: Detect
        strategy = detect_auth_strategy(requirements)
        assert strategy == "custom_jwt"

        # Step 2: Load templates
        template_files = load_template_files(strategy)
        assert len(template_files["frontend"]) == 4
        assert len(template_files["backend"]) == 4

        # Step 3: Inject into empty project
        frontend_code = {"src/App.tsx": "// main app"}
        backend_code = {"main.py": "# entrypoint"}
        result = inject_auth_into_state(strategy, frontend_code, backend_code)

        # Verify: original files preserved
        assert result["frontend_code"]["src/App.tsx"] == "// main app"
        assert result["backend_code"]["main.py"] == "# entrypoint"

        # Verify: auth files injected at correct paths
        assert "src/components/auth/LoginForm.tsx" in result["frontend_code"]
        assert "src/components/auth/RegisterForm.tsx" in result["frontend_code"]
        assert "src/lib/auth.ts" in result["frontend_code"]
        assert "src/hooks/useAuth.ts" in result["frontend_code"]
        assert "middleware/auth.py" in result["backend_code"]
        assert "routes/auth.py" in result["backend_code"]
        assert "models/user.py" in result["backend_code"]
        assert "utils/tokens.py" in result["backend_code"]

        # Verify: template content is real (not empty)
        login_content = result["frontend_code"]["src/components/auth/LoginForm.tsx"]
        assert len(login_content) > 100  # substantial template
        assert "LoginForm" in login_content

        # Step 4: Get test scenarios
        scenarios = get_test_scenarios(strategy)
        assert len(scenarios) == 6
        assert any("Login with valid credentials" in s for s in scenarios)

    def test_todo_app_with_clerk_triggers_clerk(self):
        """
        'Build a todo app with Clerk' → clerk strategy (explicit mention).
        """
        requirements = "Build a todo app with Clerk"
        strategy = detect_auth_strategy(requirements)
        assert strategy == "clerk"

        template_files = load_template_files(strategy)
        assert len(template_files["frontend"]) == 5
        assert len(template_files["backend"]) == 2

        scenarios = get_test_scenarios(strategy)
        assert len(scenarios) == 4
        assert any("ClerkProvider" in s for s in scenarios)
