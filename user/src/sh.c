/**
 * @file sh.c
 * @brief VOS3 Shell with Job Control, Pipes, and I/O Redirection
 *
 * @details Command interpreter with built-in commands, background
 *          execution, job control (fg/bg/jobs), pipelines, and
 *          input/output redirection.
 *
 * @version 3.0.0
 * @date 2026-02-16
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

#include "stdio.h"
#include "stdlib.h"
#include "string.h"
#include "unistd.h"
#include "signal.h"

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

#define MAX_LINE        256
#define MAX_ARGS        32
#define MAX_PATH        256
#define MAX_JOBS        16
#define MAX_PIPELINE    8       /* Max commands in pipeline */

/* Shell prompt */
#define PROMPT          "vos3$ "
#define ROOT_PROMPT     "vos3# "

/* Open flags (must match kernel) */
#define O_RDONLY    0x0000
#define O_WRONLY    0x0001
#define O_RDWR      0x0002
#define O_CREAT     0x0040
#define O_TRUNC     0x0200
#define O_APPEND    0x0400

/* ============================================================================
 * JOB CONTROL STRUCTURES
 * ============================================================================ */

/** @brief Job states */
typedef enum job_state {
    JOB_EMPTY = 0,      /**< Slot is empty */
    JOB_RUNNING,        /**< Job is running */
    JOB_STOPPED,        /**< Job is stopped */
    JOB_DONE,           /**< Job completed */
} job_state_t;

/** @brief Job entry */
typedef struct job {
    int             id;         /**< Job number [1], [2], etc. */
    pid_t           pgid;       /**< Process group ID */
    job_state_t     state;      /**< Job state */
    int             background; /**< Running in background? */
    int             exit_status;/**< Exit status if done */
    char            cmd[MAX_LINE]; /**< Command string */
} job_t;

/* ============================================================================
 * COMMAND STRUCTURES
 * ============================================================================ */

/** @brief Single command in a pipeline */
typedef struct command {
    char*   argv[MAX_ARGS];     /**< Argument vector */
    int     argc;               /**< Argument count */
    char*   input_file;         /**< Input redirection file */
    char*   output_file;        /**< Output redirection file */
    int     append;             /**< Append mode for output */
} command_t;

/** @brief Pipeline of commands */
typedef struct pipeline {
    command_t   cmds[MAX_PIPELINE]; /**< Commands */
    int         num_cmds;           /**< Number of commands */
    int         background;         /**< Run in background */
} pipeline_t;

/* ============================================================================
 * GLOBAL STATE
 * ============================================================================ */

static char cwd[MAX_PATH] = "/";
static int last_exit_code = 0;
static int running = 1;
static volatile int got_sigint = 0;
static volatile int got_sigchld = 0;

/* Job table */
static job_t jobs[MAX_JOBS];
static int next_job_id = 1;

/* Shell's own process group */
static pid_t shell_pgid;
static int tty_fd = STDIN_FILENO;

/* External open function */
extern int open(const char *pathname, int flags, unsigned int mode);

/* ============================================================================
 * JOB TABLE MANAGEMENT
 * ============================================================================ */

/**
 * @brief Find an empty job slot
 */
static job_t* job_alloc(void)
{
    for (int i = 0; i < MAX_JOBS; i++) {
        if (jobs[i].state == JOB_EMPTY) {
            jobs[i].id = next_job_id++;
            return &jobs[i];
        }
    }
    return NULL;
}

/**
 * @brief Find job by job ID
 */
static job_t* job_find_by_id(int id)
{
    for (int i = 0; i < MAX_JOBS; i++) {
        if (jobs[i].state != JOB_EMPTY && jobs[i].id == id) {
            return &jobs[i];
        }
    }
    return NULL;
}

/**
 * @brief Find job by process group
 */
static job_t* job_find_by_pgid(pid_t pgid)
{
    for (int i = 0; i < MAX_JOBS; i++) {
        if (jobs[i].state != JOB_EMPTY && jobs[i].pgid == pgid) {
            return &jobs[i];
        }
    }
    return NULL;
}

