/**
 * @file efi.h
 * @brief Freestanding UEFI Type Definitions for VOS3
 *
 * @details Minimal, self-contained UEFI type definitions sufficient
 *          for the VOS3 EFI boot stub.  No dependency on GNU-EFI or
 *          EDK-II.  Matches UEFI Specification 2.10 (March 2024).
 *
 *          Types defined:
 *          - Core types (EFI_STATUS, EFI_HANDLE, etc.)
 *          - System Table + Boot Services (memory map, protocol locate)
 *          - Graphics Output Protocol (GOP) for framebuffer
 *          - ACPI 2.0 GUID for RSDP discovery
 *
 * @version 8.1.0
 * @date 2026-04-10
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_EFI_H
#define VOS3_EFI_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * UEFI BASE TYPES (Spec 2.10 Chapter 2)
 * ============================================================================
 *
 * On x86_64, UEFI uses the Microsoft x64 calling convention (MS ABI):
 *   - First 4 args in RCX, RDX, R8, R9
 *   - Return value in RAX
 *   - Caller allocates 32-byte shadow space on stack
 *   - Stack 16-byte aligned before CALL
 * ============================================================================ */

typedef uint64_t        EFI_STATUS;
typedef void*           EFI_HANDLE;
typedef void*           EFI_EVENT;
typedef uint64_t        EFI_TPL;
typedef uint64_t        EFI_PHYSICAL_ADDRESS;
typedef uint64_t        EFI_VIRTUAL_ADDRESS;
typedef uint64_t        UINTN;
typedef int64_t         INTN;
typedef uint16_t        CHAR16;
typedef uint8_t         BOOLEAN;

/* EFI_STATUS codes (high bit = error) */
#define EFI_SUCCESS                  0ULL
#define EFI_LOAD_ERROR               (0x8000000000000001ULL)
#define EFI_INVALID_PARAMETER        (0x8000000000000002ULL)
#define EFI_UNSUPPORTED              (0x8000000000000003ULL)
#define EFI_BAD_BUFFER_SIZE          (0x8000000000000004ULL)
#define EFI_BUFFER_TOO_SMALL         (0x8000000000000005ULL)
#define EFI_NOT_READY                (0x8000000000000006ULL)
#define EFI_NOT_FOUND                (0x800000000000000EULL)

#define EFI_ERROR(status)   ((INTN)(status) < 0)

/* ============================================================================
 * EFI_GUID (Spec 2.10 Chapter 2.3.1)
 * ============================================================================ */

typedef struct {
    uint32_t Data1;
    uint16_t Data2;
    uint16_t Data3;
    uint8_t  Data4[8];
} EFI_GUID;

/* Well-known GUIDs */

/** @brief ACPI 2.0+ RSDP GUID (Table 4-6) */
#define EFI_ACPI_20_TABLE_GUID \
    ((EFI_GUID){0x8868E871, 0xE4F1, 0x11D3, \
     {0xBC, 0x22, 0x00, 0x80, 0xC7, 0x3C, 0x88, 0x81}})

/** @brief ACPI 1.0 RSDP GUID */
#define EFI_ACPI_TABLE_GUID \
    ((EFI_GUID){0xEB9D2D30, 0x2D88, 0x11D3, \
     {0x9A, 0x16, 0x00, 0x90, 0x27, 0x3F, 0xC1, 0x4D}})

/** @brief EFI Graphics Output Protocol GUID */
#define EFI_GRAPHICS_OUTPUT_PROTOCOL_GUID \
    ((EFI_GUID){0x9042A9DE, 0x23DC, 0x4A38, \
     {0x96, 0xFB, 0x7A, 0xDE, 0xD0, 0x80, 0x51, 0x6A}})

/* ============================================================================
 * EFI_TABLE_HEADER (Spec 2.10 Chapter 4.2)
 * ============================================================================ */

typedef struct {
    uint64_t    Signature;
    uint32_t    Revision;
    uint32_t    HeaderSize;
    uint32_t    CRC32;
    uint32_t    Reserved;
} EFI_TABLE_HEADER;

/* ============================================================================
 * EFI MEMORY DESCRIPTOR (Spec 2.10 Chapter 7.2)
 * ============================================================================ */

/** @brief UEFI memory types */
#define EFI_MMAP_RESERVED               0U
#define EFI_MMAP_LOADER_CODE            1U
#define EFI_MMAP_LOADER_DATA            2U
#define EFI_MMAP_BOOT_SERVICES_CODE     3U
#define EFI_MMAP_BOOT_SERVICES_DATA     4U
#define EFI_MMAP_RUNTIME_SERVICES_CODE  5U
#define EFI_MMAP_RUNTIME_SERVICES_DATA  6U
#define EFI_MMAP_CONVENTIONAL           7U
#define EFI_MMAP_UNUSABLE               8U
#define EFI_MMAP_ACPI_RECLAIM           9U
#define EFI_MMAP_ACPI_NVS              10U
#define EFI_MMAP_MMIO                  11U
#define EFI_MMAP_MMIO_PORT_SPACE       12U
#define EFI_MMAP_PAL_CODE             13U
#define EFI_MMAP_PERSISTENT           14U
#define EFI_MMAP_UNACCEPTED           15U

