"""
Integration Stress Test for IE-1 Architectural Guardrails Rewrite Loop

Simulates the full guardrails pipeline flow:
1. Frontend agent generates code with ARCH001 violation (fetch in component)
2. Guardrails gate detects the violation → routes back to frontend
3. Frontend agent "fixes" the code (removes fetch from component)
4. Guardrails gate runs again → no critical violations → routes to tester
5. Verify: max 2 fix cycles, state fields preserved correctly

Run: pytest backend/tests/simulate_guardrails_rewrite.py -v
"""

import os
import sys

_backend = os.path.join(os.path.dirname(__file__), "..")
if _backend not in sys.path:
    sys.path.insert(0, _backend)


# =====================================================================
# Helpers
# =====================================================================


def _make_base_state():
    """Build a complete initial ProjectState dict."""
    return {
        "messages": [],
        "requirements": "Build a user dashboard",
        "project_description": "Build a user dashboard",
        "architecture": {"tech_stack": {"frontend": "react", "backend": "fastapi"}},
        "frontend_code": None,
        "backend_code": None,
        "tests": None,
        "review_results": None,
        "current_phase": "guardrails_gate",
        "iteration": 0,
        "errors": [],
        "error": None,
        "final_project": None,
        "stuck_count": 0,
        "previous_issues_embeddings": None,
        "previous_issues_text": None,
        "model_switch_history": None,
        "_override_model": None,
        "_clear_failed_context": None,
        "_include_anti_patterns": None,
        "_latest_issues_embeddings": None,
        "_latest_issues_text": None,
        "guardrails_violations": None,
        "guardrails_iteration": 0,
    }


# Violating code: fetch() directly in a component
VIOLATING_CODE = {
    "components/UserCard.tsx": """
import React from 'react';

export function UserCard({ userId }: { userId: string }) {
    const [user, setUser] = React.useState(null);

    React.useEffect(() => {
        fetch('/api/users/' + userId)
            .then(res => res.json())
            .then(data => setUser(data));
    }, [userId]);

    if (!user) return <div>Loading...</div>;
    return <div>{user.name}</div>;
}
""",
    "components/Dashboard.tsx": """
import React from 'react';
import { UserCard } from './UserCard';

export function Dashboard() {
    return (
        <div>
            <h1>Dashboard</h1>
            <UserCard userId="123" />
        </div>
    );
}
""",
}

# Fixed code: fetch moved to a hook
FIXED_CODE = {
    "hooks/useUser.ts": """
import { useState, useEffect } from 'react';

export function useUser(userId: string) {
    const [user, setUser] = useState(null);

    useEffect(() => {
        fetch('/api/users/' + userId)
            .then(res => res.json())
            .then(data => setUser(data));
    }, [userId]);

    return user;
}
""",
    "components/UserCard.tsx": """
import React from 'react';
import { useUser } from '../hooks/useUser';

export function UserCard({ userId }: { userId: string }) {
    const user = useUser(userId);
    if (!user) return <div>Loading...</div>;
    return <div>{user.name}</div>;
}
""",
    "components/Dashboard.tsx": """
import React from 'react';
import { UserCard } from './UserCard';

export function Dashboard() {
    return (
        <div>
            <h1>Dashboard</h1>
            <UserCard userId="123" />
        </div>
    );
}
""",
}


# =====================================================================
# Test: Full Rewrite Loop Simulation
# =====================================================================


