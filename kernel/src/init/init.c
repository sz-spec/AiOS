/**
 * @file init.c
 * @brief VOS3 Kernel Init Process Spawner
 *
 * @details Creates and starts the init process (PID 1),
 *          which is the first user-space process.
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 *
 * @note MISRA C:2024 Compliant
 */

#include "../../include/vos/task.h"
#include "../../include/vos/scheduler.h"
#include "../../include/vos/elf.h"
#include "../../include/vos/vfs.h"
#include "../../include/vos/console.h"
#include "../../include/vos/string.h"
#include "../../include/vos/user.h"
#ifdef NATIVE_SMP_WORKLOAD
#include "../../include/arch/x86_64/smp.h"
#endif
#ifdef NATIVE_ISOLATION_TEST
#include "../../include/vos/native_isolation_test.h"
#endif

/* ============================================================================
 * CONFIGURATION
 * ============================================================================ */

/** @brief Path to init executable */
#define INIT_PATH       "/bin/init"

/** @brief Path to setup wizard */
#define SETUP_WIZARD_PATH "/bin/setup_wizard"

/** @brief Fallback init paths */
static const char* init_paths[] = {
#ifdef NATIVE_SMP_WORKLOAD
    "/bin/test_native_smp",
#elif defined(MEMORY_TRANSITIONS_TEST)
    "/bin/test_native_memory",
#elif defined(NATIVE_ISOLATION_TEST)
    "/bin/test_native_isolation",
#else
    "/bin/init",
    "/sbin/init",
    "/init",
#endif
    NULL
};

/** @brief Fallback setup wizard paths */
static const char* wizard_paths[] = {
    "/bin/setup_wizard",
    "/sbin/setup_wizard",
    "/setup_wizard",
    NULL
};

/* ============================================================================
 * INIT TASK
 * ============================================================================ */

/**
 * @brief Kernel init task entry point
 *
 * This kernel task is responsible for launching the first
 * user-space process (init). It runs as a kernel thread
 * initially, then execs to become the init process.
 */
