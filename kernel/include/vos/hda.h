/**
 * @file hda.h
 * @brief VOS3 Intel HD Audio (HDA) Sovereign Driver — Public API
 *
 * @details Freestanding HD Audio controller driver supporting Intel HDA
 *          specification 1.0a (2010) through HDA 1.0c (2023). Provides:
 *
 *          1. PCI class-based discovery (class 0x04, subclass 0x03)
 *          2. CORB/RIRB command transport with DMA ring buffers
 *          3. Codec enumeration and basic widget configuration
 *          4. Input/Output stream descriptor management
 *          5. V-Palace Direct Stream Injection: DMA buffers mapped into
 *             Hall memory for zero-copy audio→AI pipeline
 *          6. PCM-to-token feature extraction (mel-spectrogram binning)
 *
 *          Audio samples bypass user-space entirely — DMA writes directly
 *          into V-Palace Hall pages, preserving 100% privacy.
 *
 *          Hardware coverage: Intel ICH6+ (2004), Intel PCH (2008+),
 *          Realtek ALC (2006+), QEMU intel-hda (all versions).
 *
 * @version 1.0.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Phase 8.5 — Sovereign Multimedia & Neural-Link Integration
 * @note MISRA C:2024 Compliant — freestanding, no libc
 */

#ifndef VOS3_HDA_H
#define VOS3_HDA_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * PCI CLASSIFICATION
 * ============================================================================ */

/** @brief PCI class code for multimedia devices */
#define VOS3_HDA_PCI_CLASS          0x04U

/** @brief PCI subclass for HD Audio controller */
#define VOS3_HDA_PCI_SUBCLASS       0x03U

/* ============================================================================
 * CONTROLLER REGISTER OFFSETS (Intel HDA Spec 1.0a, Section 3)
 * ============================================================================ */

/** @brief Global Capabilities (16-bit, read-only) */
#define HDA_REG_GCAP                0x00U
/** @brief Minor Version (8-bit, read-only) */
#define HDA_REG_VMIN                0x02U
/** @brief Major Version (8-bit, read-only) */
#define HDA_REG_VMAJ                0x03U
/** @brief Output Payload Capability (16-bit) */
#define HDA_REG_OUTPAY              0x04U
/** @brief Input Payload Capability (16-bit) */
#define HDA_REG_INPAY               0x06U
/** @brief Global Control (32-bit) */
#define HDA_REG_GCTL                0x08U
/** @brief Wake Enable (16-bit) */
#define HDA_REG_WAKEEN              0x0CU
/** @brief State Change Status (16-bit) */
#define HDA_REG_STATESTS            0x0EU
/** @brief Interrupt Control (32-bit) */
#define HDA_REG_INTCTL              0x20U
/** @brief Interrupt Status (32-bit) */
#define HDA_REG_INTSTS              0x24U
/** @brief Wall Clock Counter (32-bit) */
#define HDA_REG_WALCLK              0x30U
/** @brief Stream Synchronization (32-bit) */
#define HDA_REG_SSYNC               0x38U

/* --- CORB (Command Output Ring Buffer) --- */

/** @brief CORB Lower Base Address (32-bit) */
#define HDA_REG_CORBLBASE           0x40U
/** @brief CORB Upper Base Address (32-bit) */
#define HDA_REG_CORBUBASE           0x44U
/** @brief CORB Write Pointer (16-bit) */
#define HDA_REG_CORBWP              0x48U
/** @brief CORB Read Pointer (16-bit) */
#define HDA_REG_CORBRP              0x4AU
/** @brief CORB Control (8-bit) */
#define HDA_REG_CORBCTL             0x4CU
/** @brief CORB Status (8-bit) */
#define HDA_REG_CORBSTS             0x4EU
/** @brief CORB Size (8-bit) */
#define HDA_REG_CORBSIZE            0x4FU

/* --- RIRB (Response Input Ring Buffer) --- */