/**
 * @brief Get most recent background job
 */
static job_t* job_get_current(void)
{
    job_t* current = NULL;
    int max_id = 0;

    for (int i = 0; i < MAX_JOBS; i++) {
        if (jobs[i].state != JOB_EMPTY && jobs[i].state != JOB_DONE) {
            if (jobs[i].id > max_id) {
                max_id = jobs[i].id;
                current = &jobs[i];
            }
        }
    }
    return current;
}

/**
 * @brief Remove completed jobs from table
 */
static void job_cleanup(void)
{
    for (int i = 0; i < MAX_JOBS; i++) {
        if (jobs[i].state == JOB_DONE) {
            jobs[i].state = JOB_EMPTY;
        }
    }
}

/**
 * @brief Print job status
 */
static void job_print(job_t* job)
{
    const char* state_str;

    switch (job->state) {
        case JOB_RUNNING:
            state_str = "Running";
            break;
        case JOB_STOPPED:
            state_str = "Stopped";
            break;
        case JOB_DONE:
            state_str = "Done";
            break;
        default:
            return;
    }

    printf("[%d] %s\t\t%s\n", job->id, state_str, job->cmd);
}

/* ============================================================================
 * SIGNAL HANDLERS
 * ============================================================================ */

/**
 * @brief SIGCHLD handler - child state changed
 */
static void sigchld_handler(int sig)
{
    (void)sig;
    got_sigchld = 1;
}

/**
 * @brief SIGINT handler (Ctrl+C)
 */
static void sigint_handler(int sig)
{
    (void)sig;
    got_sigint = 1;
    putchar('\n');
}

/**
 * @brief Check for terminated children
 */
static void check_children(void)
{
    int status;
    pid_t pid;

    /* Non-blocking wait for any child */
    while ((pid = waitpid(-1, &status, WNOHANG | WUNTRACED)) > 0) {
        /* Find the job */
        job_t* job = job_find_by_pgid(pid);
        if (job == NULL) {
            continue;
        }

        if (WIFEXITED(status) || WIFSIGNALED(status)) {
            job->state = JOB_DONE;
            job->exit_status = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);

            /* Print notification for background jobs */
            if (job->background) {
                printf("\n");
                job_print(job);
            }
        } else if (WIFSTOPPED(status)) {
            job->state = JOB_STOPPED;
            printf("\n");
            job_print(job);
        }
    }

    got_sigchld = 0;
}

/* ============================================================================
 * BUILT-IN COMMANDS
 * ============================================================================ */

static int builtin_cd(int argc, char *argv[])
{
    const char *path = (argc > 1) ? argv[1] : "/";

    if (chdir(path) < 0) {
        printf("cd: %s: No such file or directory\n", path);
        return 1;
    }

    if (getcwd(cwd, sizeof(cwd)) == NULL) {
        strcpy(cwd, "/");
    }

    return 0;
}

static int builtin_pwd(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    if (getcwd(cwd, sizeof(cwd)) == NULL) {
        printf("pwd: cannot get current directory\n");
        return 1;
    }

    printf("%s\n", cwd);
    return 0;
}

static int builtin_echo(int argc, char *argv[])
{
    int newline = 1;
    int start = 1;

    if (argc > 1 && strcmp(argv[1], "-n") == 0) {
        newline = 0;
        start = 2;
    }

    for (int i = start; i < argc; i++) {
        if (i > start) {
            putchar(' ');
        }
        printf("%s", argv[i]);
    }

    if (newline) {
        putchar('\n');
    }

    return 0;
}

static int builtin_exit(int argc, char *argv[])
{
    int code = 0;
    if (argc > 1) {
        code = atoi(argv[1]);
    }
    running = 0;
    return code;
}

static int builtin_jobs(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    int found = 0;
    for (int i = 0; i < MAX_JOBS; i++) {
        if (jobs[i].state != JOB_EMPTY) {
            job_print(&jobs[i]);
            found = 1;
        }
    }

    if (!found) {
        /* No jobs - silent */
    }

    job_cleanup();
    return 0;
}

