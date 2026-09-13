"""
Silicon Fortress — Real-time Kernel Security & Performance Panel.

Displays live telemetry from the VOS3 kernel:
  - HMAC violation count (VBus frame auth failures)
  - Silicon fault count (CR4/CR0 hardening violations)
  - Warp Drive throughput (ivshmem DMA MB/s)
  - HugePage utilization heatmap across 4 Wings (model slots)

Usage: vos3ctl fortress [--interval SECONDS]
"""

import sys
import time
import logging

logger = logging.getLogger("vos3ctl.fortress")


def _collect_telemetry(vbus) -> dict:
    """Collect all Silicon Fortress metrics from kernel."""
    telemetry = {
        "hmac_violations": 0,
        "silicon_faults": 0,
        "warp_active": False,
        "warp_size_bytes": 0,
        "slots": [],  # Per-slot data
        "hp_total": 128,
        "hp_used": 0,
        "hp_free": 128,
        "uptime_ticks": 0,
    }

    # 1. KIM stats (silicon_faults)
    try:
        resp = vbus.send_command("KIM_STATS")
        if resp.startswith("OK|"):
            # Parse: OK|active=N|tokens=N|silicon_faults=N|bsp=N|ap=N|steals=N
            for part in resp.split("|")[1:]:
                if part.startswith("silicon_faults="):
                    telemetry["silicon_faults"] = int(part.split("=")[1])
    except Exception:
        pass

    # 2. VBus HMAC stats
    # Kernel returns: OK|violations=N,ban_active=N,hmac_enabled=N
    # (comma-delimited within the second pipe field)
    try:
        resp = vbus.send_command("HMAC_STATS")
        if resp.startswith("OK|"):
            fields = resp.split("|", 1)[1]  # everything after "OK|"
            for kv in fields.split(","):
                k, _, v = kv.partition("=")
                if k.strip() == "violations":
                    telemetry["hmac_violations"] = int(v.strip())
    except Exception:
        pass

    # 3. Warp Drive stats
    # Kernel returns: OK|WARP_ON <size> or OK|WARP_OFF
    try:
        resp = vbus.send_command("WARP_STATUS")
        if resp.startswith("OK|"):
            payload = resp.split("|", 1)[1]
            if payload.startswith("WARP_ON"):
                parts = payload.split()
                telemetry["warp_active"] = True
                if len(parts) >= 2:
                    telemetry["warp_size_bytes"] = int(parts[1])
            else:
                telemetry["warp_active"] = False
    except Exception:
        pass

    # 4. HugePage stats
    # Kernel returns: OK|<total>|<used> (positional, no key names)
    try:
        resp = vbus.send_command("HP_STATS")
        if resp.startswith("OK|"):
            parts = resp.split("|")
            if len(parts) >= 3:
                telemetry["hp_total"] = int(parts[1])
                telemetry["hp_used"] = int(parts[2])
                telemetry["hp_free"] = telemetry["hp_total"] - telemetry["hp_used"]
    except Exception:
        pass

    # 5. Per-slot status (4 slots = 4 Wings)
    # Kernel returns: OK|<slot_id>|<status_name>|<label>|<priority>|<model_id>|<size>|<checksum>|<cycles>|<violations>
    for sid in range(4):
        slot_info = {"id": sid, "status": "FREE", "model_size": 0, "palace": [0, 0, 0]}
        try:
            resp = vbus.send_command(f"SLOT_STATUS|{sid}")
            if resp.startswith("OK|"):
                parts = resp.split("|")
                if len(parts) >= 4:
                    slot_info["status"] = parts[2]  # status name at index 2
                if len(parts) >= 7:
                    slot_info["model_size"] = int(parts[6]) if parts[6].isdigit() else 0
        except Exception:
            pass

        try:
            resp = vbus.send_command(f"PALACE_STATS|{sid}")
            if resp.startswith("OK|"):
                counts = resp.split("|")[1:]
                if len(counts) >= 3:
                    slot_info["palace"] = [
                        int(counts[0]),
                        int(counts[1]),
                        int(counts[2]),
                    ]
        except Exception:
            pass

        telemetry["slots"].append(slot_info)

    return telemetry


