/**
 * @file dns.c
 * @brief VOS3 Minimal DNS Stub Resolver
 *
 * @details Sends A-record DNS queries to QEMU SLIRP DNS (10.0.2.3:53)
 *          using raw UDP sockets. Only supports A records, no caching.
 *
 * @version 1.0.0
 * @date 2026-03-07
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "syscall.h"
#include "string.h"

/* Socket syscall numbers (match kernel socket.h) */
#define SYS_SOCKET      150
#define SYS_BIND        151
#define SYS_SENDTO      155
#define SYS_RECVFROM    156
#define SYS_CLOSE       3
#define SYS_GETRANDOM   318

/* Socket constants */
#define AF_INET         2
#define SOCK_DGRAM      2
#define IPPROTO_UDP     17

/* DNS port and resolver */
#define DNS_PORT        53
#define DNS_RESOLVER_IP 0x0A000203U  /* 10.0.2.3 (QEMU SLIRP DNS) in host byte order */

/* DNS header flags */
#define DNS_QR_QUERY    0x0000
#define DNS_OPCODE_STD  0x0000
#define DNS_RD          0x0100  /* Recursion desired */
#define DNS_QTYPE_A     1      /* A record */
#define DNS_QCLASS_IN   1      /* Internet class */

/* Byte order helpers */
static unsigned short htons(unsigned short x)
{
    return (unsigned short)((x >> 8) | (x << 8));
}

static unsigned int htonl(unsigned int x)
{
    return ((x >> 24) & 0xFF) |
           ((x >>  8) & 0xFF00) |
           ((x <<  8) & 0xFF0000) |
           ((x << 24) & 0xFF000000U);
}

static unsigned short ntohs(unsigned short x)
{
    return htons(x);
}

static unsigned int ntohl(unsigned int x)
{
    return htonl(x);
}

/* Socket address structure */
struct sockaddr_in {
    unsigned short  sin_family;
    unsigned short  sin_port;
    unsigned int    sin_addr;
    unsigned char   sin_zero[8];
} __attribute__((packed));

/**
 * @brief Encode a hostname into DNS wire format
 *
 * Converts "www.example.com" to "\3www\7example\3com\0"
 * @return Length of encoded name, or -1 on error
 */
static int dns_encode_name(const char* hostname, unsigned char* buf, int bufsz)
{
    int pos = 0;
    const char* p = hostname;

    while (*p) {
        /* Find label end (next dot or end of string) */
        const char* dot = p;
        int label_len = 0;
        while (*dot && *dot != '.') {
            dot++;
            label_len++;
        }

        if (label_len == 0 || label_len > 63) return -1;
        if (pos + label_len + 2 > bufsz) return -1;

        /* Write length byte + label */
        buf[pos++] = (unsigned char)label_len;
        for (int i = 0; i < label_len; i++) {
            buf[pos++] = (unsigned char)p[i];
        }

        p = dot;
        if (*p == '.') p++;
    }

    if (pos + 1 > bufsz) return -1;
    buf[pos++] = 0; /* Root label terminator */
    return pos;
}

/**
 * @brief Resolve a hostname to an IPv4 address
 *
 * Sends a DNS A-record query to 10.0.2.3 (SLIRP DNS) and parses the response.
 *
 * @param hostname  The hostname to resolve (e.g., "example.com")
 * @param out_ip    Output: resolved IPv4 address in network byte order
 * @return 0 on success, -1 on error
 */