/** @brief UEFI memory attributes */
#define EFI_MEMORY_UC   0x0000000000000001ULL
#define EFI_MEMORY_WC   0x0000000000000002ULL
#define EFI_MEMORY_WT   0x0000000000000004ULL
#define EFI_MEMORY_WB   0x0000000000000008ULL
#define EFI_MEMORY_RT   0x8000000000000000ULL  /* Needs runtime mapping */

typedef struct {
    uint32_t                Type;
    EFI_PHYSICAL_ADDRESS    PhysicalStart;
    EFI_VIRTUAL_ADDRESS     VirtualStart;
    uint64_t                NumberOfPages;
    uint64_t                Attribute;
} EFI_MEMORY_DESCRIPTOR;

/** @brief Allocate type for AllocatePages */
typedef enum {
    AllocateAnyPages,
    AllocateMaxAddress,
    AllocateAddress,
    MaxAllocateType
} EFI_ALLOCATE_TYPE;

/* ============================================================================
 * EFI_CONFIGURATION_TABLE (Spec 2.10 Chapter 4.6)
 * ============================================================================ */

typedef struct {
    EFI_GUID    VendorGuid;
    void*       VendorTable;
} EFI_CONFIGURATION_TABLE;

/* ============================================================================
 * SIMPLE TEXT OUTPUT PROTOCOL (Spec 2.10 Chapter 12.4)
 *
 * Minimal definition — only OutputString used for early error messages.
 * ============================================================================ */

typedef struct _EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL;

typedef EFI_STATUS (*EFI_TEXT_RESET)(
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL  *This,
    BOOLEAN                         ExtendedVerification
);

typedef EFI_STATUS (*EFI_TEXT_STRING)(
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL  *This,
    CHAR16                          *String
);

struct _EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL {
    EFI_TEXT_RESET      Reset;
    EFI_TEXT_STRING      OutputString;
    /* Additional fields not needed for boot stub */
};

/* ============================================================================
 * EFI_BOOT_SERVICES (Spec 2.10 Chapter 7 — Partial)
 *
 * Only the functions needed by the VOS3 boot stub are defined.
 * Unused slots are void* to maintain correct offsets in the table.
 * ============================================================================ */

typedef struct _EFI_BOOT_SERVICES {
    EFI_TABLE_HEADER    Hdr;

    /* Task Priority Services (2 entries) */
    void*               RaiseTPL;               /* 0 */
    void*               RestoreTPL;             /* 1 */

    /* Memory Services (5 entries) */
    EFI_STATUS (*AllocatePages)(                /* 2 */
        EFI_ALLOCATE_TYPE       Type,
        UINTN                   MemoryType,
        UINTN                   Pages,
        EFI_PHYSICAL_ADDRESS    *Memory
    );
    EFI_STATUS (*FreePages)(                    /* 3 */
        EFI_PHYSICAL_ADDRESS    Memory,
        UINTN                   Pages
    );
    EFI_STATUS (*GetMemoryMap)(                 /* 4 */
        UINTN                   *MemoryMapSize,
        EFI_MEMORY_DESCRIPTOR   *MemoryMap,
        UINTN                   *MapKey,
        UINTN                   *DescriptorSize,
        uint32_t                *DescriptorVersion
    );
    EFI_STATUS (*AllocatePool)(                 /* 5 */
        UINTN                   PoolType,
        UINTN                   Size,
        void                    **Buffer
    );
    EFI_STATUS (*FreePool)(                     /* 6 */
        void                    *Buffer
    );

    /* Event & Timer Services (6 entries) */
    void*               CreateEvent;            /* 7 */
    void*               SetTimer;               /* 8 */
    void*               WaitForEvent;           /* 9 */
    void*               SignalEvent;            /* 10 */
    void*               CloseEvent;             /* 11 */
    void*               CheckEvent;             /* 12 */

    /* Protocol Handler Services (6 entries) */
    void*               InstallProtocolInterface;   /* 13 */
    void*               ReinstallProtocolInterface; /* 14 */
    void*               UninstallProtocolInterface; /* 15 */
    EFI_STATUS (*HandleProtocol)(               /* 16 */
        EFI_HANDLE          Handle,
        EFI_GUID            *Protocol,
        void                **Interface
    );
    void*               Reserved;               /* 17 */
    void*               RegisterProtocolNotify; /* 18 */
    void*               LocateHandle;           /* 19 */
    void*               LocateDevicePath;       /* 20 */
    void*               InstallConfigurationTable; /* 21 */

    /* Image Services (5 entries) */
    void*               LoadImage;              /* 22 */
    void*               StartImage;             /* 23 */
    void*               Exit;                   /* 24 */
    void*               UnloadImage;            /* 25 */
    EFI_STATUS (*ExitBootServices)(             /* 26 */
        EFI_HANDLE          ImageHandle,
        UINTN               MapKey
    );

    /* Miscellaneous Services (3 entries) */
    void*               GetNextMonotonicCount;  /* 27 */
    void*               Stall;                  /* 28 */
    void*               SetWatchdogTimer;       /* 29 */

    /* DriverSupport Services (2 entries) */
    void*               ConnectController;      /* 30 */
    void*               DisconnectController;   /* 31 */

    /* Open and Close Protocol Services (3 entries) */
    void*               OpenProtocol;           /* 32 */
    void*               CloseProtocol;          /* 33 */
    void*               OpenProtocolInformation; /* 34 */

    /* Library Services (3 entries) */
    void*               ProtocolsPerHandle;     /* 35 */
    void*               LocateHandleBuffer;     /* 36 */
    EFI_STATUS (*LocateProtocol)(               /* 37 */
        EFI_GUID    *Protocol,
        void        *Registration,
        void        **Interface
    );

    /* Additional services not needed */
} EFI_BOOT_SERVICES;

