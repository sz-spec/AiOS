"""
Phase 2.0 — Pipeline Loop Stress Test (Subsystem #2)

Simulates a REAL pipeline scenario where the Reviewer Agent returns the
SAME semantic error ("ARCH001: fetch in component") for 3 consecutive
iterations.  Drives the router through all 3 escalation stages:

  Iteration 1 → reviewer embeds issues → _route_after_review detects loop
              → Stage 1: switch provider (Claude → GPT)
  Iteration 2 → reviewer embeds SAME issues → _route_after_review detects loop
              → Stage 2: thinking model (gpt-5.2-pro) + anti-patterns
  Iteration 3 → reviewer embeds SAME issues → _route_after_review detects loop
              → Stage 3: finalize (break the loop)

Verifies:
  a) Semantic similarity detected > 0.85 across iterations
  b) stuck_count increments correctly (0 → 1 → 2 → 3)
  c) Stage 1: provider switch (anthropic → openai)
  d) Stage 2: thinking model + _include_anti_patterns = True
  e) Stage 3: returns "finalize" to break the loop
  f) model_switch_history is preserved and accumulates entries
  g) previous_issues_embeddings are stored and carried across iterations
  h) Edge cases: high-severity issues, mixed severity, empty issues

Run:
    cd backend && python -m pytest tests/simulate_pipeline_loops.py -v
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Mock embedding infrastructure — deterministic, hash-based
# ---------------------------------------------------------------------------


class _MockEmbeddingProvider:
    """Hash-based embeddings: same text → identical vector → cosine_sim = 1.0."""

    def __init__(self, **kwargs):
        pass

    def embed(self, text):
        import hashlib

        h = hashlib.sha256(text.encode()).digest()
        return [(b - 128) / 128.0 for b in h]


class _MockSemanticStore:
    """Provides cosine similarity without requiring a real vector store."""

    @staticmethod
    def _cosine_similarity(_self_or_none, a, b):
        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot_product / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# The ARCH001 error that will repeat across all 3 iterations
# ---------------------------------------------------------------------------

ARCH001_ISSUE = {
    "title": "ARCH001: Business logic in UI component",
    "description": (
        "fetch() call detected inside components/ProductCard.tsx. "
        "API calls must live in lib/api/ or services/ layer. "
        "Move the fetch to a custom hook or service module."
    ),
    "severity": "critical",
    "file": "components/ProductCard.tsx",
    "line": 42,
    "auto_fixable": False,
}

# Secondary high-severity issue (also repeats)
ARCH002_ISSUE = {
    "title": "ARCH002: API call outside service layer",
    "description": (
        "axios.get() found in pages/Dashboard.tsx line 18. "
        "HTTP calls should be in lib/api/ or services/."
    ),
    "severity": "high",
    "file": "pages/Dashboard.tsx",
    "line": 18,
    "auto_fixable": False,
}


def _make_builder():
    """Create a MultiAgentBuilder with all LLM calls mocked out."""
    with patch("ai.agents.multi_agent.LLM"):
        from ai.agents.multi_agent import MultiAgentBuilder

        builder = MultiAgentBuilder.__new__(MultiAgentBuilder)
        builder.llm = MagicMock()
        builder.logger = MagicMock()
        builder.agents = {}
    return builder


def _make_review_result(issues, score=40, phase="fix", iteration=1):
    """Simulate what the reviewer agent returns."""
    return {
        "messages": [],
        "review_results": {
            "issues": issues,
            "score": score,
            "summary": f"Found {len(issues)} issues",
        },
        "current_phase": phase,
        "iteration": iteration,
    }


# ===========================================================================
# TEST CLASS 1: Full 3-Iteration Pipeline Simulation
# ===========================================================================


class TestFullPipelineLoopSimulation:
    """
    End-to-end simulation: runs review iterations with the SAME error,
    verifying that the router escalates through all 3 stages.

    The key insight: _reviewer_node stores embeddings in _latest_issues_embeddings.
    _route_after_review reads previous_issues_embeddings for comparison, THEN
    rotates _latest → previous for the next iteration.  No manual swapping needed.
    """

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_full_3_stage_escalation(self, mock_logger):
        """
        Simulate 4 consecutive iterations where the Reviewer returns
        the same ARCH001 error.  Verify the full escalation:
          Iter 1 → normal retry (no previous to compare)
          Iter 2 → Stage 1 (provider switch)
          Iter 3 → Stage 2 (thinking model)
          Iter 4 → Stage 3 (finalize / break)
        """
        builder = _make_builder()

        # --- Initial state (mirrors build() / build_stream() initial_state) ---
        state = {
            "requirements": "Build an e-commerce product page",
            "architecture": {"style": "react-next"},
            "frontend_code": {
                "components/ProductCard.tsx": "export default function ProductCard() { fetch('/api') }"
            },
            "backend_code": {},
            "review_results": None,
            "iteration": 0,
            "stuck_count": 0,
            "previous_issues_embeddings": None,
            "previous_issues_text": None,
            "model_switch_history": None,
            "_override_model": None,
            "_clear_failed_context": None,
            "_include_anti_patterns": None,
            "_latest_issues_embeddings": None,
            "_latest_issues_text": None,
        }

        mock_reviewer = MagicMock()
        builder.agents["reviewer"] = mock_reviewer

        # ================================================================
        # ITERATION 1: First review — no previous embeddings to compare
        # ================================================================
        mock_reviewer.invoke.return_value = _make_review_result(
            [ARCH001_ISSUE, ARCH002_ISSUE],
            score=35,
            iteration=1,
        )
        reviewer_result = builder._reviewer_node(state)
        state.update(reviewer_result)
        state["iteration"] = 1

        # Verify: embeddings stored in _latest (NOT in previous yet)
        assert state["_latest_issues_embeddings"] is not None
        assert len(state["_latest_issues_embeddings"]) == 2  # critical + high
        assert state["previous_issues_embeddings"] is None  # Not rotated yet

        # Route: previous_issues_embeddings is None → not stuck → normal retry
        route_1 = builder._route_after_review(state)
        assert (
            route_1 == "frontend"
        ), f"Expected 'frontend' (normal retry), got '{route_1}'"
        assert state["stuck_count"] == 0  # No loop detected yet

        # After routing, rotation happened: _latest → previous
        assert state["previous_issues_embeddings"] is not None
        assert len(state["previous_issues_embeddings"]) == 2

        # ================================================================
        # ITERATION 2: Same error → Stage 1 (provider switch)
        # ================================================================
        mock_reviewer.invoke.return_value = _make_review_result(
            [ARCH001_ISSUE, ARCH002_ISSUE],
            score=35,
            iteration=2,
        )
        reviewer_result_2 = builder._reviewer_node(state)
        state.update(reviewer_result_2)
        state["iteration"] = 2

        # _latest has new embeddings; previous still has iter 1's (from rotation)
        route_2 = builder._route_after_review(state)

        # --- VERIFY Stage 1 ---
        assert (
            route_2 == "frontend"
        ), f"Expected 'frontend' (Stage 1 retry), got '{route_2}'"
        assert (
            state["stuck_count"] == 1
        ), f"Expected stuck_count=1, got {state['stuck_count']}"
        assert (
            state["_override_model"] is not None
        ), "Stage 1 should set _override_model"
        assert (
            state["_clear_failed_context"] is True
        ), "Stage 1 should clear failed context"
        assert state["model_switch_history"] is not None
        assert len(state["model_switch_history"]) == 1
        assert state["model_switch_history"][0]["stage"] == 1

        # ================================================================
        # ITERATION 3: Same error → Stage 2 (thinking model)
        # ================================================================
        mock_reviewer.invoke.return_value = _make_review_result(
            [ARCH001_ISSUE, ARCH002_ISSUE],
            score=35,
            iteration=3,
        )
        reviewer_result_3 = builder._reviewer_node(state)
        state.update(reviewer_result_3)
        state["iteration"] = 3

        route_3 = builder._route_after_review(state)

        # --- VERIFY Stage 2 ---
        assert (
            route_3 == "frontend"
        ), f"Expected 'frontend' (Stage 2 retry), got '{route_3}'"
        assert (
            state["stuck_count"] == 2
        ), f"Expected stuck_count=2, got {state['stuck_count']}"
        assert state["_override_model"] == "gpt-5.2-pro"  # thinking model
        assert (
            state["_include_anti_patterns"] is True
        ), "Stage 2 should include anti-patterns"

        # ================================================================
        # ITERATION 4: Same error → Stage 3 (finalize / break loop)
        # ================================================================
        mock_reviewer.invoke.return_value = _make_review_result(
            [ARCH001_ISSUE, ARCH002_ISSUE],
            score=35,
            iteration=4,
        )
        reviewer_result_4 = builder._reviewer_node(state)
        state.update(reviewer_result_4)
        state["iteration"] = 4

        route_4 = builder._route_after_review(state)

        # --- VERIFY Stage 3 ---
        assert (
            route_4 == "finalize"
        ), f"Expected 'finalize' (Stage 3 break), got '{route_4}'"
        assert (
            state["stuck_count"] == 3
        ), f"Expected stuck_count=3, got {state['stuck_count']}"

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_model_switch_history_preserved_across_all_stages(self, mock_logger):
        """
        Verify model_switch_history accumulates correctly across the
        full 3-stage simulation.
        """
        builder = _make_builder()

        state = {
            "requirements": "Build a dashboard",
            "architecture": {},
            "frontend_code": {},
            "backend_code": {},
            "review_results": None,
            "iteration": 0,
            "stuck_count": 0,
            "previous_issues_embeddings": None,
            "previous_issues_text": None,
            "model_switch_history": None,
            "_override_model": None,
            "_clear_failed_context": None,
            "_include_anti_patterns": None,
            "_latest_issues_embeddings": None,
            "_latest_issues_text": None,
        }

        mock_reviewer = MagicMock()
        builder.agents["reviewer"] = mock_reviewer

        # Run 4 iterations (same issue each time)
        routes = []
        for i in range(4):
            mock_reviewer.invoke.return_value = _make_review_result(
                [ARCH001_ISSUE],
                score=30,
                iteration=i + 1,
            )
            result = builder._reviewer_node(state)
            state.update(result)
            state["iteration"] = i + 1
            routes.append(builder._route_after_review(state))

        # Iter 1: normal retry, Iter 2: Stage 1, Iter 3: Stage 2, Iter 4: Stage 3
        assert routes == ["frontend", "frontend", "frontend", "finalize"]
        assert state["stuck_count"] == 3

        # model_switch_history should have 1 entry (Stage 1 adds a model switch)
        # Stage 2 doesn't add to model_switch_history (it's handled separately)
        # Stage 3 doesn't add to model_switch_history
        history = state.get("model_switch_history") or []
        assert len(history) == 1  # Only Stage 1 adds an entry
        assert history[0]["stage"] == 1
        assert "from" in history[0]
        assert "to" in history[0]
        assert "reason" in history[0]


# ===========================================================================
# TEST CLASS 2: Semantic Similarity Detection
# ===========================================================================


class TestSemanticSimilarityDetection:
    """Verify that identical issues produce similarity > 0.85."""

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_identical_issues_have_high_similarity(self, mock_logger):
        """Same error text embedded twice → cosine_sim = 1.0 (> 0.85)."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()

        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"
        prev_emb = [embedder.embed(issue_text)]

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "previous_issues_embeddings": prev_emb,
        }

        is_stuck, max_sim = builder._detect_semantic_loop(state)
        assert max_sim > 0.85, f"Expected similarity > 0.85, got {max_sim}"
        assert is_stuck is True, "Identical issues should be detected as stuck"

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_identical_issues_similarity_is_1_0(self, mock_logger):
        """Exact same text → cosine similarity must be exactly 1.0."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()

        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"
        prev_emb = [embedder.embed(issue_text)]

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "previous_issues_embeddings": prev_emb,
        }

        _, max_sim = builder._detect_semantic_loop(state)
        assert abs(max_sim - 1.0) < 1e-6, f"Expected ~1.0, got {max_sim}"

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_completely_different_issues_low_similarity(self, mock_logger):
        """Unrelated errors → similarity well below 0.85."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()

        prev_emb = [
            embedder.embed("SQL injection vulnerability in login form at line 55")
        ]

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "previous_issues_embeddings": prev_emb,
        }

        is_stuck, max_sim = builder._detect_semantic_loop(state)
        assert max_sim < 0.85, f"Expected similarity < 0.85, got {max_sim}"
        assert is_stuck is False

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_majority_match_triggers_stuck(self, mock_logger):
        """2 out of 3 issues match (66% > 50%) → stuck."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()

        # Previous: 2 issues
        prev_emb = [
            embedder.embed(f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"),
            embedder.embed(f"{ARCH002_ISSUE['title']}: {ARCH002_ISSUE['description']}"),
        ]

        # Current: same 2 issues + 1 new one (2/3 match = 66% > 50%)
        new_issue = {
            "title": "SEC001: Hardcoded API key",
            "description": "Found hardcoded API key in config.ts",
            "severity": "critical",
        }

        state = {
            "review_results": {"issues": [ARCH001_ISSUE, ARCH002_ISSUE, new_issue]},
            "previous_issues_embeddings": prev_emb,
        }

        is_stuck, _ = builder._detect_semantic_loop(state)
        assert is_stuck is True, "2/3 issues matching (66%) should trigger stuck"

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_minority_match_not_stuck(self, mock_logger):
        """1 out of 3 issues match (33% < 50%) → not stuck."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()

        prev_emb = [
            embedder.embed(f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"),
        ]

        new_issue_1 = {
            "title": "SEC001: Hardcoded API key",
            "description": "Found hardcoded API key in config.ts",
            "severity": "critical",
        }
        new_issue_2 = {
            "title": "PERF003: N+1 query detected",
            "description": "N+1 query pattern in UserList component",
            "severity": "high",
        }

        state = {
            "review_results": {"issues": [ARCH001_ISSUE, new_issue_1, new_issue_2]},
            "previous_issues_embeddings": prev_emb,
        }

        is_stuck, _ = builder._detect_semantic_loop(state)
        assert is_stuck is False, "1/3 issues matching (33%) should NOT trigger stuck"


# ===========================================================================
# TEST CLASS 3: stuck_count Progression
# ===========================================================================


class TestStuckCountProgression:
    """Verify stuck_count increments exactly once per escalation."""

    def _route_with_stuck(self, builder, state, stuck_count_before):
        """Helper: set stuck_count and route, return new stuck_count."""
        state["stuck_count"] = stuck_count_before
        builder._route_after_review(state)
        return state["stuck_count"]

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_stuck_count_0_to_1(self, mock_logger):
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 5,
            "stuck_count": 0,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": None,
            "_override_model": None,
        }
        builder._route_after_review(state)
        assert state["stuck_count"] == 1

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_stuck_count_1_to_2(self, mock_logger):
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 6,
            "stuck_count": 1,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": [{"stage": 1}],
            "_override_model": "gpt-5.2-pro",
        }
        builder._route_after_review(state)
        assert state["stuck_count"] == 2

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_stuck_count_2_to_3(self, mock_logger):
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 7,
            "stuck_count": 2,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": [{"stage": 1}, {"stage": 2}],
            "_override_model": "gpt-5.2-pro",
        }
        result = builder._route_after_review(state)
        assert state["stuck_count"] == 3
        assert result == "finalize"

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_stuck_count_never_decreases(self, mock_logger):
        """Once stuck, count only goes up, never resets."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 20,
            "stuck_count": 10,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": [],
            "_override_model": None,
        }
        builder._route_after_review(state)
        assert state["stuck_count"] == 11  # 10 + 1, never reset


# ===========================================================================
# TEST CLASS 4: Stage 1 — Provider Switch
# ===========================================================================


class TestStage1ProviderSwitch:
    """Verify Stage 1 switches to a different AI provider."""

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_claude_switches_to_gpt(self, mock_logger):
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 5,
            "stuck_count": 0,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": None,
            "_override_model": None,  # default = claude-sonnet-4-6
        }
        route = builder._route_after_review(state)

        assert route == "frontend"
        assert state["_override_model"] == "gpt-5.2-pro"
        assert state["_clear_failed_context"] is True

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_gpt_switches_to_claude(self, mock_logger):
        """If already on GPT, Stage 1 should switch to Claude."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 5,
            "stuck_count": 0,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": None,
            "_override_model": "gpt-5.2-pro",  # Already on GPT
        }
        builder._route_after_review(state)

        assert state["_override_model"] == "claude-sonnet-4-6"  # Switched back


