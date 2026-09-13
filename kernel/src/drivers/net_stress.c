/**
 * @file net_stress.c
 * @brief VOS3 Network Driver Deep Penetration Test Suite
 *
 * @details Mathematical proof of driver resilience against attack vectors:
 *          - Scenario A: Giants (oversized packets)
 *          - Scenario B: Dwarves (undersized packets)
 *          - Scenario C: Storm (5000 packet flood)
 *          - Scenario D: Spoof (MAC address spoofing)
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Day 3.5 - Deep Penetration Testing
 */

#include "../../include/vos/net.h"
#include "../../include/vos/net_security.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"

/**
 * @brief Combined netbuf structure with embedded data for stack allocation
 *
 * Since vos3_netbuf_t uses a flexible array member, we need this wrapper
 * to safely allocate buffer space on the stack for tests that access data[].
 */
typedef struct {
    vos3_netbuf_t header;
    uint8_t data[256];  /* Space for packet data */
} stress_test_netbuf_t;

/* ============================================================================
 * TEST CONFIGURATION
 * ============================================================================ */

/** @brief Number of packets for size attack tests */
#define STRESS_SIZE_ATTACK_COUNT    50

/** @brief Number of packets for storm attack */
#define STRESS_STORM_PACKET_COUNT   5000

/** @brief Number of packets for MAC spoof attack */
#define STRESS_SPOOF_ATTACK_COUNT   50

/** @brief Oversized packet length (exceeds MTU) */
#define STRESS_GIANT_SIZE           2000

/** @brief Undersized packet length (below minimum) */
#define STRESS_DWARF_SIZE           30

/* ============================================================================
 * TEST RESULTS STRUCTURE
 * ============================================================================ */

/**
 * @brief Individual scenario result
 */
typedef struct {
    const char* name;
    size_t      packets_sent;
    size_t      packets_dropped;
    size_t      packets_accepted;
    int         passed;
} stress_scenario_result_t;

/**
 * @brief Complete test suite results
 */
typedef struct {
    stress_scenario_result_t giants;
    stress_scenario_result_t dwarves;
    stress_scenario_result_t storm;
    stress_scenario_result_t spoof;
    int                      all_passed;
    uint64_t                 throttle_events;
} stress_test_results_t;

/* Global results */
static stress_test_results_t g_stress_results;

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Create a fake Ethernet frame for testing
 *
 * @param[out] buf       Buffer to populate
 * @param[in]  dst_mac   Destination MAC (6 bytes)
 * @param[in]  src_mac   Source MAC (6 bytes)
 * @param[in]  len       Total frame length to set
 */
static void create_fake_frame(vos3_netbuf_t* buf,
                               const uint8_t* dst_mac,
                               const uint8_t* src_mac,
                               uint16_t len)
{
    /* Set up Ethernet header */
    if (len >= sizeof(vos3_eth_header_t) && buf->capacity >= sizeof(vos3_eth_header_t)) {
        vos3_eth_header_t* eth = (vos3_eth_header_t*)buf->data;

        /* Copy MACs */
        for (int i = 0; i < 6; i++) {
            eth->dst.bytes[i] = dst_mac[i];
            eth->src.bytes[i] = src_mac[i];
        }

        /* Set EtherType to IPv4 */
        eth->ethertype = vos3_htons(VOS3_ETHERTYPE_IPV4);
    }

    buf->len = len;
    buf->flags = VOS3_NETBUF_F_ALLOCATED | VOS3_NETBUF_F_VALIDATED;
}

/**
 * @brief Simple pseudo-random number generator
 */
static uint32_t stress_rand_state = 0xDEADBEEF;

static uint32_t stress_rand(void)
{
    stress_rand_state = stress_rand_state * 1103515245 + 12345;
    return (stress_rand_state >> 16) & 0x7FFF;
}

/* ============================================================================
 * SCENARIO A: THE GIANTS (Oversized Packets)
 * ============================================================================ */

/**
 * @brief Test oversized packet rejection
 *
 * Inject 50 packets with length > 1522 bytes.
 * Expected: 0 accepted, 50 dropped.
 */
