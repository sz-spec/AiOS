# vOS — Cross-Platform Installation Guide

**Anchor:** `aeb3736` · **Phase:** P-deploy

Each format below is produced by `infra/generate_dist.sh` from the
same source ISO at `dist/vos3_installer.iso`. The disk contents are
identical across formats — only the container differs to match each
hypervisor's native expectations.

| Host OS | Artifact | Container | Hypervisor |
|---|---|---|---|
| Linux | `dist/vos3_linux_kvm.qcow2` | qcow2 | KVM via virt-manager / libvirt |
| Windows | `dist/vos3_windows_hyperv.vhdx` | VHDX (Dynamic) | Hyper-V |
| macOS | `dist/vos3_macos_portable.utm/` | UTM bundle (.utm dir) | UTM (TCG / Hypervisor.framework) |

---

## Linux — virt-manager + KVM

### 1. Prerequisites

```bash
# Ubuntu / Debian
sudo apt-get install qemu-kvm libvirt-daemon-system libvirt-clients \
    bridge-utils virt-manager

# Fedora / RHEL
sudo dnf install qemu-kvm libvirt virt-manager virt-install

# Verify KVM is exposed
ls /dev/kvm    # must exist
egrep -c '(vmx|svm)' /proc/cpuinfo   # must be >= 1

# Add yourself to the libvirt + kvm groups, then log out + back in
sudo usermod -aG libvirt,kvm "$USER"
```

### 2. Import the qcow2 (CLI — recommended)

```bash
virt-install \
    --name vos-v1 \
    --memory 1024 --vcpus 2 \
    --disk path=$PWD/dist/vos3_linux_kvm.qcow2,bus=virtio,format=qcow2 \
    --os-variant generic \
    --network user \
    --graphics none \
    --console pty,target_type=serial \
    --boot uefi,hd \
    --import
```

The `--graphics none --console pty,target_type=serial` flags route the
boot output to your terminal so you see the vOS banner + `[KPTI]` /
`[MICROCODE]` / `[PCI-ECAM]` boot lines live. Press `Ctrl-]` to detach.

### 3. Or import via virt-manager GUI

1. Open **virt-manager**.
2. **File → New Virtual Machine**.
3. **Import existing disk image** → browse to `dist/vos3_linux_kvm.qcow2`.
4. **Operating system** → Generic Linux.
5. Memory: **1024 MB**, CPUs: **2**.
6. Name: **vos-v1**. Untick **Customize configuration before install** unless you need to fiddle.
7. **Finish** → VM starts.

For serial-console output: **View → Text Consoles → Serial 1** after the VM starts.

### 4. Expected boot output

```
[KPTI] mode=PROTECTED_FULL pcid=yes invpcid=yes smep=yes smap=yes ...
[MICROCODE] revision=0x05003604 baseline=0x05003604 below_baseline=no
[PCI-ECAM] available — buses 00..FF mapped MMIO@0xB0000000 (PCD=1, NX=1)
[KPTI] init: ready
```

A real Linux KVM host with a 2018+ CPU will land in `PROTECTED_FULL`.
Older hosts demote correctly (`PROTECTED_PCID_ONLY` for pre-Haswell,
`LEGACY_KAISER` for pre-Westmere) — see `docs/UNIVERSAL_HARDWARE_MANIFEST.md`.

### 5. Troubleshoot

| Symptom | Likely cause | Fix |
|---|---|---|
| `qemu-kvm: failed to initialize KVM: Permission denied` | Not in `kvm` group | `sudo usermod -aG kvm $USER`, log out+in |
| Boot hangs at `[INFO] VMM: Initializing` | Nested KVM (host is itself a VM with shaky nested-virt) | Run on bare-metal Linux or use TCG: `--virt-type qemu` |
| `[KPTI] mode=LEGACY_KAISER` | Host CPU pre-2010 (no PCID) | Expected — sandbox auto-tightens per P4.3 |
| `[SECURITY] Microcode outdated` | Host's microcode below the per-family baseline | Update microcode (`intel-microcode` / `amd64-microcode`) and re-run |

---

## Windows — Hyper-V + VHDX

### 1. Enable Hyper-V (one-time, per machine)

Open **PowerShell as Administrator**:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
```

Reboot when prompted.

> **Note**: Hyper-V is Windows 10/11 **Pro / Enterprise / Education** only. Windows Home users should fall back to QEMU-on-Windows (see § "Windows fallback" below) or VirtualBox.

### 2. Import the VHDX

Option A — GUI (Hyper-V Manager):

1. **Hyper-V Manager** → right-click your host → **New → Virtual Machine**.
2. **Generation 1** (Gen 2 requires UEFI — vOS supports BIOS today; Gen 2 will work once UEFI boot lands).
3. **Startup memory**: 1024 MB. Untick **Use Dynamic Memory** (we don't need ballooning for a boot test).
4. **Networking**: Default Switch.
5. **Connect virtual hard disk** → **Use an existing virtual hard disk** → browse to `dist\vos3_windows_hyperv.vhdx`.
6. **Finish**, then **Connect** to the VM → **Start**.

Option B — PowerShell:

```powershell
New-VM -Name "vOS-v1" -MemoryStartupBytes 1GB -Generation 1 `
       -VHDPath "C:\path\to\dist\vos3_windows_hyperv.vhdx" `
       -SwitchName "Default Switch"
