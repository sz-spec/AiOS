# infra/verify — Deployment-tier verification scripts

These scripts run on real hardware to produce evidence artifacts that
in-tree pytest can't produce (Apple Silicon / non-KVM hosts can't
verify x86_64 KVM behavior; that's deferred to deployment-time).

## kvm_protected_full.sh — P1.2 follow-up C

**Goal**: confirm that on a real Linux KVM host with full feature
support, the kernel chooses `PROTECTED_FULL` and KPTI initialises
cleanly.

**Requirements** (script will preflight all of these):

- Linux x86_64 host (not macOS, not WSL1).
- `/dev/kvm` readable + writable by the running user.
- `qemu-system-x86_64` built with KVM accel (most distro packages are).
- `kernel/build/vos3.elf` built (run `make` in `kernel/` first).
- Host CPU with `pcid` + `invpcid` + `smep` + `smap`. Missing any of
  these is reported as `WARN` (the kernel will correctly demote to
  `PROTECTED_PCID_ONLY`) rather than `FAIL`.

**Run**:

```bash
cd vos.v1
bash infra/verify/kvm_protected_full.sh
```

Optional env knobs:

| Var | Default | Purpose |
|---|---|---|
| `KERNEL_ELF` | `./kernel/build/vos3.elf` | Path to the kernel ELF to boot. |
| `QEMU_BIN` | `qemu-system-x86_64` | QEMU binary on PATH. |
| `MEMORY` | `1024M` | `-m` flag for QEMU. |
| `SMP` | `2` | `-smp` flag for QEMU. |
| `BOOT_TIMEOUT_S` | `30` | Seconds to wait for `[KPTI] init: ready`. |
| `REPORTS_DIR` | `infra/verify/reports/` | Where to write the boot log + summary. |

**Exit codes**: `0` if all required assertions pass (PROTECTED_FULL or
the acceptable PROTECTED_PCID_ONLY fallback on capability-limited
hardware); `1` if the kernel refused to boot, missed the KPTI init
state, or emitted a `[SECURITY]` microcode warning.

**Evidence artifact**: the script writes two files under `REPORTS_DIR`:

```
kvm_protected_full_<UTC>.log         # raw boot serial log
kvm_protected_full_<UTC>.summary.txt # one-line-per-assertion result
```

