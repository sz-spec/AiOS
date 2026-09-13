/**
 * @file test_epoll.c
 * @brief VOS3 epoll / select / eventfd system call tests (Task 1.3)
 *
 * @details Tests for:
 *   1. eventfd: create, write, read counter
 *   2. epoll_create1 + epoll_ctl + epoll_wait on eventfd
 *   3. epoll on pipe: write to pipe, epoll detects EPOLLIN
 *   4. epoll_ctl DEL: verify event no longer reported
 *   5. select() on pipe: write to pipe, select detects readable fd
 *   6. select() with zero timeout (non-blocking): returns 0 when no data
 *
 * @version 1.0.0
 * @date 2026-03-14
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"
#include <stdint.h>

/* ============================================================================
 * TEST FRAMEWORK
 * ============================================================================ */

static int g_tests_passed = 0;
static int g_tests_failed = 0;

#define TEST_PASS(name) do { \
    printf("  [PASS] %s\n", (name)); \
    g_tests_passed++; \
} while(0)

#define TEST_FAIL(name, ...) do { \
    printf("  [FAIL] " name "\n", ##__VA_ARGS__); \
    g_tests_failed++; \
} while(0)

/* ============================================================================
 * SYSCALL NUMBERS (Task 1.3)
 * ============================================================================ */

#define SYS_SELECT        23
#define SYS_EPOLL_CREATE  213
#define SYS_EPOLL_WAIT    232
#define SYS_EPOLL_CTL     233
#define SYS_EVENTFD2      284
#define SYS_EPOLL_CREATE1 291

/* Already in syscall.h: SYS_PIPE, SYS_WRITE, SYS_READ, SYS_CLOSE */

/* ============================================================================
 * EPOLL CONSTANTS
 * ============================================================================ */

#define EPOLLIN    0x00000001U
#define EPOLLOUT   0x00000004U
#define EPOLLERR   0x00000008U
#define EPOLLHUP   0x00000010U

#define EPOLL_CTL_ADD  1
#define EPOLL_CTL_DEL  2
#define EPOLL_CTL_MOD  3

#define EPOLL_CLOEXEC  0x80000

/* epoll_event: must match kernel vos3_epoll_event_t (packed) */
typedef struct __attribute__((packed)) {
    uint32_t events;
    uint64_t data_u64;
} epoll_event_t;

/* ============================================================================
 * SELECT fd_set: 1024 fds in 16 uint64_t words
 * ============================================================================ */

#define FD_SETSIZE    1024
#define NFDBITS       64
#define NWORDS        (FD_SETSIZE / NFDBITS)

typedef struct {
    uint64_t bits[NWORDS];
} fd_set_t;

static void FD_ZERO(fd_set_t* fds)
{
    for (int i = 0; i < NWORDS; i++) fds->bits[i] = 0;
}

static void FD_SET_BIT(int fd, fd_set_t* fds)
{
    if (fd >= 0 && fd < FD_SETSIZE)
        fds->bits[fd / NFDBITS] |= (1ULL << (fd % NFDBITS));
}

static int FD_ISSET_BIT(int fd, const fd_set_t* fds)
{
    if (fd < 0 || fd >= FD_SETSIZE) return 0;
    return (int)((fds->bits[fd / NFDBITS] >> (fd % NFDBITS)) & 1ULL);
}

/* struct timeval for select */
typedef struct {
    long tv_sec;
    long tv_usec;
} timeval_t;

/* ============================================================================
 * TEST 1: eventfd — create, write, read counter
 * ============================================================================ */

