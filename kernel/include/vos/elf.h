/**
 * @file elf.h
 * @brief VOS3 ELF Format Definitions
 *
 * @details ELF64 structures for x86_64 executable loading.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_ELF_H
#define VOS3_ELF_H

#include <stdint.h>
#include <stddef.h>

/* ============================================================================
 * ELF MAGIC AND IDENTIFICATION
 * ============================================================================ */

/** @brief ELF magic bytes */
#define ELF_MAGIC0      0x7FU
#define ELF_MAGIC1      'E'
#define ELF_MAGIC2      'L'
#define ELF_MAGIC3      'F'

/** @brief ELF identification indices */
#define EI_MAG0         0U      /**< Magic byte 0 */
#define EI_MAG1         1U      /**< Magic byte 1 */
#define EI_MAG2         2U      /**< Magic byte 2 */
#define EI_MAG3         3U      /**< Magic byte 3 */
#define EI_CLASS        4U      /**< File class */
#define EI_DATA         5U      /**< Data encoding */
#define EI_VERSION      6U      /**< ELF version */
#define EI_OSABI        7U      /**< OS/ABI identification */
#define EI_ABIVERSION   8U      /**< ABI version */
#define EI_PAD          9U      /**< Padding start */
#define EI_NIDENT       16U     /**< Size of e_ident[] */

/** @brief ELF class (32/64 bit) */
#define ELFCLASSNONE    0U      /**< Invalid class */
#define ELFCLASS32      1U      /**< 32-bit objects */
#define ELFCLASS64      2U      /**< 64-bit objects */

/** @brief ELF data encoding */
#define ELFDATANONE     0U      /**< Invalid encoding */
#define ELFDATA2LSB     1U      /**< Little-endian */
#define ELFDATA2MSB     2U      /**< Big-endian */

/** @brief ELF version */
#define EV_NONE         0U      /**< Invalid version */
#define EV_CURRENT      1U      /**< Current version */

/** @brief ELF OS/ABI */
#define ELFOSABI_NONE       0U  /**< UNIX System V ABI */
#define ELFOSABI_LINUX      3U  /**< Linux */

/* ============================================================================
 * ELF FILE TYPES
 * ============================================================================ */

#define ET_NONE         0U      /**< No file type */
#define ET_REL          1U      /**< Relocatable file */
#define ET_EXEC         2U      /**< Executable file */
#define ET_DYN          3U      /**< Shared object file */
#define ET_CORE         4U      /**< Core file */

/* ============================================================================
 * ELF MACHINE TYPES
 * ============================================================================ */

#define EM_NONE         0U      /**< No machine */
#define EM_386          3U      /**< Intel 80386 */
#define EM_X86_64       62U     /**< AMD x86-64 */
#define EM_AARCH64      183U    /**< ARM 64-bit */

/* ============================================================================
 * PROGRAM HEADER TYPES
 * ============================================================================ */

#define PT_NULL         0U      /**< Unused entry */
#define PT_LOAD         1U      /**< Loadable segment */
#define PT_DYNAMIC      2U      /**< Dynamic linking info */
#define PT_INTERP       3U      /**< Interpreter path */
#define PT_NOTE         4U      /**< Auxiliary info */
#define PT_SHLIB        5U      /**< Reserved */
#define PT_PHDR         6U      /**< Program header table */
#define PT_TLS          7U      /**< Thread-local storage */
#define PT_GNU_EH_FRAME 0x6474E550U /**< GCC .eh_frame_hdr */
#define PT_GNU_STACK    0x6474E551U /**< Stack executability */
#define PT_GNU_RELRO    0x6474E552U /**< Read-only after relocation */

/* ============================================================================
 * PROGRAM HEADER FLAGS
 * ============================================================================ */

#define PF_X            0x1U    /**< Execute */
#define PF_W            0x2U    /**< Write */
#define PF_R            0x4U    /**< Read */

/* ============================================================================
 * SECTION HEADER TYPES
 * ============================================================================ */

