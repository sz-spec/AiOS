# vOS — Performance Baseline

**Owner of this document:** the marketed numbers in the Product
Specification (§2.2 — `228.8 cmd/s, P99 7.6 ms, jitter 0.54 ms`) are
binding only against the configuration captured below. This file plus
`backend/tests/bench_regression.py` is what an auditor reads to verify
the spec figures are reproducible.

## Reference hardware

| Field           | Value |
|-----------------|-------|
| CPU             | Apple M-series (arm64) — host. Kernel runs under QEMU/TCG x86_64. |
| Host OS         | macOS 14.x (Darwin 25.3.0) |
| QEMU version    | qemu-system-x86_64 9.x (Homebrew) |
| Guest CPU model | `-cpu max` |
| Guest memory    | 1024 MiB |
| Guest SMP       | 2 vCPUs |
| Transport       | virtio-serial-pci over Unix socket (`qemu-vbus` make target) |
| Kernel SHA-256  | `1bdab44c304003ebf1702dd2417b52a1aa63b1234f8af603e3e50e0634f37029` (vos3.elf — community profile) |

## Marketed numbers (held in one place)

```python
# backend/tests/bench_regression.py
MARKETED_CMDS_PER_SEC = 228.8
MARKETED_P99_MS       = 7.6
MARKETED_JITTER_MS    = 0.54
```

Editing these constants is the canonical way to update the spec figure.
The PDF `vOS_Product_Specification_Long.pdf` should match them
verbatim; any drift between the PDF and these constants is a regression.

## How to reproduce

### 1. Build the reference kernel

```bash
make -C kernel clean && make -C kernel kernel-linux
shasum -a 256 kernel/build/vos3.elf
# Must match the SHA in the table above. If it doesn't, the
# reproducible-build invariants are broken — fix that BEFORE re-running
# the benchmark.
```

### 2. Boot under QEMU with the VBus socket bridge

```bash
make -C kernel qemu-vbus
# This launches QEMU daemonised with /tmp/vos3_bridge.sock as the
# virtio-serial endpoint. Wait ~3-5 seconds for the kernel banner.
```

### 3. Run the live benchmark

```bash
.venv/bin/python backend/scripts/bench_vbus.py --socket /tmp/vos3_bridge.sock
# Or via pytest (asserts the marketed numbers as a regression gate):
.venv/bin/pytest backend/tests/bench_regression.py::TestVBusPerformance::test_vbus_live_marketed_numbers -v
```

### 4. Stop QEMU

```bash
kill $(cat /tmp/vos3_qemu.pid)
```

## What the synthetic floor measures

`test_vbus_cmd_throughput_synthetic_floor` and
`test_vbus_p99_latency_synthetic_floor` exercise pure-Python
`struct.pack/unpack` of the VBus v2.19 frame header. They run on every
PR, with no QEMU dependency. Their purpose is to catch a Python
interpreter or frame-layout regression — they are **not** a substitute
for the live measurement above.

## CI policy

- Synthetic floors: required gate on every PR (`pr-checks.yml`).
- Live `test_vbus_live_marketed_numbers`: opt-in, runs only when
  `/tmp/vos3_bridge.sock` exists. CI runners that boot QEMU pre-test
  satisfy this; runners that don't get an automatic skip.

If the synthetic floor passes but the live measurement on the reference
hardware drops below the marketed numbers, the **spec must be updated
before the marketed numbers are republished externally**. This file
plus `MARKETED_CMDS_PER_SEC` in `bench_regression.py` is the single
source of truth — drift is a documentation bug, not a feature.
