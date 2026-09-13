<!--
SPDX-License-Identifier: MIT
SPDX-FileCopyrightText: 2026 VOS3 Project
-->

# VOS-SDK Manifesto

**Status:** Phase 6.0 — VBus v3.0 Standard
**Audience:** Developers building agentic systems on VOS3
**Companion artifacts:** `kernel/include/vos/vbus.h`, `sdk/python/vos3_sdk/core.py`

---

## The thesis in one sentence

> VOS-SDK is the Win32 API for the AI era — not the AI itself, but the
> stable, hardware-attested substrate every AI agent needs to be
> trustworthy.

Win32 did not invent word processors, browsers, or games. It made them
*possible at scale* by giving every program a single, stable surface
through which to ask the kernel for memory, files, threads, and
windows. The substrate was boring. The applications it unlocked
weren't.

VOS-SDK plays that role for AI agents. We do not pick which model
runs, what prompt it gets, or which framework orchestrates the swarm.
We provide the two primitives every serious agent eventually needs:

1. **Memory Safety** — agents read each other's KV-cache without
   leaking, mutating, or escaping their slot.
2. **Attested Execution** — every transaction lands in a Merkle
   Mountain Range audit chain that operators (and regulators) can
   replay byte-for-byte.

Everything else — model, framework, prompt strategy, business logic —
is the developer's call. The SDK refuses to be opinionated about any
of it.

---

## Why "Win32 for AI" is the right analogy

**1. A trust substrate, not a product.**
Win32 outlived every Microsoft application that depended on it because
it was *the contract*, not *a feature*. VOS-SDK is the same. Claude,
Gemma, your custom fine-tune, an unsigned third-party agent — they all
program against the same `vbus_intelligence_profile_t`. The SDK does
not care which one wins.

**2. Capability boundaries are kernel-enforced.**
Win32 processes can't peek into each other's heap because the kernel
draws the line in hardware. VOS-SDK slots can't peek into each other's
KV-cache for the same reason: the W^X enforcement at
`kernel/src/mm/vmm.c:889`, the slot-state ZOMBIE quarantine at
`kernel/src/sec/slot_state.c`, and the `VBUS_SHARE_*` capability
bitmask in `vbus.h` are all hardware-level invariants, not library
politeness.

**3. Forward compatibility is non-negotiable.**
The Win32 contract that NT 3.51 shipped in 1993 still runs on Windows
11 in 2026. VOS-SDK signs the same blood pact. The `VOS3Client.connect()`
contract documented today will hold when:

  * The ASCII tokenized protocol gives way to the binary v3.0 frame
    envelope (`virtio_vbus.c`, partially shipped).
  * `secure_compute()` upgrades from local-execution-with-attestation
    (today) to WASM-runtime-in-slot isolation (v20.6).
  * The Standard Service Registry crosses from 256-slot static
    allocation to dynamic per-tenant federation (v21.0).

When those upgrades land, **no developer code needs to change**.
That's the entire point.

---

## The two primitives

### 1. Memory Safety

Every agent on a VOS3 host gets a slot. Slots are isolated by:

- **Per-slot virtual address spaces.** Each slot's expansion zone is
  disjoint by construction; cross-slot pointer dereferences trap into
  the W^X violation handler (`vos3_slot_wx_violation_handler`) and
  transition the offending slot to ZOMBIE state — terminal, audit-logged,
  unrecoverable without operator intervention.
- **CoW KV-cache deduplication.** Two agents loading the same model
  prefix (e.g. system prompt) share the underlying physical pages via
  the registry in `kernel/src/mm/kv_compressor.c`. The first write
  forks via the `mark_dirty` path. Memory savings without losing
  per-slot semantic isolation.
- **Capability-gated sharing.** The `VBUS_OP_SHARE_MEMORY` request
  carries a `share_flags` bitmask; the kernel returns a SUBSET of what
  the caller requested based on the owning slot's capability mask. No
  silent privilege escalation.

What you get: **agents that cannot leak each other's prompts, weights,
or intermediate activations**, even when they share physical pages for
efficiency.

### 2. Attested Execution

Every VBus transaction with side effects writes a leaf to the Merkle
Mountain Range audit chain (`kernel/src/sec/mmr_audit.c`). The leaf
contains:

