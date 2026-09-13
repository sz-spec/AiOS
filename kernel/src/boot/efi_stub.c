/**
 * @file efi_stub.c
 * @brief Phase 8.1: UEFI Boot Stub for VOS3
 *
 * @details Standalone UEFI boot entry point that translates EFI services
 *          into VOS3's boot_info_t format and hands off to kernel_main().
 *
 *          Boot sequence:
 *          1. UEFI firmware loads this PE32+ image → efi_main()
 *          2. Query UEFI memory map via GetMemoryMap()
 *          3. Query GOP framebuffer for display
 *          4. Locate ACPI RSDP from UEFI Configuration Table
 *          5. Allocate and build VOS3 page tables (PML4/PDPT/PD)
 *             - HHDM: 0xFFFF800000000000 → physical 0x0
 *             - Kernel: 0xFFFFFFFF80100000 → physical 0x100000
 *             - Identity: physical == virtual (temporary, for transition)
 *          6. Exit Boot Services
 *          7. Switch CR3 to VOS3 page tables
 *          8. Jump to kernel_main(&boot_info) in higher-half
 *
 *          This file is compiled TWICE:
 *          - As part of the kernel ELF (dormant, for type-checking)
 *          - As a standalone PE32+ EFI application (with -DVOS3_EFI_STUB)
 *
 * @note Compiled with -mabi=ms for UEFI calling convention on x86_64.
 *       Freestanding — no libc, no kernel runtime.  All UEFI services
 *       accessed through the System Table pointers.
 *
 * @version 8.1.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "../../include/vos/efi.h"
#include "../../include/vos/boot_info.h"

/* Only compile the actual EFI stub when building the PE32+ image.
 * When compiled as part of the kernel ELF, this file provides type
 * checking but no executable code (avoids MS ABI conflicts). */
#ifdef VOS3_EFI_STUB

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief VOS3 Higher-Half Direct Map offset (physical 0 → virtual HHDM) */
#define VOS3_HHDM_OFFSET            0xFFFF800000000000ULL

/** @brief Kernel virtual base (must match linker.ld KERNEL_VMA) */
#define VOS3_KERNEL_VMA             0xFFFFFFFF80100000ULL

/** @brief Kernel physical base (must match linker.ld KERNEL_LMA) */
#define VOS3_KERNEL_LMA             0x0000000000100000ULL

/** @brief Maximum physical memory to map in HHDM (4 GiB) */
#define VOS3_HHDM_PHYS_MAX         0x100000000ULL

/** @brief PTE flags */
#define PTE_PRESENT     (1ULL << 0)
#define PTE_WRITABLE    (1ULL << 1)
#define PTE_HUGE        (1ULL << 7)     /* 2MB page (PDE) or 1GB page (PDPTE) */
#define PTE_NX          (1ULL << 63)    /* No-Execute (requires EFER.NXE) */
#define PTE_PWT         (1ULL << 3)     /* Page-level Write-Through */
#define PTE_PCD         (1ULL << 4)     /* Page-level Cache Disable */
#define PTE_PAT_4K      (1ULL << 7)     /* PAT bit for 4KB pages */
#define PTE_PAT_LARGE   (1ULL << 12)    /* PAT bit for 2MB/1GB pages */

/** @brief IA32_PAT MSR address (Intel SDM Vol 3A §12.12.4) */
#define MSR_IA32_PAT    0x277U

/** @brief VOS3 PAT value: PAT4=WC(0x01) instead of default WB(0x06).
 *  Layout (8 bytes, each byte is one PAT entry, low byte = PAT0):
 *  PAT0=WB(06) PAT1=WT(04) PAT2=UC-(07) PAT3=UC(00)
 *  PAT4=WC(01) PAT5=WT(04) PAT6=UC-(07) PAT7=UC(00)
 *  Default: 0x0007040600070406 → Changed PAT4 from 0x06→0x01 */
#define VOS3_PAT_VALUE  0x0007040100070406ULL

/** @brief Number of entries in the Guard IDT (all 256 vectors) */
#define GUARD_IDT_ENTRIES  256U

/** @brief Maximum UEFI memory map entries to translate */
#define EFI_MMAP_MAX_ENTRIES    256U

/** @brief Pages for page tables: PML4(1) + PDPT_IDENT(1) + PDPT_HHDM(1)
 *         + PDPT_KERNEL(1) + PD_KERNEL(1) + PT_KERNEL0(1) = 6 pages.
 *  PT_KERNEL0 splits PD[0] into 512×4KB pages for G2 hardening. */
#define PT_PAGES_NEEDED         6U

/* ============================================================================
 * STATIC STORAGE (in .data section of the PE32+ image)
 * ============================================================================ */

/** @brief Boot info structure passed to kernel_main */
static vos3_boot_info_t g_efi_boot_info;

/** @brief Memory map translated to VOS3 format */
static vos3_boot_mmap_entry_t g_efi_mmap[EFI_MMAP_MAX_ENTRIES];

