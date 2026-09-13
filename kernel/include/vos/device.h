/**
 * @file device.h
 * @brief VOS3 Device Driver Framework
 *
 * @details Unified device model for character and block devices.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#ifndef VOS3_DEVICE_H
#define VOS3_DEVICE_H

#include <stdint.h>
#include <stddef.h>
#include "sync.h"
#include "vfs.h"

/* ============================================================================
 * CONSTANTS
 * ============================================================================ */

/** @brief Maximum device name length */
#define VOS3_DEV_NAME_MAX       32U

/** @brief Maximum registered devices */
#define VOS3_MAX_DEVICES        64U

/** @brief Maximum major number */
#define VOS3_DEV_MAJOR_MAX      16U

/** @brief Maximum minor number */
#define VOS3_DEV_MINOR_MAX      256U

/** @brief Device magic number */
#define VOS3_DEV_MAGIC          0xDE71CEU

/* ============================================================================
 * DEVICE NUMBERS
 * ============================================================================ */

/** @brief Device number type (major:minor) */
typedef uint32_t vos3_dev_t;

/** @brief Make device number from major/minor */
#define VOS3_MKDEV(major, minor)  (((vos3_dev_t)(major) << 8) | ((minor) & 0xFFU))

/** @brief Get major number from device */
#define VOS3_MAJOR(dev)           (((dev) >> 8) & 0xFFU)

/** @brief Get minor number from device */
#define VOS3_MINOR(dev)           ((dev) & 0xFFU)

/** @brief Invalid device number */
#define VOS3_DEV_INVALID          ((vos3_dev_t)0xFFFFFFFFU)

/* ============================================================================
 * WELL-KNOWN MAJOR NUMBERS
 * ============================================================================ */

#define VOS3_MEM_MAJOR      1U    /**< Memory devices (null, zero, etc.) */
#define VOS3_TTY_MAJOR      4U    /**< TTY devices */
#define VOS3_CONSOLE_MAJOR  5U    /**< Console */
#define VOS3_DISK_MAJOR     8U    /**< Disk devices */
#define VOS3_MISC_MAJOR     10U   /**< Misc devices */

/* ============================================================================
 * DEVICE TYPES
 * ============================================================================ */

/** @brief Device type */
typedef enum vos3_dev_type {
    VOS3_DEV_CHAR   = 0,    /**< Character device */
    VOS3_DEV_BLOCK  = 1,    /**< Block device */
} vos3_dev_type_t;

/* ============================================================================
 * IOCTL COMMANDS
 * ============================================================================ */

/** @brief Generic IOCTL commands */
#define VOS3_IOCTL_GETINFO      0x0001U   /**< Get device info */
#define VOS3_IOCTL_RESET        0x0002U   /**< Reset device */
#define VOS3_IOCTL_FLUSH        0x0003U   /**< Flush buffers */

/** @brief Block device IOCTL commands */
#define VOS3_IOCTL_BLKGETSIZE   0x1001U   /**< Get size in blocks */
#define VOS3_IOCTL_BLKGETSS     0x1002U   /**< Get sector size */
#define VOS3_IOCTL_BLKFLUSH     0x1003U   /**< Flush block cache */

/** @brief TTY IOCTL commands */
#define VOS3_IOCTL_TCGETS       0x2001U   /**< Get termios */
#define VOS3_IOCTL_TCSETS       0x2002U   /**< Set termios */
#define VOS3_IOCTL_TCSETSW      0x2003U   /**< Set termios (drain) */
#define VOS3_IOCTL_TIOCGWINSZ   0x2004U   /**< Get window size */
#define VOS3_IOCTL_TIOCSWINSZ   0x2005U   /**< Set window size */
#define VOS3_IOCTL_TIOCGPGRP    0x2006U   /**< Get foreground process group */
#define VOS3_IOCTL_TIOCSPGRP    0x2007U   /**< Set foreground process group */
#define VOS3_IOCTL_TIOCSCTTY    0x2008U   /**< Set controlling terminal */

/* ============================================================================
 * ERROR CODES
 * ============================================================================ */

#define VOS3_DEV_OK             0
#define VOS3_DEV_ERR_NODEV     (-19)   /**< No such device */
#define VOS3_DEV_ERR_BUSY      (-16)   /**< Device busy */
#define VOS3_DEV_ERR_IO        (-5)    /**< I/O error */
#define VOS3_DEV_ERR_NOMEM     (-12)   /**< Out of memory */
#define VOS3_DEV_ERR_INVAL     (-22)   /**< Invalid argument */
#define VOS3_DEV_ERR_EXIST     (-17)   /**< Device exists */
#define VOS3_DEV_ERR_NOTTY     (-25)   /**< Not a tty */

