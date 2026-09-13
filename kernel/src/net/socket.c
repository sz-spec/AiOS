/**
 * @file socket.c
 * @brief VOS3 POSIX Socket Implementation
 *
 * @details User-space socket API with security hardening:
 *          - Privileged port protection (< 1024)
 *          - Buffer sanitization (CVE-2026-23057)
 *          - copy_to_user/copy_from_user validation
 *
 * @version 1.0.0
 * @date 2026-02-18
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note Day 5 - UDP Protocol & POSIX Socket API
 */

#include "../../include/vos/socket.h"
#include "../../include/vos/tcp.h"
#include "../../include/vos/net.h"
#include "../../include/vos/net_security.h"
#include "../../include/vos/compiler.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/uaccess.h"
#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/sync.h"

/* ============================================================================
 * SOCKET POOL
 * ============================================================================ */

/** @brief Maximum sockets system-wide */
#define SOCKET_POOL_SIZE    1024U

/** @brief Socket pool */
static vos3_socket_t g_socket_pool[SOCKET_POOL_SIZE];

/** @brief Socket pool lock */
static vos3_spinlock_t g_socket_lock = VOS3_SPINLOCK_INIT;

/* Forward declarations */
static void vos3_task_wake_on_socket(vos3_socket_t* sock);
static vos3_tcp_pcb_t* g_socket_tcp_pcb[SOCKET_POOL_SIZE];  /* FD -> PCB mapping */

/** @brief Socket statistics */
static struct {
    uint64_t    sockets_created;
    uint64_t    sockets_closed;
    uint64_t    bind_success;
    uint64_t    bind_privileged_denied;
    uint64_t    sendto_calls;
    uint64_t    recvfrom_calls;
    uint64_t    bytes_sent;
    uint64_t    bytes_received;
} g_socket_stats;

/* ============================================================================
 * FILE DESCRIPTOR MANAGEMENT
 * ============================================================================ */

/** @brief Starting FD for sockets (above stdio and regular files) */
#define SOCKET_FD_BASE      100

/**
 * @brief Allocate a socket from the pool
 * @return Socket pointer or NULL if pool exhausted
 */
static vos3_socket_t* socket_alloc(void)
{
    vos3_spinlock_lock(&g_socket_lock);

    for (size_t i = 0; i < SOCKET_POOL_SIZE; i++) {
        if (!g_socket_pool[i].allocated) {
            memset(&g_socket_pool[i], 0, sizeof(vos3_socket_t));
            g_socket_pool[i].allocated = 1;
            g_socket_pool[i].fd = (int32_t)(SOCKET_FD_BASE + i);
            g_socket_pool[i].state = VOS3_SOCK_UNBOUND;
            g_socket_stats.sockets_created++;

            vos3_spinlock_unlock(&g_socket_lock);
            return &g_socket_pool[i];
        }
    }

    vos3_spinlock_unlock(&g_socket_lock);
    return NULL;
}

/**
 * @brief Free a socket back to the pool
 */
static void socket_free(vos3_socket_t* sock)
{
    if (unlikely(sock == NULL)) {
        return;
    }

    /* Wake any task blocked on recv() before closing */
    if (sock->rx_waiting) {
        sock->rx_waiting = 0;
        vos3_task_wake_on_socket(sock);
    }

    vos3_spinlock_lock(&g_socket_lock);

    /* Clear receive queue */
    for (int i = 0; i < (int)VOS3_SOCKET_RX_QUEUE_MAX; i++) {
        if (sock->rx_queue[i].buf != NULL) {
            vos3_netbuf_free(sock->rx_queue[i].buf);
            sock->rx_queue[i].buf = NULL;
        }
    }

    /* Unbind port if bound */
    if (sock->state >= VOS3_SOCK_BOUND && sock->local_port != 0) {
        vos3_udp_unbind_port(sock->local_port);
    }

    sock->allocated = 0;
    sock->state = VOS3_SOCK_CLOSED;
    g_socket_stats.sockets_closed++;

    vos3_spinlock_unlock(&g_socket_lock);
}

/**
 * @brief Lookup socket by file descriptor
 * @return Socket pointer or NULL if not found
 */
static vos3_socket_t* socket_lookup(int fd)
{
    if (unlikely(fd < SOCKET_FD_BASE || fd >= SOCKET_FD_BASE + (int)SOCKET_POOL_SIZE)) {
        return NULL;
    }

    size_t idx = (size_t)(fd - SOCKET_FD_BASE);
    vos3_socket_t* sock = &g_socket_pool[idx];

    if (unlikely(!sock->allocated)) {
        return NULL;
    }

    return sock;
}

/* ============================================================================
 * POLL SUPPORT (Phase B)
 * ============================================================================ */

/**
 * @brief Check socket readability/writability for poll()
 * @param fd       Socket file descriptor
 * @param readable Set to 1 if socket has data to read
 * @param writable Set to 1 if socket can accept writes
 * @return 0 on success, -1 if fd is not a valid socket
 */
int vos3_socket_poll_check(int fd, int *readable, int *writable)
{
    vos3_socket_t* sock = socket_lookup(fd);
    if (sock == NULL) {
        return -1;
    }

    *readable = (sock->rx_count > 0) ? 1 : 0;
    *writable = 1; /* Sockets are always writable (non-blocking send) */

    return 0;
}

/* ============================================================================
 * CAPABILITY CHECKING (Privacy Shield)
 * ============================================================================ */

/**
 * @brief Check if current task has capability
 */
static int task_has_cap(uint32_t cap)
{
    /* TODO: Get from current task's capability set */
    /* For now, allow root (PID 0 or 1) full capabilities */
    vos3_task_t* current = vos3_task_current();
    if (current == NULL) {
        return 0;
    }

    /* Init process has all capabilities */
    if (current->pid <= 1) {
        return 1;
    }

    /* Check explicit capability - placeholder */
    (void)cap;
    return 0;
}

/**
 * @brief Get current task PID
 */
static uint32_t get_current_pid(void)
{
    vos3_task_t* current = vos3_task_current();
    return current ? current->pid : 0;
}

/* ============================================================================
 * BLOCKING RECV SUPPORT
 * ============================================================================ */

/**
 * @brief Wake a task blocked on recv() for this socket
 *
 * Called from the packet enqueue path when data arrives.
 */
