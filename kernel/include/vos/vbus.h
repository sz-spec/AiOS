/* SPDX-License-Identifier: MIT
 * SPDX-FileCopyrightText: 2026 VOS3 Project
 *
 * VOS3 VBus v3.0 — Open-Core Standard Public Header
 * ==================================================
 *
 * This header is the **Open-Core canonical interface** for VBus — the
 * VOS3 universal AI-substrate IPC bus. CORE consumers (open source MIT)
 * and PRO consumers (Sovereign Enterprise) both program against this
 * single contract.
 *
 * VBus is the substrate VOS3 proposes as the "DirectX of AI agents":
 *   - Universal, language-agnostic IPC between agents and kernel
 *   - Hardware-attested every transaction (MMR audit chain)
 *   - Capability-gated at the slot boundary (slots.h)
 *   - Speakable by any MCP-compatible client via tools/vos3_mcp_bridge.py
 *
 * ----------------------------------------------------------------------
 * STATUS NOTE — read this before assuming behavior
 * ----------------------------------------------------------------------
 *
 * The OPCODES defined below (VOS3_VBUS_OP_*) are the **v3.0 numeric
 * command encoding**. The shipping kernel today (v20.5.2) dispatches via
 * the ASCII tokenized protocol implemented in
 * `kernel/src/drivers/virtio_bridge.c` (see streq(tok[0], "MMR_ROOT")
 * etc). The v3.0 numeric encoding is forward-looking and will be used
 * by:
 *   - The C SDK at sdk/c/vos3.h (issues numeric ioctls)
 *   - The Python SDK at sdk/python/vos3_sdk/core.py (parses both forms)
 *   - The binary VBus transport (`virtio_vbus.h`) which already supports
 *     a 41-command numeric registry alongside the legacy ASCII protocol
 *     and uses a different 16-bit `VBUS_MAGIC = 0x5642` for its own
 *     wire frame. The two namespaces coexist by design — `virtio_vbus.h`
 *     is the wire layer; this header is the agent-facing contract.
 *
 * Until the binary transport gets the new ops wired (v20.6), consumers
 * should issue these as ASCII commands ("SHARE_MEMORY", "REGISTER_AGENT")
 * via the existing virtio_bridge.c dispatcher. The Python SDK does this
 * translation transparently.
 *
 * ----------------------------------------------------------------------
 * Layering
 * ----------------------------------------------------------------------
 *
 *   Application (any language)
 *        │
 *        ▼
 *   sdk/python/vos3_sdk/core.py  ←  this header's Python mirror
 *   sdk/c/vos3.h                  ←  C SDK declarations
 *        │
 *        ▼
 *   tools/vos3_mcp_bridge.py      ←  optional MCP transport
 *        │
 *        ▼
 *   /tmp/vos3_bridge.sock          ←  Unix-domain VBus socket
 *        │
 *        ▼
 *   virtio_bridge.c (kernel)      ←  ASCII dispatcher today
 *   virtio_vbus.c   (kernel)      ←  binary transport (Phase 4.2.5+)
 */

#ifndef VOS3_VBUS_H
#define VOS3_VBUS_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ----- Protocol identification -----
 *
 * NOTE on namespacing: the v3.0 substrate-level contract uses the
 * VOS3_VBUS_* prefix to avoid colliding with the binary-wire layer in
 * `virtio_vbus.h`, which has its own 16-bit `VBUS_MAGIC = 0x5642U` for
 * its frame header. Both can be included in the same translation unit
 * without redefinition warnings.
 */

/* "VBUS" in big-endian ASCII (V=0x56 B=0x42 U=0x55 S=0x53). Verified
 * by the v3.0 handshake on every connection. Mismatch → reject session. */
#define VOS3_VBUS_MAGIC                 0x56425553u

#define VOS3_VBUS_PROTOCOL_VERSION      0x0300u   /* v3.0 */
#define VOS3_VBUS_PROTOCOL_VERSION_MIN  0x0204u   /* binary VBus 41-cmd transport */