/** @brief CPU info (single BSP entry — SMP cores discovered via ACPI MADT) */
static vos3_boot_cpu_info_t g_efi_cpus[1];

/** @brief Raw UEFI memory map buffer (GetMemoryMap may return up to 8KB) */
static uint8_t g_raw_mmap[8192] __attribute__((aligned(8)));

/** @brief Guard IDT buffer (256 entries × 16 bytes = 4096 bytes).
 *  Loaded before ExitBootServices to catch NMIs and stray interrupts
 *  during the transition.  All entries point to efi_guard_halt(). */
static uint8_t g_guard_idt[GUARD_IDT_ENTRIES * 16] __attribute__((aligned(16)));

/* ============================================================================
 * HELPERS
 * ============================================================================ */

/**
 * @brief Count set bits in a 32-bit value (freestanding popcount)
 * Brian Kernighan's algorithm — O(set bits), no SSE/POPCNT needed.
 */
static uint8_t efi_popcount32(uint32_t v)
{
    uint8_t count = 0;
    while (v) {
        v &= v - 1;  /* Clear lowest set bit */
        count++;
    }
    return count;
}

/**
 * @brief Count trailing zeros in a 32-bit value (freestanding ctz)
 * Returns bit position of the lowest set bit, or 0 if v == 0.
 */
static uint8_t efi_ctz32(uint32_t v)
{
    if (v == 0) return 0;
    uint8_t count = 0;
    while ((v & 1U) == 0) {
        v >>= 1;
        count++;
    }
    return count;
}

/**
 * @brief Zero a memory region (freestanding — no memset available)
 */
static void efi_memzero(void *dst, UINTN size)
{
    uint8_t *p = (uint8_t *)dst;
    for (UINTN i = 0; i < size; i++) {
        p[i] = 0;
    }
}

/**
 * @brief Guard halt handler — infinite loop for NMI/stray interrupt safety.
 * All Guard IDT entries point here.  If any interrupt fires between
 * ExitBootServices and CR3 switch, the CPU halts cleanly instead of
 * executing random firmware code.
 */
static void __attribute__((used)) efi_guard_halt(void)
{
    for (;;) {
        __asm__ volatile("cli; hlt");
    }
}

/**
 * @brief Load a Guard IDT with all 256 entries pointing to efi_guard_halt().
 *
 * IDT entry format (x86_64 64-bit interrupt gate, 16 bytes):
 *   [0:1]   Offset[15:0]
 *   [2:3]   Segment Selector (CS)
 *   [4]     IST index (0 = no IST)
 *   [5]     Type/Attr: 0x8E = present, DPL=0, 64-bit interrupt gate
 *   [6:7]   Offset[31:16]
 *   [8:11]  Offset[63:32]
 *   [12:15] Reserved (0)
 */
static void efi_load_guard_idt(void)
{
    /* Get current code segment selector */
    uint16_t cs;
    __asm__ volatile("mov %%cs, %0" : "=r"(cs));

    uint64_t handler = (uint64_t)(uintptr_t)efi_guard_halt;

    /* Fill all 256 IDT entries as interrupt gates → efi_guard_halt */
    for (unsigned i = 0; i < GUARD_IDT_ENTRIES; i++) {
        uint8_t *entry = &g_guard_idt[i * 16];

        /* Offset[15:0] */
        entry[0] = (uint8_t)(handler & 0xFF);
        entry[1] = (uint8_t)((handler >> 8) & 0xFF);

        /* Segment Selector */
        entry[2] = (uint8_t)(cs & 0xFF);
        entry[3] = (uint8_t)((cs >> 8) & 0xFF);

        /* IST = 0 */
        entry[4] = 0;

        /* Type/Attr: present(bit7) + DPL=0(bits5-6) + 64-bit interrupt gate(0xE) */
        entry[5] = 0x8E;

        /* Offset[31:16] */
        entry[6] = (uint8_t)((handler >> 16) & 0xFF);
        entry[7] = (uint8_t)((handler >> 24) & 0xFF);

        /* Offset[63:32] */
        entry[8]  = (uint8_t)((handler >> 32) & 0xFF);
        entry[9]  = (uint8_t)((handler >> 40) & 0xFF);
        entry[10] = (uint8_t)((handler >> 48) & 0xFF);
        entry[11] = (uint8_t)((handler >> 56) & 0xFF);

        /* Reserved */
        entry[12] = 0;
        entry[13] = 0;
        entry[14] = 0;
        entry[15] = 0;
    }

    /* IDTR: 10-byte descriptor (2-byte limit + 8-byte base) */
    struct __attribute__((packed)) {
        uint16_t limit;
        uint64_t base;
    } idtr;

    idtr.limit = (uint16_t)(GUARD_IDT_ENTRIES * 16 - 1);
    idtr.base  = (uint64_t)(uintptr_t)g_guard_idt;

    __asm__ volatile("lidt %0" : : "m"(idtr));
}

