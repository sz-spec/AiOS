/**
 * @file taint_maps.h
 * @brief vOS Cluster C-2 — Byte-level taint label map shared
 *        between userspace (backend/security/kernel_gate_connector.py)
 *        and the eBPF LSM write-gate program (sec/taint_gate.c).
 *
 * Sprint 17 / Wave 2 / Cluster C-2 (kernel-enforced write-gating).
 *
 * Why this exists
 * ---------------
 *
 * Sprint 17 / Cluster C-1 ships per-byte taint coloring inside the
 * Python userspace (backend/security/taint_engine_v2.py) and wires it
 * at the DualLLMRouter chokepoint. Every agent tool result now
 * carries a TaintedBuffer with a parallel `colors[]` array.
 *
 * Userspace-only enforcement is necessary but NOT sufficient: a
 * compromised agent can bypass Python-level egress checks by writing
 * the same bytes directly to a file descriptor through any syscall
 * the kernel doesn't gate. The Fides paper (arxiv 2505.23643) is
 * explicit on this point — byte-level IFC requires kernel cooperation
 * to remain robust against an attacker inside the agent process.
 *
 * C-2 closes this gap by:
 *
 *   1. Defining a SHARED eBPF map (the structures in this header)
 *      that userspace populates BEFORE issuing a write() / sendmsg()
 *      on a tainted file descriptor.
 *
 *   2. Attaching an eBPF LSM program (sec/taint_gate.c) to
 *      `bpf_lsm_file_permission` (Linux ≥ 5.7) and equivalent
 *      socket hooks. The hook reads the per-fd color map, computes
 *      the max color over the write range, and returns -EPERM if
 *      the max color exceeds the per-fd sink-policy ceiling.
 *
 *   3. Providing a Python bridge (backend/security/
 *      kernel_gate_connector.py) that pushes byte-color arrays via
 *      bpf(BPF_MAP_UPDATE_ELEM) keyed by (pid, fd).
 *
 * Public surface (this header)
 * ----------------------------
 *
 *   - enum vos3_taint_label                — mirrors TaintLabel (C7 + C-1)
 *   - enum vos3_taint_sink_kind            — mirrors SinkKind
 *   - struct vos3_taint_color_entry        — map value: per-fd color array
 *   - struct vos3_taint_decision_event    — ring-buffer event for audit
 *   - VOS3_TAINT_MAX_COLOR_BYTES           — per-fd color budget (64 KB)
 *   - VOS3_TAINT_MAP_MAX_ENTRIES           — max concurrent gated fds
 *   - VOS3_TAINT_PIN_PATH                  — well-known bpffs pin path
 *
 * Honest scope ceiling
 * --------------------
 *
 *   - This header is the SHARED CONTRACT only. The eBPF program
 *     itself is `kernel/src/sec/taint_gate.c` (skeleton in this
 *     commit; verifier-clean MVP in Sprint 18).
 *   - Per-byte color is bounded at 64 KB per fd (VOS3_TAINT_MAX_COLOR_
 *     BYTES) because of the eBPF verifier's stack budget + the
 *     per-entry size limit on BPF_MAP_TYPE_PERCPU_ARRAY. Buffers
 *     >64 KB chunk to multiple map entries or fall back to per-fd
 *     max-color mode.
 *   - vOS native kernel build (CONFIG_VOS_NATIVE=y on bare metal)
 *     uses an in-kernel C implementation of the same hook, sharing
 *     this header. The eBPF path is for hosted Linux deployments
 *     (sandbox + dev modes) where vOS runs as a userspace orchestrator
 *     above a Linux kernel.
 *   - The map structure here is INTENTIONALLY simple — no
 *     provenance, no signature. Forensic provenance lives in the
 *     userspace TaintedBuffer (Cluster C-1). The kernel side only
 *     enforces the egress policy; auditors correlate fd events back
 *     to TaintedBuffer SHAs via the audit ring.
 */

#ifndef VOS_TAINT_MAPS_H
#define VOS_TAINT_MAPS_H

#include <stdint.h>

/* ============================================================================
 * Label model — KEEP IN SYNC with:
 *   - backend/security/ifc_engine.py    (C7 TaintLabel)
 *   - backend/security/taint_engine_v2.py (C-1 TaintLabel)
 *   - backend/security/kernel_gate_connector.py (C-2 bridge)
 * ============================================================================
 */

enum vos3_taint_label {
    VOS3_TAINT_PUBLIC    = 0,
    VOS3_TAINT_UNTRUSTED = 1,
    VOS3_TAINT_SECRET    = 2,
    VOS3_TAINT_TOXIC     = 3,
};

enum vos3_taint_sink_kind {
    VOS3_SINK_NETWORK_EGRESS = 0,
    VOS3_SINK_FILE_WRITE     = 1,
    VOS3_SINK_USER_STDOUT    = 2,
    VOS3_SINK_AUDIT_LOG      = 3,   /* always allowed, per C7/C-1 contract */
};

