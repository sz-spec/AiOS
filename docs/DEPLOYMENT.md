# vOS — Bare-Metal Deployment

**Anchor:** `aeb3736` · **Phase:** P-deploy · **Artifact path:** `dist/vos3_installer.iso`

This document describes how to take a freshly-built vOS kernel and
turn it into a USB stick or DVD that boots real hardware.

## 1. Build the installer ISO

```bash
cd vos.v1/kernel

# Build the kernel (BENCH_MODE=1 strips verbose VMM debug output)
make CROSS=x86_64-linux-gnu- BENCH_MODE=1

# Build the installer ISO
make iso
```

Output:

```
dist/vos3_installer.iso        ~6-10 MB hybrid BIOS+UEFI bootable
```

The `make iso` target wraps `infra/build_iso.sh`, which:

1. Preflights `grub-mkrescue`, `xorriso`, `mtools` (with platform-specific install hints if any are missing).
2. Lays out the ISO tree with `kernel/build/vos3.elf` at `/boot/vos3.elf`.
3. Generates a minimal `grub.cfg` (serial console + `multiboot2 /boot/vos3.elf; boot`).
4. Runs `grub-mkrescue -o dist/vos3_installer.iso <staging-dir>`.
5. Verifies the output is a valid ISO 9660 image.
6. Prints size + SHA-256 of the artifact.

If the host is missing `mtools`, the ISO is built **BIOS-only** (no
UEFI boot path). The script warns about this — most physical hardware
from 2015+ supports legacy BIOS boot, so the BIOS-only ISO still
works, but UEFI-locked firmware (some ThinkPads, Surface) will refuse
it.

### Dependency install (one-time, per host)

```bash
# Ubuntu / Debian
sudo apt-get install grub-pc-bin grub-common xorriso mtools

# Fedora / RHEL
sudo dnf install grub2-pc xorriso mtools

# macOS
brew install x86_64-elf-grub xorriso mtools
```

## 2. Smoke-test in QEMU first

```bash
qemu-system-x86_64 \
    -cdrom dist/vos3_installer.iso \
    -m 1024M -smp 2 \
    -serial stdio \
    -no-reboot -no-shutdown
```

You should see GRUB's launcher menu, then the vOS banner + KPTI/MICROCODE
boot lines on the serial console (stdio in this case). If GRUB shows
its menu but `multiboot2` errors out, the kernel ELF inside the ISO is
either missing the multiboot2 header or built without `-fno-pic`. Both
are caught by `make` itself before the ISO step.

## 3. Flash to a USB stick

⚠️ **Both `dd` and `Etcher` will erase the target disk. Triple-check
the device path / target label before pressing enter.**

### macOS — `dd`

```bash
# 1. Insert the USB stick, then list disks
diskutil list

#    Identify the USB by size + label, e.g.  /dev/disk4

# 2. Unmount (don't eject — we want the device, not the volumes)
diskutil unmountDisk /dev/disk4

# 3. Flash (rdisk* is the raw character device — ~3× faster than disk*)
sudo dd if=dist/vos3_installer.iso of=/dev/rdisk4 bs=4m status=progress

# 4. Eject when done
diskutil eject /dev/disk4
```

### Linux — `dd`

```bash
# 1. Identify the USB device — DO NOT skip this step
lsblk -d -o NAME,SIZE,LABEL,MODEL

#    Identify by size + model, e.g.  /dev/sdc  (a 32 GB SanDisk)

# 2. Make sure nothing's mounted from it
sudo umount /dev/sdc?* 2>/dev/null

# 3. Flash
sudo dd if=dist/vos3_installer.iso of=/dev/sdc bs=4M status=progress conv=fsync

# 4. Tell the kernel to drop the partition table cache
sudo partprobe /dev/sdc
```

### Windows — Rufus

1. Download Rufus from <https://rufus.ie> (do not use Etcher on Windows for hybrid ISOs — it occasionally mangles the El Torito catalogue).
2. Insert the USB stick.
3. Open Rufus → **Device** = your USB → **Boot selection** = `dist\vos3_installer.iso` → **Partition scheme** = `MBR` (BIOS) or `GPT` (UEFI) → **Start**.
4. When asked, choose **DD Image mode** (not ISO mode — vOS's ISO is a hybrid layout, ISO-mode strips the bootloader).

### Etcher (any OS — slowest but safest)

1. Install balenaEtcher from <https://etcher.balena.io>.
2. **Flash from file** → pick `dist/vos3_installer.iso`.
3. **Select target** → pick your USB stick (Etcher refuses internal disks unless explicitly unlocked — extra safety vs. `dd`).
4. **Flash** → wait ~30 s + verification pass.

## 4. Boot the target machine

1. Insert the USB stick.
2. Power on, mash whatever key opens the boot menu (commonly **F12** on Lenovo/Dell, **F9** on HP, **F11** on MSI, **Option** on Mac with Boot Camp).
3. Pick the USB device.
4. GRUB menu appears → press **Enter** on `vOS v1 — boot kernel` (or wait 3 s for the default selection).
5. Within ~2 seconds you'll see the vOS banner, KPTI mode line, microcode revision, PCI-ECAM probe, then init.

### Expected boot lines (serial console)

```
[GRUB] vOS v1 installer: loading kernel...

 __      _____  _____ ____
 \ \    / / _ \/ ____|___ \
  \ \  / / | | \___ \  __) |
   \ \/ /| |_| |___) |/ __/
    \__/  \___/|____/|_____|

  VOS3 Kernel v3.1.0-GOLD-MASTER
  ...
[KPTI] mode=PROTECTED_FULL pcid=yes invpcid=yes smep=yes smap=yes ...
[MICROCODE] revision=0x05003604 baseline=0x05003604 below_baseline=no
[PCI-ECAM] available — buses 00..FF mapped MMIO@0x00000000B0000000 (PCD=1, NX=1)
[KPTI] init: ready ...
```

If you see `[VOS3_BOOT_REFUSE] 32-bit-only CPU detected` — the hardware
is pre-x86_64 and outside the support window. See AAA plan §1.

If you see `[KPTI] mode=LEGACY_KAISER` — the CPU lacks PCID. The host
is < 2010 silicon. The sandbox will run with halved rlimits per P4.3.

If you see `[SECURITY] Microcode outdated` — the host's microcode is
below the baseline in `docs/MICROCODE_BASELINE.md`. The kernel
correctly demotes to LEGACY_KAISER + halved rlimits. Update the
microcode (BIOS/UEFI firmware update or `intel-microcode` package on
the live system before flashing).

## 5. Honest-scope ceilings

- The installer ISO does **not** include any user-space programs yet.
  After boot, the kernel reaches its idle loop and waits for syscalls
  that aren't coming. This is a *boot* installer, not a *runtime*
  installer — Phase 5+ wires user-space delivery.
- `make iso` only produces the BIOS bootable path reliably; UEFI is
  best-effort (requires `mtools` on the host that runs `make iso`).
  For UEFI-locked targets, build on a Linux host with all four
  dependencies installed.
- The ISO size scales with the kernel ELF size. The current debug
  build (with symbols) is ~70 MB; stripped release builds are ~6 MB.
  Use `make CROSS=... BENCH_MODE=1 EXTRA_LDFLAGS=-s` to strip.
- `dd` and Rufus DD-mode write at the disk's max sustained throughput.
  USB 2.0 sticks take ~30 s; USB 3.0 sticks take ~5 s. A flash that
  finishes in <2 s usually means the device path was wrong.
