# vOS — B3-1 eBPF Write-Gate Race Closure · Architectural Design Specification

> **Status:** DRAFT / Blueprint — **no code landed**. Design only.
> **Finding:** B3-1 — eBPF host↔guest write-gate "no-entry → default ALLOW" race (S2-ceiling, **OPEN**).
> **Chosen primitive:** **Option (a) — Atomic Mark-and-Push** (single-syscall fd-mark + color-push).
> **Base:** `main@b67e45c` · **Author:** autonomous design pass, 2026-06-13.
> **Anchors (normative):** `backend/security/kernel_gate_connector.py`,
> `kernel/include/vos/taint_maps.h`, eBPF LSM program `kernel/src/sec/taint_gate.c` (skeleton).
> **Target sprint:** Sprint 19+ (requires Linux ≥ 5.17 eBPF-LSM runner — see §7).

---

## 1. Problem Statement (ground truth)

The C-2 write-gate enforces byte-level information-flow control at the kernel
egress boundary. Userspace (the trusted bridge) pushes a per-`(pid, fd)` color
entry into a shared eBPF map; the eBPF LSM hook on `bpf_lsm_file_permission`
(and socket equivalents) recomputes `max(colors)` over the write range and
returns `-EPERM` when it exceeds the per-fd sink ceiling.

Two structural gaps make today's gate **best-effort**, not sound:

1. **Push↔write TOCTOU.** The push (`BPF_MAP_UPDATE_ELEM`) and the guarded
   `write()`/`sendmsg()` are **two separate syscalls**. A compromised agent
   thread can issue the `write()` in the window *before* the bridge's push
   becomes visible in the map.
   - Code: the LIVE push is `KernelGateConnector._push_one()` →
     `self._bpf.map_update_elem(...)` (`kernel_gate_connector.py:710-711`),
     issued separately from the egress syscall the bridge is trying to gate.

2. **No-entry → default ALLOW (fail-open).** When the LSM hook looks up a
   `(pid, fd)` with **no map entry**, it permits the write. The MOCK twin
   mirrors this exactly and the B3 suite *characterizes* it as the finding:
   - Code: `KernelGateConnector.simulate_write()` —
     `if entry is None: return GateDecision(decision=DecisionKind.ALLOW, …
     reason="no entry for (pid, fd) — default ALLOW")`
     (`kernel_gate_connector.py:763-774`).
   - Header acknowledgement: `taint_maps.h` "Honest scope ceiling" —
     userspace populates the map *before* the write, and an absent map ⇒ the
     gate "degrades to MOCK behavior".

Combined, (1)+(2) mean a racing write to a *tainted* fd that momentarily lacks
its entry is **allowed**, defeating the egress ceiling. This is exactly the
ceiling the connector's own docstring flags as Sprint 19+ work
(`kernel_gate_connector.py:55-61`).

### 1.1 Threat model

- **In scope:** an attacker executing arbitrary code *inside* the agent process
  (post-prompt-injection / RCE), able to call `write()`/`sendmsg()` directly on
  any fd, racing the bridge.
- **Trusted:** the bridge code path that materializes a `TaintedBuffer` onto an
  fd and the kernel. The bridge runs before the agent regains control on the
  egress path.
- **Out of scope (unchanged):** kernel compromise, side-channel exfiltration,
  fds that never carried a `TaintedBuffer` (untainted I/O is *not* gated by
  design — see §5.3).

---

## 2. Design Goals & Invariants

| # | Invariant | Source of truth |
|---|---|---|
| G1 | **No observable window** between "fd is gated" and "fd has its color entry". | closes §1(1) |
| G2 | A **marked** fd with a missing/stale entry **fails closed** (`-EPERM`), never ALLOW. | inverts §1(2) |
| G3 | An **unmarked** fd is outside the taint domain → ALLOW (untainted I/O unaffected). | §5.3 |
| G4 | **`max(colors)` re-computation guard (Sprint 2, LOCKED):** the ceiling check evaluates the kernel-recomputed `max` over the actual `colors[]` array — **never** the buffer's self-reported `max_color()`. | `kernel_gate_connector.py:673,702` + B3 tests |
| G5 | **Byte-for-byte struct contract** with `taint_maps.h` is preserved; any drift is a CONTRACT break that silently disables enforcement. | `kernel_gate_connector.py:399-410` ("KEEP IN SYNC") |
| G6 | No production-default flip and no key provisioning as a side effect (M3 discipline carried forward). | freeze §4 |