/* ============================================================================
 * Map sizing — eBPF verifier-safe bounds.
 *
 * Reasoning:
 *  - The verifier rejects unbounded-loop programs. The taint-check
 *    inner loop is `for (i = 0; i < write_len && i < MAX; i++)` and
 *    the verifier needs MAX to be a compile-time constant.
 *  - BPF_MAP_TYPE_HASH per-value size limit is set by the kernel
 *    (typically a few MB), but stack budget in an LSM program is
 *    512 bytes — anything bigger must live in the map value, and
 *    iterate via a bounded index over the map.
 *  - 64 KB per-fd matches the typical socket buffer / TLS record
 *    boundary. Writes larger than 64 KB get chunked at the userspace
 *    bridge before push.
 *  - 1024 concurrent gated fds is well below the kernel's typical
 *    fd-table ceiling (~64K) and well within the BPF map memory
 *    accounting limit (~32 MB total at default RLIMIT_MEMLOCK).
 * ============================================================================
 */

#define VOS3_TAINT_MAX_COLOR_BYTES   (64 * 1024)
#define VOS3_TAINT_MAP_MAX_ENTRIES   1024

/* ============================================================================
 * Map key — identifies the (pid, fd) being gated.
 *
 * The bridge pushes a fresh entry per (pid, fd) on EVERY write that
 * carries a TaintedBuffer. The eBPF LSM hook looks up by the same
 * key on file_permission(MAY_WRITE). Entries are GC'd on close()
 * via the LSM file_free hook (Sprint 18 work).
 * ============================================================================
 */

struct vos3_taint_map_key {
    uint32_t pid;
    uint32_t fd;
};

/* ============================================================================
 * Map value — the per-fd color array + sink policy.
 *
 * The userspace bridge fills `colors[0:length]` and zeroes the
 * remaining bytes. `length` may be less than VOS3_TAINT_MAX_COLOR_
 * BYTES if the underlying write is smaller.
 *
 * `sink_max_label` is the egress-policy ceiling for this fd kind:
 *   - NETWORK_EGRESS:  UNTRUSTED  (TOXIC + SECRET deny)
 *   - FILE_WRITE:      SECRET     (TOXIC denies)
 *   - USER_STDOUT:     SECRET
 *   - AUDIT_LOG:       TOXIC
 *
 * `mode` selects per-byte (0) or per-fd max-color (1). MVP ships
 * per-fd; per-byte is the Sprint-18 follow-up (needs a bounded-loop
 * verifier dance that's still being prototyped).
 * ============================================================================
 */

#define VOS3_TAINT_MODE_PER_BYTE  0
#define VOS3_TAINT_MODE_PER_FD    1

struct vos3_taint_color_entry {
    /* per-byte color array; valid in [0, length) */
    uint8_t  colors[VOS3_TAINT_MAX_COLOR_BYTES];
    uint32_t length;

    /* per-fd convenience scalar (max color over the buffer) */
    uint8_t  max_color;

    /* sink-policy ceiling for the fd kind */
    uint8_t  sink_kind;       /* enum vos3_taint_sink_kind */
    uint8_t  sink_max_label;  /* enum vos3_taint_label */

    /* hook selection */
    uint8_t  mode;            /* VOS3_TAINT_MODE_{PER_BYTE,PER_FD} */

    /* userspace bookkeeping — kernel does NOT interpret */
    uint64_t buffer_sha256_low;
    uint64_t buffer_sha256_high;

    /* timestamp (ns since boot) when the entry was pushed */
    uint64_t pushed_ns;
} __attribute__((packed));

/* ============================================================================
 * Audit event — written to a ring buffer on every gate decision.
 *
 * Userspace (audit_ring consumer in backend) reads these to record:
 *   "fd=X pid=Y attempted to write N bytes max-color=Z; decision=ALLOW/DENY"
 *
 * The SHA-256 prefix lets the userspace correlate to the originating
 * TaintedBuffer in the Python audit log without leaking the buffer
 * itself to the kernel.
 * ============================================================================
 */

#define VOS3_TAINT_DECISION_ALLOW  0
#define VOS3_TAINT_DECISION_DENY   1

struct vos3_taint_decision_event {
    uint64_t timestamp_ns;
    uint32_t pid;
    uint32_t fd;
    uint32_t length;
    uint8_t  max_color;
    uint8_t  sink_kind;
    uint8_t  sink_max_label;
    uint8_t  decision;              /* VOS3_TAINT_DECISION_{ALLOW,DENY} */
    uint64_t buffer_sha256_low;
    uint64_t buffer_sha256_high;
} __attribute__((packed));

