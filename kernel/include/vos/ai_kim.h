/**
 * @file ai_kim.h
 * @brief VOS3 Kernel Inference Manager (KIM)
 *
 * @details Hardware-accelerated LLM inference dispatcher. Uses the NPU
 *          Abstraction Layer (accel.h) for MATMUL/SOFTMAX/LAYERNORM and
 *          the GPU Memory Manager (gpu_mem.h) for DMA buffer pools.
 *
 *          KIM Lifecycle:
 *            1. Model loaded into AI slot via SLOT_START/SLOT_FINISH
 *            2. KIM_GENERATE command triggers inference
 *            3. KIM reads weight tensors from slot's HugePage-backed memory
 *            4. Dispatches ops through vos3_accel_dispatch()
 *            5. Streams generated tokens via VBUS_TYPE_TOKEN_STREAM frames
 *
 *          KV-Cache is allocated from the slot's context_base (HugePage-pinned)
 *          and persists across inference calls for session continuity.
 *
 * @version 2.0.0  (Phase 6 — Multi-Core Work-Stealing)
 * @date 2026-04-09
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 6 — Native Inference Runtime
 */

#ifndef VOS3_AI_KIM_H
#define VOS3_AI_KIM_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * KIM CONFIGURATION
 *
 * 2026-05 Universal-Model Upgrade
 * -------------------------------
 * Ceilings raised to admit the May-2026 open-weight families:
 *
 *   Gemma 3 (27B):           vocab 262 144, d_model  5 376, n_layers 62
 *   Llama 4 Scout (109B MoE):vocab 200 064, d_model  5 120, n_layers 48 (16 experts)
 *   Llama 4 Maverick (402B): vocab 200 064, d_model  5 120, n_layers 48 (128 experts)
 *   DeepSeek-V3 / R1 (671B): vocab 129 280, d_model  7 168, n_layers 61 (MLA)
 *   Qwen3 (72B):             vocab 152 064, d_model  8 192, n_layers 80
 *   Phi-4 (14B):             vocab 100 352, d_model  5 120, n_layers 40
 *
 * The constants below are runtime CLAMPS only (see ai_kim.c:219-222) — not
 * static array sizes. Lifting them does not inflate the kernel BSS. Working
 * memory grows linearly with vocab and seq_len at the per-slot logits and
 * KV-cache buffers (allocated lazily from the GPU memory manager).
 * ============================================================================ */

/** @brief Maximum transformer layers KIM can process
 *  Gemma 3 needs 62, DeepSeek-V3 has 61, Llama 4 Maverick has 48, Qwen3 has 80.
 *  128 leaves headroom for any 2026 single-tower model. */
#define VOS3_KIM_MAX_LAYERS         128U

/** @brief Maximum sequence length (context window).
 *  Doubled from 4096 to 8192 for May-2026 universal coverage. KV-cache pages
 *  are scaled proportionally (see VOS3_KIM_KV_PAGES). Long-context modes
 *  (Llama 4 Scout's 10M, Gemma 3's 128K) are out of scope for the kernel
 *  resident path — those use the host-side Ollama lane via local-snappy. */
#define VOS3_KIM_MAX_SEQ_LEN        8192U

/** @brief Maximum vocabulary size for token sampling.
 *  Lifted from 65 536 to 262 144 to accept Gemma 3's 256K SentencePiece
 *  vocabulary. Working logits buffer scales linearly: vocab × 4 B = 1 MiB
 *  at the new ceiling (was 256 KiB). Allocated transiently from GPU memory,
 *  not BSS. */
#define VOS3_KIM_MAX_VOCAB          262144U

/** @brief Maximum embedding dimension.
 *  Lifted from 8192 to 16384 to accept future MoE expert dimensions and to
 *  give 2× headroom over Qwen3-72B's 8192 d_model. Working hidden-state
 *  buffer at this ceiling: 16384 × 4 B = 64 KiB per slot. */
#define VOS3_KIM_MAX_DIM            16384U