static void vos3_task_wake_on_socket(vos3_socket_t* sock)
{
    vos3_task_t* waiter = (vos3_task_t*)sock->rx_waiter;
    if (waiter != NULL) {
        sock->rx_waiter = NULL;
        vos3_task_wake(waiter);
    }
}

/* ============================================================================
 * SOCKET SYSCALLS
 * ============================================================================ */

/**
 * @brief Initialize socket subsystem
 */
int vos3_socket_init(void)
{
    memset(g_socket_pool, 0, sizeof(g_socket_pool));
    memset(&g_socket_stats, 0, sizeof(g_socket_stats));
    g_socket_lock = VOS3_SPINLOCK_INIT;

    VOS3_INFO("[SOCKET] Socket subsystem initialized");
    VOS3_INFO("[SOCKET] Pool size: %u sockets", SOCKET_POOL_SIZE);
    VOS3_INFO("[SOCKET] FD range: %d-%d", SOCKET_FD_BASE,
              SOCKET_FD_BASE + SOCKET_POOL_SIZE - 1);
    VOS3_INFO("[SOCKET-SEC] Privileged ports (<1024): CAP_NET_ADMIN required");

    return 0;
}

/**
 * @brief Create a new socket (sys_socket)
 *
 * @param[in] domain    Address family (must be AF_INET)
 * @param[in] type      Socket type (must be SOCK_DGRAM for UDP)
 * @param[in] protocol  Protocol (0 or IPPROTO_UDP)
 * @return File descriptor on success, negative error code on failure
 */
int vos3_sys_socket(int domain, int type, int protocol)
{
    /* Validate domain */
    if (unlikely(domain != AF_INET)) {
        VOS3_DEBUG("[SOCKET] Unsupported domain: %d", domain);
        return -22;  /* -EINVAL */
    }

    /* Validate type - UDP (SOCK_DGRAM) or TCP (SOCK_STREAM) */
    if (unlikely(type != SOCK_DGRAM && type != SOCK_STREAM)) {
        VOS3_DEBUG("[SOCKET] Unsupported type: %d", type);
        return -22;  /* -EINVAL */
    }

    /* Validate protocol */
    int expected_proto = (type == SOCK_DGRAM) ? IPPROTO_UDP : IPPROTO_TCP;
    if (protocol != 0 && protocol != expected_proto) {
        VOS3_DEBUG("[SOCKET] Protocol mismatch: %d (expected %d)", protocol, expected_proto);
        return -22;  /* -EINVAL */
    }

    /* Allocate socket */
    vos3_socket_t* sock = socket_alloc();
    if (unlikely(sock == NULL)) {
        VOS3_WARN("[SOCKET] Socket pool exhausted");
        return -23;  /* -ENFILE */
    }

    /* Initialize socket */
    sock->domain = (uint16_t)domain;
    sock->type = (uint16_t)type;
    sock->protocol = (type == SOCK_DGRAM) ? IPPROTO_UDP : IPPROTO_TCP;
    sock->ops = (type == SOCK_DGRAM) ? &g_udp_socket_ops : &g_tcp_socket_ops;
    sock->state = VOS3_SOCK_UNBOUND;
    sock->owner_pid = get_current_pid();
    sock->rx_bufsize = VOS3_SOCKET_RXBUF_SIZE;
    sock->tx_bufsize = VOS3_SOCKET_RXBUF_SIZE;

    VOS3_DEBUG("[SOCKET] Created socket fd=%d type=%s pid=%u",
               sock->fd, (type == SOCK_DGRAM) ? "SOCK_DGRAM" : "SOCK_STREAM",
               sock->owner_pid);

    return sock->fd;
}

/**
 * @brief Bind socket to local address (sys_bind)
 *
 * Security: Ports < 1024 require VOS3_CAP_NET_ADMIN capability.
 *
 * @param[in] fd        Socket file descriptor
 * @param[in] addr      Local address (user pointer)
 * @param[in] addrlen   Address length
 * @return 0 on success, negative error code on failure
 */
int vos3_sys_bind(int fd, const struct sockaddr* addr, socklen_t addrlen)
{
    /* Lookup socket */
    vos3_socket_t* sock = socket_lookup(fd);
    if (unlikely(sock == NULL)) {
        return -9;  /* -EBADF */
    }

    /* Check already bound */
    if (unlikely(sock->state != VOS3_SOCK_UNBOUND)) {
        VOS3_DEBUG("[SOCKET] Socket fd=%d already bound", fd);
        return -22;  /* -EINVAL */
    }

    /* Validate address length */
    if (unlikely(addrlen < sizeof(struct sockaddr_in))) {
        return -22;  /* -EINVAL */
    }

    /* Copy address from user space */
    struct sockaddr_in local_addr;
    if (unlikely(!access_ok(addr, sizeof(struct sockaddr_in)))) {
        return -14;  /* -EFAULT */
    }
    if (copy_from_user(&local_addr, addr, sizeof(struct sockaddr_in)) != 0) {
        return -14;  /* -EFAULT */
    }

    /* Validate address family */
    if (unlikely(local_addr.sin_family != AF_INET)) {
        VOS3_DEBUG("[SOCKET] Invalid address family: %u", local_addr.sin_family);
        return -97;  /* -EAFNOSUPPORT */
    }

    /* Convert port from network order */
    uint16_t port = vos3_ntohs(local_addr.sin_port);

    /* SECURITY: Privileged port protection (Privacy Shield)
     *
     * Ports 0-1023 are privileged and require VOS3_CAP_NET_ADMIN.
     * This prevents unprivileged processes from impersonating
     * well-known services (DNS, DHCP, etc.).
     */
    if (unlikely(port < VOS3_PRIVILEGED_PORT_MAX && port != 0)) {
        if (!task_has_cap(VOS3_CAP_NET_ADMIN)) {
            VOS3_WARN("[SOCKET-SEC] Privileged port %u denied (no CAP_NET_ADMIN)",
                      port);
            g_socket_stats.bind_privileged_denied++;
            return -13;  /* -EACCES */
        }
        VOS3_DEBUG("[SOCKET-SEC] Privileged port %u allowed (CAP_NET_ADMIN)", port);
    }

    /* Auto-assign ephemeral port if port is 0 */
    if (port == 0) {
        /* Find free ephemeral port (49152-65535) */
        static uint16_t next_ephemeral = 49152;
        for (int i = 0; i < 1000; i++) {
            uint16_t try_port = next_ephemeral++;
            if (next_ephemeral < 49152U) {
                next_ephemeral = 49152U;
            }
            if (vos3_udp_lookup_port(try_port) == NULL) {
                port = try_port;
                break;
            }
        }
        if (port == 0) {
            VOS3_WARN("[SOCKET] No ephemeral ports available");
            return -98;  /* -EADDRINUSE */
        }
    }

    /* Register port binding */
    int ret = vos3_udp_bind_port(port, sock);
    if (unlikely(ret != 0)) {
        VOS3_DEBUG("[SOCKET] Port %u already in use", port);
        return -98;  /* -EADDRINUSE */
    }

    /* Update socket state */
    sock->local_ip = local_addr.sin_addr;
    sock->local_port = port;
    sock->state = VOS3_SOCK_BOUND;
    g_socket_stats.bind_success++;

    VOS3_INFO("[SOCKET] Bound fd=%d to port %u", fd, port);

    return 0;
}

