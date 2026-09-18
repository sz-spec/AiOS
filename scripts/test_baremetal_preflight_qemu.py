#!/usr/bin/env python3
"""Prove that every mandatory CPU feature is rejected before unsafe boot code."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


MANDATORY_QEMU_FEATURES = (
    "fpu", "tsc", "msr", "pae", "pge", "pat", "fxsr", "sse", "sse2",
    "syscall", "nx", "lm",
)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("seconds must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    iso_before = sha256(args.iso)
    results = []
    for feature in MANDATORY_QEMU_FEATURES:
        command = [
            "qemu-system-x86_64", "-machine", "q35,accel=tcg",
            "-cpu", f"max,-{feature}", "-smp", "1", "-m", "1024",
            "-display", "none", "-serial", "stdio", "-monitor", "none",
            "-nic", "none", "-no-reboot", "-cdrom", str(args.iso.resolve()),
        ]
        try:
            run = subprocess.run(command, input=b"", capture_output=True,
                                 timeout=args.seconds)
            status = run.returncode
            raw = run.stdout + run.stderr
        except subprocess.TimeoutExpired as timeout:
            status = "observation_timeout"
            raw = (timeout.stdout or b"") + (timeout.stderr or b"")
        (args.output / f"{feature}.log").write_bytes(raw)
        decoded = raw.decode(errors="replace")
        qemu_error = "Property '" in decoded or "not found" in decoded
        results.append({
            "feature": feature,
            "exit_status": status,
            "cpu_preflight_marker": "CPUF" in decoded,
            "qemu_configuration_error": qemu_error,
            "passed": status == "observation_timeout" and
                      "CPUF" in decoded and not qemu_error,
            "command": command,
        })
    document = {
        "iso_sha256": iso_before,
        "iso_sha256_after": sha256(args.iso),
        "qemu_version": subprocess.check_output(
            ["qemu-system-x86_64", "--version"], text=True
        ).splitlines()[0],
        "results": results,
    }
    document["artifact_unchanged"] = (
        document["iso_sha256"] == document["iso_sha256_after"]
    )
    document["passed"] = document["artifact_unchanged"] and all(
        result["passed"] for result in results
    )
    (args.output / "results.json").write_text(
        json.dumps(document, indent=2) + "\n"
    )
    print(json.dumps(document, indent=2))
    return 0 if document["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
