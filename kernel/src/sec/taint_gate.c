/**
 * @file taint_gate.c
 * @brief vOS Cluster C-2 — eBPF LSM byte-level write-gate program (SKELETON).
 *
 * Sprint 17 / Wave 2 / Cluster C-2 (kernel-enforced write-gating).
 *
 * THIS FILE IS A SKELETON / SCAFFOLDING.
 * --------------------------------------
 *
 * It defines the eBPF LSM program structure, the map declarations,
 * and the hook signatures. The verifier-clean MVP that PASSES
 * `bpftool prog load` against a real Linux ≥ 5.7 kernel ships in
 * Sprint 18. The reason for shipping the skeleton ahead of the
 * verifier-clean MVP:
 *
 *   1. It gives the userspace bridge (kernel_gate_connector.py) a
 *      concrete compile target — the map struct + key/value layout
 *      are STABLE as of this commit.
 *
 *   2. It documents the design decisions (per-fd vs per-byte mode,
 *      map sizing, hook attach points) in the same review pass as
 *      the userspace bridge, so a single PR captures the full
 *      contract.
 *
 *   3. The kernel build system (kernel/Makefile) does NOT yet
 *      compile this file. Build-out is part of the Sprint 18
 *      kernel-side work (requires clang -target bpf, libbpf-cargo,
 *      and the existing kernel-makefile's `CROSS=` toolchain bump).
 *
 * Compile target (Sprint 18 follow-up)
 * ------------------------------------
 *
 *     clang -O2 -g -target bpf \
 *           -I kernel/include \
 *           -c kernel/src/sec/taint_gate.c \
 *           -o kernel/build/bpf/taint_gate.bpf.o
 *
 * Then load + attach via libbpf:
 *
 *     bpftool prog load \
 *         kernel/build/bpf/taint_gate.bpf.o \
 *         /sys/fs/bpf/vos3/taint_gate_prog \
 *         autoattach
 *
 * Honest scope ceilings
 * ---------------------
 *
 *   - This file is INTENTIONALLY un-compilable today: it uses libbpf
 *     headers (`<linux/bpf.h>`, `<bpf/bpf_helpers.h>`, ...) that
 *     aren't part of the vOS kernel build. Compiling it requires the
 *     Linux libbpf headers AND the clang BPF target. CI does not
 *     attempt to build it. The userspace bridge (Python) calls
 *     bpf(2) directly using ctypes when running on Linux hosts; on
 *     macOS dev the bridge runs in MOCK mode (no real syscall).
 *
 *   - The skeleton uses `BPF_MAP_TYPE_HASH` for the color map.
 *     A real Sprint-18 implementation may switch to
 *     `BPF_MAP_TYPE_LRU_HASH` for automatic GC on close().
 *
 *   - Per-byte enforcement requires a bounded inner loop over the
 *     write range; the verifier accepts this only with explicit
 *     bound annotations. PER_FD MAX-color mode is the cheap,
 *     verifier-trivial default; the verifier-clean PER_BYTE "loop
 *     dance" over the full 64 KiB budget landed in Sprint 20 /
 *     Primitive (b) — see vos3_max_color_per_byte() below.
 *
 * References
 * ----------
 *
 *   - docs.kernel.org/bpf/prog_lsm.html  (LSM BPF programs)
 *   - The eBPF Runtime in the Linux Kernel — arxiv 2410.00026
 *   - Fides paper — arxiv 2505.23643 (the IFC kernel-cooperation argument)
 *   - vOS Cluster C spec — docs/CLUSTER_C_BYTE_LEVEL_IFC_SPEC.md
 *
 * Hook selection
 * --------------
 *
 *   bpf_lsm_file_permission(file, mask)
 *     Fires on every read/write attempt against an open file. We
 *     gate only when (mask & MAY_WRITE) is set.
 *
 *   bpf_lsm_socket_sendmsg(sock, msg, size)
 *     Fires on network egress (the highest-stakes sink for AI agents).
 *
 *   bpf_lsm_file_open(file)
 *     Used in Sprint 18 to initialize a per-fd entry on open(); not
 *     wired here.
 */

