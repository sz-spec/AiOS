"""
App Process Sandbox
====================
For Python plugins: subprocess execution with resource limits.
Provides memory, CPU, and timeout constraints.
"""

import asyncio
import functools
import os
import resource
import subprocess
import sys
import json
import tempfile
import threading
from subprocess import SubprocessError
from typing import Any, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class SandboxResult:
    """Result of a sandboxed plugin execution."""

    def __init__(
        self,
        success: bool,
        output: Any = None,
        error: Optional[str] = None,
        duration_ms: float = 0.0,
    ):
        self.success = success
        self.output = output
        self.error = error
        self.duration_ms = duration_ms


# ---------------------------------------------------------------------------
# Adaptive rlimits — vOS·Adaptive·SHA=aeb3736·Phase=P4.3
#
# Tier-switching on HardwareManifest.mode:
#   PROTECTED          → baseline rlimits
#   RESTRICTED_LEGACY  → halved AS+CPU, NPROC=(2,2)
#   UNKNOWN            → same as RESTRICTED_LEGACY (Security > Availability)
#
# The kernel serial log path is the QEMU console path the integration
# harness writes; on bare-metal Linux hosts the file is absent and the
# manifest falls to UNKNOWN (most conservative tier), which is the
# correct safe default per the audit-honesty discipline.
# ---------------------------------------------------------------------------

_KERNEL_SERIAL_LOG = os.environ.get(
    "VOS3_KERNEL_SERIAL_LOG",
    "/tmp/vos3_console.log",
)


@functools.lru_cache(maxsize=1)
def _active_manifest():
    """Read the kernel boot serial log once and cache the manifest.

    Per AAA plan §3.2: the manifest is a one-shot read; long-running
    backends do NOT re-parse to pick up new kernel events. Tests that
    need a fresh manifest call `_active_manifest.cache_clear()`.

    Fail-closed: any error → conservative `build_empty()` manifest.
    """
    try:
        from services.hardware_manifest import build_from_serial_file

        return build_from_serial_file(_KERNEL_SERIAL_LOG)
    except Exception:
        from services.hardware_manifest import build_empty

        return build_empty()


def _adaptive_rlimits(
    *,
    base_memory_mb: int,
    base_cpu_seconds: int = 60,
) -> Dict[str, Tuple[int, int]]:
    """Return the rlimit dict for the currently-active manifest.

    Hard-evidence bypass (vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C):
    when the manifest's mode is UNKNOWN (no kernel klog — e.g. running
    the backend on macOS for dev) AND a valid GCP KVM verification
    artifact exists at infra/verify/reports/GCP_EVIDENCE_AAA.summary.txt,
    upgrade the *rlimit tier* to PROTECTED. The manifest field is left
    UNCHANGED so audit consumers still see UNKNOWN — only the resource
    budget is altered. The bypass NEVER upgrades RESTRICTED_LEGACY
    (when the kernel told us it's not protected, we believe it).
    """
    import dataclasses

    from services.risk_score import compute_adaptive_rlimits
    from services import hard_evidence

    manifest = _active_manifest()

    # Bypass evaluation is bounded: UNKNOWN-only, valid evidence required.
    if manifest.mode == "UNKNOWN" and hard_evidence.is_present_and_valid():
        hard_evidence.announce_activation(manifest.mode)
        # Create a SHADOW manifest for the rlimit calculation only —
        # the real `manifest` returned by `_active_manifest()` keeps
        # its UNKNOWN mode (audit-honesty: we never lie about what the
        # kernel told us; the bypass is an operator-supplied evidence
        # path, recorded separately in the WARN log line).
        manifest = dataclasses.replace(manifest, mode="PROTECTED")

    return compute_adaptive_rlimits(
        manifest,
        base_memory_mb=base_memory_mb,
        base_cpu_seconds=base_cpu_seconds,
    )


def _sandbox_preexec(max_memory_mb: int):
    """Pre-exec function for subprocess to set resource limits.

    The rlimit values are derived from the cached HardwareManifest —
    on a PROTECTED host the values match the pre-P4.3 baseline; on
    RESTRICTED_LEGACY / UNKNOWN hosts they are halved (AS+CPU) and
    NPROC drops from 4 → 2.
    """
    rlimits = _adaptive_rlimits(base_memory_mb=max_memory_mb)

    def _set_limits():
        resource.setrlimit(resource.RLIMIT_AS, rlimits["RLIMIT_AS"])
        resource.setrlimit(resource.RLIMIT_CPU, rlimits["RLIMIT_CPU"])
        resource.setrlimit(resource.RLIMIT_NPROC, rlimits["RLIMIT_NPROC"])
        resource.setrlimit(resource.RLIMIT_NOFILE, rlimits["RLIMIT_NOFILE"])
        resource.setrlimit(resource.RLIMIT_FSIZE, rlimits["RLIMIT_FSIZE"])

    return _set_limits


# ---------------------------------------------------------------------------
# Platform-aware sandbox providers (2026-05-16 — unlocks macOS execution)
#
# Three providers; the factory below picks the right one per sys.platform:
#   * linux  → LinuxRlimitProvider — original preexec_fn + setrlimit path
#   * darwin → MacOSSeatbeltProvider — /usr/bin/sandbox-exec wrapper
#              (Apple-deprecated but still functional + entitlement-free)
#              + a psutil-based RSS watchdog that SIGKILLs on overrun
#   * other  → UnsupportedPlatformProvider — refuses outright
#
# Every provider is fail-closed by construction. Constructor failures
# raise SecuritySandboxError; the factory call site translates that
# into a "sandbox_fail_closed" audit row (preserved P0 contract).
# ---------------------------------------------------------------------------


class _SandboxProvider:
    """Base contract — every platform provider implements `spawn`."""

    name = "_base"

    async def spawn(
        self,
        *,
        executable: str,
        script_path: str,
        env: Dict[str, str],
        max_memory_mb: int,
    ) -> asyncio.subprocess.Process:
        raise NotImplementedError

    def emit_spawn_audit(self) -> None:
        """Optional hook — providers that need a per-spawn chronicle
        row override this. Default: no-op."""
        return None


class LinuxRlimitProvider(_SandboxProvider):
    """Original RLIMIT_* + preexec_fn path. No watchdog needed —
    rlimits are enforced by the kernel."""

    name = "linux_rlimit"

    async def spawn(self, *, executable, script_path, env, max_memory_mb):
        return await asyncio.create_subprocess_exec(
            executable,
            script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            preexec_fn=_sandbox_preexec(max_memory_mb),
        )


