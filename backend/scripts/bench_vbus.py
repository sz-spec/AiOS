#!/usr/bin/env python3
"""
VBus Throughput Benchmark — Phase 4.1

Measures VBus binary transport performance vs legacy serial baseline.

Usage:
    python3 scripts/bench_vbus.py [--socket /tmp/vos3_bridge.sock]

Note: VBusDriver is lazy-imported inside run()/main() so this module can
be imported (`from scripts.bench_vbus import run`) on Python versions
where vbus_driver.py uses newer syntax than the importer's interpreter.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time

sys.path.insert(0, ".")


def bench_ping(driver, iterations: int = 100) -> dict:
    """Measure PING round-trip latency."""
    from services.vbus_driver import VBusError

    latencies = []
    for _ in range(iterations):
        try:
            ms = driver.ping()
            latencies.append(ms)
        except VBusError as e:
            print(f"  PING error: {e}")
            break

    if not latencies:
        return {"error": "no successful pings"}

    return {
        "iterations": len(latencies),
        "min_ms": min(latencies),
        "mean_ms": statistics.mean(latencies),
        "p99_ms": sorted(latencies)[int(len(latencies) * 0.99)],
        "max_ms": max(latencies),
    }


def bench_write(driver, payload_size: int, iterations: int = 50) -> dict:
    """Measure WRITE command throughput at a given payload size."""
    from services.vbus_driver import VBusError

    # Generate hex payload (each byte -> 2 hex chars)
    data = bytes(range(256)) * (payload_size // 256 + 1)
    data = data[:payload_size]
    hex_data = data.hex()

    path = "/tmp/bench_vbus_test"
    cmd = f"WRITE|{path}|{hex_data}"

    total_bytes = 0
    start = time.monotonic()

    for _ in range(iterations):
        try:
            resp = driver.send_command(cmd)
            if resp.startswith("OK"):
                total_bytes += payload_size
        except VBusError as e:
            print(f"  WRITE error at {payload_size}B: {e}")
            break

    elapsed = time.monotonic() - start
    throughput_kbps = (total_bytes / 1024.0) / elapsed if elapsed > 0 else 0

    return {
        "payload_bytes": payload_size,
        "iterations": iterations,
        "total_bytes": total_bytes,
        "elapsed_s": round(elapsed, 3),
        "throughput_kbps": round(throughput_kbps, 1),
    }


def bench_ping_burst(driver, count: int = 1000) -> dict:
    """Send 'count' synchronous PINGs as fast as possible.

    Tests zero-drop under sustained load. Success = sent == received.
    VBus uses single-descriptor virtio-serial, so each PING must be
    acknowledged before the next is sent.
    """
    from services.vbus_driver import VBusError

    sent = 0
    received = 0
    errors = 0

    for _ in range(count):
        try:
            ms = driver.ping()
            sent += 1
            if ms >= 0:
                received += 1
        except VBusError:
            sent += 1
            errors += 1

    return {
        "sent": sent,
        "received": received,
        "dropped": sent - received,
        "errors": errors,
    }


def bench_large_transfer(driver, total_mb: int = 20, chunk_kb: int = 8) -> dict:
    """Transfer total_mb of data in chunk_kb WRITE commands.

    Measures sustained throughput and checks for errors.
    """
    from services.vbus_driver import VBusError

    chunk_bytes = chunk_kb * 1024
    total_bytes_target = total_mb * 1024 * 1024
    iterations = total_bytes_target // chunk_bytes

    # Pre-build the hex payload
    data = bytes(range(256)) * (chunk_bytes // 256 + 1)
    data = data[:chunk_bytes]
    hex_data = data.hex()

    path = "/tmp/bench_vbus_bigfile"
    # RAMFS max file size is 1MB — rotate with WRITE every 1024 x 1KB chunks
    chunks_per_file = (1024 * 1024) // chunk_bytes  # 1MB per file
    write_cmd = f"WRITE|{path}|{hex_data}"
    append_cmd = f"APPEND|{path}|{hex_data}"

    total_written = 0
    ok_count = 0
    err_count = 0
    busy_count = 0

    start = time.monotonic()

    for i in range(iterations):
        cmd = write_cmd if (i % chunks_per_file == 0) else append_cmd
        try:
            resp = driver.send_command(cmd)
            if resp.startswith("OK"):
                total_written += chunk_bytes
                ok_count += 1
            elif "BUSY" in resp:
                busy_count += 1
                time.sleep(0.01)  # Backoff
            else:
                err_count += 1
        except VBusError:
            err_count += 1
            break

    elapsed = time.monotonic() - start
    throughput_mbps = (total_written / (1024 * 1024)) / elapsed if elapsed > 0 else 0

    return {
        "total_mb": round(total_written / (1024 * 1024), 2),
        "target_mb": total_mb,
        "chunks": ok_count,
        "errors": err_count,
        "busy": busy_count,
        "elapsed_s": round(elapsed, 2),
        "throughput_mbps": round(throughput_mbps, 2),
    }


def bench_congestion(driver) -> dict:
    """Rapid-fire WRITE commands to trigger congestion (BUSY) response.

    Verifies bridge returns ERR|16|BUSY when overloaded, not a crash.
    """
    from services.vbus_driver import VBusError

    data = (b"\xab" * 512).hex()  # 512B payload to avoid virtio-serial fragmentation
    path = "/tmp/bench_cong"
    cmd = f"WRITE|{path}|{data}"

    sent = 0
    ok = 0
    busy = 0
    errors = 0
    recovered = False

    # Spam writes to trigger congestion
    for _ in range(500):
        try:
            resp = driver.send_command(cmd)
            sent += 1
            if resp.startswith("OK"):
                ok += 1
            elif "BUSY" in resp:
                busy += 1
        except VBusError:
            errors += 1
            break

    # Verify recovery: after a short pause, can we still PING?
    time.sleep(0.5)
    try:
        ms = driver.ping()
        recovered = ms > 0
    except VBusError:
        recovered = False

    return {
        "sent": sent,
        "ok": ok,
        "busy": busy,
        "errors": errors,
        "recovered": recovered,
    }


def run(
    socket_path: str = "/tmp/vos3_bridge.sock",
    ping_iterations: int = 100,
    write_iterations: int = 50,
) -> dict:
    """Programmatic entry point — used by bench_regression.py.

    Returns a results dict matching the spec's marketed VBus numbers:

        {
            "available": bool,           # False if no QEMU/socket
            "cmds_per_sec": float | None,
            "p99_ms":       float | None,
            "jitter_ms":    float | None,
            "raw":          { ... full per-test results ... }
        }

    `available=False` when the VBus Unix socket is missing — caller can
    skip the live regression in that case.
    """
    import os as _os
    import statistics as _stats

    if not _os.path.exists(socket_path):
        return {
            "available": False,
            "cmds_per_sec": None,
            "p99_ms": None,
            "jitter_ms": None,
            "raw": {},
        }

    try:
        from services.vbus_driver import VBusDriver, VBusError
    except (ImportError, TypeError) as exc:
        return {
            "available": False,
            "cmds_per_sec": None,
            "p99_ms": None,
            "jitter_ms": None,
            "raw": {"import_error": str(exc)},
        }

    driver = VBusDriver(socket_path)
    if not driver.connect():
        return {
            "available": False,
            "cmds_per_sec": None,
            "p99_ms": None,
            "jitter_ms": None,
            "raw": {},
        }

    try:
        # PING for latency + jitter.
        latencies = []
        for _ in range(ping_iterations):
            try:
                ms = driver.ping()
                if ms >= 0:
                    latencies.append(ms)
            except VBusError:
                pass
        # WRITE-style command-rate measurement: small payload, many iters.
        data = bytes(range(256))
        hex_data = data.hex()
        cmd = f"WRITE|/tmp/bench_vbus_rate|{hex_data}"
        n = max(write_iterations, 200)
        t0 = time.monotonic()
        ok = 0
        for _ in range(n):
            try:
                resp = driver.send_command(cmd)
                if resp.startswith("OK"):
                    ok += 1
            except VBusError:
                pass
        elapsed = time.monotonic() - t0
        cmds_per_sec = ok / elapsed if elapsed > 0 else 0.0

        if latencies:
            p99 = sorted(latencies)[int(len(latencies) * 0.99)]
            jitter = _stats.pstdev(latencies)
        else:
            p99 = None
            jitter = None

        return {
            "available": True,
            "cmds_per_sec": cmds_per_sec,
            "p99_ms": p99,
            "jitter_ms": jitter,
            "raw": {
                "ping_count": len(latencies),
                "write_ok": ok,
                "write_total": n,
                "elapsed_s": elapsed,
            },
        }
    finally:
        driver.disconnect()


def main():
    from services.vbus_driver import VBusDriver, VBusError, VBUS_TYPE_PING  # noqa: F401

    parser = argparse.ArgumentParser(description="VBus throughput benchmark")
    parser.add_argument(
        "--socket",
        default="/tmp/vos3_bridge.sock",
        help="Path to VBus/bridge Unix socket",
    )
    parser.add_argument(
        "--stress",
        action="store_true",
        help="Include stress tests (burst PING, large transfer, congestion)",
    )
    args = parser.parse_args()

    driver = VBusDriver(args.socket)

    print(f"Connecting to {args.socket}...")
    if not driver.connect():
        print("ERROR: Failed to connect via VBus. Is QEMU running with qemu-vbus?")
        sys.exit(1)

    print("VBus connected. Running benchmarks...\n")

    # --- PING latency ---
    print("=== PING Latency (100 iterations) ===")
    ping_results = bench_ping(driver, 100)
    if "error" not in ping_results:
        print(f"  Min:  {ping_results['min_ms']:.2f} ms")
        print(f"  Mean: {ping_results['mean_ms']:.2f} ms")
        print(f"  P99:  {ping_results['p99_ms']:.2f} ms")
        print(f"  Max:  {ping_results['max_ms']:.2f} ms")
    else:
        print(f"  {ping_results['error']}")

    # --- WRITE throughput ---
    print("\n=== WRITE Throughput ===")
    print(f"  {'Payload':>8s}  {'Iters':>5s}  {'Elapsed':>8s}  {'Throughput':>12s}")
    print(f"  {'--------':>8s}  {'-----':>5s}  {'--------':>8s}  {'----------':>12s}")

    # Note: Payloads >1KB hit virtio-serial stream fragmentation; cap at 1KB for reliable measurement
    for size in [256, 512, 1024]:
        try:
            result = bench_write(driver, size, 50)
            print(
                f"  {result['payload_bytes']:>7d}B  {result['iterations']:>5d}"
                f"  {result['elapsed_s']:>7.3f}s  {result['throughput_kbps']:>10.1f} KB/s"
            )
        except VBusError as e:
            print(f"  {size:>7d}B  ERROR: {e}")
            # Reconnect for next test
            driver.disconnect()
            driver.connect()

    # --- Stress tests ---
    if args.stress:
        print("\n=== STRESS: Burst PING (1000 rapid-fire) ===")
        burst = bench_ping_burst(driver, 1000)
        print(f"  Sent:     {burst['sent']}")
        print(f"  Received: {burst['received']}")
        print(f"  Dropped:  {burst['dropped']}")
        status = "PASS" if burst["dropped"] == 0 else "FAIL"
        print(f"  Result:   {status}")

        print("\n=== STRESS: Large Transfer (10MB in 1KB chunks) ===")
        try:
            transfer = bench_large_transfer(driver, total_mb=10, chunk_kb=1)
            print(
                f"  Written:    {transfer['total_mb']} MB / {transfer['target_mb']} MB"
            )
            print(f"  Chunks OK:  {transfer['chunks']}")
            print(f"  Errors:     {transfer['errors']}")
            print(f"  BUSY:       {transfer['busy']}")
            print(f"  Elapsed:    {transfer['elapsed_s']}s")
            print(f"  Throughput: {transfer['throughput_mbps']} MB/s")
            status = "PASS" if transfer["errors"] == 0 else "FAIL"
            print(f"  Result:     {status}")
        except (VBusError, Exception) as e:
            print(f"  ERROR: {e}")
            driver.disconnect()
            driver.connect()

        print("\n=== STRESS: Congestion Detection ===")
        try:
            cong = bench_congestion(driver)
            print(f"  Sent:      {cong['sent']}")
            print(f"  OK:        {cong['ok']}")
            print(f"  BUSY:      {cong['busy']}")
            print(f"  Errors:    {cong['errors']}")
            print(f"  Recovered: {cong['recovered']}")
            status = "PASS" if cong["recovered"] else "FAIL"
            print(f"  Result:    {status}")
        except (VBusError, Exception) as e:
            print(f"  ERROR: {e}")
            driver.disconnect()
            driver.connect()

    # --- Comparison ---
    print("\n=== Transport Comparison ===")
    print("  Legacy Serial (COM2):  ~5.7 KB/s (115200 baud, hex-encoded)")
    if "error" not in ping_results:
        print(f"  VBus (virtio-serial):  PING {ping_results['mean_ms']:.2f}ms mean")
    print("  Expected improvement:  ~18,000x (theoretical)")

    driver.disconnect()
    print("\nBenchmark complete.")


if __name__ == "__main__":
    main()
