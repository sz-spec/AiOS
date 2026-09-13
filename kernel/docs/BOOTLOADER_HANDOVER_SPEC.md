# VOS3 Bootloader Handover Specification

**Version:** 1.0.0
**Date:** 2026-02-15
**Status:** Draft
**Target Architecture:** x86_64 (AMD64)

---

## 1. Overview

This document specifies the interface between the bootloader and the VOS3 kernel. The bootloader is responsible for:

1. Loading the kernel ELF into memory
2. Setting up initial page tables (identity + higher-half mapping)
3. Entering 64-bit Long Mode
4. Providing hardware information via the Boot Information Structure
5. Transferring control to the kernel entry point

### 1.1 Supported Boot Protocols

| Protocol | Version | Status |
|----------|---------|--------|
| Limine | 8.x+ | Primary |
| Multiboot2 | 2.0 | Supported |
| UEFI Direct | - | Planned |

---

## 2. Memory Layout at Handover

### 2.1 Physical Memory Map

```
┌──────────────────────────────────────────────────────────────┐
│ 0x0000_0000_0000_0000 - 0x0000_0000_0000_0FFF : Reserved     │
│ 0x0000_0000_0000_1000 - 0x0000_0000_0007_FFFF : Usable       │
│ 0x0000_0000_0008_0000 - 0x0000_0000_0009_FFFF : EBDA         │
│ 0x0000_0000_000A_0000 - 0x0000_0000_000F_FFFF : Video/ROM    │
├──────────────────────────────────────────────────────────────┤
│ 0x0000_0000_0010_0000 - 0x0000_0000_001F_FFFF : Extended     │
│ 0x0000_0000_0020_0000 - Kernel Load Address                  │
├──────────────────────────────────────────────────────────────┤
│ KERNEL_PHYS_START     - KERNEL_PHYS_END       : Kernel Image │
│ KERNEL_PHYS_END       - ...                   : Free Memory  │
├──────────────────────────────────────────────────────────────┤
│ 0x0000_0000_FEC0_0000 - 0x0000_0000_FED0_0000 : IOAPIC       │
│ 0x0000_0000_FEE0_0000 - 0x0000_0000_FEE0_1000 : Local APIC   │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Virtual Memory Map (Higher-Half)

```
┌──────────────────────────────────────────────────────────────┐
│ VIRTUAL ADDRESS SPACE                                        │
├──────────────────────────────────────────────────────────────┤
│ 0xFFFF_8000_0000_0000 - Physical Memory Direct Map (8 TiB)   │
│ 0xFFFF_8800_0000_0000 - MMIO Mappings (8 TiB)               │
│ 0xFFFF_9000_0000_0000 - vmalloc Region (16 TiB)             │
│ 0xFFFF_A000_0000_0000 - Per-CPU Data (1 TiB)                │
│ 0xFFFF_A100_0000_0000 - Kernel Stacks (1 TiB)               │
├──────────────────────────────────────────────────────────────┤
│ 0xFFFF_FFFF_8000_0000 - Kernel .text (512 MiB)              │
│ 0xFFFF_FFFF_A000_0000 - Kernel .data/.bss (512 MiB)         │
│ 0xFFFF_FFFF_C000_0000 - Kernel Modules (512 MiB)            │
│ 0xFFFF_FFFF_E000_0000 - Fixed Mappings (512 MiB)            │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. CPU State Requirements

### 3.1 Mode Requirements

| Register/Flag | Required State |
|--------------|----------------|
| CS | Long Mode Code Segment (L=1, D=0) |
| EFER.LME | 1 (Long Mode Enable) |
| EFER.LMA | 1 (Long Mode Active) |
| CR0.PE | 1 (Protected Mode) |
| CR0.PG | 1 (Paging Enabled) |
| CR4.PAE | 1 (Physical Address Extension) |
| CR4.PGE | 1 (Page Global Enable) |
| RFLAGS.IF | 0 (Interrupts Disabled) |

### 3.2 Register State

```c
// At kernel entry:
// RAX = Magic number (0x564F5333 = "VOS3")
// RBX = Physical address of Boot Info Structure
// RCX = 0 (Reserved)
// RDX = 0 (Reserved)
// RSP = Initial kernel stack (minimum 64 KiB)
// RBP = 0
// RSI = 0
// RDI = 0
// R8-R15 = 0
```