#ifndef __VOS3_TAINT_GATE_BPF__
#define __VOS3_TAINT_GATE_BPF__
#endif

/* ----------------------------------------------------------------------- */
/* libbpf headers — provided by clang -target bpf + the host libbpf dev    */
/* package. NOT part of the vOS kernel build today.                        */
/* ----------------------------------------------------------------------- */

#ifdef __VOS3_TAINT_GATE_REAL_BPF_BUILD  /* gated so the host build skips */

/* CO-RE build (Phase 19.6 Stage B). vmlinux.h provides the full kernel type
 * layout (task_struct / files_struct / fdtable / file) needed for the fd
 * resolution walk below. Do NOT include <linux/bpf.h> or <linux/errno.h>
 * alongside vmlinux.h — they redefine BTF-provided UAPI types and clash.
 * vmlinux.h is generated at build time from /sys/kernel/btf/vmlinux (see
 * infra/runners/build_in_container.sh). */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_tracing.h>

#ifndef EPERM
#define EPERM 1   /* <linux/errno.h> is intentionally not included here */
#endif

#include "../../include/vos/taint_maps.h"

char LICENSE[] SEC("license") = "GPL";

/* ============================================================================
 * Map declarations
 * ============================================================================ */

/* Per-fd byte-color map. Keyed by (pid, fd); value is the color
 * array + sink policy entry from taint_maps.h. */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __type(key,   struct vos3_taint_map_key);
    __type(value, struct vos3_taint_color_entry);
    __uint(max_entries, VOS3_TAINT_MAP_MAX_ENTRIES);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} taint_colors SEC(".maps");

/* Audit ring buffer — userspace tail-reads these. */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 1 << 20);   /* 1 MB ring */
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} taint_audit_ring SEC(".maps");

/* Per-fd MARK set — Finding B3-1 closure (Option (a)).
 *
 * A (pid, fd) present here is "gated": the userspace bridge committed it via
 * VOS3_TAINT_IOC_MARK_PUSH (atomically with its taint_colors entry). A MARKED
 * key whose taint_colors entry is MISSING fails CLOSED in the hooks below —
 * this is what kills the "no entry -> default ALLOW" race. An UNMARKED key
 * never carried a TaintedBuffer, so it stays default-allow (untainted I/O,
 * invariant G3). The Sprint-19 production build keys this on inode/sk-storage
 * (lifetime bound to the fd, auto-GC on close); the skeleton uses a HASH. */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __type(key,   struct vos3_taint_map_key);
    __type(value, uint8_t);
    __uint(max_entries, VOS3_TAINT_MAP_MAX_ENTRIES);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} taint_marks SEC(".maps");

/* Phase 19.6 — PID-namespace config (Finding B3-1, fixes the 19.5b drift).
 *
 * `bpf_get_current_pid_tgid()` returns INIT-ns ids, but the userspace bridge
 * runs inside a container and marks CONTAINER-ns pids -> the keys never matched
 * (proven empirically in 19.5b: kernel saw host pid 73324, bridge marked 19).
 * The bridge publishes the (dev, ino) of its pid-namespace (stat of
 * /proc/self/ns/pid) into slot 0 here; the hooks then resolve the caller's pid
 * AS SEEN IN that namespace via bpf_get_ns_current_pid_tgid(). dev==ino==0
 * (unset) falls back to the init-ns id, so the program still works under
 * --pid=host or on a bare host. */
struct vos3_taint_pidns_cfg {
    __u64 dev;
    __u64 ino;
};
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __type(key, __u32);
    __type(value, struct vos3_taint_pidns_cfg);
    __uint(max_entries, 1);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} taint_config SEC(".maps");

/* Phase 22 — transactional freeze (BPF spin-lock) for cross-map update
 * consistency. The mark/colors update is two map writes from userspace; the
 * existing ladder already fails CLOSED on the marked-no-colors intermediate,
 * but this makes the barrier EXPLICIT and ordering-independent: the bridge sets
 * in_flight=1 BEFORE its two-step and =0 AFTER, under the same lock the hook
 * reads. A MARKED fd observed mid-update freezes fail-closed atomically — no
 * UAPI/G5 layout change (this map is private to taint_gate.c, not taint_maps.h).
 *
 * Verifier rules honoured: bpf_spin_lock may live only in a map value, only one
 * lock held at a time, and NO other helper calls inside the critical section —
 * so the section only reads the flag, then unlocks before any map walk. */