> **G4 is non-negotiable.** Today `_push_one` computes
> `max_color_val = max(colors) if colors else PUBLIC`
> (`:673`) and packs *that* into the map value (`:702`), independent of the
> buffer's claimed `max_color()`. The B3 suite asserts a lying buffer cannot
> under-report past the ceiling. The atomic op MUST carry the same recomputed
> scalar — see §6.2.

---

## 3. Option (a) — Atomic Mark-and-Push: Architecture

### 3.1 Core idea

Collapse "mark fd as gated" and "install color entry" into **one syscall** that
executes under a single kernel critical section. By the time the syscall
returns, the fd is *both* marked *and* has its entry — there is no intermediate
state an attacker thread can exploit (G1). A fd that is marked but (transiently,
e.g. post-GC) lacks an entry denies (G2).

```
   TODAY (racy, 2 syscalls):                  OPTION (a) (atomic, 1 syscall):
   ┌────────────┐                             ┌──────────────────────────────┐
   │ push entry │  ← BPF_MAP_UPDATE_ELEM      │ vos3_taint_mark_push(fd,…)    │
   └─────┬──────┘     (window opens here)     │   under per-fd gate lock:     │
         │  ⚠ attacker write() races here     │     1. set MARK(fd)           │
   ┌─────▼──────┐                             │     2. install color entry    │
   │  write()   │  ← LSM hook                 │   (both committed atomically) │
   └────────────┘                             └──────────────┬───────────────┘
                                                              │ then write() — fd already
                                                              ▼ marked+entried ⇒ enforced
```

### 3.2 Unified control structure (new)

A single control argument carries the fd identity, the color payload, and the
sink policy. It **embeds** the existing `struct vos3_taint_color_entry` verbatim
so the on-the-wire value is bit-identical to what `BPF_MAP_UPDATE_ELEM` installs
today (G5):

```c
/* PROPOSED — kernel/include/vos/taint_maps.h (additive, Sprint 19) */

#define VOS3_TAINT_MARK_PUSH_ABI  1u   /* bump on any field change */

struct vos3_taint_mark_push_arg {
    uint32_t abi_version;          /* == VOS3_TAINT_MARK_PUSH_ABI; else -EINVAL */
    uint32_t fd;                   /* fd being marked + gated (caller-relative) */
    uint32_t flags;                /* VOS3_TAINT_MP_* below */
    uint32_t _pad;                 /* explicit; keep 8-byte alignment */
    struct vos3_taint_color_entry entry;  /* the value, UNCHANGED layout */
} __attribute__((packed));

#define VOS3_TAINT_MP_REPLACE   (1u << 0)  /* overwrite existing entry */
#define VOS3_TAINT_MP_ONESHOT   (1u << 1)  /* auto-clear entry after one write */
```

> `entry` is the **existing** `struct vos3_taint_color_entry`
> (`taint_maps.h:~150`). No layout change ⇒ the Python packer
> `_pack_map_value()` (`kernel_gate_connector.py:420-450`,
> `_VALUE_FMT = "<{65536}sIBBBBQQQ"`) is reused unchanged; only a thin
> `_pack_mark_push_arg()` prefix wrapper is added.

### 3.3 Delivery mechanism

Two viable carriers; **recommend the ioctl path** for files + a setsockopt
mirror for sockets, both landing in the same kernel handler:

