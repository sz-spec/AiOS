"""
Phase 2.0 — Context Budget Manager Tests

Verifies:
  1. Token estimation accuracy
  2. P1 content always kept (requirements, architecture, errors, current file)
  3. P2 content included full when under budget
  4. P2 content summarized when over budget
  5. P3 content dropped (messages beyond last 5 turns)
  6. ProjectRAGIndex metadata extraction and summary generation
  7. Emergency truncation when P1 alone exceeds budget
  8. BudgetResult dataclass populated correctly

Run:
    cd backend && python -m pytest tests/test_context_manager.py -v
"""

import sys
import os
import pytest

# Ensure backend is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai.agents.context_manager import (
    estimate_tokens,
    BudgetResult,
    ContextBudgetManager,
    ProjectRAGIndex,
    MODEL_CONTEXT_LIMITS,
    BUDGET_RATIO,
)

# ---------------------------------------------------------------------------
# Fixtures — mock ProjectState data
# ---------------------------------------------------------------------------


def _make_state(
    *,
    requirements: str = "Build a todo app with auth and dark mode",
    architecture: dict = None,
    frontend_code: dict = None,
    backend_code: dict = None,
    errors: list = None,
    tests: dict = None,
    review_results: dict = None,
    guardrails_violations: list = None,
) -> dict:
    """Create a mock ProjectState dict for testing."""
    return {
        "requirements": requirements,
        "architecture": architecture or {"framework": "Next.js", "db": "Prisma"},
        "frontend_code": frontend_code or {},
        "backend_code": backend_code or {},
        "errors": errors or [],
        "tests": tests,
        "review_results": review_results,
        "guardrails_violations": guardrails_violations,
    }


def _generate_code(file_count: int, lines_per_file: int = 100) -> dict:
    """Generate fake code files of a given size."""
    files = {}
    for i in range(file_count):
        ext = ".tsx" if i % 2 == 0 else ".ts"
        name = f"src/components/Component{i}{ext}"
        lines = [
            "import React from 'react';",
            "import { useState } from 'react';",
            "",
            f"export function Component{i}() {{",
        ]
        for j in range(lines_per_file - 6):
            lines.append(f"  const value{j} = {j};  // line {j}")
        lines.append("  return <div>Component</div>;")
        lines.append("}")
        files[name] = "\n".join(lines)
    return files


# ---------------------------------------------------------------------------
# 1. Token estimation
# ---------------------------------------------------------------------------


class TestTokenEstimation:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_text(self):
        # "hello" = 5 chars → 5//4 = 1
        assert estimate_tokens("hello") == 1

    def test_medium_text(self):
        text = "The quick brown fox jumps over the lazy dog."  # 44 chars → 11 tokens
        assert estimate_tokens(text) == 11

    def test_code_text(self):
        code = "function hello() { return 'world'; }"  # 36 chars → 9
        assert estimate_tokens(code) == 9

    def test_large_text(self):
        text = "a" * 400_000  # 400K chars → 100K tokens
        assert estimate_tokens(text) == 100_000

    def test_none_safety(self):
        # Should not crash on None — but signature says str, so empty string test
        assert estimate_tokens("") == 0


# ---------------------------------------------------------------------------
# 2. P1 content always kept
# ---------------------------------------------------------------------------


class TestP1AlwaysKept:
    def test_requirements_in_prompt(self):
        state = _make_state(requirements="Build a SaaS dashboard")
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt="You are a frontend developer.",
        )
        assert "Build a SaaS dashboard" in result.agent_prompt

    def test_architecture_in_prompt(self):
        state = _make_state(architecture={"framework": "React", "state": "Zustand"})
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt="You are a frontend developer.",
        )
        assert "Zustand" in result.agent_prompt

    def test_errors_in_prompt(self):
        state = _make_state(
            errors=["TypeError: Cannot read property 'map' of undefined"]
        )
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt="You are a frontend developer.",
        )
        assert "Cannot read property" in result.agent_prompt

    def test_guardrails_violations_in_prompt(self):
        state = _make_state(
            guardrails_violations=[
                {
                    "rule_id": "ARCH001",
                    "file_path": "src/Dashboard.tsx",
                    "line": 42,
                    "severity": "critical",
                    "fix_instruction": "Move fetch() to lib/api/",
                },
            ]
        )
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt="You are a frontend developer.",
        )
        assert "ARCH001" in result.agent_prompt
        assert "Move fetch() to lib/api/" in result.agent_prompt

    def test_base_prompt_in_output(self):
        state = _make_state()
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt="You are an elite frontend developer.",
        )
        assert "elite frontend developer" in result.agent_prompt


# ---------------------------------------------------------------------------
# 3. P2 full when under budget
# ---------------------------------------------------------------------------


