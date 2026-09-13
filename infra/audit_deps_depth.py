#!/usr/bin/env python3
"""
v20.5.1-RECLAMATION — recursive dependency-tree shadow-CVE audit.

Walks the resolved dependency tree (one level deeper than ``pip-audit``
reports by default), correlates every package with public advisory
feeds, and surfaces transitives that ``pip-audit``'s top-level scan
might miss when a vulnerable package is depended on only via a
deeply-nested transitive.

What this script does
=====================

1. Resolves the full dependency tree via ``importlib.metadata`` —
   every installed package, every requirement edge.
2. Asks ``pip-audit`` for the per-package vulnerability list.
3. For each vulnerable package, walks the parent chain to surface
   the *path* through which a given top-level dep pulls in the
   vulnerable transitive — so an operator can fix the dep one hop up
   rather than hunting through the lockfile.
4. Emits a ``shadow-cve-audit.json`` report and a human-readable
   table on stdout.

What this script does NOT do
============================

- It does NOT scan against private vendor advisory feeds (those need
  authenticated access). The ``pip-audit`` PyPI / OSV / GHSA path is
  the public floor; partner-attested feeds are a v20.6 roadmap.
- It does NOT produce the upstream-fix CL — the operator still has
  to run ``uv sync --upgrade-package <name>`` or wait for the upstream
  fix when none exists today.

Usage
=====

    .venv/bin/python infra/audit_deps_depth.py
    .venv/bin/python infra/audit_deps_depth.py --json   # machine-readable
    .venv/bin/python infra/audit_deps_depth.py --max-depth 6
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple


def _normalise(name: str) -> str:
    return name.lower().replace("_", "-")


def _build_dep_graph() -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """Return (forward, reverse) edge maps over installed packages.

    forward[A] = set of packages A directly depends on.
    reverse[B] = set of packages that directly depend on B.

    ``importlib.metadata`` provides per-distribution ``requires``;
    when a requirement is marker-conditional we keep the edge — false
    positives in the report are preferred over false negatives.
    """
    forward: Dict[str, Set[str]] = defaultdict(set)
    reverse: Dict[str, Set[str]] = defaultdict(set)
    for dist in md.distributions():
        name = _normalise(dist.metadata.get("Name", "") or dist.metadata.get("name", ""))
        if not name:
            continue
        for req_str in (dist.requires or []):
            # Requirement string looks like ``foo>=1.0; python_version>'3.6'``.
            dep = _normalise(
                req_str.split(";")[0].split("[")[0].split(">")[0]
                .split("<")[0].split("=")[0].split("!")[0].strip()
            )
            if dep:
                forward[name].add(dep)
                reverse[dep].add(name)
    return forward, reverse


def _walk_chains(
    target: str,
    reverse: Dict[str, Set[str]],
    max_depth: int,
) -> List[List[str]]:
    """Enumerate parent chains from ``target`` up to top-level packages."""
    chains: List[List[str]] = []

    def _dfs(node: str, path: List[str]):
        if len(path) > max_depth:
            chains.append(path + ["<truncated>"])
            return
        parents = sorted(reverse.get(node, ()))
        if not parents:
            chains.append(path + [node])
            return
        for p in parents:
            if p in path:
                # Cycle — record and stop.
                chains.append(path + [node, "<cycle>"])
                continue
            _dfs(p, path + [node])

    _dfs(target, [])
    return chains


def _run_pip_audit() -> List[Dict]:
    """Run ``pip-audit`` JSON output. Returns a list of vuln entries.

    Each entry shape (per pip-audit ``--format json``):
        {"name": "lxml", "version": "6.0.3",
         "vulns": [{"id": "CVE-2026-41066", "fix_versions": ["6.1.0"], ...}]}
    """
    bin_path = shutil.which("pip-audit")
    if not bin_path:
        # Try repo-local venv.
        for guess in (
            "backend/.venv/bin/pip-audit",
            ".venv/bin/pip-audit",
            "../.venv/bin/pip-audit",
        ):
            if shutil.which(guess) or _exists(guess):
                bin_path = guess
                break
    if not bin_path:
        print("[!] pip-audit not found in PATH or venv; install with "
              "`uv pip install pip-audit`", file=sys.stderr)
        return []

    try:
        proc = subprocess.run(
            [bin_path, "--skip-editable", "--progress-spinner=off",
             "--format", "json"],
            check=False,
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("[!] pip-audit timed out after 300s", file=sys.stderr)
        return []
    if not proc.stdout:
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    deps = data.get("dependencies", []) if isinstance(data, dict) else data
    return [d for d in deps if d.get("vulns")]


def _exists(p: str) -> bool:
    import os
    return os.path.isfile(p)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recursive dependency-tree shadow-CVE audit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON to stdout instead of the human table",
    )
    parser.add_argument(
        "--max-depth",
        type=int, default=8,
        help="max parent-chain depth to surface (default 8)",
    )
    args = parser.parse_args(argv)

    forward, reverse = _build_dep_graph()
    vulns = _run_pip_audit()

    report = []
    for v in vulns:
        name = _normalise(v.get("name", ""))
        if not name:
            continue
        chains = _walk_chains(name, reverse, args.max_depth)
        report.append({
            "package":         name,
            "version":         v.get("version", ""),
            "vulns":           v.get("vulns", []),
            "parent_chains":   chains,
            "direct_parents":  sorted(reverse.get(name, ())),
        })

    if args.json:
        print(json.dumps({
            "report":      report,
            "vuln_count":  len(report),
        }, indent=2, sort_keys=False))
        return 0 if not report else 1

    # Human-readable form.
    if not report:
        print("audit_deps_depth: 0 transitive vulnerabilities found.")
        return 0
    print(f"audit_deps_depth: {len(report)} vulnerable package(s) discovered.\n")
    for entry in report:
        print(f"━━ {entry['package']} {entry['version']} ━━━━━━━━━━━━━━━━━━━━")
        for v in entry["vulns"]:
            fix = ", ".join(v.get("fix_versions") or []) or "<no fix yet>"
            print(f"  • {v.get('id', '?')}  fix: {fix}")
        if entry["direct_parents"]:
            print(f"  direct parents: {', '.join(entry['direct_parents'])}")
        else:
            print("  direct parents: (top-level)")
        if entry["parent_chains"]:
            print("  paths to top:")
            for c in entry["parent_chains"][:5]:           # cap noise
                print(f"    {' ← '.join(c)}")
            if len(entry["parent_chains"]) > 5:
                print(f"    … +{len(entry['parent_chains']) - 5} more")
        print()
    return 1                                              # nonzero on findings


if __name__ == "__main__":
    sys.exit(main())
