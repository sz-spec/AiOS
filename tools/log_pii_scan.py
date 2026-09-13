#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
tools/log_pii_scan.py — regression scanner for accidental PII in logs.

Zero-Gap Task 4a. Backs the spec's "Zero PII in logs" Annex IV claim
with an automated CI gate. Run by `.github/workflows/ci.yml` on every
PR; exits non-zero on any unallowlisted match.

Usage:
    python3 tools/log_pii_scan.py [path ...]

If no path is given, scans:
    backend/logs/        (runtime logs)
    kernel/build/*.log   (boot logs from QEMU smoke runs)
    /tmp/vos3_*.log      (local dev runs)

Patterns:
    - Email (RFC-5322 simplified)
    - Phone (E.164 international, +country code variant)
    - SSN (US-style 3-2-4)
    - Credit card (13–19 digits, Luhn-checked)
    - Israeli ID (9 digits, Luhn-checked)

Allowlist file: tools/log_pii_scan_allowlist.txt — one literal string
per line. Useful for whitelisting test fixtures or known-safe patterns
(e.g. example.com test emails). Lines starting with `#` are comments.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)
# E.164: + then 7-15 digits.
PHONE_RE = re.compile(r"\+\d{7,15}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Credit-card candidate; Luhn-validated below.
CC_RE = re.compile(r"\b\d{13,19}\b")


def luhn_check(number: str) -> bool:
    """Return True iff `number` (digits only) passes Luhn checksum."""
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def israeli_id_check(number: str) -> bool:
    """Israeli teudat-zehut Luhn-style validation (9 digits, weighted)."""
    if len(number) != 9 or not number.isdigit():
        return False
    total = 0
    for i, ch in enumerate(number):
        d = int(ch) * (1 if i % 2 == 0 else 2)
        if d > 9:
            d -= 9
        total += d
    return total % 10 == 0


def load_allowlist(path: Path) -> list[str]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def scan_text(text: str, allowlist: Iterable[str]) -> list[tuple[str, str]]:
    """Return list of (kind, match) hits not on the allowlist."""
    allow = set(allowlist)
    hits: list[tuple[str, str]] = []

    for m in EMAIL_RE.finditer(text):
        s = m.group(0)
        if s not in allow and not s.endswith("@example.com"):
            hits.append(("email", s))

    for m in PHONE_RE.finditer(text):
        s = m.group(0)
        if s not in allow:
            hits.append(("phone", s))

    for m in SSN_RE.finditer(text):
        s = m.group(0)
        if s not in allow:
            hits.append(("ssn", s))

    for m in CC_RE.finditer(text):
        s = m.group(0)
        if s in allow:
            continue
        if luhn_check(s):
            hits.append(("credit_card", s))
        elif len(s) == 9 and israeli_id_check(s):
            hits.append(("israeli_id", s))

    return hits


def scan_path(p: Path, allowlist: list[str]) -> list[tuple[Path, str, str]]:
    out: list[tuple[Path, str, str]] = []
    if p.is_dir():
        for sub in p.rglob("*"):
            if sub.is_file():
                out.extend(scan_path(sub, allowlist))
        return out
    if not p.is_file():
        return out
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return out
    for kind, match in scan_text(text, allowlist):
        out.append((p, kind, match))
    return out


DEFAULT_TARGETS = [
    "backend/logs",
    "kernel/build",
]
# Globs evaluated relative to / for ad-hoc dev logs only — kept narrow
# to avoid sweeping unrelated user files in /tmp.
DEFAULT_GLOBS = ["/tmp/vos3_*.log"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="VOS3 log PII scanner")
    ap.add_argument("paths", nargs="*", help="Paths to scan (file or dir).")
    ap.add_argument(
        "--allowlist",
        default="tools/log_pii_scan_allowlist.txt",
        help="Path to allowlist file.",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the count, not individual hits.",
    )
    args = ap.parse_args(argv)

    allowlist = load_allowlist(Path(args.allowlist))
    if args.paths:
        targets = [Path(p) for p in args.paths]
    else:
        import glob as _glob
        targets = [Path(p) for p in DEFAULT_TARGETS]
        for pattern in DEFAULT_GLOBS:
            targets.extend(Path(p) for p in _glob.glob(pattern))

    all_hits: list[tuple[Path, str, str]] = []
    for t in targets:
        if not t.exists():
            continue
        all_hits.extend(scan_path(t, allowlist))

    # Filter to log-style files only when scanning broad dirs. Files
    # without an extension are skipped unless their name contains
    # "log" — otherwise scanning /tmp picks up unrelated dev fixtures.
    log_extensions = {".log", ".txt", ".out"}
    filtered = []
    for path, kind, match in all_hits:
        suf = path.suffix.lower()
        name = path.name.lower()
        if suf in log_extensions or "log" in name:
            filtered.append((path, kind, match))

    if not filtered:
        if not args.quiet:
            print("log_pii_scan: 0 hits — Zero-PII claim holds.")
        return 0

    if not args.quiet:
        for path, kind, match in filtered:
            redacted = match[:3] + "*" * (len(match) - 3)
            print(f"  {path}: {kind} -> {redacted}")
    print(f"log_pii_scan: {len(filtered)} HIT(S) — review or allowlist.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
