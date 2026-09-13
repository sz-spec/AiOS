"""Binary Integrity Verification — SHA-256 manifest + live UUID probe.

Commands:
  vos3ctl sys rescan    SHA-256 check + live BUILD_UUID desync detection

Two-layer verification:
  1. SHA-256 of on-disk vos3.elf vs certified build manifest
  2. BUILD_UUID probe: queries running kernel via VBus and compares
     against the UUID extracted from the host-side ELF binary
"""

import hashlib
import os
import re
import subprocess
import logging
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

logger = logging.getLogger("vos3ctl.integrity")

# Neon theme
NEON_CYAN = "bright_cyan"
NEON_MAGENTA = "bright_magenta"
NEON_GREEN = "green"
NEON_RED = "red"

# Known-good SHA-256 manifest — certified builds
_MANIFEST = {
    "v19.3": "2a78352753cd05c95d7511cc03bfb81c5d82dd6d02da8e9f7dfdd642e5487c61",
    "v19.4": "0c6512dd43592800d9ade11d6a77ac8debefbdff18e84552a674534987a8d669",
    "v19.5": "aedfdf15865430c82509260da09550aa93f6f02d1b452760fb6d01bf62901cf4",
    "v19.8": "5f9c15487f49347c39b7680859e6b592c56f8a02d0c6e52a0362a2ebb8766050",
    # gold_v1 removed: prefix-only hash is insecure (collision-prone)
    "gold_v2": "e8bbcb95111ef24161bfc13cec5d391ad3d5c4efc02f647c2bc92eb7f81ae15f",
    "gold_v3": "92c9c4489f2afe6a054b0d6d8bd148de764ba74730b018eeb1c7864c09780e18",
}

# Audit log path (relative to project root)
_AUDIT_LOG = "docs/SOVEREIGN_LOG.audit"

# Default kernel binary path (relative to project root)
_DEFAULT_ELF = "kernel/build/vos3.elf"


def cmd_sys_rescan(client, kernel_path: str = None):
    """Verify kernel binary integrity against known-good SHA-256 manifest.

    Computes SHA-256 of the current vos3.elf and checks it against the
    certified build manifest. Reports match/mismatch with version info.
    """
    console = Console()

    # Resolve kernel path — __file__ is cli_system/modules/integrity.py,
    # so project root is 3 levels up (modules -> cli_system -> VOS3)
    if kernel_path is None:
        module_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(module_dir))
        kernel_path = os.path.join(project_root, _DEFAULT_ELF)

    console.print(Panel(
        f"[{NEON_CYAN}]Binary Integrity Scan[/]\n"
        f"[dim]Target: {kernel_path}[/dim]",
        border_style=NEON_MAGENTA,
    ))

    # Check file exists
    if not os.path.isfile(kernel_path):
        console.print(f"  [{NEON_RED}]MISSING[/] — {kernel_path} not found")
        console.print(f"  [dim]Run 'cd kernel && make clean && make' to rebuild.[/dim]")
        return

    # Compute SHA-256
    sha = _sha256_file(kernel_path)
    file_size = os.path.getsize(kernel_path)
    console.print(f"  [{NEON_CYAN}]SHA-256:[/] {sha}")
    console.print(f"  [{NEON_CYAN}]Size:[/]   {file_size:,} bytes")

    # Check against manifest
    matched_version = None
    for version, known_hash in _MANIFEST.items():
        if sha == known_hash:
            matched_version = version
            break

    if matched_version:
        console.print(Panel(
            f"[{NEON_GREEN}]INTEGRITY VERIFIED[/] — matches certified build "
            f"[bold]{matched_version}[/]",
            border_style=NEON_GREEN,
            title="[bold green]CLEAN[/]"
        ))
    else:
        console.print(Panel(
            f"[{NEON_RED}]SILENT DESYNC DETECTED[/]\n\n"
            f"Binary SHA-256 does not match any certified build.\n"
            f"This kernel may have been modified without re-certification.\n\n"
            f"[{NEON_CYAN}]Actions:[/]\n"
            f"  1. Rebuild: cd kernel && make clean && make\n"
            f"  2. Re-certify: record new SHA in manifest\n"
            f"  3. Investigate: compare with last known-good binary",
            border_style=NEON_RED,
            title="[bold red]DESYNC[/]"
        ))

    # Show manifest
    console.print()
    table = Table(
        title="Certified Build Manifest",
        border_style=NEON_CYAN,
        show_header=True,
        header_style=f"bold {NEON_MAGENTA}",
    )
    table.add_column("Version", style=NEON_CYAN, width=12)
    table.add_column("SHA-256", width=68)
    table.add_column("Match", width=8, justify="center")

    for version, known_hash in _MANIFEST.items():
        is_match = (sha == known_hash or sha.startswith(known_hash))
        match_str = f"[{NEON_GREEN}]\u2714[/]" if is_match else "[dim]\u2718[/dim]"
        hash_style = NEON_GREEN if is_match else "dim"
        table.add_row(version, f"[{hash_style}]{known_hash}[/]", match_str)

    console.print(table)

    # Phase 2: Live BUILD_UUID probe (requires VBus connection)
    _uuid_probe(client, kernel_path, console)

    # Append to audit log
    _audit_log("rescan", sha, matched_version, kernel_path)