static void test_eventfd_basic(void)
{
    printf("\n--- Test: eventfd_basic ---\n");

    /* Create eventfd with initval=0 */
    long efd = syscall2(SYS_EVENTFD2, 0, 0);
    if (efd < 0) {
        TEST_FAIL("eventfd_basic: eventfd2() returned %ld", efd);
        return;
    }
    printf("  eventfd2() = fd %ld\n", efd);

    /* Write value 42 */
    uint64_t wval = 42;
    long wr = syscall3(SYS_WRITE, efd, (long)&wval, 8);
    if (wr != 8) {
        TEST_FAIL("eventfd_basic: write returned %ld (expected 8)", wr);
        syscall1(SYS_CLOSE, efd);
        return;
    }

    /* Read back — should get 42, counter reset to 0 */
    uint64_t rval = 0;
    long rd = syscall3(SYS_READ, efd, (long)&rval, 8);
    if (rd != 8) {
        TEST_FAIL("eventfd_basic: read returned %ld (expected 8)", rd);
        syscall1(SYS_CLOSE, efd);
        return;
    }

    if (rval != 42) {
        TEST_FAIL("eventfd_basic: read value=%llu (expected 42)", (unsigned long long)rval);
        syscall1(SYS_CLOSE, efd);
        return;
    }

    printf("  counter reads back = %llu [OK]\n", (unsigned long long)rval);

    /* Write twice: counter accumulates */
    uint64_t v1 = 10, v2 = 20;
    syscall3(SYS_WRITE, efd, (long)&v1, 8);
    syscall3(SYS_WRITE, efd, (long)&v2, 8);

    uint64_t acc = 0;
    rd = syscall3(SYS_READ, efd, (long)&acc, 8);
    if (rd != 8 || acc != 30) {
        TEST_FAIL("eventfd_basic: accumulate test: rd=%ld val=%llu (expected 30)",
                  rd, (unsigned long long)acc);
        syscall1(SYS_CLOSE, efd);
        return;
    }
    printf("  accumulated counter = %llu [OK]\n", (unsigned long long)acc);

    syscall1(SYS_CLOSE, efd);
    TEST_PASS("eventfd_basic");
}

/* ============================================================================
 * TEST 2: epoll_create1 + epoll_ctl + epoll_wait on eventfd
 * ============================================================================ */

static void test_epoll_eventfd(void)
{
    printf("\n--- Test: epoll_eventfd ---\n");

    /* Create eventfd */
    long efd = syscall2(SYS_EVENTFD2, 0, 0);
    if (efd < 0) {
        TEST_FAIL("epoll_eventfd: eventfd2() returned %ld", efd);
        return;
    }

    /* Create epoll instance */
    long epfd = syscall1(SYS_EPOLL_CREATE1, 0);
    if (epfd < 0) {
        TEST_FAIL("epoll_eventfd: epoll_create1() returned %ld", epfd);
        syscall1(SYS_CLOSE, efd);
        return;
    }
    printf("  epoll_create1() = fd %ld, eventfd = fd %ld\n", epfd, efd);

    /* Register eventfd for EPOLLIN */
    epoll_event_t add_ev;
    add_ev.events    = EPOLLIN;
    add_ev.data_u64  = (uint64_t)efd;
    long rc = syscall4(SYS_EPOLL_CTL, epfd, EPOLL_CTL_ADD, efd, (long)&add_ev);
    if (rc != 0) {
        TEST_FAIL("epoll_eventfd: epoll_ctl(ADD) returned %ld", rc);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, efd);
        return;
    }

    /* epoll_wait with timeout=0 — no data yet, should return 0 */
    epoll_event_t out_ev;
    long nev = syscall4(SYS_EPOLL_WAIT, epfd, (long)&out_ev, 1, 0);
    if (nev != 0) {
        TEST_FAIL("epoll_eventfd: epoll_wait before write: nev=%ld (expected 0)", nev);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, efd);
        return;
    }
    printf("  epoll_wait (no data) = 0 events [OK]\n");

    /* Write to eventfd to signal it */
    uint64_t sig = 7;
    syscall3(SYS_WRITE, efd, (long)&sig, 8);

    /* epoll_wait — should return 1 event (EPOLLIN) */
    nev = syscall4(SYS_EPOLL_WAIT, epfd, (long)&out_ev, 1, 100);
    if (nev != 1) {
        TEST_FAIL("epoll_eventfd: epoll_wait after write: nev=%ld (expected 1)", nev);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, efd);
        return;
    }
    if (!(out_ev.events & EPOLLIN)) {
        TEST_FAIL("epoll_eventfd: EPOLLIN not set (events=0x%x)", out_ev.events);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, efd);
        return;
    }
    if (out_ev.data_u64 != (uint64_t)efd) {
        TEST_FAIL("epoll_eventfd: data.u64=%llu (expected %lld)",
                  (unsigned long long)out_ev.data_u64, (long long)efd);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, efd);
        return;
    }
    printf("  epoll_wait (after write) = 1 event EPOLLIN [OK]\n");

    syscall1(SYS_CLOSE, epfd);
    syscall1(SYS_CLOSE, efd);
    TEST_PASS("epoll_eventfd");
}

/* ============================================================================
 * TEST 3: epoll on pipe — write to pipe, detect EPOLLIN
 * ============================================================================ */