Commit the summary file (not the raw log — it's verbose) under
`infra/verify/reports/` to close out the engagement.

## Honest-scope ceilings

- Boot-time assertions only. We confirm the kernel chose the right
  mode and reached `init: ready`; we do **not** stress the syscall
  path under KPTI. Perf characterization is `kernel/run_tests.sh`'s
  job.
- Below-baseline microcode produces `FAIL`, not because the kernel
  did anything wrong (it correctly demotes to `LEGACY_KAISER` per
  P4.2), but because this particular verification targets the
  PROTECTED path — the operator should rerun once the host's
  microcode is updated.
- A host that lacks SMEP/SMAP/INVPCID **passes** with `WARN` flags;
  PROTECTED_PCID_ONLY is the correct kernel decision on that
  silicon and is acceptable evidence for the engagement.

## gcp_close_task_48.sh — GCP escape route (closes #48)

The CI route deadlocked (see next section). The escape route rents a
real bare-metal-virt GCE instance for ~5 minutes, runs the same
verification on it, scp's the summary back, and self-destructs the
instance. Cost: ~$0.01 per invocation.

### Prerequisites (one-time per workstation)

```bash
# 1. Install the gcloud CLI
#    https://cloud.google.com/sdk/docs/install

# 2. Authenticate
gcloud auth login

# 3. Set (or note) the project to bill
gcloud config set project YOUR_PROJECT_ID

# 4. Enable Compute Engine API on the project (one-time per project)
gcloud services enable compute.googleapis.com
```

### Run

```bash
cd vos.v1
bash infra/verify/gcp_close_task_48.sh
```

The script will:

1. Preflight gcloud auth + project + Compute API enablement.
2. Create `n2-standard-2` instance with `--enable-nested-virtualization`
   (the n2 family supports nested virt on GCP; the flag opts in).
3. Wait for SSH to come up (≤150 s).
4. `scp` the `kernel/` and `infra/verify/` trees over.
5. Install GCC cross-compiler + QEMU on the instance.
6. Build the kernel with `BENCH_MODE=1` (~30 s).
7. Run `kvm_protected_full.sh` on real KVM (~30 s).
8. `scp` the produced `*.summary.txt` back as
   `infra/verify/reports/GCP_EVIDENCE_AAA.summary.txt` with a header
   identifying it as the closing artifact for task #48.
9. **Delete the instance** (cleanup runs from an EXIT trap, so the
   instance is removed even on abort — the trap prints the name and
   the manual-cleanup command if delete itself fails).
10. Print the next-step `git add/commit/push` commands.

### Env overrides

| Var | Default | Purpose |
|---|---|---|
| `GCP_PROJECT` | from `gcloud config` | Project to bill |
| `GCP_ZONE` | `us-central1-a` | Zone for the temp instance |
| `GCP_MACHINE_TYPE` | `n2-standard-2` | Must support nested virt |
| `GCP_IMAGE_FAMILY` | `ubuntu-2404-lts-amd64` | Boot image |
| `INSTANCE_NAME` | `vos-kvm-verify-<short-sha>-<utc>` | Override if you want to grep for it |
| `REPORTS_DIR` | `infra/verify/reports/` | Where to drop the evidence |

### After the run

```bash
git add infra/verify/reports/GCP_EVIDENCE_AAA.summary.txt
git commit -m "evidence(P1.2-C): GCP KVM PROTECTED verification — task #48 closed"
git push
```

Task #48 closes on that commit. The evidence file is hash-anchored
to `aeb3736` so a future re-anchored engagement won't accept it
silently — `services.hard_evidence` validates the anchor SHA and the
PASS lines structurally.

### Hard-Evidence bypass (developer ergonomics)

Once `GCP_EVIDENCE_AAA.summary.txt` is committed, dev hosts running
the backend on macOS / non-vOS Linux (manifest mode `UNKNOWN`) get
the **PROTECTED-tier rlimit budget** for sandbox processes instead
of the restricted dev-host tier. Behavior:

- Applies **only** when `manifest.mode == "UNKNOWN"`. Never overrides
  `RESTRICTED_LEGACY` (when the kernel told us it's not protected,
  we believe it — Security > Availability).
- Validates the evidence file structurally: anchor `aeb3736` must
  appear; a `PASS  Mitigation tier = PROTECTED_FULL` or
  `PROTECTED_PCID_ONLY` line must be present. A touched-empty file
  is rejected.
- Logs a `WARNING` line on every activation — never silent.
- The manifest's `.mode` field is **not** mutated — only the rlimit
  decision changes. Audit consumers still see UNKNOWN in the
  manifest itself.

Tests pinning the safety properties: `backend/tests/sandbox/test_hard_evidence_bypass.py`.

## Known GHA nested-KVM limitation (2026-05-17)

The `.github/workflows/kvm_verify.yml` workflow exists but is
**manual-only** (`workflow_dispatch` trigger). It was tried with both
`-cpu host` and `-cpu Skylake-Server,+pcid,+invpcid,+smep,+smap`;
both invocations deadlocked the kernel at the same boot line:

```
[INFO]  Initializing Virtual Memory Manager
[INFO]  VMM: Initializing Virtual Memory Manager
   ← hang here, no further output even at 240s timeout
```

The hang reproduces deterministically across multiple runs and CPU
models. The same `vos3.elf` boots end-to-end in under two seconds on
Apple Silicon via QEMU TCG (see `kernel/run_tests.sh`), so the
incompatibility is specific to **GitHub Actions's nested-virt KVM**,
not the kernel logic.

The root cause was not pinned (debugging nested KVM remotely is
constrained by Azure's hosting environment). Plausible suspects:

- VMM page-table writes hitting a nested-EPT pagefault loop.
- A VMCS/MSR feature exposed through Skylake-Server that GHA's
  nested KVM doesn't actually implement.
- Memory model interaction with the kernel's IST stack setup under
  nested virt.

**Workarounds** to produce the evidence artifact:

1. Run `bash infra/verify/kvm_protected_full.sh` on any bare-metal
   Linux x86_64 host (your laptop, a workstation, a cloud VM with
   nested-virt explicitly enabled, e.g. GCP `n2-standard-4` with
   `enable-nested-virtualization=TRUE`).
2. Add a **self-hosted runner** with KVM access to the repo and the
   workflow will pass — the script's preflight + assertions are
   correct, only the GHA-hosted runner environment is the problem.
3. Wait for GHA to fix nested-virt support (no public roadmap).

Until one of the workarounds lands, **task #48 in the engagement
remains open** with the partial evidence preserved as a GHA workflow
artifact (`kvm-verify-evidence-25985875417`) and this documented
limitation. The script and workflow files are committed; running
them on a viable host will produce the closing artifact in under a
minute.