static int builtin_fg(int argc, char *argv[])
{
    job_t* job = NULL;

    if (argc > 1) {
        /* Parse %N or N */
        const char* arg = argv[1];
        if (arg[0] == '%') {
            arg++;
        }
        int id = atoi(arg);
        job = job_find_by_id(id);
    } else {
        job = job_get_current();
    }

    if (job == NULL || job->state == JOB_DONE) {
        printf("fg: no such job\n");
        return 1;
    }

    printf("%s\n", job->cmd);

    /* Put job in foreground */
    job->background = 0;
    tcsetpgrp(tty_fd, job->pgid);

    /* Continue if stopped */
    if (job->state == JOB_STOPPED) {
        job->state = JOB_RUNNING;
        kill(-job->pgid, SIGCONT);
    }

    /* Wait for it */
    int status;
    waitpid(-job->pgid, &status, WUNTRACED);

    /* Take back terminal control */
    tcsetpgrp(tty_fd, shell_pgid);

    if (WIFEXITED(status) || WIFSIGNALED(status)) {
        job->state = JOB_DONE;
        last_exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    } else if (WIFSTOPPED(status)) {
        job->state = JOB_STOPPED;
        job_print(job);
    }

    job_cleanup();
    return last_exit_code;
}

static int builtin_bg(int argc, char *argv[])
{
    job_t* job = NULL;

    if (argc > 1) {
        const char* arg = argv[1];
        if (arg[0] == '%') {
            arg++;
        }
        int id = atoi(arg);
        job = job_find_by_id(id);
    } else {
        job = job_get_current();
    }

    if (job == NULL) {
        printf("bg: no such job\n");
        return 1;
    }

    if (job->state != JOB_STOPPED) {
        printf("bg: job already running\n");
        return 1;
    }

    /* Resume in background */
    job->state = JOB_RUNNING;
    job->background = 1;
    printf("[%d] %s &\n", job->id, job->cmd);
    kill(-job->pgid, SIGCONT);

    return 0;
}

static int builtin_kill(int argc, char *argv[])
{
    if (argc < 2) {
        printf("usage: kill [-signal] pid|%%job\n");
        return 1;
    }

    int sig = SIGTERM;
    int arg_idx = 1;

    /* Check for signal specification */
    if (argv[1][0] == '-') {
        const char* sigstr = &argv[1][1];

        if (strcmp(sigstr, "INT") == 0 || strcmp(sigstr, "2") == 0) {
            sig = SIGINT;
        } else if (strcmp(sigstr, "TERM") == 0 || strcmp(sigstr, "15") == 0) {
            sig = SIGTERM;
        } else if (strcmp(sigstr, "KILL") == 0 || strcmp(sigstr, "9") == 0) {
            sig = SIGKILL;
        } else if (strcmp(sigstr, "STOP") == 0 || strcmp(sigstr, "19") == 0) {
            sig = SIGSTOP;
        } else if (strcmp(sigstr, "CONT") == 0 || strcmp(sigstr, "18") == 0) {
            sig = SIGCONT;
        } else {
            sig = atoi(sigstr);
        }
        arg_idx = 2;
    }

    if (arg_idx >= argc) {
        printf("kill: missing pid\n");
        return 1;
    }

    for (int i = arg_idx; i < argc; i++) {
        const char* arg = argv[i];
        pid_t pid;

        if (arg[0] == '%') {
            /* Job spec */
            int id = atoi(&arg[1]);
            job_t* job = job_find_by_id(id);
            if (job == NULL) {
                printf("kill: %%%d: no such job\n", id);
                continue;
            }
            pid = -job->pgid;  /* Kill process group */
        } else {
            pid = atoi(arg);
        }

        if (kill(pid, sig) < 0) {
            printf("kill: failed to send signal\n");
        }
    }

    return 0;
}