class TestFullRewriteLoopSimulation:
    """
    Simulate the full guardrails pipeline:
    Iteration 1: violating code → gate blocks → route to frontend
    Iteration 2: fixed code → gate passes → route to tester
    """

    def test_iteration_1_blocks_and_routes_to_frontend(self):
        """First iteration: ARCH001 violation detected, routed back to frontend."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()
        state["frontend_code"] = VIOLATING_CODE

        # Run guardrails gate
        gate_result = builder._guardrails_gate_node(state)

        # Merge result into state
        state.update(gate_result)

        # Verify violations detected
        violations = state["guardrails_violations"]
        assert len(violations) >= 1
        arch001 = [v for v in violations if v["rule_id"] == "ARCH001"]
        assert len(arch001) >= 1, "ARCH001 should detect fetch in component"

        # Route after guardrails
        route = builder._route_after_guardrails(state)
        assert route == "frontend", "Should route back to frontend for fix"
        assert state["guardrails_iteration"] == 1

    def test_iteration_2_passes_and_routes_to_tester(self):
        """Second iteration: fixed code passes, routed to tester."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()
        state["frontend_code"] = FIXED_CODE
        state["guardrails_iteration"] = 1  # Already had one fix cycle

        # Run guardrails gate
        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)

        # Fixed code should have no CRITICAL violations
        violations = state["guardrails_violations"]
        critical = [v for v in violations if v.get("severity") == "critical"]
        assert (
            len(critical) == 0
        ), f"Fixed code should have no CRITICAL violations, got: {critical}"

        # Route after guardrails
        route = builder._route_after_guardrails(state)
        assert route == "tester", "Should proceed to tester after fix"

    def test_full_two_iteration_flow(self):
        """End-to-end: two iterations through the guardrails gate."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)

        # === ITERATION 1: Violating code ===
        state = _make_base_state()
        state["frontend_code"] = VIOLATING_CODE

        gate_result_1 = builder._guardrails_gate_node(state)
        state.update(gate_result_1)

        route_1 = builder._route_after_guardrails(state)
        assert route_1 == "frontend"
        assert state["guardrails_iteration"] == 1

        # Save state between iterations (simulates graph state persistence)
        state["guardrails_violations"]

        # === ITERATION 2: Fixed code ===
        state["frontend_code"] = FIXED_CODE

        gate_result_2 = builder._guardrails_gate_node(state)
        state.update(gate_result_2)

        route_2 = builder._route_after_guardrails(state)
        assert route_2 == "tester"

        # Verify iteration count didn't regress
        assert (
            state["guardrails_iteration"] == 1
        )  # Not incremented since no critical violations


# =====================================================================
# Test: Max Fix Cycle Enforcement
# =====================================================================


class TestMaxFixCycleEnforcement:
    """Verify that after 2 fix cycles, guardrails gives up and proceeds."""

    def test_stubborn_violation_proceeds_after_2_cycles(self):
        """
        Simulate frontend agent that NEVER fixes the violation.
        After 2 cycles, guardrails should let it through to tester.
        """
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()
        state["frontend_code"] = VIOLATING_CODE

        routes = []
        for i in range(3):
            gate_result = builder._guardrails_gate_node(state)
            state.update(gate_result)
            route = builder._route_after_guardrails(state)
            routes.append(route)

            if route == "tester":
                break

        # Should route to frontend twice, then tester
        assert routes[0] == "frontend", "Cycle 1: should route to frontend"
        assert routes[1] == "frontend", "Cycle 2: should route to frontend"
        assert routes[2] == "tester", "Cycle 3: should give up and route to tester"
        assert (
            state["guardrails_iteration"] == 3
        )  # 3 because it was incremented before the > 2 check

    def test_fix_on_second_attempt(self):
        """Frontend fixes on second attempt — should proceed to tester."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()

        # Iteration 1: violating code
        state["frontend_code"] = VIOLATING_CODE
        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)
        route = builder._route_after_guardrails(state)
        assert route == "frontend"

        # Iteration 2: fixed code
        state["frontend_code"] = FIXED_CODE
        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)
        route = builder._route_after_guardrails(state)
        assert route == "tester"


# =====================================================================
# Test: Fix Prompt Injection
# =====================================================================