# ===========================================================================
# TEST CLASS 5: Stage 2 — Thinking Model
# ===========================================================================


class TestStage2ThinkingModel:
    """Verify Stage 2 activates the thinking model with anti-patterns."""

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_thinking_model_activated(self, mock_logger):
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 6,
            "stuck_count": 1,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": [
                {"stage": 1, "from": "claude-sonnet-4-6", "to": "gpt-5.2-pro"}
            ],
            "_override_model": "gpt-5.2-pro",
        }
        route = builder._route_after_review(state)

        assert route == "frontend"
        assert state["_override_model"] == "gpt-5.2-pro"
        assert state["_include_anti_patterns"] is True
        assert state["stuck_count"] == 2

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_anti_patterns_flag_set(self, mock_logger):
        """Stage 2 must set _include_anti_patterns to True."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 6,
            "stuck_count": 1,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": [{"stage": 1}],
            "_override_model": "gpt-5.2-pro",
        }
        builder._route_after_review(state)
        assert state.get("_include_anti_patterns") is True


# ===========================================================================
# TEST CLASS 6: Stage 3 — Finalize (Loop Break)
# ===========================================================================


class TestStage3Finalize:
    """Verify Stage 3 returns 'finalize' and doesn't loop forever."""

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_stage_3_returns_finalize(self, mock_logger):
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 7,
            "stuck_count": 2,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": [{"stage": 1}, {"stage": 2}],
            "_override_model": "gpt-5.2-pro",
        }
        result = builder._route_after_review(state)
        assert result == "finalize"

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_stage_3_at_stuck_count_100(self, mock_logger):
        """Even at absurd stuck_count, must return finalize."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 200,
            "stuck_count": 99,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": [],
            "_override_model": None,
        }
        result = builder._route_after_review(state)
        assert result == "finalize"
        assert state["stuck_count"] == 100

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_no_infinite_loop_possible(self, mock_logger):
        """Run 10 consecutive router calls — must terminate via finalize."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 0,
            "stuck_count": 0,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": None,
            "_override_model": None,
        }

        finalized = False
        for i in range(10):
            state["iteration"] = i + 3  # Start past normal retry limit
            result = builder._route_after_review(state)
            if result == "finalize":
                finalized = True
                break

        assert finalized, "Router must eventually return 'finalize' — no infinite loop"
        assert state["stuck_count"] <= 3, "Should finalize by stuck_count 3"