### 3.3 Segment Registers

```c
// Segment selectors (flat model):
// CS = 0x08 (Kernel Code, Ring 0, Long Mode)
// DS = 0x10 (Kernel Data, Ring 0)
// ES = 0x10
// FS = 0x10
// GS = 0x10
// SS = 0x10
```

---

## 4. Boot Information Structure

### 4.1 Magic Number

```c
#define VOS3_BOOT_MAGIC     0x564F5333U  /* "VOS3" */
#define VOS3_BOOT_VERSION   0x00010000U  /* Version 1.0 */
```

### 4.2 Main Boot Info Structure

```c
/**
 * @brief VOS3 Boot Information Structure
 * @note Passed from bootloader to kernel
 */
typedef struct __attribute__((packed)) vos3_boot_info {
    /* Header */
    uint32_t magic;             /**< Magic: 0x564F5333 ("VOS3") */
    uint32_t version;           /**< Structure version */
    uint32_t size;              /**< Total structure size */
    uint32_t flags;             /**< Feature flags */

    /* Memory Information */
    uint64_t total_memory;      /**< Total usable RAM (bytes) */
    uint64_t mem_map_addr;      /**< Physical addr of memory map */
    uint32_t mem_map_entries;   /**< Number of memory map entries */
    uint32_t mem_map_entry_size;/**< Size of each entry */

    /* Kernel Location */
    uint64_t kernel_phys_start; /**< Kernel physical start */
    uint64_t kernel_phys_end;   /**< Kernel physical end */
    uint64_t kernel_virt_start; /**< Kernel virtual start */
    uint64_t kernel_virt_end;   /**< Kernel virtual end */

    /* Initial Page Tables */
    uint64_t pml4_phys;         /**< PML4 physical address */

    /* Framebuffer (if available) */
    uint64_t fb_addr;           /**< Framebuffer physical address */
    uint32_t fb_width;          /**< Width in pixels */
    uint32_t fb_height;         /**< Height in pixels */
    uint32_t fb_pitch;          /**< Bytes per scanline */
    uint32_t fb_bpp;            /**< Bits per pixel */
    uint8_t  fb_red_mask_size;  /**< Red mask size */
    uint8_t  fb_red_mask_shift; /**< Red mask shift */
    uint8_t  fb_green_mask_size;
    uint8_t  fb_green_mask_shift;
    uint8_t  fb_blue_mask_size;
    uint8_t  fb_blue_mask_shift;
    uint16_t fb_reserved;

    /* ACPI Tables */
    uint64_t rsdp_addr;         /**< RSDP physical address */

    /* SMP Information */
    uint32_t cpu_count;         /**< Number of CPUs detected */
    uint32_t bsp_lapic_id;      /**< BSP Local APIC ID */
    uint64_t smp_info_addr;     /**< SMP info structure address */

    /* Boot Command Line */
    uint64_t cmdline_addr;      /**< Command line string address */
    uint32_t cmdline_size;      /**< Command line length */

    /* Module Information */
    uint32_t module_count;      /**< Number of loaded modules */
    uint64_t modules_addr;      /**< Module array address */

    /* Timestamps */
    uint64_t boot_timestamp;    /**< TSC at boot */

    /* Reserved for future use */
    uint8_t  reserved[64];

} vos3_boot_info_t;
```

### 4.3 Memory Map Entry

```c
typedef struct __attribute__((packed)) vos3_boot_mmap_entry {
    uint64_t base;      /**< Region base address */
    uint64_t length;    /**< Region length */
    uint32_t type;      /**< Region type */
    uint32_t attributes;/**< Region attributes */
} vos3_boot_mmap_entry_t;

/* Memory types */
#define VOS3_MMAP_USABLE        1U  /* Usable RAM */
#define VOS3_MMAP_RESERVED      2U  /* Reserved/unusable */
#define VOS3_MMAP_ACPI_RECLAIM  3U  /* ACPI reclaimable */
#define VOS3_MMAP_ACPI_NVS      4U  /* ACPI NVS */
#define VOS3_MMAP_BAD           5U  /* Bad memory */
#define VOS3_MMAP_BOOTLOADER    6U  /* Bootloader reclaimable */
#define VOS3_MMAP_KERNEL        7U  /* Kernel and modules */
#define VOS3_MMAP_FRAMEBUFFER   8U  /* Framebuffer */
```