/* ----- Operation codes (v3.0 numeric encoding) -----
 *
 * Range allocation:
 *   0x000-0x0FF — legacy 22 commands (PING, STAT, READ, WRITE, …)
 *   0x100-0x1FF — slot lifecycle (SLOT_START, SLOT_FINISH, …)
 *   0x200-0x2FF — model lifecycle (MODEL_START, MODEL_DONE, …)
 *   0x300-0x3FF — diagnostics (DRIVER_PRESSURE, KTEXT_HASH, MMR_ROOT, …)
 *   0x400-0x4FF — Phase 6.0 ecosystem (THIS HEADER's new ops)
 *   0x500-0x5FF — reserved for v20.6+
 */

#define VOS3_VBUS_OP_SHARE_MEMORY        0x400u  /* Read-only KV cache view across slots */
#define VOS3_VBUS_OP_REGISTER_AGENT      0x401u  /* External agent registers an Intelligence Profile */
#define VOS3_VBUS_OP_QUERY_REGISTRY      0x402u  /* List registered agents */
#define VOS3_VBUS_OP_REVOKE_AGENT        0x403u  /* Tear down an agent registration */
#define VOS3_VBUS_OP_SECURE_COMPUTE      0x404u  /* Submit attested compute request */
#define VOS3_VBUS_OP_NEGOTIATE_CAPS      0x405u  /* Dynamic capability negotiation handshake */
#define VOS3_VBUS_OP_GET_EFFICIENCY_STATS 0x406u /* KV-compressor efficiency telemetry */

/* ----- Capability flag bits for SHARE_MEMORY -----
 *
 * The owning slot of the shared region declares which operations the
 * caller may perform. READ_ONLY is the safe default; any combination
 * with WRITE forces a Copy-on-Write fork via kv_compressor's
 * mark_dirty path.
 */

#define VOS3_VBUS_SHARE_READ            (1u << 0)
#define VOS3_VBUS_SHARE_WRITE           (1u << 1)
#define VOS3_VBUS_SHARE_EXECUTE         (1u << 2)  /* Reserved — currently rejected per W^X */
#define VOS3_VBUS_SHARE_COW             (1u << 3)  /* Mark-dirty on first write fork */

#define VOS3_VBUS_SHARE_DEFAULT_READONLY (VOS3_VBUS_SHARE_READ)
#define VOS3_VBUS_SHARE_DEFAULT_COW      (VOS3_VBUS_SHARE_READ | VOS3_VBUS_SHARE_WRITE | VOS3_VBUS_SHARE_COW)

/* ----- Frame envelope (v3.0 binary header) -----
 *
 * Every binary-VBus frame starts with this 52-byte header (4-byte
 * aligned, no internal padding):
 *
 *    magic         u32  @  0  ( 4)
 *    protocol_ver  u16  @  4  ( 2)
 *    op_code       u16  @  6  ( 2)
 *    session_id    u32  @  8  ( 4)
 *    request_id    u32  @ 12  ( 4)
 *    payload_len   u16  @ 16  ( 2)
 *    flags         u16  @ 18  ( 2)
 *    hmac[32]      u8   @ 20  (32)
 *                       total: 52 bytes
 *
 * The legacy binary wire-frame in virtio_vbus.h is a separate 64-byte
 * header (AVX-512-aligned for the existing transport); this v3.0
 * envelope is a different layer and ships under a different magic.
 *
 * The payload length is bounded by VOS3_VBUS_MAX_PAYLOAD (8 KB; matches
 * the shipping VBus bound-window guard against CVE-2026-23086). The
 * HMAC-SHA256 authenticates everything after the magic — required since
 * v20.0.
 */

#define VOS3_VBUS_MAX_PAYLOAD           8192u
#define VOS3_VBUS_HMAC_SIZE             32u

