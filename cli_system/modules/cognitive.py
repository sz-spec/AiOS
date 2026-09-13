"""Cognitive Debugger — Entropy Heatmap & Real-Time Event Streaming.

Commands:
  vos3ctl inspect <slot_id>  — TUI entropy heatmap of context pages
  vos3ctl watch  <slot_id>   — Live VBus event stream (inference vs syscall)

Neon Theme: Cyan (#00FFFF) / Magenta (#FF00FF) palette.
"""

import time
import struct
import logging
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich.columns import Columns

logger = logging.getLogger("vos3ctl.cognitive")

# Neon theme colors
NEON_CYAN = "bright_cyan"
NEON_MAGENTA = "bright_magenta"
NEON_GREEN = "green"
NEON_YELLOW = "yellow"
NEON_RED = "red"
NEON_WHITE = "white"

# Entropy heatmap gradient — maps distinct-byte ratio to color
# Matches kernel Centurion Filter: <=4 distinct per 64B = noise (6.25%)
_GRADIENT = [
    (0.00, 0.0625, "bright_green",   "\u2591"),  # Low entropy — noise/padding
    (0.0625, 0.25,  "green",         "\u2592"),  # Sub-quarter
    (0.25,  0.50,   "yellow",        "\u2593"),  # Mid-range
    (0.50,  0.75,   "red",           "\u2588"),  # High density
    (0.75,  1.01,   "bright_red",    "\u2588"),  # Saturated
]

# VBus event codes (from kernel virtio_vbus.h)
EVENT_INFERENCE_START = 0x10
EVENT_INFERENCE_DONE  = 0x11
EVENT_SYSCALL         = 0x20
EVENT_CONTEXT_THAW    = 0x30
EVENT_CONTEXT_FREEZE  = 0x31
EVENT_SLOT_SWAP       = 0x40
EVENT_HEARTBEAT       = 0x50

_EVENT_NAMES = {
    EVENT_INFERENCE_START: ("INF_START", NEON_MAGENTA),
    EVENT_INFERENCE_DONE:  ("INF_DONE",  NEON_MAGENTA),
    EVENT_SYSCALL:         ("SYSCALL",   NEON_CYAN),
    EVENT_CONTEXT_THAW:    ("CTX_THAW",  NEON_GREEN),
    EVENT_CONTEXT_FREEZE:  ("CTX_FREEZE", NEON_GREEN),
    EVENT_SLOT_SWAP:       ("SLOT_SWAP", NEON_YELLOW),
    EVENT_HEARTBEAT:       ("HEARTBEAT", "dim"),
}


def _entropy_color(ratio: float) -> tuple:
    """Map distinct-byte ratio [0..1] to (color, block_char)."""
    for lo, hi, color, char in _GRADIENT:
        if lo <= ratio < hi:
            return color, char
    return "bright_red", "\u2588"