/**
 * @brief Print a simple ASCII message via UEFI ConOut.
 * Converts ASCII to UCS-2 inline (UEFI uses wide characters).
 */
static void efi_print(EFI_SYSTEM_TABLE *st, const char *msg)
{
    CHAR16 buf[128];
    UINTN i = 0;

    while (msg[i] != '\0' && i < 126) {
        buf[i] = (CHAR16)msg[i];
        i++;
    }
    buf[i] = 0;

    if (st->ConOut != NULL) {
        st->ConOut->OutputString(st->ConOut, buf);
    }
}

/**
 * @brief Convert UEFI memory type to VOS3 memory map type
 */
static uint32_t efi_to_vos3_mmap_type(uint32_t efi_type)
{
    switch (efi_type) {
    case EFI_MMAP_LOADER_CODE:
    case EFI_MMAP_LOADER_DATA:
    case EFI_MMAP_BOOT_SERVICES_CODE:
    case EFI_MMAP_BOOT_SERVICES_DATA:
    case EFI_MMAP_CONVENTIONAL:
        return VOS3_MMAP_USABLE;

    case EFI_MMAP_ACPI_RECLAIM:
        return VOS3_MMAP_ACPI_RECLAIM;

    case EFI_MMAP_ACPI_NVS:
        return VOS3_MMAP_ACPI_NVS;

    case EFI_MMAP_UNUSABLE:
        return VOS3_MMAP_BAD;

    case EFI_MMAP_RUNTIME_SERVICES_CODE:
    case EFI_MMAP_RUNTIME_SERVICES_DATA:
    case EFI_MMAP_MMIO:
    case EFI_MMAP_MMIO_PORT_SPACE:
    case EFI_MMAP_PAL_CODE:
    case EFI_MMAP_RESERVED:
    default:
        return VOS3_MMAP_RESERVED;
    }
}

/**
 * @brief Compare two EFI GUIDs (avoids the inline function across ABIs)
 */
static int guid_match(const EFI_GUID *a, const EFI_GUID *b)
{
    const uint8_t *pa = (const uint8_t *)a;
    const uint8_t *pb = (const uint8_t *)b;
    for (int i = 0; i < 16; i++) {
        if (pa[i] != pb[i]) return 0;
    }
    return 1;
}

/* ============================================================================
 * ACPI RSDP DISCOVERY
 * ============================================================================ */

/**
 * @brief Locate ACPI RSDP from UEFI System Table Configuration Tables
 *
 * Walks the ConfigurationTable array looking for ACPI 2.0 GUID first,
 * then falls back to ACPI 1.0 GUID.
 *
 * @param st UEFI System Table
 * @return Physical address of RSDP, or 0 if not found
 */
static uint64_t efi_find_rsdp(EFI_SYSTEM_TABLE *st)
{
    EFI_GUID acpi20 = EFI_ACPI_20_TABLE_GUID;
    EFI_GUID acpi10 = EFI_ACPI_TABLE_GUID;
    uint64_t rsdp_10 = 0;

    for (UINTN i = 0; i < st->NumberOfTableEntries; i++) {
        EFI_CONFIGURATION_TABLE *entry = &st->ConfigurationTable[i];

        if (guid_match(&entry->VendorGuid, &acpi20)) {
            /* Prefer ACPI 2.0+ (has XSDT with 64-bit addresses) */
            return (uint64_t)(uintptr_t)entry->VendorTable;
        }
        if (guid_match(&entry->VendorGuid, &acpi10)) {
            /* Save ACPI 1.0 as fallback */
            rsdp_10 = (uint64_t)(uintptr_t)entry->VendorTable;
        }
    }

    return rsdp_10;
}

/* ============================================================================
 * GOP FRAMEBUFFER QUERY
 * ============================================================================ */

/**
 * @brief Query UEFI Graphics Output Protocol for framebuffer info
 *
 * @param st    UEFI System Table
 * @param fb    Output: VOS3 framebuffer info structure
 * @return 0 on success, -1 if GOP not available
 */
