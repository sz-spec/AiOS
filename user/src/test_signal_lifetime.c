/* Real ring-3 signal state, inheritance and exec controls. */
#include "signal.h"
#include "stdio.h"
#include "string.h"
#include "unistd.h"
#include "syscall.h"

static volatile int calls;
static volatile unsigned short handler_cs;
static int failures;

static void handler(int sig)
{
    if (sig == SIGUSR1) calls++;
    __asm__ volatile("mov %%cs, %0" : "=r"(handler_cs));
}

static void check(int ok, const char *name)
{
    printf("[%s] signal_lifetime: %s\n", ok ? "PASS" : "FAIL", name);
    if (!ok) failures++;
}

static int reap_ok(pid_t pid)
{
    int status = -1;
    return pid > 0 && waitpid(pid, &status, 0) == pid && status == 0;
}

static int exec_check(void)
{
    struct sigaction caught = {0}, ignored = {0};
    sigset_t blocked = 0;
    check(sigaction(SIGUSR1, NULL, &caught) == 0 && caught.sa_handler == SIG_DFL,
          "exec_resets_caught_handler");
    check(sigaction(SIGUSR2, NULL, &ignored) == 0 && ignored.sa_handler == SIG_IGN,
          "exec_preserves_ignored_handler");
    check(sigprocmask(SIG_SETMASK, NULL, &blocked) == 0 && sigismember(&blocked, SIGUSR1),
          "exec_preserves_blocked_mask");
    return failures != 0;
}

static int run_cases(void)
{
    struct sigaction action = {0}, old = {0};
    action.sa_handler = handler;
    check(getpid() > 1024, "identity_exceeds_old_table_bound");
    check(sigaction(SIGUSR1, &action, NULL) == 0, "install_high_identity_handler");
    check(raise(SIGUSR1) == 0 && calls == 1 && (handler_cs & 3U) == 3U,
          "handler_executes_only_in_ring3");

    /* Bypass libc: missing restorer and kernel addresses must be rejected. */
    struct sigaction bad = {0};
    bad.sa_handler = handler;
    check(syscall4(13, SIGUSR1, (long)&bad, 0, sizeof(sigset_t)) < 0,
          "raw_missing_restorer_rejected");
    bad.sa_handler = (sighandler_t)0xffffffff80000000ULL;
    bad.sa_flags = SA_RESTORER;
    bad.sa_restorer = (void (*)(void))0xffffffff80000000ULL;
    check(syscall4(13, SIGUSR1, (long)&bad, 0, sizeof(sigset_t)) < 0,
          "raw_kernel_handler_rejected");
    check(sigaction(SIGUSR1, NULL, &old) == 0 && old.sa_handler == handler,
          "rejected_actions_leave_previous_handler");

    sigset_t mask = 0;
    sigaddset(&mask, SIGUSR1);
    calls = 0;
    check(sigprocmask(SIG_BLOCK, &mask, NULL) == 0 && raise(SIGUSR1) == 0 && calls == 0,
          "blocked_signal_stays_pending");
    pid_t child = fork();
    if (child == 0) {
        if (sigprocmask(SIG_UNBLOCK, &mask, NULL) != 0 || calls != 0) _exit(11);
        if (raise(SIGUSR1) != 0 || calls != 1 || (handler_cs & 3U) != 3U) _exit(12);
        struct sigaction ignore = {0};
        ignore.sa_handler = SIG_IGN;
        if (sigaction(SIGUSR1, &ignore, NULL) != 0) _exit(13);
        _exit(0);
    }
    check(reap_ok(child), "fork_inherits_handler_mask_but_not_pending");
    check(sigaction(SIGUSR1, NULL, &old) == 0 && old.sa_handler == handler,
          "fork_handler_changes_do_not_modify_parent");
    check(sigprocmask(SIG_UNBLOCK, &mask, NULL) == 0 && calls == 1,
          "parent_pending_signal_survives_child");

    child = fork();
    if (child == 0) {
        struct sigaction ignore = {0};
        ignore.sa_handler = SIG_IGN;
        if (sigaction(SIGUSR2, &ignore, NULL) != 0 ||
            sigprocmask(SIG_BLOCK, &mask, NULL) != 0) _exit(21);
        char *argv[] = {"test_signal_lifetime", "exec-check", NULL};
        char *envp[] = {NULL};
        execve("/bin/test_signal_lifetime", argv, envp);
        _exit(22);
    }
    check(reap_ok(child), "exec_signal_state_controls");
    return failures != 0;
}

int main(int argc, char **argv)
{
    if (argc == 2 && strcmp(argv[1], "exec-check") == 0) return exec_check();
    if (getpid() > 1024) return run_cases();
    /* A filtered standalone run still exercises the historical high-ID bug. */
    for (int i = 0; i < 1100; i++) {
        pid_t child = fork();
        if (child == 0) _exit(0);
        if (!reap_ok(child)) { check(0, "identity_churn_reaped"); return 1; }
    }
    pid_t child = fork();
    if (child == 0) _exit(run_cases());
    check(reap_ok(child), "high_identity_child_controls");
    return failures != 0;
}