/**
 * @brief Send datagram to specified address (sys_sendto)
 *
 * @param[in] fd        Socket file descriptor
 * @param[in] buf       Data buffer (user pointer)
 * @param[in] len       Data length
 * @param[in] flags     Send flags
 * @param[in] dest_addr Destination address (user pointer)
 * @param[in] addrlen   Address length
 * @return Bytes sent on success, negative error code on failure
 */
ssize_t vos3_sys_sendto(int fd, const void* buf, size_t len, int flags,
                        const struct sockaddr* dest_addr, socklen_t addrlen)
{
    (void)flags;  /* TODO: Handle flags */

    /* Lookup socket */
    vos3_socket_t* sock = socket_lookup(fd);
    if (unlikely(sock == NULL)) {
        return -9;  /* -EBADF */
    }

    /* Validate data buffer */
    if (unlikely(len > 0 && !access_ok(buf, len))) {
        return -14;  /* -EFAULT */
    }

    /* Limit payload size */
    if (unlikely(len > 65507)) {  /* Max UDP payload */
        return -90;  /* -EMSGSIZE */
    }

    /* Get destination address */
    struct sockaddr_in dest;
    if (dest_addr != NULL) {
        if (unlikely(addrlen < sizeof(struct sockaddr_in))) {
            return -22;  /* -EINVAL */
        }
        if (unlikely(!access_ok(dest_addr, sizeof(struct sockaddr_in)))) {
            return -14;  /* -EFAULT */
        }
        if (copy_from_user(&dest, dest_addr, sizeof(struct sockaddr_in)) != 0) {
            return -14;  /* -EFAULT */
        }
    } else if (sock->state == VOS3_SOCK_CONNECTED) {
        /* Use connected address */
        dest.sin_addr = sock->remote_ip;
        dest.sin_port = vos3_htons(sock->remote_port);
    } else {
        return -89;  /* -EDESTADDRREQ */
    }

    /* Auto-bind if not bound */
    if (sock->state == VOS3_SOCK_UNBOUND) {
        struct sockaddr_in any = {
            .sin_family = AF_INET,
            .sin_port = 0,
            .sin_addr = INADDR_ANY
        };
        int ret = vos3_sys_bind(fd, (struct sockaddr*)&any, sizeof(any));
        if (ret != 0) {
            return ret;
        }
    }

    /* Copy data from user space to kernel buffer */
    uint8_t kbuf[1500];  /* Stack buffer for small packets */
    size_t copy_len = (len > sizeof(kbuf)) ? sizeof(kbuf) : len;

    if (len > 0) {
        if (copy_from_user(kbuf, buf, copy_len) != 0) {
            return -14;  /* -EFAULT */
        }
    }

    /* Send via UDP */
    ssize_t sent = vos3_udp_send(sock, kbuf, copy_len,
                                  dest.sin_addr, vos3_ntohs(dest.sin_port));

    if (sent > 0) {
        g_socket_stats.sendto_calls++;
        g_socket_stats.bytes_sent += (uint64_t)sent;
        sock->tx_packets++;
        sock->tx_bytes += (uint64_t)sent;
    }

    return sent;
}

/**
 * @brief Receive datagram and source address (sys_recvfrom)
 *
 * Security: Buffer is sanitized (zeroed beyond data) per CVE-2026-23057.
 *
 * @param[in]  fd        Socket file descriptor
 * @param[out] buf       Data buffer (user pointer)
 * @param[in]  len       Buffer length
 * @param[in]  flags     Receive flags
 * @param[out] src_addr  Source address (user pointer, may be NULL)
 * @param[in,out] addrlen Address length (user pointer, may be NULL)
 * @return Bytes received on success, negative error code on failure
 */
ssize_t vos3_sys_recvfrom(int fd, void* buf, size_t len, int flags,
                          struct sockaddr* src_addr, socklen_t* addrlen)
{
    /* Lookup socket */
    vos3_socket_t* sock = socket_lookup(fd);
    if (unlikely(sock == NULL)) {
        return -9;  /* -EBADF */
    }

    /* Validate buffer */
    if (unlikely(len > 0 && !access_ok(buf, len))) {
        return -14;  /* -EFAULT */
    }

    /* Must be bound to receive */
    if (unlikely(sock->state < VOS3_SOCK_BOUND)) {
        return -22;  /* -EINVAL */
    }

    /* Check receive queue — block until data arrives */
    vos3_spinlock_lock(&g_socket_lock);

    while (sock->rx_count == 0) {
        vos3_spinlock_unlock(&g_socket_lock);

        /* Non-blocking mode */
        if (flags & MSG_DONTWAIT) {
            return -11;  /* -EAGAIN */
        }

        /* Block: mark socket as waiting, sleep, re-check after wakeup */
        vos3_task_t* self = vos3_task_current();
        if (self == NULL) {
            return -11;  /* -EAGAIN: no task context */
        }
        sock->rx_waiting = 1;
        sock->rx_waiter = self;
        self->state = VOS3_TASK_SLEEPING;
        vos3_sched_yield();
        /* After wakeup, re-check condition (spurious wakeup protection) */
        sock->rx_waiting = 0;
        sock->rx_waiter = NULL;

        /* If socket was closed while we slept, bail out */
        if (sock->state == VOS3_SOCK_CLOSED || !sock->allocated) {
            return -9;  /* -EBADF */
        }

        vos3_spinlock_lock(&g_socket_lock);
    }

