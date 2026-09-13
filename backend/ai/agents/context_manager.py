"""
Context Budget Manager — Phase 2.0 Subsystem #1

Manages token budget for each agent call in the LangGraph pipeline.
Called BEFORE every LLM invocation to decide what to keep, summarize, or drop.

Priority tiers:
  P1 (ALWAYS KEEP):  requirements, architecture, errors, current file, guardrails violations
  P2 (SUMMARIZE):    other files → RAG summary, test results → compact, review → condensed
  P3 (DROP):         old messages, stale decision log, snapshot metadata

Integration points:
  - VectorStore / DocumentLoader / EmbeddingProvider from ai.rag
  - ProjectState TypedDict from ai.agents.multi_agent
  - Called by every pipeline node before LLM invocation

See: docs/TECHNICAL_IMPLEMENTATION_SPEC.md Section 2.4
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """
    Estimate token count using the chars/4 heuristic.

    Accuracy: ~90% vs tiktoken for English text.
    Advantage: zero dependencies, instant, works for all models.
    """
    if not text:
        return 0
    return len(text) // 4


# ---------------------------------------------------------------------------
# Model context limits
# ---------------------------------------------------------------------------

MODEL_CONTEXT_LIMITS: Dict[str, int] = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "gpt-5.2-pro": 128_000,
    "gpt-5.3-codex": 128_000,
    "gemini-3-flash-preview": 1_048_576,
    "gemini-3.1-pro": 1_048_576,
}

# Use 60% of context window for input, reserve 40% for output
BUDGET_RATIO = 0.6


# ---------------------------------------------------------------------------
# BudgetResult
# ---------------------------------------------------------------------------


@dataclass
class BudgetResult:
    """Result of context budget trimming."""

    agent_prompt: str  # The final prompt to send to the LLM
    token_count: int  # Estimated tokens in the prompt
    budget_limit: int  # Max tokens allowed
    trimmed: bool  # Whether any trimming occurred
    trimmed_report: str  # Human-readable report of what was trimmed
    rag_queries_made: int  # How many RAG retrievals were done


# ---------------------------------------------------------------------------
# ProjectRAGIndex — per-build FAISS index of all project files
# ---------------------------------------------------------------------------


class ProjectRAGIndex:
    """
    Per-build FAISS index of all generated project files.

    Created once after Aggregator merges code.
    Queried by ContextBudgetManager when P2 files are summarized.

    NOT persistent — lives only for the duration of one build.
    """

    def __init__(self) -> None:
        self._store = None  # VectorStore instance (or None)
        self._file_metadata: Dict[str, Dict[str, Any]] = {}
        self._indexed = False

    # -- public API --

    def index_project(self, files: Dict[str, str]) -> None:
        """
        Index all project files into a FAISS vector store.

        Chunking strategy:
          - Chunk size: 2000 chars (~500 tokens)
          - Overlap: 400 chars (~100 tokens)
          - Metadata per chunk: {file_path, chunk_index, imports, exports, line_count}
        """
        if not files:
            return

        try:
            from ai.rag import VectorStore, DocumentLoader, EmbeddingProvider
        except ImportError:
            logger.warning(
                "[RAGIndex] ai.rag not available — falling back to metadata-only mode"
            )
            for file_path, content in files.items():
                self._file_metadata[file_path] = self._extract_metadata(
                    file_path, content
                )
            self._indexed = True
            return

        loader = DocumentLoader(chunk_size=2000, chunk_overlap=400)
        documents = []

        for file_path, content in files.items():
            self._file_metadata[file_path] = self._extract_metadata(file_path, content)

            chunks = loader.load_texts([content])
            for i, chunk in enumerate(chunks):
                chunk.metadata = {
                    "file_path": file_path,
                    "chunk_index": i,
                    **self._file_metadata[file_path],
                }
                documents.append(chunk)

        if documents:
            try:
                embedder = EmbeddingProvider(provider="auto")
                self._store = VectorStore(backend="faiss", embeddings=embedder)
                self._store.add_documents(documents)
                logger.info(
                    "[RAGIndex] Indexed %d chunks from %d files",
                    len(documents),
                    len(files),
                )
            except Exception as e:
                logger.warning(
                    "[RAGIndex] FAISS indexing failed: %s — metadata-only mode", e
                )
                self._store = None

        self._indexed = True

    def query(self, question: str, k: int = 5) -> List[Dict[str, Any]]:
        """
        Retrieve relevant code chunks for a question.

        Returns list of {file_path, content, metadata}.
        """
        if not self._store:
            return []

        try:
            results = self._store.similarity_search(question, k=k)
            return [
                {
                    "file_path": doc.metadata.get("file_path", "unknown"),
                    "content": doc.page_content,
                    "metadata": doc.metadata,
                }
                for doc in results
            ]
        except Exception as e:
            logger.warning("[RAGIndex] Query failed: %s", e)
            return []

    def get_project_summary(self) -> str:
        """
        Generate a compact summary of the entire project for P2 context.

        Returns a string like:
            "15 files generated. Key files:
             - src/App.tsx (142 lines, imports: React, Router)
             - src/lib/api/users.ts (45 lines, exports: getUsers, createUser)"
        """
        if not self._file_metadata:
            return "No project files indexed."

        lines = [f"{len(self._file_metadata)} files generated. Key files:"]

        sorted_files = sorted(
            self._file_metadata.items(),
            key=lambda x: x[1].get("line_count", 0),
            reverse=True,
        )[:10]

        for path, meta in sorted_files:
            imports_str = ", ".join(meta.get("imports", [])[:5])
            exports_str = ", ".join(meta.get("exports", [])[:5])
            lc = meta.get("line_count", 0)
            parts = [f"- {path} ({lc} lines"]
            if imports_str:
                parts.append(f", imports: {imports_str}")
            if exports_str:
                parts.append(f", exports: {exports_str}")
            parts.append(")")
            lines.append("".join(parts))

        return "\n".join(lines)

    @property
    def file_count(self) -> int:
        return len(self._file_metadata)

    @property
    def is_indexed(self) -> bool:
        return self._indexed

    # -- private helpers --

    @staticmethod
    def _extract_metadata(file_path: str, content: str) -> Dict[str, Any]:
        """Extract imports, exports, line count from a source file."""
        metadata: Dict[str, Any] = {
            "line_count": content.count("\n") + 1,
            "imports": [],
            "exports": [],
        }

        # TypeScript / JavaScript
        if file_path.endswith((".ts", ".tsx", ".js", ".jsx")):
            for m in re.finditer(r"import\s+(?:\{([^}]+)\}|(\w+))\s+from", content):
                names = m.group(1) or m.group(2)
                metadata["imports"].extend(n.strip() for n in names.split(","))
            for m in re.finditer(
                r"export\s+(?:default\s+)?(?:function|const|class|interface|type)\s+(\w+)",
                content,
            ):
                metadata["exports"].append(m.group(1))

        # Python
        elif file_path.endswith(".py"):
            for m in re.finditer(r"from\s+\S+\s+import\s+(.+)", content):
                metadata["imports"].extend(n.strip() for n in m.group(1).split(","))
            for m in re.finditer(r"^(?:def|class)\s+(\w+)", content, re.MULTILINE):
                metadata["exports"].append(m.group(1))

        return metadata


# ---------------------------------------------------------------------------
# ContextBudgetManager
# ---------------------------------------------------------------------------


class ContextBudgetManager:
    """
    Manages token budget for each agent call in the LangGraph pipeline.

    Called BEFORE every LLM invocation. Decides what to keep, summarize, or drop.

    Usage in pipeline nodes::

        budget_mgr = ContextBudgetManager(rag_index=self._project_rag_index)
        result = budget_mgr.build_prompt(
            agent_name="frontend",
            model_id="claude-sonnet-4-6",
            state=state,
            base_prompt=prompt,
        )
        # result.agent_prompt is the trimmed prompt, ready to send to LLM
    """

    def __init__(self, rag_index: Optional[ProjectRAGIndex] = None) -> None:
        self._rag_index = rag_index
        self._rag_queries = 0

    def build_prompt(
        self,
        agent_name: str,
        model_id: str,
        state: Dict[str, Any],
        base_prompt: str,
    ) -> BudgetResult:
        """
        Build a token-budget-aware prompt for the given agent.

        Steps:
            1. Calculate budget from model context limit
            2. Estimate tokens for P1 content (always kept)
            3. If P1 fits in budget: add P2 content (full or summarized)
            4. If still over: drop P3 content
            5. If still over: progressively trim P2 summaries
        """
        self._rag_queries = 0

        context_limit = MODEL_CONTEXT_LIMITS.get(model_id, 128_000)
        budget = int(context_limit * BUDGET_RATIO)

        # === P1: Always keep ===
        p1_sections, p1_file_paths = self._build_p1(agent_name, state, base_prompt)
        p1_tokens = sum(estimate_tokens(s) for s in p1_sections.values())

        if p1_tokens > budget:
            return self._emergency_truncate(p1_sections, budget)

        remaining = budget - p1_tokens

        # === P2: Summarize if needed ===
        p2_sections = self._build_p2(agent_name, state, p1_file_paths)
        p2_tokens = sum(estimate_tokens(s) for s in p2_sections.values())

        if p2_tokens <= remaining:
            # Everything fits — no trimming needed
            prompt = self._assemble_prompt(p1_sections, p2_sections)
            total = p1_tokens + p2_tokens
            return BudgetResult(
                agent_prompt=prompt,
                token_count=total,
                budget_limit=budget,
                trimmed=False,
                trimmed_report="No trimming needed.",
                rag_queries_made=0,
            )

        # P2 doesn't fit — summarize
        p2_summaries = self._summarize_p2(agent_name, state, remaining)
        p2_summary_tokens = sum(estimate_tokens(s) for s in p2_summaries.values())

        prompt = self._assemble_prompt(p1_sections, p2_summaries)
        total = p1_tokens + p2_summary_tokens
        return BudgetResult(
            agent_prompt=prompt,
            token_count=total,
            budget_limit=budget,
            trimmed=True,
            trimmed_report=self._generate_trim_report(p2_sections, p2_summaries),
            rag_queries_made=self._rag_queries,
        )

    # -- P1: always keep --

    def _build_p1(
        self, agent_name: str, state: Dict[str, Any], base_prompt: str
    ) -> Tuple[Dict[str, str], set]:
        """
        P1 content — ALWAYS kept in context, never trimmed.

        Returns:
            (sections dict, set of file paths included in P1)
        """
        sections: Dict[str, str] = {}
        p1_file_paths: set = set()

        # Base prompt (agent's role + instructions)
        sections["base_prompt"] = base_prompt

        # Requirements
        reqs = state.get("requirements", "")
        if reqs:
            sections["requirements"] = f"## Requirements\n{reqs}"

        # Architecture spec
        arch = state.get("architecture")
        if arch:
            if isinstance(arch, dict):
                sections["architecture"] = (
                    f"## Architecture\n{json.dumps(arch, indent=2)}"
                )
            else:
                sections["architecture"] = f"## Architecture\n{arch}"

        # Errors from current iteration
        errors = state.get("errors") or []
        if errors:
            # Keep last 10 errors max
            trimmed = errors[-10:]
            sections["errors"] = "## Current Errors\n" + "\n".join(
                f"- {e}" for e in trimmed
            )

        # Guardrails violations (if routing back for fix)
        violations = state.get("guardrails_violations") or []
        critical_violations = [v for v in violations if v.get("severity") == "critical"]
        if critical_violations:
            sections["guardrails"] = "## Architectural Violations to Fix\n" + "\n".join(
                f"- [{v.get('rule_id', '?')}] {v.get('file_path', '?')}:{v.get('line', '?')} — {v.get('fix_instruction', '')}"
                for v in critical_violations
            )

        # Current files being worked on (max 3 at full fidelity)
        current_files = self._get_relevant_files(agent_name, state)
        for path, content in list(current_files.items())[:3]:
            sections[f"file:{path}"] = f"## Current File: {path}\n```\n{content}\n```"
            p1_file_paths.add(path)

        return sections, p1_file_paths

    # -- P2: full or summarized --

    def _build_p2(
        self, agent_name: str, state: Dict[str, Any], p1_file_paths: set = None
    ) -> Dict[str, str]:
        """P2 content — included full if budget allows, summarized if not."""
        sections: Dict[str, str] = {}
        excluded = p1_file_paths or set()

        # All other generated files (not already in P1)
        all_code: Dict[str, str] = {}
        all_code.update(state.get("frontend_code") or {})
        all_code.update(state.get("backend_code") or {})
        other_files = {k: v for k, v in all_code.items() if k not in excluded}

        if other_files:
            sections["other_files"] = "\n\n".join(
                f"--- {path} ---\n{content}" for path, content in other_files.items()
            )

        # Test results
        tests = state.get("tests")
        if tests and isinstance(tests, dict):
            sections["tests"] = "\n\n".join(
                f"--- {path} ---\n{content}" for path, content in tests.items()
            )
        elif tests and isinstance(tests, str):
            sections["tests"] = tests

        # Review results
        review = state.get("review_results")
        if review:
            if isinstance(review, dict):
                sections["review"] = json.dumps(review, indent=2)
            else:
                sections["review"] = str(review)

        return sections

    def _summarize_p2(
        self, agent_name: str, state: Dict[str, Any], token_budget: int
    ) -> Dict[str, str]:
        """
        Generate compact summaries of P2 content using the ProjectRAGIndex.

        Instead of including 50 full files, include a project summary
        plus RAG-retrieved relevant chunks.
        """
        summaries: Dict[str, str] = {}

        if self._rag_index and self._rag_index.is_indexed:
            summaries["project_summary"] = (
                "## Project Files (summarized — full files available via RAG)\n"
                + self._rag_index.get_project_summary()
            )

            # RAG: retrieve files most relevant to the current agent's task
            agent_query = self._get_agent_rag_query(agent_name, state)
            if agent_query:
                relevant = self._rag_index.query(agent_query, k=5)
                self._rag_queries += 1
                if relevant:
                    summaries["rag_context"] = (
                        "## Most Relevant Code (retrieved via RAG)\n"
                        + "\n\n".join(
                            f"--- {r['file_path']} ---\n{r['content']}"
                            for r in relevant
                        )
                    )
        else:
            # No RAG index — use basic file-list summary
            all_code: Dict[str, str] = {}
            all_code.update(state.get("frontend_code") or {})
            all_code.update(state.get("backend_code") or {})
            file_list = "\n".join(
                f"- {path} ({len(content)} chars)" for path, content in all_code.items()
            )
            summaries["project_summary"] = f"## Project Files (summarized)\n{file_list}"

        # Test result summary
        tests = state.get("tests")
        if tests:
            count = len(tests) if isinstance(tests, dict) else 1
            summaries["test_summary"] = (
                f"## Test Results (summarized)\n{count} test file(s) generated."
            )

        # Review result summary (compact)
        review = state.get("review_results")
        if review and isinstance(review, dict):
            issues = review.get("issues", [])
            by_severity: Dict[str, int] = {}
            for i in issues:
                sev = i.get("severity", "unknown")
                by_severity[sev] = by_severity.get(sev, 0) + 1

            critical_list = "\n".join(
                f"- {i.get('title', '?')}: {str(i.get('description', ''))[:100]}"
                for i in issues
                if i.get("severity") == "critical"
            )
            summaries["review_summary"] = (
                "## Review Results (summarized)\n"
                f"Score: {review.get('score', 'N/A')}/100\n"
                f"Issues: {', '.join(f'{count} {sev}' for sev, count in by_severity.items())}\n"
                f"Critical issues:\n{critical_list}"
            )

        return summaries

    # -- helpers --

    def _get_agent_rag_query(self, agent_name: str, state: Dict[str, Any]) -> str:
        """Build a RAG query relevant to what the current agent is working on."""
        queries = {
            "frontend": "React components, hooks, state management, UI layout",
            "backend": "API routes, database models, middleware, server configuration",
            "tester": "test files, component tests, API tests, test utilities",
            "reviewer": "security patterns, error handling, type safety, code quality",
            "architect": "project architecture, component hierarchy, data flow",
            "hardener": "error boundaries, loading states, auth guards, validation",
        }
        base = queries.get(agent_name, "project architecture and key components")

        errors = state.get("errors") or []
        if errors:
            base += f". Related errors: {str(errors[-1])[:200]}"

        return base

    def _get_relevant_files(
        self, agent_name: str, state: Dict[str, Any]
    ) -> Dict[str, str]:
        """Get files most relevant to the current agent (P1 candidates)."""
        all_code: Dict[str, str] = {}

        if agent_name == "frontend":
            all_code = dict(state.get("frontend_code") or {})
        elif agent_name == "backend":
            all_code = dict(state.get("backend_code") or {})
        elif agent_name in ("tester", "reviewer", "hardener"):
            all_code.update(state.get("frontend_code") or {})
            all_code.update(state.get("backend_code") or {})

        # If there are guardrails violations, prioritize violated files
        violations = state.get("guardrails_violations") or []
        violated_paths = {
            v["file_path"] for v in violations if v.get("severity") == "critical"
        }
        if violated_paths:
            return {k: v for k, v in all_code.items() if k in violated_paths}

        return all_code

    def _assemble_prompt(
        self,
        p1: Dict[str, str],
        p2: Dict[str, str],
    ) -> str:
        """Assemble final prompt from P1 + P2 sections."""
        parts: List[str] = []

        # P1 first (always present)
        for key in (
            "base_prompt",
            "requirements",
            "architecture",
            "errors",
            "guardrails",
        ):
            if key in p1:
                parts.append(p1[key])

        # P1 file sections
        for key, val in p1.items():
            if key.startswith("file:"):
                parts.append(val)

        # P2 / P2 summaries
        for key in (
            "project_summary",
            "rag_context",
            "other_files",
            "tests",
            "test_summary",
            "review",
            "review_summary",
        ):
            if key in p2:
                parts.append(p2[key])

        return "\n\n".join(parts)

    def _emergency_truncate(
        self, p1_sections: Dict[str, str], budget: int
    ) -> BudgetResult:
        """
        P1 alone exceeds budget — truncate the largest section.
        This is unusual but possible for huge requirements or very large files.
        """
        # Find the largest section
        largest_key = max(p1_sections, key=lambda k: estimate_tokens(p1_sections[k]))
        current_total = sum(estimate_tokens(s) for s in p1_sections.values())
        excess = current_total - budget

        # Truncate the largest section by the excess amount (in chars)
        content = p1_sections[largest_key]
        chars_to_cut = excess * 4 + 100  # +100 for safety margin
        if chars_to_cut < len(content):
            p1_sections[largest_key] = (
                content[: len(content) - chars_to_cut]
                + "\n... [TRUNCATED — exceeded context budget]"
            )
        else:
            p1_sections[largest_key] = (
                "... [TRUNCATED — section too large for context budget]"
            )

        prompt = self._assemble_prompt(p1_sections, {})
        token_count = estimate_tokens(prompt)

        return BudgetResult(
            agent_prompt=prompt,
            token_count=token_count,
            budget_limit=budget,
            trimmed=True,
            trimmed_report=f"EMERGENCY: P1 section '{largest_key}' truncated by ~{excess} tokens to fit budget.",
            rag_queries_made=0,
        )

    @staticmethod
    def _generate_trim_report(
        full_p2: Dict[str, str], summarized_p2: Dict[str, str]
    ) -> str:
        """Generate a human-readable report of what was trimmed."""
        full_tokens = sum(estimate_tokens(s) for s in full_p2.values())
        summary_tokens = sum(estimate_tokens(s) for s in summarized_p2.values())
        saved = full_tokens - summary_tokens

        lines = [
            f"Context trimmed: {full_tokens} → {summary_tokens} tokens (saved {saved})",
        ]

        for key in full_p2:
            if key not in summarized_p2:
                lines.append(
                    f"  - Dropped: {key} ({estimate_tokens(full_p2[key])} tokens)"
                )
            elif key in summarized_p2 and estimate_tokens(
                full_p2[key]
            ) != estimate_tokens(summarized_p2[key]):
                lines.append(
                    f"  - Summarized: {key} ({estimate_tokens(full_p2[key])} → {estimate_tokens(summarized_p2[key])} tokens)"
                )

        return "\n".join(lines)