### 4.4 SMP Information

```c
typedef struct __attribute__((packed)) vos3_boot_cpu_info {
    uint32_t lapic_id;      /**< Local APIC ID */
    uint32_t processor_id;  /**< ACPI Processor ID */
    uint32_t flags;         /**< CPU flags */
    uint32_t reserved;
} vos3_boot_cpu_info_t;

/* CPU flags */
#define VOS3_CPU_FLAG_BSP       (1U << 0)  /* Bootstrap Processor */
#define VOS3_CPU_FLAG_ENABLED   (1U << 1)  /* CPU is enabled */
#define VOS3_CPU_FLAG_ONLINE    (1U << 2)  /* CPU is online */
```

### 4.5 Module Information

```c
typedef struct __attribute__((packed)) vos3_boot_module {
    uint64_t phys_start;    /**< Module physical start */
    uint64_t phys_end;      /**< Module physical end */
    uint64_t cmdline_addr;  /**< Module command line */
} vos3_boot_module_t;
```

---

## 5. Initial Page Table Setup

### 5.1 Required Mappings

The bootloader must set up the following page table mappings before kernel entry:

| Virtual Range | Physical Range | Size | Flags |
|--------------|----------------|------|-------|
| Identity (0-4GiB) | 0-4GiB | 4 GiB | P, W, G |
| Higher-half kernel | Kernel physical | Kernel size | P, W, G, NX (data) |
| Direct map base | 0 | 4 GiB minimum | P, W, G |

### 5.2 Page Table Flags

```c
#define PT_PRESENT      (1ULL << 0)
#define PT_WRITABLE     (1ULL << 1)
#define PT_USER         (1ULL << 2)
#define PT_WRITE_THRU   (1ULL << 3)
#define PT_CACHE_DIS    (1ULL << 4)
#define PT_ACCESSED     (1ULL << 5)
#define PT_DIRTY        (1ULL << 6)
#define PT_HUGE         (1ULL << 7)   /* 2MiB/1GiB page */
#define PT_GLOBAL       (1ULL << 8)
#define PT_NO_EXECUTE   (1ULL << 63)
```

### 5.3 Page Table Structure

```
PML4 (512 entries, each covers 512 GiB)
├── Entry 0: Identity map (first 512 GiB)
│   └── PDPT[0-3]: 0-4 GiB with 2MiB pages
├── Entry 256: Direct physical map start (0xFFFF800000000000)
│   └── PDPT: Maps physical RAM
└── Entry 511: Higher-half kernel (0xFFFFFFFF80000000)
    └── PDPT → PD → PT: Kernel code/data
```

---

## 6. Kernel Entry Point

### 6.1 Entry Function Signature

```c
/**
 * @brief Kernel entry point
 * @param magic Boot magic number (RAX)
 * @param boot_info Boot information structure address (RBX)
 * @note Called in Long Mode with paging enabled
 * @note Interrupts are disabled
 * @note Must not return
 */
__attribute__((noreturn))
void vos3_kernel_entry(uint32_t magic, vos3_boot_info_t* boot_info);
```

### 6.2 Linker Script Requirements

```ld
/* kernel.ld */
ENTRY(vos3_kernel_entry)

KERNEL_PHYS_BASE = 0x200000;
KERNEL_VIRT_BASE = 0xFFFFFFFF80000000;

SECTIONS
{
    . = KERNEL_VIRT_BASE;

    .text ALIGN(4K) : AT(ADDR(.text) - KERNEL_VIRT_BASE + KERNEL_PHYS_BASE)
    {
        *(.text.boot)    /* Entry point first */
        *(.text .text.*)
    }

    .rodata ALIGN(4K) : AT(ADDR(.rodata) - KERNEL_VIRT_BASE + KERNEL_PHYS_BASE)
    {
        *(.rodata .rodata.*)
    }

    .data ALIGN(4K) : AT(ADDR(.data) - KERNEL_VIRT_BASE + KERNEL_PHYS_BASE)
    {
        *(.data .data.*)
    }

    .bss ALIGN(4K) : AT(ADDR(.bss) - KERNEL_VIRT_BASE + KERNEL_PHYS_BASE)
    {
        *(COMMON)
        *(.bss .bss.*)
    }

    KERNEL_END = .;
}
```

---