static int efi_query_gop(EFI_SYSTEM_TABLE *st, vos3_boot_framebuffer_t *fb)
{
    EFI_GUID gop_guid = EFI_GRAPHICS_OUTPUT_PROTOCOL_GUID;
    EFI_GRAPHICS_OUTPUT_PROTOCOL *gop = NULL;

    EFI_STATUS status = st->BootServices->LocateProtocol(
        &gop_guid, NULL, (void **)&gop);

    if (EFI_ERROR(status) || gop == NULL || gop->Mode == NULL) {
        return -1;
    }

    EFI_GRAPHICS_OUTPUT_PROTOCOL_MODE *mode = gop->Mode;
    EFI_GRAPHICS_OUTPUT_MODE_INFORMATION *info = mode->Info;

    /* Validate Info pointer (UEFI spec guarantees it when Mode is valid,
     * but defensive code should still check) */
    if (info == NULL) {
        return -1;
    }

    /* Validate framebuffer exists (headless systems may report base=0) */
    if (mode->FrameBufferBase == 0 || mode->FrameBufferSize == 0) {
        return -1;
    }

    fb->address = mode->FrameBufferBase;
    fb->width   = info->HorizontalResolution;
    fb->height  = info->VerticalResolution;
    fb->bpp     = 32;  /* GOP modes are always 32bpp on modern UEFI firmware */

    /* Calculate pitch from PixelsPerScanLine */
    fb->pitch = info->PixelsPerScanLine * 4;  /* 4 bytes per pixel at 32bpp */

    /* Color channel layout depends on pixel format */
    switch (info->PixelFormat) {
    case PixelRedGreenBlueReserved8BitPerColor:
        fb->red_mask_size   = 8;  fb->red_mask_shift   = 0;
        fb->green_mask_size = 8;  fb->green_mask_shift = 8;
        fb->blue_mask_size  = 8;  fb->blue_mask_shift  = 16;
        break;
    case PixelBlueGreenRedReserved8BitPerColor:
        fb->blue_mask_size  = 8;  fb->blue_mask_shift  = 0;
        fb->green_mask_size = 8;  fb->green_mask_shift = 8;
        fb->red_mask_size   = 8;  fb->red_mask_shift   = 16;
        break;
    case PixelBitMask: {
        /* Parse actual bitmasks from firmware (F4 audit fix).
         * Count trailing zeros to find shift, popcount for size. */
        EFI_PIXEL_BITMASK *pm = &info->PixelInformation;
        fb->red_mask_shift   = efi_ctz32(pm->RedMask);
        fb->green_mask_shift = efi_ctz32(pm->GreenMask);
        fb->blue_mask_shift  = efi_ctz32(pm->BlueMask);
        fb->red_mask_size    = efi_popcount32(pm->RedMask);
        fb->green_mask_size  = efi_popcount32(pm->GreenMask);
        fb->blue_mask_size   = efi_popcount32(pm->BlueMask);
        break;
    }
    default:
        return -1;  /* BltOnly or unsupported */
    }

    return 0;
}

/* ============================================================================
 * PAGE TABLE CONSTRUCTION
 * ============================================================================
 *
 * Build a new PML4 for VOS3's expected virtual address layout:
 *
 *   PML4[0]   → Identity map first 4GB (temporary, for transition code)
 *   PML4[256] → HHDM: 0xFFFF800000000000 → physical 0x0 (first 4GB)
 *   PML4[511] → Kernel: 0xFFFFFFFF80100000 → physical 0x100000
 *
 * Uses 1GB huge pages for HHDM/identity (PDPT level),
 * and 2MB huge pages for kernel (PD level).
 *
 * Page table memory is allocated from UEFI AllocatePages BEFORE
 * ExitBootServices.  Each level is one 4KB page.
 * ============================================================================ */

/**
 * @brief Allocate a zeroed 4KB-aligned page via UEFI AllocatePages
 * @return Physical address of the page, or 0 on failure
 */
static EFI_PHYSICAL_ADDRESS efi_alloc_page(EFI_BOOT_SERVICES *bs)
{
    EFI_PHYSICAL_ADDRESS addr = 0;
    EFI_STATUS status = bs->AllocatePages(
        AllocateAnyPages, EFI_MMAP_LOADER_DATA, 1, &addr);

    if (EFI_ERROR(status)) {
        return 0;
    }

    /* Zero the page */
    efi_memzero((void *)(uintptr_t)addr, 4096);
    return addr;
}

/**
 * @brief Build VOS3 page tables for the kernel transition
 *
 * Allocates 6 pages for:
 *   - PML4 (root)
 *   - PDPT for identity map (PML4[0])
 *   - PDPT for HHDM (PML4[256])
 *   - PDPT for kernel higher-half (PML4[511])
 *   - PD for kernel (PDPT[510] → 2MB entries)
 *   - PT for PD[0] split (512 × 4KB pages covering first 2MB)
 *
 * G2 hardening: PD[0] is split into 512 × 4KB pages so that:
 *   - Pages 0-255 (phys 0x0-0xFFFFF, firmware 1MB): READ-ONLY + NX
 *   - Pages 256-511 (phys 0x100000-0x1FFFFF, kernel): READ-WRITE
 *     (kernel .text starts at 0x100000 and must remain executable;
 *      the kernel VMM later applies proper .text=RX / .data=RW+NX)
 *
 * H2 hardening: HHDM PDPT entries have NX set (kernel never
 * fetches instructions through the HHDM).
 *
 * @param bs Boot Services (for AllocatePages)
 * @return Physical address of PML4, or 0 on failure
 */
