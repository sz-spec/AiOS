"""
VOS3 System Auditor
===================
Connects Gemini (context scanner) with GPT-5.2 (logical analyzer).
"""

from typing import Dict, Any
from pathlib import Path


class SystemAuditor:
    def __init__(self):
        self.latest_report = {}

    async def scan_and_audit(self, source_path: str) -> Dict[str, Any]:
        """Run full hybrid audit on VOS3 backend."""
        print(f"[Audit] Starting Hybrid Audit on: {source_path}")

        # Load codebase
        code_context = await self._get_full_codebase(source_path)

        # Stage 1: SCANNER (Gemini 3 Pro - 1M Context)
        scan_results = await self._call_model(
            stage="scanner",
            prompt=f"Map all async dependencies and shared states in this Python codebase:\n{code_context}",
        )

        # Stage 2: ANALYZER (GPT-5.2 Thinking)
        analysis = await self._call_model(
            stage="analyzer",
            prompt=f"Analyze the following scan for SMP/high-concurrency bottlenecks:\n{scan_results}",
        )

        # Stage 3: REVIEWER (GPT-5.2 Precision)
        security_review = await self._call_model(
            stage="reviewer",
            prompt=f"Audit these files for RBAC and JWT flaws:\n{code_context}",
        )

        # Stage 4: FORMAL (GPT-5.2 Thinking)
        formal_proof = await self._call_model(
            stage="formal_verifier",
            prompt=f"Prove os_pipeline.py review loop terminates:\n{code_context}",
        )

        self.latest_report = self._finalize_report(
            analysis, security_review, formal_proof
        )
        return self.latest_report

    def _finalize_report(
        self, analysis: Dict, security: Dict, formal: Dict
    ) -> Dict[str, Any]:
        """Calculate consistency score and generate final report."""
        issues = security.get("issues", [])
        score = 100 - (len(issues) * 5)

        return {
            "consistency_score": max(0, score),
            "critical_vulnerabilities": [
                i for i in issues if i.get("severity") == "critical"
            ],
            "refactoring_map": analysis.get("refactoring_needed", []),
            "architecture_findings": analysis.get("structural_issues", []),
            "formal_proofs": formal,
        }

    async def _get_full_codebase(self, path: str) -> str:
        """Read all .py files and combine into single context."""
        all_code = []
        backend_path = Path(path)

        for py_file in backend_path.rglob("*.py"):
            if "__pycache__" not in str(py_file):
                try:
                    content = py_file.read_text(encoding="utf-8")
                    rel_path = py_file.relative_to(backend_path)
                    all_code.append(f"=== {rel_path} ===\n{content[:5000]}")
                except Exception:
                    pass

        return "\n\n".join(all_code[:100])  # Limit for context

    async def _call_model(self, stage: str, prompt: str) -> Dict:
        """Call appropriate model for the stage."""
        # Map audit stages to SmartRouter roles
        stage_to_role = {
            "scanner": "researcher",  # Uses Gemini for large context
            "analyzer": "architect",  # Uses GPT for architecture analysis
            "reviewer": "reviewer",  # Uses Claude for security review
            "formal_verifier": "architect",  # Uses GPT for formal proofs
        }

        # Higher complexity for audit tasks
        stage_complexity = {
            "scanner": 7,
            "analyzer": 9,
            "reviewer": 9,
            "formal_verifier": 10,
        }

        try:
            from ai.llm.providers import LLM

            role = stage_to_role.get(stage, "coding")
            complexity = stage_complexity.get(stage, 8)

            llm = LLM(role=role, complexity=complexity)
            print(
                f"[Audit] Stage {stage} -> role={role}, complexity={complexity}, model={llm.get_info().get('model', 'unknown')}"
            )

            response = llm.generate(prompt)

            # Parse response for structured data
            return {
                "response": response.content,
                "stage": stage,
                "model": response.model,
                "tokens": response.tokens_used,
            }
        except Exception as e:
            print(f"[DEV MODE] Stage {stage}: {e}")

        return {"status": "dev_mode", "stage": stage}
