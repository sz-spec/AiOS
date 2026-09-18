# SYSINFO ABI: bounded security review

Date: 2026-09-18. Reviewer: `/root/mcp_upgrade`. Reviewed coordinator-authored kernel ABI, shared header, native caller migration and syscall policy. This reviewer authored the musl failure checks, removal of conflicting stubs and the associated host/native libc tests; those portions require the coordinator's independent review and are not independently certified here. Frozen source: `/private/tmp/vos-sysinfo-20260918/source`.

## Defect and corrected contract

The former syscall 99 copied a private 40-byte telemetry structure into native callers that mostly allocated 32 bytes. Appending fields without a supplied capacity was not backward compatible. Its pointer check ignored length and its raw memcpy bypassed recoverable usercopy. The same number also collided with musl's Linux sysinfo ABI.

The new native syscall 483 takes pointer, exact size and version. The shared v1 header fixes the layout at 40 bytes with offsets 0, 8, 16, 20, 24, 32 and 36 checked at compile time. Unknown sizes or versions return EINVAL before output writes. The kernel initializes the complete local snapshot, then calls copy_to_user; copy failure returns EFAULT. The native wrapper supplies all three arguments and callers use the shared definition. Policy permits the new telemetry call where the former telemetry call was permitted, without removing other syscall checks.

**Linux syscall 99 is explicitly unsupported:** it returns ENOSYS without writing output. Historical native binaries that used 99 must be rebuilt against syscall 483; this is not binary compatibility. No heuristic interprets buffer contents or unspecified registers to guess between incompatible layouts.

Musl sysconf memory queries and getloadavg now reject a failed sysinfo call before reading its output and preserve errno. Inspection found a second defect: the build excluded the real Linux wrapper while vos3_stubs defined __lsysinfo as data and public sysinfo as a weak raw-error stub. The conflicting symbols were removed, and the coordinator explicitly includes only the real src/linux/sysinfo.c after the source exclusion filter. The distinct __sysinfo auxiliary-vector storage remains unchanged. Linux-compatible memory/load reporting is still unavailable; safe failure is the implemented behavior.

## Validation and limitations

The author's two focused musl host tests passed before freezing. They compile the actual relevant cases/functions with a controlled syscall substitute and test ENOSYS/EFAULT propagation, untouched load output, unit conversion, successful queries and the real wrapper's error propagation. They do not establish the final dynamic-link result. The new native libc test checks public symbols and output canaries at runtime; its result is pending. Kernel/native tests include object canaries, invalid size/version, legacy 99 no-write, invalid pointers, page crossings and continued valid operation.

The coordinator reports **109 host tests passed**. Final focused frozen-input validation and native build/runtime results are pending at the time of this review. No native pass, linked-symbol verification or full-suite result is claimed here. This review ran no heavy tests, build or VM while other measurements were active.

copy_to_user validates user ranges and effective permissions, supports established demand/COW handling and has exact fault recovery. **EFAULT may leave a copied prefix**; the API and tests must not imply atomic output. Shared-AS page-table mutation between preflight and copy remains an existing concurrency limitation. Telemetry fields are individually sampled, not one globally atomic system snapshot.

Other POSIX handlers in posix_syscall.c still use the old range check and raw memcpy; this patch does not repair or qualify those boundaries. Valid telemetry output does not establish full process isolation, safe asynchronous cancellation, remote task stopping, physical-hardware support or release readiness. The independent review found no new blocker in the bounded versioned telemetry contract; unsupported Linux functionality and the remaining boundaries stay explicit.

## Coordinator validation update

Final source snapshot: `/private/tmp/vos-sysinfo-qualified-20260918/source`. The kernel/UAPI/musl contract reviewed above is unchanged; the final caller audit also rejects stale telemetry in throughput and soak/sustained tests. The final tree passed 110 host tests. BIOS and UEFI both passed the new native SYSINFO and actual musl tests; the full suite remained FAIL at 56/57 due to the health performance threshold (41,931 and 42,850 messages/s). An unchanged old ISO control also failed at 41,144. These are coordinator observations, not a retroactive independent runtime claim by the author. See [final results and raw evidence](sysinfo-abi-2026-09-18.md).