/* ============================================================================
 * FORWARD DECLARATIONS
 * ============================================================================ */

struct vos3_device;
struct vos3_char_device;
struct vos3_block_device;

/* ============================================================================
 * CHARACTER DEVICE OPERATIONS
 * ============================================================================ */

/** @brief Character device operations */
typedef struct vos3_char_ops {
    /** Open device */
    int (*open)(struct vos3_device* dev, vos3_file_t* file);
    /** Close device */
    int (*close)(struct vos3_device* dev, vos3_file_t* file);
    /** Read from device */
    int64_t (*read)(struct vos3_device* dev, vos3_file_t* file,
                    void* buf, size_t count);
    /** Write to device */
    int64_t (*write)(struct vos3_device* dev, vos3_file_t* file,
                     const void* buf, size_t count);
    /** IO control */
    int (*ioctl)(struct vos3_device* dev, vos3_file_t* file,
                 uint32_t cmd, void* arg);
    /** Poll for events */
    int (*poll)(struct vos3_device* dev, vos3_file_t* file, uint32_t events);
} vos3_char_ops_t;

/* ============================================================================
 * BLOCK DEVICE OPERATIONS
 * ============================================================================ */

/** @brief Block request type */
typedef enum vos3_block_req_type {
    VOS3_BLK_READ   = 0,
    VOS3_BLK_WRITE  = 1,
    VOS3_BLK_FLUSH  = 2,
} vos3_block_req_type_t;

/** @brief Block request */
typedef struct vos3_block_request {
    vos3_block_req_type_t type;   /**< Request type */
    uint64_t              sector; /**< Starting sector */
    uint32_t              count;  /**< Number of sectors */
    void*                 buffer; /**< Data buffer */
    int                   status; /**< Completion status */
    struct vos3_block_request* next; /**< Next in queue */
} vos3_block_request_t;

/** @brief Block device operations */
typedef struct vos3_block_ops {
    /** Open device */
    int (*open)(struct vos3_device* dev);
    /** Close device */
    int (*close)(struct vos3_device* dev);
    /** Submit request */
    int (*submit)(struct vos3_device* dev, vos3_block_request_t* req);
    /** IO control */
    int (*ioctl)(struct vos3_device* dev, uint32_t cmd, void* arg);
    /** Get geometry */
    int (*getgeo)(struct vos3_device* dev, uint64_t* sectors,
                  uint32_t* sector_size);
} vos3_block_ops_t;

/* ============================================================================
 * DEVICE STRUCTURE
 * ============================================================================ */

/** @brief Device structure */
typedef struct vos3_device {
    uint32_t            magic;          /**< Magic number */
    char                name[VOS3_DEV_NAME_MAX]; /**< Device name */
    vos3_dev_t          devno;          /**< Device number */
    vos3_dev_type_t     type;           /**< Device type */

    /** @brief Type-specific operations */
    union {
        const vos3_char_ops_t*  char_ops;
        const vos3_block_ops_t* block_ops;
    } ops;

    uint32_t            flags;          /**< Device flags */
    uint32_t            ref_count;      /**< Open count */
    vos3_mutex_t        lock;           /**< Device lock */

    void*               private_data;   /**< Driver-specific data */

    struct vos3_device* next;           /**< Next in list */
} vos3_device_t;

/** @brief Block device extension */
typedef struct vos3_block_device {
    vos3_device_t       base;           /**< Base device */
    uint64_t            sectors;        /**< Total sectors */
    uint32_t            sector_size;    /**< Bytes per sector */
    uint32_t            max_sectors;    /**< Max sectors per request */
    vos3_block_request_t* request_queue; /**< Request queue */
    vos3_mutex_t        queue_lock;     /**< Queue lock */
} vos3_block_device_t;

/* ============================================================================
 * DEVICE REGISTRATION
 * ============================================================================ */

/**
 * @brief Initialize device subsystem
 * @return 0 on success, negative on error
 */
int vos3_dev_init(void);

/**
 * @brief Register a character device
 * @param[in] name Device name
 * @param[in] devno Device number
 * @param[in] ops Device operations
 * @param[in] private_data Driver data
 * @return Device pointer or NULL on error
 */
vos3_device_t* vos3_cdev_register(const char* name, vos3_dev_t devno,
                                   const vos3_char_ops_t* ops,
                                   void* private_data);

