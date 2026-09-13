"""
Agent Self-Correction Sanity Test
=================================
Demonstrates the ReflectionLoop and self-correction capabilities.

This test:
1. Stores a requirement in ProjectMemoryService
2. Simulates an agent that initially produces invalid output
3. Uses ReflectionLoop to detect and correct the error
4. Verifies the corrected output meets all requirements
"""

import sys
import os

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
from typing import Dict, Any, Tuple

# Configure logging to show the self-correction process
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%H:%M:%S",
)

# Set specific loggers to show detailed output
logging.getLogger("src.efficiency.reflection").setLevel(logging.DEBUG)
logging.getLogger("core.project_memory").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


def test_agent_self_correction():
    """
    End-to-end test of the agent self-correction loop.

    Scenario:
    - Requirement: All API responses must include 'metadata.version'
    - Agent initially generates response WITHOUT metadata
    - ReflectionLoop detects the missing requirement
    - Agent corrects and produces valid output
    """
    print("\n" + "=" * 70)
    print("AGENT SELF-CORRECTION SANITY TEST")
    print("=" * 70 + "\n")

    # =========================================================================
    # Step 1: Initialize ProjectMemoryService and store requirement
    # =========================================================================
    print("STEP 1: Initializing ProjectMemoryService and storing requirement")
    print("-" * 70)

    from core.project_memory import ProjectMemoryService

    # Create a fresh service for this test
    project_id = "sanity-test-self-correction"
    memory_service = ProjectMemoryService(project_id=project_id)

    # Clear any previous test data
    memory_service.clear_project_memory()

    # Store the requirement as a project spec
    requirement_text = (
        "All generated API responses must include a 'metadata' field "
        "with a 'version' key. The version should be a string like '1.0.0'."
    )

    spec = memory_service.store_project_spec(
        title="API Response Format Requirement",
        content=requirement_text,
        version="1.0",
        tags=["api", "format", "metadata", "requirement"],
    )

    print(f"  Stored requirement: {spec.title}")
    print(f"  Memory ID: {spec.id}")
    print(f"  Content: {requirement_text[:60]}...")
    print()

    # =========================================================================
    # Step 2: Define the agent task and custom requirement checker
    # =========================================================================
    print("STEP 2: Defining agent task and requirement checker")
    print("-" * 70)

    from src.efficiency.reflection import (
        ReflectionLoop,
        RequirementChecker,
        LogicErrorDetector,
    )

    # Custom check function for metadata.version requirement
    def check_metadata_version(output: Any) -> Tuple[bool, str]:
        """Check if output has metadata.version field."""
        if not isinstance(output, dict):
            try:
                output = json.loads(output) if isinstance(output, str) else {}
            except json.JSONDecodeError:
                return False, "Output is not valid JSON"

        # Handle wrapped output ({"output": {...}})
        if "output" in output and isinstance(output["output"], dict):
            output = output["output"]

        if "metadata" not in output:
            return False, "Missing 'metadata' field in response"

        if not isinstance(output["metadata"], dict):
            return False, "'metadata' must be a dictionary"

        if "version" not in output["metadata"]:
            return False, "Missing 'version' key in metadata"

        return True, "Response has valid metadata.version"

    # Create requirement checker with our custom check
    checker = RequirementChecker()
    checker.add_custom_check(check_metadata_version)

    # Also add the text requirement for keyword matching
    checker.add_requirement("must include metadata field with version key")

    print("  Created RequirementChecker with custom validation")
    print("  - Checks for 'metadata' field presence")
    print("  - Validates 'version' key in metadata")
    print()

    # =========================================================================
    # Step 3: Simulate agent behavior (first output misses requirement)
    # =========================================================================
    print("STEP 3: Simulating agent with deliberate first failure")
    print("-" * 70)

    # Track attempt count to simulate improvement over time
    attempt_tracker = {"count": 0, "outputs": []}

    def generate_user_profile_response(state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Simulated agent task: Generate a JSON response for a user profile.

        On first attempt: Produces output WITHOUT metadata (fails requirement)
        On subsequent attempts: Uses critique feedback to correct the output
        """
        attempt_tracker["count"] += 1
        attempt = attempt_tracker["count"]

        logger.info(f"Agent executing - Attempt #{attempt}")

        # Check if we have critique feedback from previous attempt
        critique_context = state.get("_critique", {})
        is_correction = state.get("_correction_attempt", False)

        if is_correction and critique_context:
            logger.info("  Correction mode - addressing previous issues")
            logger.info(f"  Previous issues: {critique_context.get('issues', [])}")

        # Simulate agent behavior based on attempt number
        if attempt == 1:
            # First attempt: Agent forgets the metadata requirement
            output = {
                "user": {
                    "id": "usr_12345",
                    "name": "John Doe",
                    "email": "john@example.com",
                },
                "status": "active",
            }
            logger.warning("  First attempt - Generated response WITHOUT metadata")
        else:
            # Correction attempt: Agent learns and includes metadata
            output = {
                "user": {
                    "id": "usr_12345",
                    "name": "John Doe",
                    "email": "john@example.com",
                },
                "status": "active",
                "metadata": {
                    "version": "1.0.0",
                    "generated_at": "2026-02-09T12:00:00Z",
                    "api_version": "v2",
                },
            }
            logger.info("  Correction attempt - Generated response WITH metadata")

        attempt_tracker["outputs"].append(output)

        return {"output": output}

    print("  Agent task: 'Generate a simple JSON response for a user profile'")
    print("  First output will deliberately MISS the 'metadata' field")
    print()

    # =========================================================================
    # Step 4: Run ReflectionLoop to detect and correct the error
    # =========================================================================
    print("STEP 4: Running ReflectionLoop with self-correction")
    print("-" * 70)

    # Create logic error detector for additional validation
    logic_detector = LogicErrorDetector()

    def check_metadata_logic(output, ctx):
        """Check for metadata in output, handling wrapped format."""
        if not isinstance(output, dict):
            return None
        # Handle wrapped output
        actual = output.get("output", output) if "output" in output else output
        if isinstance(actual, dict) and "metadata" not in actual:
            return "Response missing metadata field"
        return None

    logic_detector.add_rule(check_metadata_logic)

    # Create the reflection loop
    loop = ReflectionLoop(
        requirements=["must include metadata field with version key"],
        max_corrections=3,
        project_id=project_id,  # Enable memory integration
    )

    # Add our custom check to the loop's checker
    loop.checker.add_custom_check(check_metadata_version)

    # Add logic detector
    loop.logic_detector = logic_detector

    print("  ReflectionLoop configured:")
    print("    - max_corrections: 3")
    print(f"    - project_id: {project_id}")
    print("    - memory integration: enabled")
    print()
    print("  Starting reflection loop execution...")
    print("  " + "-" * 66)

    # Define the correction function
    def correct_output(output: Any, critique: Any, state: Dict) -> Dict[str, Any]:
        """Correction function that adds critique context for the agent."""
        logger.info("Correction triggered - preparing retry with critique context")
        state["_critique"] = critique.to_dict()
        state["_correction_attempt"] = True
        state["_previous_output"] = output
        return generate_user_profile_response(state)

    # Initial state
    initial_state = {
        "task": "Generate a simple JSON response for a user profile",
        "requirements": ["Include metadata.version field"],
    }

    # Run the reflection loop
    final_result, reflection_state = loop.run(
        execute_fn=generate_user_profile_response,
        correct_fn=correct_output,
        state=initial_state,
    )

    print("  " + "-" * 66)
    print()

    # =========================================================================
    # Step 5: Verify the results
    # =========================================================================
    print("STEP 5: Verifying self-correction results")
    print("-" * 70)

    print("\n  Reflection Loop Summary:")
    print(f"    - Total attempts: {reflection_state.current_attempt}")
    print(f"    - Converged (requirements met): {reflection_state.converged}")
    print(f"    - Mistakes recorded: {len(reflection_state.session_mistakes)}")

    # Show correction history
    print("\n  Correction History:")
    for i, attempt in enumerate(reflection_state.correction_history):
        critique = attempt.critique
        print(f"\n    Attempt {i + 1}:")
        print(f"      Result: {critique.result.value}")
        print(f"      Score: {critique.score:.2f}")
        print(f"      Passed: {critique.passed}")
        if critique.issues:
            print(f"      Issues: {critique.issues}")

    # Show the outputs
    print("\n  Agent Outputs Comparison:")
    print("\n    First Output (Invalid):")
    first_output = attempt_tracker["outputs"][0]
    print(f"      {json.dumps(first_output, indent=6)}")
    print(f"      Has metadata: {'metadata' in first_output}")

    if len(attempt_tracker["outputs"]) > 1:
        print("\n    Corrected Output (Valid):")
        corrected_output = attempt_tracker["outputs"][-1]
        print(f"      {json.dumps(corrected_output, indent=6)}")
        print(f"      Has metadata: {'metadata' in corrected_output}")
        print(f"      Has version: {'version' in corrected_output.get('metadata', {})}")

    # Check project memory for recorded mistakes
    print("\n  Project Memory (Learned Mistakes):")
    recent_memories = memory_service.recall_recent(k=5)
    mistake_memories = [
        m for m in recent_memories if "mistake" in m.tags or "correction" in m.tags
    ]

    if mistake_memories:
        for mem in mistake_memories:
            print(f"    - [{mem.memory_type}] {mem.title or 'Untitled'}")
            print(f"      Tags: {mem.tags}")
    else:
        # Check session mistakes directly
        if reflection_state.session_mistakes:
            print(
                f"    Session mistakes recorded: {len(reflection_state.session_mistakes)}"
            )
            for mistake in reflection_state.session_mistakes:
                print(f"    - Attempt {mistake['attempt']}: {mistake['error_type']}")
                print(f"      Message: {mistake['error_message'][:80]}...")

    # =========================================================================
    # Assertions / Test Verification
    # =========================================================================
    print("\n" + "=" * 70)
    print("TEST ASSERTIONS")
    print("=" * 70)

    # Verify the loop converged
    assert reflection_state.converged, "ReflectionLoop should have converged"
    print("  [PASS] ReflectionLoop converged successfully")

    # Verify multiple attempts were made (first failed, then corrected)
    assert reflection_state.current_attempt >= 2, "Should have made at least 2 attempts"
    print(
        f"  [PASS] Made {reflection_state.current_attempt} attempts (detected and corrected)"
    )

    # Verify the final output has metadata.version
    if isinstance(final_result, dict) and "output" in final_result:
        final_output = final_result["output"]
    else:
        final_output = final_result

    assert "metadata" in final_output, "Final output should have 'metadata' field"
    print("  [PASS] Final output contains 'metadata' field")

    assert "version" in final_output["metadata"], "Metadata should have 'version' key"
    print("  [PASS] Metadata contains 'version' key")

    # Verify first attempt was identified as failing
    first_critique = reflection_state.correction_history[0].critique
    assert not first_critique.passed, "First attempt should have failed critique"
    print("  [PASS] First attempt correctly identified as failing requirements")

    # Verify last attempt passed
    last_critique = reflection_state.correction_history[-1].critique
    assert last_critique.passed, "Last attempt should have passed critique"
    print("  [PASS] Final attempt passed all requirement checks")

    # Cleanup
    memory_service.clear_project_memory()

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED - Self-correction loop working correctly!")
    print("=" * 70 + "\n")

    return True


def test_reflection_middleware_decorator():
    """
    Test the @reflection_middleware decorator for simpler use cases.
    """
    print("\n" + "=" * 70)
    print("REFLECTION MIDDLEWARE DECORATOR TEST")
    print("=" * 70 + "\n")

    from src.efficiency.reflection import reflection_middleware

    attempt_count = [0]

    @reflection_middleware(
        requirements=["output must be a dictionary", "must have status field"],
        max_corrections=3,
    )
    def generate_response(state: Dict[str, Any]) -> Dict[str, Any]:
        """Decorated function with automatic reflection."""
        attempt_count[0] += 1

        if attempt_count[0] == 1:
            # First attempt: return string (fails requirement)
            return {"output": "invalid string response"}
        else:
            # Corrected: return proper dict with status
            return {"output": {"data": "result", "status": "success"}}

    print("  Running decorated function with @reflection_middleware...")
    result = generate_response({})

    print(f"\n  Result: {result}")
    print(f"  Attempts made: {attempt_count[0]}")

    # Check reflection metadata
    if "_reflection" in result:
        print("  Reflection metadata:")
        print(f"    - Attempts: {result['_reflection'].get('attempts')}")
        print(f"    - Converged: {result['_reflection'].get('converged')}")

    print("\n  [PASS] Decorator test completed")
    print("=" * 70 + "\n")

    return True


if __name__ == "__main__":
    print("\n" + "#" * 70)
    print("# SELF-CORRECTION SANITY TEST SUITE")
    print("#" * 70)

    # Run the main test
    test_agent_self_correction()

    # Run the decorator test
    test_reflection_middleware_decorator()

    print("\n" + "#" * 70)
    print("# ALL SANITY TESTS COMPLETED SUCCESSFULLY")
    print("#" * 70 + "\n")