struct vos3_taint_txn {
    struct bpf_spin_lock lock;
    __u32 in_flight;
};
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __type(key, __u32);
    __type(value, struct vos3_taint_txn);
    __uint(max_entries, 1);
    __uint(pinning, LIBBPF_PIN_BY_NAME);
} taint_txn SEC(".maps");

/* ============================================================================
 * Helpers
 * ============================================================================ */

/* True iff the userspace bridge is mid two-step update (in_flight != 0). Read
 * under the spin-lock so it can never observe a torn flag. No other helper is
 * called while the lock is held (verifier requirement). */
static __always_inline int
vos3_txn_frozen(void)
{
    __u32 zero = 0;
    struct vos3_taint_txn *t = bpf_map_lookup_elem(&taint_txn, &zero);
    if (!t)
        return 0;
    int frozen;
    bpf_spin_lock(&t->lock);
    frozen = t->in_flight != 0;
    bpf_spin_unlock(&t->lock);
    return frozen;
}

/* Resolve the caller's TGID in the bridge's pid-namespace (Phase 19.6). Falls
 * back to the init-ns TGID when no pidns is configured. No loops / no CO-RE ->
 * verifier-trivial. */
static __always_inline __u32
vos3_resolve_tgid(void)
{
    __u32 host = (__u32)(bpf_get_current_pid_tgid() >> 32);
    __u32 zero = 0;
    struct vos3_taint_pidns_cfg *cfg = bpf_map_lookup_elem(&taint_config, &zero);
    if (cfg && (cfg->dev || cfg->ino)) {
        struct bpf_pidns_info ns = {};
        long rc = bpf_get_ns_current_pid_tgid(cfg->dev, cfg->ino, &ns, sizeof(ns));
#ifdef VOS3_TAINT_BPF_DEBUG
        if (rc != 0 || ns.tgid < 2000)
            bpf_printk("vos3_ns rc=%ld nstgid=%u host=%u", rc, ns.tgid, host);
#endif
        if (rc == 0)
            return ns.tgid;
    }
    return host;
}

static __always_inline uint8_t
vos3_max_color_per_fd(const struct vos3_taint_color_entry *e)
{
    /* In PER_FD mode the userspace bridge has already computed
     * the max; we just return it. The verifier loves this because
     * there's no loop. */
    return e->max_color;
}

/* ============================================================================
 * Per-byte mode — Sprint 20 / Primitive (b): the verifier-clean "loop
 * dance" over the FULL colors[] array (up to the 64 KiB per-fd budget),
 * not the old 4096-byte single-bpf_loop cap.
 *
 * The design (docs/V1.3_BACKLOG_MASTER_PLAN.md §5.b):
 *
 *   OUTER  bpf_loop(VOS3_TAINT_PERBYTE_NCHUNKS, scan_chunk, &ctx, 0)
 *          where NCHUNKS is a COMPILE-TIME constant (64 KiB / CHUNK).
 *   INNER  #pragma unroll over a CONSTANT CHUNK, every colors[] access
 *          masked with VOS3_TAINT_COLORS_MASK (== budget-1) AND
 *          length-guarded against the ragged tail.
 *   EARLY-EXIT  the first byte whose label exceeds the sink ceiling sets
 *          deny_off and returns 1 from the outer callback, so bpf_loop
 *          stops. Clean traffic pays ~O(prefix), not O(len).
 *
 * Why this is verifier-clean where a naive loop is not:
 *   1. The outer trip count is a CONSTANT (NCHUNKS), so bpf_loop's
 *      max_iterations is statically known — the verifier accepts it.
 *   2. The inner loop is #pragma unroll'ed with a CONSTANT CHUNK, so it
 *      contributes a fixed, small instruction count (NCHUNKS is the
 *      bound that scales, and it lives in bpf_loop, not in unrolled
 *      code — so program size stays well under the 1M-insn limit).
 *   3. Every colors[] index is `(base + i) & VOS3_TAINT_COLORS_MASK`,
 *      which the verifier can prove lies in [0, 64 KiB) WITHOUT a
 *      runtime-variable bound it would otherwise reject. The separate
 *      length guard (`>= ctx->limit → break`) handles correctness for
 *      the ragged tail; the mask handles SAFETY for the verifier.
 *
 * EchoLeak defense (unchanged contract, now over the whole buffer):
 *   a buffer with a clean UNTRUSTED prefix and a TOXIC tail is allowed
 *   to egress ONLY the prefix — if the write's iov range (socket_sendmsg
 *   `size`) doesn't reach the TOXIC bytes, deny_off is never set.
 *
 * Honest scope ceilings:
 *   - bpf_loop() requires kernel ≥ 5.17. On older kernels the per-byte
 *     path returns -EPERM defensively (over-refuse beats under-protect).
 *   - Coverage is the 64 KiB per-fd color budget. Writes whose tainted
 *     content exceeds 64 KiB are chunked into multiple map entries at
 *     the userspace bridge (push_tainted_buffer), each gated on its own.
 * ============================================================================
 */

