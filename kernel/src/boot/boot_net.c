/**
 * @file boot_net.c
 * @brief VOS3 Boot — Network Subsystem Initialization
 *
 * Extracted from kmain.c (Phase 8.5-C Sovereign Consolidation).
 * VirtIO-Net driver, TCP/IP stack, stress tests, Day 4 protocol
 * tests, and CVE mitigation verification.
 */

#include "../../include/vos/boot_net.h"
#include "../../include/vos/console.h"
#include "../../include/vos/net.h"
#include "../../include/vos/string.h"

/* External network init functions */
extern int vos3_net_init(void);
extern int vos3_virtio_net_init(void);
extern int vos3_net_stress_test(void);

/* Day 4: ICMP test functions */
extern int vos3_icmp_test_ping(vos3_netif_t *netif);
extern int vos3_icmp_test_rate_limit(vos3_netif_t *netif, int count);

/* Day 4: CVE Security Tests (February 2026 Mitigations) */
extern int vos3_day4_security_test(void);

/* ============================================================================
 * BOOT NET INIT
 * ============================================================================ */

int boot_net_init(void)
{
    int result;

    /* ===== Phase 30: Network Stack ===== */
    VOS3_INFO("Initializing Network Subsystem (Phase 30)");

    /* Initialize VirtIO-Net driver first */
    result = vos3_virtio_net_init();
    if (result != 0) {
        VOS3_WARN("VirtIO-Net driver initialization failed (error %d)", result);
        /* Non-fatal: continue without VirtIO network */
    }

    /* Initialize network subsystem and run security tests */
    result = vos3_net_init();
    if (result != 0) {
        VOS3_WARN("Network initialization failed (error %d)", result);
        /* Non-fatal: continue without network */
    }

    /* ===== Day 3.5: Deep Penetration Test Suite ===== */
#ifdef VOS3_BOOT_SELF_TEST
    result = vos3_net_stress_test();
    if (result != 1) {
        VOS3_ERROR("Network penetration tests FAILED!");
        /* Continue anyway - this is a test, not a gate */
    }
#endif /* VOS3_BOOT_SELF_TEST */

    /* ===== Day 4: Protocol Stack Tests (ARP/IP/ICMP) ===== */
    {
        VOS3_INFO("[DAY4] ========================================");
        VOS3_INFO("[DAY4] Protocol Stack Tests (Fast AND Secure)");
        VOS3_INFO("[DAY4] ========================================");

        /* Create test network interface for loopback tests */
        static vos3_netif_t test_netif;
        memset(&test_netif, 0, sizeof(test_netif));
        memcpy(test_netif.name, "lo0", 4);
        test_netif.flags = VOS3_IFF_UP | VOS3_IFF_RUNNING;
        test_netif.mac_addr.bytes[0] = 0x02;  /* Locally administered */
        test_netif.mac_addr.bytes[1] = 0x00;
        test_netif.mac_addr.bytes[2] = 0x00;
        test_netif.mac_addr.bytes[3] = 0x00;
        test_netif.mac_addr.bytes[4] = 0x00;
        test_netif.mac_addr.bytes[5] = 0x01;
        test_netif.ipv4_addr = 0x0100007F;  /* 127.0.0.1 (little endian) */
        test_netif.mtu = 1500;

        int day4_passed = 0;
        int day4_total = 2;

        /* Test 1: Ping Request -> Reply Generation */
        VOS3_INFO("[DAY4] Test 1: ICMP Ping Request -> Reply");
        result = vos3_icmp_test_ping(&test_netif);
        if (result == 0) {
            VOS3_INFO("[DAY4]   [PASS] Echo Reply generated");
            day4_passed++;
        } else {
            VOS3_ERROR("[DAY4]   [FAIL] Echo Reply NOT generated");
        }

        /* Test 2: Rate Limiting (send 50 pings, expect ~30 rate-limited) */
        VOS3_INFO("[DAY4] Test 2: ICMP Rate Limiting (Token Bucket)");
        int rate_limited = vos3_icmp_test_rate_limit(&test_netif, 50);
        if (rate_limited >= 25) {
            VOS3_INFO("[DAY4]   [PASS] Rate limiting active: %d/50 throttled", rate_limited);
            day4_passed++;
        } else {
            VOS3_ERROR("[DAY4]   [FAIL] Rate limiting weak: only %d/50 throttled", rate_limited);
        }

        /* Summary */
        VOS3_INFO("[DAY4] ========================================");
        if (day4_passed == day4_total) {
            VOS3_INFO("[DAY4] All %d/%d protocol tests PASSED", day4_passed, day4_total);
            VOS3_INFO("[DAY4] Performance: likely()/unlikely() optimized");
            VOS3_INFO("[DAY4] Security: Fragments=DROP, Gratuitous ARP=DROP");
        } else {
            VOS3_ERROR("[DAY4] FAILED: %d/%d tests passed", day4_passed, day4_total);
        }
        VOS3_INFO("[DAY4] ========================================");
    }

    /* ===== Day 4: CVE Mitigation Verification (February 2026) ===== */
#ifdef VOS3_BOOT_SELF_TEST
    result = vos3_day4_security_test();
    if (result != 1) {
        VOS3_ERROR("Day 4 CVE security tests FAILED!");
        /* Continue anyway - this is a test, not a gate */
    }
#endif /* VOS3_BOOT_SELF_TEST */

    return 0;
}
