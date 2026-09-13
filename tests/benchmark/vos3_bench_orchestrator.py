#!/usr/bin/env python3
"""
VOS3 Benchmark Orchestrator

Launches QEMU with VOS3, runs benchmark programs via serial bridge,
collects results, optionally runs Linux baselines, and exports
results to JSON/CSV.

Usage:
    python3 vos3_bench_orchestrator.py [--linux-baseline] [--output results.json]

Requirements:
    - QEMU installed (qemu-system-x86_64)
    - VOS3 kernel built (kernel/build/vos3.elf)
    - gcc for Linux baseline compilation

Author: VOS3 Project
Date: 2026-02-25
"""

import argparse
import csv
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


# ============================================================================
# CONFIGURATION
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
KERNEL_ELF = PROJECT_ROOT / "kernel" / "build" / "vos3.elf"
DISK_IMG = PROJECT_ROOT / "kernel" / "disk.img"
CONSOLE_LOG = Path("/tmp/vos3_bench_console.log")
BRIDGE_SOCK = Path("/tmp/vos3_bench_bridge.sock")
QEMU_PID_FILE = Path("/tmp/vos3_bench_qemu.pid")
LINUX_BENCH_SRC = Path(__file__).parent / "linux_bench_csw.c"
LINUX_BENCH_BIN = Path("/tmp/linux_bench_csw")

QEMU_BIN = "qemu-system-x86_64"
BOOT_WAIT = 8       # Seconds to wait for VOS3 to boot
BENCH_TIMEOUT = 60  # Max seconds per benchmark
BRIDGE_DELAY = 1    # Seconds after connecting bridge socket


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class BenchResult:
    """Result from a single benchmark run."""
    name: str
    status: str = "not_run"  # "pass", "fail", "error", "not_run"
    metrics: dict = field(default_factory=dict)
    raw_output: str = ""
    duration_sec: float = 0.0


@dataclass
class BenchReport:
    """Complete benchmark report."""
    timestamp: str = ""
    vos3_version: str = "3.1.0"
    platform: str = ""
    benchmarks: list = field(default_factory=list)
    linux_baseline: Optional[dict] = None


# ============================================================================
# QEMU MANAGEMENT
# ============================================================================

def start_qemu() -> Optional[int]:
    """Start QEMU with VOS3 kernel in background."""
    if not KERNEL_ELF.exists():
        print(f"[ERROR] Kernel not found: {KERNEL_ELF}")
        print("  Run: cd kernel && make")
        return None

    # Clean up stale state
    for f in [CONSOLE_LOG, BRIDGE_SOCK, QEMU_PID_FILE]:
        f.unlink(missing_ok=True)

    # Build QEMU command
    cmd = [
        QEMU_BIN,
        "-kernel", str(KERNEL_ELF),
        "-m", "512M",
        "-chardev", f"file,id=con,path={CONSOLE_LOG}",
        "-serial", "chardev:con",
        "-chardev", f"socket,id=bridge,path={BRIDGE_SOCK},server=on,wait=off",
        "-serial", "chardev:bridge",
        "-display", "none",
        "-daemonize",
        "-pidfile", str(QEMU_PID_FILE),
    ]

    # Add disk image if available
    if DISK_IMG.exists():
        cmd.extend([
            "-drive", f"file={DISK_IMG},format=raw,if=none,id=disk0",
            "-device", "virtio-blk-pci,drive=disk0",
        ])

    print(f"[QEMU] Starting: {' '.join(cmd[:6])}...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[ERROR] QEMU failed to start: {result.stderr}")
        return None

    # Read PID
    time.sleep(1)
    if QEMU_PID_FILE.exists():
        pid = int(QEMU_PID_FILE.read_text().strip())
        print(f"[QEMU] Running (PID={pid})")
        return pid
    else:
        print("[ERROR] QEMU PID file not found")
        return None


