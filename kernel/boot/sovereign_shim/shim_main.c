/**
 * @file shim_main.c
 * @brief Sovereign Boot first-stage UEFI shim — structural skeleton.
 *
 * This is the EFI entry point for the Sovereign Boot first-stage shim
 * scaffolded in Stage 14. See `README.md` in this directory for the full
 * scope ceiling.
 *
 * What this DOES:
 *   - Provides the EFI_IMAGE_ENTRY signature an EFI loader expects.
 *   - Declares the chain-load API the kernel build pipeline will use.
 *   - Calls verify_signature() on the next-stage image and refuses to
 *     boot on verification failure.
 *
 * What this does NOT yet do:
 *   - Actually verify against a real Sovereign Boot CA — verify_signature
 *     is a stub returning EFI_NOT_READY (Stage 14.C.2 deliverable).
 *   - Implement the full PE/COFF loader for the next stage — Stage 14.C.3.
 *   - Extend RTMR[0] with the shim → kernel transition measurement —
 *     Stage 14.C.4 (depends on a TPM/TDX integration that the kernel
 *     already has via tee.c, just not yet wired into the shim path).
 *
 * Reasoning for shipping the skeleton even with stubs:
 *   The Stage-14 multi-target Makefile includes a `kernel-baremetal`
 *   target that needs SOMETHING to invoke for the shim build. A skeleton
 *   that compiles cleanly and returns a documented "EFI_NOT_READY" lets
 *   the build pipeline run end-to-end today; replacing the stubs with a
 *   real impl when the spec lands is a single-file change.
 *
 * @date 2026-05-09
 * @copyright Copyright (c) 2026 vOS Project
 * @license MIT
 */

#include <stdint.h>
#include <stddef.h>

/* Minimal EFI typedefs — we deliberately avoid pulling in the full
 * gnu-efi or edk2 header chain at this stage. When the real shim
 * implementation lands, these get replaced with the canonical EFI
 * type definitions from the chosen toolchain. */

typedef uint64_t EFI_STATUS;
typedef void *   EFI_HANDLE;
typedef void *   EFI_SYSTEM_TABLE_PTR;

#define EFI_SUCCESS             ((EFI_STATUS)0)
#define EFI_NOT_READY           ((EFI_STATUS)6)
#define EFI_SECURITY_VIOLATION  ((EFI_STATUS)26)
#define EFI_LOAD_ERROR          ((EFI_STATUS)1)

/* Public API — declared in shim_api.h (will land alongside Stage 14.C.2). */

extern EFI_STATUS sovereign_shim_verify_kernel(const void *kernel_image,
                                               size_t kernel_image_size);

extern EFI_STATUS sovereign_shim_chain_load(EFI_HANDLE image_handle,
                                            EFI_SYSTEM_TABLE_PTR system_table,
                                            const void *kernel_image,
                                            size_t kernel_image_size);

/**
 * @brief EFI entry point — Sovereign Boot first-stage shim.
 *
 * Conventional EFI shim flow:
 *   1. Locate the kernel image on the EFI System Partition.
 *   2. Verify its signature against the embedded vendor key.
 *   3. Chain-load it.
 *
 * This skeleton implements the high-level flow with stubs for the
 * actual file-load + signature-verify steps.
 */
EFI_STATUS efi_main(EFI_HANDLE image_handle,
                    EFI_SYSTEM_TABLE_PTR system_table)
{
    /* Stage 14.C.2 — locate kernel image on ESP.
     * Today: caller-supplied buffer (the Makefile passes kernel size
     * as a constant; the real shim reads it from the ESP file system). */
    const void  *kernel_image      = (const void *)0;
    const size_t kernel_image_size = 0u;

    /* Stage 14.C.2 — verify signature.
     * Today: stub returns EFI_NOT_READY; documented in README §"What this
     * does NOT yet do". */
    EFI_STATUS status = sovereign_shim_verify_kernel(
        kernel_image, kernel_image_size);
    if (status != EFI_SUCCESS) {
        /* Fail closed. A skeleton that boots into an unverified next
         * stage would be worse than a skeleton that doesn't boot at all,
         * so EFI_NOT_READY here is the correct refusal until the real
         * verifier lands. */
        return status;
    }

    /* Stage 14.C.3 — chain-load. Today: stub. */
    status = sovereign_shim_chain_load(image_handle, system_table,
                                       kernel_image, kernel_image_size);
    return status;
}

/* ============================================================================
 * Stub implementations
 *
 * Both functions return EFI_NOT_READY so calling firmware sees a clear
 * "not implemented yet" status code rather than silent success.
 * ============================================================================ */

EFI_STATUS sovereign_shim_verify_kernel(const void *kernel_image,
                                        size_t kernel_image_size)
{
    (void)kernel_image;
    (void)kernel_image_size;
    /* Stage 14.C.2 will replace this with:
     *   - Parse the PE/COFF authenticode signature embedded in vos3.efi
     *   - Verify against the embedded VOS public key (or, for the real
     *     Sovereign Boot spec, against the Sovereign-Boot-CA chain)
     *   - Extend RTMR[0] with SHA-384(shim‖kernel)
     */
    return EFI_NOT_READY;
}

EFI_STATUS sovereign_shim_chain_load(EFI_HANDLE image_handle,
                                     EFI_SYSTEM_TABLE_PTR system_table,
                                     const void *kernel_image,
                                     size_t kernel_image_size)
{
    (void)image_handle;
    (void)system_table;
    (void)kernel_image;
    (void)kernel_image_size;
    /* Stage 14.C.3 will replace this with:
     *   - LoadImage(BootPolicy=FALSE, ParentImageHandle=image_handle,
     *               DevicePath=NULL, SourceBuffer=kernel_image, ...)
     *   - StartImage(NewImageHandle, ExitDataSize=NULL, ExitData=NULL)
     *   - On return from kernel: cleanup + return its status.
     */
    return EFI_NOT_READY;
}
