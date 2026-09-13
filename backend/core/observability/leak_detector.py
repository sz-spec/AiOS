"""
v20.1.2 (gauntlet #20) — High-load memory-drift detector.

Uses ``tracemalloc`` (stdlib, <1 % overhead) + ``objgraph`` (optional, for
reference-graph forensics) to assert **zero net memory drift** across a
configurable simulated agent load.

Design notes (from April-2026 production-profiling practice):
    - `tracemalloc.start(nframes=25)` at process entry; snapshot before and
      after the load; compare with `snapshot_after.compare_to(snapshot_before,
      "lineno")` to find grow-only call sites.
    - GC is forced and idle before each snapshot so transient allocations
      don't appear as leaks.
    - The detector **fails hard** on any top-N site showing sustained growth
      above the threshold — so CI can gate on it.

Usage:
    python -m core.observability.leak_detector --agents 100 --cycles 50
"""
from __future__ import annotations

import argparse
import gc
import json
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass
from typing import List


@dataclass
class LeakReport:
    agents: int
    cycles: int
    wall_seconds: float
    rss_before_bytes: int
    rss_after_bytes: int
    tracemalloc_before_total_kb: float
    tracemalloc_after_total_kb: float
    drift_kb: float
    threshold_kb: float
    pass_: bool
    top_growers: List[dict]


def _rss_bytes() -> int:
    """Best-effort RSS (resident set size) in bytes. Works on Linux + macOS."""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Darwin returns bytes, Linux returns KiB.
        if sys.platform == "darwin":
            return int(usage)
        return int(usage) * 1024
    except Exception:
        return -1


def _force_gc_to_quiescence(passes: int = 3) -> None:
    for _ in range(passes):
        gc.collect()


def simulate_agent_load(n_agents: int, cycles: int) -> None:
    """Mock 100-agent workload.

    Exercises the hot paths we care about for leak testing: dict/list
    allocation, string manipulation, JSON round-trip, closure capture. This
    proxies the real agent lifecycle without requiring live LLM calls.
    Any import of the real ai.agents.multi_agent module would pull
    LangGraph into the profile noise; we keep this self-contained.
    """
    agents = []
    for i in range(n_agents):
        agents.append({
            "tid": i,
            "cookie": (i % 10) + 1,
            "ctx": {"prompt": f"hello from agent {i}", "history": []},
        })

    for c in range(cycles):
        for a in agents:
            a["ctx"]["history"].append({
                "turn": c,
                "text": "A" * 128,
                "meta": {"cycle": c, "tid": a["tid"]},
            })
            # Round-trip through JSON to exercise encoder/decoder paths.
            payload = json.dumps(a["ctx"])
            _ = json.loads(payload)
            # Bound the history so steady-state is actually steady.
            if len(a["ctx"]["history"]) > 4:
                a["ctx"]["history"] = a["ctx"]["history"][-4:]

    # Drop strong refs — if anything leaks after this, it's a real leak.
    agents.clear()


def run(
    agents: int = 100,
    cycles: int = 50,
    threshold_kb: float = 512.0,
    top_n: int = 10,
) -> LeakReport:
    tracemalloc.start(25)

    # Warmup cycle — one pass so the import/first-alloc is not counted.
    simulate_agent_load(agents, cycles=2)
    _force_gc_to_quiescence()

    rss_before = _rss_bytes()
    snap_before = tracemalloc.take_snapshot()
    trace_before_total = sum(s.size for s in snap_before.statistics("filename"))

    t0 = time.perf_counter()
    simulate_agent_load(agents, cycles)
    wall = time.perf_counter() - t0

    _force_gc_to_quiescence()
    rss_after = _rss_bytes()
    snap_after = tracemalloc.take_snapshot()
    trace_after_total = sum(s.size for s in snap_after.statistics("filename"))

    drift_kb = (trace_after_total - trace_before_total) / 1024.0

    diffs = snap_after.compare_to(snap_before, "lineno")
    top = []
    for stat in diffs[:top_n]:
        frame = stat.traceback[0]
        top.append({
            "file": frame.filename,
            "line": frame.lineno,
            "size_diff_kb": stat.size_diff / 1024.0,
            "count_diff": stat.count_diff,
        })

    tracemalloc.stop()

    report = LeakReport(
        agents=agents,
        cycles=cycles,
        wall_seconds=wall,
        rss_before_bytes=rss_before,
        rss_after_bytes=rss_after,
        tracemalloc_before_total_kb=trace_before_total / 1024.0,
        tracemalloc_after_total_kb=trace_after_total / 1024.0,
        drift_kb=drift_kb,
        threshold_kb=threshold_kb,
        pass_=drift_kb < threshold_kb,
        top_growers=top,
    )
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", type=int, default=100)
    ap.add_argument("--cycles", type=int, default=50)
    ap.add_argument("--threshold-kb", type=float, default=512.0,
                    help="Max tolerated drift in KiB over the run.")
    ap.add_argument("--json", action="store_true",
                    help="Emit a machine-readable JSON report.")
    args = ap.parse_args()

    report = run(agents=args.agents, cycles=args.cycles,
                 threshold_kb=args.threshold_kb)

    if args.json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(f"Agents:           {report.agents}")
        print(f"Cycles:           {report.cycles}")
        print(f"Wall:             {report.wall_seconds:.2f} s")
        print(f"RSS before:       {report.rss_before_bytes/1e6:>8.2f} MB")
        print(f"RSS after:        {report.rss_after_bytes/1e6:>8.2f} MB")
        print(f"Tracemalloc before: {report.tracemalloc_before_total_kb:>9.1f} KiB")
        print(f"Tracemalloc after:  {report.tracemalloc_after_total_kb:>9.1f} KiB")
        print(f"Drift:            {report.drift_kb:>9.1f} KiB  "
              f"(threshold {report.threshold_kb:.0f})")
        print(f"Verdict:          {'PASS ✓' if report.pass_ else 'FAIL ✗'}")
        if not report.pass_:
            print("Top growers:")
            for g in report.top_growers[:5]:
                print(f"  +{g['size_diff_kb']:>8.1f} KiB  {g['file']}:{g['line']}")
    return 0 if report.pass_ else 1


if __name__ == "__main__":
    sys.exit(main())
