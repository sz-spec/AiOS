#!/usr/bin/env python3
"""VOS3 Command Center — vos3ctl entry point (v19.8).

Sovereign CLI for the VOS3 AI Operating System.
Neon Theme: Cyan (#00FFFF) / Magenta (#FF00FF).

Commands:
  vos3ctl inspect <slot_id>           Entropy heatmap of context pages
  vos3ctl watch   <slot_id>           Real-time VBus event stream
  vos3ctl purge   [--force]           Nuclear option — kill agents, flush caches
  vos3ctl dashboard [slot_id]         Cognitive Loop latency gauge (adaptive refresh)
  vos3ctl throttle <slot_id> [level]  Manual slot priority adjustment
  vos3ctl agent list                  Show registered agents from dispatcher
  vos3ctl agent kill-all [--force]    Kill all agents (Syscall 497)
  vos3ctl sys rescan [--kernel PATH]  Binary integrity + BUILD_UUID desync probe
  vos3ctl fortress [--interval SEC]   Silicon Fortress real-time security monitor
  vos3ctl ping                        VBus round-trip test
  vos3ctl status                      System overview
"""

import argparse
import sys
import os
import logging

# Ensure backend/ is on the path for VBusDriver imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from rich.console import Console
from rich.panel import Panel

from cli_system.client import VOS3Client, SessionEpochError
from cli_system.modules.cognitive import cmd_inspect, cmd_watch
from cli_system.modules.vbus_mon import cmd_purge
from cli_system.modules.registry import cmd_agent_list, cmd_agent_kill_all
from cli_system.modules.integrity import cmd_sys_rescan
from cli_system.dashboard import cmd_dashboard, cmd_throttle

# Neon theme
NEON_CYAN = "bright_cyan"
NEON_MAGENTA = "bright_magenta"
NEON_GREEN = "green"

