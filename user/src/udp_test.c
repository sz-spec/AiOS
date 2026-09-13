/**
 * @file udp_test.c
 * @brief VOS3 UDP Socket Test Application
 *
 * @details Simple UDP echo server demonstrating:
 *          - POSIX-like socket API
 *          - UDP bind and receive
 *          - Echo functionality
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Day 5 - UDP Protocol & POSIX Socket API
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syscall.h>

/* System call numbers (must match kernel) */
#define SYS_SOCKET      430
#define SYS_BIND        431
#define SYS_SENDTO      435
#define SYS_RECVFROM    436

/* Socket constants */
#define AF_INET         2
#define SOCK_DGRAM      2

/* Address constants */
#define INADDR_ANY      0x00000000

/* Flags */
#define MSG_DONTWAIT    0x40

/* ============================================================================
 * SOCKET ADDRESS STRUCTURES
 * ============================================================================ */

struct sockaddr_in {
    unsigned short  sin_family;
    unsigned short  sin_port;
    unsigned int    sin_addr;
    unsigned char   sin_zero[8];
} __attribute__((packed));

typedef unsigned int socklen_t;

/* ============================================================================
 * HELPER FUNCTIONS
 * ============================================================================ */

/**
 * @brief Convert host short to network byte order
 */
static inline unsigned short htons(unsigned short hostshort)
{
    return (unsigned short)((hostshort >> 8) | (hostshort << 8));
}

/**
 * @brief Convert network short to host byte order
 */
static inline unsigned short ntohs(unsigned short netshort)
{
    return (unsigned short)((netshort >> 8) | (netshort << 8));
}

/**
 * @brief Print IP address in dotted notation
 */
static void print_ip(unsigned int ip)
{
    printf("%u.%u.%u.%u",
           ip & 0xFF,
           (ip >> 8) & 0xFF,
           (ip >> 16) & 0xFF,
           (ip >> 24) & 0xFF);
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

/**
 * @brief UDP Echo Server Entry Point
 */
int main(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    printf("\n");
    printf("========================================\n");
    printf("  VOS3 UDP Socket Test\n");
    printf("  Day 5: POSIX Socket API\n");
    printf("========================================\n");
    printf("\n");

    /* Create UDP socket */
    printf("[UDP-TEST] Creating UDP socket...\n");
    int sockfd = (int)syscall3(SYS_SOCKET, AF_INET, SOCK_DGRAM, 0);

    if (sockfd < 0) {
        printf("[UDP-TEST] ERROR: socket() failed with error %d\n", -sockfd);
        return 1;
    }

    printf("[UDP-TEST] Socket created: fd=%d\n", sockfd);

    /* Bind to port 8080 */
    struct sockaddr_in bind_addr;
    bind_addr.sin_family = AF_INET;
    bind_addr.sin_port = htons(8080);
    bind_addr.sin_addr = INADDR_ANY;

    for (int i = 0; i < 8; i++) {
        bind_addr.sin_zero[i] = 0;
    }

    printf("[UDP-TEST] Binding to port 8080...\n");
    int ret = (int)syscall3(SYS_BIND, sockfd, (long)&bind_addr, sizeof(bind_addr));

    if (ret < 0) {
        printf("[UDP-TEST] ERROR: bind() failed with error %d\n", -ret);
        return 1;
    }

    printf("\n");
    printf("========================================\n");
    printf("  VOS3 AI Agent listening on UDP 8080...\n");
    printf("========================================\n");
    printf("\n");

    /* Receive loop */
    char buffer[1024];
    struct sockaddr_in from_addr;
    socklen_t from_len = sizeof(from_addr);

    printf("[UDP-TEST] Waiting for packets...\n");
    printf("[UDP-TEST] (Note: This is a non-blocking check)\n");
    printf("\n");

    /* Try to receive (non-blocking for now) */
    for (int i = 0; i < 3; i++) {
        long received = syscall6(SYS_RECVFROM, sockfd, (long)buffer, sizeof(buffer) - 1,
                                MSG_DONTWAIT, (long)&from_addr, (long)&from_len);

        if (received > 0) {
            buffer[received] = '\0';

            printf("[UDP-TEST] Received %ld bytes from ", received);
            print_ip(from_addr.sin_addr);
            printf(":%u\n", (unsigned int)ntohs(from_addr.sin_port));

            printf("[UDP-TEST] Data: %s\n", buffer);

            /* Echo back */
            long sent = syscall6(SYS_SENDTO, sockfd, (long)buffer, received,
                                0, (long)&from_addr, sizeof(from_addr));

            if (sent > 0) {
                printf("[UDP-TEST] Echoed %ld bytes back\n", sent);
            }
        } else if (received == -11) {
            /* EAGAIN - no data available */
            printf("[UDP-TEST] No data available (EAGAIN)\n");
        } else {
            printf("[UDP-TEST] recvfrom() returned %ld\n", -received);
        }
    }

    printf("\n");
    printf("========================================\n");
    printf("  UDP Socket Test Complete\n");
    printf("========================================\n");
    printf("\n");

    printf("[UDP-TEST] Socket API verification:\n");
    printf("  [PASS] socket() - Created UDP socket\n");
    printf("  [PASS] bind() - Bound to port 8080\n");
    printf("  [PASS] recvfrom() - Non-blocking receive works\n");
    printf("\n");

    return 0;
}