static int builtin_help(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    printf("\nVOS3 Shell Built-in Commands:\n");
    printf("  cd [dir]       - Change directory\n");
    printf("  pwd            - Print working directory\n");
    printf("  echo [args]    - Print arguments\n");
    printf("  export VAR=val - Set environment variable\n");
    printf("  env            - Print environment\n");
    printf("  clear          - Clear screen\n");
    printf("  jobs           - List background jobs\n");
    printf("  fg [%%job]      - Bring job to foreground\n");
    printf("  bg [%%job]      - Resume job in background\n");
    printf("  kill [-sig] id - Send signal to process/job\n");
    printf("  help           - Show this help\n");
    printf("  exit [code]    - Exit shell\n");
    printf("\nPipelines and Redirection:\n");
    printf("  cmd1 | cmd2    - Pipe output of cmd1 to cmd2\n");
    printf("  cmd < file     - Read input from file\n");
    printf("  cmd > file     - Write output to file\n");
    printf("  cmd >> file    - Append output to file\n");
    printf("\nJob Control:\n");
    printf("  command &      - Run command in background\n");
    printf("  Ctrl+C         - Interrupt foreground job\n");
    printf("  Ctrl+Z         - Suspend foreground job\n");
    printf("  Ctrl+\\         - Quit foreground job\n");
    printf("\n");

    return 0;
}

static int builtin_clear(int argc, char *argv[])
{
    (void)argc;
    (void)argv;
    printf("\033[2J\033[H");
    return 0;
}

static int builtin_export(int argc, char *argv[])
{
    if (argc < 2) {
        printf("export: usage: export VAR=value\n");
        return 1;
    }

    for (int i = 1; i < argc; i++) {
        char *eq = strchr(argv[i], '=');
        if (eq == NULL) {
            printf("export: invalid format: %s\n", argv[i]);
            continue;
        }

        *eq = '\0';
        const char *name = argv[i];
        const char *value = eq + 1;

        if (setenv(name, value, 1) < 0) {
            printf("export: failed to set %s\n", name);
        }
    }

    return 0;
}

static int builtin_env(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    char *vars[] = { "PATH", "HOME", "SHELL", "TERM", "USER", NULL };

    for (int i = 0; vars[i] != NULL; i++) {
        char *val = getenv(vars[i]);
        if (val != NULL) {
            printf("%s=%s\n", vars[i], val);
        }
    }

    return 0;
}

/* ============================================================================
 * FSCHECK - Filesystem Persistence Checker (Phase 36)
 * ============================================================================ */

#define SYS_STATFS  137

/** @brief Filesystem statistics (must match kernel vos3_statfs_t) */
typedef struct {
    unsigned int    f_type;
    unsigned int    f_bsize;
    unsigned long   f_blocks;
    unsigned long   f_bfree;
    unsigned int    f_files;
    unsigned int    f_ffree;
    unsigned int    f_mount_count;
    char            f_fstype[16];
} user_statfs_t;

static inline long syscall2(long num, long arg1, long arg2)
{
    long ret;
    __asm__ volatile (
        "syscall"
        : "=a"(ret)
        : "a"(num), "D"(arg1), "S"(arg2)
        : "rcx", "r11", "memory"
    );
    return ret;
}

#define SYS_GETDENTS 78

/** @brief Directory entry (must match kernel vos3_dirent_t) */
typedef struct {
    unsigned int    d_ino;      /* uint32_t */
    unsigned short  d_reclen;   /* uint16_t */
    unsigned char   d_type;     /* uint8_t */
    char            d_name[64]; /* VOS3_NAME_MAX = 64 */
} user_dirent_t;

static inline long syscall4(long num, long a1, long a2, long a3, long a4)
{
    long ret;
    register long r10 __asm__("r10") = a4;
    __asm__ volatile (
        "syscall"
        : "=a"(ret)
        : "a"(num), "D"(a1), "S"(a2), "d"(a3), "r"(r10)
        : "rcx", "r11", "memory"
    );
    return ret;
}

