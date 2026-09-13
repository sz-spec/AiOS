#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
Pydantic → TypeScript schema sync (v20.6.1).

Walks every backend/api/*.py, finds Pydantic BaseModel subclasses, and
emits a single TypeScript file at frontend/lib/api-types.ts containing
type definitions for each model. Replaces the "manual guessing of field
names" gap documented in docs/audit/300_INTEGRATION_FAILURES_REPORT.md
§5.1.

Why hand-rolled instead of `datamodel-code-generator`:
  * Zero new dep on the build path (the codegen lib pulls 30+ packages).
  * Predictable output suitable for code review.
  * We control how Optional, list[T], dict[K,V], Literal[...] map to TS.

What it covers:
  * `class Foo(BaseModel):` with annotated fields.
  * `Optional[T]` → `T | null`.
  * `list[T]`, `List[T]`, `tuple[T, ...]` → `T[]`.
  * `dict[K, V]`, `Dict[K, V]` → `Record<K, V>`.
  * `Literal["a", "b"]` → `"a" | "b"`.
  * Primitive maps: str→string, int/float→number, bool→boolean,
    Any→any, None→null.
  * Default values → field marked optional (`field?: T`).

What it does NOT cover (acknowledged gaps):
  * Discriminated unions (`Union[A, B]` with `Literal` discriminator).
  * Forward references via TYPE_CHECKING.
  * Generic Pydantic models (`Generic[T]`).

When the script encounters an unsupported pattern, it emits a TS
`unknown` with a `// FIXME(schema-sync):` comment so the operator can
hand-fix that one type without losing the rest.

Usage:
  python3 tools/schema/sync_types.py             # writes frontend/lib/api-types.ts
  python3 tools/schema/sync_types.py --check     # exits non-zero if file is stale
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import sys
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[2]
API_DIR = REPO / "backend" / "api"
OUT_PATH = REPO / "frontend" / "lib" / "api-types.ts"

PRIMITIVE_MAP = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "bytes": "string",
    "Any": "any",
    "None": "null",
    "datetime": "string",
    "date": "string",
    "UUID": "string",
    # Bare collection annotations (no subscript) — emit valid TS so the
    # generated api-types.ts compiles. Subscripted forms `Dict[K,V]` /
    # `List[T]` are handled separately in the ast.Subscript branch.
    "dict": "Record<string, any>",
    "Dict": "Record<string, any>",
    "Mapping": "Record<string, any>",
    "list": "any[]",
    "List": "any[]",
    "tuple": "any[]",
    "Tuple": "any[]",
    "set": "any[]",
    "Set": "any[]",
    # Pydantic / project-specific aliases — Python typing references that
    # the AST walker can't resolve (defined elsewhere, e.g. as Literal[...]
    # in unrelated files OR imported from pydantic). Coerce to TS shapes
    # so the generated api-types.ts compiles cleanly.
    "EmailStr": "string",
    "AnyUrl": "string",
    "HttpUrl": "string",
    "Language": "string",
    "ChatRole": "string",
    "PromptType": "string",
    "AgentStatus": "string",
    "Decimal": "number",
}


def _ts_type(node: ast.AST) -> str:
    """Best-effort Python annotation → TypeScript type."""
    if isinstance(node, ast.Name):
        return PRIMITIVE_MAP.get(node.id, node.id)
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "null"
        return repr(node.value)
    if isinstance(node, ast.Subscript):
        # Handle Optional[T], List[T], Dict[K,V], Literal[...], etc.
        base = _ts_type(node.value) if not isinstance(node.value, ast.Name) else node.value.id
        slice_node = node.slice
        # Python 3.9+: slice is the inner node directly.
        if isinstance(slice_node, ast.Index):  # py<3.9 compat
            slice_node = slice_node.value
        if base == "Optional":
            return f"({_ts_type(slice_node)}) | null"
        if base in ("List", "list"):
            return f"({_ts_type(slice_node)})[]"
        if base in ("Tuple", "tuple"):
            if isinstance(slice_node, ast.Tuple):
                items = [_ts_type(e) for e in slice_node.elts if not (
                    isinstance(e, ast.Constant) and e.value is Ellipsis
                )]
                return f"({' | '.join(items)})[]"
            return f"({_ts_type(slice_node)})[]"
        if base in ("Dict", "dict", "Mapping"):
            if isinstance(slice_node, ast.Tuple) and len(slice_node.elts) == 2:
                k = _ts_type(slice_node.elts[0])
                v = _ts_type(slice_node.elts[1])
                if k not in ("string", "number"):
                    k = "string"
                return f"Record<{k}, {v}>"
            return "Record<string, unknown>"
        if base == "Literal":
            if isinstance(slice_node, ast.Tuple):
                lits = []
                for e in slice_node.elts:
                    if isinstance(e, ast.Constant):
                        if isinstance(e.value, str):
                            lits.append(f'"{e.value}"')
                        else:
                            lits.append(repr(e.value))
                return " | ".join(lits) if lits else "string"
            if isinstance(slice_node, ast.Constant):
                return f'"{slice_node.value}"' if isinstance(slice_node.value, str) else repr(slice_node.value)
            return "string"
        if base == "Union":
            if isinstance(slice_node, ast.Tuple):
                return " | ".join(_ts_type(e) for e in slice_node.elts)
            return _ts_type(slice_node)
        # Fallback for unknown generics.
        return f"{base}<{_ts_type(slice_node)}>"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        # Python 3.10+ pipe union: `int | None`
        return f"{_ts_type(node.left)} | {_ts_type(node.right)}"
    if isinstance(node, ast.Attribute):
        # e.g. `typing.Optional` → fall back to the attribute name
        return PRIMITIVE_MAP.get(node.attr, node.attr)
    return f"unknown /* FIXME(schema-sync): {ast.dump(node, annotate_fields=False)[:60]} */"


