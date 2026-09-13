# VOS3 Benchmark Suite

Measures 4 key performance indicators for VOS3 and compares against Linux baselines.

## KPIs

| # | KPI | Benchmark Program | What it measures |
|---|-----|--------------------|-----------------|
| 1 | Context Switch Latency | `bench_csw` | Pipe ping-pong round-trip (RDTSC cycles) |
| 2 | Memory Isolation | `bench_isolate` | Red team attacks against AI Memory Guard |
| 3 | Persistence Speed | `bench_persist` | 64KB sequential read/write throughput |
| 4 | Agent Overhead | `bench_overhead` | Fork/schedule 10 parallel AI agents |

## Quick Start

### 1. Build everything

```bash
cd kernel && make clean && make
```

This builds user programs (including benchmarks) and embeds them into the kernel.

### 2. Run benchmarks manually (QEMU)

```bash
cd kernel && make qemu
# In VOS3 shell:
/bin/bench_csw
/bin/bench_isolate
/bin/bench_persist
/bin/bench_overhead
```

### 3. Run automated orchestrator

```bash
cd tests/benchmark
python3 vos3_bench_orchestrator.py --linux-baseline -o results.json --csv results.csv
```

### 4. Run Linux baseline only

```bash
gcc -O2 -o /tmp/linux_bench_csw linux_bench_csw.c
/tmp/linux_bench_csw
```

## Orchestrator Options

```
python3 vos3_bench_orchestrator.py [OPTIONS]

  --linux-baseline    Also run Linux context switch baseline
  --output FILE       JSON output path (default: vos3_bench_results.json)
  --csv FILE          Also export CSV
  --skip-vos3         Skip VOS3 benchmarks (Linux baseline only)
```

## Output Format

### JSON

```json
{
  "timestamp": "2026-02-25T12:00:00",
  "vos3_version": "3.1.0",
  "platform": "Darwin 25.3.0 arm64",
  "benchmarks": [
    {
      "name": "bench_csw",
      "status": "pass",
      "metrics": {
        "kernel_avg_cycles": 1234,
        "kernel_min_cycles": 800,
        "kernel_max_cycles": 5000,
        "tsc_mhz": 2000
      },
      "duration_sec": 5.2
    }
  ],
  "linux_baseline": {
    "roundtrip_avg_cycles": 6000,
    "perswitch_avg_cycles": 3000
  }
}
```

### CSV

One row per metric, suitable for plotting:

```csv
benchmark,metric,value,status,source
bench_csw,kernel_avg_cycles,1234,pass,vos3
linux_baseline,perswitch_avg_cycles,3000,pass,linux
```

## Architecture

```
tests/benchmark/
  vos3_bench_orchestrator.py   # Main orchestrator (starts QEMU, collects results)
  linux_bench_csw.c            # Linux baseline (compile with gcc)
  README.md                    # This file

user/src/
  bench_csw.c                  # KPI 1: Context switch latency
  bench_isolate.c              # KPI 2: Memory isolation
  bench_persist.c              # KPI 3: Persistence speed
  bench_overhead.c             # KPI 4: Agent overhead

kernel/
  include/vos/bench.h          # Benchmark data structures & API
  src/bench/bench_hooks.c      # RDTSC instrumentation in scheduler
```

## Kernel Instrumentation

The scheduler (`scheduler.c`) has RDTSC hooks around every context switch:

```c
vos3_bench_csw_start(prev->tid, next->tid);   // Record TSC before switch
vos3_context_switch(...);                       // Actual switch
vos3_bench_csw_end();                          // Record TSC after (on new task)
```

Data is exposed via `SYS_BENCH_READ` (syscall 222), which returns either
raw samples or summary statistics (min/max/avg/count).