typedef struct vos3_vbus_frame_header {
    uint32_t magic;          /* must equal VOS3_VBUS_MAGIC */
    uint16_t protocol_ver;   /* VOS3_VBUS_PROTOCOL_VERSION (or compatible older) */
    uint16_t op_code;        /* one of VOS3_VBUS_OP_* */
    uint32_t session_id;     /* opaque per-connection identifier */
    uint32_t request_id;     /* monotonic per-session for correlation */
    uint16_t payload_len;    /* payload bytes following this header */
    uint16_t flags;          /* per-op flag bitmask (VOS3_VBUS_SHARE_* etc) */
    uint8_t  hmac[VOS3_VBUS_HMAC_SIZE]; /* HMAC-SHA256 over (header[0..size-32] || payload) */
} vos3_vbus_frame_header_t;

/* ===========================================================================
 * Standard Service Registry — v3.0 introduction
 * ===========================================================================
 *
 * The Standard Service Registry is the substrate-level "yellow pages"
 * for AI agents on a VOS3 host. Any agent — kernel-internal slot or
 * external process speaking the SDK — registers an Intelligence Profile
 * describing what it does and what it needs. The registry is queryable
 * via VOS3_VBUS_OP_QUERY_REGISTRY so other agents can discover and
 * negotiate with peers.
 *
 * The registry is bounded (max 256 active services per host) and
 * append-only with explicit revocation; this matches the kernel's
 * static-allocation discipline (no heap in the hot path).
 *
 * EVERY registration is recorded into the MMR audit ledger as an
 * VOS3_VBUS_OP_REGISTER_AGENT event — operators can replay the chain
 * to see when each agent joined.
 */

#define VOS3_VBUS_SERVICE_NAME_MAX      48u
#define VOS3_VBUS_SERVICE_VENDOR_MAX    32u
#define VOS3_VBUS_SERVICE_REGISTRY_MAX  256u

/* Profile flags — what kind of agent is this? */
#define VOS3_VBUS_PROFILE_KERNEL_INTERNAL  (1u << 0)  /* Slot 0-3 first-party */
#define VOS3_VBUS_PROFILE_EXTERNAL_TRUSTED (1u << 1)  /* Holds a signed manifest */
#define VOS3_VBUS_PROFILE_EXTERNAL_GUEST   (1u << 2)  /* Untrusted, sandbox-only */
#define VOS3_VBUS_PROFILE_PRO_FEATURE      (1u << 3)  /* Requires PRO license to register */

/* Required capability bits the agent declares it needs. Mirrors the
 * VOS3_CAP_* flags from kernel/include/ipc/slots.h, but we re-name the
 * bits here so cross-flavor consumers don't accidentally tie themselves
 * to a single ABI source. (slots.h remains the canonical source for the
 * slot-side bits; this is the agent-facing view.) */

typedef struct vos3_vbus_intelligence_profile {
    char     name[VOS3_VBUS_SERVICE_NAME_MAX];     /* "claude-frontend" etc */
    char     vendor[VOS3_VBUS_SERVICE_VENDOR_MAX]; /* "Anthropic", "VOS3", … */
    uint32_t profile_flags;                        /* VOS3_VBUS_PROFILE_* */
    uint32_t required_caps;                        /* VOS3_CAP_* it requests */
    uint32_t requested_memory_pages;               /* hugepages it wants reserved */
    uint32_t expected_complexity;                  /* 1-10 router complexity hint */
    uint8_t  manifest_signature[64];               /* Ed25519 sig over the rest */
    uint8_t  manifest_pubkey[32];                  /* identity pubkey of the agent */
} vos3_vbus_intelligence_profile_t;

/* The registry slot — what the kernel actually stores per registered service. */

typedef struct vos3_vbus_service {
    uint32_t registry_id;                          /* monotonic from 1; 0 = empty slot */
    uint32_t session_id;                           /* connection id when active */
    uint64_t registered_tsc;                       /* mmr_rdtsc() at registration */
    vos3_vbus_intelligence_profile_t profile;
    uint32_t granted_caps;                         /* what we actually approved */
    uint32_t state;                                /* VOS3_VBUS_SERVICE_STATE_* */
} vos3_vbus_service_t;