def _compute_block_entropy(data: bytes, block_size: int = 64) -> list:
    """Compute distinct-byte ratio per block (matches kernel Centurion Filter).

    Returns list of (block_index, distinct_ratio) tuples.
    """
    results = []
    for i in range(0, len(data), block_size):
        block = data[i:i + block_size]
        if len(block) == 0:
            continue
        distinct = len(set(block))
        ratio = distinct / 256.0  # Fraction of possible byte values
        results.append((i // block_size, ratio))
    return results


def cmd_inspect(client, slot_id: int, page_count: int = 16):
    """TUI visualization: Entropy Heatmap of context pages.

    Reads context pages from the given slot, computes per-block distinct-byte
    ratios (matching the kernel's Centurion Filter), and renders a color-coded
    heatmap with Green (low entropy/noise) to Red (high density/tokens).
    """
    console = Console()

    if not client.connected:
        if not client.connect():
            console.print("[bold red]Failed to connect to VBus[/]")
            return

    console.print(Panel(
        f"[{NEON_CYAN}]Cognitive Debugger[/] — Slot {slot_id} Entropy Heatmap",
        border_style=NEON_MAGENTA,
        subtitle=f"[dim]Centurion Filter: <=4 distinct/64B = noise (6.25%)[/dim]"
    ))

    # Read context pages via CTX_READ bridge command
    page_data_list = []
    for pg in range(page_count):
        try:
            resp = client.send_command(f"CTX_READ|{slot_id}|{pg}")
            if resp.startswith("OK|"):
                hex_data = resp.split("|", 2)[2] if resp.count("|") >= 2 else ""
                if hex_data:
                    page_data_list.append(bytes.fromhex(hex_data))
                else:
                    page_data_list.append(b"\x00" * 4096)
            else:
                page_data_list.append(b"\x00" * 4096)
        except Exception as e:
            logger.debug("CTX_READ page %d failed: %s", pg, e)
            page_data_list.append(b"\x00" * 4096)

    # Build heatmap grid — 64 blocks per page (4096 / 64)
    blocks_per_page = 4096 // 64
    heatmap_width = 64  # Blocks per row

    total_noise = 0
    total_blocks = 0

    console.print()
    console.print(f"  [{NEON_CYAN}]Page[/]  ", end="")
    # Column headers every 8 blocks
    for c in range(0, heatmap_width, 8):
        console.print(f"[dim]{c:>8}[/dim]", end="")
    console.print()
    console.print(f"  {'─' * 5}  {'─' * heatmap_width}", style="dim")

    for pg_idx, page_data in enumerate(page_data_list):
        entropy_blocks = _compute_block_entropy(page_data, 64)
        total_blocks += len(entropy_blocks)

        line = Text()
        line.append(f"  {pg_idx:>4}  ", style="dim")

        for blk_idx, ratio in entropy_blocks:
            color, char = _entropy_color(ratio)
            line.append(char, style=color)
            if ratio < 0.0625:  # Centurion noise threshold
                total_noise += 1

        console.print(line)

    # Summary stats
    noise_pct = (total_noise / total_blocks * 100) if total_blocks > 0 else 0
    dense_pct = 100.0 - noise_pct

    console.print()
    console.print(Panel(
        f"[{NEON_CYAN}]Blocks scanned:[/] {total_blocks}  "
        f"[{NEON_GREEN}]Noise (<=6.25%):[/] {total_noise} ({noise_pct:.1f}%)  "
        f"[{NEON_RED}]Dense:[/] {total_blocks - total_noise} ({dense_pct:.1f}%)",
        border_style=NEON_CYAN,
        title="[bold]Centurion Filter Summary[/]"
    ))

    # Legend
    legend = Text()
    legend.append("  Legend: ", style="bold")
    for lo, hi, color, char in _GRADIENT:
        pct = f"{lo*100:.0f}-{hi*100:.0f}%"
        legend.append(f" {char}{char} ", style=color)
        legend.append(f"{pct} ", style="dim")
    console.print(legend)
    console.print()


def cmd_watch(client, slot_id: int, duration: int = 30):
    """Real-time streaming of VBus events for a specific agent slot.

    Shows inference vs syscall frequency with Neon-themed live TUI.
    Streams for `duration` seconds (default 30).
    """
    console = Console()

    if not client.connected:
        if not client.connect():
            console.print("[bold red]Failed to connect to VBus[/]")
            return

    # Subscribe to events for this slot
    try:
        client.send_command(f"EVENT_SUBSCRIBE|0|{slot_id}")
    except Exception:
        pass  # Subscribe is best-effort — events may still arrive

    console.print(Panel(
        f"[{NEON_MAGENTA}]Event Monitor[/] — Slot {slot_id}  "
        f"[dim](streaming {duration}s, Ctrl-C to stop)[/dim]",
        border_style=NEON_CYAN
    ))

    # Counters
    inference_count = 0
    syscall_count = 0
    other_count = 0
    events_log = []  # Recent events for display
    max_log = 20

    start = time.monotonic()

    try:
        while (time.monotonic() - start) < duration:
            events = client.driver.collect_events(timeout=0.5)

            for ev in events:
                ev_slot = ev.get("slot_id", -1)
                if ev_slot != slot_id and ev_slot != 0xFF:
                    continue  # Filter to our slot

                code = ev.get("event_code", 0)
                value = ev.get("value", 0)
                ts = ev.get("timestamp", 0)

                name, color = _EVENT_NAMES.get(code, (f"0x{code:02X}", "dim"))

                if code in (EVENT_INFERENCE_START, EVENT_INFERENCE_DONE):
                    inference_count += 1
                elif code == EVENT_SYSCALL:
                    syscall_count += 1
                else:
                    other_count += 1

                elapsed = time.monotonic() - start
                entry = f"[dim]{elapsed:7.2f}s[/dim]  [{color}]{name:<12}[/]  val={value}"
                events_log.append(entry)
                if len(events_log) > max_log:
                    events_log.pop(0)

                console.print(entry)

            # Periodic frequency summary
            elapsed = time.monotonic() - start
            if elapsed > 0:
                inf_hz = inference_count / elapsed
                sys_hz = syscall_count / elapsed

                status = (
                    f"\r  [{NEON_MAGENTA}]INF: {inference_count} ({inf_hz:.1f}/s)[/]  "
                    f"[{NEON_CYAN}]SYS: {syscall_count} ({sys_hz:.1f}/s)[/]  "
                    f"[dim]Other: {other_count}[/dim]  "
                    f"[dim]{elapsed:.0f}/{duration}s[/dim]"
                )
                console.print(status, end="")

    except KeyboardInterrupt:
        pass

    # Unsubscribe
    try:
        client.send_command(f"EVENT_UNSUBSCRIBE|0")
    except Exception:
        pass

    # Final summary
    elapsed = time.monotonic() - start
    console.print()
    console.print(Panel(
        f"[{NEON_MAGENTA}]Inference events:[/] {inference_count}  "
        f"[{NEON_CYAN}]Syscall events:[/] {syscall_count}  "
        f"[dim]Other:[/dim] {other_count}  "
        f"[dim]Duration: {elapsed:.1f}s[/dim]",
        border_style=NEON_MAGENTA,
        title="[bold]Watch Summary[/]"
    ))