/**
 * @brief Register a block device
 * @param[in] name Device name
 * @param[in] devno Device number
 * @param[in] ops Device operations
 * @param[in] sectors Total sectors
 * @param[in] sector_size Bytes per sector
 * @param[in] private_data Driver data
 * @return Device pointer or NULL on error
 */
vos3_device_t* vos3_bdev_register(const char* name, vos3_dev_t devno,
                                   const vos3_block_ops_t* ops,
                                   uint64_t sectors, uint32_t sector_size,
                                   void* private_data);

/**
 * @brief Unregister a device
 * @param[in] dev Device to unregister
 * @return 0 on success, negative on error
 */
int vos3_dev_unregister(vos3_device_t* dev);

/**
 * @brief Get device by number
 * @param[in] devno Device number
 * @return Device pointer or NULL
 */
vos3_device_t* vos3_dev_get(vos3_dev_t devno);

/**
 * @brief Get device by name
 * @param[in] name Device name
 * @return Device pointer or NULL
 */
vos3_device_t* vos3_dev_find(const char* name);

/* ============================================================================
 * DEVICE FILE OPERATIONS
 * ============================================================================ */

/**
 * @brief Open device file
 * @param[in] dev Device
 * @param[in] file File structure
 * @return 0 on success, negative on error
 */
int vos3_dev_open(vos3_device_t* dev, vos3_file_t* file);

/**
 * @brief Close device file
 * @param[in] dev Device
 * @param[in] file File structure
 * @return 0 on success, negative on error
 */
int vos3_dev_close(vos3_device_t* dev, vos3_file_t* file);

/**
 * @brief Read from device
 * @param[in] dev Device
 * @param[in] file File structure
 * @param[out] buf Buffer
 * @param[in] count Bytes to read
 * @return Bytes read or negative error
 */
int64_t vos3_dev_read(vos3_device_t* dev, vos3_file_t* file,
                       void* buf, size_t count);

/**
 * @brief Write to device
 * @param[in] dev Device
 * @param[in] file File structure
 * @param[in] buf Buffer
 * @param[in] count Bytes to write
 * @return Bytes written or negative error
 */
int64_t vos3_dev_write(vos3_device_t* dev, vos3_file_t* file,
                        const void* buf, size_t count);

/**
 * @brief Device IO control
 * @param[in] dev Device
 * @param[in] file File structure
 * @param[in] cmd Command
 * @param[in,out] arg Argument
 * @return 0 on success, negative on error
 */
int vos3_dev_ioctl(vos3_device_t* dev, vos3_file_t* file,
                    uint32_t cmd, void* arg);

/* ============================================================================
 * BLOCK DEVICE HELPERS
 * ============================================================================ */

/**
 * @brief Read blocks from device
 * @param[in] dev Block device
 * @param[in] sector Starting sector
 * @param[in] count Number of sectors
 * @param[out] buf Buffer
 * @return 0 on success, negative on error
 */
int vos3_bdev_read(vos3_block_device_t* dev, uint64_t sector,
                    uint32_t count, void* buf);

/**
 * @brief Write blocks to device
 * @param[in] dev Block device
 * @param[in] sector Starting sector
 * @param[in] count Number of sectors
 * @param[in] buf Buffer
 * @return 0 on success, negative on error
 */
int vos3_bdev_write(vos3_block_device_t* dev, uint64_t sector,
                     uint32_t count, const void* buf);

/**
 * @brief Flush block device
 * @param[in] dev Block device
 * @return 0 on success, negative on error
 */
int vos3_bdev_flush(vos3_block_device_t* dev);

/* ============================================================================
 * DEVFS FUNCTIONS
 * ============================================================================ */

/**
 * @brief Initialize devfs
 * @return 0 on success, negative on error
 */
int vos3_devfs_init(void);

/**
 * @brief Create device node in /dev
 * @param[in] name Device name
 * @param[in] dev Device
 * @return 0 on success, negative on error
 */
int vos3_devfs_create(const char* name, vos3_device_t* dev);

/**
 * @brief Remove device node from /dev
 * @param[in] name Device name
 * @return 0 on success, negative on error
 */
int vos3_devfs_remove(const char* name);

/**
 * @brief Get devfs file operations
 * @return Pointer to devfs file operations structure
 */
struct vos3_file_ops;
const struct vos3_file_ops* vos3_devfs_get_ops(void);

#endif /* VOS3_DEVICE_H */