int dns_resolve(const char* hostname, unsigned int* out_ip)
{
    if (hostname == NULL || out_ip == NULL) return -1;

    /* Create UDP socket */
    long sockfd = syscall3(SYS_SOCKET, AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) return -1;

    /* Build DNS query packet */
    unsigned char pkt[512];
    int pos = 0;

    /* DNS Header (12 bytes) — random TXID for security */
    unsigned short txid = 0x1234;
    if (syscall3(SYS_GETRANDOM, (long)&txid, 2, 0) < 0) {
        txid = 0x1234;  /* fallback if getrandom fails */
    }
    pkt[pos++] = (unsigned char)(txid >> 8);
    pkt[pos++] = (unsigned char)(txid & 0xFF);
    /* Flags: standard query, recursion desired */
    unsigned short flags = DNS_RD;
    pkt[pos++] = (unsigned char)(flags >> 8);
    pkt[pos++] = (unsigned char)(flags & 0xFF);
    /* QDCOUNT = 1 */
    pkt[pos++] = 0; pkt[pos++] = 1;
    /* ANCOUNT = 0 */
    pkt[pos++] = 0; pkt[pos++] = 0;
    /* NSCOUNT = 0 */
    pkt[pos++] = 0; pkt[pos++] = 0;
    /* ARCOUNT = 0 */
    pkt[pos++] = 0; pkt[pos++] = 0;

    /* Question section: encoded hostname */
    int name_len = dns_encode_name(hostname, pkt + pos, (int)sizeof(pkt) - pos - 4);
    if (name_len < 0) {
        syscall1(SYS_CLOSE, sockfd);
        return -1;
    }
    pos += name_len;

    /* QTYPE = A (1) */
    pkt[pos++] = 0;
    pkt[pos++] = (unsigned char)DNS_QTYPE_A;
    /* QCLASS = IN (1) */
    pkt[pos++] = 0;
    pkt[pos++] = (unsigned char)DNS_QCLASS_IN;

    /* Send to SLIRP DNS (10.0.2.3:53) */
    struct sockaddr_in dest;
    memset(&dest, 0, sizeof(dest));
    dest.sin_family = AF_INET;
    dest.sin_port = htons(DNS_PORT);
    dest.sin_addr = htonl(DNS_RESOLVER_IP);

    long sent = syscall6(SYS_SENDTO, sockfd, (long)pkt, (long)pos,
                         0, (long)&dest, (long)sizeof(dest));
    if (sent < 0) {
        syscall1(SYS_CLOSE, sockfd);
        return -1;
    }

    /* Receive response */
    unsigned char resp[512];
    struct sockaddr_in from;
    unsigned int fromlen = sizeof(from);

    long rcvd = syscall6(SYS_RECVFROM, sockfd, (long)resp, (long)sizeof(resp),
                         0, (long)&from, (long)&fromlen);
    syscall1(SYS_CLOSE, sockfd);

    if (rcvd < 12) return -1;  /* Too short for DNS header */

    /* Parse response header */
    unsigned short r_flags = (unsigned short)((resp[2] << 8) | resp[3]);
    unsigned short r_ancount = (unsigned short)((resp[6] << 8) | resp[7]);

    /* Check for errors (RCODE in bottom 4 bits of flags) */
    if ((r_flags & 0x000F) != 0) return -1;
    if (r_ancount == 0) return -1;

    /* Skip question section (header is 12 bytes, then skip QNAME + QTYPE + QCLASS) */
    int rpos = 12;
    /* Skip QNAME */
    while (rpos < rcvd && resp[rpos] != 0) {
        if ((resp[rpos] & 0xC0) == 0xC0) {
            rpos += 2;  /* Pointer */
            goto skip_done;
        }
        rpos += resp[rpos] + 1;
    }
    rpos++; /* Skip trailing 0 */
skip_done:
    rpos += 4; /* Skip QTYPE + QCLASS */

    /* Parse first answer record */
    /* Skip NAME (may be compressed) */
    if (rpos >= rcvd) return -1;
    if ((resp[rpos] & 0xC0) == 0xC0) {
        rpos += 2;  /* Pointer */
    } else {
        while (rpos < rcvd && resp[rpos] != 0) {
            rpos += resp[rpos] + 1;
        }
        rpos++;
    }

    /* Read TYPE (2), CLASS (2), TTL (4), RDLENGTH (2) */
    if (rpos + 10 > rcvd) return -1;
    unsigned short atype = (unsigned short)((resp[rpos] << 8) | resp[rpos+1]);
    rpos += 2; /* TYPE */
    rpos += 2; /* CLASS */
    rpos += 4; /* TTL */
    unsigned short rdlen = (unsigned short)((resp[rpos] << 8) | resp[rpos+1]);
    rpos += 2; /* RDLENGTH */

    /* Verify it's an A record with 4 bytes of data */
    if (atype != DNS_QTYPE_A || rdlen != 4) return -1;
    if (rpos + 4 > rcvd) return -1;

    /* Extract IPv4 address (already in network byte order) */
    *out_ip = (unsigned int)resp[rpos] |
              ((unsigned int)resp[rpos+1] << 8) |
              ((unsigned int)resp[rpos+2] << 16) |
              ((unsigned int)resp[rpos+3] << 24);

    return 0;
}
