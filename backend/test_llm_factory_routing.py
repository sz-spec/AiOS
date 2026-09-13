"""
LLM Factory Routing Test - February 2026 Stack
===============================================
Tests the SmartRouter integration with LLM Factory for the updated model stack.

February 2026 Model Stack:
- Architect: gpt-5.2-pro (Thinking Mode for technical specs)
- Coding: claude-3-opus-20260210 (Opus 4.6 for large codebases)
- Reviewer: gpt-5.2 (Cross-model verification)
- Reviewer Self: claude-opus-low-temp (temp 0.2 for self-correction)
- Researcher: gemini-3-pro-latest (massive context for docs)

Complexity threshold: 7 (tasks >= 7 get premium models)
"""

import logging
import sys
from unittest.mock import patch, MagicMock
from dataclasses import dataclass
from typing import List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Add backend to path
sys.path.insert(0, "/Users/snirzano/Desktop/vos7220206/VOS3/backend")


@dataclass
class TestScenario:
    """Test scenario definition."""

    name: str
    role: str
    complexity: int
    expected_router_model: str
    expected_provider: str
    expected_model_id: str
    description: str
    check_thinking_mode: bool = False


@dataclass
class TestResult:
    """Test result."""

    scenario: TestScenario
    router_model: str
    provider: str
    model_id: str
    llm_class: str
    thinking_enabled: bool
    passed: bool
    details: str


def get_test_scenarios() -> List[TestScenario]:
    """
    Define test scenarios based on February 2026 router configuration.

    Router config (complexity_threshold=7):
    - complexity >= 7: SmartRouter library hardcoded behavior:
      - architect role -> "gpt" (maps to gpt-5.2-pro)
      - other roles -> "claude-opus"
    - complexity < 7: uses role_mappings

    Role mappings (Feb 2026):
    - architect -> gpt-5.2-pro (Thinking Mode)
    - coding -> claude-opus (Opus 4.6)
    - reviewer -> gpt-5.2 (Cross-model verification)
    - reviewer_self -> claude-opus-low-temp
    - researcher -> gemini-3-pro
    """
    return [
        # Scenario 1: Architect at high complexity (uses "gpt" alias)
        TestScenario(
            name="Architect High Complexity",
            role="architect",
            complexity=9,
            expected_router_model="gpt",  # Library returns "gpt" for high-complexity architect
            expected_provider="openai",
            expected_model_id="gpt-5.2-pro",  # "gpt" maps to gpt-5.2-pro
            description="High complexity architecture - GPT-5.2-thinking via 'gpt' alias",
            check_thinking_mode=True,
        ),
        # Scenario 2: Architect at low complexity (uses role_mappings)
        TestScenario(
            name="Architect Low Complexity",
            role="architect",
            complexity=5,
            expected_router_model="gpt-5.2-pro",  # From role_mappings
            expected_provider="openai",
            expected_model_id="gpt-5.2-pro",
            description="Standard architecture - GPT-5.2-thinking from role_mappings",
            check_thinking_mode=True,
        ),
        # Scenario 3: Coding with Claude Opus 4.6
        TestScenario(
            name="Coding (Claude Opus 4.6)",
            role="coding",
            complexity=5,
            expected_router_model="claude-opus",
            expected_provider="anthropic",
            expected_model_id="claude-3-opus-20260210",
            description="Standard coding task - Claude Opus 4.6 for large codebase handling",
        ),
        # Scenario 4: Reviewer at low complexity (uses role_mappings for cross-check)
        TestScenario(
            name="Reviewer (GPT-5.2 Cross-Check)",
            role="reviewer",
            complexity=6,  # Below threshold to use role_mappings
            expected_router_model="gpt-5.2",
            expected_provider="openai",
            expected_model_id="gpt-5.2",
            description="Code review - GPT-5.2 reviews Claude's code (cross-model verification)",
        ),
        # Scenario 5: Reviewer at high complexity (library fallback to claude-opus)
        TestScenario(
            name="Reviewer High Complexity",
            role="reviewer",
            complexity=8,  # Above threshold -> claude-opus (library hardcoded)
            expected_router_model="claude-opus",
            expected_provider="anthropic",
            expected_model_id="claude-3-opus-20260210",
            description="High complexity review - Falls back to Claude Opus per library behavior",
        ),
        # Scenario 6: Researcher with Gemini 3 Pro
        TestScenario(
            name="Researcher (Gemini 3 Pro)",
            role="researcher",
            complexity=4,
            expected_router_model="gemini-3-pro",
            expected_provider="google",
            expected_model_id="gemini-3-pro-latest",
            description="Research task - Gemini 3 Pro for massive context window",
        ),
        # Scenario 7: Self-Correction Review (Low Temp)
        TestScenario(
            name="Self-Correction (Claude Low-Temp)",
            role="reviewer_self",
            complexity=6,
            expected_router_model="claude-opus-low-temp",
            expected_provider="anthropic",
            expected_model_id="claude-3-opus-20260210",
            description="Self-correction review - Claude Opus at temp 0.2",
        ),
    ]