#define VOS3_VBUS_SERVICE_STATE_PENDING     1u  /* Profile received, capabilities not yet negotiated */
#define VOS3_VBUS_SERVICE_STATE_ACTIVE      2u  /* Negotiated, fully connected */
#define VOS3_VBUS_SERVICE_STATE_QUARANTINE  3u  /* Slot transitioned ZOMBIE; service quarantined */
#define VOS3_VBUS_SERVICE_STATE_REVOKED     4u  /* Operator-initiated teardown */

/* ===========================================================================
 * VOS3_VBUS_OP_SHARE_MEMORY payload
 * ===========================================================================
 *
 * An external agent (caller) requests a read-only or CoW view of another
 * slot's KV-cache region. The request goes through three gates:
 *
 *   1. The owning slot's capability mask must include `granted_caps`
 *      (e.g. VOS3_CAP_GPU_DIRECT for GPU buffer views)
 *   2. The owning slot must NOT be in ZOMBIE state
 *   3. The kv_compressor lookup table is consulted — if the requested
 *      region is already deduped (multi-slot CoW), the reference count
 *      is bumped instead of allocating a fresh mapping
 *
 * The kernel returns a virtual address valid only within the caller's
 * slot context. Cross-slot pointer leakage is prevented by the slot
 * isolation invariant from kernel/src/mm/vmm.c (each slot's expansion
 * VA region is disjoint by construction).
 */

typedef struct vos3_vbus_share_memory_request {
    uint32_t target_slot_id;                  /* slot whose KV we want to view */
    uint32_t share_flags;                     /* VOS3_VBUS_SHARE_* */
    uint64_t target_offset_bytes;             /* within target slot's expansion zone */
    uint64_t length_bytes;                    /* bounded by VOS3_VBUS_MAX_PAYLOAD per request */
} vos3_vbus_share_memory_request_t;

typedef struct vos3_vbus_share_memory_response {
    uintptr_t mapped_va;                      /* 0 on failure */
    uint64_t  mapped_length;
    uint32_t  granted_flags;                  /* may be a SUBSET of requested */
    uint32_t  registry_id_of_owner;           /* for audit */
} vos3_vbus_share_memory_response_t;

/* ===========================================================================
 * VOS3_VBUS_OP_REGISTER_AGENT payload
 * ===========================================================================
 *
 * External agent presents its Intelligence Profile + manifest signature.
 * Kernel verifies:
 *   - Manifest signature is well-formed Ed25519 (32-byte pubkey + 64-byte sig)
 *   - profile.required_caps are policy-acceptable for this connection's
 *     trust posture (KERNEL_INTERNAL > EXTERNAL_TRUSTED > EXTERNAL_GUEST)
 *   - profile.profile_flags do not request VOS3_VBUS_PROFILE_PRO_FEATURE
 *     without a valid PRO license (queried via vos3_pro_license_check)
 *
 * On accept, the kernel allocates a vos3_vbus_service_t in the registry
 * and returns its registry_id. On reject, the response carries a reason
 * code and no registry slot is consumed.
 */

typedef struct vos3_vbus_register_agent_response {
    uint32_t registry_id;                     /* 0 if rejected */
    uint32_t granted_caps;                    /* may be a SUBSET of requested */
    uint32_t reject_reason;                   /* VOS3_VBUS_REJECT_* if registry_id == 0 */
} vos3_vbus_register_agent_response_t;

#define VOS3_VBUS_REJECT_INVALID_SIGNATURE  1u
#define VOS3_VBUS_REJECT_REGISTRY_FULL      2u
#define VOS3_VBUS_REJECT_PRO_FEATURE_NO_LIC 3u
#define VOS3_VBUS_REJECT_CAPS_DENIED        4u
#define VOS3_VBUS_REJECT_PROTOCOL_MISMATCH  5u