/** @brief RIRB Lower Base Address (32-bit) */
#define HDA_REG_RIRBLBASE           0x50U
/** @brief RIRB Upper Base Address (32-bit) */
#define HDA_REG_RIRBUBASE           0x54U
/** @brief RIRB Write Pointer (16-bit) */
#define HDA_REG_RIRBWP              0x58U
/** @brief Response Interrupt Count (16-bit) */
#define HDA_REG_RINTCNT             0x5AU
/** @brief RIRB Control (8-bit) */
#define HDA_REG_RIRBCTL             0x5CU
/** @brief RIRB Status (8-bit) */
#define HDA_REG_RIRBSTS             0x5EU
/** @brief RIRB Size (8-bit) */
#define HDA_REG_RIRBSIZE            0x5FU

/* --- DMA Position Buffer --- */

/** @brief DMA Position Buffer Lower Base (32-bit) */
#define HDA_REG_DPLBASE             0x70U
/** @brief DMA Position Buffer Upper Base (32-bit) */
#define HDA_REG_DPUBASE             0x74U

/* --- Stream Descriptor Offsets (relative to SD base) --- */

/** @brief First Input Stream Descriptor base */
#define HDA_SD_BASE                 0x80U
/** @brief Stride between consecutive stream descriptors */
#define HDA_SD_STRIDE               0x20U

/** @brief SD Control (24-bit: 3 bytes at offset 0x00-0x02) */
#define HDA_SD_CTL                  0x00U
/** @brief SD Status (8-bit at offset 0x03) */
#define HDA_SD_STS                  0x03U
/** @brief SD Link Position in Buffer (32-bit) */
#define HDA_SD_LPIB                 0x04U
/** @brief SD Cyclic Buffer Length (32-bit) */
#define HDA_SD_CBL                  0x08U
/** @brief SD Last Valid Index (16-bit) */
#define HDA_SD_LVI                  0x0CU
/** @brief SD FIFO Size (16-bit, read-only) */
#define HDA_SD_FIFOS                0x10U
/** @brief SD Stream Format (16-bit) */
#define HDA_SD_FMT                  0x12U
/** @brief SD BDL Pointer Lower (32-bit) */
#define HDA_SD_BDLPL                0x18U
/** @brief SD BDL Pointer Upper (32-bit) */
#define HDA_SD_BDLPU                0x1CU

/* ============================================================================
 * CONTROL BITS
 * ============================================================================ */

/** @brief GCTL: Controller Reset bit */
#define HDA_GCTL_CRST               (1U << 0)
/** @brief GCTL: Accept Unsolicited Response Enable */
#define HDA_GCTL_UNSOL              (1U << 8)

/** @brief CORBCTL: Run bit */
#define HDA_CORBCTL_RUN             (1U << 1)
/** @brief CORBRP: Reset bit */
#define HDA_CORBRP_RST              (1U << 15)

/** @brief RIRBCTL: Run bit */
#define HDA_RIRBCTL_RUN             (1U << 1)
/** @brief RIRBCTL: Interrupt control */
#define HDA_RIRBCTL_RINTCTL         (1U << 0)

/** @brief INTCTL: Global Interrupt Enable */
#define HDA_INTCTL_GIE              (1U << 31)
/** @brief INTCTL: Controller Interrupt Enable */
#define HDA_INTCTL_CIE              (1U << 30)

/** @brief SDCTL: Stream Run */
#define HDA_SDCTL_RUN               (1U << 1)
/** @brief SDCTL: Interrupt on Completion Enable */
#define HDA_SDCTL_IOCE              (1U << 2)
/** @brief SDCTL: Stripe Control (bit 16-17) */
#define HDA_SDCTL_STRIPE_SHIFT      16U
/** @brief SDCTL: Stream Number (bits 20-23) */
#define HDA_SDCTL_STREAM_SHIFT      20U

