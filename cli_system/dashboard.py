"""VOS3 CLI Dashboard — Cognitive Loop Latency Profiling.

Displays a real-time "Cognitive Loop" gauge showing the time delta between
VBUS_TYPE_CMD dispatch and its corresponding VBUS_TYPE_RESP, sourced from
CTX_LATENCY telemetry per slot.

Features:
  - Per-slot latency gauge with avg/P99 tracking
  - 500ms "Reasoning Stall" bright-red warning
  - Adaptive refresh: auto-degrades from 2Hz to 0.5Hz under VBus pressure

Usage:
  vos3ctl dashboard         — Full dashboard with all slots
  vos3ctl dashboard <slot>  — Single-slot focused view
"""

import time
import logging

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich.columns import Columns

logger = logging.getLogger("vos3ctl.dashboard")

# Neon theme
NEON_CYAN = "bright_cyan"
NEON_MAGENTA = "bright_magenta"
NEON_GREEN = "green"
NEON_YELLOW = "yellow"
NEON_RED = "red"

# Latency thresholds (microseconds) for gauge coloring
_LATENCY_GOOD = 1000       # < 1ms = green
_LATENCY_WARN = 5000       # < 5ms = yellow
_LATENCY_BAD  = 20000      # < 20ms = red
# >= 20ms = bright_red (critical)
_LATENCY_STALL = 500000    # >= 500ms = "Reasoning Stall" (blinking bright_red)

# Gauge bar width
_GAUGE_WIDTH = 30
_GAUGE_MAX_US = 50000  # 50ms max for gauge scale

# Adaptive refresh — jitter shield
_REFRESH_NORMAL = 2.0       # Normal: poll every 2s
_REFRESH_DEGRADED = 4.0     # Degraded: poll every 4s under pressure
_JITTER_THRESHOLD_MS = 50.0 # VBus ping > 50ms triggers degraded mode


def _latency_color(us: int) -> str:
    """Map latency in microseconds to a color."""
    if us < _LATENCY_GOOD:
        return NEON_GREEN
    elif us < _LATENCY_WARN:
        return NEON_YELLOW
    elif us < _LATENCY_BAD:
        return NEON_RED
    return "bright_red"


def _gauge_bar(us: int) -> Text:
    """Render a colored gauge bar for a latency value."""
    color = _latency_color(us)
    filled = min(int(us / _GAUGE_MAX_US * _GAUGE_WIDTH), _GAUGE_WIDTH)
    empty = _GAUGE_WIDTH - filled

    bar = Text()
    bar.append("\u2588" * filled, style=color)
    bar.append("\u2591" * empty, style="dim")
    bar.append(f" {us:>6} \u00b5s", style=color)
    return bar


def _format_slot_state(state_str: str) -> str:
    """Color-code slot state."""
    s = state_str.upper().strip()
    if s in ("ACTIVE", "RUNNING"):
        return f"[{NEON_GREEN}]{s}[/]"
    elif s in ("DORMANT", "SUSPENDED"):
        return f"[{NEON_YELLOW}]{s}[/]"
    elif s in ("EMPTY", "FREE"):
        return f"[dim]{s}[/dim]"
    elif s in ("ERROR", "FAULT"):
        return f"[{NEON_RED}]{s}[/]"
    return f"[{NEON_CYAN}]{s}[/]"