/** @brief KV-Cache page count per slot (HugePage-pinned).
 *  Doubled from 4→8 (8 × 2 MiB = 16 MiB per slot) to match the doubled
 *  VOS3_KIM_MAX_SEQ_LEN. Total KV ceiling across 8 slots: 128 MiB.
 *  Working memory per slot at the new ceilings: 16 MiB KV + ~1.1 MiB logits/
 *  hidden/scratch ≈ 17.1 MiB. */
#define VOS3_KIM_KV_PAGES           8U  /* 8 × 2 MiB = 16 MiB per slot */

/** @brief Token output buffer size */
#define VOS3_KIM_TOKEN_BUF_SIZE     256U

/* ============================================================================
 * KIM STATE
 * ============================================================================ */

/** @brief KIM inference state */
typedef enum vos3_kim_state {
    VOS3_KIM_IDLE       = 0U,   /**< No inference in progress */
    VOS3_KIM_LOADING    = 1U,   /**< Preparing weights and buffers */
    VOS3_KIM_RUNNING    = 2U,   /**< Inference loop active */
    VOS3_KIM_STREAMING  = 3U,   /**< Token streaming in progress */
    VOS3_KIM_ERROR      = 4U,   /**< Last inference failed */
    VOS3_KIM_STOLEN     = 5U,   /**< Inference migrated to another core */
} vos3_kim_state_t;

/** @brief Model header extracted from slot data (GGUF-style) */
typedef struct vos3_kim_model_info {
    uint32_t n_layers;          /**< Number of transformer layers */
    uint32_t n_heads;           /**< Number of attention heads */
    uint32_t d_model;           /**< Model embedding dimension */
    uint32_t d_ff;              /**< Feed-forward inner dimension */
    uint32_t vocab_size;        /**< Vocabulary size */
    uint32_t max_seq_len;       /**< Maximum sequence length */
    uint8_t  quant_type;        /**< 0=FP32, 1=FP16, 2=INT8, 3=INT4 */
} vos3_kim_model_info_t;

/** @brief Per-slot KIM inference context */
typedef struct vos3_kim_ctx {
    uint8_t             slot_id;        /**< AI model slot ID */
    vos3_kim_state_t    state;          /**< Current inference state */
    vos3_kim_model_info_t model;        /**< Model parameters */

    /* KV-Cache (HugePage-pinned, persistent across calls) */
    uintptr_t           kv_base;        /**< Virtual base of KV-cache */
    uint32_t            kv_size;        /**< Total KV-cache bytes */
    uint32_t            kv_seq_pos;     /**< Current sequence position */
    uint8_t             kv_initialized; /**< 1 if KV-cache allocated */

    /* Multi-core dispatch (Phase 6 v2.0) */
    uint32_t            exec_core;      /**< CPU ID executing inference */
    uint32_t            steal_count;    /**< Times this slot was work-stolen */

    /* Statistics */
    uint64_t            tokens_generated;   /**< Total tokens this session */
    uint64_t            total_ops;          /**< Total accel dispatch ops */
    uint64_t            total_matmuls;      /**< MATMUL operations */
    uint64_t            last_latency_us;    /**< Last token latency (usec) */
} vos3_kim_ctx_t;

/** @brief KIM subsystem statistics */
typedef struct vos3_kim_stats {
    uint32_t active_slots;          /**< Slots with inference running */
    uint64_t total_tokens;          /**< Lifetime tokens generated */
    uint64_t total_dispatches;      /**< Lifetime accel dispatches */
    uint64_t total_token_frames;    /**< TOKEN_STREAM frames sent */
    uint64_t avg_latency_us;        /**< Average token latency (usec) */
    /* Multi-core (Phase 6 v2.0) */
    uint32_t bsp_tokens;            /**< Tokens generated on BSP (core 0) */
    uint32_t ap_tokens;             /**< Tokens generated on AP cores */
    uint32_t total_steals;          /**< Lifetime work-steal events */
    uint32_t silicon_faults;        /**< Silicon hardening violations detected */
    /* Phase 6.1: Per-slot inference state transition tracking */
    uint32_t inference_transitions; /**< Total state transitions across all slots */
    uint32_t anti_preempt_grants;   /**< Total anti-preemption timeslice grants */
} vos3_kim_stats_t;

