<!--
SPDX-License-Identifier: MIT
SPDX-FileCopyrightText: 2026 VOS3 Project
-->

# VOS3 Forward Architecture Vision

> **Date:** 2026-05-02 · **Search window:** 2025-Q4 → 2026-Q2 ·
> **Method:** real WebSearch on public sources. Discord/Matrix not searched (closed platforms).
> **Provenance rule:** every claim is either tagged `[Source:]` (link from a real WebSearch result) or `[Architectural Vision]` (a proposed direction — not a research finding).

This document answers a single question: **what would VOS3 need to absorb from the public OS-and-AI landscape to become the "Windows of AI" within 18 months?** It is a research-to-action bridge, not a literature review.

---

## §1. Verifiable findings (from WebSearch, 2026-05-02)

### 1.1 — CXL 4.0 is real and dated; production deployment is 2027+

| Fact | Provenance |
|---|---|
| CXL 4.0 spec **released 2025-11-18**. Bandwidth **128 GT/s** on PCIe 7.0; bundled ports give **1.5 TB/s** logical attachments. | [Source: blocksandfiles.com — "CXL 4.0 doubles bandwidth"] |
| Phase-2 memory tiering (2025–2026): hot data in DRAM, cold in CXL, software-managed. | [Source: introl.com — "CXL 4.0 Infrastructure Planning Guide"] |
| Production timeline: **CXL 2.0 today; CXL 3.x late 2026; CXL 4.0 multi-rack 2026-Q4 → 2027+**. | [Source: introl.com] |
| CXL 4.0 unlocks **100+ TB pooled memory with cache coherency** across AI infrastructure. | [Source: synopsys.com — "CXL 4.0 Bandwidth First"] |

**What is NOT in the public CXL 4.0 spec (per searches):** there is no NPU-specific zero-copy primitive in the standard. The "zero-copy AI" framing is a market narrative that *uses* CXL but is not standardised by the consortium.

### 1.2 — Blackwell driver upstream landed via Nova; Hopper/Blackwell prep ongoing

| Fact | Provenance |
|---|---|
| **Nova driver** (Rust, NVIDIA-developed) is **already upstream in the mainline Linux kernel**. | [Source: phoronix.com — "NVIDIA Preparing Hopper & Blackwell GPU Support With Nova"] |
| Hopper + Blackwell support being prepped by NVIDIA engineer **John Hubbard**. | [Source: phoronix.com] |
| Consumer **RTX 50 (Blackwell) requires open kernel modules** — proprietary path is end-of-life on consumer Blackwell. | [Source: developer.nvidia.com forums — "RTX 50 Series GPU Drivers on Linux"] |
| Datacenter B200/GB200 driver: NVIDIA Data Center GPU Driver 580.126.20 (release notes 2026-Q1). | [Source: docs.nvidia.com — "Tesla 580.126.20 release notes"] |

**Implication for VOS3:** the path of least resistance for Blackwell support in the medium term is to track Nova's evolving DRM-like contract rather than the proprietary blob. Nova is Rust → VOS3's Tauri Rust layer is a natural integration point.

### 1.3 — Intel Gaudi 4 is not yet publicly documented; Gaudi 3 driver still out-of-tree

| Fact | Provenance |
|---|---|
| Gaudi 3 PCIe cards are shipping; **no mainline Linux driver**. The `habanalabs` out-of-tree (OOT) module is still the supported path. | [Source: phoronix.com — "Intel Gaudi 3 PCIe Accelerator Cards Now Available - Still Waiting On Upstream Linux Driver"] |
| Latest Habana docs at the time of search reference **Gaudi 3 / Software Suite 1.23.0**. **Gaudi 4 not present in the public docs** as of 2026-05-02. | [Source: docs.habana.ai] |

**Implication for VOS3:** the universal-driver contract MUST handle out-of-tree drivers gracefully. Today VOS3's `vos3_driver_t` (universal_driver_spec.md) assumes a clean in-kernel dispatch. Realistically, the first Gaudi support will be a userspace shim binding to Intel's OOT module via VFIO.

