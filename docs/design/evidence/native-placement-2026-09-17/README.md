# Deterministic initial CPU placement — 2026-09-17

The clean TLB/SMP qualification at commit `61ad2771f526b3744323c654f9fa4ed7b5781000` passed BIOS and UEFI with one and four CPUs. Every configuration produced all 16 user checkpoints and the expected translation observations on every online CPU. Both four-CPU configurations placed worker 0 on hardware APIC 0 and worker 1 on APIC 1. The one-CPU controls used APIC 0. The classifier and workload were not weakened or modified.

The [previous UEFI4 failure](../native-signal-lifetime-2026-09-17/clean-tlb/uefi4/result.json) remains preserved. Its two workers both ran on APIC 1 because first-dispatch lock acquisition determined ownership. Longer sleeps could not change their permanent owners. The gated scheduler now assigns genuinely new tasks once at enqueue using round-robin selection among started CPUs. Existing owners, shared-address-space siblings, and BSP bootstrap remain unchanged. This is initial placement, not migration or hotplug support. The normal build path is unchanged.

Implementation author and execution reviewer: `/root/mcp_upgrade`. Independent source and host-test reviewer: `/root/math_build_review`. The execution reviewer does not claim to independently audit their own implementation. The actual-source host test `scripts/test_scheduler_initial_placement.py` exercises both the helper and enqueue path with mocked topology/IRQ primitives, including sparse topology, failed CPUs, bootstrap, bounds, duplicate enqueue, and owner preservation.

[The evidence recheck](independent-evidence-check.json) recomputed all 6,034 source-file hashes, the source archive, ISO, kernel, both manifest representations, and each raw log. It reran the classifier from the archived source and independently recomputed every checkpoint's 200,000-step arithmetic sequence. The manifest's canonical-JSON digest in `result.json` is intentionally different from its formatted file-byte digest; both are recorded explicitly.

The clean build had no initial generated outputs, preserved its source inputs, and removed its owned Docker container. A retained warning reports the new output directory `build/native-smp` as 0.0005 seconds in the future; no reused output or source/object clock warning was observed. Raw build output is retained in [build.log](build.log).

This gate observes two-page kernel translation replacement and ring-3 work on distinct hardware CPU identities in QEMU. It does not establish shared-user-address-space isolation, hotplug, migration/FPU safety, scheduling fairness, universal hardware support, or a whole-kernel security proof. No repeated clean run was required after this pass.

Staged secret scanning produced 142 candidates confined to `source-manifest.json`. Root independently verified each against the corresponding archived source file SHA-256; no suppression was added.