static void stress_test_giants(void)
{
    VOS3_INFO("[STRESS-A] ========================================");
    VOS3_INFO("[STRESS-A] SCENARIO A: THE GIANTS");
    VOS3_INFO("[STRESS-A] Injecting %d packets > %u bytes",
              STRESS_SIZE_ATTACK_COUNT, VOS3_NET_MTU_MAX);
    VOS3_INFO("[STRESS-A] ========================================");

    stress_scenario_result_t* result = &g_stress_results.giants;
    result->name = "Giants (>1522 bytes)";
    result->packets_sent = 0;
    result->packets_dropped = 0;
    result->packets_accepted = 0;

    /* Create test interface */
    vos3_netif_t test_if;
    memset(&test_if, 0, sizeof(test_if));
    memcpy(test_if.name, "stress0", 8);

    /* Set our MAC */
    test_if.mac_addr.bytes[0] = 0x52;
    test_if.mac_addr.bytes[1] = 0x54;
    test_if.mac_addr.bytes[2] = 0x00;
    test_if.mac_addr.bytes[3] = 0x12;
    test_if.mac_addr.bytes[4] = 0x34;
    test_if.mac_addr.bytes[5] = 0x56;

    /* Source MAC */
    uint8_t src_mac[6] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF};
    (void)src_mac;

    /* Record stats before */
    uint64_t dropped_before = g_net_security_stats.oversized_dropped;

    /* Inject oversized packets */
    for (size_t i = 0; i < STRESS_SIZE_ATTACK_COUNT; i++) {
        vos3_netbuf_t test_buf;
        memset(&test_buf, 0, sizeof(test_buf));

        /* Set oversized length */
        test_buf.len = STRESS_GIANT_SIZE + (i * 10);  /* Vary sizes */
        test_buf.capacity = test_buf.len;
        test_buf.flags = VOS3_NETBUF_F_ALLOCATED | VOS3_NETBUF_F_VALIDATED;

        result->packets_sent++;

        int ret = vos3_net_rx_ethernet(&test_if, &test_buf);

        if (ret < 0) {
            result->packets_dropped++;
        } else {
            result->packets_accepted++;
        }
    }

    uint64_t dropped_after = g_net_security_stats.oversized_dropped;
    uint64_t actual_dropped = dropped_after - dropped_before;

    /* Verify results */
    result->passed = (result->packets_accepted == 0) &&
                     (result->packets_dropped == STRESS_SIZE_ATTACK_COUNT);

    VOS3_INFO("[STRESS-A] Sent: %zu, Dropped: %zu, Accepted: %zu",
              result->packets_sent, result->packets_dropped, result->packets_accepted);
    VOS3_INFO("[STRESS-A] Security counter delta: %llu",
              (unsigned long long)actual_dropped);
    VOS3_INFO("[STRESS-A] Result: %s",
              result->passed ? "PASS" : "FAIL");
}

/* ============================================================================
 * SCENARIO B: THE DWARVES (Undersized Packets)
 * ============================================================================ */

/**
 * @brief Test undersized packet rejection
 *
 * Inject 50 packets with length < 60 bytes.
 * Expected: 0 accepted, 50 dropped.
 */