static int builtin_fscheck(int argc, char *argv[])
{
    (void)argc;
    (void)argv;

    const char *path = "/disk";

    printf("\n=== VOS3 Filesystem Persistence Check ===\n\n");

    /* Get filesystem statistics via statfs syscall */
    user_statfs_t st;
    long rc = syscall2(SYS_STATFS, (long)path, (long)&st);

    if (rc != 0) {
        printf("  statfs('%s') failed (error %ld)\n", path, rc);
        printf("  RESULT: FAIL - Filesystem not mounted\n\n");
        return 1;
    }

    printf("  Mount point:  %s\n", path);
    printf("  FS type:      %s\n", st.f_fstype);
    printf("  Block size:   %u\n", st.f_bsize);
    printf("  Total blocks: %lu\n", st.f_blocks);
    printf("  Free blocks:  %lu\n", st.f_bfree);
    printf("  Total inodes: %u\n", st.f_files);
    printf("  Free inodes:  %u\n", st.f_ffree);
    printf("  Mount count:  %u\n", st.f_mount_count);

    printf("\n  --- Root Directory of %s ---\n", path);

    /* Open /disk and list directory entries */
    int fd = open(path, 0, 0);
    if (fd < 0) {
        printf("  (could not open %s)\n", path);
    } else {
        user_dirent_t entries[16];
        size_t count = 0;
        long drc = syscall4(SYS_GETDENTS, (long)fd, (long)entries, 16, (long)&count);
        if (drc == 0 && count > 0) {
            for (size_t i = 0; i < count; i++) {
                const char *type_str = (entries[i].d_type == 2) ? "DIR" : "FILE";
                printf("    [%s] %s (ino=%u)\n", type_str, entries[i].d_name, entries[i].d_ino);
            }
        } else if (count == 0) {
            printf("    (empty directory)\n");
        }
        close(fd);
    }

    printf("\n  VERDICT: Mount #%u - Persistence %s\n\n",
           st.f_mount_count,
           st.f_mount_count > 1 ? "CONFIRMED" : "FIRST BOOT");

    return 0;
}

/* Built-in command table */
typedef struct {
    const char *name;
    int (*func)(int argc, char *argv[]);
} builtin_t;

static const builtin_t builtins[] = {
    { "cd",      builtin_cd },
    { "pwd",     builtin_pwd },
    { "echo",    builtin_echo },
    { "exit",    builtin_exit },
    { "jobs",    builtin_jobs },
    { "fg",      builtin_fg },
    { "bg",      builtin_bg },
    { "kill",    builtin_kill },
    { "help",    builtin_help },
    { "clear",   builtin_clear },
    { "export",  builtin_export },
    { "env",     builtin_env },
    { "fscheck", builtin_fscheck },
    { NULL,      NULL }
};

/* ============================================================================
 * COMMAND PARSING
 * ============================================================================ */

/**
 * @brief Parse a single command (handles redirections)
 * @return Number of arguments
 */
static int parse_command(char *line, command_t *cmd)
{
    int argc = 0;
    char *p = line;
    int in_quote = 0;
    char quote_char = 0;

    cmd->argc = 0;
    cmd->input_file = NULL;
    cmd->output_file = NULL;
    cmd->append = 0;

    while (*p != '\0' && argc < MAX_ARGS - 1) {
        /* Skip whitespace */
        while (*p == ' ' || *p == '\t') {
            p++;
        }

        if (*p == '\0' || *p == '#') {
            break;
        }

        /* Check for I/O redirection */
        if (*p == '<') {
            p++;
            while (*p == ' ' || *p == '\t') p++;
            cmd->input_file = p;
            /* Find end of filename */
            while (*p && *p != ' ' && *p != '\t' && *p != '>' && *p != '<') {
                p++;
            }
            if (*p) {
                *p++ = '\0';
            }
            continue;
        }

        if (*p == '>') {
            p++;
            if (*p == '>') {
                cmd->append = 1;
                p++;
            }
            while (*p == ' ' || *p == '\t') p++;
            cmd->output_file = p;
            /* Find end of filename */
            while (*p && *p != ' ' && *p != '\t' && *p != '>' && *p != '<') {
                p++;
            }
            if (*p) {
                *p++ = '\0';
            }
            continue;
        }

        /* Start of argument */
        cmd->argv[argc] = p;

        /* Find end of argument */
        while (*p != '\0') {
            if (!in_quote) {
                if (*p == ' ' || *p == '\t' || *p == '<' || *p == '>') {
                    break;
                }
                if (*p == '"' || *p == '\'') {
                    in_quote = 1;
                    quote_char = *p;
                    memmove(p, p + 1, strlen(p));
                    continue;
                }
            } else {
                if (*p == quote_char) {
                    in_quote = 0;
                    memmove(p, p + 1, strlen(p));
                    continue;
                }
            }
            p++;
        }

        if (*p != '\0' && *p != '<' && *p != '>') {
            *p++ = '\0';
        }

        if (*cmd->argv[argc] != '\0') {
            argc++;
        }
    }

    cmd->argv[argc] = NULL;
    cmd->argc = argc;

    return argc;
}

