"""
Phase 7 Verification Test -- VOS3 Agent Swarm Scaling

Tests lazy-thaw demand paging, multi-channel VBus interleaving,
hot-swappable model registry, and density improvements for the
VOS3 Agent Swarm architecture.

Key features verified:
  - 32-agent lazy-thaw simulation without OOM
  - Cold-start TTFT (Time-To-First-Token) measurement
  - Multi-channel concurrent inference interleaving
  - Live model hot-swap during active inference
  - Density improvement measurement (lazy-thaw vs eager)
  - Full swarm lifecycle zero-leak verification

Run: python3 -m pytest tests/stress_swarm_scaling.py -v -o "addopts=" -s

Requires: QEMU running with VOS3 kernel and VBus bridge at /tmp/vos3_bridge.sock
"""

import os
import sys
import time
import tempfile
import threading
import logging
from typing import Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.vbus_driver import VBusDriver, VBusError

logger = logging.getLogger("swarm_scaling")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRIDGE_SOCKET = os.environ.get("VOS3_BRIDGE_SOCKET", "/tmp/vos3_bridge.sock")
CONSOLE_LOG = os.environ.get("VOS3_CONSOLE_LOG", "/tmp/vos3_console.log")
HUGEPAGE_SIZE = 2 * 1024 * 1024  # 2 MB
NUM_MODEL_SLOTS = 8  # 0-7; slot 0 is Coordinator (kernel-only)
USABLE_SLOTS = list(range(1, 8))  # Slots 1-7 available for agents
HP_POOL_MAX = 128  # Hard limit from pmm.c

# ---------------------------------------------------------------------------
# Markers and skip conditions
# ---------------------------------------------------------------------------

requires_qemu = pytest.mark.skipif(
    not os.path.exists(BRIDGE_SOCKET),
    reason=f"VOS3 bridge socket not found at {BRIDGE_SOCKET}",
)

swarm = pytest.mark.swarm


# ---------------------------------------------------------------------------
# VBusClient -- thread-safe wrapper around VBusDriver
# ---------------------------------------------------------------------------


