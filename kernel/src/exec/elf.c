/**
 * @file elf.c
 * @brief VOS3 ELF Loader Implementation
 *
 * @details Loads and validates ELF64 executables.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/elf.h"
#include "../../include/vos/vmm.h"
#include "../../include/vos/pmm.h"
#include "../../include/vos/heap.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/string.h"
#include "../../include/vos/console.h"
#include "../../include/vos/user.h"

/* ============================================================================
 * ELF VALIDATION
 * ============================================================================ */

int vos3_elf_validate(const elf64_ehdr_t* ehdr)
{
    if (ehdr == NULL) {
        return VOS3_ELF_ERR_INVALID;
    }

    /* Check magic number */
    if (ehdr->e_ident[EI_MAG0] != ELF_MAGIC0 ||
        ehdr->e_ident[EI_MAG1] != ELF_MAGIC1 ||
        ehdr->e_ident[EI_MAG2] != ELF_MAGIC2 ||
        ehdr->e_ident[EI_MAG3] != ELF_MAGIC3) {
        VOS3_DEBUG("ELF: Invalid magic number");
        return VOS3_ELF_ERR_INVALID;
    }

    /* Check class (64-bit) */
    if (ehdr->e_ident[EI_CLASS] != ELFCLASS64) {
        VOS3_DEBUG("ELF: Not a 64-bit ELF");
        return VOS3_ELF_ERR_ARCH;
    }

    /* Check endianness (little-endian) */
    if (ehdr->e_ident[EI_DATA] != ELFDATA2LSB) {
        VOS3_DEBUG("ELF: Not little-endian");
        return VOS3_ELF_ERR_ARCH;
    }

    /* Check version */
    if (ehdr->e_ident[EI_VERSION] != EV_CURRENT) {
        VOS3_DEBUG("ELF: Unsupported version");
        return VOS3_ELF_ERR_INVALID;
    }

    /* Check file type (executable or shared object for PIE) */
    if (ehdr->e_type != ET_EXEC && ehdr->e_type != ET_DYN) {
        VOS3_DEBUG("ELF: Not an executable");
        return VOS3_ELF_ERR_NOEXEC;
    }

    /* Check machine type (x86_64) */
    if (ehdr->e_machine != EM_X86_64) {
        VOS3_DEBUG("ELF: Not x86_64 architecture");
        return VOS3_ELF_ERR_ARCH;
    }

    /* Check program header existence */
    if (ehdr->e_phoff == 0 || ehdr->e_phnum == 0) {
        VOS3_DEBUG("ELF: No program headers");
        return VOS3_ELF_ERR_INVALID;
    }

    /* Check entry point is in user space */
    if (ehdr->e_type == ET_EXEC && ehdr->e_entry >= VOS3_USER_END) {
        VOS3_DEBUG("ELF: Entry point not in user space");
        return VOS3_ELF_ERR_INVALID;
    }

    return VOS3_ELF_OK;
}

/* ============================================================================
 * ELF LOADING
 * ============================================================================ */

/**
 * @brief Convert ELF flags to page table flags
 */
static uint64_t elf_flags_to_pte(uint32_t p_flags)
{
    uint64_t pte_flags = VOS3_PTE_PRESENT | VOS3_PTE_USER;

    if ((p_flags & PF_W) != 0U) {
        pte_flags |= VOS3_PTE_WRITABLE;
    }

    if ((p_flags & PF_X) == 0U) {
        pte_flags |= VOS3_PTE_NO_EXECUTE;
    }

    return pte_flags;
}

/**
 * @brief Core ELF loading logic with explicit bias control
 *
 * @param[in] data          ELF file data
 * @param[in] size          File size
 * @param[in] forced_bias   Load bias to use when use_forced_bias==1
 * @param[in] use_forced_bias  1=use forced_bias, 0=compute from ELF type
 * @param[out] info         Loaded ELF information
 * @return 0 on success, negative error code otherwise
 */