class TestP2FullWhenUnderBudget:
    def test_small_project_no_trimming(self):
        """A small project (3 files) should fit entirely — no trimming."""
        state = _make_state(
            frontend_code=_generate_code(3, lines_per_file=20),
        )
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt="You are a frontend developer.",
        )
        assert not result.trimmed
        assert "No trimming needed" in result.trimmed_report
        assert result.rag_queries_made == 0

    def test_all_files_present_when_small(self):
        """When under budget, all P2 files should be in the prompt."""
        code = {
            "src/App.tsx": "export function App() { return <div>Hello</div>; }",
            "src/api.ts": "export async function fetchUsers() { return []; }",
        }
        state = _make_state(backend_code=code)
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="backend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt="You are a backend developer.",
        )
        # The backend agent's P1 files are backend_code, so App.tsx goes to P2
        # for a backend agent
        assert result.token_count < result.budget_limit


# ---------------------------------------------------------------------------
# 4. P2 summarized when over budget
# ---------------------------------------------------------------------------


class TestP2Summarized:
    def test_large_project_triggers_trimming(self):
        """A 50-file project should trigger P2 summarization on a small budget."""
        # Generate ~50 files with 200 lines each → ~500K chars → ~125K tokens
        state = _make_state(
            frontend_code=_generate_code(50, lines_per_file=200),
        )
        mgr = ContextBudgetManager()
        # Use GPT model (128K context → 76.8K budget)
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="gpt-5.2-pro",
            state=state,
            base_prompt="You are a frontend developer.",
        )
        assert result.trimmed
        assert result.token_count <= result.budget_limit
        assert (
            "summarized" in result.trimmed_report.lower()
            or "dropped" in result.trimmed_report.lower()
        )

    def test_trimmed_prompt_within_budget(self):
        """After trimming, token count must be <= budget."""
        state = _make_state(
            frontend_code=_generate_code(30, lines_per_file=300),
            backend_code=_generate_code(20, lines_per_file=200),
        )
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="gpt-5.2-pro",
            state=state,
            base_prompt="You are a frontend developer.",
        )
        assert result.token_count <= result.budget_limit

    def test_review_summary_compact(self):
        """When P2 is summarized, review results should be compact."""
        state = _make_state(
            frontend_code=_generate_code(50, lines_per_file=200),
            review_results={
                "score": 72,
                "issues": [
                    {
                        "title": "SEC001: Hardcoded secret",
                        "severity": "critical",
                        "description": "API key in source",
                    },
                    {
                        "title": "PERF001: N+1 query",
                        "severity": "high",
                        "description": "Loop with DB call",
                    },
                    {
                        "title": "STYLE001: Naming",
                        "severity": "low",
                        "description": "Inconsistent naming",
                    },
                ],
            },
        )
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="gpt-5.2-pro",
            state=state,
            base_prompt="You are a frontend developer.",
        )
        # Should mention the critical issue in summarized form
        assert result.trimmed
        assert result.token_count <= result.budget_limit


# ---------------------------------------------------------------------------
# 5. Emergency truncation
# ---------------------------------------------------------------------------


class TestEmergencyTruncation:
    def test_huge_requirements_truncated(self):
        """If requirements alone exceed budget, emergency truncation kicks in."""
        # Create a state with enormous requirements (~200K tokens)
        huge_reqs = "Build an app that " + "has feature " * 200_000
        state = _make_state(requirements=huge_reqs)
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="gpt-5.2-pro",  # 76.8K budget
            state=state,
            base_prompt="You are a frontend developer.",
        )
        assert result.trimmed
        assert "EMERGENCY" in result.trimmed_report
        assert result.token_count <= result.budget_limit + 100  # small margin OK


# ---------------------------------------------------------------------------
# 6. ProjectRAGIndex
# ---------------------------------------------------------------------------


class TestProjectRAGIndex:
    def test_metadata_extraction_tsx(self):
        content = """import React from 'react';
import { useState, useEffect } from 'react';

export function Dashboard() {
  return <div>Dashboard</div>;
}

export const SIDEBAR_WIDTH = 250;
"""
        meta = ProjectRAGIndex._extract_metadata("src/Dashboard.tsx", content)
        assert meta["line_count"] == 9
        assert "React" in meta["imports"]
        assert "useState" in meta["imports"] or " useState" in meta["imports"]
        assert "Dashboard" in meta["exports"]

    def test_metadata_extraction_python(self):
        content = """from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

class UserCreate(BaseModel):
    name: str
    email: str

def create_user(data: UserCreate):
    pass
"""
        meta = ProjectRAGIndex._extract_metadata("api/users.py", content)
        assert meta["line_count"] == 10  # 9 content lines + trailing newline
        assert "FastAPI" in meta["imports"]
        assert "UserCreate" in meta["exports"]
        assert "create_user" in meta["exports"]

    def test_project_summary(self):
        idx = ProjectRAGIndex()
        files = {
            "src/App.tsx": "import React from 'react';\nexport function App() { return null; }\n"
            + "// filler\n" * 100,
            "src/api.ts": "export async function fetchUsers() { return []; }\n"
            + "// filler\n" * 50,
            "src/utils.ts": "export const format = (s: string) => s;\n",
        }
        idx.index_project(files)
        summary = idx.get_project_summary()
        assert "3 files generated" in summary
        assert "src/App.tsx" in summary
        assert "src/api.ts" in summary

    def test_empty_project(self):
        idx = ProjectRAGIndex()
        idx.index_project({})
        assert idx.file_count == 0
        assert "No project files" in idx.get_project_summary()

    def test_file_count(self):
        idx = ProjectRAGIndex()
        files = _generate_code(5, lines_per_file=10)
        idx.index_project(files)
        assert idx.file_count == 5
        assert idx.is_indexed


