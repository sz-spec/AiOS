/**
 * @file model_registry.h
 * @brief VOS3 Model Registry — Format Detection and Metadata Types
 *
 * @details Minimal type definitions for AI model format identification
 *          and metadata extraction. Used by future model loading and
 *          slot management subsystems.
 *
 * @version 0.1.0
 * @date 2026-04-03
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 4.9a — Skeleton only, no implementation file yet.
 */

#ifndef VOS3_MODEL_REGISTRY_H
#define VOS3_MODEL_REGISTRY_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/* ============================================================================
 * MODEL FORMAT IDENTIFIERS
 * ============================================================================ */

#define VOS3_MODEL_FMT_UNKNOWN     0x00U
#define VOS3_MODEL_FMT_GGUF        0x01U
#define VOS3_MODEL_FMT_SAFETENSORS 0x02U
#define VOS3_MODEL_FMT_ONNX        0x03U
#define VOS3_MODEL_FMT_RAW         0xFFU

/* ============================================================================
 * MODEL METADATA
 * ============================================================================ */

/**
 * @brief Per-model metadata record
 *
 * @details Populated during model load by format-specific parsers.
 *          Stored in the model registry for slot management and
 *          telemetry reporting.
 */
typedef struct vos3_model_metadata {
    uint8_t     format;         /**< VOS3_MODEL_FMT_* identifier */
    uint8_t     quant_type;     /**< 0=F32, 1=F16, 2=Q8_0, 3=Q4_K_M */
    uint16_t    reserved;       /**< Padding — must be zero */
    uint32_t    n_layers;       /**< Number of transformer layers */
    uint64_t    total_size;     /**< Total model size in bytes */
    uint64_t    header_size;    /**< Format-specific header size */
    char        label[32];      /**< Human-readable model name */
} __attribute__((packed)) vos3_model_metadata_t;

#ifdef __cplusplus
}
#endif

#endif /* VOS3_MODEL_REGISTRY_H */