static int elf_load_core(const void* data, size_t size,
                         uint64_t forced_bias, int use_forced_bias,
                         vos3_elf_info_t* info)
{
    if (data == NULL || info == NULL || size < sizeof(elf64_ehdr_t)) {
        return VOS3_ELF_ERR_INVALID;
    }

    const elf64_ehdr_t* ehdr = (const elf64_ehdr_t*)data;

    /* Validate ELF header */
    int result = vos3_elf_validate(ehdr);
    if (result != VOS3_ELF_OK) {
        return result;
    }

    /* Check program headers fit in file */
    size_t ph_end = ehdr->e_phoff + ((size_t)ehdr->e_phnum * ehdr->e_phentsize);
    if (ph_end > size) {
        return VOS3_ELF_ERR_INVALID;
    }

    const uint8_t* file_data = (const uint8_t*)data;
    const elf64_phdr_t* phdr_base = (const elf64_phdr_t*)(file_data + ehdr->e_phoff);

    /* Initialize info */
    memset(info, 0, sizeof(*info));
    info->entry = ehdr->e_entry;
    info->phdr_num = ehdr->e_phnum;
    info->phdr_size = ehdr->e_phentsize;
    info->is_pie = (ehdr->e_type == ET_DYN) ? 1 : 0;

    /* Determine load bias */
    uint64_t load_bias = 0ULL;
    if (use_forced_bias) {
        load_bias = forced_bias;
        info->entry += load_bias;
    } else if (info->is_pie != 0) {
        load_bias = 0x400000ULL;  /* Standard PIE base */
        info->entry += load_bias;
    }
    info->base = load_bias;

    /* First pass: find extent of loadable segments */
    uint64_t min_addr = ~0ULL;
    uint64_t max_addr = 0ULL;

    for (uint16_t i = 0U; i < ehdr->e_phnum; i++) {
        const elf64_phdr_t* phdr = (const elf64_phdr_t*)
            ((const uint8_t*)phdr_base + (i * ehdr->e_phentsize));

        if (phdr->p_type == PT_LOAD) {
            uint64_t seg_start = phdr->p_vaddr + load_bias;
            uint64_t seg_end = seg_start + phdr->p_memsz;

            if (seg_start < min_addr) {
                min_addr = seg_start;
            }
            if (seg_end > max_addr) {
                max_addr = seg_end;
            }
        } else if (phdr->p_type == PT_PHDR) {
            info->phdr_addr = phdr->p_vaddr + load_bias;
        } else if (phdr->p_type == PT_INTERP) {
            if (phdr->p_filesz > 0 && phdr->p_filesz < sizeof(info->interp) &&
                phdr->p_offset + phdr->p_filesz <= size) {
                memcpy(info->interp, (const uint8_t*)data + phdr->p_offset,
                       (size_t)phdr->p_filesz);
                info->interp[phdr->p_filesz] = '\0';
                info->has_interp = 1;
            }
        }
    }

    if (min_addr >= max_addr) {
        VOS3_DEBUG("ELF: No loadable segments");
        return VOS3_ELF_ERR_INVALID;
    }

    /* Fallback: if no PT_PHDR entry, find which LOAD segment contains
     * e_phoff and compute the runtime virtual address. This matches the
     * Linux kernel's fallback in load_elf_binary(). If the program headers
     * aren't within any LOAD segment, phdr_addr stays 0 (unavailable). */
    if (info->phdr_addr == 0 && ehdr->e_phoff != 0) {
        for (uint16_t i = 0U; i < ehdr->e_phnum; i++) {
            const elf64_phdr_t* phdr = (const elf64_phdr_t*)
                ((const uint8_t*)phdr_base + (i * ehdr->e_phentsize));
            if (phdr->p_type == PT_LOAD &&
                ehdr->e_phoff >= phdr->p_offset &&
                ehdr->e_phoff < phdr->p_offset + phdr->p_filesz) {
                info->phdr_addr = phdr->p_vaddr + load_bias +
                                  (ehdr->e_phoff - phdr->p_offset);
                break;
            }
        }
    }

    VOS3_DEBUG("ELF: Segment extent: min=0x%llx, max=0x%llx",
               (unsigned long long)min_addr, (unsigned long long)max_addr);

    /* Set program break (heap start) */
    info->brk = (max_addr + VOS3_PAGE_SIZE - 1ULL) & ~(VOS3_PAGE_SIZE - 1ULL);

    VOS3_DEBUG("ELF: brk calculated as 0x%llx (page-aligned max_addr)",
               (unsigned long long)info->brk);

    /* Second pass: load segments */
    for (uint16_t i = 0U; i < ehdr->e_phnum; i++) {
        const elf64_phdr_t* phdr = (const elf64_phdr_t*)
            ((const uint8_t*)phdr_base + (i * ehdr->e_phentsize));

        if (phdr->p_type != PT_LOAD) {
            continue;
        }

        /* Calculate addresses */
        uint64_t vaddr = phdr->p_vaddr + load_bias;
        uint64_t vaddr_page = vaddr & ~(VOS3_PAGE_SIZE - 1ULL);
        uint64_t offset_in_page = vaddr - vaddr_page;
        size_t memsz = (size_t)phdr->p_memsz;
        size_t filesz = (size_t)phdr->p_filesz;

        /* Validate file offset */
        if (phdr->p_offset + filesz > size) {
            VOS3_DEBUG("ELF: Segment extends past file end");
            return VOS3_ELF_ERR_INVALID;
        }

        /* Calculate pages needed */
        size_t total_size = offset_in_page + memsz;
        size_t page_count = (total_size + VOS3_PAGE_SIZE - 1ULL) / VOS3_PAGE_SIZE;

        /* Get page flags */
        uint64_t pte_flags = elf_flags_to_pte(phdr->p_flags);

        /* Allocate and map pages */
        for (size_t p = 0U; p < page_count; p++) {
            uint64_t page_vaddr = vaddr_page + (p * VOS3_PAGE_SIZE);

            /* Allocate physical page */
            uint64_t phys = vos3_pmm_alloc(0);
            if (phys == 0ULL) {
                VOS3_ERROR("ELF: Out of memory allocating page");
                return VOS3_ELF_ERR_NOMEM;
            }

            /* Copy through a writable, non-executable kernel alias. The final
             * user mapping receives the segment's permissions after copying. */
            void* kaddr = vos3_vmm_map_pages(phys, VOS3_PAGE_SIZE,
                                              VOS3_PTE_PRESENT | VOS3_PTE_WRITABLE |
                                              VOS3_PTE_NO_EXECUTE);
            if (kaddr == NULL) {
                vos3_pmm_free(phys);
                return VOS3_ELF_ERR_NOMEM;
            }

            /* Clear page */
            memset(kaddr, 0, VOS3_PAGE_SIZE);

            /* Copy file data if within filesz */
            size_t page_offset = p * VOS3_PAGE_SIZE;
            if (p == 0U) {
                /* First page - account for offset within page */
                if (filesz > 0U) {
                    size_t copy_size = filesz;
                    if (copy_size > VOS3_PAGE_SIZE - offset_in_page) {
                        copy_size = VOS3_PAGE_SIZE - offset_in_page;
                    }
                    memcpy((uint8_t*)kaddr + offset_in_page,
                           file_data + phdr->p_offset, copy_size);
                }
            } else {
                /* Subsequent pages */
                size_t file_offset = page_offset - offset_in_page;
                if (file_offset < filesz) {
                    size_t copy_size = filesz - file_offset;
                    if (copy_size > VOS3_PAGE_SIZE) {
                        copy_size = VOS3_PAGE_SIZE;
                    }
                    memcpy(kaddr, file_data + phdr->p_offset + file_offset,
                           copy_size);
                }
            }

            /* Unmap from kernel */
            vos3_vmm_unmap_pages(kaddr, VOS3_PAGE_SIZE);

            /* Map to user space with correct flags */
            result = vos3_vmm_map_user(page_vaddr, phys, pte_flags);
            if (result != 0) {
                vos3_pmm_free(phys);
                VOS3_ERROR("ELF: Failed to map page to user space");
                return VOS3_ELF_ERR_NOMEM;
            }
        }

        VOS3_DEBUG("ELF: Loaded segment at 0x%llx (filesz=%llu, memsz=%llu, flags=0x%x)",
                   (unsigned long long)vaddr,
                   (unsigned long long)filesz,
                   (unsigned long long)memsz,
                   (unsigned int)phdr->p_flags);
    }

    VOS3_DEBUG("ELF: Loaded executable, entry=0x%llx, base=0x%llx, brk=0x%llx",
               (unsigned long long)info->entry,
               (unsigned long long)info->base,
               (unsigned long long)info->brk);

    return VOS3_ELF_OK;
}