/**
 * @brief Parse command line into a pipeline
 * @return 0 on success, negative on error
 */
static int parse_pipeline(char *line, pipeline_t *pl)
{
    char *p = line;
    char *cmd_start;
    int cmd_idx = 0;

    pl->num_cmds = 0;
    pl->background = 0;

    /* Check for trailing & */
    size_t len = strlen(line);
    if (len > 0) {
        char *end = line + len - 1;
        while (end > line && (*end == ' ' || *end == '\t')) {
            end--;
        }
        if (*end == '&') {
            pl->background = 1;
            *end = '\0';
        }
    }

    /* Split by pipe character */
    cmd_start = p;
    while (*p != '\0' && cmd_idx < MAX_PIPELINE) {
        if (*p == '|') {
            *p = '\0';

            if (parse_command(cmd_start, &pl->cmds[cmd_idx]) > 0) {
                cmd_idx++;
            }

            p++;
            cmd_start = p;
        } else {
            p++;
        }
    }

    /* Parse last command */
    if (cmd_start < p && cmd_idx < MAX_PIPELINE) {
        if (parse_command(cmd_start, &pl->cmds[cmd_idx]) > 0) {
            cmd_idx++;
        }
    }

    pl->num_cmds = cmd_idx;

    return (cmd_idx > 0) ? 0 : -1;
}

/* ============================================================================
 * COMMAND EXECUTION
 * ============================================================================ */

/**
 * @brief Find and run a built-in command
 */
static int run_builtin(command_t *cmd, int *exit_code)
{
    if (cmd->argc == 0) {
        return 0;
    }

    for (int i = 0; builtins[i].name != NULL; i++) {
        if (strcmp(cmd->argv[0], builtins[i].name) == 0) {
            *exit_code = builtins[i].func(cmd->argc, cmd->argv);
            return 1;
        }
    }

    return 0;
}

/**
 * @brief Setup I/O redirections in child process
 */
static int setup_redirections(command_t *cmd)
{
    /* Input redirection */
    if (cmd->input_file != NULL) {
        int fd = open(cmd->input_file, O_RDONLY, 0);
        if (fd < 0) {
            printf("sh: cannot open %s for reading\n", cmd->input_file);
            return -1;
        }
        dup2(fd, STDIN_FILENO);
        close(fd);
    }

    /* Output redirection */
    if (cmd->output_file != NULL) {
        int flags = O_WRONLY | O_CREAT;
        if (cmd->append) {
            flags |= O_APPEND;
        } else {
            flags |= O_TRUNC;
        }

        int fd = open(cmd->output_file, flags, 0644);
        if (fd < 0) {
            printf("sh: cannot open %s for writing\n", cmd->output_file);
            return -1;
        }
        dup2(fd, STDOUT_FILENO);
        close(fd);
    }

    return 0;
}

/**
 * @brief Execute a single command
 */