def _render_heatmap(slots: list, hp_total: int) -> str:
    """Render ASCII heatmap of HugePage utilization across 4 Wings."""
    # Each wing gets a row, 32 columns wide (each block = 4 HugePages)
    BAR_WIDTH = 32

    lines = []
    lines.append("  HugePage Utilization — 4 Wings (each block = 4 HP)")
    lines.append("  " + "-" * (BAR_WIDTH + 20))

    for slot in slots:
        # model_size in bytes; compute HP count (each HP = 2MB)
        model_size = slot.get("model_size", 0)
        hp = (model_size + 2097151) // 2097152 if model_size > 0 else 0
        max_hp = 128  # VOS3_MODEL_SLOT_MAX_HP
        filled = (
            min(BAR_WIDTH, int(hp / max_hp * BAR_WIDTH)) if max_hp > 0 else 0
        )

        # Intensity chars: normal / warning / critical
        bar = ""
        for i in range(BAR_WIDTH):
            if i < filled:
                pct = i / BAR_WIDTH
                if pct < 0.6:
                    bar += "#"  # Normal
                elif pct < 0.85:
                    bar += "="  # Warning
                else:
                    bar += "!"  # Critical
            else:
                bar += "."

        status_tag = slot["status"][:6].ljust(6)
        pct = (hp / max_hp * 100) if max_hp > 0 else 0

        # Palace breakdown: W=weight, P=persistent, T=transient
        w, p, t = slot["palace"]
        palace_str = f"W:{w} P:{p} T:{t}"

        lines.append(
            f"  Wing {slot['id']} [{status_tag}] |{bar}| "
            f"{hp:3d}/{max_hp} ({pct:5.1f}%) {palace_str}"
        )

    lines.append("  " + "-" * (BAR_WIDTH + 20))
    return "\n".join(lines)


def _render_panel(telemetry: dict) -> str:
    """Render the complete Silicon Fortress panel."""
    lines = []
    lines.append("")
    lines.append(
        "+=======================================================+"
    )
    lines.append(
        "|         SILICON FORTRESS -- Real-Time Monitor          |"
    )
    lines.append(
        "+========================================================+"
    )

    # Security metrics
    hmac_v = telemetry["hmac_violations"]
    si_f = telemetry["silicon_faults"]
    hmac_status = "SECURE" if hmac_v == 0 else f"!! {hmac_v} VIOLATIONS"
    si_status = "HARDENED" if si_f == 0 else f"!! {si_f} FAULTS"

    lines.append(f"|  HMAC Auth:     {hmac_status:<36}   |")
    lines.append(f"|  Silicon Guard: {si_status:<36}   |")

    # Warp Drive
    warp_active = telemetry.get("warp_active", False)
    warp_size = telemetry.get("warp_size_bytes", 0)
    if warp_active:
        size_mb = warp_size / (1024 * 1024)
        warp_str = f"ACTIVE ({size_mb:.0f} MB ivshmem)"
    else:
        warp_str = "INACTIVE"
    lines.append(f"|  Warp Drive:    {warp_str:<36}   |")

    # HugePage summary
    hp_used = telemetry["hp_used"]
    hp_total = telemetry["hp_total"]
    hp_free = telemetry["hp_free"]
    hp_pct = (hp_used / hp_total * 100) if hp_total > 0 else 0
    hp_line = f"{hp_used}/{hp_total} used ({hp_pct:.0f}%), {hp_free} free"
    lines.append(f"|  HugePages:     {hp_line:<36}   |")

    lines.append(
        "+========================================================+"
    )

    # Heatmap
    heatmap = _render_heatmap(telemetry["slots"], hp_total)
    for hline in heatmap.split("\n"):
        padded = hline.ljust(56)
        lines.append(f"|{padded}|")

    lines.append(
        "+========================================================+"
    )
    lines.append(
        "|  Press Ctrl+C to exit                                  |"
    )
    lines.append(
        "+=======================================================+"
    )

    return "\n".join(lines)


def cmd_fortress(args, vbus):
    """Silicon Fortress real-time monitoring panel."""
    interval = getattr(args, "interval", 2.0)

    print("[Silicon Fortress] Connecting to kernel telemetry...")

    try:
        while True:
            telemetry = _collect_telemetry(vbus)

            # Clear screen (ANSI)
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()

            panel = _render_panel(telemetry)
            print(panel)

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[Silicon Fortress] Monitor stopped.")