## 7. Boot Sequence Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        BOOTLOADER                               │
├─────────────────────────────────────────────────────────────────┤
│ 1. BIOS/UEFI initialization                                    │
│ 2. Load kernel ELF from disk                                   │
│ 3. Parse ELF, load segments to physical memory                 │
│ 4. Detect memory map (E820 / UEFI GetMemoryMap)               │
│ 5. Detect ACPI tables (RSDP)                                   │
│ 6. Set up initial GDT (flat model)                             │
│ 7. Set up initial page tables:                                 │
│    - Identity map: 0-4GiB                                      │
│    - Higher-half: 0xFFFFFFFF80000000 → kernel physical         │
│    - Direct map: 0xFFFF800000000000 → 0 physical               │
│ 8. Enter Long Mode (if not already)                            │
│ 9. Build boot_info structure                                   │
│ 10. Set up initial stack                                       │
│ 11. Jump to kernel entry point                                 │
├─────────────────────────────────────────────────────────────────┤
│                        KERNEL ENTRY                             │
├─────────────────────────────────────────────────────────────────┤
│ 12. Validate boot magic                                        │
│ 13. Initialize BSS section                                     │
│ 14. Set up permanent GDT                                       │
│ 15. Set up IDT (minimal exception handlers)                    │
│ 16. Initialize physical memory manager                         │
│ 17. Initialize virtual memory manager                          │
│ 18. Set up permanent kernel page tables                        │
│ 19. Initialize per-CPU structures (BSP)                        │
│ 20. Initialize APIC                                            │
│ 21. Initialize other subsystems                                │
│ 22. Start AP cores (SMP)                                       │
│ 23. Enter scheduler                                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 8. Error Handling

### 8.1 Boot Failure Codes

```c
#define VOS3_BOOT_OK            0x00U
#define VOS3_BOOT_ERR_MAGIC     0x01U  /* Invalid magic number */
#define VOS3_BOOT_ERR_VERSION   0x02U  /* Unsupported version */
#define VOS3_BOOT_ERR_MEMORY    0x03U  /* Insufficient memory */
#define VOS3_BOOT_ERR_CPU       0x04U  /* CPU not supported */
#define VOS3_BOOT_ERR_PAGING    0x05U  /* Page table error */
```

### 8.2 Early Panic

If the kernel detects an error before console initialization:

1. Write error code to port 0x3F8 (COM1)
2. Write error code to port 0x80 (POST code)
3. Halt all CPUs

---

## 9. Compatibility Notes

### 9.1 Limine Protocol

When using Limine bootloader, the following request tags are used:

```c
static volatile struct limine_memmap_request memmap_request = {
    .id = LIMINE_MEMMAP_REQUEST,
    .revision = 0
};

static volatile struct limine_kernel_address_request kernel_address_request = {
    .id = LIMINE_KERNEL_ADDRESS_REQUEST,
    .revision = 0
};

static volatile struct limine_hhdm_request hhdm_request = {
    .id = LIMINE_HHDM_REQUEST,
    .revision = 0
};
```

### 9.2 Multiboot2 Header

```c
.section .multiboot2
.align 8
multiboot2_header:
    .long 0xE85250D6                    /* Magic */
    .long 0                             /* Architecture: i386 */
    .long multiboot2_header_end - multiboot2_header
    .long -(0xE85250D6 + 0 + (multiboot2_header_end - multiboot2_header))

    /* Tags here */

    /* End tag */
    .word 0
    .word 0
    .long 8
multiboot2_header_end:
```

---

## 10. Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-02-15 | VOS3 Team | Initial specification |

---

## Appendix A: Quick Reference

### A.1 Key Addresses

| Name | Virtual Address | Physical Address |
|------|-----------------|------------------|
| Kernel Base | 0xFFFFFFFF80000000 | 0x200000 |
| Direct Map | 0xFFFF800000000000 | 0x0 |
| Kernel Stack (BSP) | 0xFFFFA10000010000 | Dynamic |

### A.2 Key Constants

```c
#define VOS3_PAGE_SIZE          4096ULL
#define VOS3_KERNEL_PHYS_BASE   0x200000ULL
#define VOS3_KERNEL_VIRT_BASE   0xFFFFFFFF80000000ULL
#define VOS3_DIRECT_MAP_BASE    0xFFFF800000000000ULL
#define VOS3_BOOT_MAGIC         0x564F5333U
```