int vos3_elf_load(const void* data, size_t size, vos3_elf_info_t* info)
{
    return elf_load_core(data, size, 0, 0, info);
}

int vos3_elf_load_at(const void* data, size_t size,
                     uint64_t load_base, vos3_elf_info_t* info)
{
    if (data == NULL || info == NULL || size < sizeof(elf64_ehdr_t)) {
        return VOS3_ELF_ERR_INVALID;
    }

    /* Interpreters must be ET_DYN (shared objects) */
    const elf64_ehdr_t* ehdr = (const elf64_ehdr_t*)data;
    if (ehdr->e_type != ET_DYN) {
        VOS3_ERROR("ELF: load_at requires ET_DYN (got type %u)", ehdr->e_type);
        return VOS3_ELF_ERR_NOEXEC;
    }

    return elf_load_core(data, size, load_base, 1, info);
}

int vos3_elf_load_file_at(const char* path, uint64_t load_base,
                          vos3_elf_info_t* info)
{
    if (path == NULL || info == NULL) {
        return VOS3_ELF_ERR_INVALID;
    }

    int fd = vos3_open(path, VOS3_O_RDONLY, 0U);
    if (fd < 0) {
        VOS3_DEBUG("ELF: Cannot open interpreter '%s'", path);
        return VOS3_ELF_ERR_NOENT;
    }

    vos3_inode_t stat;
    int result = vos3_fstat(fd, &stat);
    if (result != 0) {
        vos3_close(fd);
        return VOS3_ELF_ERR_IO;
    }

    size_t file_size = stat.size;
    if (file_size < sizeof(elf64_ehdr_t)) {
        vos3_close(fd);
        return VOS3_ELF_ERR_INVALID;
    }

    void* buffer = vos3_kmalloc(file_size);
    if (buffer == NULL) {
        vos3_close(fd);
        return VOS3_ELF_ERR_NOMEM;
    }

    int64_t bytes_read = vos3_read(fd, buffer, file_size);
    vos3_close(fd);

    if (bytes_read < 0 || (size_t)bytes_read != file_size) {
        vos3_kfree(buffer);
        return VOS3_ELF_ERR_IO;
    }

    result = vos3_elf_load_at(buffer, file_size, load_base, info);
    vos3_kfree(buffer);

    return result;
}