#define SHT_NULL        0U      /**< Inactive */
#define SHT_PROGBITS    1U      /**< Program data */
#define SHT_SYMTAB      2U      /**< Symbol table */
#define SHT_STRTAB      3U      /**< String table */
#define SHT_RELA        4U      /**< Relocation entries with addends */
#define SHT_HASH        5U      /**< Symbol hash table */
#define SHT_DYNAMIC     6U      /**< Dynamic linking info */
#define SHT_NOTE        7U      /**< Notes */
#define SHT_NOBITS      8U      /**< Uninitialized data (bss) */
#define SHT_REL         9U      /**< Relocation entries */
#define SHT_DYNSYM      11U     /**< Dynamic symbol table */

/* ============================================================================
 * SECTION HEADER FLAGS
 * ============================================================================ */

#define SHF_WRITE       0x1U    /**< Writable */
#define SHF_ALLOC       0x2U    /**< Occupies memory */
#define SHF_EXECINSTR   0x4U    /**< Executable */

/* ============================================================================
 * ELF64 STRUCTURES
 * ============================================================================ */

/** @brief ELF64 file header */
typedef struct __attribute__((packed)) elf64_ehdr {
    uint8_t     e_ident[EI_NIDENT]; /**< ELF identification */
    uint16_t    e_type;             /**< Object file type */
    uint16_t    e_machine;          /**< Machine type */
    uint32_t    e_version;          /**< Object file version */
    uint64_t    e_entry;            /**< Entry point address */
    uint64_t    e_phoff;            /**< Program header offset */
    uint64_t    e_shoff;            /**< Section header offset */
    uint32_t    e_flags;            /**< Processor-specific flags */
    uint16_t    e_ehsize;           /**< ELF header size */
    uint16_t    e_phentsize;        /**< Program header entry size */
    uint16_t    e_phnum;            /**< Program header count */
    uint16_t    e_shentsize;        /**< Section header entry size */
    uint16_t    e_shnum;            /**< Section header count */
    uint16_t    e_shstrndx;         /**< Section name string table index */
} elf64_ehdr_t;

/** @brief ELF64 program header */
typedef struct __attribute__((packed)) elf64_phdr {
    uint32_t    p_type;             /**< Segment type */
    uint32_t    p_flags;            /**< Segment flags */
    uint64_t    p_offset;           /**< Segment offset in file */
    uint64_t    p_vaddr;            /**< Virtual address */
    uint64_t    p_paddr;            /**< Physical address */
    uint64_t    p_filesz;           /**< Size in file */
    uint64_t    p_memsz;            /**< Size in memory */
    uint64_t    p_align;            /**< Alignment */
} elf64_phdr_t;

/** @brief ELF64 section header */
typedef struct __attribute__((packed)) elf64_shdr {
    uint32_t    sh_name;            /**< Section name (string index) */
    uint32_t    sh_type;            /**< Section type */
    uint64_t    sh_flags;           /**< Section flags */
    uint64_t    sh_addr;            /**< Virtual address */
    uint64_t    sh_offset;          /**< Offset in file */
    uint64_t    sh_size;            /**< Section size */
    uint32_t    sh_link;            /**< Link to another section */
    uint32_t    sh_info;            /**< Additional info */
    uint64_t    sh_addralign;       /**< Address alignment */
    uint64_t    sh_entsize;         /**< Entry size if fixed */
} elf64_shdr_t;

/** @brief ELF64 symbol table entry */
typedef struct __attribute__((packed)) elf64_sym {
    uint32_t    st_name;            /**< Symbol name (string index) */
    uint8_t     st_info;            /**< Symbol type and binding */
    uint8_t     st_other;           /**< Symbol visibility */
    uint16_t    st_shndx;           /**< Section index */
    uint64_t    st_value;           /**< Symbol value */
    uint64_t    st_size;            /**< Symbol size */
} elf64_sym_t;

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_ELF_OK             0
#define VOS3_ELF_ERR_INVALID   (-1)   /**< Invalid ELF file */
#define VOS3_ELF_ERR_NOEXEC    (-2)   /**< Not executable */
#define VOS3_ELF_ERR_ARCH      (-3)   /**< Wrong architecture */
#define VOS3_ELF_ERR_NOMEM     (-4)   /**< Out of memory */
#define VOS3_ELF_ERR_IO        (-5)   /**< I/O error */
#define VOS3_ELF_ERR_NOENT     (-6)   /**< File not found */

/* ============================================================================
 * ELF INFO STRUCTURE
 * ============================================================================ */