/* ============================================================================
 * MULTI-CORE WORK-STEAL QUEUE (Phase 6 v2.0)
 * ============================================================================ */

/**
 * @brief Pending inference work item for work-stealing.
 *
 * Posted when BSP is busy with VBus I/O; AP can claim via
 * vos3_kim_try_steal().
 */
typedef struct vos3_kim_work_item {
    volatile uint32_t   claimed;        /**< 0=available, 1=claimed */
    uint8_t             slot_id;        /**< Target model slot */
    uint32_t            max_tokens;     /**< Tokens remaining to generate */
    uint32_t            temperature_fp; /**< Temperature parameter */
    uint32_t            poster_core;    /**< Core that posted this work */
} vos3_kim_work_item_t;

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize the Kernel Inference Manager
 *
 * Zeros all per-slot KIM contexts. Must be called after accel_init()
 * and gpu_mem_init().
 *
 * @return 0 on success, negative errno on failure
 */
int vos3_kim_init(void);

/**
 * @brief Run inference on a loaded model slot
 *
 * Entry point for VBus KIM_GENERATE command and SYS_INFERENCE_HINT.
 *
 * 1. Reads model header from slot base address
 * 2. Allocates working buffers via GPU memory manager
 * 3. Iterates transformer layers: LayerNorm → MATMUL → Softmax
 * 4. Samples output token from logits
 * 5. Streams token via VBUS_TYPE_TOKEN_STREAM
 * 6. Updates KV-cache sequence position
 * 7. Repeats until max_tokens or EOS
 *
 * @param[in] slot_id       Model slot (0-7)
 * @param[in] max_tokens    Maximum tokens to generate (1-4096)
 * @param[in] temperature_fp Temperature in fixed-point ×100 (100 = 1.0)
 * @return Number of tokens generated, or negative errno on failure
 */
int vos3_kim_generate(uint8_t slot_id, uint32_t max_tokens,
                       uint32_t temperature_fp);

/**
 * @brief Get per-slot KIM context (read-only)
 *
 * @param[in] slot_id Slot to query (0-7)
 * @return Pointer to KIM context, or NULL if invalid
 */
const vos3_kim_ctx_t *vos3_kim_get_ctx(uint8_t slot_id);

/**
 * @brief Get KIM subsystem statistics
 *
 * @param[out] stats Statistics structure to fill
 * @return 0 on success, -1 if stats is NULL
 */
int vos3_kim_get_stats(vos3_kim_stats_t *stats);

/**
 * @brief Reset KIM context for a slot (on SLOT_RESET)
 *
 * Clears KV-cache state and statistics. Does NOT free HugePages
 * (that is handled by ai_slots.c).
 *
 * @param[in] slot_id Slot to reset (0-7)
 */
void vos3_kim_reset(uint8_t slot_id);

/**
 * @brief Try to steal pending inference work from the multi-core queue.
 *
 * Called by AP cores (or any idle core) to claim pending inference work
 * that was posted by a busy core. Uses atomic compare-exchange to prevent
 * double-claim. If work is found, runs vos3_kim_generate() on this core.
 *
 * @return Number of tokens generated (>0), 0 if no work available,
 *         or negative errno on failure
 */
int vos3_kim_try_steal(void);

/**
 * @brief Post inference work to the steal queue for AP pickup.
 *
 * Called when BSP detects it should offload inference to a less-loaded
 * core. The AP can later claim this via vos3_kim_try_steal().
 *
 * @param[in] slot_id       Model slot (0-7)
 * @param[in] max_tokens    Maximum tokens to generate
 * @param[in] temperature_fp Temperature in fixed-point ×100
 * @return 0 on success, -1 if queue full
 */
int vos3_kim_post_work(uint8_t slot_id, uint32_t max_tokens,
                        uint32_t temperature_fp);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_AI_KIM_H */