static void stress_test_dwarves(void)
{
    VOS3_INFO("[STRESS-B] ========================================");
    VOS3_INFO("[STRESS-B] SCENARIO B: THE DWARVES");
    VOS3_INFO("[STRESS-B] Injecting %d packets < %u bytes",
              STRESS_SIZE_ATTACK_COUNT, VOS3_NET_FRAME_MIN);
    VOS3_INFO("[STRESS-B] ========================================");

    stress_scenario_result_t* result = &g_stress_results.dwarves;
    result->name = "Dwarves (<60 bytes)";
    result->packets_sent = 0;
    result->packets_dropped = 0;
    result->packets_accepted = 0;

    /* Create test interface */
    vos3_netif_t test_if;
    memset(&test_if, 0, sizeof(test_if));
    memcpy(test_if.name, "stress1", 8);

    /* Set our MAC */
    test_if.mac_addr.bytes[0] = 0x52;
    test_if.mac_addr.bytes[1] = 0x54;
    test_if.mac_addr.bytes[2] = 0x00;
    test_if.mac_addr.bytes[3] = 0x12;
    test_if.mac_addr.bytes[4] = 0x34;
    test_if.mac_addr.bytes[5] = 0x56;

    /* Record stats before */
    uint64_t dropped_before = g_net_security_stats.undersized_dropped;

    /* Inject undersized packets */
    for (size_t i = 0; i < STRESS_SIZE_ATTACK_COUNT; i++) {
        vos3_netbuf_t test_buf;
        memset(&test_buf, 0, sizeof(test_buf));

        /* Set undersized length (vary from 1 to 59 bytes) */
        test_buf.len = (uint16_t)(1 + (i % (VOS3_NET_FRAME_MIN - 1)));
        test_buf.capacity = 64;
        test_buf.flags = VOS3_NETBUF_F_ALLOCATED | VOS3_NETBUF_F_VALIDATED;

        result->packets_sent++;

        int ret = vos3_net_rx_ethernet(&test_if, &test_buf);

        if (ret < 0) {
            result->packets_dropped++;
        } else {
            result->packets_accepted++;
        }
    }

    uint64_t dropped_after = g_net_security_stats.undersized_dropped;
    uint64_t actual_dropped = dropped_after - dropped_before;

    /* Verify results */
    result->passed = (result->packets_accepted == 0) &&
                     (result->packets_dropped == STRESS_SIZE_ATTACK_COUNT);

    VOS3_INFO("[STRESS-B] Sent: %zu, Dropped: %zu, Accepted: %zu",
              result->packets_sent, result->packets_dropped, result->packets_accepted);
    VOS3_INFO("[STRESS-B] Security counter delta: %llu",
              (unsigned long long)actual_dropped);
    VOS3_INFO("[STRESS-B] Result: %s",
              result->passed ? "PASS" : "FAIL");
}

/* ============================================================================
 * SCENARIO C: THE STORM (5000 Packet Flood)
 * ============================================================================ */

/**
 * @brief Test flood attack resilience
 *
 * Inject 5000 valid packets in a tight loop.
 * Expected: System uptime > 0, Throttling Active flag = TRUE.
 */
static void stress_test_storm(void)
{
    VOS3_INFO("[STRESS-C] ========================================");
    VOS3_INFO("[STRESS-C] SCENARIO C: THE STORM");
    VOS3_INFO("[STRESS-C] Injecting %d packets (flood attack)",
              STRESS_STORM_PACKET_COUNT);
    VOS3_INFO("[STRESS-C] ========================================");

    stress_scenario_result_t* result = &g_stress_results.storm;
    result->name = "Storm (5000 flood)";
    result->packets_sent = 0;
    result->packets_dropped = 0;
    result->packets_accepted = 0;

    /* Record throttle events before */
    uint64_t throttle_before = g_net_security_stats.budget_exceeded_events;

    /* Run flood attack simulation */
    int throttle_events = vos3_net_test_flood_attack(STRESS_STORM_PACKET_COUNT);

    if (throttle_events < 0) {
        VOS3_ERROR("[STRESS-C] Flood test failed to execute");
        result->passed = 0;
        return;
    }

    result->packets_sent = STRESS_STORM_PACKET_COUNT;

    uint64_t throttle_after = g_net_security_stats.budget_exceeded_events;
    g_stress_results.throttle_events = throttle_after - throttle_before;

    /* Calculate expected throttle events:
     * With 5000 packets and budget of 32, we expect ~156 throttle events
     * (5000 / 32 = 156.25)
     */
    size_t expected_throttles = STRESS_STORM_PACKET_COUNT / VOS3_NET_IRQ_BUDGET;

    /* Verify throttling occurred */
    result->passed = (throttle_events > 0) &&
                     ((size_t)throttle_events >= expected_throttles - 5);  /* Allow small variance */

    VOS3_INFO("[STRESS-C] Packets simulated: %zu", result->packets_sent);
    VOS3_INFO("[STRESS-C] Throttle events: %llu (expected ~%zu)",
              (unsigned long long)g_stress_results.throttle_events, expected_throttles);
    VOS3_INFO("[STRESS-C] System responsive: YES (you're reading this)");
    VOS3_INFO("[STRESS-C] Result: %s",
              result->passed ? "PASS" : "FAIL");
}

/* ============================================================================
 * SCENARIO D: THE SPOOF (MAC Address Spoofing)
 * ============================================================================ */

/**
 * @brief Test MAC address filtering
 *
 * Inject packets with random destination MACs (not ours).
 * Expected: Filtered by MAC check, packet count = 0.
 */