static void test_epoll_pipe(void)
{
    printf("\n--- Test: epoll_pipe ---\n");

    /* Create a pipe */
    int pfd[2];
    long rc = syscall1(SYS_PIPE, (long)pfd);
    if (rc < 0) {
        TEST_FAIL("epoll_pipe: pipe() returned %ld", rc);
        return;
    }

    /* Create epoll */
    long epfd = syscall1(SYS_EPOLL_CREATE1, 0);
    if (epfd < 0) {
        TEST_FAIL("epoll_pipe: epoll_create1() returned %ld", epfd);
        syscall1(SYS_CLOSE, pfd[0]);
        syscall1(SYS_CLOSE, pfd[1]);
        return;
    }

    /* Register pipe read-end for EPOLLIN */
    epoll_event_t ev;
    ev.events   = EPOLLIN;
    ev.data_u64 = (uint64_t)pfd[0];
    rc = syscall4(SYS_EPOLL_CTL, epfd, EPOLL_CTL_ADD, pfd[0], (long)&ev);
    if (rc != 0) {
        TEST_FAIL("epoll_pipe: epoll_ctl(ADD) returned %ld", rc);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, pfd[0]);
        syscall1(SYS_CLOSE, pfd[1]);
        return;
    }

    /* No data yet — expect 0 events */
    epoll_event_t out;
    long nev = syscall4(SYS_EPOLL_WAIT, epfd, (long)&out, 1, 0);
    if (nev != 0) {
        TEST_FAIL("epoll_pipe: before write: nev=%ld (expected 0)", nev);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, pfd[0]);
        syscall1(SYS_CLOSE, pfd[1]);
        return;
    }
    printf("  epoll_wait (no data) = 0 events [OK]\n");

    /* Write to pipe */
    const char* msg = "test";
    syscall3(SYS_WRITE, pfd[1], (long)msg, 4);

    /* epoll_wait — should see EPOLLIN */
    nev = syscall4(SYS_EPOLL_WAIT, epfd, (long)&out, 1, 100);
    if (nev != 1) {
        TEST_FAIL("epoll_pipe: after write: nev=%ld (expected 1)", nev);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, pfd[0]);
        syscall1(SYS_CLOSE, pfd[1]);
        return;
    }
    if (!(out.events & EPOLLIN)) {
        TEST_FAIL("epoll_pipe: EPOLLIN not set (events=0x%x)", out.events);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, pfd[0]);
        syscall1(SYS_CLOSE, pfd[1]);
        return;
    }
    printf("  epoll_wait (after write) = 1 event EPOLLIN [OK]\n");

    syscall1(SYS_CLOSE, epfd);
    syscall1(SYS_CLOSE, pfd[0]);
    syscall1(SYS_CLOSE, pfd[1]);
    TEST_PASS("epoll_pipe");
}

/* ============================================================================
 * TEST 4: epoll_ctl DEL — event no longer reported after delete
 * ============================================================================ */

static void test_epoll_ctl_del(void)
{
    printf("\n--- Test: epoll_ctl_del ---\n");

    long efd = syscall2(SYS_EVENTFD2, 0, 0);
    if (efd < 0) {
        TEST_FAIL("epoll_ctl_del: eventfd2() failed: %ld", efd);
        return;
    }

    long epfd = syscall1(SYS_EPOLL_CREATE1, 0);
    if (epfd < 0) {
        TEST_FAIL("epoll_ctl_del: epoll_create1() failed: %ld", epfd);
        syscall1(SYS_CLOSE, efd);
        return;
    }

    /* ADD */
    epoll_event_t ev;
    ev.events   = EPOLLIN;
    ev.data_u64 = (uint64_t)efd;
    long rc = syscall4(SYS_EPOLL_CTL, epfd, EPOLL_CTL_ADD, efd, (long)&ev);
    if (rc != 0) {
        TEST_FAIL("epoll_ctl_del: ADD failed: %ld", rc);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, efd);
        return;
    }

    /* Signal eventfd */
    uint64_t sig = 1;
    syscall3(SYS_WRITE, efd, (long)&sig, 8);

    /* DEL before reading event */
    rc = syscall4(SYS_EPOLL_CTL, epfd, EPOLL_CTL_DEL, efd, 0);
    if (rc != 0) {
        TEST_FAIL("epoll_ctl_del: DEL failed: %ld", rc);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, efd);
        return;
    }

    /* epoll_wait — should return 0 (no watches) */
    epoll_event_t out;
    long nev = syscall4(SYS_EPOLL_WAIT, epfd, (long)&out, 1, 0);
    if (nev != 0) {
        TEST_FAIL("epoll_ctl_del: after DEL: nev=%ld (expected 0)", nev);
        syscall1(SYS_CLOSE, epfd);
        syscall1(SYS_CLOSE, efd);
        return;
    }
    printf("  epoll_wait after DEL = 0 events [OK]\n");

    syscall1(SYS_CLOSE, epfd);
    syscall1(SYS_CLOSE, efd);
    TEST_PASS("epoll_ctl_del");
}

