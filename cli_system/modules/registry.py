"""Agent Registry — Persistent agent listing with kernel dispatcher integration.

Command:
  vos3ctl agent list            Show all registered agents with status
  vos3ctl agent kill-all        Kill all agents (Syscall 497 via VBus)

Displays agent TIDs, slot bindings, capabilities, and current task state
by querying the kernel's dispatcher via AGENT_STATUS and SLOT_ENUMERATE
bridge commands.
"""

import logging

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

logger = logging.getLogger("vos3ctl.registry")

# Neon theme
NEON_CYAN = "bright_cyan"
NEON_MAGENTA = "bright_magenta"
NEON_GREEN = "green"
NEON_YELLOW = "yellow"
NEON_RED = "red"

# Agent states (from kernel dispatcher)
_STATE_COLORS = {
    "RUNNING":    NEON_GREEN,
    "ACTIVE":     NEON_GREEN,
    "IDLE":       NEON_CYAN,
    "BLOCKED":    NEON_YELLOW,
    "DORMANT":    NEON_YELLOW,
    "SUSPENDED":  NEON_YELLOW,
    "DEAD":       NEON_RED,
    "ZOMBIE":     NEON_RED,
}


def cmd_agent_list(client):
    """Show all registered agents from the kernel dispatcher."""
    console = Console()

    if not client.connected:
        if not client.connect():
            console.print("[bold red]Failed to connect to VBus[/]")
            return

    console.print(Panel(
        f"[{NEON_CYAN}]Agent Registry[/] — Kernel Dispatcher View",
        border_style=NEON_MAGENTA,
    ))

    # Query dispatcher status for agent list
    agents = []
    try:
        resp = client.send_command("AGENT_STATUS")
        if resp.startswith("OK|"):
            agents = _parse_agent_status(resp)
    except Exception as e:
        logger.debug("AGENT_STATUS failed: %s", e)

    # Also get slot bindings for cross-reference
    slots = {}
    try:
        slot_list = client.driver.slot_enumerate()
        for s in slot_list:
            sid = s.get("slot_id")
            if sid is not None:
                slots[sid] = s
    except Exception:
        pass

    if not agents:
        console.print(f"  [dim]No agents registered in kernel dispatcher.[/dim]")
        console.print(f"  [dim]Use AGENT_STATUS bridge command to check.[/dim]")

        # Fallback: show slot-based view
        if slots:
            _show_slot_agents(console, slots)
        return

    # Build agent table
    table = Table(
        border_style=NEON_CYAN,
        show_header=True,
        header_style=f"bold {NEON_MAGENTA}",
        title=f"[{NEON_CYAN}]Registered Agents[/]",
        expand=True,
    )
    table.add_column("TID", style=NEON_CYAN, width=6, justify="right")
    table.add_column("Slot", width=6, justify="center")
    table.add_column("State", width=12)
    table.add_column("Caps", width=12)
    table.add_column("Queue", width=8, justify="right")
    table.add_column("Work Done", style="dim", width=10, justify="right")

    for agent in agents:
        tid = agent.get("tid", "?")
        slot = agent.get("slot_id", "-")
        state = agent.get("state", "UNKNOWN")
        caps = agent.get("capabilities", "0x0")
        queue_depth = agent.get("queue_depth", 0)
        work_done = agent.get("work_completed", 0)

        state_color = _STATE_COLORS.get(state.upper(), "dim")
        state_fmt = f"[{state_color}]{state}[/]"

        table.add_row(
            str(tid), str(slot), state_fmt,
            str(caps), str(queue_depth), str(work_done),
        )

    console.print(table)
    console.print(f"  [{NEON_CYAN}]Total agents:[/] {len(agents)}")


def cmd_agent_kill_all(client, force: bool = False):
    """Kill all registered agents via AGENT_KILL_ALL bridge command."""
    console = Console()

    if not client.connected:
        if not client.connect():
            console.print("[bold red]Failed to connect to VBus[/]")
            return

    if not force:
        confirm = console.input(
            f"  [{NEON_RED}]Kill ALL agents? Type 'KILL' to confirm:[/] "
        )
        if confirm.strip() != "KILL":
            console.print(f"  [{NEON_CYAN}]Aborted.[/]")
            return

    try:
        resp = client.send_command("AGENT_KILL_ALL")
        console.print(Panel(
            f"[{NEON_GREEN}]AGENT_KILL_ALL:[/] {resp}",
            border_style=NEON_GREEN,
            title="[bold]Agents Terminated[/]"
        ))
    except Exception as e:
        console.print(f"  [{NEON_RED}]AGENT_KILL_ALL failed:[/] {e}")


def _show_slot_agents(console, slots: dict):
    """Fallback: show slot-based agent view when AGENT_STATUS is unavailable."""
    table = Table(
        border_style=NEON_CYAN,
        show_header=True,
        header_style=f"bold {NEON_MAGENTA}",
        title=f"[{NEON_CYAN}]Slot-Based Agent View[/] [dim](AGENT_STATUS unavailable)[/dim]",
        expand=True,
    )
    table.add_column("Slot", style=NEON_CYAN, width=6, justify="center")
    table.add_column("State", width=12)
    table.add_column("Model ID", width=10, justify="right")
    table.add_column("Label", width=30)
    table.add_column("Owner TID", width=10, justify="right")

    active = 0
    for sid in sorted(slots.keys()):
        s = slots[sid]
        state = s.get("state", "EMPTY")
        if state in ("EMPTY", "FREE", ""):
            continue
        active += 1
        state_color = _STATE_COLORS.get(state.upper(), "dim")
        table.add_row(
            str(sid),
            f"[{state_color}]{state}[/]",
            str(s.get("model_id", "-")),
            s.get("label", "").strip("\x00").strip() or "-",
            str(s.get("owner_tid", "-")),
        )

    console.print(table)
    console.print(f"  [{NEON_CYAN}]Active slots:[/] {active}/{len(slots)}")


def _parse_agent_status(resp: str) -> list:
    """Parse AGENT_STATUS response into a list of agent dicts.

    Expected format: OK|tid:slot:state:caps:queue:work|tid:slot:...
    """
    agents = []
    raw = resp[3:]  # Strip "OK|"
    for segment in raw.split("|"):
        parts = segment.split(":")
        if len(parts) < 4:
            continue
        try:
            agent = {
                "tid": int(parts[0]),
                "slot_id": int(parts[1]) if parts[1] != "-" else None,
                "state": parts[2],
                "capabilities": parts[3] if len(parts) > 3 else "0x0",
                "queue_depth": int(parts[4]) if len(parts) > 4 else 0,
                "work_completed": int(parts[5]) if len(parts) > 5 else 0,
            }
            agents.append(agent)
        except (ValueError, IndexError):
            continue
    return agents