Set-VMProcessor "vOS-v1" -Count 2
Start-VM -Name "vOS-v1"
vmconnect.exe localhost "vOS-v1"
```

### 3. Watching boot output

Hyper-V Manager's console window shows GRUB → vOS banner → boot lines.
Hyper-V doesn't expose a clean host-side serial passthrough by default;
to capture serial to a file:

```powershell
Set-VMComPort "vOS-v1" -Number 1 -Path \\.\pipe\vos-v1-serial
```

Then on the host:

```powershell
# Read the named pipe
Get-Content \\.\pipe\vos-v1-serial -Wait
```

### Windows fallback (no Hyper-V available)

If your Windows edition is Home, or Hyper-V conflicts with VMware/VirtualBox:

```cmd
:: Install QEMU from https://qemu.weilnetz.de/w64/
qemu-system-x86_64.exe ^
    -drive file=dist\vos3_windows_hyperv.vhdx,format=vhdx,if=virtio ^
    -m 1024 -smp 2 -serial stdio -display none
```

QEMU runs in software emulation (no KVM on Windows), which is slow
but works on every edition.

### Troubleshoot

| Symptom | Likely cause | Fix |
|---|---|---|
| "Hyper-V requires a processor that supports SLAT" | CPU without EPT/RVI (very old) | Upgrade the host CPU, or use QEMU fallback |
| Black screen on boot, no GRUB | Generation 2 VM with no UEFI boot in the VHDX | Re-create as Generation 1 |
| "The application encountered an error" on import | VHDX corrupted in transit | Re-generate: `bash infra/generate_dist.sh` |

---

## macOS — UTM (Apple Silicon + Intel)

UTM is a free SwiftUI front-end to QEMU. On Apple Silicon it uses
Hypervisor.framework for ARM guests, but vOS is an x86_64 kernel —
which means UTM falls back to TCG emulation on Apple Silicon (slow
but universal) and uses Hypervisor.framework on Intel Macs (native
speed).

### 1. Install UTM (one-time)

```bash
brew install --cask utm
# or download from https://mac.getutm.app
```

### 2. Open the bundle

```bash
open dist/vos3_macos_portable.utm
```

UTM should auto-detect the bundle and open the VM in its sidebar. If
it shows "Import VM..." instead, click that, then choose
`dist/vos3_macos_portable.utm` — UTM will normalise the config.

### 3. Start the VM

In UTM's sidebar:

1. Select **vOS v1**.
2. Click **▶ Run**.
3. The bundled config uses a serial console (no GUI) — the boot lines
   appear in a terminal window UTM opens.

### 4. Expected performance

| Mac | Path | Boot time to `[KPTI] init: ready` |
|---|---|---|
| Intel Mac (any 2015+) | Hypervisor.framework (native x86_64) | ~2 s |
| Apple Silicon (M1/M2/M3) | TCG emulation | ~30 s |
| Apple Silicon + Rosetta-fast TCG | TCG + Rosetta JIT | ~10 s (UTM 4.4+) |

Apple Silicon users: the bundled config disables hardware accel so it
works portably. To opt into Rosetta-fast TCG, in UTM: **Edit → System
→ Use Rosetta x86_64 JIT** (UTM 4.4+, macOS 13+).

### 5. The bundle layout

```
dist/vos3_macos_portable.utm/
├── Info.plist            ← bundle metadata (CFBundlePackageType=VMUT)
├── config.plist          ← UTM VM configuration (x86_64 + q35 + virtio)
└── Data/
    └── disk-0.qcow2      ← the same qcow2 disk as Linux KVM
```

The bundle is portable — copy or rsync the whole `.utm` directory to
another Mac and it just works.

### Troubleshoot

| Symptom | Likely cause | Fix |
|---|---|---|
| "Unable to launch QEMU" | UTM version pre-4.0 | Update UTM: `brew upgrade --cask utm` |
| "No bootable medium" | UTM imported but stripped the BootDevice key | Edit → System → Boot → first device = `disk-0.qcow2` |
| Boot is glacially slow on Apple Silicon | Pure TCG, no Rosetta acceleration | Enable Rosetta JIT (UTM 4.4+, macOS 13+) or use Intel Mac / Linux host |

---

## Honest-scope ceilings

- **Live-CD semantics on all three platforms.** The qcow2/VHDX/UTM
  disks wrap the ISO contents — they boot vOS as if from a CD-ROM.
  No state persists across reboots. This is correct for vOS today
  (kernel-only, no user-space installer yet) but means you can't
  `apt install foo` inside the running VM.

- **Performance varies by host.** PROTECTED_FULL on real Linux KVM
  with Skylake-Server+ silicon is ~2% slower than the bare-metal
  syscall path. PROTECTED_PCID_ONLY on Westmere-era silicon is ~3%.
  LEGACY_KAISER on pre-PCID silicon is 5–30% (documented in
  `docs/UNIVERSAL_HARDWARE_MANIFEST.md`).

- **Apple Silicon emulates a foreign architecture.** Real-hardware
  protection guarantees (KPTI, microcode mitigations) don't apply
  to an x86_64 guest under TCG — the host's ARM CPU has its own
  mitigations, separate from what the vOS kernel is enforcing inside
  the emulated guest. Use Apple Silicon for development convenience,
  not for security validation. The KVM verification on a real x86_64
  host is the load-bearing evidence — see `infra/verify/README.md`
  and the GCP escape route in `infra/verify/gcp_close_task_48.sh`.

- **No installer yet.** "Installing" vOS = running it from the live
  image. A real installer (user-space delivery + persistence) lands
  in a later phase. Until then, these images are demo / verification
  / development tools, not deployable production artifacts.