#define VOS3_TAINT_PERBYTE_CHUNK    256u
#define VOS3_TAINT_PERBYTE_MAX      VOS3_TAINT_MAX_COLOR_BYTES  /* 64 KiB */
#define VOS3_TAINT_PERBYTE_NCHUNKS  (VOS3_TAINT_PERBYTE_MAX / VOS3_TAINT_PERBYTE_CHUNK)
#define VOS3_TAINT_COLORS_MASK      (VOS3_TAINT_PERBYTE_MAX - 1u)  /* 0xFFFF */
#define VOS3_TAINT_NO_DENY          ((uint32_t)-1)

struct vos3_perbyte_ctx {
    const struct vos3_taint_color_entry *entry;
    uint32_t limit;       /* min(write_length, entry->length, 64 KiB) */
    uint8_t  ceiling;     /* sink_max_label */
    uint8_t  max_color;
    uint32_t deny_off;    /* first offset exceeding ceiling, or NO_DENY */
};

static long
vos3_perbyte_chunk_cb(uint32_t chunk_idx, void *ctx)
{
    struct vos3_perbyte_ctx *c = ctx;
    uint32_t base = chunk_idx * VOS3_TAINT_PERBYTE_CHUNK;
    if (base >= c->limit) return 1;   /* past the write range — stop */

    /* CONSTANT trip count → fully unrolled; masked + length-guarded. */
    #pragma unroll
    for (uint32_t i = 0; i < VOS3_TAINT_PERBYTE_CHUNK; i++) {
        uint32_t pos = base + i;
        if (pos >= c->limit) break;                 /* ragged-tail guard */
        uint32_t off = pos & VOS3_TAINT_COLORS_MASK;/* verifier in-bounds */
        uint8_t v = c->entry->colors[off];
        if (v > c->max_color) c->max_color = v;
        if (v > c->ceiling) {                        /* first toxic byte */
            c->deny_off = pos;
            return 1;                                /* early-exit OUTER  */
        }
    }
    return 0;   /* continue to next chunk */
}

static __always_inline uint8_t
vos3_max_color_per_byte(const struct vos3_taint_color_entry *e,
                        uint32_t write_length, uint8_t ceiling,
                        uint32_t *deny_off_out)
{
    struct vos3_perbyte_ctx ctx = {
        .entry = e,
        .limit = write_length,
        .ceiling = ceiling,
        .max_color = 0,
        .deny_off = VOS3_TAINT_NO_DENY,
    };
    if (ctx.limit > e->length) ctx.limit = e->length;
    if (ctx.limit > VOS3_TAINT_PERBYTE_MAX) ctx.limit = VOS3_TAINT_PERBYTE_MAX;

    bpf_loop(VOS3_TAINT_PERBYTE_NCHUNKS, vos3_perbyte_chunk_cb, &ctx, 0);
    if (deny_off_out) *deny_off_out = ctx.deny_off;
    return ctx.max_color;
}

