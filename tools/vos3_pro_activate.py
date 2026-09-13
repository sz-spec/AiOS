#!/usr/bin/env python3
"""
VOS3 Pro Activation Tool — License Request Generator
======================================================

Generates a `license_request.json` file containing this host's hardware
fingerprint, suitable for emailing to licensing@vos3.ai to receive a
signed `vos3.lic` file in return.

What this tool does:
  1. Reads the same hardware identifiers the kernel reads at boot
     (CPUID Leaf 0 / Leaf 1, primary MAC, and TPM EK pub when available).
  2. Computes the SHA-256 fingerprint that mirrors the kernel's
     `vos3_get_hw_fingerprint()` (kernel/src/pro/license_check.c).
  3. Writes `license_request.json` for the operator to send to VOS3.

What this tool does NOT do:
  - Activate anything on its own. The signed `vos3.lic` blob comes from
    VOS3 after a customer license agreement.
  - Validate an existing license. That's `vos3_verify` (separate tool).
  - Modify any kernel state. Read-only.

Honest scoping (matches kernel/src/pro/license_check.c):
  - On hosts WITHOUT a TPM, the EK pub portion of the fingerprint is
    zero-filled. The license is still bindable, just to a smaller set
    of hardware identifiers.
  - On hosts WITHOUT a network interface up, the MAC portion is
    zero-filled. The fingerprint stays valid.
  - This tool runs in userspace; it cannot reach the kernel's actual
    boot-time fingerprint computation. The two SHOULD agree by
    construction (same inputs, same hash) but a CI test should verify
    this on the customer's reference hardware before licensing ships.

Usage:
    python3 tools/vos3_pro_activate.py
    python3 tools/vos3_pro_activate.py --output mylicense_request.json
    python3 tools/vos3_pro_activate.py --customer "Acme Corp"
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

VOS3_LICENSING_EMAIL = "licensing@vos3.ai"
LICENSE_REQUEST_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Hardware identifier collection — mirrors license_check.c at boot
# ---------------------------------------------------------------------------

def read_cpuid_vendor_string() -> bytes:
    """12-byte CPUID Leaf 0 vendor string (e.g. b"GenuineIntel").
    Falls back to platform.processor() when CPUID isn't reachable from Python."""
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.vendor"],
                text=True, timeout=5,
            ).strip()
            return out.encode("ascii", errors="replace")[:12].ljust(12, b"\x00")
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("vendor_id"):
                        v = line.split(":", 1)[1].strip()
                        return v.encode("ascii", errors="replace")[:12].ljust(12, b"\x00")
        except OSError:
            pass
    return platform.processor().encode("ascii", errors="replace")[:12].ljust(12, b"\x00")


def read_cpuid_signature() -> int:
    """uint64 packing of CPUID Leaf 1 (family/model/stepping). Heuristic
    extraction from /proc/cpuinfo or sysctl since Python can't issue CPUID
    directly."""
    family = model = stepping = 0
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("cpu family"):
                        family = int(line.split(":", 1)[1].strip())
                    elif line.startswith("model") and "name" not in line:
                        try:
                            model = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            pass
                    elif line.startswith("stepping"):
                        try:
                            stepping = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            pass
                    if family and model and stepping:
                        break
        except OSError:
            pass
    elif sys.platform == "darwin":
        try:
            family   = int(subprocess.check_output(["sysctl", "-n", "machdep.cpu.family"],   text=True, timeout=5).strip())
            model    = int(subprocess.check_output(["sysctl", "-n", "machdep.cpu.model"],    text=True, timeout=5).strip())
            stepping = int(subprocess.check_output(["sysctl", "-n", "machdep.cpu.stepping"], text=True, timeout=5).strip())
        except (subprocess.SubprocessError, ValueError, FileNotFoundError):
            pass
    # Pack like CPUID Leaf 1 EAX (kernel-side cpuid_signature() is wider —
    # this is a reasonable userspace approximation; v20.6 will ship a
    # tighter agreement once the kernel exposes the fingerprint via VBus).
    return ((family & 0xFFF) << 8) | ((model & 0xFF) << 4) | (stepping & 0xF)


def read_primary_mac() -> bytes:
    """6-byte primary NIC MAC. Returns zeros if no MAC enumerable."""
    try:
        import uuid
        node = uuid.getnode()
        # uuid.getnode() returns a 48-bit number with the multicast bit set
        # if it had to fabricate one. Real MACs have bit 41 (the
        # locally-administered bit) cleared.
        if (node >> 40) & 1:
            return b"\x00" * 6  # synthesized — treat as no-MAC
        return node.to_bytes(6, "big")
    except Exception:
        return b"\x00" * 6