static uint64_t efi_build_page_tables(EFI_BOOT_SERVICES *bs)
{
    /* Allocate all page table pages */
    EFI_PHYSICAL_ADDRESS pml4_phys    = efi_alloc_page(bs);
    EFI_PHYSICAL_ADDRESS pdpt_ident   = efi_alloc_page(bs);
    EFI_PHYSICAL_ADDRESS pdpt_hhdm    = efi_alloc_page(bs);
    EFI_PHYSICAL_ADDRESS pdpt_kernel  = efi_alloc_page(bs);
    EFI_PHYSICAL_ADDRESS pd_kernel    = efi_alloc_page(bs);
    EFI_PHYSICAL_ADDRESS pt_kernel0   = efi_alloc_page(bs);  /* G2: PD[0] split */

    if (pml4_phys == 0 || pdpt_ident == 0 || pdpt_hhdm == 0 ||
        pdpt_kernel == 0 || pd_kernel == 0 || pt_kernel0 == 0) {
        return 0;
    }

    uint64_t *pml4   = (uint64_t *)(uintptr_t)pml4_phys;
    uint64_t *pdpt_i = (uint64_t *)(uintptr_t)pdpt_ident;
    uint64_t *pdpt_h = (uint64_t *)(uintptr_t)pdpt_hhdm;
    uint64_t *pdpt_k = (uint64_t *)(uintptr_t)pdpt_kernel;
    uint64_t *pd_k   = (uint64_t *)(uintptr_t)pd_kernel;
    uint64_t *pt_k0  = (uint64_t *)(uintptr_t)pt_kernel0;

    /* --- PML4 entries --- */

    /* PML4[0]: Identity map (temporary — cleared by kernel after boot) */
    pml4[0] = pdpt_ident | PTE_PRESENT | PTE_WRITABLE;

    /* PML4[256]: HHDM at 0xFFFF800000000000 (index = 256) */
    pml4[256] = pdpt_hhdm | PTE_PRESENT | PTE_WRITABLE;

    /* PML4[511]: Kernel higher-half at 0xFFFFFFFF80000000 (index = 511) */
    pml4[511] = pdpt_kernel | PTE_PRESENT | PTE_WRITABLE;

    /* --- Identity PDPT: 4 × 1GB huge pages covering 0-4GB ---
     * No NX here — EFI stub code runs in identity-mapped space
     * during the transition (RIP is in low addresses). */
    for (int i = 0; i < 4; i++) {
        pdpt_i[i] = ((uint64_t)i * 0x40000000ULL)
                     | PTE_PRESENT | PTE_WRITABLE | PTE_HUGE;
    }

    /* --- HHDM PDPT: 4 × 1GB huge pages covering physical 0-4GB ---
     * H2 hardening: Set NX on all HHDM entries.  The kernel never
     * fetches instructions through the HHDM (0xFFFF800000000000+).
     * Code execution goes through PML4[511] (kernel higher-half). */
    for (int i = 0; i < 4; i++) {
        pdpt_h[i] = ((uint64_t)i * 0x40000000ULL)
                     | PTE_PRESENT | PTE_WRITABLE | PTE_HUGE | PTE_NX;
    }

    /* --- Kernel PDPT: entry 510 points to PD for kernel ---
     * Virtual 0xFFFFFFFF80000000 = PML4[511], PDPT[510]
     * (0xFFFFFFFF80000000 >> 30) & 0x1FF = 510 */
    pdpt_k[510] = pd_kernel | PTE_PRESENT | PTE_WRITABLE;

    /* --- Kernel PD[0]: G2 hardening — split into 512 × 4KB pages ---
     *
     * PD[0] covers virtual 0xFFFFFFFF80000000-0xFFFFFFFF801FFFFF
     * which maps to physical 0x0-0x1FFFFF (first 2MB).
     *
     * Physical 0x0-0xFFFFF (pages 0-255): EFI/BIOS firmware data.
     *   → Read-only + NX (no kernel code here, just legacy tables)
     *
     * Physical 0x100000-0x1FFFFF (pages 256-511): Kernel image.
     *   → Read-write + executable (kernel .text starts at 0x100000).
     *   NOTE: Cannot set NX here because kernel_main lives at 0x100000.
     *   The kernel VMM will later split .text(RX) from .data(RW+NX)
     *   with proper 4KB granularity at vos3_vmm_init(). */

    /* PT[0..255]: firmware region — present, read-only, no-execute */
    for (int i = 0; i < 256; i++) {
        pt_k0[i] = ((uint64_t)i * 0x1000ULL)
                    | PTE_PRESENT | PTE_NX;
    }

    /* PT[256..511]: kernel region — present, writable, executable */
    for (int i = 256; i < 512; i++) {
        pt_k0[i] = ((uint64_t)i * 0x1000ULL)
                    | PTE_PRESENT | PTE_WRITABLE;
    }

    /* PD[0] → PT (4KB-split, NOT a 2MB huge page) */
    pd_k[0] = pt_kernel0 | PTE_PRESENT | PTE_WRITABLE;

    /* --- Kernel PD[1..7]: remaining 14 MB as 2MB huge pages --- */
    for (int i = 1; i < 8; i++) {
        pd_k[i] = ((uint64_t)i * 0x200000ULL)
                   | PTE_PRESENT | PTE_WRITABLE | PTE_HUGE;
    }

    return pml4_phys;
}