def stop_qemu(pid: int):
    """Stop QEMU process."""
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    print(f"[QEMU] Stopped (PID={pid})")


# ============================================================================
# SERIAL BRIDGE
# ============================================================================

def connect_bridge() -> Optional[socket.socket]:
    """Connect to VOS3 serial bridge socket."""
    if not BRIDGE_SOCK.exists():
        print("[ERROR] Bridge socket not found")
        return None

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(BRIDGE_SOCK))
        sock.settimeout(BENCH_TIMEOUT)
        time.sleep(BRIDGE_DELAY)
        print("[BRIDGE] Connected")
        return sock
    except (ConnectionRefusedError, FileNotFoundError) as e:
        print(f"[ERROR] Bridge connection failed: {e}")
        return None


def send_command(sock: socket.socket, cmd: str) -> str:
    """Send command via bridge and collect output."""
    # Drain any pending data
    sock.settimeout(0.5)
    try:
        while True:
            sock.recv(4096)
    except (socket.timeout, BlockingIOError):
        pass

    sock.settimeout(BENCH_TIMEOUT)

    # Send command
    sock.sendall((cmd + "\n").encode())
    time.sleep(0.5)

    # Collect output until we see the prompt or timeout
    output = b""
    deadline = time.time() + BENCH_TIMEOUT

    while time.time() < deadline:
        try:
            data = sock.recv(4096)
            if not data:
                break
            output += data
            text = output.decode("utf-8", errors="replace")
            # Look for shell prompt or "Benchmark complete" or similar markers
            if "Benchmark complete" in text or "complete." in text:
                time.sleep(1)  # Collect remaining output
                try:
                    sock.settimeout(1)
                    output += sock.recv(4096)
                except socket.timeout:
                    pass
                break
            if text.endswith("$ ") or text.endswith("# "):
                break
        except socket.timeout:
            break

    return output.decode("utf-8", errors="replace")


# ============================================================================
# RESULT PARSING
# ============================================================================