/* ============================================================================
 * TEST 5: select() on pipe — write to pipe, detect readable fd
 * ============================================================================ */

static void test_select_pipe(void)
{
    printf("\n--- Test: select_pipe ---\n");

    int pfd[2];
    long rc = syscall1(SYS_PIPE, (long)pfd);
    if (rc < 0) {
        TEST_FAIL("select_pipe: pipe() returned %ld", rc);
        return;
    }

    /* Write to pipe first */
    const char* msg = "sel";
    syscall3(SYS_WRITE, pfd[1], (long)msg, 3);

    /* select: watch pfd[0] for readability */
    fd_set_t rfds;
    FD_ZERO(&rfds);
    FD_SET_BIT(pfd[0], &rfds);

    timeval_t tv;
    tv.tv_sec  = 0;
    tv.tv_usec = 0;  /* immediate check */

    long nfds = (long)(pfd[0] + 1);
    long nrdy = syscall5(SYS_SELECT, nfds, (long)&rfds, 0, 0, (long)&tv);
    if (nrdy <= 0) {
        TEST_FAIL("select_pipe: select() = %ld (expected > 0)", nrdy);
        syscall1(SYS_CLOSE, pfd[0]);
        syscall1(SYS_CLOSE, pfd[1]);
        return;
    }
    if (!FD_ISSET_BIT(pfd[0], &rfds)) {
        TEST_FAIL("select_pipe: pfd[0] not set in rfds after select");
        syscall1(SYS_CLOSE, pfd[0]);
        syscall1(SYS_CLOSE, pfd[1]);
        return;
    }
    printf("  select() = %ld ready fds, pfd[0] readable [OK]\n", nrdy);

    syscall1(SYS_CLOSE, pfd[0]);
    syscall1(SYS_CLOSE, pfd[1]);
    TEST_PASS("select_pipe");
}

/* ============================================================================
 * TEST 6: select() with zero timeout — returns 0 when no data
 * ============================================================================ */

static void test_select_timeout(void)
{
    printf("\n--- Test: select_timeout ---\n");

    /* Create empty pipe (no writes) */
    int pfd[2];
    long rc = syscall1(SYS_PIPE, (long)pfd);
    if (rc < 0) {
        TEST_FAIL("select_timeout: pipe() returned %ld", rc);
        return;
    }

    fd_set_t rfds;
    FD_ZERO(&rfds);
    FD_SET_BIT(pfd[0], &rfds);

    timeval_t tv;
    tv.tv_sec  = 0;
    tv.tv_usec = 0;  /* zero timeout = non-blocking */

    long nfds = (long)(pfd[0] + 1);
    long nrdy = syscall5(SYS_SELECT, nfds, (long)&rfds, 0, 0, (long)&tv);
    if (nrdy != 0) {
        TEST_FAIL("select_timeout: select() = %ld (expected 0, pipe empty)", nrdy);
        syscall1(SYS_CLOSE, pfd[0]);
        syscall1(SYS_CLOSE, pfd[1]);
        return;
    }
    printf("  select() on empty pipe with 0 timeout = 0 [OK]\n");

    syscall1(SYS_CLOSE, pfd[0]);
    syscall1(SYS_CLOSE, pfd[1]);
    TEST_PASS("select_timeout");
}

/* ============================================================================
 * MAIN
 * ============================================================================ */

int main(void)
{
    printf("=== VOS3 epoll/select/eventfd Tests (Task 1.3) ===\n");

    test_eventfd_basic();
    test_epoll_eventfd();
    test_epoll_pipe();
    test_epoll_ctl_del();
    test_select_pipe();
    test_select_timeout();

    printf("\n=== Results: %d passed, %d failed ===\n",
           g_tests_passed, g_tests_failed);

    if (g_tests_failed == 0) {
        printf("[PASS] test_epoll\n");
        return 0;
    } else {
        printf("[FAIL] test_epoll: %d failures\n", g_tests_failed);
        return 1;
    }
}