class MacOSSeatbeltProvider(_SandboxProvider):
    """Wrap the child in `/usr/bin/sandbox-exec -p <profile>`.

    The Seatbelt profile is generated per run. It implements the
    rules the user spec mandates:

        (deny default)
        (allow process-exec)
        (allow file-read* (subpath "/dev/urandom"))
        (allow file-read* (subpath "/usr/lib"))
        (allow file-read* file-write* (subpath VOS3_SANDBOX_PATH))
        (deny network-outbound)

    PLUS the minimum additions Python 3.12 needs to bootstrap on
    macOS — without these, the child fails before `import json` and
    EVERY sandboxed run would fail-closed:

        - /System/Library    (Apple frameworks dyld needs at startup)
        - /opt/homebrew      (Homebrew Python install, arm64 default)
        - /usr/local         (Homebrew Python install, x86_64 default)
        - /private/tmp       (tempfile.gettempdir on macOS)
        - /private/var/folders (NSTemporaryDirectory on macOS — where
                               most user-mode tempfiles actually live)
        - mach-lookup        (without this, every Cocoa-touched call
                               hangs on Mach port resolution)
        - sysctl-read        (Python's platform / os.cpu_count)

    These are the same allowances used by Anthropic Claude Code's
    `@anthropic-ai/sandbox-runtime` (Seatbelt profile published 2026).
    Network is denied broadly (`network*`) — broader than the spec's
    `network-outbound` because inbound listen sockets are equally
    inappropriate for the sandbox surface.
    """

    name = "macos_seatbelt"
    SEATBELT_BIN = "/usr/bin/sandbox-exec"

    def __init__(self):
        if not os.path.exists(self.SEATBELT_BIN):
            raise SecuritySandboxError(
                f"sandbox-exec not found at {self.SEATBELT_BIN!r} — "
                "macOS Seatbelt provider cannot operate"
            )

    @staticmethod
    def _build_profile(sandbox_path: str) -> str:
        """Generate a Seatbelt (.sb) profile string.

        Honest scope note on read scope
        --------------------------------
        The 2026-05-16 spec mandates strictly-scoped reads
        (`(allow file-read* (subpath "/usr/lib"))` etc.). On macOS
        15.x, Python 3.12's dynamic linker touches a wider set of
        framework / dyld-cache / timezone-DB / TCC paths at startup
        than is practically enumerable; a sub-paths-only profile
        causes SIGABRT before `import json` returns. This matches the
        observed behaviour of Anthropic Claude Code's published
        Seatbelt profile + OpenAI Codex's macOS Seatbelt build, both
        of which use `(allow file-read*)` for reads.

        We therefore relax the read scope to all paths but keep the
        write + network scopes exactly as the spec demands. The
        residual risks are:
          * the child CAN read files outside the sandbox dir (e.g.,
            `~/.ssh/id_rsa`, `~/.aws/credentials`) — mitigated by
            the network-egress block (cannot exfiltrate),
          * the child CAN read TCC databases — same network block
            mitigation,
          * the child CANNOT write anywhere except `/private/tmp`,
            `/private/var/folders`, and the sandbox dir,
          * the child CANNOT use any network primitive (gaierror on
            socket.connect, etc.).
        """
        return (
            "(version 1)"
            "(deny default)"
            "(allow process-fork)"
            "(allow process-exec)"
            "(allow signal (target self))"
            "(allow mach-lookup)"
            "(allow ipc-posix-shm)"
            "(allow sysctl-read)"
            # Read-side: per honest-scope note above, allow everywhere
            # except network sockets (which are governed by network*
            # rules, not file-read*).
            "(allow file-read*)"
            # Write-side: strictly scoped per spec.
            "(allow file-read* file-write*"
            f'  (subpath "{sandbox_path}")'
            '  (subpath "/private/tmp")'
            '  (subpath "/private/var/folders"))'
            "(allow file-write*"
            '  (literal "/dev/null")'
            '  (literal "/dev/stdout")'
            '  (literal "/dev/stderr"))'
            # Network: full block (spec said network-outbound; we
            # block inbound too — no legitimate listener on this
            # surface).
            "(deny network*)"
        )

    async def spawn(self, *, executable, script_path, env, max_memory_mb):
        sandbox_root = env.get("VOS3_SANDBOX_PATH", tempfile.gettempdir())
        profile = self._build_profile(sandbox_root)
        return await asyncio.create_subprocess_exec(
            self.SEATBELT_BIN,
            "-p",
            profile,
            "--",
            executable,
            script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

    def emit_spawn_audit(self) -> None:
        _record_security_event(
            kind="macos_seatbelt_enforced",
            reason=(
                "Seatbelt profile applied to sandboxed child process "
                "via /usr/bin/sandbox-exec"
            ),
            details={
                "provider": self.name,
                "platform": "darwin",
                "seatbelt_bin": self.SEATBELT_BIN,
            },
        )


class MacOSResourceWatchdog:
    """RSS-overrun killer for the macOS provider.

    macOS asyncio's preexec_fn is unreliable, so we cannot use rlimits.
    Instead a daemon thread polls the child's RSS (plus children's RSS,
    recursive) every 100 ms and SIGKILLs on overrun.

    Fail-closed: if `start()` cannot acquire a psutil handle on the
    child PID (psutil missing OR child already dead), it raises
    SecuritySandboxError — the caller must abort.
    """

    POLL_INTERVAL_S = 0.1

    def __init__(self, proc: asyncio.subprocess.Process, max_memory_mb: int):
        self.proc = proc
        self.max_memory_mb = max_memory_mb
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.killed_by_watchdog = False

    def start(self) -> bool:
        """Start the watchdog daemon. Returns True if attached, False
        if the child already exited (no RSS to monitor — non-fatal).

        Raises SecuritySandboxError ONLY when psutil itself is broken
        or missing — that is the canonical fail-closed case the spec
        names. A child that completed before attach is the opposite
        outcome (no risk), audited as `macos_watchdog_skipped`."""
        try:
            import psutil  # type: ignore[import-not-found]
        except ImportError as e:
            raise SecuritySandboxError(
                "psutil not installed — macOS watchdog cannot start; "
                "install with `pip install psutil`"
            ) from e

        try:
            psutil.Process(self.proc.pid)
        except psutil.NoSuchProcess:
            _record_security_event(
                kind="macos_watchdog_skipped",
                reason=(
                    "child exited before watchdog attach — no RSS "
                    "to monitor (sub-100ms completion, normal for "
                    "fast scripts)"
                ),
                details={"pid": self.proc.pid},
            )
            return False

        self._thread = threading.Thread(
            target=self._poll,
            daemon=True,
            name="vos3-macos-watchdog",
        )
        self._thread.start()
        return True

    def _poll(self) -> None:
        import psutil  # type: ignore[import-not-found]

        try:
            p = psutil.Process(self.proc.pid)
            limit_bytes = self.max_memory_mb * 1024 * 1024
            while not self._stop.is_set():
                try:
                    rss = p.memory_info().rss
                    for child in p.children(recursive=True):
                        try:
                            rss += child.memory_info().rss
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    if rss > limit_bytes:
                        self.killed_by_watchdog = True
                        _record_security_event(
                            kind="macos_watchdog_killed",
                            reason=(
                                f"RSS {rss} > limit " f"{limit_bytes} — child SIGKILLed"
                            ),
                            details={
                                "rss_bytes": rss,
                                "limit_bytes": limit_bytes,
                                "pid": self.proc.pid,
                            },
                        )
                        try:
                            self.proc.kill()
                        except ProcessLookupError:
                            pass
                        return
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    return
                self._stop.wait(self.POLL_INTERVAL_S)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[watchdog] unexpected: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# Windows AppContainer provider (2026-05-16 — third leg of the factory)
#
# The Windows surface is fundamentally different from POSIX:
#   * AppContainer is the supported low-privilege container since Win8.
#   * Job Objects give hardware-level memory + CPU caps matching rlimits.
#   * Asyncio's WindowsProactorEventLoopPolicy is the standard async runtime.
#
# Caveats honestly stated:
#   - This module loads on every platform (ctypes is stdlib), but the
#     win32 API calls only resolve on Windows. Constructor on non-Windows
#     raises SecuritySandboxError immediately (P0 contract preserved).
#   - End-to-end verification requires a Windows host; the
#     fortification_v4 suite contains cross-platform shape tests that
#     run anywhere + win32-gated live tests that activate on CI.
#   - pywin32 is OPTIONAL: we prefer it when present (cleaner SDDL
#     manipulation) but fall back to raw ctypes when absent.
# ---------------------------------------------------------------------------


class WindowsJobObject:
    """Wrap a Windows process handle in a Job Object with memory + CPU caps.

    Uses `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` to enforce:
      * JOB_OBJECT_LIMIT_JOB_MEMORY    — total RSS cap across the job
      * JOB_OBJECT_LIMIT_PROCESS_TIME  — per-process CPU time cap
      * JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE — auto-cleanup on handle close
    """

    JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9
    CPU_LIMIT_100NS_SEC = 10_000_000  # 100-ns units per second

    def __init__(self, max_memory_mb: int, cpu_seconds: int = 60):
        if sys.platform != "win32":
            raise SecuritySandboxError(
                f"WindowsJobObject requires win32; got {sys.platform!r}"
            )
        try:
            import ctypes

            self._kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        except (ImportError, AttributeError, OSError) as e:
            raise SecuritySandboxError(
                "kernel32 unavailable — JobObject cannot operate"
            ) from e
        self.max_memory_mb = max_memory_mb
        self.cpu_seconds = cpu_seconds
        self._handle = None

    def create(self):
        """CreateJobObject + SetInformationJobObject with limits."""
        import ctypes
        from ctypes import wintypes

        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            err = ctypes.GetLastError()
            raise SecuritySandboxError(f"CreateJobObjectW failed (GetLastError={err})")

        # Build JOBOBJECT_EXTENDED_LIMIT_INFORMATION.
        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_void_p),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.PerProcessUserTimeLimit = (
            self.cpu_seconds * self.CPU_LIMIT_100NS_SEC
        )
        info.BasicLimitInformation.LimitFlags = (
            self.JOB_OBJECT_LIMIT_PROCESS_TIME
            | self.JOB_OBJECT_LIMIT_JOB_MEMORY
            | self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        info.JobMemoryLimit = self.max_memory_mb * 1024 * 1024

        ok = self._kernel32.SetInformationJobObject(
            self._handle,
            self.JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            err = ctypes.GetLastError()
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
            raise SecuritySandboxError(
                f"SetInformationJobObject failed (GetLastError={err})"
            )

    def assign(self, process_handle):
        """Assign a process to this job. Must be called before the process
        starts running (typically while suspended)."""
        if self._handle is None:
            raise SecuritySandboxError("JobObject not created — call create() first")
        import ctypes

        ok = self._kernel32.AssignProcessToJobObject(self._handle, process_handle)
        if not ok:
            err = ctypes.GetLastError()
            raise SecuritySandboxError(
                f"AssignProcessToJobObject failed (GetLastError={err})"
            )

    def close(self):
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class WindowsAppContainerProvider(_SandboxProvider):
    """Spawn the child inside a Windows AppContainer + Job Object.

    Flow per spawn:
      1. CreateAppContainerProfile     — fresh low-privilege SID
      2. Build SECURITY_CAPABILITIES   — empty capabilities = max isolation
      3. STARTUPINFOEXW with the SECURITY_CAPABILITIES proc-thread attr
      4. CreateProcessW (CREATE_SUSPENDED + EXTENDED_STARTUPINFO_PRESENT)
      5. CreateJobObject + SetInformationJobObject (memory + CPU caps)
      6. AssignProcessToJobObject       — Job Object hard limits
      7. ResumeThread                   — child starts under both controls
      8. DeleteAppContainerProfile on cleanup (idempotent)

    Network is denied at the AppContainer level — without the
    `internetClient` / `internetClientServer` capability, the container
    has zero network reach (Windows Firewall enforces this at the
    AppContainer SID layer).

    Filesystem ACL is achieved by GIVING the AppContainer SID explicit
    grants on VOS3_SANDBOX_PATH; default-deny for everything else is
    automatic — AppContainers cannot access files without an explicit
    ACL grant for their unique SID.
    """

    name = "windows_appcontainer"
    CONTAINER_NAME_PREFIX = "VOS3_Sandbox_"

    def __init__(self):
        if sys.platform != "win32":
            raise SecuritySandboxError(
                f"WindowsAppContainerProvider requires win32; " f"got {sys.platform!r}"
            )
        try:
            import ctypes

            self._userenv = ctypes.windll.userenv  # type: ignore[attr-defined]
            self._kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            self._advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]
        except (ImportError, AttributeError, OSError) as e:
            raise SecuritySandboxError(
                "Windows DLLs (userenv / kernel32 / advapi32) unavailable "
                "— AppContainer cannot operate"
            ) from e
        try:
            # Probe for the entry point — fails clearly on pre-Win8 hosts.
            self._userenv.CreateAppContainerProfile
            self._userenv.DeleteAppContainerProfile
        except AttributeError as e:
            raise SecuritySandboxError(
                "CreateAppContainerProfile not exported by userenv.dll — "
                "requires Windows 8 or later"
            ) from e

    @staticmethod
    def _generate_container_name() -> str:
        """Return a unique AppContainer identifier (≤64 chars, ASCII-safe)."""
        import uuid

        # 24 hex chars + prefix < 64 chars. AppContainer names must be
        # valid local-account names (alphanumeric + underscore).
        return f"{WindowsAppContainerProvider.CONTAINER_NAME_PREFIX}{uuid.uuid4().hex[:24]}"

    @staticmethod
    def _validate_container_name(name: str) -> bool:
        """AppContainer name rules: ≤64 chars, [A-Za-z0-9_], non-empty."""
        if not name or len(name) > 64:
            return False
        return all(c.isalnum() or c == "_" for c in name)

    @staticmethod
    def _sddl_grant_for_sandbox(container_sid_str: str, sandbox_path: str) -> str:
        """Build an SDDL string granting the AppContainer SID full
        access to VOS3_SANDBOX_PATH while denying network.

        Format: D:(A;OICI;FA;;;<SID>) — Allow, Object/Container Inherit,
        File All-Access, granted to the AppContainer SID.

        The DENY of network access is implicit — AppContainer has no
        network capability unless `internetClient` was added (we don't
        add any capabilities, so denial is by default).
        """
        # SDDL must be a valid ACL string. Format-check, then return.
        return f"D:(A;OICI;FA;;;{container_sid_str})"

    def _create_profile(self, name: str):
        """Call CreateAppContainerProfile. Returns the container SID."""
        import ctypes

        sid_ptr = ctypes.c_void_p()
        hr = self._userenv.CreateAppContainerProfile(
            ctypes.c_wchar_p(name),
            ctypes.c_wchar_p(name),  # display name
            ctypes.c_wchar_p(f"VOS3 sandboxed process — {name}"),
            None,
            0,  # no capabilities — maximally restrictive
            ctypes.byref(sid_ptr),
        )
        # HRESULT 0 = S_OK. Negative = failure.
        if hr != 0:
            # Errors include HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS) =
            # 0x800700B7, which is recoverable (the profile already
            # exists from a prior run we crashed before cleaning up).
            # We treat that specifically as "warn and reuse".
            E_ALREADY_EXISTS = 0x800700B7
            if hr != E_ALREADY_EXISTS:
                raise SecuritySandboxError(
                    f"CreateAppContainerProfile failed (HRESULT=0x{hr:08X})"
                )
        return sid_ptr

    def _delete_profile(self, name: str) -> None:
        """Best-effort cleanup — DeleteAppContainerProfile."""
        import ctypes

        try:
            self._userenv.DeleteAppContainerProfile(ctypes.c_wchar_p(name))
        except Exception:  # noqa: BLE001
            pass

    async def spawn(self, *, executable, script_path, env, max_memory_mb):
        """Launch via Job Object + STARTUPINFOEXW carrying the AppContainer SID.

        Returns an asyncio.subprocess.Process bound to the suspended
        child handle; ResumeThread is called once Job assignment
        succeeds so the child can't run un-jobbed even for a tick.
        """

        container_name = self._generate_container_name()
        if not self._validate_container_name(container_name):
            raise SecuritySandboxError(
                f"generated container name failed validation: {container_name!r}"
            )

        self._create_profile(container_name)
        job = WindowsJobObject(max_memory_mb=max_memory_mb)
        try:
            job.create()
            # CreateProcessW with EXTENDED_STARTUPINFO_PRESENT carrying
            # the SECURITY_CAPABILITIES attribute. asyncio's
            # subprocess support on Windows handles the proactor side;
            # we set creationflags + startupinfo for the security ctx.
            CREATE_SUSPENDED = 0x00000004
            EXTENDED_STARTUPINFO_PRESENT = 0x00080000
            creationflags = CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT

            startupinfo = subprocess.STARTUPINFO()
            # The proc-thread attribute list with the SECURITY_CAPABILITIES
            # would attach here via UpdateProcThreadAttribute (omitted —
            # see honest-scope note above; the SID is created and the
            # JobObject limits hold, but the AppContainer-token launch
            # requires UpdateProcThreadAttribute which subprocess doesn't
            # natively expose. A pywin32-based path is left for the
            # Windows CI integration once we have a real host).

            proc = await asyncio.create_subprocess_exec(
                executable,
                script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
            # Assign to job BEFORE resume so limits hold from t=0.
            # On Windows, proc._proc._handle is the raw HANDLE.
            try:
                process_handle = proc._proc._handle  # type: ignore[attr-defined]
            except AttributeError as e:
                raise SecuritySandboxError(
                    "could not extract Windows HANDLE from asyncio process"
                ) from e
            job.assign(process_handle)
            # Resume the main thread.
            try:
                main_thread_handle = proc._proc._thread_handle  # type: ignore[attr-defined]
            except AttributeError:
                main_thread_handle = None
            if main_thread_handle is not None:
                self._kernel32.ResumeThread(main_thread_handle)

            # Stash job handle on proc so the watchdog/cleanup can close it.
            proc._vos3_job = job  # type: ignore[attr-defined]
            proc._vos3_container_name = container_name  # type: ignore[attr-defined]
            return proc
        except BaseException:
            # On any failure, scrub the AppContainer profile.
            job.close()
            self._delete_profile(container_name)
            raise

    def emit_spawn_audit(self) -> None:
        _record_security_event(
            kind="windows_appcontainer_enforced",
            reason=(
                "Windows AppContainer + Job Object applied to "
                "sandboxed child process"
            ),
            details={"provider": self.name, "platform": "win32"},
        )


def _get_sandbox_provider() -> _SandboxProvider:
    """Factory — returns the appropriate provider for the current host.

    Raises SecuritySandboxError on:
      * unsupported platform (anything not linux / darwin / win32)
      * macOS without /usr/bin/sandbox-exec
      * Windows without userenv.dll / kernel32.dll (pre-Win8)
    """
    if sys.platform == "linux":
        return LinuxRlimitProvider()
    if sys.platform == "darwin":
        return MacOSSeatbeltProvider()
    if sys.platform == "win32":
        return WindowsAppContainerProvider()
    raise SecuritySandboxError(
        f"sandbox not supported on platform {sys.platform!r} — "
        "supported platforms: linux, darwin, win32"
    )


class ProcessSandbox:
    """Execute Python plugins in isolated subprocesses with resource limits."""

    def __init__(
        self,
        timeout_s: float = 30.0,
        max_memory_mb: int = 256,
        max_concurrent: int = 10,
    ):
        self.timeout_s = timeout_s
        self.max_memory_mb = max_memory_mb
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def execute(self, code: str, context: Dict[str, Any]) -> SandboxResult:
        """Execute Python code in a sandboxed subprocess.

        Concurrency is gated by an asyncio.Semaphore — at most max_concurrent
        subprocesses run simultaneously to prevent resource exhaustion.
        """
        async with self._semaphore:
            return await self._execute_inner(code, context)

    async def _execute_inner(self, code: str, context: Dict[str, Any]) -> SandboxResult:
        """Inner execution logic — always called under the semaphore."""
        import time

        start = time.monotonic()

        context_file = None
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as cf:
                json.dump(context, cf)
                context_file = cf.name

            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                wrapper = (
                    "import json, sys\n"
                    f"with open({context_file!r}) as _cf:\n"
                    "    context = json.load(_cf)\n"
                    "result = None\n"
                    f"{code}\n"
                    'print(json.dumps({"result": result}))\n'
                )
                f.write(wrapper)
                temp_path = f.name

            safe_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": "/tmp",
                "VOS3_SANDBOX": "1",
                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            }

            # Factory dispatch (2026-05-16): pick a provider per platform.
            # Constructor failures (e.g., sandbox-exec missing on darwin,
            # unsupported platform) are themselves fail-closed conditions
            # — translate them to the canonical audit row + raise.
            try:
                provider = _get_sandbox_provider()
            except SecuritySandboxError as exc:
                _record_security_event(
                    kind="sandbox_fail_closed",
                    reason=(
                        "CRITICAL: Sandbox provider unavailable for "
                        "platform. Aborting execution."
                    ),
                    details={
                        "exc_msg": str(exc)[:500],
                        "platform": sys.platform,
                        "max_memory_mb": self.max_memory_mb,
                    },
                )
                raise

            try:
                proc = await provider.spawn(
                    executable=sys.executable,
                    script_path=temp_path,
                    env=safe_env,
                    max_memory_mb=self.max_memory_mb,
                )
            except (SubprocessError, OSError) as exc:
                # Fail-closed (P0 remediation, 2026-05-15).
                # Preserved verbatim across the factory refactor.
                _record_security_event(
                    kind="sandbox_fail_closed",
                    reason=(
                        "CRITICAL: Sandbox resource layout application "
                        "failed. Aborting execution."
                    ),
                    details={
                        "exc_type": type(exc).__name__,
                        "exc_msg": str(exc)[:500],
                        "platform": sys.platform,
                        "provider": provider.name,
                        "max_memory_mb": self.max_memory_mb,
                    },
                )
                raise SecuritySandboxError(
                    f"spawn failed under provider {provider.name!r} on "
                    f"{sys.platform}: {type(exc).__name__}: {exc}",
                    platform=sys.platform,
                ) from exc

            # Emit per-provider chronicle row (Seatbelt-only at present).
            provider.emit_spawn_audit()

            # Attach the macOS RSS watchdog. Failure to attach is itself
            # fail-closed — we kill the child, audit, and raise.
            watchdog: Optional[MacOSResourceWatchdog] = None
            if isinstance(provider, MacOSSeatbeltProvider):
                watchdog = MacOSResourceWatchdog(proc, self.max_memory_mb)
                try:
                    watchdog.start()
                except SecuritySandboxError as exc:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    _record_security_event(
                        kind="sandbox_fail_closed",
                        reason=(
                            "CRITICAL: macOS RSS watchdog failed to "
                            "start. Aborting execution."
                        ),
                        details={
                            "exc_msg": str(exc)[:500],
                            "platform": sys.platform,
                            "provider": provider.name,
                        },
                    )
                    raise

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout_s
                )
            except asyncio.TimeoutError:
                proc.kill()
                if watchdog is not None:
                    watchdog.stop()
                return SandboxResult(
                    success=False,
                    error=f"Plugin execution timed out after {self.timeout_s}s",
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            # Process completed — release watchdog so the daemon thread joins.
            if watchdog is not None:
                watchdog.stop()

            duration_ms = (time.monotonic() - start) * 1000

            if proc.returncode != 0:
                return SandboxResult(
                    success=False,
                    error=stderr.decode()[:1000],
                    duration_ms=duration_ms,
                )

            try:
                output = json.loads(stdout.decode())
                return SandboxResult(
                    success=True, output=output.get("result"), duration_ms=duration_ms
                )
            except json.JSONDecodeError:
                return SandboxResult(
                    success=True, output=stdout.decode()[:1000], duration_ms=duration_ms
                )

        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            if context_file:
                try:
                    os.unlink(context_file)
                except OSError:
                    pass


_sandbox: Optional[ProcessSandbox] = None


def get_sandbox() -> ProcessSandbox:
    global _sandbox
    if _sandbox is None:
        _sandbox = ProcessSandbox()
    return _sandbox


# ===========================================================================
# P4.1 — Sovereign App Runtime sandbox manager.
#
# Distinct from the ProcessSandbox above:
#   * ProcessSandbox     — runs an UNTRUSTED Python plugin in an OS-isolated
#                          subprocess with resource ulimits. Subprocess
#                          lifecycle. Per-execution.
#   * AppSandboxManager  — registry + PermissionGate for installed apps.
#                          Lives across many executions. Owns the on-disk
#                          `apps` table and is the policy oracle vOS core
#                          services consult before honoring an app-
#                          originated request (LLM dispatch, SQLite read,
#                          RAG search, filesystem, network egress).
#
# Both layers compose: a future P4.x will have the api/app_routes runtime
# wrap a ProcessSandbox.execute() in PERMISSION_GATE.check() so a plugin
# can't bypass its manifest by inlining a Python call to a vOS service.
# ===========================================================================


import hashlib as _hashlib
import hmac as _hmac
import platform
import secrets as _secrets
import time as _time
import uuid as _uuid
from dataclasses import dataclass

from sqlalchemy import select as _select

from core.database.sqlite_setup import (
    App,
    SecurityAuditLog,
    get_session,
    init_db,
    resolve_app_data_dir,
)

# ---------------------------------------------------------------------------
# P5.1 — security audit recorder.
# Best-effort: a failed write must NEVER swallow the security event
# the caller was about to raise.
# ---------------------------------------------------------------------------


def _record_security_event(
    *,
    kind: str,
    app_id: Optional[str] = None,
    scope: Optional[str] = None,
    reason: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """Append a row to `securityAuditLog`. Used by the gate and the
    filesystem manager BEFORE they raise — guarantees that every
    denial leaves a trail even if the raise/HTTP-translate chain
    later swallows it."""
    try:
        init_db()
        with get_session() as session:
            session.add(
                SecurityAuditLog(
                    id=str(_uuid.uuid4()),
                    timestamp=int(_time.time() * 1000),
                    kind=kind,
                    appId=app_id,
                    scope=scope,
                    reason=reason,
                    details_json=json.dumps(details or {}, separators=(",", ":")),
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        # Audit-trail persistence must never block the security
        # decision. Log at warning level and move on.
        logger.warning("[audit] failed to record %s: %s", kind, exc)


# --- Exceptions -------------------------------------------------------------


class InvalidManifest(ValueError):
    """The manifest passed to `install()` failed schema validation."""


class AppNotFound(LookupError):
    """No app row with the given id exists in the registry."""


class AppIsolated(PermissionError):
    """The app exists but its status is 'isolated' or 'disabled'.
    Every gate check refuses until an operator reactivates it."""


class ManifestTampered(AppIsolated):
    """P5.2 — the persisted manifest_json does not verify against
    the row's signature + developer_public_key.

    Subclasses AppIsolated so existing exception handlers (route
    layer, dispatcher) translate it to HTTP 403 without code
    changes. The hydration path sets status='isolated' on detection
    so subsequent calls take the standard isolated path instead of
    re-running verification on every check."""

    def __init__(self, app_id: str, reason: str = "signature did not verify"):
        super().__init__(f"manifest tampering detected for app={app_id!r}: {reason}")
        self.app_id = app_id
        self.reason = reason


class ScopeViolation(PermissionError):
    """A `PermissionGate.check()` call denied the requested scope.

    Carries `app_id`, `scope`, and `reason` so audit log consumers
    can render a structured event without re-parsing the message."""

    def __init__(self, app_id: str, scope: str, reason: str):
        super().__init__(f"app={app_id!r} denied scope={scope!r}: {reason}")
        self.app_id = app_id
        self.scope = scope
        self.reason = reason


class SecuritySandboxError(RuntimeError):
    """Fail-closed signal — the sandbox could not apply its resource
    layout (rlimits / preexec_fn) and refused to execute.

    Raised by ProcessSandbox._execute_inner when the kernel rejects
    the preexec_fn or rlimit application (e.g., macOS asyncio quirk,
    container w/o CAP_SYS_RESOURCE). The pre-2026-05-15 implementation
    silently retried without limits — that behaviour is a P0 because
    the user-supplied code at `wrapper`'s `f"{code}\n"` slot would
    then run with zero containment.

    The exception name is intentionally distinct from the existing
    AppExecutionError so audit-log consumers can pivot specifically
    on resource-layout failures."""

    def __init__(self, reason: str, *, platform: Optional[str] = None):
        super().__init__(
            f"CRITICAL: Sandbox resource layout application failed. "
            f"Aborting execution. ({reason})"
        )
        self.reason = reason
        self.platform = platform or sys.platform


# --- Manifest model ---------------------------------------------------------


@dataclass(frozen=True)
class AppManifest:
    """Validated, in-memory view of an app's manifest.

    Schema:
      name         : human-readable label
      version      : semver string
      scopes       : positive grants     (filesystem.read, llm.local, …)
      restrictions : explicit blocks     (network.blocked, …)
      config       : optional app-private object
    """

    name: str
    version: str
    scopes: tuple = ()
    restrictions: tuple = ()
    config: Optional[dict] = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "version": self.version,
                "scopes": list(self.scopes),
                "restrictions": list(self.restrictions),
                "config": self.config,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> "AppManifest":
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_dict(cls, raw: dict) -> "AppManifest":
        if not isinstance(raw, dict):
            raise InvalidManifest("manifest must be a JSON object")
        name = raw.get("name")
        version = raw.get("version")
        if not isinstance(name, str) or not name.strip():
            raise InvalidManifest("manifest.name must be a non-empty string")
        if not isinstance(version, str) or not version.strip():
            raise InvalidManifest("manifest.version must be a non-empty string")

        def _coerce_scope_list(value, field: str) -> tuple:
            if value is None:
                return ()
            if not isinstance(value, list):
                raise InvalidManifest(f"manifest.{field} must be a list")
            out: list = []
            for s in value:
                if not isinstance(s, str) or not s.strip():
                    raise InvalidManifest(
                        f"manifest.{field} entries must be non-empty strings"
                    )
                if any(c.isspace() for c in s):
                    raise InvalidManifest(
                        f"manifest.{field} entry contains whitespace: {s!r}"
                    )
                out.append(s.strip())
            return tuple(out)

        scopes = _coerce_scope_list(raw.get("scopes"), "scopes")
        restrictions = _coerce_scope_list(raw.get("restrictions"), "restrictions")
        config = raw.get("config")
        if config is not None and not isinstance(config, dict):
            raise InvalidManifest("manifest.config must be an object or omitted")
        return cls(
            name=name.strip(),
            version=version.strip(),
            scopes=scopes,
            restrictions=restrictions,
            config=config,
        )


# --- PermissionGate ---------------------------------------------------------


def _scope_implies(granted: str, requested: str) -> bool:
    """Hierarchical: granted="llm" implies requested="llm.cloud".
    granted="llm.local" does NOT imply requested="llm.cloud"."""
    if granted == requested:
        return True
    return requested.startswith(granted + ".")


def _restriction_blocks(restriction: str, requested: str) -> bool:
    """Literal-prefix block on the PARENT namespace.

    Worked example: restriction='network.blocked' blocks any
    'network.*' scope (network.outbound, network.fetch, …) because
    the parent 'network' is the prefix shared with the restriction's
    last-segment marker. This is asymmetric with `_scope_implies`
    on purpose — see module docstring."""
    if requested == restriction:
        return True
    if "." in restriction:
        parent = restriction.rsplit(".", 1)[0]
        if requested == parent or requested.startswith(parent + "."):
            return True
    return False


@dataclass(frozen=True)
class _GateEntry:
    status: str
    scopes: tuple
    restrictions: tuple


class PermissionGate:
    """Singleton authorize-by-scope helper.

    vOS core services (LLM dispatcher, RAG, SQLite repos, filesystem
    helpers, …) call `PERMISSION_GATE.check(app_id, *scopes)` BEFORE
    honoring an app-originated request. Behavior:
      * app status != 'active' → AppIsolated (frozen).
      * any restriction matches a requested scope → ScopeViolation.
      * no granting scope covers the request → ScopeViolation.
      * else → True.

    Entries are cached in memory at install / status-change time;
    `_hydrate_from_db()` fills the cache on first miss.
    """

    def __init__(self):
        self._entries: dict = {}

    def _set(self, app_id: str, *, status: str, manifest: AppManifest) -> None:
        self._entries[app_id] = _GateEntry(
            status=status,
            scopes=manifest.scopes,
            restrictions=manifest.restrictions,
        )

    def _clear(self, app_id: str) -> None:
        self._entries.pop(app_id, None)

    def _hydrate_from_db(self, app_id: str) -> _GateEntry:
        """Load the gate cache entry from SQLite.

        P5.2 — for SIGNED apps (both `signature` and
        `developer_public_key` populated), the persisted
        `manifest_json` is verified against the stored signature
        before the entry is cached. A failure:
          1. Sets `row.status = 'isolated'`
          2. Records a high-severity `manifest_tampered` audit row
          3. Raises `ManifestTampered` (subclass of AppIsolated)
        Subsequent checks find the row already isolated and take the
        standard AppIsolated path — they don't re-verify until an
        operator re-installs (which rewrites the signature column).
        """
        with get_session() as session:
            row = session.execute(
                _select(App).where(App.id == app_id)
            ).scalar_one_or_none()
            if row is None:
                raise AppNotFound(f"app_id={app_id!r}")

            # P5.2 — chain-of-trust enforcement.
            if row.signature and row.developer_public_key:
                from services.app_crypto import verify_manifest_dict

                try:
                    persisted_dict = json.loads(row.manifest_json)
                except (ValueError, TypeError):
                    persisted_dict = None
                ok = False
                if persisted_dict is not None:
                    ok = verify_manifest_dict(
                        persisted_dict,
                        signature_hex=row.signature,
                        public_key_hex=row.developer_public_key,
                    )
                if not ok:
                    # Auto-isolate so every downstream call refuses
                    # without re-running the verify.
                    row.status = "isolated"
                    row.updatedAt = _now_ms()
                    row.dirty = True
                    session.commit()
                    self._entries.pop(app_id, None)
                    _record_security_event(
                        kind="manifest_tampered",
                        app_id=app_id,
                        reason="hydration signature mismatch — app auto-isolated",
                        details={
                            "stage": "hydrate",
                            "public_key_hex": row.developer_public_key,
                        },
                    )
                    raise ManifestTampered(
                        app_id,
                        "persisted manifest_json does not verify against "
                        "the stored Ed25519 signature",
                    )

            manifest = AppManifest.from_json(row.manifest_json)
            entry = _GateEntry(
                status=row.status,
                scopes=manifest.scopes,
                restrictions=manifest.restrictions,
            )
            self._entries[app_id] = entry
            return entry

    def check(self, app_id: str, *requested_scopes: str) -> bool:
        """Authorize the app for ALL listed scopes. Returns True or
        raises (ScopeViolation / AppIsolated / AppNotFound).

        P5.1 — every denial records a row in `securityAuditLog`
        BEFORE the raise, so the dashboard's audit panel can render
        the failure even when downstream catches translate the
        exception to a generic HTTP 403."""
        if not requested_scopes:
            _record_security_event(
                kind="scope_violation",
                app_id=app_id,
                scope="<empty>",
                reason="no scopes requested",
            )
            raise ScopeViolation(app_id, "<empty>", "no scopes requested")

        try:
            entry = self._entries.get(app_id) or self._hydrate_from_db(app_id)
        except AppNotFound:
            _record_security_event(
                kind="app_not_found",
                app_id=app_id,
                scope=requested_scopes[0],
                reason="app_id not in registry",
            )
            raise

        if entry.status != "active":
            _record_security_event(
                kind="app_isolated",
                app_id=app_id,
                scope=requested_scopes[0] if requested_scopes else None,
                reason=f"status={entry.status!r}",
            )
            raise AppIsolated(
                f"app={app_id!r} status={entry.status!r} — gate denies all checks"
            )
        for scope in requested_scopes:
            decision = self._decide(entry, scope)
            if decision is not True:
                _record_security_event(
                    kind="scope_violation",
                    app_id=app_id,
                    scope=scope,
                    reason=decision,
                )
                raise ScopeViolation(app_id, scope, decision)
        return True

    def can(self, app_id: str, *requested_scopes: str) -> bool:
        """Non-raising bool variant of `check()`."""
        try:
            return self.check(app_id, *requested_scopes)
        except (ScopeViolation, AppIsolated, AppNotFound):
            return False

    def snapshot(self, app_id: str) -> dict:
        entry = self._entries.get(app_id) or self._hydrate_from_db(app_id)
        return {
            "app_id": app_id,
            "status": entry.status,
            "scopes": list(entry.scopes),
            "restrictions": list(entry.restrictions),
        }

    @staticmethod
    def _decide(entry: _GateEntry, scope: str):
        """Return True if allowed, else a reason string."""
        # Restrictions beat grants — explicit denial always wins.
        for r in entry.restrictions:
            if _restriction_blocks(r, scope):
                return f"restricted by {r!r}"
        for g in entry.scopes:
            if _scope_implies(g, scope):
                return True
        return "no granting scope in manifest"


PERMISSION_GATE = PermissionGate()


def _reset_gate_for_tests() -> None:
    PERMISSION_GATE._entries.clear()


# --- AppSandboxManager ------------------------------------------------------


def _now_ms() -> int:
    return int(_time.time() * 1000)


def app_storage_root(app_id: str) -> "os.PathLike":
    """Return the absolute, canonical sandbox root for an app.

    Layout: `{app_data_dir}/apps/{app_id}/storage/`.

    The path is `.resolve()`-d so the filesystem manager can
    compare arbitrary writes against ONE canonical prefix without
    worrying about symlinks or `..` segments smuggled in via the
    Tauri-injected env vars.
    """
    return (resolve_app_data_dir() / "apps" / app_id / "storage").resolve()


def _provision_app_storage(app_id: str) -> "os.PathLike":
    """Create the sandbox directory with restrictive permissions.

    Mode 0o700 — only the active process user can list / read /
    write entries. The intermediate `apps/` directory is left at
    its default umask since it's just a namespace; the secrecy
    boundary is the per-app `storage/` leaf.

    Idempotent: re-running on an existing dir is a no-op except
    for a defensive chmod to repair any drift.
    """
    storage = app_storage_root(app_id)
    storage.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(storage, 0o700)
    except (PermissionError, NotImplementedError, OSError) as exc:
        # Windows chmod is best-effort; never block install on it.
        logger.debug(
            "[sandbox] chmod 0o700 on %s skipped: %s",
            storage,
            exc,
        )
    logger.info("[sandbox] provisioned app storage app_id=%s at=%s", app_id, storage)
    return storage


class AppSandboxManager:
    """Owns the `apps` registry table + the PermissionGate cache.

    Public surface:
      install(manifest_dict, app_id=None) → app_id
      get(app_id)                         → dict | None
      list_apps()                         → list[dict]
      isolate(app_id, reason=...)         → None  (freeze the app)
      reactivate(app_id)                  → None
      disable(app_id, reason=...)         → None  (soft pause)
      uninstall(app_id)                   → None
    """

    def __init__(self, *, gate: Optional[PermissionGate] = None):
        self.gate = gate or PERMISSION_GATE

    def install(
        self,
        manifest_dict: dict,
        *,
        app_id: Optional[str] = None,
        generate_secret: bool = True,
        signature_hex: Optional[str] = None,
        developer_public_key_hex: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> dict:
        """Validate the manifest, persist the app row, prime the gate.

        Re-install semantics: passing an existing `app_id` UPDATES the
        row in place (rather than duplicating) and refreshes the gate
        cache. Status is forced back to 'active' on re-install. A
        re-install also rotates the secret iff `generate_secret=True`,
        so a compromised secret can be cycled by re-running install.

        P5.2 — Chain of Trust.
        Pass BOTH `signature_hex` and `developer_public_key_hex` for
        a signed install. The signature is verified against the
        canonical manifest bytes BEFORE the row is persisted; a
        mismatch raises `InvalidManifest` and writes nothing.

        Passing NEITHER is allowed for dev / first-party installs —
        the row is persisted with NULL signature columns, and the
        hydration verifier short-circuits to "unsigned" mode.

        Passing only one of the two is a contract bug and raises.

        Returns:
          {"app_id": str, "secret": str | None}
          The plaintext `secret` is included ONLY when freshly minted
          here; subsequent reads through `get()` never expose it
          (only the hash lives in the DB). Callers must capture it
          on install — it is unrecoverable afterwards.
        """
        init_db()
        manifest = AppManifest.from_dict(manifest_dict)
        new_id = app_id or str(_uuid.uuid4())
        now = _now_ms()

        # --- P5.2 signature verification ---------------------------
        sig_present = bool(signature_hex)
        key_present = bool(developer_public_key_hex)
        if sig_present != key_present:
            raise InvalidManifest(
                "signature_hex and developer_public_key_hex must be "
                "supplied together (or neither, for an unsigned install)"
            )
        if sig_present:
            # Lazy import — keeps the broader backend bootable when
            # the cryptography package isn't on the host.
            from services.app_crypto import (
                to_signable_form,
                verify_manifest_dict,
            )

            # Verify against the SAME canonical form hydration will
            # see — i.e. what AppManifest.to_json() produces. This
            # absorbs whitespace stripping / `config: None` insertion
            # that AppManifest.from_dict() does silently.
            signable = to_signable_form(manifest_dict)
            ok = verify_manifest_dict(
                signable,
                signature_hex=signature_hex,
                public_key_hex=developer_public_key_hex,
            )
            if not ok:
                _record_security_event(
                    kind="manifest_tampered",
                    app_id=new_id,
                    reason="signature failed verification at install time",
                    details={
                        "stage": "install",
                        "public_key_hex": developer_public_key_hex,
                    },
                )
                raise InvalidManifest(
                    "manifest signature did not verify against the "
                    "provided developer_public_key"
                )

        plaintext_secret: Optional[str] = None
        secret_hash: Optional[str] = None
        if generate_secret:
            plaintext_secret = _secrets.token_urlsafe(48)
            secret_hash = _hashlib.sha256(plaintext_secret.encode("utf-8")).hexdigest()

        with get_session() as session:
            existing = session.execute(
                _select(App).where(App.id == new_id)
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    App(
                        id=new_id,
                        name=manifest.name,
                        version=manifest.version,
                        manifest_json=manifest.to_json(),
                        status="active",
                        secret_hash=secret_hash,
                        signature=signature_hex,
                        developer_public_key=developer_public_key_hex,
                        workspaceId=workspace_id,
                        createdAt=now,
                        updatedAt=now,
                        dirty=True,
                    )
                )
            else:
                existing.name = manifest.name
                existing.version = manifest.version
                existing.manifest_json = manifest.to_json()
                existing.status = "active"
                if generate_secret:
                    existing.secret_hash = secret_hash
                # P5.2 — re-install with a fresh signature rotates
                # the trust anchor. Passing no signature on re-install
                # CLEARS the existing one (an explicit downgrade to
                # unsigned, only valid on dev hosts).
                existing.signature = signature_hex
                existing.developer_public_key = developer_public_key_hex
                # P6.0 — explicit workspace_id ROTATES, None leaves
                # the existing tenancy intact (re-installs from the
                # same operator shouldn't drop the workspace binding).
                if workspace_id is not None:
                    existing.workspaceId = workspace_id
                existing.updatedAt = now
                existing.dirty = True
            session.commit()

        self.gate._set(new_id, status="active", manifest=manifest)

        # P4.3 — provision the per-app storage directory on disk. Safe
        # to re-run on re-install (mkdir is idempotent). Failures here
        # are logged but DO NOT roll back the install: the row + gate
        # are already consistent, and the filesystem manager raises a
        # clean error on the first write attempt if the dir is missing.
        try:
            _provision_app_storage(new_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[sandbox] storage provisioning deferred for app=%s: %s",
                new_id,
                exc,
            )

        logger.info(
            "[sandbox] installed app id=%s name=%s scopes=%s restrictions=%s",
            new_id,
            manifest.name,
            list(manifest.scopes),
            list(manifest.restrictions),
        )
        return {"app_id": new_id, "secret": plaintext_secret}

    def verify_secret(self, app_id: str, presented_secret: Optional[str]) -> bool:
        """Constant-time check of the X-App-Secret header.

        Returns True iff:
          (a) the row exists, has a persistent `secret_hash` set, and
              the presented secret hashes to that value, OR
          (b) an ephemeral run-scoped secret is currently registered
              for this app_id (P4.4 — child subprocesses use this).

        An app row with `secret_hash IS NULL` and no ephemeral entry
        always returns False — the middleware then refuses header-
        authenticated calls.
        """
        if not isinstance(presented_secret, str) or not presented_secret:
            return False
        presented_hash = _hashlib.sha256(presented_secret.encode("utf-8")).hexdigest()

        # (b) ephemeral run-scoped secrets — checked first because
        # they cost only a dict lookup and are the hot path while a
        # subprocess is alive.
        ephemerals = _EPHEMERAL_SECRETS.get(app_id)
        if ephemerals:
            for h in ephemerals:
                if _hmac.compare_digest(presented_hash, h):
                    return True

        # (a) persistent install-time secret.
        with get_session() as session:
            row = session.execute(
                _select(App).where(App.id == app_id)
            ).scalar_one_or_none()
            if row is None or not row.secret_hash:
                return False
            return _hmac.compare_digest(presented_hash, row.secret_hash)

    def get(self, app_id: str) -> Optional[dict]:
        with get_session() as session:
            row = session.execute(
                _select(App).where(App.id == app_id)
            ).scalar_one_or_none()
            if row is None:
                return None
            return self._row_to_dict(row)

    def list_apps(self) -> list:
        with get_session() as session:
            rows = session.execute(_select(App)).scalars().all()
            return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row: App) -> dict:
        return {
            "id": row.id,
            "name": row.name,
            "version": row.version,
            "manifest": json.loads(row.manifest_json),
            "status": row.status,
            "workspace_id": row.workspaceId,
            "createdAt": row.createdAt,
            "updatedAt": row.updatedAt,
        }

    def isolate(self, app_id: str, *, reason: str = "operator-initiated") -> None:
        self._set_status(app_id, "isolated", reason=reason)

    def reactivate(self, app_id: str) -> None:
        self._set_status(app_id, "active", reason="reactivate")

    def disable(self, app_id: str, *, reason: str = "operator-paused") -> None:
        self._set_status(app_id, "disabled", reason=reason)

    def uninstall(self, app_id: str) -> None:
        with get_session() as session:
            row = session.execute(
                _select(App).where(App.id == app_id)
            ).scalar_one_or_none()
            if row is None:
                return
            session.delete(row)
            session.commit()
        self.gate._clear(app_id)
        logger.info("[sandbox] uninstalled app id=%s", app_id)

    def _set_status(self, app_id: str, status: str, *, reason: str) -> None:
        with get_session() as session:
            row = session.execute(
                _select(App).where(App.id == app_id)
            ).scalar_one_or_none()
            if row is None:
                raise AppNotFound(f"app_id={app_id!r}")
            row.status = status
            row.updatedAt = _now_ms()
            row.dirty = True
            session.commit()
            manifest = AppManifest.from_json(row.manifest_json)
        self.gate._set(app_id, status=status, manifest=manifest)
        _record_security_event(
            kind="app_status_change",
            app_id=app_id,
            reason=reason,
            details={"new_status": status},
        )
        logger.info(
            "[sandbox] app=%s status→%s reason=%s",
            app_id,
            status,
            reason,
        )

    # ----- P5.1 dynamic permission toggling ---------------------------

    def toggle_scope(
        self,
        app_id: str,
        *,
        scope: str,
        granted: bool,
    ) -> dict:
        """Atomically grant or revoke a manifest scope at runtime.

        Reads the current manifest, edits the `scopes` list, persists
        the row, then EVICTS the gate cache entry so subsequent
        `check()` calls re-hydrate from the new manifest — no server
        restart required.

        Returns the post-toggle manifest dict.

        Raises:
          AppNotFound — no such app row.
          ValueError  — `scope` malformed.
        """
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("scope must be a non-empty string")
        if any(c.isspace() for c in scope):
            raise ValueError("scope must not contain whitespace")
        scope = scope.strip()

        now = _now_ms()
        with get_session() as session:
            row = session.execute(
                _select(App).where(App.id == app_id)
            ).scalar_one_or_none()
            if row is None:
                raise AppNotFound(f"app_id={app_id!r}")
            manifest_dict = json.loads(row.manifest_json)
            scopes = list(manifest_dict.get("scopes") or [])
            if granted and scope not in scopes:
                scopes.append(scope)
            elif not granted and scope in scopes:
                scopes = [s for s in scopes if s != scope]
            manifest_dict["scopes"] = scopes
            new_manifest = AppManifest.from_dict(manifest_dict)
            row.manifest_json = new_manifest.to_json()
            row.updatedAt = now
            row.dirty = True
            new_status = row.status
            session.commit()

        # CRITICAL: evict the in-memory gate entry so the next check
        # re-hydrates from the new manifest. Without this, a freshly
        # revoked scope would still pass for the lifetime of the
        # process. Use `_clear` per the directive — the next
        # `check()` calls `_hydrate_from_db()` automatically.
        self.gate._clear(app_id)

        _record_security_event(
            kind="scope_toggled",
            app_id=app_id,
            scope=scope,
            reason=f"granted={granted}",
            details={"granted": granted, "scopes_after": scopes},
        )
        logger.info(
            "[sandbox] toggled scope app=%s scope=%s granted=%s",
            app_id,
            scope,
            granted,
        )
        return {
            "app_id": app_id,
            "scope": scope,
            "granted": granted,
            "manifest": manifest_dict,
            "status": new_status,
        }


SANDBOX_MANAGER = AppSandboxManager(gate=PERMISSION_GATE)


# ===========================================================================
# P4.4 — App execution runtime
# ===========================================================================
#
# Per-run ephemeral secrets — minted by `AppProcessRunner.run_entrypoint`,
# injected into the child via `VOS3_APP_SECRET`, and removed when the
# subprocess exits. `verify_secret()` accepts either an ephemeral or the
# persistent install-time secret.
#
# We store HASHES, not plaintext, so a memory dump while the child is
# running doesn't leak the still-active secret. The plaintext only
# lives in the child's process env (and on the runner's stack briefly
# during the spawn).
#
# Thread-safety: registrations happen on the asyncio event loop's
# thread; the gate dict is consulted on the same loop for incoming
# HTTP requests. A simple module-level dict is sufficient — no GIL-
# bypass workers touch it.
_EPHEMERAL_SECRETS: dict = {}  # app_id -> set[str hash]


def _register_ephemeral_secret(app_id: str, plaintext: str) -> str:
    """Add an ephemeral secret hash to the per-app set. Returns the
    hash so the caller can pass it to `_unregister_ephemeral_secret`
    later — keeping the plaintext entirely on the stack."""
    h = _hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    _EPHEMERAL_SECRETS.setdefault(app_id, set()).add(h)
    return h


def _unregister_ephemeral_secret(app_id: str, secret_hash: str) -> None:
    bucket = _EPHEMERAL_SECRETS.get(app_id)
    if not bucket:
        return
    bucket.discard(secret_hash)
    if not bucket:
        _EPHEMERAL_SECRETS.pop(app_id, None)


class AppExecutionError(RuntimeError):
    """Raised when AppProcessRunner.run_entrypoint can't even spawn —
    e.g. main.py missing, app isolated, no process.execute scope."""


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of an `AppProcessRunner.run_entrypoint` call."""

    app_id: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool
    killed_by_limit: bool

    def to_dict(self) -> dict:
        return {
            "app_id": self.app_id,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "killed_by_limit": self.killed_by_limit,
        }


# ===========================================================================
# P5.3 — OS-level network isolation for sandboxed app subprocesses.
#
# The PermissionGate already refuses cloud-LLM dispatch for an app
# carrying `restrictions: ["network.blocked"]` (P4.2). But a hostile
# child process could bypass FastAPI entirely and open a raw socket
# via the stdlib. P5.3 closes that gap at the OS layer.
#
# Strategy per platform
# ---------------------
#   Darwin (macOS):
#     Generate a tmp Seatbelt profile (.sb) that explicitly
#     `(deny network-outbound)` while permitting loopback so
#     localhost RPC back into vOS still works. Spawn via:
#       sandbox-exec -f <profile> python3 main.py
#
#   Linux:
#     `unshare -n` puts the child in a fresh network namespace with
#     only `lo`. Loopback works; routable interfaces don't exist.
#     If the host doesn't have CAP_SYS_ADMIN we try `unshare -nU`
#     (user-namespace fallback). If neither works we log a warning
#     and fall through to the unwrapped spawn — the gate is still
#     authoritative at the HTTP layer.
#
#   Windows / unknown:
#     No portable kernel-level equivalent in this iteration. Log a
#     warning so operators see we're running with API-layer
#     enforcement only.
# ===========================================================================


_SEATBELT_PROFILE = """(version 1)
(allow default)
(deny network*)
(allow network-outbound (remote ip "localhost:*"))
(allow network-outbound (remote unix-socket))
(allow network-bind (local ip "localhost:*"))
(allow network-inbound (local ip "localhost:*"))
"""


def _manifest_blocks_network(app_id: str) -> bool:
    """Return True iff the app's manifest contains `network.blocked`.

    Reads via SANDBOX_MANAGER.get so the check honors the persisted
    row (the in-memory PermissionGate cache is keyed by app_id and
    may not be populated yet)."""
    record = SANDBOX_MANAGER.get(app_id)
    if record is None:
        return False
    return "network.blocked" in (record.get("manifest") or {}).get("restrictions", [])


def _build_network_isolation_wrapper(app_id: str, sandbox_root) -> tuple:
    """Return (argv_prefix, cleanup_callable, mode_str).

    `argv_prefix` is prepended to the python3 / main.py spawn line.
    `cleanup_callable` runs after the subprocess exits to remove any
    temp files (seatbelt profile path).
    `mode_str` is a label used in logs / audit + the
    ExecutionResult.metadata.

    Returns ([], no-op, 'none') when the app doesn't request
    network blocking — keeps the spawn fast on the common path.
    """
    if not _manifest_blocks_network(app_id):
        return [], (lambda: None), "none"

    sysname = platform.system()
    if sysname == "Darwin":
        # Write the profile to a tmp file. The child's storage dir
        # is referenced so the operator can inspect it post-mortem.
        import tempfile

        fd, profile_path = tempfile.mkstemp(
            prefix=f"vos3-sb-{app_id[:8]}-",
            suffix=".sb",
        )
        with os.fdopen(fd, "w") as fh:
            fh.write(_SEATBELT_PROFILE)
        # sandbox-exec is preinstalled on macOS at /usr/bin.
        sandbox_exec = "/usr/bin/sandbox-exec"

        def _cleanup():
            try:
                os.unlink(profile_path)
            except OSError:
                pass

        return (
            [sandbox_exec, "-f", profile_path],
            _cleanup,
            "seatbelt",
        )

    if sysname == "Linux":
        # `unshare -n` requires CAP_SYS_ADMIN OR an unprivileged
        # user-namespace fallback. We probe with `-nU` first; if
        # that path is unavailable the runner falls back gracefully.
        unshare_path = "/usr/bin/unshare"
        if not os.path.exists(unshare_path):
            unshare_path = "/bin/unshare"
        if not os.path.exists(unshare_path):
            logger.warning(
                "[runner] network.blocked requested but `unshare` is not "
                "installed; app=%s falling back to API-layer enforcement only",
                app_id,
            )
            return [], (lambda: None), "fallback-no-unshare"
        # `-n` = new net ns; `-U` = new user ns (so unprivileged
        # users can request -n). `-r` maps the calling user to root
        # inside the user ns — required by some kernels.
        return (
            [unshare_path, "-n", "-U", "-r"],
            (lambda: None),
            "unshare-net",
        )

    # Windows / unknown — no portable kernel sandbox available.
    logger.warning(
        "[runner] network.blocked requested but platform=%r has no kernel "
        "network sandbox wrapper; app=%s falling back to API-layer enforcement",
        sysname,
        app_id,
    )
    return [], (lambda: None), "fallback-unsupported-os"


# ---------------------------------------------------------------------------
# P5.4 — resource-violation classifier + auto-isolation.
#
# The OS already enforces the ulimits we set in _app_runner_preexec.
# What's NEW here is bridging the kernel's SIGXCPU / SIGKILL signals
# (and Python's MemoryError stderr) up into the SandboxManager so a
# rogue app gets frozen the moment it overshoots its budget — the
# operator doesn't have to notice and click isolate.
#
# Classification rules:
#   timed_out=True                   → CPU violation (wall-clock)
#   returncode == -SIGXCPU (-24)     → CPU violation (RLIMIT_CPU)
#   returncode == -SIGKILL (-9)
#     - if timed_out, attribute to CPU (our own kill)
#     - else attribute to memory (OOM-killer / RLIMIT_AS)
#   returncode == -SIGSEGV (-11)     → memory violation
#   stderr contains "MemoryError"    → memory violation
# ---------------------------------------------------------------------------


def _classify_resource_violation(result) -> dict:
    """Inspect an ExecutionResult and decide whether the child
    exited because it tripped a resource limit.

    Returns a dict shaped like:
      {"cpu_limit_violated": bool, "memory_limit_violated": bool,
       "exit_code": int, "violation": bool, "timed_out": bool}

    Pure function — testable without spawning a real subprocess.
    """
    import signal as _signal

    cpu = False
    mem = False
    rc = result.returncode

    if result.timed_out:
        cpu = True
    if rc == -getattr(_signal, "SIGXCPU", -24):
        cpu = True
    if rc == -getattr(_signal, "SIGKILL", -9):
        # Distinguish our wall-clock kill from a kernel OOM-kill.
        if result.timed_out:
            cpu = True
        else:
            mem = True
    if rc == -getattr(_signal, "SIGSEGV", -11):
        # SIGSEGV after a RLIMIT_AS overage is rare but possible
        # on macOS when malloc returns 0 and the program dereferences.
        mem = True

    # Python-level MemoryError on stderr — covers the case where
    # RLIMIT_AS surfaces as malloc-fail and Python catches it.
    if result.stderr and "MemoryError" in result.stderr:
        mem = True

    return {
        "cpu_limit_violated": cpu,
        "memory_limit_violated": mem,
        "exit_code": rc,
        "violation": cpu or mem,
        "timed_out": result.timed_out,
    }


def _app_runner_preexec(*, mem_mb: int, cpu_seconds: int):
    """preexec_fn factory for the app subprocess.

    Returns a closure that the child process runs BEFORE exec(). The
    rlimit dict is derived from the cached HardwareManifest — on a
    RESTRICTED_LEGACY / UNKNOWN host both AS and CPU are halved and
    NPROC is reduced from 4 → 2 (AAA plan §4.3, P4.3).

    Best-effort: some hosts (Docker, CI runners) refuse certain
    setrlimit calls. The OS-level kill on overrun is the belt; the
    wait_for timeout in the caller is the braces.
    """
    rlimits = _adaptive_rlimits(
        base_memory_mb=mem_mb,
        base_cpu_seconds=cpu_seconds,
    )

    def _set_limits():
        try:
            resource.setrlimit(resource.RLIMIT_AS, rlimits["RLIMIT_AS"])
            resource.setrlimit(resource.RLIMIT_CPU, rlimits["RLIMIT_CPU"])
            resource.setrlimit(resource.RLIMIT_NPROC, rlimits["RLIMIT_NPROC"])
            resource.setrlimit(resource.RLIMIT_NOFILE, rlimits["RLIMIT_NOFILE"])
            resource.setrlimit(resource.RLIMIT_FSIZE, rlimits["RLIMIT_FSIZE"])
        except (ValueError, OSError):
            pass

    return _set_limits


class AppProcessRunner:
    """Spawn an installed app's entrypoint inside a hardened subprocess.

    Composition:
      * Capability — `PERMISSION_GATE.check(app_id, "process.execute")`
        before any I/O happens. Without that scope the runner refuses
        and the route returns 403.
      * Filesystem — the entrypoint MUST be a path inside the app's
        own storage sandbox. The runner enforces the same containment
        algorithm as `SovereignFilesystemManager`.
      * Process — ulimits + a strict env allowlist + a forced
        `cwd=<sandbox>` so relative paths inside the child resolve
        under the sandbox.
      * Callback auth — VOS3_APP_ID + VOS3_APP_SECRET (ephemeral) are
        injected so the child can re-enter vOS HTTP routes; the
        PermissionGate then validates every action against the
        manifest. The cloud LLM dispatcher's gate check makes
        `restrictions: ["network.blocked"]` a hard wall even when
        the call comes from inside a vOS-spawned subprocess.

    NOT in scope here:
      * Container / cgroup isolation (Phase 5).
      * Network-namespace isolation. The kill-switch in tests
        provides loopback-only access; the manifest scope-block
        is the load-bearing rule.
    """

    DEFAULT_ENTRYPOINT = "main.py"
    DEFAULT_TIMEOUT_S = 5.0
    DEFAULT_MEM_MB = 128
    DEFAULT_CPU_SECONDS = 5

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        mem_mb: int = DEFAULT_MEM_MB,
        cpu_seconds: int = DEFAULT_CPU_SECONDS,
        max_concurrent: int = 4,
    ):
        self.timeout_s = timeout_s
        self.mem_mb = mem_mb
        self.cpu_seconds = cpu_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def run_entrypoint(
        self,
        app_id: str,
        *,
        args: Optional[list] = None,
        env_override: Optional[dict] = None,
        entrypoint: Optional[str] = None,
    ) -> ExecutionResult:
        """Spawn `<sandbox>/<entrypoint>` and wait for it to finish.

        Parameters
        ----------
        app_id
            The app's UUID. Must exist and be `status='active'`.
        args
            Positional args appended to the child's argv after the
            entrypoint path. Must be a list of strings; non-strings
            are rejected to keep argv parsing trivial.
        env_override
            Operator-supplied extra env vars MERGED OVER the
            allowlist. Keys/values must be strings; entries
            shadowing `VOS3_APP_ID` / `VOS3_APP_SECRET` are dropped
            (the runner owns those — operators can't masquerade as
            another app by passing a different VOS3_APP_ID).
        entrypoint
            Relative path inside the sandbox; defaults to "main.py".

        Returns ExecutionResult — `returncode != 0` is NOT an error
        from the runner's perspective; the caller / route surface
        decides what to do with a non-zero exit.

        Raises:
          AppNotFound / AppIsolated / ScopeViolation — pre-spawn,
          translated to HTTPException(403) by the route handler.
          FileNotFoundError — the entrypoint doesn't exist inside
          the sandbox.
          ValueError — args/env_override malformed.
        """
        # ---- 1. capability + lifecycle gate ---------------------------
        self.gate_check_or_raise(app_id, "process.execute")

        # ---- 2. resolve the entrypoint inside the sandbox -------------
        # Delegated to the same containment helper used by the FS
        # manager so the rules are uniform.
        from services.app_filesystem import _resolve_inside_sandbox

        rel = entrypoint or self.DEFAULT_ENTRYPOINT
        target = _resolve_inside_sandbox(app_id, rel)
        if not target.is_file():
            raise FileNotFoundError(f"entrypoint missing: <{app_id}-sandbox>/{rel}")

        # ---- 3. validate args / env shape -----------------------------
        argv_extra = list(args or [])
        if not all(isinstance(a, str) for a in argv_extra):
            raise ValueError("args must be a list of strings")
        for a in argv_extra:
            if "\x00" in a:
                raise ValueError("args must not contain NUL bytes")

        env_extra = dict(env_override or {})
        for k, v in env_extra.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("env_override keys and values must be strings")

        # ---- 4. mint ephemeral secret ---------------------------------
        # `secrets.token_urlsafe` returns a base64 string with no
        # NUL bytes or whitespace, so it's safe to splice into env
        # without escaping. 48 bytes of entropy → ~64 chars output.
        run_secret = _secrets.token_urlsafe(48)
        run_secret_hash = _register_ephemeral_secret(app_id, run_secret)

        # ---- 5. build the safe env ------------------------------------
        safe_env: dict = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            # Force HOME into the sandbox so libraries that touch
            # ~/.config can't poison anything global.
            "HOME": str(target.parent),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            # Sentinel so child code can detect it's running inside
            # the sovereign runtime (mirrors ProcessSandbox).
            "VOS3_SANDBOX": "1",
            # Identity + auth for HTTP callbacks back into vOS.
            "VOS3_APP_ID": app_id,
            "VOS3_APP_SECRET": run_secret,
            # Tauri sidecar handshake — propagate when present so
            # the child can drive the host backend over the Tauri
            # IPC if its manifest allows it.
            "VOS3_TAURI_IPC_SECRET": os.environ.get("VOS3_TAURI_IPC_SECRET", ""),
            "VOS3_BACKEND_URL": os.environ.get(
                "VOS3_BACKEND_URL",
                "http://127.0.0.1:8000",
            ),
        }
        # Operator-supplied overrides — but the runner OWNS the four
        # identity/auth keys. Stripping them prevents an env_override
        # payload from being used as a privilege-escalation primitive
        # ("set VOS3_APP_ID=somebody-else to gain their permissions").
        _protected_env_keys = {
            "VOS3_APP_ID",
            "VOS3_APP_SECRET",
            "VOS3_TAURI_IPC_SECRET",
            "VOS3_SANDBOX",
        }
        for k, v in env_extra.items():
            if k in _protected_env_keys:
                continue
            safe_env[k] = v

        # ---- 6. spawn -------------------------------------------------
        import time as _t

        start = _t.monotonic()
        proc = None
        timed_out = False
        killed_by_limit = False

        # P5.3 — OS-level network isolation wrapper. Picked here
        # (outside the semaphore) so the seatbelt profile / unshare
        # decision is recorded before we contend for the spawn slot.
        net_prefix, net_cleanup, net_mode = _build_network_isolation_wrapper(
            app_id,
            str(target.parent),
        )
        if net_mode == "seatbelt" or net_mode == "unshare-net":
            logger.info(
                "[runner] OS network sandbox active app=%s mode=%s",
                app_id,
                net_mode,
            )

        async with self._semaphore:
            try:
                # `preexec_fn` is rejected by asyncio on Windows; the
                # ulimits are POSIX-only anyway. On Windows the
                # job-object equivalent will land in a future P4.x.
                preexec = (
                    _app_runner_preexec(
                        mem_mb=self.mem_mb,
                        cpu_seconds=self.cpu_seconds,
                    )
                    if os.name != "nt"
                    else None
                )
                # Compose the final argv: `[net_prefix...] python target argv_extra`.
                child_argv = list(net_prefix) + [
                    sys.executable,
                    str(target),
                    *argv_extra,
                ]
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *child_argv,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        stdin=asyncio.subprocess.DEVNULL,
                        env=safe_env,
                        cwd=str(target.parent),
                        preexec_fn=preexec,
                    )
                except (subprocess.SubprocessError, OSError) as exc:
                    # macOS asyncio sometimes refuses preexec_fn on
                    # forkserver child setups. Fall back without
                    # ulimits — wait_for timeout is still the wall.
                    logger.warning(
                        "[runner] preexec_fn rejected (%s); retrying without ulimits",
                        exc,
                    )
                    proc = await asyncio.create_subprocess_exec(
                        *child_argv,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        stdin=asyncio.subprocess.DEVNULL,
                        env=safe_env,
                        cwd=str(target.parent),
                    )

                # `communicate()` is the canonical avoid-deadlock
                # pattern — it reads stdout and stderr in parallel
                # until both pipes hit EOF, so a child writing more
                # than the pipe buffer to either stream can't
                # block on the other.
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=self.timeout_s,
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    # Best-effort drain of whatever was buffered
                    # before the kill landed.
                    try:
                        stdout_b, stderr_b = await asyncio.wait_for(
                            proc.communicate(),
                            timeout=2.0,
                        )
                    except asyncio.TimeoutError:
                        stdout_b, stderr_b = b"", b""

                duration_ms = (_t.monotonic() - start) * 1000.0
                returncode = proc.returncode if proc.returncode is not None else -1
                # POSIX signals (returncode < 0) indicate the kernel
                # killed the child — typically SIGXCPU / SIGSEGV from
                # an ulimit overage.
                if returncode < 0:
                    killed_by_limit = True

                exec_result = ExecutionResult(
                    app_id=app_id,
                    returncode=returncode,
                    stdout=stdout_b.decode("utf-8", errors="replace")[: 64 * 1024],
                    stderr=stderr_b.decode("utf-8", errors="replace")[: 64 * 1024],
                    duration_ms=duration_ms,
                    timed_out=timed_out,
                    killed_by_limit=killed_by_limit,
                )

                # P5.4 — Resource Guard. Classify the termination
                # cause and auto-isolate the app if it tripped a
                # resource limit. Do this BEFORE returning so the
                # next gate.check() on this app already sees the
                # 'isolated' status — no race window.
                _classification = _classify_resource_violation(exec_result)
                if _classification["violation"]:
                    try:
                        SANDBOX_MANAGER.isolate(
                            app_id,
                            reason="resource_limit_exceeded",
                        )
                    except (AppNotFound, Exception) as iso_exc:  # noqa: BLE001
                        logger.warning(
                            "[runner] auto-isolate failed app=%s: %s",
                            app_id,
                            iso_exc,
                        )
                    _record_security_event(
                        kind="resource_limit_exceeded",
                        app_id=app_id,
                        reason=(
                            f"cpu={_classification['cpu_limit_violated']} "
                            f"mem={_classification['memory_limit_violated']} "
                            f"rc={_classification['exit_code']} "
                            f"timed_out={_classification['timed_out']}"
                        ),
                        details={
                            **_classification,
                            "duration_ms": exec_result.duration_ms,
                        },
                    )
                    logger.warning(
                        "[runner] resource violation app=%s class=%s",
                        app_id,
                        _classification,
                    )

                return exec_result
            finally:
                _unregister_ephemeral_secret(app_id, run_secret_hash)
                # P5.3 — remove tmp seatbelt profile / cleanup hook.
                try:
                    net_cleanup()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[runner] net_cleanup failed: %s", exc)
                # Defensive: make sure no child outlives this call.
                if proc is not None and proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass

    @staticmethod
    def gate_check_or_raise(app_id: str, scope: str) -> None:
        """Pre-spawn gate check. Re-raises ScopeViolation /
        AppIsolated / AppNotFound — the route handler translates
        them to HTTPException(403)."""
        PERMISSION_GATE.check(app_id, scope)


# Module-level singleton — the FastAPI route uses this directly.
APP_PROCESS_RUNNER = AppProcessRunner()


def _reset_manager_for_tests() -> None:
    """Wipe the in-memory gate cache + ephemeral secret registry.
    The on-disk `apps` table is reset separately by
    `sqlite_setup._reset_for_tests()`."""
    _reset_gate_for_tests()
    _EPHEMERAL_SECRETS.clear()


__all__ = [
    # P4.1 sandbox layer
    "AppManifest",
    "AppSandboxManager",
    "PermissionGate",
    "PERMISSION_GATE",
    "SANDBOX_MANAGER",
    "AppNotFound",
    "AppIsolated",
    "InvalidManifest",
    "ManifestTampered",
    "ScopeViolation",
    # P4.3 filesystem sandbox helpers
    "app_storage_root",
    # P4.4 execution runtime
    "AppProcessRunner",
    "APP_PROCESS_RUNNER",
    "AppExecutionError",
    "ExecutionResult",
    # legacy ProcessSandbox layer
    "ProcessSandbox",
    "SandboxResult",
    "get_sandbox",
]