### 1.4 — sched_ext is in mainline; agent-managed schedulers are a research reality

| Fact | Provenance |
|---|---|
| **sched_ext landed in Linux 6.12** — write custom CPU schedulers as eBPF, load into running kernel without patching. | [Source: dev.to — "I Put an LLM Inside the Linux Kernel Scheduler"] |
| **SchedCP** (MCP-server architecture for LLM agents managing Linux schedulers) achieves **1.79× perf, 13× cost reduction** vs naive agent prompting. | [Source: arxiv.org/abs/2509.01245 — "Towards Agentic OS: An LLM Agent Framework for Linux Schedulers"] |
| sched_ext microconference at **Linux Plumbers Conf 2025-12-11** discussed BPF-extensible scheduler classes. | [Source: lpc.events/event/19/sessions/229] |
| Real recent LKML scheduling-latency thread (kernel 6.12, severe sched latency under specific workload). | [Source: lkml.org/lkml/2026/4/14/1586] |

**Implication for VOS3:** the most important architectural lesson is **decouple "what to schedule" from "how to schedule it"**. SchedCP's design separates LLM semantic reasoning from the kernel's execution; that's exactly the abstraction VOS3 needs for AI-native scheduling, and it costs nothing to adopt the same control-plane shape today.

---

## §2. Holy-Grail problems still open as of May 2026

These are real open problems we found discussion of, not VOS3-internal speculation:

| Problem | Why it's "Holy Grail" | Where discussed |
|---|---|---|
| Per-NPU-context interrupt coalescing | Modern NPUs expose 64+ MSI-X vectors; OSes still dispatch them via the same generic IRQ path → completion latency tail-spikes | LKML threads, Phoronix coverage |
| CXL memory-page hotness tracking at hardware speed | Software tiering (Phase 2) is millisecond-class; hardware access-bit polling is microsecond-class but vendor-specific | CXL Q3-2025 webinar [Source: computeexpresslink.org] |
| In-kernel LLM-driven scheduler safety | sched_ext + BPF gives a sandbox, but proving an LLM-emitted policy is non-degenerate is unsolved | SchedCP paper (§4) |
| Multi-tenant NPU isolation without IOMMU groups | Modern HBM is shared; per-tenant fault isolation falls back to time-multiplexing | Phoronix Gaudi 3 coverage |

**VOS3 posture today (honest):** we touch all four problems but solve none of them. The kernel exposes one MSI-X vector per NPU (Bridge B1 from OLYMPUS audit), CXL is not yet integrated, the dispatcher is static (no LLM control plane), and IOMMU groups are partial.

---

## §3. [Architectural Vision] How VOS3 leapfrogs

Everything in this section is a **proposal**, not a sourced fact. Read accordingly.

### 3.1 Vision A — Two-Plane Scheduler (lift the SchedCP pattern)

Today the dispatcher (`kernel/src/ipc/dispatcher.c`) is monolithic: it picks an agent by capabilities + queue depth. SchedCP's design separates **semantic reasoning** (which workload type → which policy) from **execution** (the BPF program / inline scheduler).

Proposal:
- **Control plane** (Python, talks to backend SmartRouter) emits a JSON descriptor: `{policy: "interactive_priority", target: "slot_2", deadline_us: 30000}`.
- **Data plane** (kernel `dispatcher.c`) consumes the descriptor as a tagged hint, never as a direct command. Bad descriptors degrade gracefully to current behavior.
- Verification gate: every descriptor has a static type-check (Pydantic on the backend, struct validate in the kernel) plus a runtime-bounded execution window. This mirrors SchedCP's three-step execution-verifier pattern.

Cost: ~2 weeks engineering, no ABI break, gated behind a new VBus command `DISPATCH_HINT`. Lands as RFC-2026-XX-DISPATCH_HINT.

### 3.2 Vision B — VBus Memory-Region Handshake for CXL.mem