/** @brief SDSTS: Buffer Completion Interrupt Status */
#define HDA_SDSTS_BCIS              (1U << 2)
/** @brief SDSTS: FIFO Error */
#define HDA_SDSTS_FIFOE             (1U << 3)
/** @brief SDSTS: Descriptor Error */
#define HDA_SDSTS_DESE              (1U << 4)

/* ============================================================================
 * CODEC VERB ENCODING (Section 7.3)
 * ============================================================================ */

/** @brief Build a 12-bit verb (SET/GET widget parameter) */
#define HDA_VERB_12(cad, nid, verb, param) \
    (((uint32_t)(cad) << 28) | ((uint32_t)(nid) << 20) | \
     ((uint32_t)(verb) << 8) | (uint32_t)(param))

/** @brief Build a 4-bit verb (SET_CONVERTER, etc.) */
#define HDA_VERB_4(cad, nid, verb, param) \
    (((uint32_t)(cad) << 28) | ((uint32_t)(nid) << 20) | \
     ((uint32_t)(verb) << 16) | (uint32_t)(param))

/** @brief GET_PARAMETER verb (12-bit, VID=0xF00) */
#define HDA_VERB_GET_PARAM         0xF00U
/** @brief GET_CONNECTION_LIST (12-bit, VID=0xF02) */
#define HDA_VERB_GET_CONN_LIST     0xF02U
/** @brief GET_CONVERTER_FORMAT (4-bit, VID=0xA) */
#define HDA_VERB_GET_CONV_FMT      0xAU
/** @brief SET_CONVERTER_FORMAT (4-bit, VID=0x2) */
#define HDA_VERB_SET_CONV_FMT      0x2U
/** @brief SET_CHANNEL_STREAMID (12-bit, VID=0x706) */
#define HDA_VERB_SET_STREAM_CHAN    0x706U
/** @brief SET_PIN_WIDGET_CONTROL (12-bit, VID=0x707) */
#define HDA_VERB_SET_PIN_CTL       0x707U
/** @brief SET_EAPD_BTLENABLE (12-bit, VID=0x70C) */
#define HDA_VERB_SET_EAPD          0x70CU
/** @brief SET_POWER_STATE (12-bit, VID=0x705) */
#define HDA_VERB_SET_POWER         0x705U
/** @brief GET_POWER_STATE (12-bit, VID=0xF05) */
#define HDA_VERB_GET_POWER         0xF05U

/** @brief Parameter IDs for GET_PARAMETER */
#define HDA_PARAM_VENDOR_ID        0x00U
#define HDA_PARAM_REVISION_ID      0x02U
#define HDA_PARAM_SUBNODE_COUNT    0x04U
#define HDA_PARAM_FUNC_GROUP_TYPE  0x05U
#define HDA_PARAM_AUDIO_CAPS       0x09U
#define HDA_PARAM_PIN_CAPS         0x0CU
#define HDA_PARAM_CONN_LIST_LEN    0x0EU
#define HDA_PARAM_STREAM_FORMATS   0x0BU
#define HDA_PARAM_AMP_OUT_CAPS     0x12U

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

/** @brief Maximum number of codecs on the HDA link (4 SDI lines) */
#define VOS3_HDA_MAX_CODECS         15U

/** @brief Maximum number of streams (input + output) */
#define VOS3_HDA_MAX_STREAMS        30U

/** @brief CORB depth (256 entries × 4 bytes = 1 KB) */
#define VOS3_HDA_CORB_ENTRIES       256U

/** @brief RIRB depth (256 entries × 8 bytes = 2 KB) */
#define VOS3_HDA_RIRB_ENTRIES       256U

/** @brief BDL entries per stream (max 256 per HDA spec) */
#define VOS3_HDA_BDL_ENTRIES        32U

/** @brief Default PCM sample rate: 16 kHz (speech optimal) */
#define VOS3_HDA_SAMPLE_RATE_16K    16000U

/** @brief Default PCM sample rate: 48 kHz (standard audio) */
#define VOS3_HDA_SAMPLE_RATE_48K    48000U