/* ============================================================================
 * Well-known bpffs pin paths.
 *
 * The userspace bridge opens these by path; the eBPF program pins
 * them in its sec("license") init code. The convention matches the
 * Sprint 16 B3 lsm_cap_gate skeleton (kept under /sys/fs/bpf/vos3/).
 * ============================================================================
 */

#define VOS3_TAINT_MAP_PIN_PATH       "/sys/fs/bpf/vos3/taint_colors"
#define VOS3_TAINT_AUDIT_RING_PATH    "/sys/fs/bpf/vos3/taint_audit_ring"
#define VOS3_TAINT_LSM_PROG_PIN_PATH  "/sys/fs/bpf/vos3/taint_gate_prog"
#define VOS3_TAINT_MARKS_PIN_PATH     "/sys/fs/bpf/vos3/taint_marks"

/* ============================================================================
 * Atomic Mark-and-Push — Finding B3-1 closure (Option (a), Sprint 19).
 *
 * The legacy flow pushes a color entry with BPF_MAP_UPDATE_ELEM and lets the
 * write() race ahead of it ("no entry -> default ALLOW", the B3-1 fail-open).
 * This control structure collapses "mark the fd as gated" and "install the
 * color entry" into ONE syscall, committed under a single per-fd critical
 * section: by the time the call returns the fd is BOTH marked AND has its
 * entry, so there is no observable window, and a MARKED fd with no entry
 * fails CLOSED. See docs/design/vOS_B31_eBPF_Race_Closure_Spec.md (§3-§6).
 *
 * Invariant G5: `entry` is the UNCHANGED struct vos3_taint_color_entry, so the
 * value half is byte-identical to what BPF_MAP_UPDATE_ELEM installs today.
 *   sizeof(struct vos3_taint_mark_push_arg) == 16 + 65568 == 65584.
 * ============================================================================
 */

#define VOS3_TAINT_MARK_PUSH_ABI  1u   /* bump on ANY field/layout change */

#define VOS3_TAINT_MP_REPLACE  (1u << 0)  /* overwrite an existing entry */
#define VOS3_TAINT_MP_ONESHOT  (1u << 1)  /* auto-clear the entry after one write */

struct vos3_taint_mark_push_arg {
    uint32_t abi_version;   /* == VOS3_TAINT_MARK_PUSH_ABI, else -EINVAL */
    uint32_t fd;            /* fd to mark + gate (ignored on the setsockopt carrier) */
    uint32_t flags;         /* VOS3_TAINT_MP_* */
    uint32_t _pad;          /* explicit; preserves 8-byte alignment of `entry` */
    struct vos3_taint_color_entry entry;  /* UNCHANGED layout (invariant G5) */
} __attribute__((packed));

/*
 * ioctl request code for the control-device carrier.
 *
 * DEVIATION FROM THE SPEC DRAFT (intentional): the blueprint wrote
 *   _IOW('T', 0x44, struct vos3_taint_mark_push_arg)
 * but the _IOC size field is only ~14 bits (Linux) / 13 bits (BSD), so a
 * 65584-byte struct OVERFLOWS it and the size is silently truncated. The
 * correct idiom for a >16 KiB argument is to encode a POINTER (sizeof == 8)
 * and copy_from_user() the full struct in the handler. Hence the `*` below.
 *
 * Guarded by __has_include so the freestanding vOS kernel build and the host
 * struct-probe (which lack <sys/ioctl.h>) still get the struct definition. The
 * encoded value only has to agree between the bridge and the kernel on the
 * Linux target; the host probe just needs the macro to compile + be nonzero.
 * Skipped under the eBPF build (__VOS3_TAINT_GATE_REAL_BPF_BUILD): the ioctl
 * request code is a userspace-only concern, and pulling <sys/ioctl.h> into a
 * -target bpf object is both unnecessary and a source of header conflicts.
 */
#if defined(__has_include) && !defined(__VOS3_TAINT_GATE_REAL_BPF_BUILD)
#  if __has_include(<sys/ioctl.h>)
#    include <sys/ioctl.h>
#    define VOS3_TAINT_IOC_MARK_PUSH \
         _IOW('T', 0x44, struct vos3_taint_mark_push_arg *)
#  endif
#endif

/* ============================================================================
 * Helper — encode/decode SHA-256 into the two u64 fields.
 *
 * We store the FIRST 16 bytes of the SHA-256 (high + low). 16 bytes
 * gives ≈ 2^64 collision resistance which is sufficient for the
 * audit-correlation use case (we don't sign-with this).
 * ============================================================================
 */

static inline void
vos3_taint_sha_pack(const uint8_t sha256[32],
                    uint64_t *high, uint64_t *low)
{
    *high = 0; *low = 0;
    for (int i = 0; i < 8; i++) {
        *high = (*high << 8) | sha256[i];
        *low  = (*low  << 8) | sha256[i + 8];
    }
}

#endif /* VOS_TAINT_MAPS_H */
