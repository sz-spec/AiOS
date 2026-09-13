#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
M4 — QEMU Assertion Runner ("The Judge")

Boots VOS3 inside QEMU, captures the COM1 serial stream, and parses
VOS3_ASSERT_CERT lines emitted by the kernel-side harness in
kernel/src/diag/assert_cert.c.

Required binary on PATH: qemu-system-x86_64.

Output line format (one per VOS3_ASSERT_CERT call):

    ~~CERT~~ <id> <PASS|FAIL> <file>:<line> <expr>

Sentinel:

    ~~CERT~~ DONE <count>

The runner stops reading on DONE (or the timeout, whichever comes
first). It prints a per-ID summary to stdout and exits with:

    0   — every populated cert ID emitted exactly once and PASSed
    1   — at least one populated cert FAILed or was missing
    2   — runtime error (QEMU not on PATH, kernel ELF missing, etc.)

Honest accounting: the runner reports BOTH the ID-registry's claimed
populated count (from kernel/include/vos/assert_cert_ids.h) and the
count actually observed on stdout. If those differ, the registry and
the implementation are out of sync — that is itself a failure.

Usage:

    python3 tools/runner/qemu_assert_runner.py \\
        --elf kernel/build/vos3.elf \\
        [--timeout 60] \\
        [--qemu qemu-system-x86_64] \\
        [--registry kernel/include/vos/assert_cert_ids.h] \\
        [--junit out.xml]
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

CERT_RE = re.compile(
    r"~~CERT~~\s+"
    r"(?P<id>\d+)\s+"
    r"(?P<status>PASS|FAIL)\s+"
    r"(?P<site>[^\s:]+:\d+)\s+"
    r"(?P<expr>.*)$"
)
DONE_RE = re.compile(r"~~CERT~~\s+DONE\s+(?P<count>\d+)\s*$")
REGISTRY_DEFINE_RE = re.compile(
    r"^\s*#define\s+(VOS3_CERT_\w+)\s+(\d+)\s*(?:/\*.*\*/)?\s*$"
)


@dataclass
class CertResult:
    id: int
    passed: bool
    site: str
    expr: str


# ---------------------------------------------------------------------------
# Registry parsing
# ---------------------------------------------------------------------------


def parse_registry(path: Path) -> dict[int, str]:
    """Return {numeric_id: symbolic_name} for every #define in the
    registry header. Skips MAX_ID_CURRENTLY_USED so it doesn't count
    as a real cert point."""
    out: dict[int, str] = {}
    for raw in path.read_text().splitlines():
        m = REGISTRY_DEFINE_RE.match(raw)
        if not m:
            continue
        name, value = m.group(1), int(m.group(2))
        if name == "VOS3_CERT_MAX_ID_CURRENTLY_USED":
            continue
        if name in out.values():
            print(f"WARNING: duplicate symbol name {name}", file=sys.stderr)
        if value in out:
            print(
                f"ERROR: duplicate ID {value} in registry "
                f"({out[value]} vs {name})",
                file=sys.stderr,
            )
        out[value] = name
    return out


# ---------------------------------------------------------------------------
# QEMU run + serial parse
# ---------------------------------------------------------------------------