| Carrier | Hook | Notes |
|---|---|---|
| `ioctl(ctrl_fd, VOS3_TAINT_IOC_MARK_PUSH, &arg)` on a control device (e.g. `/dev/vos3-taint`, or a pinned BPF link fd) | file egress | Caller passes the **target** fd inside `arg.fd`; kernel resolves it relative to the caller's fd table under the gate lock. |
| `setsockopt(sock_fd, SOL_VOS3, VOS3_SO_TAINT_MARK_PUSH, &arg, len)` | socket egress | `arg.fd` is ignored; the socket the option is set on **is** the marked fd — closes any "which fd" ambiguity. |

Both converge on `vos3_taint_mark_push_commit(task, fd, &arg)` (kernel C, shared
by the native build per `taint_maps.h` "vOS native kernel build … in-kernel C
implementation of the same hook").

### 3.4 The MARK bit

The "is this fd gated" flag lives in **per-fd kernel storage**, not the color
map, so its lifetime is bound to the fd object (auto-cleared on `close()`):

- **Sockets:** `bpf_sk_storage` (`BPF_MAP_TYPE_SK_STORAGE`).
- **Files:** `bpf_inode_storage` (`BPF_MAP_TYPE_INODE_STORAGE`, ≥ 5.10) keyed via
  the file's inode, scoped by `(pid, fd)` cross-check against the color map key.

The MARK store holds a tiny record: `{ marked: u8, abi: u8, pushed_ns: u64 }`.
The color payload stays in the existing `(pid,fd)`-keyed color map
(`VOS3_TAINT_MAP_PIN_PATH = "/sys/fs/bpf/vos3/taint_colors"`).

---

## 4. Revised LSM Decision Table (the closure)

The hook (`bpf_lsm_file_permission` on `MAY_WRITE`, ≥ 5.7; socket hooks for
egress) changes its no-entry branch from ALLOW to a **MARK-aware fail-closed**
ladder:

| MARK(fd) | color entry present? | Decision | vs. today |
|:---:|:---:|---|---|
| unset | n/a | **ALLOW** | unchanged — untainted fd, not in taint domain (G3) |
| **set** | **absent** | **DENY `-EPERM`** | **was ALLOW (the B3-1 bug)** — now fail-closed (G2) |
| set | present | enforce `max(colors) > sink_max_label ? DENY : ALLOW` | unchanged math, G4 |
| set | present, stale `pushed_ns` (> TTL) | **DENY `-EPERM`** | new — stale entry never silently passes |

The only behavioral change for legitimate traffic: a marked fd whose entry was
GC'd or never installed now denies instead of leaking. Because mark+push are
atomic (§3.1), the "marked but absent" state can only arise from GC/TTL or
attacker tampering — both are correctly fail-closed.

---

## 5. How this closes "no entry → default ALLOW"

### 5.1 The race window is eliminated (G1)

The attacker's exploit today is: install nothing → `write()` → hook sees
`entry is None` → ALLOW. Under Option (a):

1. The bridge issues `vos3_taint_mark_push()` **before** the tainted bytes can
   reach a writable fd / before the agent regains control on the egress path.
2. That single syscall sets `MARK(fd)` **and** installs the entry atomically.
3. Any concurrent attacker `write()` is ordered by the per-fd gate lock either
   *before* the commit (fd still **unmarked** → it cannot yet carry tainted
   bytes, ALLOW is safe) or *after* (fd **marked + entried** → full enforcement).

There is no longer a "marked, tainted, but entry-less" instant to race into.

### 5.2 The default flips to fail-closed (G2)

The dangerous branch — `entry is None → ALLOW` — is replaced for marked fds by
`entry absent → DENY`. The MOCK twin's `simulate_write()`
(`kernel_gate_connector.py:763-774`) is updated in lockstep: when a `(pid, fd)`
is in the **mark set** but has no color entry, it returns
`DecisionKind.DENY / errno=-13`, reason `"marked fd, no entry — fail-closed (B3-1)"`.
Unmarked fds keep the existing ALLOW path so untainted I/O is untouched (G3).

### 5.3 Untainted I/O is explicitly unaffected (G3)

An fd that never carried a `TaintedBuffer` is never marked, so it never enters
the gated ladder — no false `-EPERM` on ordinary file/socket writes. This is the
deliberate boundary that keeps the gate from breaking the whole system; it is
*not* a fail-open hole because such fds are, by construction, outside the
information-flow domain.

---

## 6. Contract mapping onto `taint_maps.h` (G5)

### 6.1 What is added vs. unchanged

| Element | Change | Why |
|---|---|---|
| `struct vos3_taint_color_entry` | **UNCHANGED** | preserves `_VALUE_FMT` packing; zero drift risk |
| `struct vos3_taint_map_key {u32 pid; u32 fd;}` | **UNCHANGED** | color map keying identical |
| `struct vos3_taint_mark_push_arg` | **NEW** (additive) | wraps fd + flags + embedded entry |
| MARK store record | **NEW** (sk/inode storage) | per-fd lifetime, GC on close |
| `VOS3_TAINT_IOC_MARK_PUSH`, `VOS3_SO_TAINT_MARK_PUSH` | **NEW** | atomic carriers |
| Pin paths (`VOS3_TAINT_MAP_PIN_PATH`, audit ring, prog) | **UNCHANGED** | bridge opens by path as today |

The "KEEP IN SYNC" triad (`taint_maps.h` ↔ `kernel_gate_connector.py` packers ↔
`sec/taint_gate.c`) gains exactly one new struct; the Python side adds a
`_pack_mark_push_arg(fd, flags, value_bytes)` that prepends the 16-byte header to
the **existing** `_pack_map_value()` output — so the value half stays
byte-identical and the contract test continues to assert the same packing.

### 6.2 The `max(colors)` invariant rides through unchanged (G4)

`vos3_taint_mark_push_arg.entry.max_color` MUST be the kernel/bridge-recomputed
`max(colors[0:length])`, exactly as `_push_one` produces today
(`kernel_gate_connector.py:673` → packed at `:702`). The kernel commit handler
**re-derives** `max(colors)` from the embedded array and **overwrites**
`entry.max_color` before storing — so even a malicious `mark_push` arg that lies
in `max_color` cannot under-report. PER_BYTE mode (`GateMode`/`TaintMode`,
`kernel_gate_connector.py:153-178`) is unaffected: the hook still walks
`colors[write_start:write_start+len]` with the verifier-clean `bpf_loop` dance
(§7) and takes the range max. The Sprint-2 lock holds verbatim.

---

## 7. Linux ≥ 5.17 eBPF-LSM Runner Matrix (exit MOCK-only)

The live half cannot be exercised on the current dev hosts (macOS/arm64 → MOCK;
`_detect_live_mode_capable()` requires Linux + bpffs + libbpf,
`kernel_gate_connector.py:464-487`). Sprint 19+ must stand up a CI matrix:

| Requirement | Reason | Min kernel |
|---|---|---|
| `bpf_lsm_file_permission` LSM hook | the write-gate attach point | 5.7 |
| `BPF_MAP_TYPE_INODE_STORAGE` | per-file MARK bit (§3.4) | 5.10 |
| `bpf_loop()` verifier helper | full 64 KiB per-byte scan without unroll-bomb (PER_BYTE mode) | **5.17** |
| `CONFIG_BPF_LSM=y` + `lsm=…,bpf` boot param | LSM program load | — |
| bpffs mounted at `/sys/fs/bpf` | pin paths in `taint_maps.h` | — |

→ **Runner matrix:** {Ubuntu 22.04 (5.15-hwe→5.17), Ubuntu 24.04 (6.8),
Fedora 40 (6.x)} × {`CONFIG_BPF_LSM=y`}. The 5.17 floor is set by `bpf_loop`
(per-byte mode); the PER_FD MVP gate alone needs only 5.7+5.10. Flip the runner
out of MOCK by setting `VOS3_TAINT_GATE_FORCE_LIVE=1`
(`kernel_gate_connector.py:472`) **only** on a kernel that satisfies the table —
never as a production default (G6).

> Until this matrix exists, B3 stays **characterized, not closed** — the MOCK
> twin proves the *policy*, not the *kernel enforcement*. Do not advance the moat
> on MOCK-only evidence.

---

## 8. Phasing (Sprint 19+)

| Phase | Deliverable | Gate |
|---|---|---|
| 19.0 | `vos3_taint_mark_push_arg` + ioctl/setsockopt skeleton; Python `_pack_mark_push_arg` + `mark_push()` API on `KernelGateConnector` | unit: packing round-trips byte-for-byte vs `taint_maps.h` |
| 19.1 | MARK store (sk/inode storage); LSM decision ladder (§4); MOCK twin fail-closed branch | B3 suite extended; `simulate_write` marked-no-entry ⇒ DENY |
| 19.2 | Atomic commit handler (`vos3_taint_mark_push_commit`) under per-fd lock; kernel re-derives `max(colors)` (G4) | live race test on ≥5.17 runner (§9) |
| 19.3 | GC on `close()`/`file_free`; TTL on stale `pushed_ns`; audit-ring DENY events | endurance: 1e6 mark/push/close cycles, zero leak |
| 19.4 | External eBPF/LSM audit (analogous to M3 Phase 6) | only then is the 49→50 moat advance honest |

---

## 9. Test Plan Additions (`backend/tests/audit/test_b3_ebpf_bridge.py` + LIVE)

1. **Atomicity (MOCK):** mark_push installs MARK and entry in one call; assert no
   intermediate "marked, no entry" state is ever observable via the connector API.
2. **Fail-closed (MOCK):** force a marked `(pid,fd)` with the color entry evicted →
   `simulate_write` returns DENY `-EPERM`, reason cites B3-1.
3. **Untainted pass-through (MOCK):** unmarked fd → ALLOW (no regression on G3).
4. **Lying `max_color` (MOCK):** `mark_push` arg with `entry.max_color` understated
   → kernel/commit re-derivation overrides; ceiling still enforced (G4).
5. **LIVE race (≥5.17 runner):** N threads hammer `write()` on a freshly
   mark-pushed fd carrying TOXIC bytes to a NETWORK_EGRESS sink; assert **zero**
   bytes egress (all `-EPERM`), proving the window is closed.
6. **Contract drift guard:** `struct.calcsize(_MARK_PUSH_FMT)` matches
   `sizeof(struct vos3_taint_mark_push_arg)` (compile a tiny C probe in the
   kernel host-runner, mirroring the existing A3/A8 twin pattern).

---

## 10. Open Questions / Honest Ceilings

- **fd resolution under ioctl:** resolving `arg.fd` relative to the caller's fd
  table must happen under the gate lock to avoid a fd-reuse race; the setsockopt
  carrier sidesteps this for sockets and is preferred where applicable.
- **inode_storage vs (pid,fd) skew:** files shared across `dup()`/`fork()` need
  the MARK keyed so a re-opened/inherited fd does not inherit a stale ALLOW;
  resolved by binding MARK to the inode-storage record and cross-checking the
  `(pid,fd)` color key on each hook invocation.
- **Native vOS path:** the bare-metal `CONFIG_VOS_NATIVE=y` build implements the
  same commit handler in-kernel C (per `taint_maps.h`); this spec's struct +
  ladder are shared, but the native syscall surface (not ioctl) is a 19.x
  follow-up.
- **This remains best-effort against a kernel-level attacker** — Option (a)
  closes the *userspace-race* ceiling only; full robustness against in-kernel
  compromise is out of scope (unchanged from the threat model, §1.1).

---

*Drafted hands-free against `main@b67e45c` for sz@aidg.com, 2026-06-13.
No code landed; this is the architectural blueprint for the Sprint 19+ B3-1
closure. Companion ledger: `docs/vOS_GA_1.3_Security_Audit_Master_Ledger.md`
(B3 row); freeze: `docs/SESSION_FREEZE_2026-06-13.md` §3/§7.*
