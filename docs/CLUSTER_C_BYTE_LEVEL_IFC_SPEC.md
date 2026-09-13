# Cluster C — Byte-Level Information Flow Control Technical Specification

**Date:** 2026-05-25
**Status:** DRAFT (awaiting CEO sign-off before implementation beyond hello-world)
**Owner:** Sprint 17 / Cluster C
**Predecessor:** Sprint 16 / C7 (`backend/security/ifc_engine.py` — blob-level high-watermark)
**Sources (May 25, 2026 horizon):**
  - [Fides — Securing AI Agents with Information-Flow Control](https://arxiv.org/abs/2505.23643) — confidentiality + integrity labels + selective hiding primitives
  - [Hybrid Information Flow Analysis for Python Bytecode (IEEE)](https://ieeexplore.ieee.org/document/7057995/) — bytecode-level taint design
  - [Mixed-mode Information Flow Tracking (IEEE 9346239)](https://ieeexplore.ieee.org/document/9346239/) — byte-granularity taint with moderate overhead
  - [Governing Dynamic Capabilities (arxiv 2603.14332)](https://arxiv.org/pdf/2603.14332) — cryptographic binding + reproducibility (BBS+, Groth16 DV-SNARK)
  - [The AI Agents of Tomorrow Need Data Integrity (IEEE Spectrum)](https://spectrum.ieee.org/data-integrity)

---

## 0. Why Cluster C

Sprint 16 / C7 ships **blob-level** taint (one label per `TaintedBlob`). The known limitation per the Fides paper + the C7 module's own honest-scope ceiling:

> "High-watermark over a whole blob falsely contaminates clean fields with a single UNTRUSTED byte. Byte-level tracking solves the false-positive class."

A real-world example: an agent fetches a web page that contains both a public document body AND an attacker-injected exfiltration payload. Under C7, the entire blob inherits the worst label (`UNTRUSTED` or `TOXIC`) and every byte downstream is contaminated. The privileged LLM's summary, even if it ignored the malicious payload, is also tainted — and refused at egress.

Cluster C upgrade: **per-byte color**. The clean document body retains its lower label; only the bytes the attacker injected carry `TOXIC`. The privileged LLM's summary inherits the max color of the bytes it actually references.

Three sub-features compose Cluster C:

| Sub-feature | What | Status target |
|---|---|---|
| **C-1** Byte-level coloring | `TaintedBuffer` with per-byte label array; high-watermark slicing | hello-world this commit |
| **C-2** Kernel-enforced write-gating | LSM-eBPF hook that consults the byte-level labels at egress syscall sites | spec only; impl in Sprint 18 |
| **C-3** Cryptographic declassification | Privileged-LLM-signed evidence that bytes have been reviewed → label downgrade | spec + Python prototype |

---

## 1. Data model

### 1.1 Color (per-byte)

Same enum as C7 `TaintLabel`:

```
PUBLIC      = 0
UNTRUSTED   = 1
SECRET      = 2
TOXIC       = 3
```

Per-byte `color[i]` is the label of the i-th byte.

### 1.2 TaintedBuffer

```python
@dataclass(frozen=True)
class TaintedBuffer:
    content: bytes
    colors: bytes              # len == len(content); each byte is a TaintLabel int
    provenance_chain: tuple[str, ...]
    sha256: str                # of content
    creation_ts: float
```

Invariant: `len(content) == len(colors)`. Color values constrained to `{0,1,2,3}`.

Memory cost: 1 byte of color per content byte (100% overhead). The Fides paper accepts this for the security property; for large model-weight payloads we exclude them from coloring (operator opt-out via `VOS3_IFC_NO_COLOR_GLOBS`).

### 1.3 BufferSlice / Multi-source Composition

Slicing inherits the slice's color range:

```python
sub = buffer[start:end]   # sub.colors == buffer.colors[start:end]
```

Concat raises the per-byte color of the resulting buffer to the max of constituent slices at each position. For non-overlapping concats (the common case) this is a simple `b"".join` of the `colors` arrays.

---

## 2. API surface (`backend/security/taint_engine_v2.py`)

```python
class ByteTaintEngine:
    def label_source(self, source_id: str, content: bytes,
                     label: TaintLabel = TaintLabel.UNTRUSTED
                     ) -> TaintedBuffer: ...

    def label_range(self, buffer: TaintedBuffer, start: int, end: int,
                     label: TaintLabel) -> TaintedBuffer:
        """Return a new buffer with [start:end) re-labeled."""

    def concat(self, buffers: Iterable[TaintedBuffer]) -> TaintedBuffer:
        """Per-byte max-color concat. Provenance unioned."""

    def slice(self, buffer: TaintedBuffer, start: int, end: int
               ) -> TaintedBuffer:
        """Per-byte color preserved on slice."""

    def max_color_in_range(self, buffer: TaintedBuffer,
                            start: int, end: int) -> TaintLabel: ...

    def check_egress(self, buffer: TaintedBuffer,
                      sink: SinkKind) -> EgressDecision:
        """Like C7's check_egress, but uses the MAX color in the buffer."""

    def declassify_range(self, buffer: TaintedBuffer, start: int, end: int,
                          target_label: TaintLabel, *,
                          evidence: DeclassEvidence) -> TaintedBuffer:
        """Cryptographically-evidenced declassification of a byte range."""

    def snapshot_stats() -> ByteTaintStats: ...
```

### 2.1 Backward compatibility with C7

`ByteTaintEngine` does NOT subclass or replace `TaintEngine` (C7). Both coexist:

- **C7 `TaintEngine`** stays as the blob-level fast path for callers that don't need per-byte fidelity (existing dual-LLM router, MCP bridge audit envelope).
- **`ByteTaintEngine`** is opt-in for callers that need per-byte fidelity (web-fetch result intake, document-parser output, MAIF artifact streams).

Conversion: `engine_v2.from_blob(c7_blob) -> TaintedBuffer` lifts a blob into byte-level with uniform color (same as the blob's single label).

---

## 3. C-3 — Cryptographic declassification

### 3.1 The problem

C7's `declassify()` records a textual marker in the provenance chain. An auditor reading the chain knows the declassification happened, but has no cryptographic proof that the bytes were actually reviewed. A bug or misconfigured policy can declassify TOXIC bytes silently.

### 3.2 Cluster C-3 spec

Declassification requires a signed `DeclassEvidence` from the privileged LLM (or an operator):

```python
@dataclass(frozen=True)
class DeclassEvidence:
    reviewer_id: str             # SPIFFE-ID or operator email
    reviewed_bytes_sha256: str   # SHA-256 of the bytes being downgraded
    reviewed_at: float
    target_label: int
    reason: str
    signature: bytes             # Ed25519 over the canonical evidence struct
    public_key: bytes            # Ed25519 pubkey of reviewer
```

The engine verifies:
1. `signature` matches the canonical-JSON of evidence (excluding signature itself), signed by `public_key`.
2. `reviewed_bytes_sha256` matches the SHA-256 of `buffer.content[start:end]`.
3. `reviewed_at` is within a 5-minute window of `time.time()`.

On failure: refuse the declassification + emit an audit event.

Per the Fides paper + the Governing Dynamic Capabilities paper:
- Ed25519 base path is the MVP (this hello-world spec).
- BBS+ selective-disclosure is the Sprint 18 extension (lets the reviewer prove "I reviewed bytes [10:50)" without revealing the bytes).
- Groth16 DV-SNARK is the Sprint 19+ extension (lets the reviewer prove "the bytes match policy X" without revealing the bytes OR the policy).

---

## 4. C-2 — Kernel-enforced write-gating (spec only)

Out of scope for this hello-world; full design tracked here for Sprint 18.

### 4.1 Design

eBPF LSM program (extending Sprint 16 / B3 `lsm_cap_gate`) consults a per-fd map: when a process writes to an egress-class fd (network socket, file under fscrypt-required dir, named pipe to user-stdout), the LSM hook:

1. Looks up the fd's "byte-color map" in a BPF map (populated by userspace when the buffer enters the kernel).
2. Computes the max color over the write range.
3. Returns deny if max color > sink policy max.

### 4.2 Why deferred

Requires:
- BPF map size budget (per-fd color array — bounded at ~64KB)
- New ioctl `vos3_ifc_attach_color_map(fd, color_bytes)` for userspace to push the color array
- Kernel module + eBPF program with constant-time per-byte max scan

All three are Sprint 18 / Wave 2 work. Hello-world (this PR) ships only the userspace engine.

---

## 5. Overhead budget

Per the LLM-inference IFC overhead research (May 2026):
- Dynamic IFC overhead in high-throughput inference: typically <3% to stay under SLA
- Byte-level shadow memory: ~100% memory overhead (1 color byte per content byte)
- Per-byte scan on hot path: O(N) where N=buffer length

vOS budget:
- Userspace `ByteTaintEngine` operations: P95 latency < 5% of the operation's data size in microseconds (e.g. 10KB buffer ≤ 500µs per op)
- Memory overhead: 100% (acceptable for non-weight buffers; weights opt-out)

Benchmark target captured in Sprint 18 follow-up using `infra/benchmarks/pq_tls_bench.py` harness pattern.

---

## 6. Honest scope ceilings (publish at v1.2 release)

1. **Byte-level coloring is Python-side only** in this prototype. Kernel-side enforcement (C-2) ships in Sprint 18.
2. **Declassification is Ed25519-base** in C-3 MVP. BBS+ + Groth16 are Sprint 18+ extensions.
3. **The Fides paper notes** byte-level IFC has FALSE-NEGATIVE risk on covert channels (timing, cache, etc.). Pair with the existing Sprint 16 controls: C2 dual-LLM, H1 combined egress, C7 declassification-on-review.
4. **No coverage for shared-memory channels** (Sprint 16 / H3 AF_VOS3_AGENT) — those carry their own labels via the socket family; byte-level fidelity within those channels is Sprint 18 work.
5. **Memory cost is 100% overhead** on coloring; large model-weight buffers opt out via `VOS3_IFC_NO_COLOR_GLOBS` env (e.g. `*.gguf,*.safetensors,*.onnx`).
6. **Concurrent writers to a TaintedBuffer** are not coordinated by the engine. Buffers are immutable; mutations create new buffers. Caller code that mutates the underlying bytes outside the engine (e.g. by holding a reference + writing in-place) breaks the invariant.

---

## 7. Sub-feature shipping order

| Phase | Sub-feature | This PR? |
|---|---|---|
| 1 | C-1 byte-level coloring (hello-world) — `taint_engine_v2.py` + tests | ✅ YES |
| 2 | C-3 Ed25519 declassification — minimal `DeclassEvidence` + verify path | ⚠️ partial — interface only; full Ed25519 verify is small follow-up |
| 3 | C-2 kernel eBPF LSM write-gate | ❌ Sprint 18 |
| 4 | C-3 BBS+ selective disclosure | ❌ Sprint 19 |
| 5 | C-3 Groth16 DV-SNARK proof | ❌ Sprint 19+ |

---

## 8. Open questions for CEO

| # | Question | Default |
|---|---|---|
| 1 | Do we ship C-1 hello-world this week, or hold until C-3 Ed25519 is also done? | ship now; declassification is small follow-up |
| 2 | Memory budget: 100% byte-color overhead acceptable for non-weight buffers? | yes (model weights opt out via env) |
| 3 | Coexistence: keep C7 `TaintEngine` indefinitely, or deprecate after C-1 lands? | KEEP both — C7 is the fast path for callers that don't need per-byte |
| 4 | Cluster B (Identity Federation) vs Cluster C ordering — do C first or B first? | C first; it's pure software + doesn't wait for IETF draft ratification |

---

**Status:** spec drafted; hello-world `taint_engine_v2.py` lands in the same commit.