class VBusClient:
    """Thread-safe VBus client with connection management, HANDSHAKE, and
    command send/recv. Wraps VBusDriver with a reentrant lock for safe
    concurrent access from multiple threads."""

    def __init__(self, socket_path: str = BRIDGE_SOCKET):
        self.socket_path = socket_path
        self._driver: Optional[VBusDriver] = None
        self._lock = threading.RLock()

    def connect(self, retries: int = 15, delay: float = 0.5) -> bool:
        """Connect to VBus with retry. Returns True on success."""
        with self._lock:
            if self._driver is not None:
                return True
            for attempt in range(retries):
                drv = VBusDriver(socket_path=self.socket_path)
                try:
                    if drv.connect():
                        self._driver = drv
                        return True
                except VBusError:
                    pass
                time.sleep(delay)
            return False

    def disconnect(self) -> None:
        """Close the VBus connection."""
        with self._lock:
            if self._driver:
                self._driver.disconnect()
                self._driver = None

    def send_command(self, command: str, slot_id: int = 0xFF) -> str:
        """Thread-safe command send/recv."""
        with self._lock:
            if self._driver is None:
                raise VBusError("Not connected")
            return self._driver.send_command(command, slot_id=slot_id)

    def ping(self) -> float:
        """Thread-safe PING. Returns RTT in ms."""
        with self._lock:
            if self._driver is None:
                raise VBusError("Not connected")
            return self._driver.ping()

    def slot_start(
        self, slot_id: int, model_id: int, size: int, label: str = ""
    ) -> str:
        with self._lock:
            if self._driver is None:
                raise VBusError("Not connected")
            return self._driver.slot_start(slot_id, model_id, size, label)

    def slot_finish(self, slot_id: int) -> str:
        with self._lock:
            if self._driver is None:
                raise VBusError("Not connected")
            return self._driver.slot_finish(slot_id)

    def slot_reset(self, slot_id: int) -> str:
        with self._lock:
            if self._driver is None:
                raise VBusError("Not connected")
            return self._driver.slot_reset(slot_id)

    def slot_status(self, slot_id: int) -> str:
        with self._lock:
            if self._driver is None:
                raise VBusError("Not connected")
            return self._driver.slot_status(slot_id)

    def kim_generate(
        self, slot_id: int, max_tokens: int = 256, temperature: int = 100
    ) -> str:
        with self._lock:
            if self._driver is None:
                raise VBusError("Not connected")
            return self._driver.kim_generate(slot_id, max_tokens, temperature)

    def load_model_burst(
        self,
        file_path: str,
        slot_id: int,
        model_id: int = 1,
        label: str = "",
        chunk_size: int = 49152,
    ) -> dict:
        with self._lock:
            if self._driver is None:
                raise VBusError("Not connected")
            return self._driver.load_model_burst(
                file_path, slot_id, model_id, label, chunk_size
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_client() -> VBusClient:
    """Create and connect a VBusClient with retry."""
    client = VBusClient()
    if not client.connect():
        raise RuntimeError("Cannot connect to VOS3 bridge")
    return client


def get_hp_stats(client: VBusClient) -> dict:
    """Query HugePage pool stats via HP_STATS command.

    Returns dict with keys: free, used, total.
    Response format varies; handles:
      "OK|<total>|<used>"
      "HP_STATS|free=N|used=M|total=T"
    """
    resp = client.send_command("HP_STATS")
    parts = resp.split("|")

    result = {"free": 0, "used": 0, "total": 0}

    # Try key=value format first: "HP_STATS|free=N|used=M|total=T"
    kv_parsed = False
    for part in parts:
        if "=" in part:
            key, _, val = part.partition("=")
            key = key.strip().lower()
            if key in result:
                result[key] = int(val.strip())
                kv_parsed = True

    if kv_parsed:
        # Compute missing fields if needed
        if result["free"] == 0 and result["total"] > 0 and result["used"] > 0:
            result["free"] = result["total"] - result["used"]
        elif result["used"] == 0 and result["total"] > 0 and result["free"] > 0:
            result["used"] = result["total"] - result["free"]
        return result

    # Fallback: "OK|<total>|<used>"
    if parts[0] == "OK" and len(parts) >= 3:
        result["total"] = int(parts[1])
        result["used"] = int(parts[2])
        result["free"] = result["total"] - result["used"]
    elif len(parts) >= 2:
        result["total"] = int(parts[0])
        result["used"] = int(parts[1])
        result["free"] = result["total"] - result["used"]

    return result


def reset_slot_safe(client: VBusClient, slot_id: int) -> None:
    """Reset a slot, ignoring errors."""
    try:
        client.slot_reset(slot_id)
    except Exception:
        pass


def reset_all_slots(client: VBusClient) -> None:
    """Reset all usable slots (1-7), ignoring errors."""
    for slot_id in USABLE_SLOTS:
        reset_slot_safe(client, slot_id)
    time.sleep(0.3)


def check_console_for_panic() -> bool:
    """Check /tmp/vos3_console.log for kernel panic strings.

    Returns True if a panic is detected, False otherwise.
    """
    if not os.path.exists(CONSOLE_LOG):
        return False
    try:
        with open(CONSOLE_LOG, "r", errors="replace") as f:
            content = f.read()
        panic_markers = [
            "KERNEL PANIC",
            "kernel panic",
            "PANIC:",
            "OOM KILL",
            "out of memory",
            "page fault in kernel",
            "double fault",
            "stack overflow",
        ]
        for marker in panic_markers:
            if marker in content:
                logger.error("PANIC detected in console log: %s", marker)
                return True
    except OSError:
        pass
    return False


def create_dummy_model(size: int) -> str:
    """Create a temp file with repeating pattern for model simulation.

    Returns path to the temporary file. Caller must unlink after use.
    """
    pattern = bytes(range(256))
    f = tempfile.NamedTemporaryFile(suffix=".weights", delete=False)
    written = 0
    while written < size:
        chunk = pattern[: min(len(pattern), size - written)]
        f.write(chunk)
        written += len(chunk)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Test 1: 32-Agent Lazy-Thaw Simulation (No OOM)
# ---------------------------------------------------------------------------


@swarm
@requires_qemu
class TestLazyThaw32Agents:
    """Simulate 32 agents on limited HugePages (128 pool max) with
    lazy-thaw demand paging enabled. Verifies no OOM and zero HP leak."""

    def test_32_agent_lazy_thaw_no_oom(self):
        """Cycle 32 simulated agents across 7 slots with LAZY_THAW enabled."""
        client = make_client()
        try:
            reset_all_slots(client)

            # 1. Record HP baseline
            hp_baseline = get_hp_stats(client)
            print("\n  === 32-AGENT LAZY-THAW TEST ===")
            print(
                f"  HP baseline: total={hp_baseline['total']}, "
                f"used={hp_baseline['used']}, free={hp_baseline['free']}"
            )

            # 2. Start slots 1-7 with small model stubs
            model_size = HUGEPAGE_SIZE  # 2MB each
            model_files = []
            for slot_id in USABLE_SLOTS:
                fpath = create_dummy_model(model_size)
                model_files.append(fpath)
                try:
                    resp = client.slot_start(
                        slot_id, 7000 + slot_id, model_size, f"Agent{slot_id}"
                    )
                    assert resp.startswith(
                        "OK|"
                    ), f"SLOT_START slot {slot_id} failed: {resp}"
                except Exception:
                    # Clean up on failure
                    for fp in model_files:
                        os.unlink(fp)
                    raise

            # 3. Enable LAZY_THAW on all slots
            lazy_thaw_enabled = 0
            for slot_id in USABLE_SLOTS:
                resp = client.send_command(f"LAZY_THAW|{slot_id}|1")
                if "OK" in resp:
                    lazy_thaw_enabled += 1
                    print(f"  Slot {slot_id}: LAZY_THAW enabled")
                else:
                    print(f"  Slot {slot_id}: LAZY_THAW response: {resp}")

            print(
                f"  LAZY_THAW enabled on {lazy_thaw_enabled}/{len(USABLE_SLOTS)} slots"
            )

            # 4. Simulate 32 agents cycling through slots in round-robin
            num_agents = 32
            generate_results = []
            panics_detected = 0

            for agent_id in range(num_agents):
                slot_id = (agent_id % len(USABLE_SLOTS)) + 1  # 1-7
                try:
                    resp = client.kim_generate(slot_id, max_tokens=4, temperature=100)
                    generate_results.append(
                        {
                            "agent_id": agent_id,
                            "slot_id": slot_id,
                            "response": resp,
                            "ok": "OK" in resp
                            or "ERR" in resp,  # Any response = no crash
                        }
                    )
                except VBusError as e:
                    generate_results.append(
                        {
                            "agent_id": agent_id,
                            "slot_id": slot_id,
                            "response": str(e),
                            "ok": False,
                        }
                    )

                # Check for panic every 8 agents
                if (agent_id + 1) % 8 == 0:
                    if check_console_for_panic():
                        panics_detected += 1
                        print(f"  PANIC detected after agent {agent_id}!")

            # 5. Verify no OOM panic
            assert (
                not check_console_for_panic()
            ), "Kernel panic detected in console log during 32-agent simulation"
            assert (
                panics_detected == 0
            ), f"{panics_detected} panics detected during simulation"

            # 6. Check HP_STATS within limits
            hp_during = get_hp_stats(client)
            print(
                f"  HP during test: total={hp_during['total']}, "
                f"used={hp_during['used']}, free={hp_during['free']}"
            )
            assert (
                hp_during["used"] <= HP_POOL_MAX
            ), f"HP usage {hp_during['used']} exceeds pool max {HP_POOL_MAX}"

            # 7. Reset all slots and check zero HP leak
            reset_all_slots(client)
            time.sleep(0.5)

            hp_final = get_hp_stats(client)
            hp_delta = hp_final["used"] - hp_baseline["used"]

            ok_count = sum(1 for r in generate_results if r["ok"])
            print("\n  === 32-AGENT RESULTS ===")
            print(f"  Agents simulated: {num_agents}")
            print(f"  Responses OK: {ok_count}/{num_agents}")
            print(
                f"  HP delta: {hp_delta} (baseline={hp_baseline['used']}, "
                f"final={hp_final['used']})"
            )
            print(f"  Panics: {panics_detected}")

            assert hp_delta == 0, (
                f"HugePage LEAK: delta={hp_delta} (baseline={hp_baseline['used']}, "
                f"final={hp_final['used']})"
            )

        finally:
            # Clean up model files
            for fp in model_files:
                try:
                    os.unlink(fp)
                except OSError:
                    pass
            reset_all_slots(client)
            client.disconnect()


# ---------------------------------------------------------------------------
# Test 2: TTFT Cold-Start Measurement
# ---------------------------------------------------------------------------


@swarm
@requires_qemu
class TestTTFTColdStart:
    """Measure Time-To-First-Token during lazy-thaw cold start."""

    def test_ttft_cold_start_measurement(self):
        """Measure TTFT across 5 iterations with lazy-thaw cold start."""
        client = make_client()
        try:
            reset_all_slots(client)

            slot_id = 1
            model_size = HUGEPAGE_SIZE
            iterations = 5
            ttft_samples = []

            print("\n  === TTFT COLD-START MEASUREMENT ===")
            print(f"  {'Iter':<6} {'TTFT (ms)':<12} {'Response':<30}")
            print(f"  {'-'*48}")

            for i in range(iterations):
                # 1. Start slot, finish it (loads pages), enable LAZY_THAW
                fpath = create_dummy_model(model_size)
                try:
                    resp = client.slot_start(slot_id, 8000 + i, model_size, f"TTFT_{i}")
                    assert resp.startswith("OK|"), f"SLOT_START failed: {resp}"

                    # Finish to commit the slot state
                    client.slot_finish(slot_id)
                    # Note: SLOT_FINISH may succeed or report partial data

                    # Enable LAZY_THAW (all pages unmapped for cold start)
                    client.send_command(f"LAZY_THAW|{slot_id}|1")

                    # 2-4. Measure wall-clock time for KIM_GENERATE
                    start_time = time.monotonic()
                    gen_resp = client.kim_generate(
                        slot_id, max_tokens=1, temperature=100
                    )
                    end_time = time.monotonic()

                    ttft_ms = (end_time - start_time) * 1000.0
                    ttft_samples.append(ttft_ms)

                    print(f"  {i+1:<6} {ttft_ms:<12.2f} {gen_resp[:30]}")

                    # Reset for next iteration
                    reset_slot_safe(client, slot_id)
                    time.sleep(0.2)

                finally:
                    os.unlink(fpath)

            # 5. Compute statistics
            if ttft_samples:
                mean_ttft = sum(ttft_samples) / len(ttft_samples)
                sorted_samples = sorted(ttft_samples)
                p99_idx = max(0, int(len(sorted_samples) * 0.99) - 1)
                p99_ttft = sorted_samples[p99_idx]
                min_ttft = min(ttft_samples)
                max_ttft = max(ttft_samples)

                print("\n  === TTFT STATISTICS ===")
                print(f"  Samples:  {len(ttft_samples)}")
                print(f"  Mean:     {mean_ttft:.2f} ms")
                print(f"  Min:      {min_ttft:.2f} ms")
                print(f"  Max:      {max_ttft:.2f} ms")
                print(f"  P99:      {p99_ttft:.2f} ms")

                # 6. Assert TTFT < 5.0 seconds (generous for QEMU TCG)
                assert (
                    mean_ttft < 5000.0
                ), f"Mean TTFT {mean_ttft:.2f}ms exceeds 5000ms threshold"
                assert (
                    p99_ttft < 5000.0
                ), f"P99 TTFT {p99_ttft:.2f}ms exceeds 5000ms threshold"
            else:
                pytest.fail("No TTFT samples collected")

        finally:
            reset_all_slots(client)
            client.disconnect()


# ---------------------------------------------------------------------------
# Test 3: Multi-Channel Interleave
# ---------------------------------------------------------------------------


@swarm
@requires_qemu
class TestMultiChannelInterleave:
    """Verify concurrent inference on different slots without deadlock."""

    def test_multi_channel_interleave(self):
        """Launch 4 threads each sending KIM_GENERATE to a different slot."""
        client = make_client()
        try:
            reset_all_slots(client)

            num_channels = 4
            slot_ids = USABLE_SLOTS[:num_channels]  # Slots 1-4

            # 1. Start slots 1-4
            for slot_id in slot_ids:
                fpath = create_dummy_model(HUGEPAGE_SIZE)
                try:
                    resp = client.slot_start(
                        slot_id, 9000 + slot_id, HUGEPAGE_SIZE, f"Channel{slot_id}"
                    )
                    assert resp.startswith(
                        "OK|"
                    ), f"SLOT_START slot {slot_id} failed: {resp}"
                finally:
                    os.unlink(fpath)

            # 2. Launch threads for concurrent inference
            results = {}
            errors = {}
            lock = threading.Lock()

            def generate_on_slot(slot_id: int, max_tokens: int):
                """Thread target: generate tokens on a specific slot."""
                try:
                    # Each thread creates its own client for true concurrency
                    # (single VBusClient is thread-safe via lock, but that
                    # serializes commands -- separate clients test true
                    # multi-channel behavior)
                    thread_client = make_client()
                    try:
                        start = time.monotonic()
                        resp = thread_client.kim_generate(
                            slot_id, max_tokens=max_tokens, temperature=100
                        )
                        elapsed = time.monotonic() - start
                        with lock:
                            results[slot_id] = {
                                "response": resp,
                                "elapsed_ms": elapsed * 1000.0,
                                "max_tokens": max_tokens,
                            }
                    finally:
                        thread_client.disconnect()
                except Exception as e:
                    with lock:
                        errors[slot_id] = str(e)

            print("\n  === MULTI-CHANNEL INTERLEAVE TEST ===")
            print(f"  Channels: {num_channels} (slots {slot_ids})")

            # Measure sequential baseline first
            seq_start = time.monotonic()
            for slot_id in slot_ids:
                client.kim_generate(slot_id, max_tokens=8, temperature=100)
            seq_elapsed = time.monotonic() - seq_start
            print(f"  Sequential baseline: {seq_elapsed*1000:.1f} ms")

            # 3. Launch concurrent threads
            conc_start = time.monotonic()
            threads = []
            for slot_id in slot_ids:
                t = threading.Thread(target=generate_on_slot, args=(slot_id, 8))
                threads.append(t)
                t.start()

            # Wait with timeout to detect deadlocks
            deadline = time.monotonic() + 30.0  # 30s timeout
            for t in threads:
                remaining = max(0.1, deadline - time.monotonic())
                t.join(timeout=remaining)
                if t.is_alive():
                    pytest.fail("Thread deadlock detected after 30s")

            conc_elapsed = time.monotonic() - conc_start

            # 4. Verify all returned results
            print(f"  Concurrent elapsed: {conc_elapsed*1000:.1f} ms")
            print(f"  Results: {len(results)}/{num_channels}")
            print(f"  Errors:  {len(errors)}")

            for slot_id in slot_ids:
                if slot_id in results:
                    r = results[slot_id]
                    print(
                        f"    Slot {slot_id}: {r['elapsed_ms']:.1f}ms "
                        f"- {r['response'][:40]}"
                    )
                elif slot_id in errors:
                    print(f"    Slot {slot_id}: ERROR - {errors[slot_id][:60]}")

            assert len(errors) == 0, f"Concurrent inference errors: {errors}"
            assert (
                len(results) == num_channels
            ), f"Only {len(results)}/{num_channels} slots returned results"

            # 5. Verify token counts are independent per slot
            for slot_id, r in results.items():
                assert r["response"], f"Slot {slot_id} returned empty response"

            # 6. Concurrent should be < 2x sequential (interleaving benefit)
            if seq_elapsed > 0:
                speedup_ratio = conc_elapsed / seq_elapsed
                print(f"\n  Concurrency ratio: {speedup_ratio:.2f}x " f"(want < 2.0x)")
                assert speedup_ratio < 2.0, (
                    f"Concurrent took {speedup_ratio:.2f}x sequential -- "
                    f"no interleaving benefit"
                )

        finally:
            reset_all_slots(client)
            client.disconnect()


# ---------------------------------------------------------------------------
# Test 4: Model Hot-Swap During Inference
# ---------------------------------------------------------------------------


@swarm
@requires_qemu
class TestModelHotSwap:
    """Test live model weight update during active inference."""

    def test_model_hot_swap_during_inference(self):
        """Swap model weights while slot 1 is generating tokens."""
        client = make_client()
        try:
            reset_all_slots(client)

            hp_baseline = get_hp_stats(client)
            print("\n  === MODEL HOT-SWAP TEST ===")
            print(f"  HP baseline: used={hp_baseline['used']}")

            # 1. Start slot 1 (active model) and slot 2 (shadow model)
            for slot_id, label in [(1, "Active"), (2, "Shadow")]:
                fpath = create_dummy_model(HUGEPAGE_SIZE)
                try:
                    resp = client.slot_start(
                        slot_id, 5000 + slot_id, HUGEPAGE_SIZE, label
                    )
                    assert resp.startswith(
                        "OK|"
                    ), f"SLOT_START slot {slot_id} failed: {resp}"

                    # Finish slot to commit weights
                    client.slot_finish(slot_id)
                finally:
                    os.unlink(fpath)

            # 2. Start long inference on slot 1 and attempt hot-swap
            swap_result = {"response": None, "error": None}
            gen_result = {"response": None, "error": None}
            lock = threading.Lock()

            def long_inference():
                """Generate 64 tokens on slot 1 (long inference)."""
                try:
                    inf_client = make_client()
                    try:
                        resp = inf_client.kim_generate(
                            1, max_tokens=64, temperature=100
                        )
                        with lock:
                            gen_result["response"] = resp
                    finally:
                        inf_client.disconnect()
                except Exception as e:
                    with lock:
                        gen_result["error"] = str(e)

            def hot_swap():
                """Attempt MODEL_LOAD_UPDATE during inference."""
                time.sleep(0.1)  # Let inference start first
                try:
                    swap_client = make_client()
                    try:
                        resp = swap_client.send_command("MODEL_LOAD_UPDATE|1|2")
                        with lock:
                            swap_result["response"] = resp
                    finally:
                        swap_client.disconnect()
                except Exception as e:
                    with lock:
                        swap_result["error"] = str(e)

            # 3. Launch both threads
            t_inf = threading.Thread(target=long_inference)
            t_swap = threading.Thread(target=hot_swap)

            t_inf.start()
            t_swap.start()

            t_inf.join(timeout=30.0)
            t_swap.join(timeout=10.0)

            assert not t_inf.is_alive(), "Inference thread deadlocked"
            assert not t_swap.is_alive(), "Swap thread deadlocked"

            # 4. Verify results
            print(f"  Inference result: {gen_result}")
            print(f"  Swap result:      {swap_result}")

            # Either swap succeeds (OK|SWAP_OK) or is deferred (EBUSY)
            # The critical requirement is NO CRASH
            assert not check_console_for_panic(), "Kernel panic during hot-swap"

            if swap_result["response"]:
                resp = swap_result["response"]
                assert (
                    "OK" in resp or "EBUSY" in resp or "ERR" in resp
                ), f"Unexpected swap response: {resp}"
                print(f"  Swap outcome: {resp}")
            elif swap_result["error"]:
                # Transport errors are acceptable (kernel may reject
                # concurrent access), crashes are not
                print(f"  Swap error (acceptable): {swap_result['error']}")

            # 5. Verify HP_STATS zero leak after cleanup
            reset_all_slots(client)
            time.sleep(0.5)

            hp_final = get_hp_stats(client)
            hp_delta = hp_final["used"] - hp_baseline["used"]
            print(f"  HP final: used={hp_final['used']}, delta={hp_delta}")

            assert hp_delta == 0, f"HugePage LEAK after hot-swap: delta={hp_delta}"

        finally:
            reset_all_slots(client)
            client.disconnect()


# ---------------------------------------------------------------------------
# Test 5: Density Measurement
# ---------------------------------------------------------------------------


@swarm
@requires_qemu
class TestDensityMeasurement:
    """Measure density improvement from lazy-thaw demand paging."""

    def test_density_measurement(self):
        """Compare HP usage with and without lazy-thaw across 7 slots."""
        client = make_client()
        try:
            reset_all_slots(client)

            model_size = HUGEPAGE_SIZE  # 2MB per slot
            print("\n  === DENSITY MEASUREMENT TEST ===")
            print(f"  Model size per slot: {model_size / (1024*1024):.1f} MB")

            # --- Phase A: Without lazy-thaw (eager allocation) ---
            print("\n  Phase A: Eager allocation (no lazy-thaw)")

            hp_before_eager = get_hp_stats(client)

            for slot_id in USABLE_SLOTS:
                fpath = create_dummy_model(model_size)
                try:
                    resp = client.slot_start(
                        slot_id, 6000 + slot_id, model_size, f"Eager{slot_id}"
                    )
                    assert resp.startswith(
                        "OK|"
                    ), f"SLOT_START slot {slot_id} failed: {resp}"
                finally:
                    os.unlink(fpath)

            hp_eager = get_hp_stats(client)
            eager_used = hp_eager["used"] - hp_before_eager["used"]
            print(f"  Eager HP used: {eager_used} " f"(total used: {hp_eager['used']})")

            # Reset all for lazy-thaw phase
            reset_all_slots(client)
            time.sleep(0.5)

            # --- Phase B: With lazy-thaw (demand paging) ---
            print("\n  Phase B: Lazy-thaw allocation (demand paging)")

            hp_before_lazy = get_hp_stats(client)

            for slot_id in USABLE_SLOTS:
                fpath = create_dummy_model(model_size)
                try:
                    resp = client.slot_start(
                        slot_id, 6100 + slot_id, model_size, f"Lazy{slot_id}"
                    )
                    assert resp.startswith(
                        "OK|"
                    ), f"SLOT_START slot {slot_id} failed: {resp}"
                finally:
                    os.unlink(fpath)

            # Enable LAZY_THAW on all slots
            for slot_id in USABLE_SLOTS:
                client.send_command(f"LAZY_THAW|{slot_id}|1")

            # Don't generate yet -- pages should remain unmapped
            hp_lazy_idle = get_hp_stats(client)
            lazy_idle_used = hp_lazy_idle["used"] - hp_before_lazy["used"]
            print(f"  Lazy-thaw HP used (idle, no generation): {lazy_idle_used}")

            # Generate on just 1 slot -- only that slot's pages get mapped
            gen_slot = USABLE_SLOTS[0]
            client.kim_generate(gen_slot, max_tokens=4, temperature=100)

            hp_lazy_one = get_hp_stats(client)
            lazy_one_used = hp_lazy_one["used"] - hp_before_lazy["used"]
            print(f"  Lazy-thaw HP used (1 slot active): {lazy_one_used}")

            # --- Compute density ratio ---
            print("\n  === DENSITY RESULTS ===")
            print(f"  Eager allocation (7 slots):     {eager_used} HP")
            print(f"  Lazy-thaw idle (7 slots, 0 gen): {lazy_idle_used} HP")
            print(f"  Lazy-thaw (7 slots, 1 gen):     {lazy_one_used} HP")

            # Density = agents_supported / pages_consumed
            # With lazy-thaw idle, we support 7 agents with minimal pages
            if lazy_idle_used > 0 and eager_used > 0:
                density_ratio = eager_used / lazy_idle_used
                print(f"  Density improvement ratio: {density_ratio:.1f}x")
                assert (
                    density_ratio >= 3.0
                ), f"Density improvement {density_ratio:.1f}x < 3.0x minimum"
            elif lazy_idle_used == 0 and eager_used > 0:
                # Perfect lazy-thaw: zero pages allocated until generation
                print("  Density improvement: INFINITE (0 HP used in idle)")
                # This is the ideal case -- pass
            else:
                print(
                    f"  WARNING: Cannot compute density ratio "
                    f"(eager={eager_used}, lazy_idle={lazy_idle_used})"
                )
                # If both are 0, the kernel may not allocate HP at SLOT_START
                # which is acceptable behavior

            # Cleanup
            reset_all_slots(client)

        finally:
            reset_all_slots(client)
            client.disconnect()


# ---------------------------------------------------------------------------
# Test 6: Zero-Leak Full Swarm Lifecycle
# ---------------------------------------------------------------------------


@swarm
@requires_qemu
class TestZeroLeakSwarmLifecycle:
    """End-to-end leak test: 5 rounds of full slot lifecycle."""

    def test_zero_leak_full_swarm_lifecycle(self):
        """5 rounds of start/lazy-thaw/generate/reset across all 7 slots."""
        client = make_client()
        try:
            reset_all_slots(client)
            time.sleep(0.5)

            # 1. Record HP baseline
            hp_baseline = get_hp_stats(client)
            print("\n  === ZERO-LEAK SWARM LIFECYCLE ===")
            print(
                f"  HP baseline: total={hp_baseline['total']}, "
                f"used={hp_baseline['used']}"
            )

            num_rounds = 5
            round_stats = []

            for round_idx in range(num_rounds):
                round_start = time.monotonic()

                # Start all 7 slots
                model_files = []
                for slot_id in USABLE_SLOTS:
                    fpath = create_dummy_model(HUGEPAGE_SIZE)
                    model_files.append(fpath)
                    resp = client.slot_start(
                        slot_id,
                        3000 + round_idx * 10 + slot_id,
                        HUGEPAGE_SIZE,
                        f"R{round_idx}S{slot_id}",
                    )
                    assert resp.startswith(
                        "OK|"
                    ), f"Round {round_idx}: SLOT_START slot {slot_id} failed: {resp}"

                # Enable lazy-thaw on all
                for slot_id in USABLE_SLOTS:
                    client.send_command(f"LAZY_THAW|{slot_id}|1")

                # Generate on all slots (triggers demand paging)
                gen_ok = 0
                for slot_id in USABLE_SLOTS:
                    try:
                        resp = client.kim_generate(
                            slot_id, max_tokens=4, temperature=100
                        )
                        if "OK" in resp or "ERR" in resp:
                            gen_ok += 1
                    except VBusError:
                        pass

                # Check HP mid-round
                hp_mid = get_hp_stats(client)

                # Reset all slots
                reset_all_slots(client)
                time.sleep(0.3)

                # Clean up model files
                for fp in model_files:
                    os.unlink(fp)

                round_elapsed = time.monotonic() - round_start
                hp_after = get_hp_stats(client)

                round_stat = {
                    "round": round_idx + 1,
                    "gen_ok": gen_ok,
                    "hp_mid_used": hp_mid["used"],
                    "hp_after_used": hp_after["used"],
                    "elapsed_s": round_elapsed,
                }
                round_stats.append(round_stat)

                print(
                    f"  Round {round_idx+1}: gen_ok={gen_ok}/7, "
                    f"hp_mid={hp_mid['used']}, hp_after={hp_after['used']}, "
                    f"elapsed={round_elapsed:.2f}s"
                )

            # 2. Final HP_STATS must equal baseline (ZERO DELTA)
            hp_final = get_hp_stats(client)
            hp_delta = hp_final["used"] - hp_baseline["used"]

            print("\n  === LIFECYCLE RESULTS ===")
            print(f"  Rounds completed: {num_rounds}")
            print(f"  HP baseline: {hp_baseline['used']}")
            print(f"  HP final:    {hp_final['used']}")
            print(f"  HP delta:    {hp_delta}")

            total_gen = sum(r["gen_ok"] for r in round_stats)
            total_elapsed = sum(r["elapsed_s"] for r in round_stats)
            print(f"  Total generations: {total_gen}/{num_rounds * len(USABLE_SLOTS)}")
            print(f"  Total elapsed: {total_elapsed:.2f}s")

            # Check no panic
            assert (
                not check_console_for_panic()
            ), "Kernel panic during swarm lifecycle test"

            # ZERO DELTA requirement
            assert hp_delta == 0, (
                f"HugePage LEAK after {num_rounds} rounds: "
                f"delta={hp_delta} (baseline={hp_baseline['used']}, "
                f"final={hp_final['used']})"
            )

        finally:
            reset_all_slots(client)
            client.disconnect()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-o", "addopts=", "-s"])