/* ============================================================================
 * UEFI MEMORY MAP → VOS3 TRANSLATION
 * ============================================================================ */

/**
 * @brief Get the UEFI memory map and translate to VOS3 format
 *
 * @param bs        Boot Services
 * @param boot_info VOS3 boot info to populate
 * @param map_key   Output: memory map key (needed for ExitBootServices)
 * @return 0 on success, negative on error
 */
static int efi_get_memory_map(EFI_BOOT_SERVICES *bs,
                              vos3_boot_info_t *boot_info,
                              UINTN *map_key)
{
    UINTN mmap_size = sizeof(g_raw_mmap);
    UINTN desc_size = 0;
    uint32_t desc_version = 0;

    EFI_STATUS status = bs->GetMemoryMap(
        &mmap_size, (EFI_MEMORY_DESCRIPTOR *)g_raw_mmap,
        map_key, &desc_size, &desc_version);

    if (EFI_ERROR(status)) {
        return -1;
    }

    /* Translate UEFI descriptors to VOS3 memory map entries */
    UINTN entry_count = mmap_size / desc_size;
    uint32_t vos3_count = 0;
    uint64_t total_usable = 0;

    for (UINTN i = 0; i < entry_count && vos3_count < EFI_MMAP_MAX_ENTRIES; i++) {
        EFI_MEMORY_DESCRIPTOR *desc = (EFI_MEMORY_DESCRIPTOR *)
            (g_raw_mmap + i * desc_size);

        g_efi_mmap[vos3_count].base       = desc->PhysicalStart;
        g_efi_mmap[vos3_count].length      = desc->NumberOfPages * 4096ULL;
        g_efi_mmap[vos3_count].type        = efi_to_vos3_mmap_type(desc->Type);
        g_efi_mmap[vos3_count].attributes  = 0;

        if (g_efi_mmap[vos3_count].type == VOS3_MMAP_USABLE) {
            total_usable += g_efi_mmap[vos3_count].length;
        }

        vos3_count++;
    }

    boot_info->total_memory       = total_usable;
    boot_info->mem_map_addr       = (uint64_t)(uintptr_t)g_efi_mmap;
    boot_info->mem_map_entries    = vos3_count;
    boot_info->mem_map_entry_size = sizeof(vos3_boot_mmap_entry_t);

    return 0;
}

/* ============================================================================
 * EFI ENTRY POINT
 * ============================================================================ */

/**
 * @brief UEFI application entry point
 *
 * Called by UEFI firmware when the boot application is loaded.
 * Uses Microsoft x64 calling convention (compiled with -mabi=ms).
 *
 * @param ImageHandle Handle to the loaded image
 * @param SystemTable Pointer to the UEFI System Table
 * @return EFI_STATUS (never returns on success)
 */