    /* Dequeue packet */
    vos3_rx_packet_t* pkt = &sock->rx_queue[sock->rx_head];
    vos3_netbuf_t* nbuf = pkt->buf;
    struct sockaddr_in from = pkt->from_addr;
    size_t data_len = pkt->data_len;

    /* Clear queue entry */
    pkt->buf = NULL;
    pkt->valid = 0;
    sock->rx_head = (sock->rx_head + 1) % VOS3_SOCKET_RX_QUEUE_MAX;
    sock->rx_count--;

    vos3_spinlock_unlock(&g_socket_lock);

    /* Calculate copy length */
    size_t copy_len = (data_len > len) ? len : data_len;

    /* SECURITY: Buffer Sanitization (CVE-2026-23057)
     *
     * Zero the entire user buffer before copying data.
     * This prevents information leaks from previous buffer contents.
     */
    uint8_t zero_buf[1500];
    memset(zero_buf, 0, sizeof(zero_buf));
    size_t zero_len = (len > sizeof(zero_buf)) ? sizeof(zero_buf) : len;
    if (copy_to_user(buf, zero_buf, zero_len) != 0) {
        vos3_netbuf_free(nbuf);
        return -14;  /* -EFAULT */
    }

    /* Copy data to user space */
    /* Data starts after UDP header in the netbuf */
    uint8_t* data_ptr = nbuf->data + nbuf->data_offset + 8;  /* +8 for UDP header */
    if (copy_to_user(buf, data_ptr, copy_len) != 0) {
        vos3_netbuf_free(nbuf);
        return -14;  /* -EFAULT */
    }

    /* Copy source address if requested */
    if (src_addr != NULL && addrlen != NULL) {
        socklen_t user_addrlen;
        if (copy_from_user(&user_addrlen, addrlen, sizeof(socklen_t)) != 0) {
            vos3_netbuf_free(nbuf);
            return -14;  /* -EFAULT */
        }

        size_t addr_copy_len = (user_addrlen > sizeof(from)) ?
                                sizeof(from) : user_addrlen;
        if (!access_ok(src_addr, addr_copy_len)) {
            vos3_netbuf_free(nbuf);
            return -14;  /* -EFAULT */
        }
        if (copy_to_user(src_addr, &from, addr_copy_len) != 0) {
            vos3_netbuf_free(nbuf);
            return -14;  /* -EFAULT */
        }

        /* Update addrlen */
        socklen_t actual_len = sizeof(struct sockaddr_in);
        if (copy_to_user(addrlen, &actual_len, sizeof(socklen_t)) != 0) {
            vos3_netbuf_free(nbuf);
            return -14;  /* -EFAULT */
        }
    }

    /* Free network buffer */
    vos3_netbuf_free(nbuf);

    /* Update statistics */
    g_socket_stats.recvfrom_calls++;
    g_socket_stats.bytes_received += copy_len;
    sock->rx_packets++;
    sock->rx_bytes += copy_len;

    /* Return actual data length (may be truncated if MSG_TRUNC not set) */
    if ((flags & MSG_TRUNC) && data_len > len) {
        return (ssize_t)data_len;  /* Return original length */
    }

    return (ssize_t)copy_len;
}

/**
 * @brief Close socket (sys_close for sockets)
 */
int vos3_sys_closesocket(int fd)
{
    vos3_socket_t* sock = socket_lookup(fd);
    if (unlikely(sock == NULL)) {
        return -9;  /* -EBADF */
    }

    VOS3_DEBUG("[SOCKET] Closing fd=%d port=%u", fd, sock->local_port);

    /* Protocol-specific cleanup via ops vtable */
    if (sock->ops != NULL && sock->ops->close != NULL) {
        sock->ops->close(sock);
    }

    socket_free(sock);

    return 0;
}

/**
 * @brief Enqueue received packet to socket (called by UDP layer)
 */
int vos3_socket_enqueue_packet(vos3_socket_t* sock, vos3_netbuf_t* buf,
                                const struct sockaddr_in* from, size_t data_len)
{
    if (unlikely(sock == NULL || buf == NULL)) {
        return -1;
    }

    vos3_spinlock_lock(&g_socket_lock);

    /* Check queue full */
    if (sock->rx_count >= VOS3_SOCKET_RX_QUEUE_MAX) {
        sock->rx_overflow++;
        vos3_spinlock_unlock(&g_socket_lock);
        VOS3_DEBUG("[SOCKET] RX queue overflow fd=%d", sock->fd);
        return -1;
    }

    /* Enqueue packet */
    vos3_rx_packet_t* pkt = &sock->rx_queue[sock->rx_tail];
    pkt->buf = buf;
    pkt->from_addr = *from;
    pkt->data_len = (uint16_t)data_len;
    pkt->valid = 1;

    sock->rx_tail = (sock->rx_tail + 1) % VOS3_SOCKET_RX_QUEUE_MAX;
    sock->rx_count++;

    /* Wake any task blocked on recv() for this socket */
    int needs_wake = sock->rx_waiting;

    vos3_spinlock_unlock(&g_socket_lock);

    if (needs_wake) {
        sock->rx_waiting = 0;
        vos3_task_wake_on_socket(sock);
    }

    return 0;
}

/**
 * @brief Print socket statistics
 */
void vos3_socket_print_stats(void)
{
    VOS3_INFO("[SOCKET] Statistics:");
    VOS3_INFO("  Sockets created:   %llu",
              (unsigned long long)g_socket_stats.sockets_created);
    VOS3_INFO("  Sockets closed:    %llu",
              (unsigned long long)g_socket_stats.sockets_closed);
    VOS3_INFO("  Binds succeeded:   %llu",
              (unsigned long long)g_socket_stats.bind_success);
    VOS3_INFO("  Privileged denied: %llu",
              (unsigned long long)g_socket_stats.bind_privileged_denied);
    VOS3_INFO("  sendto() calls:    %llu",
              (unsigned long long)g_socket_stats.sendto_calls);
    VOS3_INFO("  recvfrom() calls:  %llu",
              (unsigned long long)g_socket_stats.recvfrom_calls);
    VOS3_INFO("  Bytes sent:        %llu",
              (unsigned long long)g_socket_stats.bytes_sent);
    VOS3_INFO("  Bytes received:    %llu",
              (unsigned long long)g_socket_stats.bytes_received);
}