def cmd_dashboard(client, slot_id: int = -1, refresh: int = 2, duration: int = 60):
    """Live dashboard with Cognitive Loop latency gauge per slot.

    Polls CTX_LATENCY for each active slot every `refresh` seconds.
    Runs for `duration` seconds or until Ctrl-C.
    """
    console = Console()

    # Validate slot_id range (0–7 or -1 for all slots).
    if slot_id < -1 or slot_id > 7:
        console.print(f"[bold red]Invalid slot_id {slot_id} (must be -1 to 7)[/]")
        return

    if not client.connected:
        if not client.connect():
            console.print("[bold red]Failed to connect to VBus[/]")
            return

    console.print(Panel(
        f"[{NEON_CYAN}]VOS3 Command Center[/] — Cognitive Loop Dashboard\n"
        f"[dim]Dreadhead Standard: CMD\u2192RESP latency via CTX_LATENCY[/dim]\n"
        f"[dim]Refresh: {refresh}s | Duration: {duration}s | Ctrl-C to exit[/dim]",
        border_style=NEON_MAGENTA,
    ))

    start = time.monotonic()
    history = {}  # slot_id -> list of (timestamp, latency_us)
    adaptive_refresh = float(refresh)
    stall_slots = set()  # Slots currently in "Reasoning Stall"

    try:
        while (time.monotonic() - start) < duration:
            # Determine which slots to poll
            if slot_id >= 0:
                target_slots = [slot_id]
            else:
                target_slots = _discover_active_slots(client)

            # Ping RTT for adaptive refresh (jitter shield)
            try:
                rtt = client.ping()
                ping_text = f"[{NEON_GREEN}]{rtt:.1f} ms[/]"
                # Adaptive refresh: degrade if VBus is under pressure
                if rtt > _JITTER_THRESHOLD_MS:
                    adaptive_refresh = min(adaptive_refresh * 1.5, _REFRESH_DEGRADED)
                    ping_text = f"[{NEON_YELLOW}]{rtt:.1f} ms (degraded)[/]"
                else:
                    # Recover toward normal rate
                    adaptive_refresh = max(adaptive_refresh * 0.8, float(refresh))
            except Exception:
                ping_text = f"[{NEON_RED}]TIMEOUT[/]"
                adaptive_refresh = _REFRESH_DEGRADED

            # Build the dashboard table
            mode_tag = "" if adaptive_refresh <= float(refresh) else f" [dim][{NEON_YELLOW}]DEGRADED[/][/dim]"
            table = Table(
                border_style=NEON_CYAN,
                show_header=True,
                header_style=f"bold {NEON_MAGENTA}",
                title=(
                    f"[{NEON_CYAN}]Cognitive Loop Gauge[/] "
                    f"[dim]({time.strftime('%H:%M:%S')} | "
                    f"refresh {adaptive_refresh:.1f}s)[/dim]{mode_tag}"
                ),
                expand=True,
            )
            table.add_column("Slot", style=NEON_CYAN, width=6, justify="center")
            table.add_column("State", width=12)
            table.add_column("Latency Gauge", min_width=45)
            table.add_column("Avg", style="dim", width=10, justify="right")
            table.add_column("P99", style="dim", width=10, justify="right")
            table.add_column("Samples", style="dim", width=8, justify="right")

            stall_slots.clear()
            for sid in target_slots:
                # Get slot state
                try:
                    status_resp = client.send_command(f"SLOT_STATUS|{sid}")
                    state = _parse_slot_state(status_resp)
                except Exception:
                    state = "UNKNOWN"

                # Get CMD->RESP latency
                try:
                    latency_us = client.driver.ctx_get_latency(sid)
                except Exception:
                    latency_us = 0

                # Track history
                if sid not in history:
                    history[sid] = []
                history[sid].append((time.monotonic(), latency_us))
                # Keep last 100 samples
                if len(history[sid]) > 100:
                    history[sid] = history[sid][-100:]

                samples = history[sid]
                values = [v for _, v in samples]
                avg_us = int(sum(values) / len(values)) if values else 0
                p99_us = _percentile(values, 99) if values else 0

                # Reasoning Stall detection: >= 500ms
                is_stall = latency_us >= _LATENCY_STALL
                if is_stall:
                    stall_slots.add(sid)

                gauge = _gauge_bar(latency_us)
                slot_label = f"[bold bright_red blink]{sid} STALL[/]" if is_stall else str(sid)
                table.add_row(
                    slot_label,
                    _format_slot_state(state),
                    gauge,
                    f"{avg_us} \u00b5s",
                    f"{p99_us} \u00b5s",
                    str(len(samples)),
                )

            # Session info
            session_text = (
                f"  [{NEON_CYAN}]VBus Ping:[/] {ping_text}  "
                f"[dim]| Epoch: {client.epoch or 'N/A'} "
                f"| Session: {client.session_age_s():.0f}s[/dim]"
            )

            console.clear()
            console.print(Panel(
                f"[{NEON_CYAN}]VOS3 Command Center[/] — Cognitive Loop Dashboard",
                border_style=NEON_MAGENTA,
            ))
            console.print(table)
            console.print(session_text)

            # Stall alert
            if stall_slots:
                console.print(Panel(
                    f"[bold bright_red]REASONING STALL[/] detected on "
                    f"slot(s) {', '.join(str(s) for s in sorted(stall_slots))} "
                    f"(\u2265 500ms CMD\u2192RESP)",
                    border_style="bright_red",
                ))

            # Legend
            legend = Text("  ")
            legend.append(f"\u2588\u2588 <{_LATENCY_GOOD//1000}ms ", style=NEON_GREEN)
            legend.append(f"\u2588\u2588 <{_LATENCY_WARN//1000}ms ", style=NEON_YELLOW)
            legend.append(f"\u2588\u2588 <{_LATENCY_BAD//1000}ms ", style=NEON_RED)
            legend.append(f"\u2588\u2588 >={_LATENCY_BAD//1000}ms ", style="bright_red")
            legend.append(f"  STALL \u2265500ms ", style="bold bright_red")
            console.print(legend)

            time.sleep(adaptive_refresh)

    except KeyboardInterrupt:
        pass

    console.print(f"\n  [{NEON_CYAN}]Dashboard stopped.[/]")