def _is_basemodel_subclass(node: ast.ClassDef) -> bool:
    for b in node.bases:
        if isinstance(b, ast.Name) and b.id == "BaseModel":
            return True
        if isinstance(b, ast.Attribute) and b.attr == "BaseModel":
            return True
    return False


def _extract_models(py_path: Path) -> list[tuple[str, list[tuple[str, str, bool]]]]:
    """Returns [(class_name, [(field_name, ts_type, has_default), ...])]"""
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    out: list[tuple[str, list[tuple[str, str, bool]]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_basemodel_subclass(node):
            continue
        fields: list[tuple[str, str, bool]] = []
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                name = item.target.id
                if name.startswith("_"):
                    continue
                ts = _ts_type(item.annotation)
                has_default = item.value is not None
                fields.append((name, ts, has_default))
        if fields:
            out.append((node.name, fields))
    return out


def _render(models_by_file: dict[str, list]) -> str:
    lines = [
        "// SPDX-License-Identifier: MIT",
        "// SPDX-FileCopyrightText: 2026 VOS3 Project",
        "//",
        "// AUTO-GENERATED by tools/schema/sync_types.py — DO NOT EDIT BY HAND.",
        "//",
        "// Source of truth: backend/api/*_routes.py Pydantic BaseModel definitions.",
        "// Regenerate after Pydantic schema changes:",
        "//     python3 tools/schema/sync_types.py",
        "//",
        "// Schema-sync gap closure per",
        "// docs/audit/300_INTEGRATION_FAILURES_REPORT.md §5.1.",
        "",
    ]
    seen: set[str] = set()
    for src_file in sorted(models_by_file.keys()):
        models = models_by_file[src_file]
        if not models:
            continue
        rel = os.path.relpath(src_file, REPO)
        lines.append(f"// ----- {rel} -----")
        lines.append("")
        for cls_name, fields in models:
            if cls_name in seen:
                # Same class name in multiple files → emit only first; second
                # gets a stub comment so the reader knows there's a duplicate.
                lines.append(f"// (duplicate of {cls_name} from earlier file — skipped)")
                lines.append("")
                continue
            seen.add(cls_name)
            lines.append(f"export interface {cls_name} {{")
            for name, ts, has_default in fields:
                opt = "?" if has_default else ""
                lines.append(f"  {name}{opt}: {ts};")
            lines.append("}")
            lines.append("")
    return "\n".join(lines)


def _content_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 2)[1].strip())
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if api-types.ts would change")
    args = ap.parse_args()

    if not API_DIR.exists():
        print(f"ERROR: {API_DIR} does not exist", file=sys.stderr)
        return 2

    models_by_file: dict[str, list] = {}
    for py in sorted(API_DIR.glob("*.py")):
        if py.name.startswith("__"):
            continue
        models = _extract_models(py)
        if models:
            models_by_file[str(py)] = models

    rendered = _render(models_by_file)

    if args.check:
        existing = OUT_PATH.read_text() if OUT_PATH.exists() else ""
        if existing != rendered:
            print("api-types.ts is STALE — run python3 tools/schema/sync_types.py", file=sys.stderr)
            print(f"  expected sha256: {hashlib.sha256(rendered.encode()).hexdigest()[:16]}", file=sys.stderr)
            print(f"  actual sha256:   {_content_hash(OUT_PATH)[:16]}", file=sys.stderr)
            return 1
        print("api-types.ts is up to date ✓")
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(rendered)
    n_classes = sum(len(m) for m in models_by_file.values())
    print(f"Wrote {OUT_PATH.relative_to(REPO)} — {n_classes} interfaces from {len(models_by_file)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