# ===========================================================================
# TEST CLASS 7: model_switch_history Preservation
# ===========================================================================


class TestModelSwitchHistoryPreservation:
    """Verify model_switch_history accumulates across all stages."""

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_history_accumulates_across_stages(self, mock_logger):
        """Run through Stage 1 and verify history has 1 entry."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 5,
            "stuck_count": 0,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": None,
            "_override_model": None,
        }

        # Stage 1
        builder._route_after_review(state)
        assert len(state["model_switch_history"]) == 1
        assert state["model_switch_history"][0]["stage"] == 1
        assert "from" in state["model_switch_history"][0]
        assert "to" in state["model_switch_history"][0]
        assert "reason" in state["model_switch_history"][0]
        assert "similarity" in state["model_switch_history"][0]["reason"]

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_history_not_mutated_on_stage_3(self, mock_logger):
        """Stage 3 (finalize) should NOT add to model_switch_history."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        existing_history = [
            {
                "stage": 1,
                "from": "claude-sonnet-4-6",
                "to": "gpt-5.2-pro",
                "reason": "loop",
            },
            {
                "stage": 2,
                "from": "gpt-5.2-pro",
                "to": "gpt-5.2-pro",
                "reason": "thinking",
            },
        ]

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 7,
            "stuck_count": 2,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": deepcopy(existing_history),
            "_override_model": "gpt-5.2-pro",
        }

        builder._route_after_review(state)
        # Stage 3 just finalizes, no new model switch entry
        assert len(state["model_switch_history"]) == 2  # unchanged
        assert state["model_switch_history"] == existing_history

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_history_from_field_reflects_current_model(self, mock_logger):
        """Stage 1 'from' should match the current model override."""
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()
        issue_text = f"{ARCH001_ISSUE['title']}: {ARCH001_ISSUE['description']}"

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 5,
            "stuck_count": 0,
            "previous_issues_embeddings": [embedder.embed(issue_text)],
            "previous_issues_text": [issue_text],
            "model_switch_history": None,
            "_override_model": "gemini-3-flash-preview",  # Non-default model
        }

        builder._route_after_review(state)
        entry = state["model_switch_history"][0]
        assert entry["from"] == "gemini-3-flash-preview"
        assert entry["to"] == "claude-sonnet-4-6"  # Gemini → Claude