static __always_inline int
vos3_compare_to_sink(uint8_t max_color, uint8_t sink_max_label)
{
    /* Returns 0 if allowed, -EPERM if denied. */
    if (max_color > sink_max_label) {
        return -EPERM;
    }
    return 0;
}

static __always_inline void
vos3_emit_audit(const struct vos3_taint_color_entry *e,
                struct vos3_taint_map_key key,
                uint32_t write_len,
                int decision)
{
    struct vos3_taint_decision_event *ev =
        bpf_ringbuf_reserve(&taint_audit_ring, sizeof(*ev), 0);
    if (!ev) return;

    ev->timestamp_ns        = bpf_ktime_get_ns();
    ev->pid                 = key.pid;
    ev->fd                  = key.fd;
    ev->length              = write_len;
    ev->max_color           = e->max_color;
    ev->sink_kind           = e->sink_kind;
    ev->sink_max_label      = e->sink_max_label;
    ev->decision            = (decision == 0)
                                ? VOS3_TAINT_DECISION_ALLOW
                                : VOS3_TAINT_DECISION_DENY;
    ev->buffer_sha256_low   = e->buffer_sha256_low;
    ev->buffer_sha256_high  = e->buffer_sha256_high;

    bpf_ringbuf_submit(ev, 0);
}

/* Phase 28 (Gap G10) — STALE-SLOT deny audit.
 *
 * Emitted on the MARKED-but-no-color-entry path: the userspace bridge deleted
 * this fd's color entry via a token-rotation flush (invalidate_slot ->
 * bpf(BPF_MAP_DELETE_ELEM) on taint_colors) while leaving the MARK, so the slot
 * is STALE and we fail closed. There is no entry to read sink/sha from, so this
 * records the (pid, fd) + a DENY verdict with zeroed policy fields — enough for
 * userspace to see "a rotated/revoked credential's slot was used after the
 * flush" instead of the deny being silent. Same verifier-safe reserve/submit
 * pattern as vos3_emit_audit. */
static __always_inline void
vos3_emit_stale_deny(struct vos3_taint_map_key key, uint32_t write_len)
{
    struct vos3_taint_decision_event *ev =
        bpf_ringbuf_reserve(&taint_audit_ring, sizeof(*ev), 0);
    if (!ev) return;

    ev->timestamp_ns        = bpf_ktime_get_ns();
    ev->pid                 = key.pid;
    ev->fd                  = key.fd;
    ev->length              = write_len;
    ev->max_color           = 0;
    ev->sink_kind           = 0;
    ev->sink_max_label      = 0;
    ev->decision            = VOS3_TAINT_DECISION_DENY;
    ev->buffer_sha256_low   = 0;
    ev->buffer_sha256_high  = 0;

    bpf_ringbuf_submit(ev, 0);
}

/* ============================================================================
 * LSM hook — file_permission(MAY_WRITE).
 *
 * Fires on every write attempt against an open file descriptor.
 * If the userspace bridge has pushed a color entry for this (pid, fd),
 * we evaluate the policy and return -EPERM on violation.
 *
 * If no entry exists for the (pid, fd), we ALLOW (default-allow on
 * miss — the bridge is responsible for pushing entries when the fd
 * carries tainted data; if no entry, the fd is treated as untainted).
 *
 * Modes: PER_FD MAX-color (default) and the Sprint-20 verifier-clean
 * PER_BYTE loop dance over the full colors[] array (the
 * file_permission hook has no iov range, so per-byte here walks
 * colors[0..entry->length] — the conservative "whole pushed slice
 * could be written" interpretation; socket_sendmsg below bounds the
 * scan by the actual iov `size`).
 * ============================================================================ */

/* ============================================================================
 * Phase 19.6 Stage B — CO-RE fd resolution.
 *
 * The LSM file_permission hook receives a `struct file *`, not an integer fd.
 * To gate per-fd (not blanket per-pid) we walk the CURRENT task's open-file
 * table (current->files->fdt->fd[]) and find the index i where fd[i] == file.
 * The scan is bounded by VOS3_FD_SCAN_CAP for the verifier (bpf_loop). Reads
 * are CO-RE-relocatable (BPF_CORE_READ / bpf_core_read), so the program is
 * portable across kernel layouts.
 * ============================================================================ */