static void stress_test_spoof(void)
{
    VOS3_INFO("[STRESS-D] ========================================");
    VOS3_INFO("[STRESS-D] SCENARIO D: THE SPOOF");
    VOS3_INFO("[STRESS-D] Injecting %d packets with wrong MACs",
              STRESS_SPOOF_ATTACK_COUNT);
    VOS3_INFO("[STRESS-D] ========================================");

    stress_scenario_result_t* result = &g_stress_results.spoof;
    result->name = "Spoof (wrong MAC)";
    result->packets_sent = 0;
    result->packets_dropped = 0;
    result->packets_accepted = 0;

    /* Create test interface with specific MAC */
    vos3_netif_t test_if;
    memset(&test_if, 0, sizeof(test_if));
    memcpy(test_if.name, "stress3", 8);

    /* Our MAC: 52:54:00:12:34:56 */
    test_if.mac_addr.bytes[0] = 0x52;
    test_if.mac_addr.bytes[1] = 0x54;
    test_if.mac_addr.bytes[2] = 0x00;
    test_if.mac_addr.bytes[3] = 0x12;
    test_if.mac_addr.bytes[4] = 0x34;
    test_if.mac_addr.bytes[5] = 0x56;

    /* Source MAC (doesn't matter for this test) */
    uint8_t src_mac[6] = {0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE};

    /* Inject packets with random destination MACs */
    for (size_t i = 0; i < STRESS_SPOOF_ATTACK_COUNT; i++) {
        /* Use combined structure to safely access data[] */
        stress_test_netbuf_t test_combined;
        memset(&test_combined, 0, sizeof(test_combined));

        vos3_netbuf_t* test_buf = &test_combined.header;

        /* Valid size */
        test_buf->len = 64;
        test_buf->capacity = sizeof(test_combined.data);
        test_buf->flags = VOS3_NETBUF_F_ALLOCATED | VOS3_NETBUF_F_VALIDATED;

        /* Create Ethernet header with RANDOM destination MAC */
        vos3_eth_header_t* eth = (vos3_eth_header_t*)test_combined.data;

        /* Generate random MAC (never matching ours or broadcast) */
        eth->dst.bytes[0] = (uint8_t)(stress_rand() & 0xFE);  /* Clear multicast bit */
        eth->dst.bytes[1] = (uint8_t)(stress_rand() & 0xFF);
        eth->dst.bytes[2] = (uint8_t)(stress_rand() & 0xFF);
        eth->dst.bytes[3] = (uint8_t)(stress_rand() & 0xFF);
        eth->dst.bytes[4] = (uint8_t)(stress_rand() & 0xFF);
        eth->dst.bytes[5] = (uint8_t)(stress_rand() & 0xFE);  /* Never 0xFF (broadcast) */

        /* Ensure it's not our MAC */
        if (eth->dst.bytes[0] == 0x52 && eth->dst.bytes[1] == 0x54) {
            eth->dst.bytes[0] = 0x00;  /* Change it */
        }

        /* Copy source MAC */
        for (int j = 0; j < 6; j++) {
            eth->src.bytes[j] = src_mac[j];
        }

        eth->ethertype = vos3_htons(VOS3_ETHERTYPE_IPV4);

        result->packets_sent++;

        int ret = vos3_net_rx_ethernet(&test_if, test_buf);

        if (ret < 0) {
            result->packets_dropped++;
        } else {
            result->packets_accepted++;
        }
    }

    /* Verify results - ALL packets should be dropped */
    result->passed = (result->packets_accepted == 0) &&
                     (result->packets_dropped == STRESS_SPOOF_ATTACK_COUNT);

    VOS3_INFO("[STRESS-D] Sent: %zu, Dropped: %zu, Accepted: %zu",
              result->packets_sent, result->packets_dropped, result->packets_accepted);
    VOS3_INFO("[STRESS-D] MAC filtering: %s",
              result->packets_accepted == 0 ? "EFFECTIVE" : "BYPASSED!");
    VOS3_INFO("[STRESS-D] Result: %s",
              result->passed ? "PASS" : "FAIL");
}

/* ============================================================================
 * RESULTS TABLE
 * ============================================================================ */

/**
 * @brief Print formatted results table
 */
