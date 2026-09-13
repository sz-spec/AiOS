/**
 * @file poll.h
 * @brief VOS3 User-Space poll() Definitions
 *
 * @version 1.0.0
 * @date 2026-03-07
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#ifndef VOS3_USER_POLL_H
#define VOS3_USER_POLL_H

#include <stdint.h>

/* ============================================================================
 * POLL EVENT FLAGS
 * ============================================================================ */

/** @brief Data available for reading */
#define POLLIN      0x0001

/** @brief Urgent data available */
#define POLLPRI     0x0002

/** @brief Writing possible */
#define POLLOUT     0x0004

/** @brief Error condition (output only) */
#define POLLERR     0x0008

/** @brief Hang up (output only) */
#define POLLHUP     0x0010

/** @brief Invalid fd (output only) */
#define POLLNVAL    0x0020

/* ============================================================================
 * POLL STRUCTURES
 * ============================================================================ */

/**
 * @brief File descriptor poll request structure
 */
struct pollfd {
    int     fd;         /**< File descriptor */
    short   events;     /**< Requested events */
    short   revents;    /**< Returned events */
};

/* ============================================================================
 * POLL FUNCTION
 * ============================================================================ */

/**
 * @brief Wait for events on file descriptors
 * @param fds Array of pollfd structures
 * @param nfds Number of entries in fds
 * @param timeout Timeout in milliseconds (-1 = infinite, 0 = immediate)
 * @return Number of fds with events, 0 on timeout, -1 on error
 */
int poll(struct pollfd *fds, unsigned int nfds, int timeout);

#endif /* VOS3_USER_POLL_H */