/* ============================================================================
 * SOCKET OPS VTABLE IMPLEMENTATIONS
 * ============================================================================ */

/* --- UDP protocol ops --- */

static ssize_t udp_ops_send(vos3_socket_t* sock,
                             const void* kbuf, size_t len, int flags)
{
    (void)flags;
    if (sock->state != VOS3_SOCK_CONNECTED) {
        return -89;  /* -EDESTADDRREQ */
    }
    return vos3_udp_send(sock, kbuf, len, sock->remote_ip, sock->remote_port);
}

static ssize_t udp_ops_recv(vos3_socket_t* sock,
                             void* kbuf, size_t len, int flags)
{
    (void)sock; (void)kbuf; (void)len; (void)flags;
    /* UDP recv goes through recvfrom path; shouldn't reach here */
    return -95;  /* -EOPNOTSUPP */
}

static int udp_ops_connect(vos3_socket_t* sock, uint32_t ip, uint16_t port)
{
    sock->remote_ip = ip;
    sock->remote_port = port;
    sock->state = VOS3_SOCK_CONNECTED;
    return 0;
}

static int udp_ops_listen(vos3_socket_t* sock, int backlog)
{
    (void)sock; (void)backlog;
    return -95;  /* -EOPNOTSUPP */
}

static int udp_ops_accept(vos3_socket_t* sock,
                           struct sockaddr* addr, socklen_t* addrlen)
{
    (void)sock; (void)addr; (void)addrlen;
    return -95;  /* -EOPNOTSUPP */
}

static int udp_ops_close(vos3_socket_t* sock)
{
    (void)sock;
    return 0;  /* No protocol-specific cleanup for UDP */
}

const vos3_socket_ops_t g_udp_socket_ops = {
    .send    = udp_ops_send,
    .recv    = udp_ops_recv,
    .connect = udp_ops_connect,
    .listen  = udp_ops_listen,
    .accept  = udp_ops_accept,
    .close   = udp_ops_close,
};

/* --- TCP protocol ops --- */

static ssize_t tcp_ops_send(vos3_socket_t* sock,
                             const void* kbuf, size_t len, int flags)
{
    (void)flags;
    size_t idx = (size_t)(sock->fd - SOCKET_FD_BASE);
    if (idx >= SOCKET_POOL_SIZE || g_socket_tcp_pcb[idx] == NULL) {
        return -107;  /* -ENOTCONN */
    }
    return vos3_tcp_send(g_socket_tcp_pcb[idx], kbuf, len);
}

static ssize_t tcp_ops_recv(vos3_socket_t* sock,
                             void* kbuf, size_t len, int flags)
{
    (void)flags;
    size_t idx = (size_t)(sock->fd - SOCKET_FD_BASE);
    if (idx >= SOCKET_POOL_SIZE || g_socket_tcp_pcb[idx] == NULL) {
        return -107;  /* -ENOTCONN */
    }
    return vos3_tcp_recv(g_socket_tcp_pcb[idx], kbuf, len);
}

static int tcp_ops_connect(vos3_socket_t* sock, uint32_t ip, uint16_t port)
{
    size_t idx = (size_t)(sock->fd - SOCKET_FD_BASE);
    if (idx >= SOCKET_POOL_SIZE) {
        return -9;  /* -EBADF */
    }

    vos3_tcp_pcb_t* pcb = g_socket_tcp_pcb[idx];
    if (pcb == NULL) {
        pcb = vos3_tcp_pcb_alloc();
        if (pcb == NULL) {
            return -12;  /* -ENOMEM */
        }
        pcb->local_ip = sock->local_ip;
        pcb->local_port = sock->local_port;
        pcb->socket = sock;
        g_socket_tcp_pcb[idx] = pcb;
    }

    return vos3_tcp_connect(pcb, ip, port);
}

static int tcp_ops_listen(vos3_socket_t* sock, int backlog)
{
    size_t idx = (size_t)(sock->fd - SOCKET_FD_BASE);
    if (idx >= SOCKET_POOL_SIZE) {
        return -9;  /* -EBADF */
    }

    vos3_tcp_pcb_t* pcb = g_socket_tcp_pcb[idx];
    if (pcb == NULL) {
        pcb = vos3_tcp_pcb_alloc();
        if (pcb == NULL) {
            return -12;  /* -ENOMEM */
        }
        pcb->local_ip = sock->local_ip;
        pcb->local_port = sock->local_port;
        pcb->socket = sock;
        g_socket_tcp_pcb[idx] = pcb;
    }

    return vos3_tcp_listen(pcb, backlog);
}

static int tcp_ops_accept(vos3_socket_t* sock,
                           struct sockaddr* addr, socklen_t* addrlen)
{
    size_t idx = (size_t)(sock->fd - SOCKET_FD_BASE);
    if (idx >= SOCKET_POOL_SIZE || g_socket_tcp_pcb[idx] == NULL) {
        return -9;  /* -EBADF */
    }

    vos3_tcp_pcb_t* listen_pcb = g_socket_tcp_pcb[idx];

    vos3_tcp_pcb_t* new_pcb = vos3_tcp_accept(listen_pcb);
    if (new_pcb == NULL) {
        return -11;  /* -EAGAIN */
    }

    /* Allocate new socket for accepted connection */
    vos3_socket_t* new_sock = socket_alloc();
    if (new_sock == NULL) {
        vos3_tcp_pcb_free(new_pcb);
        return -23;  /* -ENFILE */
    }

    new_sock->domain = AF_INET;
    new_sock->type = SOCK_STREAM;
    new_sock->protocol = IPPROTO_TCP;
    new_sock->ops = &g_tcp_socket_ops;
    new_sock->state = VOS3_SOCK_CONNECTED;
    new_sock->local_ip = new_pcb->local_ip;
    new_sock->local_port = new_pcb->local_port;
    new_sock->remote_ip = new_pcb->remote_ip;
    new_sock->remote_port = new_pcb->remote_port;
    new_sock->owner_pid = get_current_pid();

    size_t new_idx = (size_t)(new_sock->fd - SOCKET_FD_BASE);
    if (new_idx < SOCKET_POOL_SIZE) {
        g_socket_tcp_pcb[new_idx] = new_pcb;
        new_pcb->socket = new_sock;
    }

