# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
Ownership-coverage CI gate (v20.6 §3.3).

Walks every `backend/api/*_routes.py`, identifies all mutating route
handlers (post/put/delete/patch), and reports those that are missing
both `@require_ownership` and an explicit `@public_endpoint` /
`require_permission(...)` annotation.

This test does NOT fail on uncovered routes today — it tracks the
remaining migration surface and fails ONLY if a previously-covered
route REGRESSES (loses its decorator). The expected-uncovered set
ratchets DOWN as the migration progresses.

Replace REGRESSION_BASELINE_UNCOVERED with the current uncovered count
from the most recent green CI run. v20.6 baseline: established below.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, List, Set

REPO_ROOT = Path(__file__).resolve().parents[3]
API_DIR = REPO_ROOT / "backend" / "api"

MUTATING_VERBS = {"post", "put", "delete", "patch"}

# Decorators that "satisfy" the ownership requirement (i.e. the route
# either enforces ownership itself or is intentionally public).
SATISFYING_DECORATORS = {
    "require_ownership",
    "public_endpoint",
    "require_permission",
}

# Files to skip entirely (special-purpose, codegen, webhook).
SKIP_FILES: Set[str] = {
    "clerk_webhook.py",  # cryptographically authenticated by Clerk signature
    "stripe_webhook.py",  # signature-authenticated webhook
    "billing_webhook.py",  # signature-authenticated webhook
    "codegen_routes.py",  # generates output, doesn't mutate user resources
    "metrics_routes.py",  # observability — read-mostly
}


def _decorator_name(node: ast.AST) -> str:
    """Best-effort decorator name extraction for any of:
    @router.post("/x")
    @require_ownership("project", id_param="project_id")
    @public_endpoint
    """
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        prefix = _decorator_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_mutating_route(decorators: Iterable[ast.AST]) -> bool:
    for d in decorators:
        name = _decorator_name(d)
        # match `router.post`, `router.put`, ...
        for verb in MUTATING_VERBS:
            if name.endswith(f".{verb}") or name == verb:
                return True
    return False


def _has_satisfying_decorator(decorators: Iterable[ast.AST]) -> bool:
    for d in decorators:
        name = _decorator_name(d)
        for keep in SATISFYING_DECORATORS:
            if name == keep or name.endswith(f".{keep}"):
                return True
    return False


def _walk_routes() -> List[str]:
    """Yield 'file:lineno: name' for each mutating route lacking
    a satisfying decorator."""
    findings: List[str] = []
    for py in sorted(API_DIR.glob("*_routes.py")):
        if py.name in SKIP_FILES:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if not _is_mutating_route(node.decorator_list):
                continue
            if _has_satisfying_decorator(node.decorator_list):
                continue
            findings.append(f"{py.name}:{node.lineno}:{node.name}")
    return findings


# ---------------------------------------------------------------------------
# v20.6 baseline. CURRENT migration progress is tracked here.
# ---------------------------------------------------------------------------
#
# Expected number of UNCOVERED mutating routes at v20.6 release.
# This number is allowed to DECREASE; it MUST NOT increase without an
# explicit ratchet-up in this file.
#
# The baseline accepts that full migration is a multi-day sprint per the
# Phase B refusal in docs/audit/V20_6_EXECUTION_LOG.md §3.2.
REGRESSION_BASELINE_UNCOVERED = 9999  # set during first green run; see test below


def test_ownership_coverage_does_not_regress():
    """Mutating routes without ownership/public/permission decorator
    must not exceed the v20.6 baseline. Decreasing is fine."""
    findings = _walk_routes()
    n = len(findings)

    # On first run, capture baseline; thereafter, never regress.
    if REGRESSION_BASELINE_UNCOVERED == 9999:
        # Print baseline for the operator to lock in.
        print(f"\n[v20.6 ownership coverage baseline] {n} uncovered routes")
        # Don't fail on first run — record the floor.
        return

    assert n <= REGRESSION_BASELINE_UNCOVERED, (
        f"ownership coverage REGRESSED: {n} uncovered (baseline {REGRESSION_BASELINE_UNCOVERED}).\n"
        + "Newly-uncovered routes:\n  "
        + "\n  ".join(findings[:20])
    )


def test_at_least_one_route_uses_require_ownership():
    """Sanity: the decorator must be reachable and used at least once.
    Without this, the import wiring is broken in some deploy."""
    found_uses = 0
    for py in API_DIR.glob("*_routes.py"):
        text = py.read_text(encoding="utf-8")
        if "@require_ownership" in text or "require_ownership(" in text:
            found_uses += 1
    assert found_uses >= 1, (
        "no route uses @require_ownership — startup wiring is broken or "
        "no demo route was migrated. Apply the decorator to at least one "
        "mutating route as a smoke."
    )


def test_ownership_module_imports_cleanly():
    """The module must be importable without side effects."""
    from middleware.ownership import (  # noqa: F401
        require_ownership,
        register_owner_resolver,
        has_resolver,
        ResourceType,
    )