static void init_task_entry(void* arg)
{
    (void)arg;

    VOS3_INFO("Init: Kernel init task starting");
#ifdef NATIVE_SMP_WORKLOAD
    for (uint32_t cpu = 0; cpu < vos3_smp_cpu_count(); cpu++) {
        const vos3_smp_cpu_info_t *info = vos3_smp_get_cpu_info(cpu);
        if (info != NULL && info->started)
            VOS3_INFO("NATIVE_SMP topology cpu=%u apic=%u online=1", cpu, info->apic_id);
    }
#endif

    /*
     * Set up standard file descriptors (stdin/stdout/stderr).
     * These must be opened before exec so the user process has
     * working console I/O.
     */
#ifdef NATIVE_TLB_TEST
    extern void vos3_native_tlb_test(void);
    vos3_native_tlb_test();
#endif
#ifdef MEMORY_TRANSITIONS_TEST
    extern void vos3_test_vm_backing(void);
    vos3_test_vm_backing();
#endif
#ifdef AI_CONTEXT_LIFETIME_TEST
    extern void vos3_test_ai_context_lifetime(void);
    vos3_test_ai_context_lifetime();
#endif
    int fd0 = vos3_open("/dev/console", VOS3_O_RDWR, 0U);
    if (fd0 < 0) {
        VOS3_ERROR("Init: Failed to open /dev/console for stdin (error %d)", fd0);
    } else {
        VOS3_DEBUG("Init: stdin (fd %d) -> /dev/console", fd0);
    }

    int fd1 = vos3_open("/dev/console", VOS3_O_RDWR, 0U);
    if (fd1 < 0) {
        VOS3_ERROR("Init: Failed to open /dev/console for stdout (error %d)", fd1);
    } else {
        VOS3_DEBUG("Init: stdout (fd %d) -> /dev/console", fd1);
    }

    int fd2 = vos3_open("/dev/console", VOS3_O_RDWR, 0U);
    if (fd2 < 0) {
        VOS3_ERROR("Init: Failed to open /dev/console for stderr (error %d)", fd2);
    } else {
        VOS3_DEBUG("Init: stderr (fd %d) -> /dev/console", fd2);
    }

    /* Verify we got fd 0, 1, 2 */
    if (fd0 != 0 || fd1 != 1 || fd2 != 2) {
        VOS3_WARN("Init: Expected fd 0,1,2 but got %d,%d,%d", fd0, fd1, fd2);
    }

    /* Try each init path until one succeeds */
    const char* path = NULL;

    for (size_t i = 0U; init_paths[i] != NULL; i++) {
        /* Check if file exists */
        vos3_inode_t stat;
        int result = vos3_stat(init_paths[i], &stat);

        if (result == 0 && (stat.mode & VOS3_S_IFREG) != 0U) {
            path = init_paths[i];
            VOS3_INFO("Init: Found init at '%s'", path);
            break;
        }
    }

    if (path == NULL) {
        VOS3_ERROR("Init: No init executable found!");
        VOS3_ERROR("Init: Tried paths: /bin/init, /sbin/init, /init");

        /* Enter emergency shell mode */
        VOS3_INFO("Init: Entering kernel emergency mode");

        /* Just idle forever */
        for (;;) {
            vos3_task_sleep_ms(1000U);
        }
    }

    /* Execute init */
    VOS3_INFO("Init: Executing '%s'", path);

    const char* argv[] = { path, NULL };
#ifdef NATIVE_ISOLATION_TEST
    const char* target_env = vos3_native_isolation_target();
    if (target_env == NULL) {
        VOS3_ERROR("NATIVE_ISOLATION FAIL target_setup");
        for (;;) vos3_task_sleep_ms(1000U);
    }
#endif
    const char* envp[] = {
        "PATH=/bin:/sbin:/usr/bin:/usr/sbin",
        "HOME=/",
        "TERM=vt100",
#ifdef NATIVE_ISOLATION_TEST
        target_env,
#endif
        NULL
    };

    int result = vos3_exec(path, argv, envp);

    /* If exec returns, it failed */
    VOS3_ERROR("Init: Failed to exec init (error %d)", result);

    for (;;) {
        vos3_task_sleep_ms(1000U);
    }
}

/* ============================================================================
 * SETUP WIZARD TASK (Phase 23)
 * ============================================================================ */

/**
 * @brief Setup wizard task entry point
 *
 * This kernel task runs during first boot when /etc/vos3.conf doesn't exist.
 * It launches the setup wizard which configures the system identity.
 * Shell access is blocked until provisioning completes.
 */
static void setup_wizard_task_entry(void* arg)
{
    (void)arg;

    VOS3_INFO("SetupWizard: Provisioning task starting");

    /* Set up standard file descriptors */
    int fd0 = vos3_open("/dev/console", VOS3_O_RDWR, 0U);
    int fd1 = vos3_open("/dev/console", VOS3_O_RDWR, 0U);
    int fd2 = vos3_open("/dev/console", VOS3_O_RDWR, 0U);

    if (fd0 != 0 || fd1 != 1 || fd2 != 2) {
        VOS3_WARN("SetupWizard: Expected fd 0,1,2 but got %d,%d,%d", fd0, fd1, fd2);
    }

    /* Find setup wizard executable */
    const char* path = NULL;

    for (size_t i = 0U; wizard_paths[i] != NULL; i++) {
        vos3_inode_t stat;
        int result = vos3_stat(wizard_paths[i], &stat);

        if (result == 0 && (stat.mode & VOS3_S_IFREG) != 0U) {
            path = wizard_paths[i];
            VOS3_INFO("SetupWizard: Found wizard at '%s'", path);
            break;
        }
    }

    if (path == NULL) {
        VOS3_ERROR("SetupWizard: No setup_wizard executable found!");

        /* Fall back to init */
        VOS3_INFO("SetupWizard: Falling back to init process");

        for (size_t i = 0U; init_paths[i] != NULL; i++) {
            vos3_inode_t stat;
            int result = vos3_stat(init_paths[i], &stat);

            if (result == 0 && (stat.mode & VOS3_S_IFREG) != 0U) {
                path = init_paths[i];
                break;
            }
        }

        if (path == NULL) {
            VOS3_ERROR("SetupWizard: No init found either - system halted");
            for (;;) {
                vos3_task_sleep_ms(1000U);
            }
        }
    }

    /* Execute setup wizard */
    VOS3_INFO("SetupWizard: Executing '%s'", path);

    const char* argv[] = { path, NULL };
    const char* envp[] = {
        "PATH=/bin:/sbin:/usr/bin:/usr/sbin",
        "HOME=/",
        "TERM=vt100",
        "VOS3_PROVISIONING=1",
        NULL
    };

    int result = vos3_exec(path, argv, envp);

    /* If exec returns, it failed */
    VOS3_ERROR("SetupWizard: Failed to exec (error %d)", result);

    for (;;) {
        vos3_task_sleep_ms(1000U);
    }
}