def run_qemu(
    elf_path: Path,
    qemu: str,
    timeout_s: int,
) -> tuple[list[CertResult], Optional[int]]:
    """Boot VOS3 in QEMU, capture COM1, parse cert lines.

    Returns (results, done_count) where done_count is None if the
    DONE sentinel was not seen before the timeout (which is itself a
    failure mode but distinct from FAIL asserts)."""
    if not elf_path.is_file():
        raise FileNotFoundError(f"kernel ELF not found: {elf_path}")

    # M5 NOTE: -smp 1 is the default until VOS3_HW_LAPIC is enabled at boot.
    # Without LAPIC ICR programming, SMP wake hangs at "Waking CPU 1" because
    # vos3_apic_send_resched_ipi returns -ENODEV (g_apic_base_va NULL).
    # Override via --smp once HW-2 is wired.
    cmd = [
        qemu,
        "-kernel", str(elf_path),
        "-m", "4096M",
        "-smp", "1",
        "-cpu", "max",
        "-display", "none",
        "-monitor", "none",
        "-no-reboot",
        "-serial", "stdio",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    results: list[CertResult] = []
    done_count: Optional[int] = None
    deadline = time.time() + timeout_s

    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            done_m = DONE_RE.search(line)
            if done_m:
                done_count = int(done_m.group("count"))
                break
            cert_m = CERT_RE.search(line)
            if cert_m:
                results.append(CertResult(
                    id=int(cert_m.group("id")),
                    passed=(cert_m.group("status") == "PASS"),
                    site=cert_m.group("site"),
                    expr=cert_m.group("expr").strip(),
                ))
            if time.time() > deadline:
                break
    finally:
        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    return results, done_count


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def emit_junit(
    out_path: Path,
    registry: dict[int, str],
    results: list[CertResult],
    done_count: Optional[int],
) -> None:
    """Write a JUnit-XML report. One <testcase> per registry entry.
    Missing emits become <failure>; FAIL emits become <failure>;
    duplicates become <failure>; PASS becomes a clean <testcase>."""
    by_id: dict[int, list[CertResult]] = {}
    for r in results:
        by_id.setdefault(r.id, []).append(r)

    cases_xml: list[str] = []
    fails = 0
    for cid, name in sorted(registry.items()):
        emits = by_id.get(cid, [])
        if not emits:
            cases_xml.append(
                f'  <testcase name="{name}" classname="vos3.cert" '
                f'id="{cid}"><failure type="missing">'
                f'no emit observed</failure></testcase>'
            )
            fails += 1
        elif len(emits) > 1:
            cases_xml.append(
                f'  <testcase name="{name}" classname="vos3.cert" '
                f'id="{cid}"><failure type="duplicate">'
                f'observed {len(emits)} times</failure></testcase>'
            )
            fails += 1
        else:
            e = emits[0]
            if not e.passed:
                cases_xml.append(
                    f'  <testcase name="{name}" classname="vos3.cert" '
                    f'id="{cid}"><failure type="assertion-fail">'
                    f'{e.site}: {e.expr}</failure></testcase>'
                )
                fails += 1
            else:
                cases_xml.append(
                    f'  <testcase name="{name}" classname="vos3.cert" '
                    f'id="{cid}"/>'
                )

    if done_count is None:
        cases_xml.append(
            '  <testcase name="harness.done" classname="vos3.cert">'
            '<failure type="no-sentinel">DONE line not observed; '
            'kernel may have crashed mid-run or runner timed out</failure>'
            '</testcase>'
        )
        fails += 1

    out_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuite name="vos3.assert_cert" '
        f'tests="{len(registry) + 1}" failures="{fails}">\n'
        + "\n".join(cases_xml)
        + "\n</testsuite>\n"
    )


def print_summary(
    registry: dict[int, str],
    results: list[CertResult],
    done_count: Optional[int],
) -> int:
    """Console summary. Returns POSIX exit code."""
    by_id: dict[int, list[CertResult]] = {}
    for r in results:
        by_id.setdefault(r.id, []).append(r)

    populated = len(registry)
    emitted = len({r.id for r in results})
    failed = [r for r in results if not r.passed]
    duplicates = [cid for cid, lst in by_id.items() if len(lst) > 1]
    missing = sorted(set(registry) - set(by_id))

    print()
    print("===== M4 Assertion Harness Summary =====")
    print(f"Registry-populated cert points : {populated}")
    print(f"Emitted-and-parsed cert IDs    : {emitted}")
    if done_count is not None:
        print(f"Kernel-reported emit count     : {done_count}")
        if done_count != len(results):
            print(
                f"  WARNING: kernel claims {done_count} emits but "
                f"runner parsed {len(results)} cert lines"
            )
    else:
        print("Kernel-reported emit count     : MISSING (no DONE sentinel)")
    print(f"FAIL emits                     : {len(failed)}")
    print(f"Duplicate IDs                  : {len(duplicates)}")
    print(f"Missing IDs (in registry, not emitted): {len(missing)}")
    if missing[:10]:
        for cid in missing[:10]:
            print(f"  - {cid:>3} {registry[cid]}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")
    print("=========================================")

    if failed or duplicates or missing or done_count is None:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[Iterable[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--elf", default="kernel/build/vos3.elf",
                   help="path to vos3.elf")
    p.add_argument("--qemu", default="qemu-system-x86_64",
                   help="QEMU binary name or absolute path")
    p.add_argument("--timeout", type=int, default=60,
                   help="serial-read timeout in seconds")
    p.add_argument(
        "--registry", default="kernel/include/vos/assert_cert_ids.h",
        help="path to the cert ID registry header",
    )
    p.add_argument("--junit", default=None,
                   help="optional path to write JUnit XML report")
    args = p.parse_args(argv)

    elf = Path(args.elf).resolve()
    registry_path = Path(args.registry).resolve()

    try:
        registry = parse_registry(registry_path)
    except FileNotFoundError:
        print(f"ERROR: registry header not found: {registry_path}",
              file=sys.stderr)
        return 2

    if not _which(args.qemu):
        print(
            f"ERROR: {args.qemu} not on PATH — install qemu-system-x86_64 "
            f"or pass --qemu /absolute/path",
            file=sys.stderr,
        )
        return 2

    try:
        results, done_count = run_qemu(elf, args.qemu, args.timeout)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.junit:
        emit_junit(Path(args.junit), registry, results, done_count)

    return print_summary(registry, results, done_count)


def _which(name: str) -> Optional[str]:
    if os.path.isabs(name):
        return name if os.access(name, os.X_OK) else None
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(d, name)
        if os.access(cand, os.X_OK):
            return cand
    return None


if __name__ == "__main__":
    sys.exit(main())