/* ============================================================================
 * EFI_SYSTEM_TABLE (Spec 2.10 Chapter 4.3)
 * ============================================================================ */

typedef struct {
    EFI_TABLE_HEADER                    Hdr;
    CHAR16                              *FirmwareVendor;
    uint32_t                            FirmwareRevision;
    EFI_HANDLE                          ConsoleInHandle;
    void*                               ConIn;      /* EFI_SIMPLE_TEXT_INPUT_PROTOCOL */
    EFI_HANDLE                          ConsoleOutHandle;
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL     *ConOut;
    EFI_HANDLE                          StandardErrorHandle;
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL     *StdErr;
    void*                               RuntimeServices;
    EFI_BOOT_SERVICES                  *BootServices;
    UINTN                               NumberOfTableEntries;
    EFI_CONFIGURATION_TABLE            *ConfigurationTable;
} EFI_SYSTEM_TABLE;

/* ============================================================================
 * EFI GRAPHICS OUTPUT PROTOCOL (Spec 2.10 Chapter 12.9)
 * ============================================================================ */

typedef enum {
    PixelRedGreenBlueReserved8BitPerColor,
    PixelBlueGreenRedReserved8BitPerColor,
    PixelBitMask,
    PixelBltOnly,
    PixelFormatMax
} EFI_GRAPHICS_PIXEL_FORMAT;

typedef struct {
    uint32_t    RedMask;
    uint32_t    GreenMask;
    uint32_t    BlueMask;
    uint32_t    ReservedMask;
} EFI_PIXEL_BITMASK;

typedef struct {
    uint32_t                    Version;
    uint32_t                    HorizontalResolution;
    uint32_t                    VerticalResolution;
    EFI_GRAPHICS_PIXEL_FORMAT   PixelFormat;
    EFI_PIXEL_BITMASK           PixelInformation;
    uint32_t                    PixelsPerScanLine;
} EFI_GRAPHICS_OUTPUT_MODE_INFORMATION;

typedef struct {
    uint32_t                                MaxMode;
    uint32_t                                Mode;
    EFI_GRAPHICS_OUTPUT_MODE_INFORMATION    *Info;
    UINTN                                   SizeOfInfo;
    EFI_PHYSICAL_ADDRESS                    FrameBufferBase;
    UINTN                                   FrameBufferSize;
} EFI_GRAPHICS_OUTPUT_PROTOCOL_MODE;

typedef struct _EFI_GRAPHICS_OUTPUT_PROTOCOL {
    void*                               QueryMode;
    void*                               SetMode;
    void*                               Blt;
    EFI_GRAPHICS_OUTPUT_PROTOCOL_MODE   *Mode;
} EFI_GRAPHICS_OUTPUT_PROTOCOL;

/* ============================================================================
 * GUID COMPARISON HELPER
 * ============================================================================ */

/**
 * @brief Compare two EFI GUIDs
 * @return 1 if equal, 0 if different
 */
static inline int efi_guid_equal(const EFI_GUID *a, const EFI_GUID *b)
{
    if (a->Data1 != b->Data1) return 0;
    if (a->Data2 != b->Data2) return 0;
    if (a->Data3 != b->Data3) return 0;
    for (int i = 0; i < 8; i++) {
        if (a->Data4[i] != b->Data4[i]) return 0;
    }
    return 1;
}

#endif /* VOS3_EFI_H */