#define VOS3_FD_SCAN_CAP   1024u
#define VOS3_FD_UNRESOLVED 0xFFFFFFFFu

struct vos3_fd_walk_ctx {
    struct file **farr;     /* current->files->fdt->fd (kernel ptr) */
    struct file  *target;   /* the file the hook is gating */
    __u32         max_fds;  /* current->files->fdt->max_fds */
    int           found_fd; /* resolved fd, or -1 */
};

/* bpf_loop callback: one fd-table slot per iteration. Address-taken -> must be
 * a real (non-inlined) subprog. */
static long
vos3_fd_walk_cb(__u32 i, void *ctx_)
{
    struct vos3_fd_walk_ctx *c = ctx_;
    if (i >= c->max_fds)
        return 1;                          /* past the table -> stop */
    struct file *f = NULL;
    /* Raw kernel-memory read of the i-th fd slot. NOT bpf_core_read: farr is a
     * scalar kernel address we computed (current->files->fdt->fd resolved via
     * CO-RE already), not a relocatable kernel-struct field access. */
    bpf_probe_read_kernel(&f, sizeof(f), &c->farr[i]);
    if (f && f == c->target) {
        c->found_fd = (int)i;
        return 1;                          /* found -> stop */
    }
    return 0;                              /* keep scanning */
}

/* Resolve the integer fd in current's fd table that points at `file`, or
 * VOS3_FD_UNRESOLVED if not found within the scan cap. */
static __always_inline __u32
vos3_resolve_fd(struct file *file)
{
    if (!file)
        return VOS3_FD_UNRESOLVED;
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    struct file **farr   = BPF_CORE_READ(task, files, fdt, fd);
    __u32         max_fds = BPF_CORE_READ(task, files, fdt, max_fds);
    if (!farr || !max_fds)
        return VOS3_FD_UNRESOLVED;
    struct vos3_fd_walk_ctx ctx = {
        .farr = farr, .target = file, .max_fds = max_fds, .found_fd = -1,
    };
    __u32 cap = max_fds < VOS3_FD_SCAN_CAP ? max_fds : VOS3_FD_SCAN_CAP;
    bpf_loop(cap, vos3_fd_walk_cb, &ctx, 0);
    return ctx.found_fd < 0 ? VOS3_FD_UNRESOLVED : (__u32)ctx.found_fd;
}

SEC("lsm/file_permission")
int BPF_PROG(vos3_taint_gate_file_perm,
             struct file *file, int mask, int ret_in)
{
    /* Short-circuit on prior LSM hook denial. */
    if (ret_in != 0) return ret_in;

    /* Only gate writes. */
    if (!(mask & 0x2 /* MAY_WRITE */)) return 0;

    struct vos3_taint_map_key key = {
        .pid = vos3_resolve_tgid(),     /* Phase 19.6: container-ns aware */
        .fd  = vos3_resolve_fd(file),   /* Phase 19.6 Stage B: real per-fd key */
    };

    /* Finding B3-1 closure (Option (a)): consult the per-fd MARK set FIRST.
     * A MARKED fd with no color entry fails CLOSED (-EPERM) — the historical
     * "no entry -> default ALLOW" bypass is gone. An UNMARKED fd never carried
     * tainted bytes, so it stays default-allow (untainted I/O, invariant G3). */
    uint8_t *marked = bpf_map_lookup_elem(&taint_marks, &key);

    struct vos3_taint_color_entry *entry =
        bpf_map_lookup_elem(&taint_colors, &key);

#ifdef VOS3_TAINT_BPF_DEBUG
    /* Phase 19.5b diagnostic (compile with -DVOS3_TAINT_BPF_DEBUG). Gated on a
     * MARKED hit -> low volume; proves the deny is computed for a marked task. */
    if (marked)
        bpf_printk("vos3_fp MARKED pid=%u entry_present=%d return=%d",
                   key.pid, entry != 0, entry ? 0 : -EPERM);
#endif

    /* Phase 22: if this fd is MARKED and the bridge is mid two-step update,
     * freeze fail-closed under the transactional lock (ordering-independent). */
    if (marked && vos3_txn_frozen())
        return -EPERM;