static void exec_command(command_t *cmd)
{
    char path[MAX_PATH];

    /* Setup redirections */
    if (setup_redirections(cmd) < 0) {
        _exit(1);
    }

    if (strchr(cmd->argv[0], '/') != NULL) {
        execve(cmd->argv[0], cmd->argv, NULL);
    } else {
        snprintf(path, sizeof(path), "/bin/%s", cmd->argv[0]);
        execve(path, cmd->argv, NULL);

        snprintf(path, sizeof(path), "/usr/bin/%s", cmd->argv[0]);
        execve(path, cmd->argv, NULL);
    }

    printf("sh: %s: command not found\n", cmd->argv[0]);
    _exit(127);
}

/**
 * @brief Execute a pipeline
 */
static int run_pipeline(pipeline_t *pl, const char *cmdline)
{
    if (pl->num_cmds == 0) {
        return 0;
    }

    /* Single command without pipes - check for builtin */
    if (pl->num_cmds == 1 && pl->cmds[0].input_file == NULL &&
        pl->cmds[0].output_file == NULL) {
        int exit_code;
        if (run_builtin(&pl->cmds[0], &exit_code)) {
            return exit_code;
        }
    }

    int pipes[MAX_PIPELINE - 1][2];  /* Pipe file descriptors */
    pid_t pids[MAX_PIPELINE];        /* Child PIDs */
    pid_t pgid = 0;                  /* Process group ID */

    /* Create all necessary pipes */
    for (int i = 0; i < pl->num_cmds - 1; i++) {
        if (pipe(pipes[i]) < 0) {
            printf("sh: pipe failed\n");
            return 1;
        }
    }

    /* Fork all children */
    for (int i = 0; i < pl->num_cmds; i++) {
        pids[i] = fork();

        if (pids[i] < 0) {
            printf("sh: fork failed\n");
            return 127;
        }

        if (pids[i] == 0) {
            /* Child process */

            /* Set process group */
            if (i == 0) {
                setpgid(0, 0);  /* First child creates new group */
            } else {
                setpgid(0, pids[0]);  /* Others join first child's group */
            }

            /* If foreground, take control of terminal */
            if (!pl->background && i == 0) {
                tcsetpgrp(tty_fd, getpid());
            }

            /* Restore default signal handlers */
            signal(SIGINT, SIG_DFL);
            signal(SIGQUIT, SIG_DFL);
            signal(SIGTSTP, SIG_DFL);
            signal(SIGCHLD, SIG_DFL);

            /* Setup pipe redirections */
            if (i > 0) {
                /* Not first command - read from previous pipe */
                dup2(pipes[i - 1][0], STDIN_FILENO);
            }

            if (i < pl->num_cmds - 1) {
                /* Not last command - write to next pipe */
                dup2(pipes[i][1], STDOUT_FILENO);
            }

            /* Close all pipe fds */
            for (int j = 0; j < pl->num_cmds - 1; j++) {
                close(pipes[j][0]);
                close(pipes[j][1]);
            }

            /* Execute the command */
            exec_command(&pl->cmds[i]);
            _exit(127);  /* Should not reach here */
        }

        /* Parent: set process group */
        if (i == 0) {
            pgid = pids[0];
        }
        setpgid(pids[i], pgid);
    }

    /* Parent: close all pipe fds */
    for (int i = 0; i < pl->num_cmds - 1; i++) {
        close(pipes[i][0]);
        close(pipes[i][1]);
    }

    if (pl->background) {
        /* Add to job table */
        job_t* job = job_alloc();
        if (job != NULL) {
            job->pgid = pgid;
            job->state = JOB_RUNNING;
            job->background = 1;
            strncpy(job->cmd, cmdline, MAX_LINE - 1);
            job->cmd[MAX_LINE - 1] = '\0';

            printf("[%d] %d\n", job->id, pgid);
        }

        return 0;
    } else {
        /* Foreground - give terminal and wait for all children */
        tcsetpgrp(tty_fd, pgid);

        int status;
        int final_status = 0;

        for (int i = 0; i < pl->num_cmds; i++) {
            waitpid(pids[i], &status, WUNTRACED);

            if (i == pl->num_cmds - 1) {
                /* Last command determines exit status */
                if (WIFEXITED(status)) {
                    final_status = WEXITSTATUS(status);
                } else if (WIFSIGNALED(status)) {
                    final_status = 128 + WTERMSIG(status);
                } else if (WIFSTOPPED(status)) {
                    /* Pipeline was stopped - add to job table */
                    job_t* job = job_alloc();
                    if (job != NULL) {
                        job->pgid = pgid;
                        job->state = JOB_STOPPED;
                        job->background = 0;
                        strncpy(job->cmd, cmdline, MAX_LINE - 1);
                        job->cmd[MAX_LINE - 1] = '\0';

                        job_print(job);
                    }
                    final_status = 0;
                }
            }
        }

        /* Take back terminal */
        tcsetpgrp(tty_fd, shell_pgid);

        return final_status;
    }
}

