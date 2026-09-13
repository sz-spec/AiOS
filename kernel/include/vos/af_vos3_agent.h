/**
 * @file af_vos3_agent.h
 * @brief AF_VOS3_AGENT socket family (Sprint 16 / Item H3).
 *
 * Why this exists
 * ---------------
 *
 * From the 80-problem agent-era catalog, H3:
 *   "Agent-to-agent comm: no 'trusted local channel' OS primitive —
 *    sibling agents on same host should be able to verify each other;
 *    only userspace mTLS today."
 *
 * Userspace mTLS for sibling agents on the same host is wasteful (full
 * TLS handshake for every connection) and brittle (cert distribution +
 * rotation each side). The Linux kernel has SO_PEERCRED for AF_UNIX
 * sockets, but it only exposes uid/gid/pid — not SPIFFE identity.
 *
 * AF_VOS3_AGENT is a new socket family that:
 *
 *   1. Lives on top of AF_UNIX (it's not actually a new kernel-level
 *      domain — it's a thin wrapper that piggybacks on AF_UNIX's
 *      SO_PEERCRED + adds SPIFFE-SVID exchange at connect() time).
 *   2. At connect(), each side presents its SPIFFE SVID (via the F1
 *      SPIFFEWITVerifier shipped Sprint 15, optionally federated via
 *      F4 SPIFFEFederationVerifier shipped this same Wave 2).
 *   3. The kernel-side hook validates SVIDs against the local trust
 *      bundle BEFORE the connect() returns success — so neither side
 *      sees data from a peer whose identity hasn't been verified.
 *   4. The verified peer SPIFFE-ID is exposed via SO_VOS3_PEER_SPIFFE
 *      (analogous to SO_PEERCRED) for the userspace handler to make
 *      authorization decisions.
 *
 * Public surface
 * --------------
 *
 *   AF_VOS3_AGENT                  socket family constant
 *   SOCK_VOS3_AGENT                socket type constant
 *   SO_VOS3_PEER_SPIFFE           getsockopt name for peer SPIFFE-ID
 *   SO_VOS3_TRUST_DOMAIN_REQUIRE  setsockopt to restrict accepted peers
 *
 *   vos3_af_vos3_agent_register() — module init (kernel)
 *   vos3_af_vos3_agent_unregister() — module fini (kernel)
 *
 * Honest scope ceiling
 * --------------------
 *
 *   - This header declares the protocol. The kernel-side socket-family
 *     registration (net/af_vos3_agent.c — to be written in Wave 3)
 *     hooks into AF_UNIX + the F1/F4 verifier. Today, the Python
 *     simulator in backend/services/agent_local_channel.py exercises
 *     the connect-time SVID-exchange handshake end-to-end against the
 *     F1 verifier surface so the integration is testable.
 *
 *   - Connect-time SVID exchange adds a one-time ~5ms latency (matches
 *     SPIFFE Workload API latency budget). The first read/write after
 *     connect carries zero extra overhead vs plain AF_UNIX.
 *
 *   - SVID rotation during a long-lived connection is NOT enforced
 *     by this layer; the F5 vos3_cred_rotate path will invalidate the
 *     fd if the underlying credential expires.
 */

#ifndef VOS_AF_VOS3_AGENT_H
#define VOS_AF_VOS3_AGENT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * Constants — must NOT collide with existing AF_* in glibc / Linux.
 * AF_MAX in glibc is currently 46; we pick a private high range start.
 * ============================================================================ */

#define VOS3_AF_VOS3_AGENT           0x9603   /**< Private family number */
#define VOS3_SOCK_VOS3_AGENT         0x9604

/** getsockopt name for peer SPIFFE-ID (returned as null-terminated UTF-8) */
#define VOS3_SO_VOS3_PEER_SPIFFE     0x9610

/** setsockopt: require peer trust-domain match before accept()/connect() */
#define VOS3_SO_VOS3_TRUST_DOMAIN_REQUIRE  0x9611

/* ============================================================================
 * Maximum lengths
 * ============================================================================ */

/** SPIFFE-ID max length (the RFC suggests 2048 bytes; we cap at 512 for
 *  in-kernel buffer sizing). */
#define VOS3_AF_VOS3_AGENT_SPIFFE_MAX_LEN    512U

/** Trust-domain max length (RFC 6125 SAN dNSName limit). */
#define VOS3_AF_VOS3_AGENT_TRUST_DOMAIN_MAX_LEN 253U

/* ============================================================================
 * Return codes
 * ============================================================================ */

#define VOS3_AF_VOS3_AGENT_OK              0
#define VOS3_AF_VOS3_AGENT_ERR_INVAL      -1
#define VOS3_AF_VOS3_AGENT_ERR_PEER_AUTH  -2  /**< Peer's SVID failed verification */
#define VOS3_AF_VOS3_AGENT_ERR_TRUST_REQ  -3  /**< Peer's trust domain not in required set */
#define VOS3_AF_VOS3_AGENT_ERR_NOMEM      -4

/* ============================================================================
 * Module entry points
 * ============================================================================ */

int  vos3_af_vos3_agent_register(void);
void vos3_af_vos3_agent_unregister(void);

#ifdef __cplusplus
}
#endif

#endif /* VOS_AF_VOS3_AGENT_H */