    /* Single return path — see VOS3_LSM_RET clamp note below. */
    int ret;
    if (!entry) {
        /* B3-1 + Phase 28 (G10): MARKED + no entry -> fail-closed (-EPERM).
         * After a userspace token-rotation flush (invalidate_slot deleted the
         * color entry but KEPT the MARK), this is the STALE-SLOT path: deny and
         * AUDIT it so the rotation lifecycle gap is observable, never silent.
         * UNMARKED -> untainted I/O, default-allow (invariant G3). */
        if (marked) {
            vos3_emit_stale_deny(key, 0);
            ret = -EPERM;
        } else {
            ret = 0;
        }
    } else {
        /* Sprint 18 / Wave 3.C — mode branch.
         * The LSM file_permission hook doesn't carry the iov range; for
         * per-byte mode we walk colors[0..min(length, BOUND)] which is
         * the conservative "the whole pushed slice could be written"
         * interpretation. socket_sendmsg (below) bounds by the iov size. */
        uint8_t max_color;
        uint32_t deny_off = VOS3_TAINT_NO_DENY;
        if (entry->mode == VOS3_TAINT_MODE_PER_BYTE) {
            max_color = vos3_max_color_per_byte(
                entry, entry->length, entry->sink_max_label, &deny_off);
        } else {
            max_color = vos3_max_color_per_fd(entry);
        }
        ret = vos3_compare_to_sink(max_color, entry->sink_max_label);
        vos3_emit_audit(entry, key, entry->length, ret);
    }

    /* LSM programs MUST return a value the verifier can prove lies in
     * [-4095, 0]. The branches above can leave R0 as an optimizer-emitted NEG
     * (`-(x&1)`), which the verifier flags as an unknown scalar. Re-bounding
     * with explicit compares makes the verifier re-learn the range and fixes
     * "R0 ... should have been in [-4095, 0]" (-22 EINVAL). All paths converge
     * here so no early return can escape the clamp. */
    barrier_var(ret);  /* opaque to the optimizer so the clamp below survives;
                        * without it clang proves the range and deletes the
                        * compares, leaving a bare NEG the verifier can't bound. */
    if (ret > 0) ret = 0;
    if (ret < -4095) ret = -4095;
    return ret;
}

/* ============================================================================
 * Phase 27 (Gap G4) — CO-RE socket-fd resolution.
 *
 * The socket_sendmsg LSM hook receives a `struct socket *`, not an integer fd.
 * A socket's userspace file descriptor is the fd-table slot whose `struct file *`
 * is `sock->file` (every socket fd is backed by that file). So we read sock->file
 * via CO-RE and feed it to the SAME `vos3_resolve_fd()` walk the file_permission
 * hook uses (current->files->fdt->fd[]) — complete structural parity with the
 * file-permission loader subsystem, no second code path.
 *
 * This RETIRES the legacy `fd=0` blanket stub. Before this change every socket
 * for a pid collapsed to the single key (pid, 0) — a de-facto per-PID block that
 * could neither distinguish a tainted socket from a clean one nor key its
 * per-byte color entry. With a real per-socket fd, the existing per-(pid,fd)
 * lookup + per-byte color scan (vos3_max_color_per_byte) finally engages on the
 * network egress sink exactly as it already does on file writes.
 * ============================================================================ */
static __always_inline __u32
vos3_resolve_sock_fd(struct socket *sock)
{
    if (!sock)
        return VOS3_FD_UNRESOLVED;
    /* sock->file is a relocatable kernel-struct field -> CO-RE read. The
     * resulting `struct file *` is then matched against the fd table by the
     * shared walk (vos3_resolve_fd), identical to the file_permission path. */
    struct file *sf = BPF_CORE_READ(sock, file);
    return vos3_resolve_fd(sf);
}