CXL 4.0's relevant primitive for VOS3 is **cache-coherent shared memory pools**. VOS3 already has zone-ACL'd ivshmem (kernel/src/drivers/ivshmem.c). The vision is to **rebrand** ivshmem zones as CXL.mem regions when the kernel detects a CXL host bridge, and let VBus negotiate region access via the existing zone-owner protocol.

Concrete:
- Add a `VBUS_CXL_REGION_REQ` command that the host sends with `{region_id, mode}`.
- Kernel maps the region into the requesting slot's VAS via the existing `vmm_map_range` path with a new flag `VOS3_MAP_CXL_COHERENT`.
- Eviction / cold-tier demotion is handled by an LRU policy in `kv_compressor.c` — the same code path that already manages KV cache eviction, extended to CXL.mem pages.

Why this works: **the kernel doesn't need to know about CXL hardware** in v1. CXL appears as a memory range with NUMA-like access cost. The MMU already handles the rest. We get tiering without writing a CXL driver.

### 3.3 Vision C — Multi-Vector Universal-Driver MSI-X

Found in OLYMPUS as Bridge B1 — confirmed against Blackwell/Gaudi reality. Single MSI-X vector is the bottleneck.

Concrete: extend `vos3_driver_t` (kernel/include/...) with:
```c
struct vos3_driver_v2 {
    /* ... existing fields ... */
    int (*irq_register_vector)(struct vos3_device *, uint16_t vec, void (*h)(uint16_t));
    int (*irq_set_affinity)(struct vos3_device *, uint16_t vec, uint32_t cpu_mask);
    uint16_t max_vectors;     /* device-reported; queried via PCI capabilities */
};
```
Migration: existing single-vector drivers register vector 0; the dispatch glue handles the fanout. **ABI-stable** (we add a new struct, keep the old one).

### 3.4 Vision D — Kernel-rooted "Latency Class" tag for Agent tasks

From OLYMPUS §4.3. The 2-bit `latency_class` field on `vos3_task_t` becomes the **input** to an sched_ext-style BPF policy that VOS3 ships pre-loaded:
- `INTERACTIVE` → preempts BATCH, runs on cluster_id 0 only
- `STREAMING`   → strict deadline scheduling (EDF-lite)
- `BATCH`       → background, yields freely
- `CONTROL`     → kernel-only, never preempted

This composes cleanly with Vision A: the semantic plane decides the class; the data plane enforces it.

---

## §4. Impact-on-VOS3-Dominance Ranking

Using a deliberately conservative scale: **HIGH** = ships within 6 months, addresses a real customer-blocking gap; **MED** = 6–18 months; **LOW** = research-grade, decisive ground only after 18 months.

| Rank | Item | Source | Impact | Effort |
|---|---|---|---|---|
| 1 | **Multi-vector MSI-X universal driver (3.3)** | Real, Blackwell/Gaudi need it now | HIGH | 4-6 weeks |
| 2 | **sched_ext-style two-plane dispatcher (3.1)** | Real (SchedCP pattern) | HIGH | 2-4 weeks (RFC + gate) |
| 3 | **Latency-class tags + BPF policy stub (3.4)** | Vision, builds on #2 | MED | 2 weeks after #2 |
| 4 | **CXL.mem via ivshmem reskin (3.2)** | Real (CXL 4.0 deployed 2027+) | MED | 6-8 weeks |
| 5 | **Nova driver tracking for Blackwell** | Real (upstream landed) | MED | continuous |
| 6 | Hardware page-hotness tracking | Vendor-specific, not in CXL spec | LOW | research only |
| 7 | LLM-driven scheduler safety proof | Open problem (SchedCP §4) | LOW | research only |

**Bottom-line strategic call:** items 1 and 2 are the cheapest moves with the highest leverage. They are the difference between "VOS3 runs on a desk demo" and "VOS3 runs an NVIDIA-Blackwell production cluster." Everything else is downstream of those two.

---

## §5. Concrete Adjustments to Current VOS3 Files (audit)