def parse_csw_results(output: str) -> dict:
    """Parse bench_csw output for key metrics."""
    metrics = {}

    patterns = {
        "user_roundtrip_avg_cycles": r"Round-trip avg:\s+(\d+)\s+cycles",
        "user_perswitch_avg_cycles": r"Per-switch avg:\s+~(\d+)\s+cycles",
        "kernel_csw_count": r"Total switches:\s+(\d+)",
        "kernel_min_cycles": r"Min latency:\s+(\d+)\s+cycles",
        "kernel_max_cycles": r"Max latency:\s+(\d+)\s+cycles",
        "kernel_avg_cycles": r"Avg latency:\s+(\d+)\s+cycles",
        "kernel_avg_us": r"Avg latency:\s+~(\d+)\s+us",
        "tsc_mhz": r"TSC frequency:\s+~(\d+)\s+MHz",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        if match:
            metrics[key] = int(match.group(1))

    return metrics


def parse_isolate_results(output: str) -> dict:
    """Parse bench_isolate output."""
    metrics = {}

    passed = len(re.findall(r"\[PASS\]", output))
    failed = len(re.findall(r"\[FAIL\]", output))
    metrics["tests_passed"] = passed
    metrics["tests_failed"] = failed
    metrics["isolation_verified"] = failed == 0

    # Extract validation overhead
    match = re.search(r"getpid\(\) avg:\s+(\d+)\s+cycles", output)
    if match:
        metrics["getpid_cycles"] = int(match.group(1))

    match = re.search(r"read\(\) avg:\s+(\d+)\s+cycles", output)
    if match:
        metrics["read_cycles"] = int(match.group(1))

    match = re.search(r"Overhead delta:\s+~(\d+)\s+cycles", output)
    if match:
        metrics["validation_overhead_cycles"] = int(match.group(1))

    return metrics


def parse_persist_results(output: str) -> dict:
    """Parse bench_persist output."""
    metrics = {}

    for kind in ["Write", "Read"]:
        match = re.search(
            rf"\[RESULTS\] {kind} 64KB.*?Min:\s+(\d+).*?Max:\s+(\d+).*?Average:\s+(\d+)",
            output, re.DOTALL
        )
        if match:
            metrics[f"{kind.lower()}_min_cycles"] = int(match.group(1))
            metrics[f"{kind.lower()}_max_cycles"] = int(match.group(2))
            metrics[f"{kind.lower()}_avg_cycles"] = int(match.group(3))

    return metrics


def parse_overhead_results(output: str) -> dict:
    """Parse bench_overhead output."""
    metrics = {}

    match = re.search(r"Forked (\d+) agents", output)
    if match:
        metrics["agents_forked"] = int(match.group(1))

    match = re.search(r"Avg fork time:\s+(\d+)\s+cycles", output)
    if match:
        metrics["avg_fork_cycles"] = int(match.group(1))

    match = re.search(r"Total time:\s+(\d+)\s+cycles", output)
    if match:
        metrics["total_cycles"] = int(match.group(1))

    match = re.search(r"Context switches:\s+(\d+)", output)
    if match:
        metrics["context_switches"] = int(match.group(1))

    match = re.search(r"Completed:\s+(\d+)\s+OK,\s+(\d+)\s+failed", output)
    if match:
        metrics["agents_ok"] = int(match.group(1))
        metrics["agents_failed"] = int(match.group(2))

    match = re.search(r"Success rate:\s+(\d+)%", output)
    if match:
        metrics["success_rate_pct"] = int(match.group(1))

    return metrics


def parse_linux_baseline(output: str) -> dict:
    """Parse Linux baseline output."""
    metrics = {}

    patterns = {
        "roundtrip_min_cycles": r"Round-trip min:\s+(\d+)\s+cycles",
        "roundtrip_max_cycles": r"Round-trip max:\s+(\d+)\s+cycles",
        "roundtrip_avg_cycles": r"Round-trip avg:\s+(\d+)\s+cycles",
        "perswitch_avg_cycles": r"Per-switch avg:\s+~(\d+)\s+cycles",
        "roundtrip_avg_us": r"Round-trip avg:\s+~(\d+)\s+us",
        "perswitch_avg_us": r"Per-switch avg:\s+~(\d+)\s+us",
        "timer_mhz": r"Timer frequency:\s+~(\d+)\s+MHz",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        if match:
            metrics[key] = int(match.group(1))

    return metrics


# ============================================================================
# LINUX BASELINE
# ============================================================================

def run_linux_baseline() -> Optional[dict]:
    """Compile and run Linux context switch baseline."""
    if not LINUX_BENCH_SRC.exists():
        print("[LINUX] Baseline source not found, skipping")
        return None

    print("[LINUX] Compiling baseline...")
    result = subprocess.run(
        ["gcc", "-O2", "-o", str(LINUX_BENCH_BIN), str(LINUX_BENCH_SRC)],
        capture_output=True, text=True
    )

    if result.returncode != 0:
        print(f"[LINUX] Compilation failed: {result.stderr}")
        return None

    print("[LINUX] Running baseline...")
    result = subprocess.run(
        [str(LINUX_BENCH_BIN)],
        capture_output=True, text=True, timeout=30
    )

    if result.returncode != 0:
        print(f"[LINUX] Baseline failed: {result.stderr}")
        return None

    print(result.stdout)
    return parse_linux_baseline(result.stdout)


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

def run_vos3_benchmarks() -> list:
    """Run all VOS3 benchmarks via QEMU."""
    results = []

    # Start QEMU
    pid = start_qemu()
    if pid is None:
        return results

    print(f"[BOOT] Waiting {BOOT_WAIT}s for VOS3 to boot...")
    time.sleep(BOOT_WAIT)

    # Connect bridge
    sock = connect_bridge()
    if sock is None:
        stop_qemu(pid)
        return results

    # Run each benchmark
    benchmarks = [
        ("bench_csw", "Context Switch Latency", parse_csw_results),
        ("bench_isolate", "Memory Isolation Red Team", parse_isolate_results),
        ("bench_persist", "Persistence Access Speed", parse_persist_results),
        ("bench_overhead", "Host Resource Overhead", parse_overhead_results),
    ]

    for bench_name, description, parser in benchmarks:
        print(f"\n{'='*50}")
        print(f"[BENCH] Running: {description}")
        print(f"{'='*50}")

        result = BenchResult(name=bench_name)
        start_time = time.time()

        try:
            output = send_command(sock, f"/bin/{bench_name}")
            result.raw_output = output
            result.duration_sec = time.time() - start_time
            result.metrics = parser(output)
            result.status = "pass" if result.metrics else "error"

            print(f"[RESULT] {bench_name}: {result.status}")
            for k, v in result.metrics.items():
                print(f"  {k}: {v}")

        except Exception as e:
            result.status = "error"
            result.raw_output = str(e)
            result.duration_sec = time.time() - start_time
            print(f"[ERROR] {bench_name}: {e}")

        results.append(result)

    # Cleanup
    sock.close()
    stop_qemu(pid)

    return results


def export_json(report: BenchReport, path: str):
    """Export report as JSON."""
    data = asdict(report)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"[EXPORT] JSON: {path}")


def export_csv(report: BenchReport, path: str):
    """Export report as CSV (one row per metric)."""
    rows = []
    for bench in report.benchmarks:
        for key, value in bench.get("metrics", {}).items():
            rows.append({
                "benchmark": bench["name"],
                "metric": key,
                "value": value,
                "status": bench["status"],
                "source": "vos3",
            })

    if report.linux_baseline:
        for key, value in report.linux_baseline.items():
            rows.append({
                "benchmark": "linux_baseline",
                "metric": key,
                "value": value,
                "status": "pass",
                "source": "linux",
            })

    with open(path, "w", newline="") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    print(f"[EXPORT] CSV: {path}")


def main():
    parser = argparse.ArgumentParser(description="VOS3 Benchmark Orchestrator")
    parser.add_argument("--linux-baseline", action="store_true",
                        help="Also run Linux context switch baseline")
    parser.add_argument("--output", "-o", default="vos3_bench_results.json",
                        help="Output JSON file path")
    parser.add_argument("--csv", default=None,
                        help="Also export CSV to this path")
    parser.add_argument("--skip-vos3", action="store_true",
                        help="Skip VOS3 benchmarks (Linux baseline only)")
    args = parser.parse_args()

    print("=" * 60)
    print("  VOS3 Benchmark Orchestrator")
    print("=" * 60)
    print()

    report = BenchReport(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        platform=subprocess.run(
            ["uname", "-srm"], capture_output=True, text=True
        ).stdout.strip(),
    )

    # Run VOS3 benchmarks
    if not args.skip_vos3:
        bench_results = run_vos3_benchmarks()
        report.benchmarks = [asdict(r) for r in bench_results]

    # Run Linux baseline
    if args.linux_baseline:
        print(f"\n{'='*60}")
        print("  Linux Baseline")
        print(f"{'='*60}")
        report.linux_baseline = run_linux_baseline()

    # Export results
    export_json(report, args.output)
    if args.csv:
        export_csv(report, args.csv)

    # Summary
    print(f"\n{'='*60}")
    print("  Summary")
    print(f"{'='*60}")
    passed = sum(1 for b in report.benchmarks if b.get("status") == "pass")
    total = len(report.benchmarks)
    print(f"  VOS3 Benchmarks: {passed}/{total} passed")
    if report.linux_baseline:
        print(f"  Linux Baseline:  {len(report.linux_baseline)} metrics collected")
    print(f"  Results:         {args.output}")
    print()


if __name__ == "__main__":
    main()
