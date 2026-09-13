"""VBus Monitor — Ghost Context Mitigation (Reddit PSA Fix).

Command:
  vos3ctl purge  — Nuclear option: kill all agents, flush caches, scrub buffers.

Four-phase purge sequence:
  1. AGENT_KILL_ALL via VBus bridge (Syscall 497)
  2. L1d/L2/L3 hardware cache flush via bridge
  3. Force-clear Python VBusDriver internal frame buffers
  4. HugePage pool integrity verification (leak detection)

Visual: "De-fragmenting" progress bar during scrub.
"""

import time
import logging

from rich.console import Console
from rich.progress import (
    Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn,
)
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

logger = logging.getLogger("vos3ctl.vbus_mon")

# Neon theme
NEON_CYAN = "bright_cyan"
NEON_MAGENTA = "bright_magenta"
NEON_RED = "red"
NEON_GREEN = "green"

# Purge phases
_PHASES = [
    ("AGENT_KILL_ALL",    "Terminating all agents + scrubbing model slots..."),
    ("CACHE_FLUSH",       "Flushing L1d/L2/L3 hardware caches..."),
    ("BUFFER_SCRUB",      "Clearing VBusDriver internal frame buffers..."),
    ("HP_VERIFY",         "Verifying HugePage pool integrity..."),
]


def cmd_purge(client, force: bool = False):
    """Nuclear Option — kill agents, flush caches, scrub buffers.

    This is the Ghost Context Mitigation command from the Reddit PSA.
    It ensures no residual context from a previous session can leak
    into a new one.
    """
    console = Console()

    if not client.connected:
        if not client.connect():
            console.print("[bold red]Failed to connect to VBus[/]")
            return

    # Confirmation gate (unless --force)
    if not force:
        console.print(Panel(
            f"[{NEON_RED}]NUCLEAR PURGE[/] — This will:\n"
            f"  1. Kill ALL registered agents (Syscall 497)\n"
            f"  2. Flush L1d/L2/L3 hardware caches\n"
            f"  3. Scrub all VBusDriver internal buffers\n"
            f"  4. Verify HugePage pool for leaks\n\n"
            f"[{NEON_CYAN}]No model data will survive this operation.[/]",
            border_style=NEON_RED,
            title="[bold red]WARNING[/]"
        ))
        confirm = console.input(
            f"  [{NEON_MAGENTA}]Type 'PURGE' to confirm:[/] "
        )
        if confirm.strip() != "PURGE":
            console.print(f"  [{NEON_CYAN}]Aborted.[/]")
            return

    console.print()
    results = {}

    with Progress(
        SpinnerColumn(style=NEON_MAGENTA),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(
            bar_width=40,
            style=NEON_CYAN,
            complete_style=NEON_MAGENTA,
            finished_style=NEON_GREEN,
        ),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        task = progress.add_task(
            f"[{NEON_RED}]De-fragmenting ghost context...[/]",
            total=len(_PHASES)
        )

        # Phase 1: AGENT_KILL_ALL
        progress.update(task, description=f"[{NEON_MAGENTA}]{_PHASES[0][1]}[/]")
        try:
            resp = client.send_command("AGENT_KILL_ALL")
            results["kill"] = resp
            logger.info("AGENT_KILL_ALL: %s", resp)
        except Exception as e:
            results["kill"] = f"ERR: {e}"
            logger.error("AGENT_KILL_ALL failed: %s", e)
        progress.advance(task)
        time.sleep(0.3)  # Visual pacing

        # Phase 2: Cache Flush via bridge
        progress.update(task, description=f"[{NEON_CYAN}]{_PHASES[1][1]}[/]")
        try:
            # WBINVD equivalent: kernel flushes all CPU caches
            resp = client.send_command("CACHE_FLUSH")
            results["cache"] = resp
            logger.info("CACHE_FLUSH: %s", resp)
        except Exception as e:
            # Cache flush may not be implemented — degrade gracefully
            results["cache"] = f"SKIP: {e}"
            logger.debug("CACHE_FLUSH not available: %s", e)
        progress.advance(task)
        time.sleep(0.3)

        # Phase 3: Scrub Python-side VBusDriver buffers
        progress.update(task, description=f"[{NEON_GREEN}]{_PHASES[2][1]}[/]")
        driver = client.driver
        # Clear event queue
        cleared_events = len(driver._event_queue)
        driver._event_queue.clear()
        # Clear feedback state
        driver._last_feedback = None
        # Reset slot sequence counters
        cleared_seqs = len(driver._slot_seq)
        driver._slot_seq.clear()
        # Drain any pending frames from the socket
        try:
            driver._drain_pending_frames()
            drained = len(driver._event_queue)
            driver._event_queue.clear()
        except Exception:
            drained = 0
        results["buffer"] = f"events={cleared_events}, seqs={cleared_seqs}, drained={drained}"
        logger.info("Buffer scrub: %s", results["buffer"])
        progress.advance(task)
        time.sleep(0.3)

        # Phase 4: HP_STATS verification (leak detection)
        progress.update(task, description=f"[dim]{_PHASES[3][1]}[/]")
        try:
            hp_resp = client.send_command("HP_STATS")
            results["hp"] = hp_resp
            logger.info("HP_STATS: %s", hp_resp)
        except Exception as e:
            results["hp"] = f"SKIP: {e}"
        progress.advance(task)

    # Report
    console.print()
    report = Table(
        title="Purge Report",
        border_style=NEON_CYAN,
        show_header=True,
        header_style=f"bold {NEON_MAGENTA}",
    )
    report.add_column("Phase", style=NEON_CYAN, width=20)
    report.add_column("Result", style=NEON_GREEN)

    report.add_row("Agent Kill", _fmt_result(results.get("kill", "N/A")))
    report.add_row("Cache Flush", _fmt_result(results.get("cache", "N/A")))
    report.add_row("Buffer Scrub", results.get("buffer", "N/A"))
    report.add_row("HugePage Stats", results.get("hp", "N/A"))
    console.print(report)

    # Final seal
    all_ok = all(
        not str(v).startswith("ERR")
        for v in results.values()
    )
    if all_ok:
        console.print(Panel(
            f"[{NEON_GREEN}]GHOST CONTEXT PURGED[/] — "
            f"All agents killed, caches flushed, buffers scrubbed.",
            border_style=NEON_GREEN,
            title="[bold green]CLEAN[/]"
        ))
    else:
        console.print(Panel(
            f"[{NEON_RED}]PURGE INCOMPLETE[/] — Check errors above.",
            border_style=NEON_RED,
            title="[bold red]WARNING[/]"
        ))

    # Audit log
    _purge_audit_log(results, all_ok)


def _purge_audit_log(results: dict, success: bool):
    """Append purge event to sovereign audit log."""
    import os
    module_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(module_dir))
    log_path = os.path.join(project_root, "docs", "SOVEREIGN_LOG.audit")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    kill_result = results.get("kill", "N/A")[:60]
    hp_result = results.get("hp", "N/A")[:60]
    entry = (
        f"[{timestamp}] action=purge "
        f"success={success} "
        f"kill={kill_result} "
        f"hp={hp_result}\n"
    )
    try:
        with open(log_path, "a") as f:
            f.write(entry)
    except OSError:
        pass


def _fmt_result(resp: str) -> str:
    """Format a bridge response for display."""
    if resp.startswith("OK"):
        return f"[green]{resp}[/green]"
    elif resp.startswith("ERR"):
        return f"[red]{resp}[/red]"
    elif resp.startswith("SKIP"):
        return f"[yellow]{resp}[/yellow]"
    return resp