    /* Copy remote address if requested */
    if (addr != NULL && addrlen != NULL) {
        struct sockaddr_in remote;
        remote.sin_family = AF_INET;
        remote.sin_port = vos3_htons(new_pcb->remote_port);
        remote.sin_addr = new_pcb->remote_ip;
        memset(remote.sin_zero, 0, sizeof(remote.sin_zero));

        socklen_t user_len;
        if (copy_from_user(&user_len, addrlen, sizeof(socklen_t)) == 0) {
            size_t copy_len = (user_len > sizeof(remote)) ? sizeof(remote) : user_len;
            if (access_ok(addr, copy_len)) {
                copy_to_user(addr, &remote, copy_len);
            }
            socklen_t actual_len = sizeof(struct sockaddr_in);
            copy_to_user(addrlen, &actual_len, sizeof(socklen_t));
        }
    }

    VOS3_INFO("[SOCKET] Accepted connection: fd=%d from %u.%u.%u.%u:%u",
              new_sock->fd,
              new_pcb->remote_ip & 0xFF, (new_pcb->remote_ip >> 8) & 0xFF,
              (new_pcb->remote_ip >> 16) & 0xFF, (new_pcb->remote_ip >> 24) & 0xFF,
              new_pcb->remote_port);

    return new_sock->fd;
}

static int tcp_ops_close(vos3_socket_t* sock)
{
    size_t idx = (size_t)(sock->fd - SOCKET_FD_BASE);
    if (idx < SOCKET_POOL_SIZE && g_socket_tcp_pcb[idx] != NULL) {
        /* Gracefully close TCP connection (sends FIN) before freeing PCB */
        vos3_tcp_close(g_socket_tcp_pcb[idx]);
        vos3_tcp_pcb_free(g_socket_tcp_pcb[idx]);
        g_socket_tcp_pcb[idx] = NULL;
    }
    return 0;
}

const vos3_socket_ops_t g_tcp_socket_ops = {
    .send    = tcp_ops_send,
    .recv    = tcp_ops_recv,
    .connect = tcp_ops_connect,
    .listen  = tcp_ops_listen,
    .accept  = tcp_ops_accept,
    .close   = tcp_ops_close,
};

/* ============================================================================
 * TCP SOCKET SYSCALLS (Day 6)
 * ============================================================================ */

/**
 * @brief Listen for connections (TCP only)
 */
int vos3_sys_listen(int fd, int backlog)
{
    vos3_socket_t* sock = socket_lookup(fd);
    if (unlikely(sock == NULL)) {
        return -9;  /* -EBADF */
    }

    /* Must be bound */
    if (unlikely(sock->state < VOS3_SOCK_BOUND)) {
        return -22;  /* -EINVAL */
    }

    /* Dispatch through ops vtable */
    if (sock->ops == NULL || sock->ops->listen == NULL) {
        return -95;  /* -EOPNOTSUPP */
    }

    int ret = sock->ops->listen(sock, backlog);
    if (ret == 0) {
        sock->state = VOS3_SOCK_LISTENING;
        VOS3_INFO("[SOCKET] Listening on port %u (backlog=%d)", sock->local_port, backlog);
    }

    return ret;
}

/**
 * @brief Accept incoming connection (TCP only)
 */
int vos3_sys_accept(int fd, struct sockaddr* addr, socklen_t* addrlen)
{
    vos3_socket_t* sock = socket_lookup(fd);
    if (unlikely(sock == NULL)) {
        return -9;  /* -EBADF */
    }

    /* Must be listening */
    if (unlikely(sock->state != VOS3_SOCK_LISTENING)) {
        return -22;  /* -EINVAL */
    }

    /* Dispatch through ops vtable */
    if (sock->ops == NULL || sock->ops->accept == NULL) {
        return -95;  /* -EOPNOTSUPP */
    }

    return sock->ops->accept(sock, addr, addrlen);
}

/**
 * @brief Connect to remote address (TCP)
 */
int vos3_sys_connect(int fd, const struct sockaddr* addr, socklen_t addrlen)
{
    vos3_socket_t* sock = socket_lookup(fd);
    if (unlikely(sock == NULL)) {
        return -9;  /* -EBADF */
    }

    /* Validate address */
    if (unlikely(addrlen < sizeof(struct sockaddr_in))) {
        return -22;  /* -EINVAL */
    }

    struct sockaddr_in remote;
    if (!access_ok(addr, sizeof(struct sockaddr_in))) {
        return -14;  /* -EFAULT */
    }
    if (copy_from_user(&remote, addr, sizeof(struct sockaddr_in)) != 0) {
        return -14;  /* -EFAULT */
    }

    if (remote.sin_family != AF_INET) {
        return -97;  /* -EAFNOSUPPORT */
    }

    /* Auto-bind if not bound (needed for TCP) */
    if (sock->state == VOS3_SOCK_UNBOUND && sock->type == SOCK_STREAM) {
        struct sockaddr_in any = {
            .sin_family = AF_INET,
            .sin_port = 0,
            .sin_addr = INADDR_ANY
        };
        int ret = vos3_sys_bind(fd, (struct sockaddr*)&any, sizeof(any));
        if (ret != 0) {
            return ret;
        }
    }

    /* Dispatch through ops vtable */
    if (sock->ops == NULL || sock->ops->connect == NULL) {
        return -95;  /* -EOPNOTSUPP */
    }

    uint16_t port = vos3_ntohs(remote.sin_port);
    int ret = sock->ops->connect(sock, remote.sin_addr, port);
    if (ret == 0) {
        sock->remote_ip = remote.sin_addr;
        sock->remote_port = port;
    }

    VOS3_DEBUG("[SOCKET] connect fd=%d to %u.%u.%u.%u:%u ret=%d",
               fd, remote.sin_addr & 0xFF, (remote.sin_addr >> 8) & 0xFF,
               (remote.sin_addr >> 16) & 0xFF, (remote.sin_addr >> 24) & 0xFF,
               port, ret);

    return ret;
}

/**
 * @brief Send data on connected socket
 */
