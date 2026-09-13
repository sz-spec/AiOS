"""
Test Suite for IE-1 Architectural Guardrails — Phase 2.0 Subsystem #3

Tests:
    - All 10 Iron Rules (ARCH001-ARCH010) detection
    - Guardrails gate node integration with pipeline
    - Fix cycle limit (max 2)
    - Routing logic (_route_after_guardrails)
    - ArchitecturalRules in StaticAnalyzer
    - build_fix_prompt generation

Run: pytest backend/tests/test_guardrails.py -v
"""

import os
import sys

# Ensure backend is on sys.path
_backend = os.path.join(os.path.dirname(__file__), "..")
if _backend not in sys.path:
    sys.path.insert(0, _backend)


# =====================================================================
# ARCH001 — Business logic in UI component
# =====================================================================


class TestARCH001:
    """Test: fetch/axios/ORM in components/ is flagged CRITICAL."""

    def test_fetch_in_component(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/UserCard.tsx": """
import React from 'react';
export function UserCard() {
    const data = fetch('/api/users');
    return <div>{data}</div>;
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch001 = [v for v in violations if v.rule_id == "ARCH001"]
        assert len(arch001) >= 1
        assert arch001[0].severity.value == "critical"

    def test_axios_in_component(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/Dashboard.tsx": """
import axios from 'axios';
export function Dashboard() {
    const res = axios.get('/api/stats');
    return <div />;
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch001 = [v for v in violations if v.rule_id == "ARCH001"]
        assert len(arch001) >= 1

    def test_service_file_not_flagged(self):
        """fetch in services/ should NOT be flagged."""
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "lib/api/users.ts": """
export async function getUsers() {
    return fetch('/api/users');
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch001 = [v for v in violations if v.rule_id == "ARCH001"]
        assert len(arch001) == 0

    def test_hook_file_not_flagged(self):
        """fetch in hooks/ should NOT be flagged."""
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "hooks/useUsers.ts": """
export function useUsers() {
    return fetch('/api/users');
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch001 = [v for v in violations if v.rule_id == "ARCH001"]
        assert len(arch001) == 0


# =====================================================================
# ARCH002 — API call outside service layer
# =====================================================================


class TestARCH002:
    def test_fetch_in_util_file(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/helpers.ts": """
export function loadData() {
    return fetch('/api/data');
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        # Should catch either ARCH001 (UI) or ARCH002 (outside service layer)
        api_violations = [v for v in violations if v.rule_id in ("ARCH001", "ARCH002")]
        assert len(api_violations) >= 1


# =====================================================================
# ARCH003 — File exceeds 300 lines
# =====================================================================


class TestARCH003:
    def test_large_file_flagged(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/BigComponent.tsx": "\n".join(
                [f"// line {i}" for i in range(350)]
            ),
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch003 = [v for v in violations if v.rule_id == "ARCH003"]
        assert len(arch003) == 1
        assert "350" in arch003[0].message

    def test_small_file_not_flagged(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/SmallComponent.tsx": "\n".join(
                [f"// line {i}" for i in range(50)]
            ),
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch003 = [v for v in violations if v.rule_id == "ARCH003"]
        assert len(arch003) == 0


# =====================================================================
# ARCH005 — Direct DOM manipulation
# =====================================================================


class TestARCH005:
    def test_getelementbyid_flagged(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/Modal.tsx": """
export function Modal() {
    const el = document.getElementById('modal');
    el.innerHTML = '<p>Hello</p>';
    return null;
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch005 = [v for v in violations if v.rule_id == "ARCH005"]
        assert len(arch005) >= 1


# =====================================================================
# ARCH006 — Inconsistent naming
# =====================================================================


class TestARCH006:
    def test_lowercase_component_flagged(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/userCard.tsx": "export function userCard() { return null; }",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch006 = [v for v in violations if v.rule_id == "ARCH006"]
        assert len(arch006) >= 1

    def test_pascal_component_ok(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/UserCard.tsx": "export function UserCard() { return null; }",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch006 = [v for v in violations if v.rule_id == "ARCH006"]
        assert len(arch006) == 0


# =====================================================================
# ARCH007 — Missing TypeScript types
# =====================================================================


class TestARCH007:
    def test_excessive_any_flagged(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "lib/api/client.ts": """
export function get(url: any, opts: any, data: any): any {
    return fetch(url);
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch007 = [v for v in violations if v.rule_id == "ARCH007"]
        assert len(arch007) >= 1

    def test_few_any_ok(self):
        """1-2 'any' usages are tolerated."""
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "lib/api/client.ts": """
export function get(url: string, opts: any): Response {
    return fetch(url);
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch007 = [v for v in violations if v.rule_id == "ARCH007"]
        assert len(arch007) == 0


# =====================================================================
# ARCH008 — Circular dependency
# =====================================================================


class TestARCH008:
    def test_circular_dep_flagged(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/A.tsx": "import { B } from './B';\nexport const A = () => <B />;",
            "components/B.tsx": "import { A } from './A';\nexport const B = () => <A />;",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch008 = [v for v in violations if v.rule_id == "ARCH008"]
        assert len(arch008) >= 1
        assert arch008[0].severity.value == "critical"

    def test_no_circular_dep_ok(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/A.tsx": "import { B } from './B';\nexport const A = () => <B />;",
            "components/B.tsx": "export const B = () => <div />;",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch008 = [v for v in violations if v.rule_id == "ARCH008"]
        assert len(arch008) == 0


# =====================================================================
# ARCH009 — Unused imports
# =====================================================================


class TestARCH009:
    def test_unused_import_flagged(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/Card.tsx": """
import { useState, useEffect, useRef } from 'react';

export function Card() {
    const [val, setVal] = useState(0);
    return <div>{val}</div>;
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch009 = [v for v in violations if v.rule_id == "ARCH009"]
        # useEffect and useRef are unused
        assert len(arch009) >= 1
        unused_names = [v.message for v in arch009]
        assert any("useEffect" in m for m in unused_names)

    def test_all_used_ok(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/Card.tsx": """
import { useState } from 'react';

export function Card() {
    const [val, setVal] = useState(0);
    return <div>{val}</div>;
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch009 = [v for v in violations if v.rule_id == "ARCH009"]
        assert len(arch009) == 0


# =====================================================================
# ARCH010 — Missing error boundary
# =====================================================================


class TestARCH010:
    def test_page_without_error_boundary_flagged(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "app/dashboard/page.tsx": """
export default function DashboardPage() {
    return <div>Dashboard</div>;
}
""",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch010 = [v for v in violations if v.rule_id == "ARCH010"]
        assert len(arch010) == 1

    def test_page_with_sibling_error_boundary_ok(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "app/dashboard/page.tsx": "export default function DashboardPage() { return <div />; }",
            "app/dashboard/error.tsx": "export default function Error() { return <div>Error</div>; }",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch010 = [v for v in violations if v.rule_id == "ARCH010"]
        assert len(arch010) == 0


# =====================================================================
# Guardrails Gate Node — Pipeline Integration
# =====================================================================


class TestGuardrailsGateNode:
    """Test the _guardrails_gate_node and _route_after_guardrails."""

    def _make_state(self, frontend_code=None, backend_code=None, guardrails_iter=0):
        """Build a minimal ProjectState dict."""
        return {
            "messages": [],
            "requirements": "test",
            "project_description": "test",
            "architecture": None,
            "frontend_code": frontend_code,
            "backend_code": backend_code,
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
            "guardrails_iteration": guardrails_iter,
        }

    def test_gate_detects_violations(self):
        """Gate node should detect ARCH001 and return violations in state."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        # Call the gate method directly (bypass graph)
        state = self._make_state(
            frontend_code={
                "components/UserCard.tsx": "const data = fetch('/api/users');",
            }
        )
        result = builder._guardrails_gate_node(state)
        assert "guardrails_violations" in result
        assert len(result["guardrails_violations"]) >= 1
        assert any(v["rule_id"] == "ARCH001" for v in result["guardrails_violations"])

    def test_gate_passes_clean_code(self):
        """Gate node should return empty violations for clean code."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state(
            frontend_code={
                "hooks/useUsers.ts": "export function useUsers() { return fetch('/api/users'); }",
                "components/UserCard.tsx": "import { useUsers } from '../hooks/useUsers';\nexport function UserCard() { return null; }",
            }
        )
        result = builder._guardrails_gate_node(state)
        violations = result.get("guardrails_violations", [])
        critical = [v for v in violations if v.get("severity") == "critical"]
        assert len(critical) == 0

    def test_route_to_tester_when_clean(self):
        """No critical violations → route to db_migrations (Phase 2.5)."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state()
        state["guardrails_violations"] = []
        result = builder._route_after_guardrails(state)
        assert result == "db_migrations"

    def test_route_to_frontend_when_critical(self):
        """Critical violations → route back to frontend for fix."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state()
        state["guardrails_violations"] = [
            {
                "rule_id": "ARCH001",
                "file_path": "components/UserCard.tsx",
                "line": 3,
                "message": "fetch in component",
                "fix_instruction": "Move to hook",
                "severity": "critical",
            }
        ]
        result = builder._route_after_guardrails(state)
        assert result == "frontend"
        assert state["guardrails_iteration"] == 1

    def test_route_to_tester_after_max_cycles(self):
        """After 2 fix cycles, proceed to db_migrations regardless (Phase 2.5)."""
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        state = self._make_state(guardrails_iter=2)
        state["guardrails_violations"] = [
            {
                "rule_id": "ARCH001",
                "file_path": "components/X.tsx",
                "line": 1,
                "message": "fetch in component",
                "fix_instruction": "Move to hook",
                "severity": "critical",
            }
        ]
        result = builder._route_after_guardrails(state)
        assert result == "db_migrations"  # Max cycles reached, proceed anyway


# =====================================================================
# Fix Prompt Generation
# =====================================================================


class TestFixPrompt:
    def test_build_fix_prompt(self):
        from ai.agents.guardrails import (
            ArchitecturalGuardrails,
            Violation,
            ViolationSeverity,
        )

        g = ArchitecturalGuardrails()
        violations = [
            Violation(
                rule_id="ARCH001",
                file_path="components/UserCard.tsx",
                line=3,
                message="fetch() detected in UI component",
                fix_instruction="Move to hooks/useUsers.ts",
                severity=ViolationSeverity.CRITICAL,
            ),
            Violation(
                rule_id="ARCH005",
                file_path="components/Modal.tsx",
                line=7,
                message="document.getElementById in React",
                fix_instruction="Use useRef instead",
                severity=ViolationSeverity.HIGH,
            ),
        ]
        prompt = g.build_fix_prompt(violations, "frontend")
        assert "ARCH001" in prompt
        assert "ARCH005" in prompt
        assert "components/UserCard.tsx" in prompt
        assert "components/Modal.tsx" in prompt
        assert "ONLY the modified files" in prompt


# =====================================================================
# StaticAnalyzer Integration (ArchitecturalRules in rules.py)
# =====================================================================


class TestStaticAnalyzerArchRules:
    """Verify ArchitecturalRules is registered and runs in StaticAnalyzer."""

    def test_arch001_in_static_analyzer(self):
        from code_review.rules import StaticAnalyzer

        code = "const data = fetch('/api/users');"
        findings = StaticAnalyzer.analyze(code, "components/UserCard.tsx")
        arch_findings = [f for f in findings if f.id.startswith("ARCH001")]
        assert len(arch_findings) >= 1

    def test_arch005_in_static_analyzer(self):
        from code_review.rules import StaticAnalyzer

        code = "const el = document.getElementById('root');"
        findings = StaticAnalyzer.analyze(code, "components/Modal.tsx")
        arch_findings = [f for f in findings if f.id.startswith("ARCH005")]
        assert len(arch_findings) >= 1

    def test_arch003_line_count(self):
        from code_review.rules import StaticAnalyzer

        code = "\n".join([f"// line {i}" for i in range(350)])
        findings = StaticAnalyzer.analyze(code, "components/BigFile.tsx")
        arch003 = [f for f in findings if f.id.startswith("ARCH003")]
        assert len(arch003) == 1

    def test_no_false_positives_on_clean_code(self):
        from code_review.rules import StaticAnalyzer

        code = """
import { useState } from 'react';

export function Counter() {
    const [count, setCount] = useState(0);
    return <button onClick={() => setCount(count + 1)}>{count}</button>;
}
"""
        findings = StaticAnalyzer.analyze(code, "components/Counter.tsx")
        # Filter only ARCH rules
        arch_findings = [f for f in findings if f.id.startswith("ARCH")]
        assert len(arch_findings) == 0


# =====================================================================
# ProjectState Fields
# =====================================================================


class TestProjectStateFields:
    """Verify new guardrails fields exist in ProjectState."""

    def test_guardrails_fields_in_state(self):
        from ai.agents.multi_agent import ProjectState

        hints = ProjectState.__annotations__
        assert "guardrails_violations" in hints
        assert "guardrails_iteration" in hints


# =====================================================================
# Edge Cases
# =====================================================================


class TestEdgeCases:
    def test_empty_project(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        g = ArchitecturalGuardrails()
        violations = g.validate_project({})
        assert violations == []

    def test_non_code_files_ignored(self):
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "README.md": "# My Project\nThis has fetch() in docs",
            "package.json": '{"name": "test"}',
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        # Should not flag ARCH001 in non-code files
        arch001 = [v for v in violations if v.rule_id == "ARCH001"]
        assert len(arch001) == 0

    def test_python_file_not_flagged_for_jsx_rules(self):
        """Python files should not trigger JSX-specific rules like ARCH005."""
        from ai.agents.guardrails import ArchitecturalGuardrails

        files = {
            "components/helper.py": "result = document.getElementById('test')",
        }
        g = ArchitecturalGuardrails()
        violations = g.validate_project(files)
        arch005 = [v for v in violations if v.rule_id == "ARCH005"]
        assert len(arch005) == 0

    def test_get_critical_violations_filter(self):
        from ai.agents.guardrails import ArchitecturalGuardrails, ViolationSeverity

        g = ArchitecturalGuardrails()
        files = {
            "components/Bad.tsx": "const d = fetch('/api/x');\n" * 5
            + "\n".join([f"// {i}" for i in range(350)]),
        }
        violations = g.validate_project(files)
        critical = g.get_critical_violations(violations)
        for v in critical:
            assert v.severity == ViolationSeverity.CRITICAL

    def test_violations_to_dict(self):
        from ai.agents.guardrails import Violation, ViolationSeverity

        v = Violation(
            rule_id="ARCH001",
            file_path="test.tsx",
            line=5,
            message="test",
            fix_instruction="fix it",
            severity=ViolationSeverity.CRITICAL,
        )
        d = v.to_dict()
        assert d["rule_id"] == "ARCH001"
        assert d["severity"] == "critical"
        assert d["line"] == 5