A literal read of the request: do `vbus_transport.c` or `license_check.c` need changes for Blackwell-2 / Gaudi-4 specifically?

**`vbus_transport.c`** — `[Architectural Vision]` answer: yes, eventually. The current 22-command set has no `DISPATCH_HINT` and no `CXL_REGION_REQ`. These are item-1 and item-4 on the ranking table. Patch sketch lives in §3.1 / §3.2.

**`license_check.c`** — **No, not for hardware.** This file gates the 10 GiB hugepage ceiling on a TPM/SMBIOS fingerprint; the per-NPU choice is downstream of that gate, not coupled to it. The OLYMPUS Tier-A SMP-race fix (S1/G1/G4) was the only license_check.c change actually needed for current correctness. **Adding "Blackwell-2 awareness" to license_check.c would be a category error** — that logic belongs in `npu.c` and `acpi.c`.

---

## §6. What this document IS and ISN'T

**IS:** an honest synthesis of public-web evidence (Sources cited inline) plus clearly-labeled architectural proposals (`[Architectural Vision]` tag).

**IS NOT:** a literature review, an exhaustive scan, or a substitute for engineering review. Discord/Matrix channels, vendor NDA materials, and any source not reachable from public WebSearch were not consulted. "Blackwell-2" and "Gaudi-4" specifically did not surface as documented products in the searches; treating them as roadmap items is reasonable but not sourced.

**Confidence by section:** §1 (HIGH — sourced facts), §2 (MED — open problems are real but our framing is editorial), §3 (LOW — proposals), §4 (MED — ranking is judgment), §5 (HIGH — direct code-base read).

---

## Sources (real, May 2026)

- [CXL 4.0 doubles bandwidth and stretches memory pooling — blocksandfiles.com (2025-11-24)](https://blocksandfiles.com/2025/11/24/cxl-4/)
- [CXL 4.0 Infrastructure Planning Guide — introl.com](https://introl.com/blog/cxl-4-0-infrastructure-planning-guide-memory-pooling-2025)
- [CXL 4.0 and the Interconnect Wars — introl.com (Dec 2025)](https://introl.com/blog/cxl-4-0-specification-interconnect-wars-december-2025)
- [CXL 4.0 Bandwidth First — synopsys.com](https://www.synopsys.com/blogs/chip-design/cxl-4-bandwidth-first-what-designers-are-solving-next.html)
- [CXL Q3-2025 Webinar — How CXL Transforms Server Memory Infrastructure](https://computeexpresslink.org/wp-content/uploads/2025/10/CXL_Q3-2025-Webinar_FINAL.pdf)
- [NVIDIA Preparing Hopper & Blackwell GPU Support With Nova — phoronix.com](https://www.phoronix.com/news/Hopper-Blackwell-Nova-Prep)
- [NVIDIA Tesla 580.126.20 Release Notes (Linux)](https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-580-126-20/index.html)
- [RTX 50 (Blackwell) GPU Drivers on Linux — NVIDIA Developer Forums](https://forums.developer.nvidia.com/t/rtx-50-series-blackwell-gpu-drivers-on-linux/335669)
- [Intel Gaudi 3 PCIe Accelerator Cards — Phoronix](https://www.phoronix.com/news/Intel-Gaudi-3-PCIe-Cards)
- [Habana Gaudi Documentation 1.23.0](https://docs.habana.ai/en/latest/Installation_Guide/Driver_Installation.html)
- [Towards Agentic OS: LLM Agent Framework for Linux Schedulers — arXiv 2509.01245](https://arxiv.org/abs/2509.01245)
- [I Put an LLM Inside the Linux Kernel Scheduler — dev.to](https://dev.to/naufalw/i-put-an-llm-inside-the-linux-kernel-scheduler-heres-what-happened-1cn9)
- [sched_ext microconference — Linux Plumbers Conf 2025](https://lpc.events/event/19/sessions/229/)
- [LKML — Sched scheduling latency on 6.12 (2026-04-14)](https://lkml.org/lkml/2026/4/14/1586)