/**
 * @brief Execute a command line
 */
static int execute(char *line)
{
    char cmdline[MAX_LINE];
    pipeline_t pl;

    /* Save original command */
    strncpy(cmdline, line, MAX_LINE - 1);
    cmdline[MAX_LINE - 1] = '\0';

    /* Skip leading whitespace */
    while (*line == ' ' || *line == '\t') {
        line++;
    }

    if (*line == '\0' || *line == '#') {
        return 0;
    }

    /* Parse into pipeline */
    if (parse_pipeline(line, &pl) < 0) {
        return 0;
    }

    /* Execute pipeline */
    return run_pipeline(&pl, cmdline);
}

/* ============================================================================
 * MAIN LOOP
 * ============================================================================ */

/**
 * @brief Read a line from stdin
 */
static char *read_line(char *buf, size_t size)
{
    size_t pos = 0;
    int c;

    while (pos < size - 1) {
        c = getchar();

        if (c == EOF) {
            if (pos == 0) {
                return NULL;
            }
            break;
        }

        if (c == '\n') {
            break;
        }

        if (c == '\b' || c == 127) {
            if (pos > 0) {
                pos--;
                putchar('\b');
                putchar(' ');
                putchar('\b');
            }
            continue;
        }

        putchar(c);
        buf[pos++] = (char)c;
    }

    buf[pos] = '\0';
    return buf;
}

int main(int argc, char *argv[], char *envp[])
{
    (void)argc;
    (void)argv;
    (void)envp;

    char line[MAX_LINE];
    uid_t uid = getuid();

    /* Initialize job table */
    for (int i = 0; i < MAX_JOBS; i++) {
        jobs[i].state = JOB_EMPTY;
    }

    /* Get shell's own process group */
    shell_pgid = getpid();

    /* Put shell in its own process group */
    setpgid(shell_pgid, shell_pgid);

    /* Take control of terminal */
    tcsetpgrp(tty_fd, shell_pgid);

    /* Setup signal handlers */
    signal(SIGINT, sigint_handler);
    signal(SIGQUIT, SIG_IGN);
    signal(SIGTSTP, SIG_IGN);
    signal(SIGCHLD, sigchld_handler);

    printf("\n");
    printf("VOS3 Shell v3.0 (Pipes & Redirection)\n");
    printf("Type 'help' for available commands.\n");
    printf("\n");

    /* Initialize cwd */
    if (getcwd(cwd, sizeof(cwd)) == NULL) {
        strcpy(cwd, "/");
    }

    /* Main loop */
    while (running) {
        /* Check for terminated children */
        if (got_sigchld) {
            check_children();
        }

        /* Clear signal flag */
        got_sigint = 0;

        /* Print prompt */
        printf("%s", (uid == 0) ? ROOT_PROMPT : PROMPT);

        /* Read line */
        if (read_line(line, sizeof(line)) == NULL) {
            if (got_sigint) {
                continue;
            }
            printf("\n");
            break;
        }
        printf("\n");

        /* Execute */
        last_exit_code = execute(line);

        /* Cleanup completed jobs */
        job_cleanup();
    }

    return last_exit_code;
}