def _uuid_probe(client, kernel_path: str, console: Console):
    """Compare running kernel's BUILD_UUID against the host-side ELF binary.

    The kernel embeds a compile-time string (vos3_build_uuid = __DATE__ " " __TIME__)
    in .rodata. We extract it from the ELF with strings/grep, then query the
    running kernel via the BUILD_UUID VBus command. A mismatch means the running
    kernel doesn't match the binary on disk.
    """
    console.print()

    # Extract UUID from host-side ELF binary (.rodata contains the date+time string)
    host_uuid = _extract_elf_build_uuid(kernel_path)
    if host_uuid is None:
        console.print(f"  [dim]UUID probe: could not extract BUILD_UUID from ELF[/dim]")
        return

    console.print(f"  [{NEON_CYAN}]ELF UUID:[/]    {host_uuid}")

    # Query running kernel
    if not client.connected:
        console.print(f"  [dim]UUID probe: not connected to VBus (offline check only)[/dim]")
        return

    try:
        resp = client.send_command("BUILD_UUID")
        if resp.startswith("OK|"):
            kernel_uuid = resp[3:].strip()
        else:
            console.print(f"  [dim]UUID probe: kernel returned {resp}[/dim]")
            return
    except Exception as e:
        console.print(f"  [dim]UUID probe: VBus query failed ({e})[/dim]")
        return

    console.print(f"  [{NEON_CYAN}]Kernel UUID:[/] {kernel_uuid}")

    if host_uuid == kernel_uuid:
        console.print(Panel(
            f"[{NEON_GREEN}]BINARY SYNCED[/] — disk ELF matches running kernel",
            border_style=NEON_GREEN,
        ))
    else:
        console.print(Panel(
            f"[{NEON_RED}]BINARY DESYNC[/]\n\n"
            f"Disk ELF:      {host_uuid}\n"
            f"Running kernel: {kernel_uuid}\n\n"
            f"The kernel binary on disk was rebuilt but QEMU is still running\n"
            f"the previous version. Restart QEMU to load the new binary.",
            border_style=NEON_RED,
            title="[bold red]DESYNC[/]"
        ))


def _extract_elf_build_uuid(elf_path: str) -> str:
    """Extract the vos3_build_uuid string from the ELF .rodata section.

    Looks for a date+time pattern like 'Apr  7 2026 16:30:45' in the binary.
    """
    # Read the binary and search for the date+time pattern
    # Format: "Mon DD YYYY HH:MM:SS" (from __DATE__ " " __TIME__)
    date_pattern = re.compile(
        rb'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
        rb'\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}'
    )
    try:
        import mmap as mmap_mod
        with open(elf_path, "rb") as f:
            # Memory-map the file instead of reading the entire ELF into RAM.
            data = mmap_mod.mmap(f.fileno(), 0, access=mmap_mod.ACCESS_READ)
        # Find matches in .rodata region (after first MB typically)
        matches = list(date_pattern.finditer(data))
        if matches:
            # The vos3_build_uuid is the last date+time string in the binary
            # (it's in .rodata, which comes after .text)
            match = matches[-1]
            return match.group(0).decode("ascii")
    except Exception as e:
        logger.debug("ELF UUID extraction failed: %s", e)
    return None


def _audit_log(action: str, sha: str, version: str, kernel_path: str):
    """Append an entry to the sovereign audit log."""
    module_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(module_dir))
    log_path = os.path.join(project_root, _AUDIT_LOG)

    # Ensure docs/ directory exists
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    entry = (
        f"[{timestamp}] action={action} "
        f"sha256={sha} "
        f"version={version or 'UNKNOWN'} "
        f"binary={os.path.basename(kernel_path)}\n"
    )

    try:
        with open(log_path, "a") as f:
            f.write(entry)
        logger.debug("Audit log: %s", entry.strip())
    except OSError as e:
        logger.warning("Could not write audit log: %s", e)


def _sha256_file(path: str) -> str:
    """Compute SHA-256 of a file, reading in 64K chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