/** @brief Default PCM bit depth: 16-bit signed */
#define VOS3_HDA_BITS_16            16U

/** @brief V-Palace token frame size: 512 samples (32 ms at 16 kHz) */
#define VOS3_HDA_TOKEN_FRAME_SIZE   512U

/** @brief Number of mel-frequency bins for tokenization */
#define VOS3_HDA_MEL_BINS           80U

/** @brief Virtual base address for HDA MMIO mapping */
#define VOS3_HDA_MMIO_VBASE         0xFFFF880034000000ULL

/* ============================================================================
 * TYPES
 * ============================================================================ */

/** @brief HDA codec descriptor */
typedef struct vos3_hda_codec {
    uint8_t     cad;            /**< Codec address (0-14) */
    uint8_t     active;         /**< 1 if codec detected and configured */
    uint16_t    vendor_id;      /**< Codec vendor ID */
    uint16_t    device_id;      /**< Codec device ID */
    uint8_t     start_nid;      /**< First subordinate node ID */
    uint8_t     num_nodes;      /**< Number of subordinate nodes */
    uint32_t    func_group_type;/**< Function group type */
} vos3_hda_codec_t;

/** @brief HDA stream format descriptor */
typedef struct vos3_hda_stream_fmt {
    uint32_t    sample_rate;    /**< Sample rate in Hz */
    uint8_t     bits;           /**< Bits per sample (16, 24, 32) */
    uint8_t     channels;       /**< Number of channels (1=mono, 2=stereo) */
    uint16_t    hw_format;      /**< HDA hardware format register value */
} vos3_hda_stream_fmt_t;

/**
 * @brief Buffer Descriptor List entry (16 bytes, HDA spec Section 3.6.3)
 *
 * Must be aligned to 128-byte boundary per HDA spec.
 */
typedef struct vos3_hda_bdl_entry {
    uint64_t    address;        /**< Physical address of buffer fragment */
    uint32_t    length;         /**< Length of buffer fragment in bytes */
    uint32_t    ioc;            /**< Interrupt On Completion (bit 0) */
} __attribute__((packed)) vos3_hda_bdl_entry_t;

/** @brief Stream direction */
typedef enum {
    VOS3_HDA_STREAM_INPUT  = 0, /**< Capture (microphone) */
    VOS3_HDA_STREAM_OUTPUT = 1  /**< Playback (speakers) */
} vos3_hda_stream_dir_t;

/** @brief V-Palace injection mode */
typedef enum {
    VOS3_HDA_INJECT_NONE  = 0,  /**< No V-Palace injection */
    VOS3_HDA_INJECT_RAW   = 1,  /**< Raw PCM into Hall page */
    VOS3_HDA_INJECT_TOKEN = 2   /**< Mel-tokenized into Hall page */
} vos3_hda_inject_mode_t;

/** @brief HDA driver statistics */
typedef struct vos3_hda_stats {
    uint32_t    codecs_found;       /**< Number of codecs detected */
    uint32_t    streams_active;     /**< Currently running streams */
    uint64_t    samples_captured;   /**< Total PCM samples captured */
    uint64_t    samples_played;     /**< Total PCM samples played */
    uint64_t    tokens_generated;   /**< Audio frames tokenized */
    uint64_t    dma_errors;         /**< DMA FIFO/descriptor errors */
    uint64_t    palace_injections;  /**< Successful V-Palace DMA injections */
    uint32_t    capture_active;     /**< 1 if capture stream running */
    uint32_t    playback_active;    /**< 1 if playback stream running */
} vos3_hda_stats_t;

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_HDA_OK                 (0)
#define VOS3_HDA_E_NODEV           (-1)   /**< No HDA controller found */
#define VOS3_HDA_E_INVAL           (-2)   /**< Invalid argument */
#define VOS3_HDA_E_NOMEM           (-3)   /**< DMA buffer allocation failed */
#define VOS3_HDA_E_TIMEOUT         (-4)   /**< Controller reset/response timeout */
#define VOS3_HDA_E_NOCODEC         (-5)   /**< No codecs detected */
#define VOS3_HDA_E_BUSY            (-6)   /**< Stream already running */
#define VOS3_HDA_E_IO              (-7)   /**< DMA or MMIO I/O error */
#define VOS3_HDA_E_PALACE          (-8)   /**< V-Palace injection failed */

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Initialize the Intel HD Audio driver
 *
 * Scans PCI bus for class 0x04/subclass 0x03, maps BAR0, resets
 * the controller, sets up CORB/RIRB, and enumerates codecs.
 *
 * @return VOS3_HDA_OK on success, negative error code on failure
 */