def run_test_scenario(scenario: TestScenario) -> TestResult:
    """
    Run a single test scenario.

    Uses mocking to capture routing decisions without making API calls.
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"Testing: {scenario.name}")
    logger.info(f"Role: {scenario.role}, Complexity: {scenario.complexity}")
    logger.info(f"Description: {scenario.description}")
    logger.info(f"{'='*60}")

    # Import the factory and router
    from src.efficiency.factory import (
        get_llm_for_task,
        _get_provider_and_model,
        _is_thinking_mode_model,
    )
    from src.efficiency.router import assign_model

    # Step 1: Get the router decision (no mocking needed - this is pure logic)
    router_model = assign_model(scenario.role, scenario.complexity)
    logger.info(f"Router Decision: {router_model}")

    # Step 2: Get provider and model mapping
    provider, model_id = _get_provider_and_model(router_model)
    logger.info(f"Provider: {provider}")
    logger.info(f"Model ID: {model_id}")

    # Check thinking mode
    thinking_enabled = _is_thinking_mode_model(router_model)
    if scenario.check_thinking_mode:
        logger.info(f"Thinking Mode: {thinking_enabled}")

    # Step 3: Mock the actual LLM creation to avoid API calls
    mock_llm = MagicMock()
    mock_llm.model = model_id
    mock_llm.__class__.__name__ = f"Mock{provider.title()}LLM"

    with patch.object(
        sys.modules["src.efficiency.factory"], "_create_llm", return_value=mock_llm
    ) as mock_create:
        # Call the factory
        llm = get_llm_for_task(
            role=scenario.role, complexity=scenario.complexity, temperature=0.7
        )

        # Verify _create_llm was called with correct params
        mock_create.assert_called_once()
        logger.info(f"LLM Class: {llm.__class__.__name__}")

    # Validate results
    checks = []
    details = []

    # Check router model
    router_match = router_model == scenario.expected_router_model
    checks.append(router_match)
    if not router_match:
        details.append(
            f"Router: expected {scenario.expected_router_model}, got {router_model}"
        )

    # Check provider
    provider_match = provider == scenario.expected_provider
    checks.append(provider_match)
    if not provider_match:
        details.append(
            f"Provider: expected {scenario.expected_provider}, got {provider}"
        )

    # Check model ID
    model_match = model_id == scenario.expected_model_id
    checks.append(model_match)
    if not model_match:
        details.append(
            f"Model ID: expected {scenario.expected_model_id}, got {model_id}"
        )

    # Check thinking mode if required
    if scenario.check_thinking_mode:
        thinking_match = thinking_enabled
        checks.append(thinking_match)
        if not thinking_match:
            details.append(f"Thinking mode: expected True, got {thinking_enabled}")

    passed = all(checks)
    logger.info(f"Result: {'PASS' if passed else 'FAIL'}")
    if details:
        for d in details:
            logger.info(f"  - {d}")

    return TestResult(
        scenario=scenario,
        router_model=router_model,
        provider=provider,
        model_id=model_id,
        llm_class=llm.__class__.__name__,
        thinking_enabled=thinking_enabled,
        passed=passed,
        details="; ".join(details) if details else "All checks passed",
    )


def print_summary_table(results: List[TestResult]):
    """Print a summary table of all test results."""
    logger.info("\n")
    logger.info("=" * 100)
    logger.info("FEBRUARY 2026 STACK - MODEL ROUTING SUMMARY")
    logger.info("=" * 100)

    # Header
    header = (
        f"{'Role':<15} | "
        f"{'Complexity':>10} | "
        f"{'Router Model':<20} | "
        f"{'Provider':<12} | "
        f"{'Model ID':<25} | "
        f"{'Status':<6}"
    )
    logger.info(header)
    logger.info("-" * 100)

    # Results
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        row = (
            f"{result.scenario.role:<15} | "
            f"{result.scenario.complexity:>10} | "
            f"{result.router_model:<20} | "
            f"{result.provider:<12} | "
            f"{result.model_id:<25} | "
            f"{status:<6}"
        )
        logger.info(row)

    logger.info("-" * 100)

    # Summary stats
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    logger.info(f"Total: {passed}/{total} tests passed")

    # Detailed mapping table
    logger.info("\n")
    logger.info("=" * 100)
    logger.info("FEBRUARY 2026 MODEL MAPPING TABLE")
    logger.info("=" * 100)
    logger.info(f"{'Input':<40} -> {'Output':<50}")
    logger.info("-" * 100)

    for result in results:
        input_str = (
            f"role='{result.scenario.role}', complexity={result.scenario.complexity}"
        )
        output_str = f"{result.provider}/{result.model_id}"
        if result.thinking_enabled:
            output_str += " [THINKING]"
        logger.info(f"{input_str:<40} -> {output_str:<50}")

    # Cross-model verification explanation
    logger.info("\n")
    logger.info("=" * 100)
    logger.info("CROSS-MODEL VERIFICATION STRATEGY")
    logger.info("=" * 100)
    logger.info("Code written by Claude Opus 4.6 -> Reviewed by GPT-5.2")
    logger.info("Code written by GPT-5.2 -> Reviewed by Claude Opus (low-temp)")
    logger.info(
        "This prevents 'echo chamber' effects where models validate their own patterns"
    )


def main():
    """Main test runner."""
    logger.info("=" * 100)
    logger.info("LLM FACTORY ROUTING TEST - FEBRUARY 2026 STACK")
    logger.info("=" * 100)
    logger.info("Testing SmartRouter with updated model configurations:")
    logger.info("  - GPT-5.2-thinking for architecture (Thinking Mode)")
    logger.info("  - Claude Opus 4.6 for coding (large codebase handling)")
    logger.info("  - GPT-5.2 for code review (cross-model verification)")
    logger.info("  - Gemini 3 Pro for research (massive context window)")
    logger.info("  - Complexity threshold: 7 (premium models for >= 7)")
    logger.info("=" * 100)

    # Get scenarios
    scenarios = get_test_scenarios()
    results: List[TestResult] = []

    # Run each scenario
    for scenario in scenarios:
        try:
            result = run_test_scenario(scenario)
            results.append(result)
        except Exception as e:
            logger.error(f"Error in scenario '{scenario.name}': {e}")
            import traceback

            traceback.print_exc()

    # Print summary
    print_summary_table(results)

    # Return exit code
    all_passed = all(r.passed for r in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
