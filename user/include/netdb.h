/**
 * @file netdb.h
 * @brief VOS3 User-Space Network Database Definitions
 *
 * @version 1.0.0
 * @date 2026-03-07
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_USER_NETDB_H
#define VOS3_USER_NETDB_H

/**
 * @brief Resolve a hostname to an IPv4 address
 *
 * @param hostname  Hostname to resolve (e.g., "example.com")
 * @param out_ip    Output: IPv4 address in network byte order
 * @return 0 on success, -1 on error
 */
int dns_resolve(const char* hostname, unsigned int* out_ip);

#endif /* VOS3_USER_NETDB_H */