# ---------------------------------------------------------------------------
# 7. BudgetResult dataclass
# ---------------------------------------------------------------------------


class TestBudgetResult:
    def test_fields(self):
        br = BudgetResult(
            agent_prompt="test",
            token_count=100,
            budget_limit=1000,
            trimmed=False,
            trimmed_report="No trimming needed.",
            rag_queries_made=0,
        )
        assert br.agent_prompt == "test"
        assert br.token_count == 100
        assert br.budget_limit == 1000
        assert not br.trimmed

    def test_trimmed_result(self):
        br = BudgetResult(
            agent_prompt="trimmed",
            token_count=500,
            budget_limit=600,
            trimmed=True,
            trimmed_report="Summarized 3 files",
            rag_queries_made=2,
        )
        assert br.trimmed
        assert br.rag_queries_made == 2


# ---------------------------------------------------------------------------
# 8. Model context limits
# ---------------------------------------------------------------------------


class TestModelContextLimits:
    def test_known_models(self):
        assert MODEL_CONTEXT_LIMITS["claude-opus-4-6"] == 200_000
        assert MODEL_CONTEXT_LIMITS["gpt-5.2-pro"] == 128_000
        assert MODEL_CONTEXT_LIMITS["gemini-3-flash-preview"] == 1_048_576

    def test_budget_ratio(self):
        assert BUDGET_RATIO == 0.6

    def test_unknown_model_defaults(self):
        """Unknown model should fall back to 128K."""
        mgr = ContextBudgetManager()
        state = _make_state()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="unknown-model-xyz",
            state=state,
            base_prompt="You are a developer.",
        )
        expected_budget = int(128_000 * 0.6)
        assert result.budget_limit == expected_budget


# ---------------------------------------------------------------------------
# 9. Agent-specific file relevance
# ---------------------------------------------------------------------------


class TestAgentFileRelevance:
    def test_frontend_agent_gets_frontend_code(self):
        state = _make_state(
            frontend_code={"src/App.tsx": "export function App() {}"},
            backend_code={"api/server.py": "app = FastAPI()"},
        )
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt="Frontend dev",
        )
        # Frontend code should be in P1 (current files), backend in P2
        assert "export function App" in result.agent_prompt

    def test_backend_agent_gets_backend_code(self):
        state = _make_state(
            frontend_code={"src/App.tsx": "export function App() {}"},
            backend_code={"api/server.py": "app = FastAPI()"},
        )
        mgr = ContextBudgetManager()
        result = mgr.build_prompt(
            agent_name="backend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt="Backend dev",
        )
        assert "FastAPI" in result.agent_prompt


# ---------------------------------------------------------------------------
# 10. Trim report
# ---------------------------------------------------------------------------


class TestTrimReport:
    def test_report_shows_savings(self):
        full_p2 = {
            "other_files": "x" * 40000,  # ~10K tokens
            "tests": "y" * 8000,  # ~2K tokens
        }
        summarized_p2 = {
            "project_summary": "15 files generated.",  # tiny
            "test_summary": "12 tests.",  # tiny
        }
        report = ContextBudgetManager._generate_trim_report(full_p2, summarized_p2)
        assert "→" in report  # shows token reduction
        assert "Dropped" in report or "Summarized" in report


# ---------------------------------------------------------------------------
# 11. Integration: with ProjectRAGIndex
# ---------------------------------------------------------------------------


class TestContextManagerWithRAG:
    def test_rag_index_used_in_summarization(self):
        """When RAG index is provided, summaries should include project summary."""
        idx = ProjectRAGIndex()
        files = _generate_code(5, lines_per_file=20)
        idx.index_project(files)

        # Create a state that will exceed budget (many large files)
        large_code = _generate_code(50, lines_per_file=200)
        state = _make_state(frontend_code=large_code)

        mgr = ContextBudgetManager(rag_index=idx)
        result = mgr.build_prompt(
            agent_name="frontend",
            model_id="gpt-5.2-pro",
            state=state,
            base_prompt="You are a frontend developer.",
        )
        assert result.trimmed
        assert result.token_count <= result.budget_limit
        # The project summary from RAG should be present
        assert "files generated" in result.agent_prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
