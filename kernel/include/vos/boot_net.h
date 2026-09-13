#ifndef VOS3_BOOT_NET_H
#define VOS3_BOOT_NET_H

/**
 * @brief Initialize network subsystems.
 *
 * VirtIO-Net driver, TCP/IP stack, network stress test,
 * Day 4 protocol tests (ICMP ping, rate limiting), and
 * CVE mitigation verification.  Warns on failure; non-fatal.
 *
 * @return 0 on success, negative on failure (non-fatal).
 */
int boot_net_init(void);

#endif /* VOS3_BOOT_NET_H */