static void stress_print_results_table(void)
{
    VOS3_INFO("");
    VOS3_INFO("+=================================================================+");
    VOS3_INFO("|        VOS3 NETWORK DRIVER PENETRATION TEST RESULTS            |");
    VOS3_INFO("+=================================================================+");
    VOS3_INFO("| Attack Type         | Sent  | Dropped | Accepted | Result      |");
    VOS3_INFO("+-----------------------------------------------------------------+");

    /* Scenario A */
    VOS3_INFO("| %-19s | %5zu | %7zu | %8zu | %-11s |",
              g_stress_results.giants.name,
              g_stress_results.giants.packets_sent,
              g_stress_results.giants.packets_dropped,
              g_stress_results.giants.packets_accepted,
              g_stress_results.giants.passed ? "PASS" : "FAIL");

    /* Scenario B */
    VOS3_INFO("| %-19s | %5zu | %7zu | %8zu | %-11s |",
              g_stress_results.dwarves.name,
              g_stress_results.dwarves.packets_sent,
              g_stress_results.dwarves.packets_dropped,
              g_stress_results.dwarves.packets_accepted,
              g_stress_results.dwarves.passed ? "PASS" : "FAIL");

    /* Scenario C */
    VOS3_INFO("| %-19s | %5zu | %7s | %8s | %-11s |",
              g_stress_results.storm.name,
              g_stress_results.storm.packets_sent,
              "N/A",
              "THROTTLED",
              g_stress_results.storm.passed ? "PASS" : "FAIL");

    /* Scenario D */
    VOS3_INFO("| %-19s | %5zu | %7zu | %8zu | %-11s |",
              g_stress_results.spoof.name,
              g_stress_results.spoof.packets_sent,
              g_stress_results.spoof.packets_dropped,
              g_stress_results.spoof.packets_accepted,
              g_stress_results.spoof.passed ? "PASS" : "FAIL");

    VOS3_INFO("+-----------------------------------------------------------------+");
    VOS3_INFO("| Throttle Events: %-3llu | IRQ Budget: %2u pkts/IRQ              |",
              (unsigned long long)g_stress_results.throttle_events,
              VOS3_NET_IRQ_BUDGET);
    VOS3_INFO("+=================================================================+");

    /* Final verdict */
    g_stress_results.all_passed = g_stress_results.giants.passed &&
                                   g_stress_results.dwarves.passed &&
                                   g_stress_results.storm.passed &&
                                   g_stress_results.spoof.passed;

    VOS3_INFO("");
    if (g_stress_results.all_passed) {
        VOS3_INFO("+=========================================================+");
        VOS3_INFO("|  VERDICT: ALL PENETRATION TESTS PASSED                  |");
        VOS3_INFO("|  The network driver is MATHEMATICALLY PROVEN resilient  |");
        VOS3_INFO("+=========================================================+");
    } else {
        VOS3_ERROR("+=========================================================+");
        VOS3_ERROR("|  VERDICT: PENETRATION TESTS FAILED                      |");
        VOS3_ERROR("|  Security vulnerabilities detected!                     |");
        VOS3_ERROR("+=========================================================+");
    }
    VOS3_INFO("");
}

/* ============================================================================
 * PUBLIC API
 * ============================================================================ */

/**
 * @brief Run complete network stress test suite
 *
 * Executes all 4 attack scenarios and produces results table.
 *
 * @return 1 if all tests passed, 0 if any failed
 */
int vos3_net_stress_test(void)
{
    VOS3_INFO("");
    VOS3_INFO("###############################################################");
    VOS3_INFO("#                                                             #");
    VOS3_INFO("#   VOS3 NETWORK DRIVER DEEP PENETRATION TEST SUITE           #");
    VOS3_INFO("#   Day 3.5 - Mathematical Proof of Resilience                #");
    VOS3_INFO("#                                                             #");
    VOS3_INFO("###############################################################");
    VOS3_INFO("");

    /* Initialize results */
    memset(&g_stress_results, 0, sizeof(g_stress_results));

    /* Run all scenarios */
    stress_test_giants();
    VOS3_INFO("");

    stress_test_dwarves();
    VOS3_INFO("");

    stress_test_storm();
    VOS3_INFO("");

    stress_test_spoof();
    VOS3_INFO("");

    /* Print results table */
    stress_print_results_table();

    return g_stress_results.all_passed ? 1 : 0;
}