ssize_t vos3_sys_send(int fd, const void* buf, size_t len, int flags)
{
    vos3_socket_t* sock = socket_lookup(fd);
    if (unlikely(sock == NULL)) {
        return -9;  /* -EBADF */
    }

    /* Validate buffer */
    if (unlikely(len > 0 && !access_ok(buf, len))) {
        return -14;  /* -EFAULT */
    }

    /* For UDP connected sockets, redirect through sendto */
    if (sock->type == SOCK_DGRAM) {
        if (sock->state != VOS3_SOCK_CONNECTED) {
            return -89;  /* -EDESTADDRREQ */
        }
        struct sockaddr_in dest = {
            .sin_family = AF_INET,
            .sin_port = vos3_htons(sock->remote_port),
            .sin_addr = sock->remote_ip
        };
        return vos3_sys_sendto(fd, buf, len, flags, (struct sockaddr*)&dest, sizeof(dest));
    }

    /* Dispatch through ops vtable */
    if (sock->ops == NULL || sock->ops->send == NULL) {
        return -95;  /* -EOPNOTSUPP */
    }

    /* Copy data from user space */
    uint8_t kbuf[1460];  /* MSS-sized buffer */
    size_t copy_len = (len > sizeof(kbuf)) ? sizeof(kbuf) : len;

    if (len > 0) {
        if (copy_from_user(kbuf, buf, copy_len) != 0) {
            return -14;  /* -EFAULT */
        }
    }

    ssize_t sent = sock->ops->send(sock, kbuf, copy_len, flags);
    if (sent > 0) {
        sock->tx_packets++;
        sock->tx_bytes += (uint64_t)sent;
    }

    return sent;
}

/**
 * @brief Receive data from connected socket
 */
ssize_t vos3_sys_recv(int fd, void* buf, size_t len, int flags)
{
    vos3_socket_t* sock = socket_lookup(fd);
    if (unlikely(sock == NULL)) {
        return -9;  /* -EBADF */
    }

    /* Validate buffer */
    if (unlikely(len > 0 && !access_ok(buf, len))) {
        return -14;  /* -EFAULT */
    }

    /* For UDP, use recvfrom */
    if (sock->type == SOCK_DGRAM) {
        return vos3_sys_recvfrom(fd, buf, len, flags, NULL, NULL);
    }

    /* Dispatch through ops vtable */
    if (sock->ops == NULL || sock->ops->recv == NULL) {
        return -95;  /* -EOPNOTSUPP */
    }

    /* Receive into kernel buffer */
    uint8_t kbuf[1460];
    size_t recv_len = (len > sizeof(kbuf)) ? sizeof(kbuf) : len;

    ssize_t received = sock->ops->recv(sock, kbuf, recv_len, flags);
    if (received > 0) {
        if (copy_to_user(buf, kbuf, (size_t)received) != 0) {
            return -14;  /* -EFAULT */
        }
        sock->rx_packets++;
        sock->rx_bytes += (uint64_t)received;
    }

    return received;
}

/* ============================================================================
 * SYSCALL WRAPPERS (for syscall table)
 * ============================================================================ */

#include "../../include/vos/syscall.h"

/**
 * @brief Syscall wrapper for sys_socket
 */
static int64_t syscall_socket(vos3_syscall_frame_t* frame)
{
    int domain = (int)frame->rdi;
    int type = (int)frame->rsi;
    int protocol = (int)frame->rdx;
    return vos3_sys_socket(domain, type, protocol);
}

/**
 * @brief Syscall wrapper for sys_bind
 */
static int64_t syscall_bind(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    const struct sockaddr* addr = (const struct sockaddr*)frame->rsi;
    socklen_t addrlen = (socklen_t)frame->rdx;
    return vos3_sys_bind(fd, addr, addrlen);
}

/**
 * @brief Syscall wrapper for sys_sendto
 */
static int64_t syscall_sendto(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    const void* buf = (const void*)frame->rsi;
    size_t len = (size_t)frame->rdx;
    int flags = (int)frame->r10;
    const struct sockaddr* dest_addr = (const struct sockaddr*)frame->r8;
    socklen_t addrlen = (socklen_t)frame->r9;
    return vos3_sys_sendto(fd, buf, len, flags, dest_addr, addrlen);
}

/**
 * @brief Syscall wrapper for sys_recvfrom
 */
static int64_t syscall_recvfrom(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    void* buf = (void*)frame->rsi;
    size_t len = (size_t)frame->rdx;
    int flags = (int)frame->r10;
    struct sockaddr* src_addr = (struct sockaddr*)frame->r8;
    socklen_t* addrlen = (socklen_t*)frame->r9;
    return vos3_sys_recvfrom(fd, buf, len, flags, src_addr, addrlen);
}

/**
 * @brief Syscall wrapper for sys_listen
 */
static int64_t syscall_listen(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    int backlog = (int)frame->rsi;
    return vos3_sys_listen(fd, backlog);
}

/**
 * @brief Syscall wrapper for sys_accept
 */
static int64_t syscall_accept(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    struct sockaddr* addr = (struct sockaddr*)frame->rsi;
    socklen_t* addrlen = (socklen_t*)frame->rdx;
    return vos3_sys_accept(fd, addr, addrlen);
}

/**
 * @brief Syscall wrapper for sys_connect
 */
static int64_t syscall_connect(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    const struct sockaddr* addr = (const struct sockaddr*)frame->rsi;
    socklen_t addrlen = (socklen_t)frame->rdx;
    return vos3_sys_connect(fd, addr, addrlen);
}

/**
 * @brief Syscall wrapper for sys_send
 */
static int64_t syscall_send(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    const void* buf = (const void*)frame->rsi;
    size_t len = (size_t)frame->rdx;
    int flags = (int)frame->r10;
    return vos3_sys_send(fd, buf, len, flags);
}

/**
 * @brief Syscall wrapper for sys_recv
 */
static int64_t syscall_recv(vos3_syscall_frame_t* frame)
{
    int fd = (int)frame->rdi;
    void* buf = (void*)frame->rsi;
    size_t len = (size_t)frame->rdx;
    int flags = (int)frame->r10;
    return vos3_sys_recv(fd, buf, len, flags);
}

/* ============================================================================
 * SETSOCKOPT / GETSOCKOPT (Phase G)
 * ============================================================================ */

/** @brief Socket flag bits for SO_REUSEADDR / SO_REUSEPORT */
#define VOS3_SOCK_F_REUSEADDR   0x0001U
#define VOS3_SOCK_F_REUSEPORT   0x0002U