int vos3_elf_load_file(const char* path, vos3_elf_info_t* info)
{
    if (path == NULL || info == NULL) {
        return VOS3_ELF_ERR_INVALID;
    }

    /* Open file */
    int fd = vos3_open(path, VOS3_O_RDONLY, 0U);
    if (fd < 0) {
        VOS3_DEBUG("ELF: Cannot open file '%s'", path);
        return VOS3_ELF_ERR_NOENT;
    }

    /* Get file size using fstat */
    vos3_inode_t stat;
    int result = vos3_fstat(fd, &stat);
    if (result != 0) {
        vos3_close(fd);
        return VOS3_ELF_ERR_IO;
    }

    size_t file_size = stat.size;
    if (file_size < sizeof(elf64_ehdr_t)) {
        vos3_close(fd);
        return VOS3_ELF_ERR_INVALID;
    }

    /* Allocate buffer */
    void* buffer = vos3_kmalloc(file_size);
    if (buffer == NULL) {
        vos3_close(fd);
        return VOS3_ELF_ERR_NOMEM;
    }

    /* Read entire file */
    int64_t bytes_read = vos3_read(fd, buffer, file_size);
    vos3_close(fd);

    if (bytes_read < 0 || (size_t)bytes_read != file_size) {
        vos3_kfree(buffer);
        return VOS3_ELF_ERR_IO;
    }

    /* Load ELF from buffer */
    result = vos3_elf_load(buffer, file_size, info);

    vos3_kfree(buffer);

    return result;
}