class TestFixPromptInjection:
    """Verify the fix prompt is injected into state messages."""

    def test_fix_prompt_added_to_messages(self):
        """When routing to frontend, a targeted fix prompt should be in messages."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()
        state["frontend_code"] = VIOLATING_CODE

        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)

        initial_msg_count = len(state["messages"])

        route = builder._route_after_guardrails(state)
        assert route == "frontend"

        # Check that a fix prompt was added
        messages = state["messages"]
        assert (
            len(messages) > initial_msg_count
        ), "Fix prompt should have been added to messages"

        last_msg = messages[-1]
        assert "ARCHITECTURAL GUARDRAILS FIX REQUIRED" in last_msg.content
        assert "ARCH001" in last_msg.content
        assert "components/UserCard.tsx" in last_msg.content


# =====================================================================
# Test: State Consistency Across Iterations
# =====================================================================


class TestStateConsistency:
    """Verify state fields are preserved correctly across iterations."""

    def test_guardrails_iteration_increments(self):
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()
        state["frontend_code"] = VIOLATING_CODE

        assert state["guardrails_iteration"] == 0

        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)
        builder._route_after_guardrails(state)
        assert state["guardrails_iteration"] == 1

        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)
        builder._route_after_guardrails(state)
        assert state["guardrails_iteration"] == 2

    def test_violations_updated_each_iteration(self):
        """Violations list should reflect current code state, not stale."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()

        # Iter 1: violating code
        state["frontend_code"] = VIOLATING_CODE
        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)
        violations_1 = state["guardrails_violations"]
        assert len([v for v in violations_1 if v["rule_id"] == "ARCH001"]) >= 1

        builder._route_after_guardrails(state)

        # Iter 2: fixed code
        state["frontend_code"] = FIXED_CODE
        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)
        violations_2 = state["guardrails_violations"]
        critical_2 = [v for v in violations_2 if v.get("severity") == "critical"]
        assert len(critical_2) == 0, "Fixed code should have no CRITICAL violations"

    def test_other_state_fields_untouched(self):
        """Guardrails gate should not modify unrelated state fields."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()
        state["frontend_code"] = VIOLATING_CODE
        state["stuck_count"] = 1
        state["model_switch_history"] = [{"from": "a", "to": "b", "stage": 1}]

        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)

        # These fields should be untouched
        assert state["stuck_count"] == 1
        assert state["model_switch_history"] == [{"from": "a", "to": "b", "stage": 1}]
        assert state["requirements"] == "Build a user dashboard"


# =====================================================================
# Test: Edge Cases
# =====================================================================


class TestGuardrailsEdgeCases:

    def test_empty_frontend_code(self):
        """Gate should handle empty frontend code gracefully."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()
        state["frontend_code"] = {}

        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)
        assert state["guardrails_violations"] == []

        route = builder._route_after_guardrails(state)
        assert route == "tester"

    def test_none_frontend_code(self):
        """Gate should handle None frontend code gracefully."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()
        state["frontend_code"] = None

        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)
        assert state["guardrails_violations"] == []

    def test_high_severity_only_does_not_block(self):
        """HIGH violations should NOT trigger rewrite loop, only CRITICAL."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()
        # A file with 350 lines — ARCH003 HIGH violation, but no CRITICAL
        state["frontend_code"] = {
            "hooks/useData.ts": "\n".join([f"// line {i}" for i in range(350)]),
        }

        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)

        violations = state["guardrails_violations"]
        assert len(violations) >= 1  # ARCH003 should be detected

        critical = [v for v in violations if v.get("severity") == "critical"]
        assert len(critical) == 0  # No CRITICAL violations

        route = builder._route_after_guardrails(state)
        assert route == "tester"  # HIGH-only violations don't block

    def test_multiple_violations_multiple_files(self):
        """Multiple files with multiple violation types."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = _make_base_state()
        state["frontend_code"] = {
            "components/Bad.tsx": """
const data = fetch('/api/data');
const el = document.getElementById('root');
""",
            "components/A.tsx": "import { B } from './B';\nexport const A = () => <B />;",
            "components/B.tsx": "import { A } from './A';\nexport const B = () => <A />;",
        }

        gate_result = builder._guardrails_gate_node(state)
        state.update(gate_result)

        violations = state["guardrails_violations"]
        rule_ids = {v["rule_id"] for v in violations}
        # Should detect ARCH001 (fetch), ARCH005 (getElementById), ARCH008 (circular dep)
        assert "ARCH001" in rule_ids
        assert "ARCH005" in rule_ids or "ARCH008" in rule_ids  # At least one more