/* [OLYMPUS-FIX A-15] APEX-HOME — architectural PRO-license seam.
 *
 * Returns the appropriate VOS3_VBUS_REJECT_* code if the requested
 * profile_flags ask for a PRO feature on a kernel without an active
 * PRO license. Returns 0 to mean "proceed". Implementation lives in
 * kernel/src/drivers/vbus_transport.c.
 *
 * Today no in-tree caller invokes this — the seam is in place so a
 * future PRO-gated registration handler can refuse cleanly without
 * any further infrastructure work. */
uint32_t vos3_vbus_check_pro_license(uint32_t profile_flags);

/* ============================================================================
 * VOS3 ZERO-COPY FABRIC — selector contract
 * ============================================================================
 *
 *   [QUANTUM-LEAP-SCAFFOLD]  Operation HOME-DOMINANCE v21.0  2026-05-02
 *
 * The zero-copy fabric is the abstraction the binary VBus transport
 * uses to move bytes between an agent and the kernel without an
 * intermediate buffer copy. Three backends are defined; selection is
 * runtime, based on hardware probe (cf. kernel/include/vos/cpu_features.h
 * and kernel/src/drivers/acpi.c CXL host-bridge detection):
 *
 *   1. CXL.mem coherent shared region (PRO hardware, CXL 4.0+)
 *   2. AVX-512 / AMX optimized ring buffer (consumer x86_64)
 *   3. SSE2 fallback ring buffer (any x86_64)
 *
 * The selector (VOS3_ZERO_COPY_FABRIC_SELECT) defaults to AVX-512/AMX
 * on home PCs, satisfying the >10,000 req/sec target for consumer
 * hardware without requiring CXL. CXL.mem upgrades the path when
 * available; the API is identical to callers.
 *
 * STATUS: definition-only in this header; the selector switch and the
 * ring-buffer implementation land in subsequent commits. Keep this
 * macro suite stable so the SDK side can build against it today.
 */

typedef enum vos3_zero_copy_backend {
    VOS3_ZCF_NONE        = 0u,  /* Plain heap copy (legacy fallback) */
    VOS3_ZCF_SSE2_RING   = 1u,  /* SSE2 64-byte ring (any x86_64)    */
    VOS3_ZCF_AVX512_RING = 2u,  /* AVX-512/AMX optimized ring        */
    VOS3_ZCF_CXL_REGION  = 3u,  /* CXL.mem cache-coherent region     */
} vos3_zero_copy_backend_t;

/* Selector contract: callers query once at session start, cache the
 * result, and dispatch through the matching backend. The kernel may
 * downgrade the backend at runtime (e.g. CXL link error → AVX ring)
 * but never upgrades silently — protocol guarantees stability per
 * session. */
#define VOS3_ZERO_COPY_FABRIC_VERSION  1u

typedef struct vos3_zero_copy_capabilities {
    uint32_t backend;            /* vos3_zero_copy_backend_t              */
    uint32_t alignment_bytes;    /* Required ring-entry alignment         */
    uint32_t max_payload_bytes;  /* Largest single-frame payload          */
    uint32_t cxl_region_id;      /* If backend==CXL_REGION; else zero     */
    uint64_t flags;              /* Reserved (HMAC mandatory, etc.)       */
} vos3_zero_copy_capabilities_t;

/* Capability flags */
#define VOS3_ZCF_FLAG_HMAC_REQUIRED        (1ull << 0)
#define VOS3_ZCF_FLAG_COHERENT             (1ull << 1)  /* hardware-coherent */
#define VOS3_ZCF_FLAG_NUMA_LOCAL           (1ull << 2)
#define VOS3_ZCF_FLAG_CXL_BUNDLED_PORT     (1ull << 3)  /* CXL 4.0 bundled    */

/* Threshold below which the ring backend is preferred even when CXL
 * is available (small-message round-trip is dominated by setup cost,
 * not bandwidth). The constant is documented here so the agent SDK
 * can mirror the choice without a VBus round-trip. */
#define VOS3_ZCF_RING_PREFERRED_BYTES      4096u

#ifdef __cplusplus
}
#endif

#endif /* VOS3_VBUS_H */