# ===========================================================================
# TEST CLASS 8: Reviewer Embedding Storage
# ===========================================================================


class TestReviewerEmbeddingStorage:
    """Verify _reviewer_node correctly embeds and stores issues."""

    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    def test_only_critical_and_high_embedded(self):
        """Low/medium severity issues should NOT be embedded."""
        builder = _make_builder()
        mock_reviewer = MagicMock()
        mock_reviewer.invoke.return_value = _make_review_result(
            [
                {"title": "Critical", "description": "crash", "severity": "critical"},
                {"title": "High", "description": "memory leak", "severity": "high"},
                {"title": "Medium", "description": "style issue", "severity": "medium"},
                {"title": "Low", "description": "naming", "severity": "low"},
            ]
        )
        builder.agents["reviewer"] = mock_reviewer

        result = builder._reviewer_node({})
        assert len(result["_latest_issues_embeddings"]) == 2
        assert len(result["_latest_issues_text"]) == 2
        assert "Critical" in result["_latest_issues_text"][0]
        assert "High" in result["_latest_issues_text"][1]

    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    def test_no_issues_produces_empty_embeddings(self):
        """If review has no issues, embeddings should be empty."""
        builder = _make_builder()
        mock_reviewer = MagicMock()
        mock_reviewer.invoke.return_value = _make_review_result([])
        builder.agents["reviewer"] = mock_reviewer

        result = builder._reviewer_node({})
        assert result["_latest_issues_embeddings"] == []
        assert result["_latest_issues_text"] == []

    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    def test_embedding_dimensions_consistent(self):
        """All embeddings should have the same dimensionality."""
        builder = _make_builder()
        mock_reviewer = MagicMock()
        mock_reviewer.invoke.return_value = _make_review_result(
            [
                ARCH001_ISSUE,
                ARCH002_ISSUE,
            ]
        )
        builder.agents["reviewer"] = mock_reviewer

        result = builder._reviewer_node({})
        embs = result["_latest_issues_embeddings"]
        assert len(embs) == 2
        assert len(embs[0]) == len(embs[1])  # Same dimension (SHA-256 = 32)
        assert len(embs[0]) == 32


