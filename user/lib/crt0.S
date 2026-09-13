/**
 * @file crt0.S
 * @brief VOS3 User-Space C Runtime Startup
 *
 * @details Entry point for user programs. Sets up stack
 *          and calls main().
 *
 * @version 1.0.0
 * @date 2026-02-15
 *
 * @copyright Copyright (c) 2026 VOS3 Project
 * @license MIT
 */

.section .text
.global _start
.type _start, @function

/**
 * Program entry point
 *
 * Stack layout on entry (set up by kernel exec):
 *   [RSP+0]   argc
 *   [RSP+8]   argv[0]
 *   [RSP+16]  argv[1]
 *   ...
 *   [RSP+?]   NULL (end of argv)
 *   [RSP+?]   envp[0]
 *   ...
 *   [RSP+?]   NULL (end of envp)
 */
_start:
    /* Clear frame pointer for debuggers */
    xor     %rbp, %rbp

    /* Get argc from stack */
    mov     (%rsp), %rdi            /* argc -> rdi (first arg) */

    /* Get argv pointer */
    lea     8(%rsp), %rsi           /* argv -> rsi (second arg) */

    /* Calculate envp pointer: argv + argc + 1 (for NULL) */
    mov     %rdi, %rax
    add     $1, %rax                /* argc + 1 */
    shl     $3, %rax                /* * 8 (pointer size) */
    lea     8(%rsp,%rax,1), %rdx    /* envp -> rdx (third arg) */

    /* Align stack to 16 bytes (required by System V ABI) */
    and     $-16, %rsp

    /* Call main(argc, argv, envp) */
    call    main

    /* Call exit with return value from main */
    mov     %eax, %edi
    call    exit

    /* Should not reach here, but just in case */
    mov     $60, %rax               /* SYS_EXIT */
    xor     %edi, %edi
    syscall

.size _start, . - _start