/* Socket option constants (Linux values) */
#ifndef SO_REUSEPORT
#define SO_REUSEPORT    15
#endif

/**
 * @brief Set socket option (sys_setsockopt)
 */
static int64_t syscall_setsockopt(vos3_syscall_frame_t* frame)
{
    int fd        = (int)frame->rdi;
    int level     = (int)frame->rsi;
    int optname   = (int)frame->rdx;
    const void* optval = (const void*)frame->r10;
    socklen_t optlen   = (socklen_t)frame->r8;

    (void)optval;
    (void)optlen;

    vos3_socket_t* sock = socket_lookup(fd);
    if (sock == NULL) {
        return -9;   /* -EBADF */
    }

    if (level != SOL_SOCKET) {
        /* For non-SOL_SOCKET levels (e.g. IPPROTO_TCP), accept silently */
        return 0;
    }

    switch (optname) {
        case SO_REUSEADDR:
            sock->flags |= VOS3_SOCK_F_REUSEADDR;
            VOS3_DEBUG("[SOCKET] setsockopt fd=%d SO_REUSEADDR", fd);
            return 0;

        case SO_REUSEPORT:
            sock->flags |= VOS3_SOCK_F_REUSEPORT;
            VOS3_DEBUG("[SOCKET] setsockopt fd=%d SO_REUSEPORT", fd);
            return 0;

        case SO_RCVBUF:
            /* Accept and ignore — we use fixed-size queues */
            VOS3_DEBUG("[SOCKET] setsockopt fd=%d SO_RCVBUF (ignored)", fd);
            return 0;

        case SO_SNDBUF:
            /* Accept and ignore */
            VOS3_DEBUG("[SOCKET] setsockopt fd=%d SO_SNDBUF (ignored)", fd);
            return 0;

        case SO_BROADCAST:
            /* Accept and ignore */
            VOS3_DEBUG("[SOCKET] setsockopt fd=%d SO_BROADCAST (ignored)", fd);
            return 0;

        default:
            /* Unknown option — accept silently for compatibility */
            VOS3_DEBUG("[SOCKET] setsockopt fd=%d unknown opt=%d (ignored)", fd, optname);
            return 0;
    }
}

/**
 * @brief Get socket option (sys_getsockopt)
 */
static int64_t syscall_getsockopt(vos3_syscall_frame_t* frame)
{
    int fd        = (int)frame->rdi;
    int level     = (int)frame->rsi;
    int optname   = (int)frame->rdx;
    void* optval  = (void*)frame->r10;
    socklen_t* optlen = (socklen_t*)frame->r8;

    (void)level;

    vos3_socket_t* sock = socket_lookup(fd);
    if (sock == NULL) {
        return -9;   /* -EBADF */
    }

    int value = 0;

    switch (optname) {
        case SO_REUSEADDR:
            value = (sock->flags & VOS3_SOCK_F_REUSEADDR) ? 1 : 0;
            break;
        case SO_REUSEPORT:
            value = (sock->flags & VOS3_SOCK_F_REUSEPORT) ? 1 : 0;
            break;
        case SO_RCVBUF:
            value = (int)sock->rx_bufsize;
            break;
        case SO_SNDBUF:
            value = (int)sock->tx_bufsize;
            break;
        default:
            value = 0;
            break;
    }

    /* Copy value to user */
    if (optval != NULL && optlen != NULL) {
        if (access_ok(optval, sizeof(int)) && access_ok(optlen, sizeof(socklen_t))) {
            copy_to_user(optval, &value, sizeof(int));
            socklen_t len = sizeof(int);
            copy_to_user(optlen, &len, sizeof(socklen_t));
        }
    }

    return 0;
}

/**
 * @brief Register socket syscalls
 */
void vos3_register_socket_syscalls(void)
{
    /* Initialize TCP PCB mapping */
    memset(g_socket_tcp_pcb, 0, sizeof(g_socket_tcp_pcb));

    /* VOS3 custom syscall numbers (150-162) */
    vos3_syscall_register(VOS3_SYS_SOCKET, syscall_socket);
    vos3_syscall_register(VOS3_SYS_BIND, syscall_bind);
    vos3_syscall_register(VOS3_SYS_SENDTO, syscall_sendto);
    vos3_syscall_register(VOS3_SYS_RECVFROM, syscall_recvfrom);
    vos3_syscall_register(VOS3_SYS_LISTEN, syscall_listen);
    vos3_syscall_register(VOS3_SYS_ACCEPT, syscall_accept);
    vos3_syscall_register(VOS3_SYS_CONNECT, syscall_connect);
    vos3_syscall_register(VOS3_SYS_SEND, syscall_send);
    vos3_syscall_register(VOS3_SYS_RECV, syscall_recv);

    /* Linux x86-64 ABI aliases (for musl/glibc compatibility) */
    vos3_syscall_register(41, syscall_socket);     /* __NR_socket */
    vos3_syscall_register(42, syscall_connect);    /* __NR_connect */
    vos3_syscall_register(43, syscall_accept);     /* __NR_accept */
    vos3_syscall_register(44, syscall_sendto);     /* __NR_sendto */
    vos3_syscall_register(45, syscall_recvfrom);   /* __NR_recvfrom */
    vos3_syscall_register(46, syscall_sendto);     /* __NR_sendmsg (→sendto) */
    vos3_syscall_register(47, syscall_recvfrom);   /* __NR_recvmsg (→recvfrom) */
    vos3_syscall_register(49, syscall_bind);       /* __NR_bind */
    vos3_syscall_register(50, syscall_listen);     /* __NR_listen */

    /* setsockopt / getsockopt (Phase G) */
    vos3_syscall_register(VOS3_SYS_SETSOCKOPT, syscall_setsockopt);
    vos3_syscall_register(VOS3_SYS_GETSOCKOPT, syscall_getsockopt);
    vos3_syscall_register(54, syscall_setsockopt); /* __NR_setsockopt (Linux) */
    vos3_syscall_register(55, syscall_getsockopt); /* __NR_getsockopt (Linux) */

    VOS3_INFO("[SOCKET] Registered socket syscalls (VOS3: 150-162, Linux: 41-55)");
    VOS3_INFO("[SOCKET] Protocol dispatch via socket_ops vtable (UDP + TCP)");
}