# ===========================================================================
# TEST CLASS 9: Edge Cases
# ===========================================================================


class TestEdgeCases:
    """Edge cases that could break the escalation logic."""

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_empty_previous_embeddings_list(self, mock_logger):
        """Empty list (not None) for previous embeddings → not stuck."""
        builder = _make_builder()
        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "previous_issues_embeddings": [],  # Empty list, not None
        }
        is_stuck, sim = builder._detect_semantic_loop(state)
        assert is_stuck is False

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_only_low_severity_no_escalation(self, mock_logger):
        """Only low/medium issues → no loop detection triggered."""
        builder = _make_builder()
        state = {
            "review_results": {
                "issues": [
                    {"title": "Style", "description": "indent", "severity": "low"},
                    {
                        "title": "Naming",
                        "description": "camelCase",
                        "severity": "medium",
                    },
                ]
            },
            "previous_issues_embeddings": [[0.1] * 32],
        }
        is_stuck, sim = builder._detect_semantic_loop(state)
        assert is_stuck is False
        assert sim == 0.0

    @patch("ai.agents.multi_agent.lg_logger")
    def test_no_review_results_at_all(self, mock_logger):
        """None review_results → not stuck."""
        builder = _make_builder()
        state = {
            "review_results": None,
            "previous_issues_embeddings": None,
        }
        is_stuck, sim = builder._detect_semantic_loop(state)
        assert is_stuck is False
        assert sim == 0.0

    def test_route_no_critical_with_high_issues(self):
        """High but no critical issues → finalize (only critical triggers retry)."""
        builder = _make_builder()
        state = {
            "review_results": {
                "issues": [
                    {"severity": "high", "title": "memory leak"},
                    {"severity": "high", "title": "performance"},
                ]
            },
            "iteration": 0,
            "stuck_count": 0,
            "previous_issues_embeddings": None,
            "previous_issues_text": None,
            "model_switch_history": None,
            "_override_model": None,
        }
        result = builder._route_after_review(state)
        assert result == "finalize"

    @patch("memory.SemanticStore", _MockSemanticStore)
    @patch("memory.EmbeddingProvider", _MockEmbeddingProvider)
    @patch("ai.agents.multi_agent.lg_logger")
    def test_iteration_limit_triggers_escalation_even_without_loop(self, mock_logger):
        """
        iteration >= 3 with critical issues and NOT stuck →
        should still escalate (hits the stuck path because is_stuck=False
        but iteration >= 3, so the normal retry condition fails).
        """
        builder = _make_builder()
        embedder = _MockEmbeddingProvider()

        # Previous embeddings are DIFFERENT from current → not semantically stuck
        prev_emb = [embedder.embed("Completely unrelated previous issue about CSS")]

        state = {
            "review_results": {"issues": [ARCH001_ISSUE]},
            "iteration": 5,  # > 3 → past normal retry limit
            "stuck_count": 0,
            "previous_issues_embeddings": prev_emb,
            "previous_issues_text": ["Completely unrelated previous issue about CSS"],
            "model_switch_history": None,
            "_override_model": None,
        }
        route = builder._route_after_review(state)

        # Not stuck semantically, but iteration >= 3 fails the
        # `not is_stuck and iteration < 3` check → falls through to escalation
        assert route == "frontend"  # Stage 1 (stuck_count 0 → 1)
        assert state["stuck_count"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