- The op-code (`VBUS_OP_REGISTER_AGENT`, `VBUS_OP_SECURE_COMPUTE`, …)
- The session and request IDs
- A SHA-256 over the inputs (code hash + data hash for `secure_compute`)
- An RDSEED entropy nonce
- The current TSC

The current MMR root is queryable at any time
(`VOS3Client.query_mmr_root()`) and updates monotonically. Operators —
or regulators under EU AI Act Article 12 — can:

1. Pull the chain.
2. Re-derive the root from the leaves.
3. Verify byte-for-byte that no transaction was inserted, removed, or
   reordered.

What you get: **a tamper-evident audit ledger with cryptographic
proof of integrity**, available to every developer through one method
call.

---

## What VOS-SDK is *not*

- **Not a model registry.** We do not curate, rank, or recommend
  models. The router (`backend/src/efficiency/router.py`) makes routing
  decisions based on operator-configured policy, not SDK opinion.
- **Not a prompt framework.** No prompt templates, no chains, no
  graphs. Bring LangGraph, LlamaIndex, your own framework, or none.
- **Not an orchestration runtime.** Multi-agent coordination is the
  developer's responsibility; the SDK gives you the substrate
  (registry + isolation + audit), not the choreography.
- **Not a sandbox alternative.** It is a complement. Run your
  untrusted code in a Linux namespace or gVisor sandbox AS WELL AS in
  a VOS3 slot. Defense in depth is an SDK virtue.

---

## Honest scoping (read this before shipping)

The substrate is real. Some pieces are scaffolded. We say so out loud:

| Capability | Today (v20.5.2) | Lands in |
|-----------|-----------------|----------|
| ASCII VBus dispatch | shipping in `virtio_bridge.c` | — |
| Numeric op-code envelope (v3.0) | header defined; SDK speaks ASCII shim under the hood | v20.6 |
| MMR audit ledger | shipping (1340 ASSERT certified) | — |
| KV-cache deduplication | source-shape verified, expansion-path integration | v20.5.3 |
| Hugepage ceiling gate (CORE/PRO) | shipping (256 / 5120 pages) | — |
| Ed25519 license signature verify | stub (returns false; degrades to CORE) | v20.6 |
| `secure_compute` local execution + attestation | shipping | — |
| `secure_compute` WASM-in-slot isolation | not yet | v20.6 |
| TPM EK extraction | zero-filled stub (CRB MMIO unmapped) | v20.6 |
| Federated registry (>256 services / multi-host) | static-allocation registry | v21.0 |

If you build on a feature labelled "shipping," it will not regress.
If you build on a feature labelled by a future version, your code's
`VOS3Client` surface will not change — only the kernel side will get
stronger.

---

## The contract we sign with developers

1. **The header is canonical.** `kernel/include/vos/vbus.h` is the
   single source of truth for op-codes, magic, flags, and
   structures. The Python SDK and (forthcoming) C SDK mirror it.
   Drift between SDK and header is a bug.
2. **Backward compatibility is the default.** We will not delete an
   op-code or change a struct layout without a major version bump and
   a deprecation cycle of at least two minor versions.
3. **Forward compatibility is built in.** New op-codes occupy reserved
   ranges (`0x500-0x5FF` for v20.6+); old clients ignore unknown
   responses gracefully.
4. **Honest capability reporting.** Every `register_agent` response
   tells the caller which capabilities were *granted* vs *requested*.
   No silent demotion.
5. **Audit chain is non-bypassable.** No SDK method writes to the
   kernel without an MMR leaf. There is no "fast path" that skips
   audit, and there will not be one.

---

## Five lines of code to start

```python
from vos3_sdk import VOS3Client

with VOS3Client() as client:
    client.connect()
    token = client.secure_compute(my_agent_step, {"prompt": "..."})
    print("Attestation root:", token.attestation_root)
```

That is the whole substrate. Build whatever you want on top.

---

## Further reading

- VBus header: `kernel/include/vos/vbus.h`
- Python SDK core: `sdk/python/vos3_sdk/core.py`
- MCP bridge: `tools/vos3_mcp_bridge.py`
- Open-Core licensing charter: `docs/strategy/OPEN_CORE_LICENSING.md`
- MMR audit implementation: `kernel/src/sec/mmr_audit.c`
- Slot state machine: `kernel/src/sec/slot_state.c`