/* ============================================================================
 * PUBLIC FUNCTIONS
 * ============================================================================ */

/**
 * @brief Spawn the setup wizard process
 *
 * Creates a kernel task that will exec to become the setup wizard.
 * Used during first boot when system is not yet provisioned.
 *
 * @return 0 on success, negative error code on failure
 */
int vos3_spawn_setup_wizard(void)
{
    VOS3_INFO("Spawning setup wizard...");

    /* Create the setup wizard task */
    vos3_task_t* wizard_task = vos3_task_create(
        "setup_wizard",
        setup_wizard_task_entry,
        NULL,
        VOS3_PRIORITY_NORMAL
    );

    if (wizard_task == NULL) {
        VOS3_ERROR("Failed to create setup wizard task");
        return -1;
    }

    /* Set PID to 1 (special case - becomes init after wizard completes) */
    wizard_task->pid = 1U;
    wizard_task->tid = 1U;

    /* Initialize credentials */
    wizard_task->uid = 0U;   /* root */
    wizard_task->gid = 0U;   /* root */
    wizard_task->euid = 0U;
    wizard_task->egid = 0U;
    wizard_task->sid = 1U;
    wizard_task->pgid = 1U;

    /* Add to scheduler */
    vos3_sched_add_task(wizard_task);

    VOS3_INFO("Setup wizard task created (PID 1)");

    return 0;
}

/**
 * @brief Spawn the init process
 *
 * Creates a kernel task that will exec to become init.
 * This ensures init gets PID 1.
 *
 * @return 0 on success, negative error code on failure
 */
int vos3_spawn_init(void)
{
    VOS3_INFO("Spawning init process...");

    /* Create the init task */
    vos3_task_t* init_task = vos3_task_create(
        "init",
        init_task_entry,
        NULL,
        VOS3_PRIORITY_NORMAL
    );

    if (init_task == NULL) {
        VOS3_ERROR("Failed to create init task");
        return -1;
    }

    /* Set PID to 1 (special case for init) */
    init_task->pid = 1U;
    init_task->tid = 1U;

    /* Initialize credentials */
    init_task->uid = 0U;   /* root */
    init_task->gid = 0U;   /* root */
    init_task->euid = 0U;
    init_task->egid = 0U;
    init_task->sid = 1U;   /* Session leader */
    init_task->pgid = 1U;  /* Process group leader */

    /* Add to scheduler */
    vos3_sched_add_task(init_task);

    VOS3_INFO("Init task created (PID 1)");

    return 0;
}

/**
 * @brief Kernel idle task
 *
 * Simple task that runs when no other tasks are ready.
 */
void vos3_kernel_idle(void* arg)
{
    (void)arg;

    for (;;) {
        /* Halt until next interrupt */
        __asm__ volatile ("hlt");
    }
}

/**
 * @brief Create kernel worker tasks
 *
 * Creates essential kernel background tasks.
 *
 * @return 0 on success, negative error code on failure
 */
int vos3_kernel_tasks_init(void)
{
    VOS3_INFO("Creating kernel tasks...");

    /* NOTE: Idle task for CPU 0 is already created by vos3_sched_init().
     * Do NOT create a duplicate here — two idle/0 tasks corrupt the
     * scheduler's g_idle_task[0] pointer and pollute the run queue. */

    VOS3_INFO("Kernel tasks created");

    return 0;
}