/* ============================================================================
 * LSM hook — socket_sendmsg.
 *
 * Highest-stakes egress sink. Now FULLY at parity with file_permission:
 * container-ns pid (vos3_resolve_tgid) + real per-socket fd (CO-RE), the same
 * MARK/colors fail-closed ladder, the same Phase-22 transactional freeze, and
 * the same per-byte color scan — here bounded by the iov `size` so only the
 * bytes actually being sent are inspected. NETWORK_EGRESS sink ceiling is
 * UNTRUSTED (TOXIC + SECRET deny); PUBLIC/UNTRUSTED transit cleanly.
 * ============================================================================ */

SEC("lsm/socket_sendmsg")
int BPF_PROG(vos3_taint_gate_sendmsg,
             struct socket *sock, struct msghdr *msg, int size, int ret_in)
{
    if (ret_in != 0) return ret_in;

    struct vos3_taint_map_key key = {
        .pid = vos3_resolve_tgid(),          /* Phase 19.6: container-ns aware */
        .fd  = vos3_resolve_sock_fd(sock),   /* Phase 27 (G4): real per-socket fd */
    };

    /* Finding B3-1 closure (Option (a)) — same fail-closed ladder as the
     * file_permission hook: MARKED + no entry -> -EPERM; UNMARKED -> allow. */
    uint8_t *marked = bpf_map_lookup_elem(&taint_marks, &key);

    struct vos3_taint_color_entry *entry =
        bpf_map_lookup_elem(&taint_colors, &key);

#ifdef VOS3_TAINT_BPF_DEBUG
    /* Parity with file_permission: gated on a MARKED hit -> low volume. */
    if (marked)
        bpf_printk("vos3_sm MARKED pid=%u fd=%u entry_present=%d",
                   key.pid, key.fd, entry != 0);
#endif

    /* Phase 22 parity: if this socket fd is MARKED and the bridge is mid
     * two-step update, freeze fail-closed under the transactional lock. */
    if (marked && vos3_txn_frozen())
        return -EPERM;

    int ret;
    if (!entry) {
        /* B3-1 + Phase 28 (G10): MARKED + no entry -> fail-closed (-EPERM).
         * After a userspace token-rotation flush (invalidate_slot deleted the
         * color entry but KEPT the MARK), this is the STALE-SLOT path: deny and
         * AUDIT it so the rotation lifecycle gap is observable, never silent.
         * UNMARKED -> untainted I/O, default-allow (invariant G3). */
        if (marked) {
            vos3_emit_stale_deny(key, 0);
            ret = -EPERM;
        } else {
            ret = 0;
        }
    } else {
        /* socket_sendmsg DOES carry the write size — use it as the per-byte
         * range bound so the kernel walks ONLY the bytes actually being sent
         * (kernel-side EchoLeak defense: a clean UNTRUSTED prefix can egress if
         * the iov size doesn't reach the TOXIC bytes). */
        uint8_t max_color;
        uint32_t deny_off = VOS3_TAINT_NO_DENY;
        if (entry->mode == VOS3_TAINT_MODE_PER_BYTE) {
            uint32_t span = (uint32_t)size;
            if (span > entry->length) span = entry->length;
            max_color = vos3_max_color_per_byte(
                entry, span, entry->sink_max_label, &deny_off);
        } else {
            max_color = vos3_max_color_per_fd(entry);
        }
        ret = vos3_compare_to_sink(max_color, entry->sink_max_label);
        vos3_emit_audit(entry, key, (uint32_t)size, ret);
    }

    /* Verifier return-range clamp to [-4095, 0] (see file_permission note). */
    barrier_var(ret);  /* opaque to the optimizer so the clamp below survives;
                        * without it clang proves the range and deletes the
                        * compares, leaving a bare NEG the verifier can't bound. */
    if (ret > 0) ret = 0;
    if (ret < -4095) ret = -4095;
    return ret;
}

#endif /* __VOS3_TAINT_GATE_REAL_BPF_BUILD */

/* ----------------------------------------------------------------------- */
/* HOST-build placeholder.                                                 */
/* When __VOS3_TAINT_GATE_REAL_BPF_BUILD is undefined (the default in the  */
/* vOS kernel CI), this file compiles to nothing. The userspace bridge    */
/* picks up the slack via the MOCK code path in                            */
/* backend/security/kernel_gate_connector.py.                              */
/* ----------------------------------------------------------------------- */