_BANNER = f"""[{NEON_MAGENTA}]
 ██╗   ██╗ ██████╗ ███████╗██████╗      ██████╗████████╗██╗
 ██║   ██║██╔═══██╗██╔════╝╚════██╗    ██╔════╝╚══██╔══╝██║
 ██║   ██║██║   ██║███████╗ █████╔╝    ██║        ██║   ██║
 ╚██╗ ██╔╝██║   ██║╚════██║ ╚═══██╗    ██║        ██║   ██║
  ╚████╔╝ ╚██████╔╝███████║██████╔╝    ╚██████╗   ██║   ███████╗
   ╚═══╝   ╚═════╝ ╚══════╝╚═════╝     ╚═════╝   ╚═╝   ╚══════╝
[/][{NEON_CYAN}]            Sovereign Command Center v19.8[/]"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vos3ctl",
        description="VOS3 Sovereign Command Center (v19.8)",
    )
    parser.add_argument(
        "--socket", default="/tmp/vos3_bridge.sock",
        help="VBus socket path (default: /tmp/vos3_bridge.sock)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging"
    )

    sub = parser.add_subparsers(dest="command")

    # inspect
    p_inspect = sub.add_parser("inspect", help="Entropy heatmap of context pages")
    p_inspect.add_argument("slot_id", type=int, help="Slot ID (0-7)")
    p_inspect.add_argument("--pages", type=int, default=16, help="Pages to scan (default: 16)")

    # watch
    p_watch = sub.add_parser("watch", help="Real-time VBus event stream")
    p_watch.add_argument("slot_id", type=int, help="Slot ID (0-7)")
    p_watch.add_argument("--duration", type=int, default=30, help="Duration in seconds (default: 30)")

    # purge
    p_purge = sub.add_parser("purge", help="Nuclear option — kill agents, flush caches")
    p_purge.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Cognitive Loop latency gauge")
    p_dash.add_argument("slot_id", type=int, nargs="?", default=-1, help="Slot ID (default: all)")
    p_dash.add_argument("--refresh", type=int, default=2, help="Refresh interval in seconds (default: 2)")
    p_dash.add_argument("--duration", type=int, default=60, help="Duration in seconds (default: 60)")

    # agent (subcommand group)
    p_agent = sub.add_parser("agent", help="Agent registry commands")
    agent_sub = p_agent.add_subparsers(dest="agent_command")
    agent_sub.add_parser("list", help="Show registered agents")
    p_agent_kill = agent_sub.add_parser("kill-all", help="Kill all agents (Syscall 497)")
    p_agent_kill.add_argument("--force", action="store_true", help="Skip confirmation")

    # sys (subcommand group)
    p_sys = sub.add_parser("sys", help="System integrity commands")
    sys_sub = p_sys.add_subparsers(dest="sys_command")
    p_rescan = sys_sub.add_parser("rescan", help="Binary integrity verification")
    p_rescan.add_argument("--kernel", type=str, default=None,
                          help="Path to vos3.elf (default: kernel/build/vos3.elf)")

    # throttle
    p_throttle = sub.add_parser("throttle", help="Adjust slot scheduling priority")
    p_throttle.add_argument("slot_id", type=int, help="Slot ID (0-7)")
    p_throttle.add_argument("level", choices=["low", "normal", "high"],
                            default="low", nargs="?",
                            help="Priority level (default: low)")

    # fortress
    p_fortress = sub.add_parser("fortress", help="Silicon Fortress real-time monitor")
    p_fortress.add_argument("--interval", type=float, default=2.0,
                            help="Refresh interval in seconds (default: 2.0)")

    # ping
    sub.add_parser("ping", help="VBus round-trip test")

    # status
    sub.add_parser("status", help="System overview")

    return parser


def _cmd_ping(client):
    """Simple VBus ping with RTT display."""
    console = Console()
    try:
        rtt = client.ping()
        console.print(f"  [{NEON_GREEN}]PONG[/] — {rtt:.2f} ms")
    except Exception as e:
        console.print(f"  [red]PING FAILED[/] — {e}")


def _cmd_status(client):
    """System overview: SMP, HugePages, VBus, slots."""
    console = Console()

    try:
        info = client.send_command("SYSINFO")
        console.print(Panel(
            f"[{NEON_CYAN}]Kernel:[/] {info}",
            border_style=NEON_CYAN,
            title="[bold]System Info[/]"
        ))
    except Exception as e:
        console.print(f"  [dim]SYSINFO: {e}[/dim]")

    try:
        smp = client.send_command("SMP_STATUS")
        console.print(f"  [{NEON_CYAN}]SMP:[/] {smp}")
    except Exception:
        pass

    try:
        hp = client.send_command("HP_STATS")
        console.print(f"  [{NEON_CYAN}]HugePages:[/] {hp}")
    except Exception:
        pass

    try:
        sq = client.send_command("SQ_STATUS")
        console.print(f"  [{NEON_CYAN}]Submit Queue:[/] {sq}")
    except Exception:
        pass

    try:
        slots = client.driver.slot_enumerate()
        active = [s for s in slots if s.get("state") not in ("EMPTY", "FREE", "")]
        console.print(f"  [{NEON_CYAN}]Active slots:[/] {len(active)}/{len(slots)}")
    except Exception:
        pass


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    if args.command is None:
        console.print(_BANNER)
        parser.print_help()
        return 0

    # Create client
    client = VOS3Client(socket_path=args.socket)

    try:
        # Connect (HMAC-enforced)
        if not client.connect():
            console.print(f"  [{NEON_MAGENTA}]Cannot connect to VBus at {args.socket}[/]")
            console.print(f"  [dim]Is QEMU running? Is the bridge socket active?[/dim]")
            return 1

        # Dispatch
        if args.command == "inspect":
            cmd_inspect(client, args.slot_id, page_count=args.pages)
        elif args.command == "watch":
            cmd_watch(client, args.slot_id, duration=args.duration)
        elif args.command == "purge":
            cmd_purge(client, force=args.force)
        elif args.command == "dashboard":
            cmd_dashboard(client, slot_id=args.slot_id,
                         refresh=args.refresh, duration=args.duration)
        elif args.command == "agent":
            if getattr(args, "agent_command", None) == "list":
                cmd_agent_list(client)
            elif getattr(args, "agent_command", None) == "kill-all":
                cmd_agent_kill_all(client, force=getattr(args, "force", False))
            else:
                console.print(f"  [dim]Usage: vos3ctl agent {{list|kill-all}}[/dim]")
        elif args.command == "sys":
            if getattr(args, "sys_command", None) == "rescan":
                cmd_sys_rescan(client, kernel_path=getattr(args, "kernel", None))
            else:
                console.print(f"  [dim]Usage: vos3ctl sys {{rescan}}[/dim]")
        elif args.command == "throttle":
            cmd_throttle(client, args.slot_id, level=args.level)
        elif args.command == "fortress":
            from cli_system.modules.silicon_fortress import cmd_fortress
            cmd_fortress(args, client)
        elif args.command == "ping":
            _cmd_ping(client)
        elif args.command == "status":
            _cmd_status(client)

    except SessionEpochError as e:
        console.print(Panel(
            f"[{NEON_MAGENTA}]SESSION EPOCH MISMATCH[/]\n\n"
            f"{e}\n\n"
            f"[{NEON_CYAN}]The kernel was rebooted. Re-run your command to "
            f"establish a new session.[/]",
            border_style=NEON_MAGENTA,
            title="[bold]Re-Authentication Required[/]"
        ))
        return 2

    except KeyboardInterrupt:
        console.print(f"\n  [{NEON_CYAN}]Interrupted.[/]")

    finally:
        client.disconnect()

    return 0


if __name__ == "__main__":
    sys.exit(main())