int vos3_hda_init(void);

/**
 * @brief Start an audio capture stream (microphone input)
 *
 * Configures the first available input stream descriptor, sets up
 * BDL with DMA buffers, and starts capture.
 *
 * @param[in] fmt  Stream format (sample rate, bits, channels)
 * @return VOS3_HDA_OK on success, negative error code on failure
 */
int vos3_hda_capture_start(const vos3_hda_stream_fmt_t *fmt);

/**
 * @brief Stop the active capture stream
 *
 * @return VOS3_HDA_OK on success
 */
int vos3_hda_capture_stop(void);

/**
 * @brief Start an audio playback stream (speaker output)
 *
 * @param[in] fmt  Stream format
 * @return VOS3_HDA_OK on success, negative error code on failure
 */
int vos3_hda_playback_start(const vos3_hda_stream_fmt_t *fmt);

/**
 * @brief Stop the active playback stream
 *
 * @return VOS3_HDA_OK on success
 */
int vos3_hda_playback_stop(void);

/**
 * @brief Write PCM data to the playback stream
 *
 * @param[in] data  PCM sample data
 * @param[in] len   Length in bytes
 * @return Number of bytes written, or negative error code
 */
int vos3_hda_playback_write(const void *data, size_t len);

/**
 * @brief Enable V-Palace Direct Stream Injection
 *
 * Maps the capture DMA buffer into a V-Palace Hall page for
 * zero-copy audio→AI pipeline. Bypasses user-space entirely.
 *
 * @param[in] slot_id  AI model slot to inject into (1-3)
 * @param[in] mode     Injection mode (RAW or TOKEN)
 * @return VOS3_HDA_OK on success, negative error code on failure
 */
int vos3_hda_palace_inject(uint8_t slot_id, vos3_hda_inject_mode_t mode);

/**
 * @brief Disable V-Palace Direct Stream Injection
 *
 * @return VOS3_HDA_OK on success
 */
int vos3_hda_palace_detach(void);

/**
 * @brief Send a codec verb command via CORB/RIRB
 *
 * @param[in]  verb      Encoded 32-bit verb (use HDA_VERB_* macros)
 * @param[out] response  Codec response (may be NULL if not needed)
 * @return VOS3_HDA_OK on success, negative error code on failure
 */
int vos3_hda_codec_verb(uint32_t verb, uint32_t *response);

/**
 * @brief Get HDA driver statistics
 *
 * @param[out] stats  Statistics structure to populate
 * @return VOS3_HDA_OK on success
 */
int vos3_hda_get_stats(vos3_hda_stats_t *stats);

/**
 * @brief Process a captured audio frame through the tokenizer
 *
 * Extracts mel-frequency bin features from PCM data and produces
 * a token embedding vector. Used internally by the V-Palace
 * injection pipeline.
 *
 * @param[in]  pcm_data   Raw PCM samples (16-bit signed, mono)
 * @param[in]  num_samples Number of samples
 * @param[out] mel_out    Output mel-frequency bins (VOS3_HDA_MEL_BINS floats)
 * @return VOS3_HDA_OK on success
 */
int vos3_hda_pcm_to_mel(const int16_t *pcm_data, uint32_t num_samples,
                         int32_t *mel_out);

#ifdef __cplusplus
}
#endif

#endif /* VOS3_HDA_H */