/** @brief Loaded ELF information */
typedef struct vos3_elf_info {
    uint64_t    entry;              /**< Entry point */
    uint64_t    base;               /**< Load base address */
    uint64_t    brk;                /**< Program break (heap start) */
    uint64_t    phdr_addr;          /**< Program header address */
    uint16_t    phdr_num;           /**< Number of program headers */
    uint16_t    phdr_size;          /**< Size of each program header */
    int         is_pie;             /**< Position-independent executable */
    char        interp[256];        /**< PT_INTERP interpreter path (NUL-terminated) */
    int         has_interp;         /**< 1 if PT_INTERP was present in the ELF */
} vos3_elf_info_t;

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * @brief Validate ELF header
 * @param[in] ehdr ELF header
 * @return 0 if valid, negative error code otherwise
 */
int vos3_elf_validate(const elf64_ehdr_t* ehdr);

/**
 * @brief Load ELF executable from memory
 * @param[in] data ELF file data
 * @param[in] size File size
 * @param[out] info Loaded ELF information
 * @return 0 on success, negative error code otherwise
 */
int vos3_elf_load(const void* data, size_t size, vos3_elf_info_t* info);

/**
 * @brief Load ELF executable from file
 * @param[in] path File path
 * @param[out] info Loaded ELF information
 * @return 0 on success, negative error code otherwise
 */
int vos3_elf_load_file(const char* path, vos3_elf_info_t* info);

/**
 * @brief Load ELF at a specific base address (for interpreter loading)
 * @param[in] data ELF file data
 * @param[in] size File size
 * @param[in] load_base Forced load base address
 * @param[out] info Loaded ELF information
 * @return 0 on success, negative error code otherwise
 */
int vos3_elf_load_at(const void* data, size_t size,
                     uint64_t load_base, vos3_elf_info_t* info);

/**
 * @brief Load ELF from file at a specific base address
 * @param[in] path File path
 * @param[in] load_base Forced load base address
 * @param[out] info Loaded ELF information
 * @return 0 on success, negative error code otherwise
 */
int vos3_elf_load_file_at(const char* path, uint64_t load_base,
                          vos3_elf_info_t* info);

/**
 * @brief Execute ELF program
 * @param[in] path Program path
 * @param[in] argv Argument vector (NULL-terminated)
 * @param[in] envp Environment vector (NULL-terminated)
 * @return Does not return on success, negative error code on failure
 */
int vos3_exec(const char* path, const char* argv[], const char* envp[]);

/**
 * @brief Fork current process
 * @return Child PID in parent, 0 in child, negative on error
 * @deprecated Use vos3_fork_with_frame() for proper child return
 */
int vos3_fork(void);

/* Forward declaration for syscall frame */
struct vos3_syscall_frame;

/**
 * @brief Fork with user state for proper child return
 * @param[in] frame Syscall frame containing user state
 * @param[in] user_rsp User RSP at syscall
 * @return Child PID in parent, 0 in child, negative on error
 */
int vos3_fork_with_frame(struct vos3_syscall_frame* frame, uint64_t user_rsp);

/**
 * @brief Wait for child process
 * @param[out] status Exit status
 * @return Child PID or negative error
 */
int vos3_wait(int* status);

/**
 * @brief Wait for specific child
 * @param[in] pid Process ID to wait for
 * @param[out] status Exit status
 * @param[in] options Wait options
 * @return Child PID or negative error
 */
int vos3_waitpid(int pid, int* status, int options);

/* ============================================================================
 * WAIT OPTIONS
 * ============================================================================ */

#define WNOHANG     0x1     /**< Don't block */
#define WUNTRACED   0x2     /**< Report stopped children */

/* ============================================================================
 * EXIT STATUS MACROS
 * ============================================================================ */

#define WIFEXITED(s)    (((s) & 0x7F) == 0)
#define WEXITSTATUS(s)  (((s) >> 8) & 0xFF)
#define WIFSIGNALED(s)  (((s) & 0x7F) != 0 && ((s) & 0x7F) != 0x7F)
#define WTERMSIG(s)     ((s) & 0x7F)
#define WIFSTOPPED(s)   (((s) & 0xFF) == 0x7F)
#define WSTOPSIG(s)     (((s) >> 8) & 0xFF)

#endif /* VOS3_ELF_H */