def _build_hardware_panel(client) -> Panel:
    """Hardware Surface panel — PCI device list from kernel."""
    table = Table(
        border_style=NEON_CYAN, show_header=True,
        header_style=f"bold {NEON_MAGENTA}",
        title=f"[{NEON_CYAN}]Hardware Surface[/] — PCI Bus Scan",
        expand=True,
    )
    table.add_column("Bus:Dev.Fn", style=NEON_CYAN, width=10)
    table.add_column("Vendor", width=8)
    table.add_column("Device", width=8)
    table.add_column("Class", width=8)
    table.add_column("Name", style=NEON_GREEN)

    try:
        import json
        resp = client.send_command("PCI_LIST")
        if resp.startswith("OK|"):
            devices = json.loads(resp[3:])
            for d in devices:
                bdf = f"{d.get('bus', 0):02x}:{d.get('dev', 0):02x}.{d.get('fn', 0)}"
                table.add_row(
                    bdf, d.get("vendor", "?"), d.get("device", "?"),
                    d.get("class", "?"), d.get("name", "unknown"),
                )
        else:
            table.add_row("[dim]—[/dim]", "", "", "", f"[{NEON_RED}]{resp}[/]")
    except Exception as e:
        table.add_row("[dim]—[/dim]", "", "", "", f"[{NEON_RED}]Error: {e}[/]")

    return Panel(table, border_style=NEON_MAGENTA)


def _build_integrity_panel(client) -> Panel:
    """System Integrity panel — KTEXT_HASH live check."""
    try:
        import json
        resp = client.send_command("KTEXT_HASH")
        if resp.startswith("OK|"):
            data = json.loads(resp[3:])
            boot_crc = data.get("boot_crc", "?")
            live_crc = data.get("live_crc", "?")
            match = data.get("match", False)
            if match:
                status = f"[{NEON_GREEN}]INTACT[/]"
                detail = f"CRC32C: {boot_crc} (boot) = {live_crc} (live)"
            else:
                status = f"[bold {NEON_RED}]TAMPERED[/]"
                detail = f"CRC32C: {boot_crc} (boot) != {live_crc} (live)"
        else:
            status = f"[{NEON_YELLOW}]UNAVAILABLE[/]"
            detail = resp
    except Exception as e:
        status = f"[{NEON_RED}]ERROR[/]"
        detail = str(e)

    import time as _time
    text = Text()
    text.append("Kernel .text Integrity: ")
    text.append_text(Text.from_markup(status))
    text.append(f"\n{detail}")
    text.append(f"\nLast check: {_time.strftime('%H:%M:%S')}")

    return Panel(text, title=f"[{NEON_CYAN}]System Integrity[/]", border_style=NEON_MAGENTA)


def cmd_hardware(client):
    """Display hardware surface and system integrity panels."""
    console = Console()

    if not client.connected:
        if not client.connect():
            console.print("[bold red]Failed to connect to VBus[/]")
            return

    console.print(Panel(
        f"[{NEON_CYAN}]VOS3 Command Center[/] — Hardware & Integrity",
        border_style=NEON_MAGENTA,
    ))
    console.print(_build_hardware_panel(client))
    console.print(_build_integrity_panel(client))


def _discover_active_slots(client) -> list:
    """Query kernel for active slot IDs."""
    try:
        slots = client.driver.slot_enumerate()
        return [s["slot_id"] for s in slots if s.get("state") not in ("EMPTY", "FREE", "")]
    except Exception:
        return list(range(8))  # Fallback: poll all 8 slots


def _parse_slot_state(resp: str) -> str:
    """Extract state string from SLOT_STATUS response."""
    # Response format: OK|ACTIVE or OK|DORMANT etc.
    if resp.startswith("OK|"):
        parts = resp.split("|")
        if len(parts) >= 2:
            return parts[1]
    return "UNKNOWN"


def cmd_throttle(client, slot_id: int, level: str = "low"):
    """Manually adjust a slot's scheduling priority via VBus.

    Levels:
      low    — reduce priority (NICE +10), fewer CPU cycles allocated
      normal — restore default priority (NICE 0)
      high   — boost priority (NICE -5), more CPU cycles allocated

    This is an operator-initiated action — the CLI never adjusts
    priority automatically to avoid unpredictable cascade effects.
    """
    console = Console()

    # Validate slot_id range (0–7).
    if slot_id < 0 or slot_id > 7:
        console.print(f"[bold red]Invalid slot_id {slot_id} (must be 0–7)[/]")
        return

    if not client.connected:
        if not client.connect():
            console.print("[bold red]Failed to connect to VBus[/]")
            return

    nice_map = {"low": 10, "normal": 0, "high": -5}
    nice_value = nice_map.get(level, 0)

    try:
        resp = client.send_command(f"SLOT_QUOTA|{slot_id}|{nice_value}")
        color = NEON_GREEN if resp.startswith("OK") else NEON_RED
        console.print(
            f"  [{color}]Slot {slot_id} throttle={level} (nice={nice_value}):[/] {resp}"
        )
    except Exception as e:
        console.print(f"  [{NEON_RED}]Throttle failed:[/] {e}")


def _percentile(values: list, pct: int) -> int:
    """Compute percentile from a list of values."""
    if not values:
        return 0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * pct / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]