EFI_STATUS efi_main(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
    EFI_BOOT_SERVICES *bs = SystemTable->BootServices;

    efi_print(SystemTable, "VOS3 UEFI Boot Stub v8.1\r\n");

    /* ---- Initialize boot info ---- */
    efi_memzero(&g_efi_boot_info, sizeof(g_efi_boot_info));
    g_efi_boot_info.magic   = VOS3_BOOT_MAGIC;
    g_efi_boot_info.version = VOS3_BOOT_VERSION;
    g_efi_boot_info.size    = sizeof(vos3_boot_info_t);
    g_efi_boot_info.flags   = VOS3_BOOT_FLAG_UEFI;

    /* ---- HHDM offset ---- */
    g_efi_boot_info.direct_map_offset = VOS3_HHDM_OFFSET;

    /* ---- Kernel location (must match linker script) ---- */
    g_efi_boot_info.kernel_phys_start = VOS3_KERNEL_LMA;
    g_efi_boot_info.kernel_phys_end   = VOS3_KERNEL_LMA + 0x400000; /* 4MB est. */
    g_efi_boot_info.kernel_virt_start = VOS3_KERNEL_VMA;
    g_efi_boot_info.kernel_virt_end   = VOS3_KERNEL_VMA + 0x400000;

    /* ---- BSP CPU info (SMP discovered later via ACPI MADT) ---- */
    g_efi_cpus[0].lapic_id     = 0;
    g_efi_cpus[0].processor_id = 0;
    g_efi_cpus[0].flags        = VOS3_CPU_FLAG_BSP | VOS3_CPU_FLAG_ENABLED;
    g_efi_boot_info.cpu_count      = 1;
    g_efi_boot_info.bsp_lapic_id   = 0;
    g_efi_boot_info.cpu_info_addr  = (uint64_t)(uintptr_t)g_efi_cpus;
    g_efi_boot_info.flags         |= VOS3_BOOT_FLAG_SMP;

    /* ---- Locate ACPI RSDP ---- */
    uint64_t rsdp = efi_find_rsdp(SystemTable);
    if (rsdp != 0) {
        g_efi_boot_info.rsdp_addr = rsdp;
        g_efi_boot_info.flags |= VOS3_BOOT_FLAG_ACPI;
        efi_print(SystemTable, "  ACPI RSDP found\r\n");
    }

    /* ---- Query GOP framebuffer ---- */
    if (efi_query_gop(SystemTable, &g_efi_boot_info.framebuffer) == 0) {
        g_efi_boot_info.flags |= VOS3_BOOT_FLAG_FRAMEBUFFER;
        efi_print(SystemTable, "  GOP framebuffer acquired\r\n");
    }

    /* ---- Build VOS3 page tables ---- */
    uint64_t pml4_phys = efi_build_page_tables(bs);
    if (pml4_phys == 0) {
        efi_print(SystemTable, "FATAL: Page table allocation failed\r\n");
        return EFI_LOAD_ERROR;
    }
    g_efi_boot_info.pml4_phys = pml4_phys;
    efi_print(SystemTable, "  Page tables built\r\n");

    /* ---- Get UEFI memory map (MUST be last before ExitBootServices) ----
     * After this call, no more UEFI memory allocations are allowed.
     * GetMemoryMap returns a MapKey that must be passed to ExitBootServices
     * IMMEDIATELY — any intervening Boot Services call invalidates it. */
    UINTN map_key = 0;
    if (efi_get_memory_map(bs, &g_efi_boot_info, &map_key) != 0) {
        efi_print(SystemTable, "FATAL: GetMemoryMap failed\r\n");
        return EFI_LOAD_ERROR;
    }
    efi_print(SystemTable, "  Memory map acquired\r\n");

    /* ---- Read TSC for boot timestamp ---- */
    uint32_t lo, hi;
    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    g_efi_boot_info.boot_timestamp = ((uint64_t)hi << 32) | lo;

    /* ---- T1 hardening: Load Guard IDT ----
     * Install a minimal IDT where every vector points to efi_guard_halt().
     * If an NMI (vector 2) or other non-maskable interrupt fires between
     * ExitBootServices and the CR3 switch, the CPU halts cleanly instead
     * of executing stale firmware interrupt handlers.  CLI only masks
     * maskable interrupts; NMIs, machine checks, and debug exceptions
     * bypass CLI entirely.  The Guard IDT catches all of them. */
    efi_load_guard_idt();

    /* ---- Exit Boot Services ----
     * After this call, UEFI firmware is no longer available.
     * We own all memory.  ConOut is dead — no more efi_print().
     * If ExitBootServices fails (map key stale), we must re-call
     * GetMemoryMap and retry ONCE. */
    EFI_STATUS exit_status = bs->ExitBootServices(ImageHandle, map_key);
    if (EFI_ERROR(exit_status)) {
        /* Retry: GetMemoryMap → ExitBootServices (UEFI spec § 7.2) */
        UINTN mmap_size2 = sizeof(g_raw_mmap);
        UINTN desc_size2 = 0;
        uint32_t desc_ver2 = 0;
        bs->GetMemoryMap(&mmap_size2, (EFI_MEMORY_DESCRIPTOR *)g_raw_mmap,
                         &map_key, &desc_size2, &desc_ver2);
        exit_status = bs->ExitBootServices(ImageHandle, map_key);
        if (EFI_ERROR(exit_status)) {
            /* Unrecoverable — firmware broken */
            for (;;) { __asm__ volatile("hlt"); }
        }
    }

    /* ============================================================
     * POST-ExitBootServices: NO UEFI SERVICES AVAILABLE
     * Interrupts are disabled.  We own the machine.
     * ============================================================ */

    /* Disable interrupts (should already be disabled, but be safe) */
    __asm__ volatile("cli");

    /* ---- Enable NXE in EFER (required for W^X) ---- */
    uint32_t efer_lo, efer_hi;
    __asm__ volatile(
        "mov $0xC0000080, %%ecx\n\t"
        "rdmsr"
        : "=a"(efer_lo), "=d"(efer_hi)
        :
        : "ecx"
    );
    efer_lo |= (1U << 11);  /* NXE bit */
    __asm__ volatile(
        "mov $0xC0000080, %%ecx\n\t"
        "wrmsr"
        :
        : "a"(efer_lo), "d"(efer_hi)
        : "ecx"
    );

    /* ---- F2/F3 hardening: Program PAT MSR ----
     * Set PAT4 = Write-Combining (WC, 0x01) instead of default WB (0x06).
     * This allows the kernel VMM to map the framebuffer with WC caching
     * by selecting PAT index 4 via PTE bits {PAT=1, PCD=0, PWT=0}.
     * Programming the PAT after EFER.NXE but before CR3 ensures the new
     * caching attributes are active when page tables take effect.
     *
     * MSR 0x277 = IA32_PAT (Intel SDM Vol 3A §12.12.4)
     * Value: 0x0007040100070406 (PAT4=WC, rest unchanged from default) */
    {
        uint32_t pat_lo = (uint32_t)(VOS3_PAT_VALUE & 0xFFFFFFFFULL);
        uint32_t pat_hi = (uint32_t)(VOS3_PAT_VALUE >> 32);
        __asm__ volatile(
            "mov $0x277, %%ecx\n\t"
            "wrmsr"
            :
            : "a"(pat_lo), "d"(pat_hi)
            : "ecx"
        );
    }

    /* ---- Switch to VOS3 page tables ---- */
    __asm__ volatile("mov %0, %%cr3" : : "r"(pml4_phys) : "memory");

    /* ---- Update boot_info pointer to HHDM virtual address ----
     * The boot_info struct is in the identity-mapped region.
     * After CR3 switch, HHDM is active, so we can compute the
     * virtual address and use it for the kernel call. */
    vos3_boot_info_t *boot_info_virt = (vos3_boot_info_t *)(
        (uintptr_t)&g_efi_boot_info + VOS3_HHDM_OFFSET);

    /* ---- Also remap the memory map pointer to HHDM ---- */
    boot_info_virt->mem_map_addr =
        (uint64_t)((uintptr_t)g_efi_mmap + VOS3_HHDM_OFFSET);
    boot_info_virt->cpu_info_addr =
        (uint64_t)((uintptr_t)g_efi_cpus + VOS3_HHDM_OFFSET);

    /* ---- Jump to kernel entry point ----
     * VOS3_KERNEL_ENTRY is the virtual address of kernel_main(),
     * extracted from the kernel ELF at build time via:
     *   nm build/vos3.elf | grep " T kernel_main"
     * The Makefile passes this as -DVOS3_KERNEL_ENTRY=0x...
     *
     * PML4[511] maps the kernel higher-half range, so the jump
     * from identity-mapped EFI code to higher-half kernel is valid
     * as long as the identity map (PML4[0]) is still active for
     * the current instruction pointer. */
#ifndef VOS3_KERNEL_ENTRY
#error "VOS3_KERNEL_ENTRY must be defined (use: make efi)"
#endif

    /* CRITICAL ABI TRANSITION + REGISTER PURITY:
     *
     * T2 fix: kernel_main() uses System V AMD64 ABI (first arg in RDI).
     * This TU uses MS ABI (first arg in RCX).  We handle the transition
     * explicitly via inline asm rather than relying on __attribute__((sysv_abi))
     * — this also gives us GPR zeroing for register purity.
     *
     * Register state entering kernel_main:
     *   RDI = boot_info_virt (System V first argument)
     *   RAX = kernel entry address (used for indirect jump)
     *   All other GPRs zeroed — no stale EFI data leaks into kernel.
     *
     * We use `jmp *%%rax` (not CALL) because kernel_main never returns
     * and we don't want a stale return address on the stack. */
    __asm__ volatile(
        "xor %%rbx, %%rbx\n\t"    /* Zero RBX */
        "xor %%rcx, %%rcx\n\t"    /* Zero RCX (MS ABI leftover) */
        "xor %%rdx, %%rdx\n\t"    /* Zero RDX */
        "xor %%rsi, %%rsi\n\t"    /* Zero RSI */
        "xor %%rbp, %%rbp\n\t"    /* Zero RBP (frame pointer) */
        "xor %%r8,  %%r8\n\t"     /* Zero R8 */
        "xor %%r9,  %%r9\n\t"     /* Zero R9 */
        "xor %%r10, %%r10\n\t"    /* Zero R10 */
        "xor %%r11, %%r11\n\t"    /* Zero R11 */
        "xor %%r12, %%r12\n\t"    /* Zero R12 */
        "xor %%r13, %%r13\n\t"    /* Zero R13 */
        "xor %%r14, %%r14\n\t"    /* Zero R14 */
        "xor %%r15, %%r15\n\t"    /* Zero R15 */
        "jmp *%%rax"               /* Jump to kernel_main (never returns) */
        :
        : "a"((uint64_t)VOS3_KERNEL_ENTRY),  /* RAX = entry address */
          "D"((uint64_t)(uintptr_t)boot_info_virt) /* RDI = boot_info */
        : "rbx", "rcx", "rdx", "rsi",
          "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15",
          "memory"
        /* NOTE: rbp zeroed by xor above but not in clobber list because
         * GCC requires rbp as frame pointer.  Safe: we jmp, never return. */
    );

    /* Should never reach here */
    __builtin_unreachable();
}

#endif /* VOS3_EFI_STUB */
