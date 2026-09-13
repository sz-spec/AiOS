# Risk Score → Adaptive rlimits

**Anchor:** `aeb3736` · **Phase:** P4.3 · **Modules:** `backend/services/risk_score.py`, `backend/services/app_sandbox.py`

## Purpose

The Hardware Manifest from P3.2 tells us **what the host can prove**.
P4.3 turns that into **what the sandbox will allow**.

Two layers, kept distinct:

1. **Risk Score (informational)** — a 30..100 number derived by
   `services.hardware_manifest.compute_risk_score`. Surfaced in the
   AAA cert report so the operator can see at a glance how the host
   was graded. Does **not** drive runtime decisions on its own.

2. **rlimit tier (load-bearing)** — picked by manifest `.mode`:
   `PROTECTED` → baseline; `RESTRICTED_LEGACY` or `UNKNOWN` →
   tightened. This is what `app_sandbox._sandbox_preexec` and
   `_app_runner_preexec` actually feed to `resource.setrlimit`.

The split is deliberate. A numeric score is easy to misread (is 65
ok? is 75? operators disagree); a binary tier decision is auditable.
We publish the score and we enforce the tier.

## Score formula

```
score = 100
  - 20  if mode != PROTECTED
  - 10  if no SHA-NI                 (proxy for AES-NI generation)
  - 10  if mode != PROTECTED         (proxy for RDRAND availability)
  - 10  if microcode_below_baseline  (placeholder, 0 until P4.2)
  -  5  if pqc_backend == "pure_python"
  -  5  if kpti_mode == "LEGACY_KAISER"
floor:    30
```

Worked examples (sourced from `tests/hardware/test_universal_manifest.py`
+ `tests/sandbox/test_adaptive_rlimits.py`):

| Host | mode | sha_ni | kpti_mode | pqc | Score |
|---|---|---|---|---|---|
| Modern AVX-512 host w/ oqs | PROTECTED | yes | PROTECTED_FULL | oqs | 100 |
| Modern host w/o oqs | PROTECTED | yes | PROTECTED_FULL | pure_python | 95 |
| Nehalem in QEMU | RESTRICTED_LEGACY | no | LEGACY_KAISER | pure_python | 50 |
| Empty klog (boot pre-init) | UNKNOWN | no | — | pure_python | 55 |

## Tier table

| Manifest `.mode` | `RLIMIT_AS` | `RLIMIT_CPU` (soft, hard) | `RLIMIT_NPROC` | `RLIMIT_NOFILE` | `RLIMIT_FSIZE` |
|---|---|---|---|---|---|
| **PROTECTED** | `base_memory_mb · 1MB` | `(60, 120)` | `(4, 4)` | `(64, 64)` | `(10MB, 10MB)` |
| **RESTRICTED_LEGACY** | `(base_memory_mb // 2) · 1MB` ≥ 32MB | `(30, 60)` ≥ 5s soft | `(2, 2)` | `(64, 64)` | `(10MB, 10MB)` |
| **UNKNOWN** | same as RESTRICTED_LEGACY (Security > Availability) |

`base_memory_mb` is the caller's request (default 256 MB for plugins,
128 MB for app entrypoints). `base_cpu_seconds` defaults to 60.

**Floors**: halving 16 MB or 4 s of CPU would render the sandbox
unusable. The clamp is 32 MB memory and 5 s CPU.

**Why FSIZE and NOFILE don't shrink**: disk size and fd count are not
hardware-tier-sensitive. A 2010 host has the same filesystem and the
same syscall surface as a 2026 host; what differs is its protection
posture. The tier-switch deliberately targets resources whose
overcommit can amplify a sandbox escape: AS (memory bombs), CPU
(fork bombs eat cycles), NPROC (fork-bomb proper).

## Why mode and not score drives the tier

Per AAA plan §4.3 the original wording was "Score < 50 ⇒ RESTRICTED
rlimits". In practice that threshold only fires on RESTRICTED_LEGACY
hosts that also miss SHA-NI **and** use LEGACY_KAISER **and** fall
back to pure_python PQC — i.e., a subset of hosts already in
RESTRICTED_LEGACY mode. Tying the rlimit switch directly to `.mode`
makes the audit row trivially explainable: "the kernel told us it's
not PROTECTED, so we tightened." The score remains an informational
breakdown for operators and EU AI Act Annex IV §V.1 evidence.

## Reading the cached manifest

```python
from services.app_sandbox import _active_manifest

m = _active_manifest()      # cached after first call
m.mode                      # "PROTECTED" | "RESTRICTED_LEGACY" | "UNKNOWN"
m.risk_score                # 30..100
```

`_active_manifest` is `@functools.lru_cache(maxsize=1)`. Tests that
need to swap the manifest call `_active_manifest.cache_clear()` and
either monkeypatch the function or override `VOS3_KERNEL_SERIAL_LOG`.

Override the serial-log path via env: `VOS3_KERNEL_SERIAL_LOG=/path/to/log`.
A missing file produces `build_empty()` → `mode="UNKNOWN"` → restricted tier.

## What's NOT in scope (yet)

- **Live re-tier**: a long-running sandbox process keeps the rlimits
  it was spawned with. We do not re-parse the klog on a tick. Out of
  scope per `docs/UNIVERSAL_HARDWARE_MANIFEST.md`.
- **Per-app override**: the tier is a host-level decision. An app
  manifest cannot raise rlimits above the host tier.
- **Network egress tightening**: AAA plan §4.3 hints that score < 50
  could trigger tighter network egress. That's a separate follow-up
  (touches `_build_network_isolation_wrapper`); P4.3 only does
  rlimits.
- **Microcode baseline deduction**: the `-10 microcode_below_baseline`
  line in the formula is wired to always 0 today. It activates when
  P4.2 lands (`kernel/src/arch/x86_64/microcode_check.c`).

## Source-of-truth pinning

| Source | Pinned by |
|---|---|
| Risk-score formula | `services/hardware_manifest.py::compute_risk_score` |
| Tier table | `services/risk_score.py::compute_adaptive_rlimits` |
| Manifest cache wiring | `services/app_sandbox.py::_active_manifest`, `_adaptive_rlimits` |
| Test pins | `tests/sandbox/test_adaptive_rlimits.py` (28 tests) |

A future iteration of the AAA plan that moves any value in the tier
table must update **both** the implementation and the test in the
same commit.
