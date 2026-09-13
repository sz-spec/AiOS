"""
H.17 — Fail-open sweep (TEST_PLAN_300 §H.17)
============================================

The entire vOS security architecture is built on a *fail-closed* contract:
every ``require_*()`` / ``check_*()`` / ``validate_*()`` gate must REFUSE the
unsafe operation when it cannot prove the operation is safe. A single gate that
fails *open* — an early ``return True``, a swallowed ``except: pass``, an
``except: return <permit>`` — silently defeats the whole model.

This test parses the AST of every gate function in the security surface and
flags fail-open smells. It is intentionally a *lint*, not a unit test: it does
not execute the gates, it reads their control flow. Findings are reported as
test failures so they show up in the hard-gate run; a gate that legitimately
returns a permissive value must be added to ``KNOWN_SAFE`` with a rationale.

Run:
    .venv_p312/bin/python -m pytest tests/audit/test_h17_fail_open_sweep.py -v
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

# Directories that hold the fail-closed security surface.
GATE_DIRS = [
    BACKEND / "security",
    BACKEND / "core" / "security",
    BACKEND / "services",
    BACKEND / "ai" / "agents",
]

GATE_PREFIXES = ("require_", "check_", "validate_", "_assert_", "ensure_")

# (file_substr, func_name): reason it is allowed to return a permissive value.
# Keep this list SHORT and justified — every entry is a documented exception.
KNOWN_SAFE: dict[tuple[str, str], str] = {}


def _iter_gate_functions():
    for d in GATE_DIRS:
        if not d.exists():
            continue
        for py in d.rglob("*.py"):
            if "__pycache__" in str(py) or py.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                        node.name.startswith(GATE_PREFIXES):
                    yield py, node


def _rel(py: Path) -> str:
    return str(py.relative_to(BACKEND))


# ---------------------------------------------------------------------------
# Smell 1: bare/broad except that swallows or permits.
# ---------------------------------------------------------------------------

def _find_swallowing_excepts(node: ast.AST):
    """Return list of (lineno, kind) for except handlers that pass or return."""
    hits = []
    for h in ast.walk(node):
        if not isinstance(h, ast.ExceptHandler):
            continue
        # bare `except:`  or  `except Exception:`
        broad = h.type is None or (
            isinstance(h.type, ast.Name) and h.type.id in {"Exception", "BaseException"}
        )
        body = h.body
        only_pass = len(body) == 1 and isinstance(body[0], ast.Pass)
        returns_true = any(
            isinstance(s, ast.Return)
            and isinstance(s.value, ast.Constant)
            and s.value.value is True
            for s in body
        )
        if broad and (only_pass or returns_true):
            kind = "except:pass" if only_pass else "except:return True"
            hits.append((h.lineno, kind))
    return hits


# ---------------------------------------------------------------------------
# Smell 2: the FIRST top-level statement is `return True` (gate that never
#          actually checks anything — a stub left enabled).
# ---------------------------------------------------------------------------

def _is_trivial_permit(node: ast.AST) -> bool:
    body = [s for s in node.body if not isinstance(s, ast.Expr)]  # skip docstring
    if not body:
        return False
    first = body[0]
    return (
        isinstance(first, ast.Return)
        and isinstance(first.value, ast.Constant)
        and first.value.value is True
    )


GATES = list(_iter_gate_functions())


def test_gates_were_discovered():
    """Sanity: the sweep actually found the security surface."""
    assert len(GATES) >= 20, f"expected >=20 gate fns, found {len(GATES)}"


@pytest.mark.parametrize(
    "py,node",
    GATES,
    ids=[f"{_rel(p)}::{n.name}" for p, n in GATES],
)
def test_gate_has_no_swallowing_except(py, node):
    key = (py.name, node.name)
    hits = _find_swallowing_excepts(node)
    hits = [h for h in hits if key not in KNOWN_SAFE]
    assert not hits, (
        f"FAIL-OPEN smell in {_rel(py)}::{node.name} — "
        f"broad except that swallows/permits at line(s) {hits}. "
        f"A gate must fail CLOSED on exception. If intentional, add to KNOWN_SAFE."
    )


@pytest.mark.parametrize(
    "py,node",
    GATES,
    ids=[f"{_rel(p)}::{n.name}" for p, n in GATES],
)
def test_gate_is_not_trivial_permit(py, node):
    key = (py.name, node.name)
    if key in KNOWN_SAFE:
        pytest.skip(KNOWN_SAFE[key])
    assert not _is_trivial_permit(node), (
        f"FAIL-OPEN smell in {_rel(py)}::{node.name} — "
        f"first statement is `return True` (gate never checks anything). "
        f"If this is an intentional always-allow, add to KNOWN_SAFE with rationale."
    )
