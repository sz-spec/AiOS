/**
 * @file test_signal_musl.c
 * @brief Signal delivery test — dynamically linked via musl libc.
 *
 * Tests: sigaction, kill(getpid()), SIG_IGN, multiple signals.
 * Exercises musl's full signal path including __restore_rt + rt_sigreturn.
 */
#define _GNU_SOURCE

#include <stdio.h>
#include <signal.h>
#include <stdlib.h>
#include <unistd.h>

static volatile sig_atomic_t handler_called = 0;
static volatile sig_atomic_t handler_signum = 0;

static void test_handler(int sig)
{
    handler_called = 1;
    handler_signum = sig;
}

int main(void)
{
    /* Test 1: sigaction + kill(getpid(), SIGUSR1) */
    struct sigaction sa;
    sa.sa_handler = test_handler;
    sa.sa_flags = 0;
    sigemptyset(&sa.sa_mask);

    if (sigaction(SIGUSR1, &sa, NULL) == 0)
        printf("[PASS] test_signal: sigaction_setup\n");
    else
        printf("[FAIL] test_signal: sigaction_setup\n");

    kill(getpid(), SIGUSR1);
    if (handler_called && handler_signum == SIGUSR1)
        printf("[PASS] test_signal: raise_sigusr1\n");
    else
        printf("[FAIL] test_signal: raise_sigusr1 (called=%d sig=%d)\n",
               (int)handler_called, (int)handler_signum);

    /* Test 2: SIG_IGN — signal should be silently ignored */
    sa.sa_handler = SIG_IGN;
    sigaction(SIGUSR1, &sa, NULL);
    kill(getpid(), SIGUSR1);  /* should not crash or call handler */
    printf("[PASS] test_signal: sig_ign\n");

    /* Test 3: multiple signals — SIGUSR2 with handler */
    handler_called = 0;
    handler_signum = 0;
    sa.sa_handler = test_handler;
    sa.sa_flags = 0;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGUSR2, &sa, NULL);
    kill(getpid(), SIGUSR2);
    if (handler_called && handler_signum == SIGUSR2)
        printf("[PASS] test_signal: raise_sigusr2\n");
    else
        printf("[FAIL] test_signal: raise_sigusr2 (called=%d sig=%d)\n",
               (int)handler_called, (int)handler_signum);

    /* Test 4: handler called twice — reset and re-raise SIGUSR1 */
    handler_called = 0;
    handler_signum = 0;
    sa.sa_handler = test_handler;
    sigaction(SIGUSR1, &sa, NULL);
    kill(getpid(), SIGUSR1);
    if (handler_called && handler_signum == SIGUSR1)
        printf("[PASS] test_signal: re_raise_sigusr1\n");
    else
        printf("[FAIL] test_signal: re_raise_sigusr1 (called=%d sig=%d)\n",
               (int)handler_called, (int)handler_signum);

    return 0;
}