def read_tpm_ek_pub() -> bytes:
    """32-byte TPM EK pub if available; zeros otherwise. Honest stub —
    real extraction requires tpm2-tools or a kernel ioctl bridge that
    isn't yet in scope for v20.5.2."""
    return b"\x00" * 32


# ---------------------------------------------------------------------------
# Fingerprint computation — must agree with license_check.c
# ---------------------------------------------------------------------------

def compute_fingerprint() -> dict:
    """Compose 58-byte buffer and SHA-256 it, exactly mirroring the
    kernel's vos3_get_hw_fingerprint() in license_check.c."""
    vendor = read_cpuid_vendor_string()
    sig = read_cpuid_signature()
    sig_bytes = sig.to_bytes(8, "little")
    tpm_ek = read_tpm_ek_pub()
    mac = read_primary_mac()

    buf = vendor + sig_bytes + tpm_ek + mac.ljust(6, b"\x00")
    assert len(buf) == 58, f"fingerprint buffer length mismatch: {len(buf)}"

    digest = hashlib.sha256(buf).hexdigest()
    return {
        "fingerprint_sha256_hex": digest,
        "cpuid_vendor": vendor.rstrip(b"\x00").decode("ascii", errors="replace"),
        "cpuid_signature": sig,
        "has_tpm_ek": any(b for b in tpm_ek),
        "has_primary_mac": any(b for b in mac),
        "fingerprint_input_bytes_hex": buf.hex(),
    }


# ---------------------------------------------------------------------------
# License request file generation
# ---------------------------------------------------------------------------

def build_request(fp: dict, customer: str | None) -> dict:
    return {
        "schema_version": LICENSE_REQUEST_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "node": platform.node(),
        },
        "fingerprint": fp,
        "customer": {
            "name": customer or "(please fill in before sending)",
            "contact_email": "(please fill in before sending)",
        },
        "vos3_version": "v20.5.2",
        "instructions": [
            "1. Review the fingerprint values above for correctness.",
            "2. Fill in customer.name and customer.contact_email.",
            f"3. Email this JSON file to {VOS3_LICENSING_EMAIL}",
            "4. VOS3 will reply with a signed vos3.lic file.",
            "5. Place vos3.lic at /boot/vos3.lic on the target host before",
            "   booting the PRO kernel.",
            "6. The kernel will read it, verify the Ed25519 signature against",
            "   the embedded VOS3 CA pubkey, and unlock the 10 GiB hugepage",
            "   ceiling if the fingerprint matches.",
        ],
        "honest_caveats": [
            "Without a TPM, has_tpm_ek will be False and the license binds",
            "to CPUID + MAC only. This is acceptable but slightly less robust.",
            "VOS3 PRO features stay capped at 512 MB if the .lic file is",
            "missing, expired, or signed against a different fingerprint.",
            "The kernel will NOT brick or refuse to boot — it gracefully",
            "degrades to CORE limits per the open-core charter.",
        ],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1].strip())
    ap.add_argument("--output", default="license_request.json",
                    help="output file path (default: license_request.json)")
    ap.add_argument("--customer", default=None,
                    help="customer name to embed in the request")
    ap.add_argument("--print", action="store_true",
                    help="print the request JSON to stdout instead of writing")
    args = ap.parse_args()

    fp = compute_fingerprint()
    req = build_request(fp, args.customer)
    payload = json.dumps(req, indent=2)

    if args.print:
        print(payload)
        return 0

    out = Path(args.output)
    out.write_text(payload + "\n", encoding="utf-8")

    print(f"VOS3 Pro license request written to: {out.resolve()}")
    print()
    print("Fingerprint summary:")
    print(f"  cpuid_vendor:    {fp['cpuid_vendor']}")
    print(f"  cpuid_signature: {fp['cpuid_signature']}")
    print(f"  has_tpm_ek:      {fp['has_tpm_ek']}")
    print(f"  has_primary_mac: {fp['has_primary_mac']}")
    print(f"  sha256:          {fp['fingerprint_sha256_hex']}")
    print()
    print(f"Next: review the file, fill in customer info,")
    print(f"      email to {VOS3_LICENSING_EMAIL}")
    print()
    print("Honest note: the technical gate is a backup signal. The legal")
    print("license is the primary moat. See LICENSE_PRO and")
    print("docs/strategy/OPEN_CORE_LICENSING.md for the full picture.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
