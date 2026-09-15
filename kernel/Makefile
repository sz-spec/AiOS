#
# VOS3 Kernel Makefile
#
# Build targets:
#   make          - Build kernel
#   make clean    - Clean build artifacts
#   make iso      - Create bootable ISO
#   make qemu     - Run in QEMU
#   make debug    - Run in QEMU with GDB
#

# ============================================================================
# CONFIGURATION
# ============================================================================

# Kernel name
KERNEL := vos3.elf

# Directories
SRC_DIR     := src
INC_DIR     := include
BUILD_DIR   ?= build
OBJ_DIR     := $(BUILD_DIR)/obj
ISO_DIR     := $(BUILD_DIR)/iso

# Architecture
ARCH := x86_64

# Cross-compiler prefix (leave empty for native)
CROSS := x86_64-elf-

# Tools
CC      := $(CROSS)gcc
AS      := $(CROSS)as
LD      := $(CROSS)ld
OBJCOPY := $(CROSS)objcopy
OBJDUMP := $(CROSS)objdump

# QEMU
QEMU := qemu-system-x86_64

# ============================================================================
# REPRODUCIBLE BUILD (Stage 7)
# ============================================================================
# Honor SOURCE_DATE_EPOCH if provided in the environment; otherwise pin a
# fixed canonical anchor so identical source produces a bit-for-bit identical
# vos3.elf regardless of when or where the build happens.
#
# GCC 7.5+ overrides __DATE__, __TIME__, and __TIMESTAMP__ with the value of
# this variable so the boot-banner timestamps embedded in the kernel binary
# become deterministic. The value 1700000000 = 2023-11-14T22:13:20Z, chosen
# as a clean, well-inside-the-supported-range anchor.
SOURCE_DATE_EPOCH ?= 1700000000
export SOURCE_DATE_EPOCH

# ============================================================================
# COMPILER FLAGS
# ============================================================================

# Common warnings (relaxed for cross-compilation)
WARNINGS := -Wall -Wextra \
            -Wno-unused-parameter \
            -Wno-unused-function \
            -Wno-missing-field-initializers \
            -Wno-pedantic

# C flags
# Note: -mapx-features=none disables APX instructions (GCC 14+) which QEMU
# doesn't yet emulate. The flag is unrecognised on GCC ≤13, so we feature-
# detect: empty string on older toolchains, no harm done — APX isn't emitted
# without the explicit -march flag anyway. Lets the kernel cross-build on
# Ubuntu 22.04/24.04 (gcc-x86-64-linux-gnu = gcc 11–13) which is what every
# CI runner ships today.
APX_FLAG := $(shell printf 'int main(void){return 0;}\n' \
              | $(CC) -mapx-features=none -x c - -o /dev/null 2>/dev/null \
              && echo "-mapx-features=none")
CFLAGS := -std=c11 -ffreestanding -fstack-protector-strong \
          -fno-pic -fno-pie -fno-common \
          -mno-red-zone -mno-mmx -mno-sse -mno-sse2 \
          -march=x86-64 -mtune=generic $(APX_FLAG) \
          -fcf-protection=branch \
          -mcmodel=kernel -m64 \
          -ffunction-sections -fdata-sections \
          '-ffile-prefix-map=$(CURDIR)=.' \
          '-ffile-prefix-map=$(CURDIR)/$(SRC_DIR)=src' \
          -I$(INC_DIR) -Iboot \
          $(WARNINGS) \
          $(EXTRA_CFLAGS) \
          -O2 -g

# Assembly flags
ASFLAGS := -g

# Linker flags
# Stage 7: --build-id=none strips the random GNU build-ID note section,
# which would otherwise change every build and defeat reproducibility.
LDFLAGS := -nostdlib -static -z max-page-size=0x1000 \
           --gc-sections \
           --build-id=none \
           -T linker.ld

# ============================================================================
# SOURCE FILES
# ============================================================================

# C sources
C_SOURCES := \
    $(SRC_DIR)/boot/multiboot2_stub.c \
    $(SRC_DIR)/boot/multiboot2_parse.c \
    $(SRC_DIR)/boot/limine_stub.c \
    $(SRC_DIR)/boot/kmain.c \
    $(SRC_DIR)/boot/boot_mm.c \
    $(SRC_DIR)/boot/boot_drivers.c \
    $(SRC_DIR)/boot/boot_fs.c \
    $(SRC_DIR)/boot/boot_ai.c \
    $(SRC_DIR)/boot/boot_net.c \
    $(SRC_DIR)/boot/uefi_boot.c \
    $(SRC_DIR)/mm/pmm.c \
    $(SRC_DIR)/mm/vmm.c \
    $(SRC_DIR)/arch/x86_64/kpti_roots.c \
    $(SRC_DIR)/mm/heap.c \
    $(SRC_DIR)/mm/ai_guard.c \
    $(SRC_DIR)/mm/ai_pte.c \
    $(SRC_DIR)/mm/ai_slots.c \
    $(SRC_DIR)/mm/ai_sq.c \
    $(SRC_DIR)/mm/ai_isc.c \
    $(SRC_DIR)/mm/ai_monitor.c \
    $(SRC_DIR)/mm/ai_telemetry.c \
    $(SRC_DIR)/mm/user_copy.c \
    $(SRC_DIR)/mm/ai_kv_managed.c \
    $(SRC_DIR)/mm/ai_kv_compress.c \
    $(SRC_DIR)/mm/ai_kv_prefix.c \
    $(SRC_DIR)/mm/kv_compressor.c \
    $(SRC_DIR)/mm/intent_validator.c \
    $(SRC_DIR)/mm/tee.c \
    $(SRC_DIR)/mm/hcs.c \
    $(SRC_DIR)/mm/audit_ring.c \
    $(SRC_DIR)/arch/x86_64/numa.c \
    $(SRC_DIR)/arch/x86_64/percpu.c \
    $(SRC_DIR)/arch/x86_64/smp.c \
    $(SRC_DIR)/arch/x86_64/smp_telemetry.c \
    $(SRC_DIR)/drivers/console.c \
    $(SRC_DIR)/drivers/timer.c \
    $(SRC_DIR)/drivers/device.c \
    $(SRC_DIR)/drivers/devfs.c \
    $(SRC_DIR)/drivers/mem.c \
    $(SRC_DIR)/drivers/tty.c \
    $(SRC_DIR)/drivers/keyboard.c \
    $(SRC_DIR)/drivers/dev_init.c \
    $(SRC_DIR)/arch/x86_64/gdt.c \
    $(SRC_DIR)/arch/x86_64/idt.c \
    $(SRC_DIR)/arch/x86_64/interrupts.c \
    $(SRC_DIR)/arch/x86_64/pic.c \
    $(SRC_DIR)/arch/x86_64/cpu.c \
    $(SRC_DIR)/arch/x86_64/mitigation_factory.c \
    $(SRC_DIR)/arch/x86_64/microcode_check.c \
    $(SRC_DIR)/arch/x86_64/kpti.c \
    $(SRC_DIR)/arch/x86_64/pci_ecam.c \
    $(SRC_DIR)/arch/x86_64/apic.c \
    $(SRC_DIR)/arch/x86_64/ioapic.c \
    $(SRC_DIR)/arch/x86_64/xsave.c \
    $(SRC_DIR)/diag/assert_cert.c \
    $(SRC_DIR)/arch/x86_64/syscall.c \
    $(SRC_DIR)/arch/x86_64/user.c \
    $(SRC_DIR)/sched/task.c \
    $(SRC_DIR)/sched/scheduler.c \
    $(SRC_DIR)/sched/sync.c \
    $(SRC_DIR)/sched/dispatch_hint.c \
    $(SRC_DIR)/sched/xsave_ctx.c \
    $(SRC_DIR)/sched/core_cookie.c \
    $(SRC_DIR)/pro/license_check.c \
    $(SRC_DIR)/ipc/ipc.c \
    $(SRC_DIR)/ipc/msgqueue.c \
    $(SRC_DIR)/ipc/shm.c \
    $(SRC_DIR)/ipc/pipe.c \
    $(SRC_DIR)/ipc/signal.c \
    $(SRC_DIR)/ipc/signal_syscall.c \
    $(SRC_DIR)/ipc/futex.c \
    $(SRC_DIR)/ipc/dispatcher.c \
    $(SRC_DIR)/fs/posix_syscall.c \
    $(SRC_DIR)/fs/vfs.c \
    $(SRC_DIR)/fs/fd.c \
    $(SRC_DIR)/fs/ramfs.c \
    $(SRC_DIR)/fs/file.c \
    $(SRC_DIR)/fs/dir.c \
    $(SRC_DIR)/fs/pipe.c \
    $(SRC_DIR)/fs/vos3fs.c \
    $(SRC_DIR)/fs/procfs.c \
    $(SRC_DIR)/fs/epoll.c \
    $(SRC_DIR)/fs/fs_syscall.c \
    $(SRC_DIR)/fs/vvfs.c \
    $(SRC_DIR)/fs/vvfs_codec.c \
    $(SRC_DIR)/fs/vvfs_transport.c \
    $(SRC_DIR)/fs/vvfs_model_verify.c \
    $(SRC_DIR)/fs/vbus_fs.c \
    $(SRC_DIR)/exec/elf.c \
    $(SRC_DIR)/exec/exec.c \
    $(SRC_DIR)/exec/exec_syscall.c \
    $(SRC_DIR)/time/time_syscall.c \
    $(SRC_DIR)/init/init.c \
    $(SRC_DIR)/init/init_binaries.c \
    $(SRC_DIR)/core/vos3_config.c \
    $(SRC_DIR)/core/memcpy.c \
    $(SRC_DIR)/core/kaslr.c \
    $(SRC_DIR)/core/stack_protector.c \
    $(SRC_DIR)/core/crc32c.c \
    $(SRC_DIR)/core/crc64.c \
    $(SRC_DIR)/core/cet.c \
    $(SRC_DIR)/mm/ai_prefetch.c \
    $(SRC_DIR)/drivers/virtio_net.c \
    $(SRC_DIR)/drivers/virtio_blk.c \
    $(SRC_DIR)/drivers/virtio_core.c \
    $(SRC_DIR)/drivers/virtio_vbus.c \
    $(SRC_DIR)/drivers/virtio_bridge.c \
    $(SRC_DIR)/drivers/vbus_transport.c \
    $(SRC_DIR)/drivers/vbus_fs_cmds.c \
    $(SRC_DIR)/drivers/vbus_ai_cmds.c \
    $(SRC_DIR)/drivers/vbus_diag_cmds.c \
    $(SRC_DIR)/drivers/vbus_app_cmds.c \
    $(SRC_DIR)/drivers/pci.c \
    $(SRC_DIR)/drivers/acpi.c \
    $(SRC_DIR)/drivers/storage_hal.c \
    $(SRC_DIR)/drivers/nvme.c \
    $(SRC_DIR)/drivers/ivshmem.c \
    $(SRC_DIR)/drivers/virtio_gpu.c \
    $(SRC_DIR)/drivers/gpu_compute.c \
    $(SRC_DIR)/drivers/npu.c \
    $(SRC_DIR)/drivers/vbus_transport_avx.c \
    $(SRC_DIR)/drivers/hda_audio.c \
    $(SRC_DIR)/ai/driver_synth.c \
    $(SRC_DIR)/ai/npu_dispatch.c \
    $(SRC_DIR)/drivers/accel.c \
    $(SRC_DIR)/drivers/dma_warp.c \
    $(SRC_DIR)/drivers/net_stress.c \
    $(SRC_DIR)/net/ethernet.c \
    $(SRC_DIR)/net/arp.c \
    $(SRC_DIR)/net/ip.c \
    $(SRC_DIR)/net/icmp.c \
    $(SRC_DIR)/net/udp.c \
    $(SRC_DIR)/net/tcp.c \
    $(SRC_DIR)/net/socket.c \
    $(SRC_DIR)/net/dns.c \
    $(SRC_DIR)/net/day4_security_test.c \
    $(SRC_DIR)/mm/gpu_mem.c \
    $(SRC_DIR)/mm/ai_kim.c \
    $(SRC_DIR)/sched/per_cpu.c \
    $(SRC_DIR)/crypto/entropy.c \
    $(SRC_DIR)/crypto/sha256.c \
    $(SRC_DIR)/crypto/sha384.c \
    $(SRC_DIR)/crypto/sha512.c \
    $(SRC_DIR)/crypto/ed25519_verify.c \
    $(SRC_DIR)/crypto/mlkem768.c \
    $(SRC_DIR)/crypto/crypto_helpers.c \
    $(SRC_DIR)/crypto/x25519.c \
    $(SRC_DIR)/crypto/aes_gcm.c \
    $(SRC_DIR)/crypto/tls13.c \
    $(SRC_DIR)/net/dns_tls.c \
    $(SRC_DIR)/net/http.c \
    $(SRC_DIR)/ai/model_download.c \
    $(SRC_DIR)/ai/ggml_core.c \
    $(SRC_DIR)/ai/gguf_loader.c \
    $(SRC_DIR)/bench/bench_hooks.c \
    $(SRC_DIR)/tests/bench_kim_neural.c \
    $(SRC_DIR)/tests/bench_kim_multi_core.c \
    $(SRC_DIR)/tests/vbus_replay_test.c \
    $(SRC_DIR)/tests/tls_integrity_test.c \
    $(SRC_DIR)/tests/test_phase21_audit.c \
    $(SRC_DIR)/tests/test_phase21_extreme.c \
    $(SRC_DIR)/tests/test_phase21_divine.c \
    $(SRC_DIR)/ai/spec_decoder.c \
    $(SRC_DIR)/mm/ai_context_manager.c \
    $(SRC_DIR)/tests/test_phase22_velocity.c \
    $(SRC_DIR)/tests/test_phase22_audit.c \
    $(SRC_DIR)/ai/hybrid_orch.c \
    $(SRC_DIR)/mm/npu_affinity.c \
    $(SRC_DIR)/tests/test_phase23_audit.c \
    $(SRC_DIR)/tests/test_phase23_vortex.c \
    $(SRC_DIR)/fs/vector_vfs.c \
    $(SRC_DIR)/ai/sni_orchestrator.c \
    $(SRC_DIR)/tests/test_phase31_audit.c \
    $(SRC_DIR)/ai/vscreen_parser.c \
    $(SRC_DIR)/ai/agent_mesh.c \
    $(SRC_DIR)/exec/action_bridge.c \
    $(SRC_DIR)/tests/test_phase41_audit.c \
    $(SRC_DIR)/tests/test_phase41_extreme.c \
    $(SRC_DIR)/tests/test_phase41_omega.c \
    $(SRC_DIR)/ui/vspace_shell.c \
    $(SRC_DIR)/sec/pud_sandbox.c \
    $(SRC_DIR)/ai/sovereign_clipboard.c \
    $(SRC_DIR)/tests/test_phase51_audit.c \
    $(SRC_DIR)/tests/test_phase51_infinity.c \
    $(SRC_DIR)/tests/test_phase51_singularity.c \
    $(SRC_DIR)/tests/test_phase51_event_horizon.c \
    $(SRC_DIR)/tests/test_phase51_quantum_void.c \
    $(SRC_DIR)/sec/immutable_lock.c \
    $(SRC_DIR)/sec/recovery_bridge.c \
    $(SRC_DIR)/sec/sovereign_watermark.c \
    $(SRC_DIR)/sec/mmr_audit.c \
    $(SRC_DIR)/sec/tpm2.c \
    $(SRC_DIR)/sec/hybrid_key.c \
    $(SRC_DIR)/sec/capability_table.c \
    $(SRC_DIR)/tests/test_phase61_audit.c \
    $(SRC_DIR)/tests/test_phase61_event_horizon.c \
    $(SRC_DIR)/tests/test_phase62_absolute_zero.c \
    $(SRC_DIR)/tests/test_phase70_genesis_gate.c \
    $(SRC_DIR)/tests/test_phase81_velocity.c \
    $(SRC_DIR)/tests/test_phase82_endurance.c \
    $(SRC_DIR)/tests/test_phase10_omega.c \
    $(SRC_DIR)/hyperv/hyperv_bridge.c \
    $(SRC_DIR)/hyperv/hyperv_init.c

ifeq ($(PROCESS_ROOTS_TEST),1)
C_SOURCES += $(SRC_DIR)/tests/test_process_roots.c
CFLAGS += -DVOS3_PROCESS_ROOTS_TEST
endif

# Assembly sources
ifeq ($(NATIVE_ISOLATION_TEST),1)
C_SOURCES += $(SRC_DIR)/tests/test_native_isolation.c
endif

# Assembly sources
ASM_SOURCES := \
    $(SRC_DIR)/boot/multiboot2_entry.S \
    $(SRC_DIR)/boot/entry.S \
    $(SRC_DIR)/arch/x86_64/isr_stubs.S \
    $(SRC_DIR)/arch/x86_64/syscall_entry.S \
    $(SRC_DIR)/arch/x86_64/user_entry.S \
    $(SRC_DIR)/arch/x86_64/trampoline.S \
    $(SRC_DIR)/sched/context.S

# Object files
C_OBJECTS   := $(patsubst $(SRC_DIR)/%.c,$(OBJ_DIR)/%.o,$(C_SOURCES)) $(OBJ_DIR)/init/embedded_bins.o
ASM_OBJECTS := $(patsubst $(SRC_DIR)/%.S,$(OBJ_DIR)/%.o,$(ASM_SOURCES))
OBJECTS     := $(C_OBJECTS) $(ASM_OBJECTS)

# Dependencies
DEPS := $(OBJECTS:.o=.d)

# ============================================================================
# PRODUCTION BUILD
# ============================================================================

# v21.3.1 (Track A) — VOS3_BUILD_TYPE → preprocessor gate for PRO-only code.
#
# Open-Core split per docs/strategy/OPEN_CORE_LICENSING.md: certain symbols
# (e.g. kv_compressor_dedupe_hint) live under #ifdef VOS3_PRO so they are
# never emitted in CORE builds. The wrapper script tools/check_open_core_split.sh
# verifies the link-graph segregation; tests A11/A12 in
# backend/tests/audit/test_stress_75_rounds.py verify the COMPILER
# segregation via x86_64-elf-nm on kv_compressor.o.
#
# Without this gate, VOS3_PRO is never defined and PRO-only symbols
# silently disappear from PRO builds — exactly the v21.3.1 inline-edit
# gap surfaced by the TRUTH-BRIDGE forensic protocol (test A12 failure).
ifeq ($(VOS3_BUILD_TYPE),PRO)
CFLAGS += -DVOS3_PRO
endif

# Open-core build: explicit CORE branch so the VOS3_BUILD_TYPE contract is
# symmetric (CORE | PRO) and self-documenting. CORE defines no PRO gates — the
# kernel runs the full open-core security infrastructure with the conservative
# (non-enterprise) feature ceilings.
ifeq ($(VOS3_BUILD_TYPE),CORE)
CFLAGS += -DVOS3_CORE
endif

# Boot mode: HEADLESS_AUDIT skips provisioning wizard and spawns init directly.
# Moved from a source-level #define (PC01 audit finding) to a build flag so it
# can be suppressed on production images with HEADLESS_AUDIT=0.
ifdef PRODUCTION
HEADLESS_AUDIT ?= 0
endif
ifneq ($(HEADLESS_AUDIT),0)
CFLAGS += -DHEADLESS_AUDIT
endif

# Explicit diagnostic image; never enabled by a normal native build.
ifeq ($(NATIVE_ISOLATION_TEST),1)
ifneq ($(HEADLESS_AUDIT),1)
$(error NATIVE_ISOLATION_TEST=1 requires HEADLESS_AUDIT=1)
endif
CFLAGS += -DNATIVE_ISOLATION_TEST
endif
ifeq ($(MEMORY_TRANSITIONS_TEST),1)
ifneq ($(NATIVE_ISOLATION_TEST),1)
$(error MEMORY_TRANSITIONS_TEST=1 requires NATIVE_ISOLATION_TEST=1)
endif
CFLAGS += -DMEMORY_TRANSITIONS_TEST
endif
ifeq ($(NATIVE_SMP_WORKLOAD),1)
ifneq ($(HEADLESS_AUDIT),1)
$(error NATIVE_SMP_WORKLOAD=1 requires HEADLESS_AUDIT=1)
endif
ifneq ($(filter 1,$(NATIVE_ISOLATION_TEST) $(MEMORY_TRANSITIONS_TEST)),)
$(error NATIVE_SMP_WORKLOAD requires a separate diagnostic image)
endif
CFLAGS += -DNATIVE_SMP_WORKLOAD
endif
ifeq ($(NATIVE_SMP_TEST),1)
ifneq ($(NATIVE_SMP_WORKLOAD),1)
$(error NATIVE_SMP_TEST=1 requires NATIVE_SMP_WORKLOAD=1)
endif
CFLAGS += -DNATIVE_SMP_TEST
endif

ifdef PRODUCTION
CFLAGS += -DVOS3_PRODUCTION_BUILD
# Keep informational/security events without debug output on every switch.
CFLAGS += -DVOS3_LOG_MIN_LEVEL=1 -DVOS3_SERIAL_MIN_LEVEL=1
# Strip debug info in production
CFLAGS := $(subst -g,,$(CFLAGS))
# OpenSSF 2026 hardening (applicable to freestanding)
CFLAGS += -ftrivial-auto-var-init=zero
CFLAGS += -fno-delete-null-pointer-checks
# Optimize crypto and AI hot paths to -O3
$(OBJ_DIR)/crypto/%.o: CFLAGS := $(subst -O2,-O3,$(CFLAGS))
$(OBJ_DIR)/ai/%.o: CFLAGS := $(subst -O2,-O3,$(CFLAGS))
$(OBJ_DIR)/mm/ai_kim.o: CFLAGS := $(subst -O2,-O3,$(CFLAGS))
$(OBJ_DIR)/mm/ai_kv_managed.o: CFLAGS := $(subst -O2,-O3,$(CFLAGS))
$(OBJ_DIR)/mm/ai_kv_compress.o: CFLAGS := $(subst -O2,-O3,$(CFLAGS))
endif

# ============================================================================
# v21.3 HARDWARE-ACTIVATION GATES (M1 / M2 / M3)
# ============================================================================
#
# All three flags default to OFF. Set ONE flag at a time on the command
# line (or in the deployment build env) to activate the corresponding
# subsystem. Per the Blind-Implementation Protocol in
# docs/plans/V21_3_TOTAL_SUPREMACY_PLAN.md §0, no commit may simultaneously
# enable two HW flags by default — the env-var pattern below preserves
# that discipline.
#
# Example downstream activation:
#     make VOS3_BUILD_TYPE=PRO VOS3_HW_XSAVE=1
#     make VOS3_BUILD_TYPE=PRO VOS3_HW_LAPIC=1
#     make VOS3_BUILD_TYPE=PRO VOS3_HW_IOAPIC=1
#
# Setting VOS3_HW_XSAVE=1 also implies VOS3_XSAVE_LIVE so the existing
# inline xsave64/xrstor64 code paths in src/sched/xsave_ctx.c become
# active. The HW prerequisites (CR4.OSXSAVE + XCR0) are programmed by
# vos3_xsave_boot_init(), which is itself gated by VOS3_HW_XSAVE — a
# single flag flips both source layers.

ifeq ($(VOS3_HW_XSAVE),1)
CFLAGS += -DVOS3_HW_XSAVE -DVOS3_XSAVE_LIVE
endif

ifeq ($(VOS3_HW_LAPIC),1)
CFLAGS += -DVOS3_HW_LAPIC
endif

ifeq ($(VOS3_HW_IOAPIC),1)
CFLAGS += -DVOS3_HW_IOAPIC
endif

# M4 — Certification Assertion Harness (default OFF).
#
# When enabled, every VOS3_ASSERT_CERT(id, expr) in the source tree
# emits a "~~CERT~~ <id> <PASS|FAIL> <site> <expr>" line on COM1
# (port 0x3F8) at the moment of the assertion. The Python runner at
# tools/runner/qemu_assert_runner.py boots VOS3 in QEMU, captures the
# serial stream, and verifies every populated cert ID emits exactly
# once with PASS status.
#
# Activation:
#     make VOS3_BUILD_TYPE=PRO VOS3_ASSERT_HARNESS=1
#
# Default builds: VOS3_ASSERT_CERT() expands to ((void)(expr)) — no
# emit, no I/O cost, no runtime change.

ifeq ($(VOS3_ASSERT_HARNESS),1)
CFLAGS += -DVOS3_ASSERT_HARNESS
endif

# ============================================================================
# BUILD RULES
# ============================================================================

.PHONY: all clean iso qemu qemu-bridge qemu-iso debug debug-iso boot-check dump info check help efi qemu-efi release release-kernel release-bundle audit-no-memcmp-in-crypto kernel-linux kernel-baremetal kernel-hyperv multi-target kernel-community kernel-enterprise kernel-fortress

all: $(BUILD_DIR)/$(KERNEL)

# ============================================================================
# Stage 14 — Three-target reproducible build pipeline
#
# kernel-linux        → build/vos3.elf            (Linux/QEMU multiboot2 — REAL)
# kernel-baremetal    → build/vos3.efi            (Bare-metal UEFI — STUB,
#                                                  honest scope: PE/COFF wrapper
#                                                  via objcopy; full UEFI runtime
#                                                  is Stage 14.D.2)
# kernel-hyperv       → build/vos3-hyperv.elf     (Windows Hyper-V variant —
#                                                  separate build symbol, real
#                                                  divergence pending Hyper-V
#                                                  synth-driver port in Stage 14.D.3)
# multi-target        → produces all three sequentially
#
# All three targets honour the same SOURCE_DATE_EPOCH / --build-id=none /
# -ffile-prefix-map flags, so they are bit-for-bit reproducible across
# rebuilds with the same toolchain.
# ============================================================================

kernel-linux: $(BUILD_DIR)/$(KERNEL)
	@echo ""
	@echo "Stage-14 kernel-linux: $(BUILD_DIR)/$(KERNEL)"
	@shasum -a 256 $(BUILD_DIR)/$(KERNEL) || sha256sum $(BUILD_DIR)/$(KERNEL) || true

# Bare-metal UEFI variant — honest stub.
# objcopy --target=efi-app-x86_64 wraps the existing ELF in a PE/COFF
# envelope an UEFI loader will accept as a binary. This is NOT a full
# UEFI-runtime conformant kernel — the multiboot2 entry path still
# applies. A real UEFI app needs UEFI services (BootServices, RuntimeServices)
# that are absent from the current entry. Deferred to Stage 14.D.2.
# kernel-baremetal delegates to the existing `efi` target (which has its
# own dedicated build rule using src/boot/efi_stub.c). Stage 14 adds the
# canonical kernel-baremetal alias so the multi-target contract is met
# under the standard naming.
kernel-baremetal: efi
	@echo "Stage-14 kernel-baremetal: $(BUILD_DIR)/vos3.efi"
	@shasum -a 256 $(BUILD_DIR)/vos3.efi 2>/dev/null || sha256sum $(BUILD_DIR)/vos3.efi || true

# Windows Hyper-V variant — Stage 14.D.3 genuine divergence.
#
# Builds the entire kernel a SECOND TIME with -DVOS3_TARGET_HYPERV +
# -DHYPERV_EXTENDED_ISOLATION via a recursive sub-make into a separate
# build directory (build-hyperv/). The sub-make invokes the same recipe
# but with:
#   BUILD_DIR     = build-hyperv      (so .o files don't clobber the Linux build)
#   OBJ_DIR       = build-hyperv/obj
#   KERNEL        = vos3-hyperv.elf
#   EXTRA_CFLAGS  += -DVOS3_TARGET_HYPERV -DHYPERV_EXTENDED_ISOLATION
#
# Result: kernel/src/hyperv/hyperv_init.c compiles in real Hyper-V init
# code (TLFS v6.0b §3.6 / §3.13 / §10.3 — see source comments) which is
# absent from the Linux build. SHA-256 differs as a result.
#
# After the sub-make completes, the result is moved to build/vos3-hyperv.elf
# so the multi-target manifest sees all three artefacts in one place.
kernel-hyperv $(BUILD_DIR)/vos3-hyperv.elf:
	@echo "  RECURSIVE-MAKE  $(BUILD_DIR)/vos3-hyperv.elf  (Stage-14.D.3 -DVOS3_TARGET_HYPERV)"
	@$(MAKE) --no-print-directory \
	    BUILD_DIR=build-hyperv \
	    OBJ_DIR=build-hyperv/obj \
	    KERNEL=vos3-hyperv.elf \
	    EXTRA_CFLAGS="-DVOS3_TARGET_HYPERV -DHYPERV_EXTENDED_ISOLATION" \
	    build-hyperv/vos3-hyperv.elf
	@mkdir -p $(BUILD_DIR)
	@cp -p build-hyperv/vos3-hyperv.elf $(BUILD_DIR)/vos3-hyperv.elf
	@echo "Stage-14.D.3 kernel-hyperv: $(BUILD_DIR)/vos3-hyperv.elf"
	@shasum -a 256 $(BUILD_DIR)/vos3-hyperv.elf 2>/dev/null \
	    || sha256sum $(BUILD_DIR)/vos3-hyperv.elf || true

# ============================================================================
# Zero-Gap Task 1 — VOS_PROFILE composite targets.
#
# The three deployment profiles map 1:1 to the backend/vos_profile.py
# Profile enum and the kernel-side macros in include/vos/profile.h:
#
#   kernel-community   -> -DVOS3_PROFILE=0  (default)
#   kernel-enterprise  -> -DVOS3_PROFILE=1
#   kernel-fortress    -> -DVOS3_PROFILE=2  + Hyper-V build
#
# The fortress target is the canonical Hyper-V kernel image: it implies
# -DVOS3_TARGET_HYPERV so the SynIC/hypercall init in src/hyperv/ links
# in. A fortress-profile deployment that intends to run on bare metal
# can override by invoking `kernel-fortress VOS3_TARGET_HYPERV=`, but
# the default ships the Hyper-V variant per docs/PROFILES.md.
# ============================================================================

kernel-community:
	@echo "  PROFILE  community"
	@$(MAKE) --no-print-directory \
	    EXTRA_CFLAGS="-DVOS3_PROFILE=0" \
	    $(BUILD_DIR)/$(KERNEL)
	@shasum -a 256 $(BUILD_DIR)/$(KERNEL) 2>/dev/null \
	    || sha256sum $(BUILD_DIR)/$(KERNEL) || true

kernel-enterprise:
	@echo "  PROFILE  enterprise"
	@$(MAKE) --no-print-directory \
	    EXTRA_CFLAGS="-DVOS3_PROFILE=1" \
	    $(BUILD_DIR)/$(KERNEL)
	@shasum -a 256 $(BUILD_DIR)/$(KERNEL) 2>/dev/null \
	    || sha256sum $(BUILD_DIR)/$(KERNEL) || true

kernel-fortress:
	@echo "  PROFILE  fortress (Hyper-V build)"
	@$(MAKE) --no-print-directory \
	    BUILD_DIR=build-fortress \
	    OBJ_DIR=build-fortress/obj \
	    KERNEL=vos3-fortress.elf \
	    EXTRA_CFLAGS="-DVOS3_PROFILE=2 -DVOS3_TARGET_HYPERV -DHYPERV_EXTENDED_ISOLATION" \
	    build-fortress/vos3-fortress.elf
	@mkdir -p $(BUILD_DIR)
	@cp -p build-fortress/vos3-fortress.elf $(BUILD_DIR)/vos3-fortress.elf
	@echo "Fortress kernel: $(BUILD_DIR)/vos3-fortress.elf"
	@shasum -a 256 $(BUILD_DIR)/vos3-fortress.elf 2>/dev/null \
	    || sha256sum $(BUILD_DIR)/vos3-fortress.elf || true

multi-target: kernel-linux kernel-baremetal kernel-hyperv
	@echo ""
	@echo "============================================================"
	@echo "Stage 14 multi-target build complete:"
	@echo "  Linux ELF:      $(BUILD_DIR)/$(KERNEL)"
	@echo "  Bare-metal EFI: $(BUILD_DIR)/vos3.efi"
	@echo "  Hyper-V ELF:    $(BUILD_DIR)/vos3-hyperv.elf"
	@echo "============================================================"
	@echo "SHA-256 manifest (suitable for docs/PROVENANCE.md):"
	@for f in $(BUILD_DIR)/$(KERNEL) $(BUILD_DIR)/vos3.efi $(BUILD_DIR)/vos3-hyperv.elf ; do \
	    if command -v shasum >/dev/null 2>&1 ; then \
	        shasum -a 256 $$f ; \
	    else \
	        sha256sum $$f ; \
	    fi ; \
	done

# Link kernel
$(BUILD_DIR)/$(KERNEL): $(OBJECTS) linker.ld | $(BUILD_DIR)
	@echo "  LD      $@"
	@$(LD) $(LDFLAGS) -o $@ $(OBJECTS)
	@echo ""
	@echo "Kernel built: $@"
	@$(OBJDUMP) -h $@ | grep -E "^\s+[0-9]+"
	@echo ""
	@$(CROSS)size $@ || true

# AI tensor files require SSE2 for float return values (x86_64 ABI: XMM0).
# These are task-context-only (KIM dispatcher) — never called from ISR.
# FPU state is per-task, saved/restored via FXSAVE/FXRSTOR on context switch.
$(OBJ_DIR)/ai/ggml_core.o: CFLAGS += -msse -msse2 -mfpmath=sse
$(OBJ_DIR)/tests/bench_kim_neural.o: CFLAGS += -msse -msse2 -mfpmath=sse

# KIM inference: SSE2 for float tensor operations (FXSAVE/FXRSTOR on context switch)
$(OBJ_DIR)/mm/ai_kim.o: CFLAGS += -msse -msse2 -mfpmath=sse

# TLS integrity test: requires SSE for FPU guard state isolation test (Test 9)
$(OBJ_DIR)/tests/tls_integrity_test.o: CFLAGS += -msse -msse2

# AES-GCM: GPR-only for now. When AES-NI fast-path is added, enable:
# $(OBJ_DIR)/crypto/aes_gcm.o: CFLAGS += -msse -msse2 -maes -mpclmul

# C-CRIT-1 (v20.6 → restored 2026-05-02): forbid memcmp() in crypto-adjacent
# translation units. memcmp short-circuits on first byte mismatch and leaks
# per-byte timing, enabling timing-oracle forgery against HMAC verification.
# Use vos3_ct_equal() (kernel/src/crypto/crypto_helpers.c) for any
# authentication-tag comparison.
#
# Annotate intentional non-crypto memcmp uses with the comment marker
# "/* not crypto:" on the same line to suppress the gate.
audit-no-memcmp-in-crypto:
	@echo "  AUDIT   no-memcmp-in-crypto"
	@if grep -rn 'memcmp\s*(' $(SRC_DIR)/crypto/ $(SRC_DIR)/drivers/vbus_transport.c $(SRC_DIR)/drivers/virtio_vbus.c 2>/dev/null | grep -v '/\* not crypto:'; then \
		echo "ERROR: memcmp found in crypto-adjacent path — use vos3_ct_equal() for authentication-tag comparison."; \
		exit 1; \
	fi
	@echo "  OK      crypto paths free of memcmp"

# Compile C sources
$(OBJ_DIR)/%.o: $(SRC_DIR)/%.c | $(OBJ_DIR)
	@mkdir -p $(dir $@)
	@echo "  CC      $<"
	@$(CC) $(CFLAGS) -MMD -MP -c $< -o $@

# Assemble assembly sources
$(OBJ_DIR)/%.o: $(SRC_DIR)/%.S | $(OBJ_DIR)
	@mkdir -p $(dir $@)
	@echo "  AS      $<"
	@$(CC) $(CFLAGS) -MMD -MP -c $< -o $@

# Create directories
$(BUILD_DIR) $(OBJ_DIR):
	@mkdir -p $@

# Clean
clean:
	@echo "Cleaning..."
	@rm -rf $(BUILD_DIR)

# Include dependencies
-include $(DEPS)

# ============================================================================
# EMBEDDED USER BINARIES
# ============================================================================

# User binaries directory
USER_BUILD_DIR := $(BUILD_DIR)/user
USER_BIN_DIR := $(USER_BUILD_DIR)/bin

# User programs to embed
include ../user/programs.mk
USER_PROGRAMS := $(PROGRAMS)

# Each build flavor owns its generated source. Never rewrite tracked source
# files or share an append-in-place output between concurrent kernel builds.
$(BUILD_DIR)/generated/embedded_bins.c: tools/embed_binaries.py
	@echo "  GEN     $@"
	@python3 tools/embed_binaries.py --output "$@" --bin-dir "$(USER_BIN_DIR)" $(USER_PROGRAMS)

$(OBJ_DIR)/init/embedded_bins.o: $(BUILD_DIR)/generated/embedded_bins.c | $(OBJ_DIR)
	@mkdir -p "$(dir $@)"
	@echo "  CC      $<"
	@$(CC) $(CFLAGS) -MMD -MP -c "$<" -o "$@"

# Allow bench mode to be controlled from kernel build: make BENCH_MODE=1
# This propagates BENCH_MODE to user/Makefile which sets -DHEADLESS_BENCH_SUITE=1
# Also suppresses VOS3_DEBUG logging to avoid serial console bottleneck (Day 20 fix)
ifeq ($(BENCH_MODE),1)
_USER_BENCH := BENCH_MODE=$(BENCH_MODE)
CFLAGS += -DVOS3_LOG_MIN_LEVEL=1
CFLAGS += -DVOS3_SERIAL_MIN_LEVEL=1
ifdef TEST_ONLY
_USER_BENCH += TEST_ONLY=$(TEST_ONLY)
endif
else
_USER_BENCH :=
endif

# Ensure user binaries are built first — single stamp target prevents
# parallel make from invoking sub-make 100+ times concurrently.
.PHONY: FORCE_USER_BUILD
FORCE_USER_BUILD:
$(USER_BIN_DIR)/.stamp: FORCE_USER_BUILD
	@$(MAKE) -C ../user BUILD_DIR="../kernel/$(USER_BUILD_DIR)" $(_USER_BENCH)
	@python3 tools/build_config.py "$@" VOS_EMBED_ $(addprefix $(USER_BIN_DIR)/,$(USER_PROGRAMS))

$(BUILD_DIR)/generated/embedded_bins.c: $(USER_BIN_DIR)/.stamp ../user/programs.mk

# ============================================================================
# ISO IMAGE
# ============================================================================

# --------------------------------------------------------------------------
# ISO build targets — vOS·Adaptive·SHA=aeb3736·Phase=P-deploy
#
# Two artifacts:
#   * $(ISO)           — kernel/build/vos3.iso  (for `qemu-iso`, in-tree CI)
#   * $(INSTALLER_ISO) — dist/vos3_installer.iso (for bare-metal deploy)
#
# `make iso` (this directive's primary entry point) delegates to
# infra/build_iso.sh which produces the deployment artifact at
# $(INSTALLER_ISO). The script handles xorriso + mtools preflight,
# generates grub.cfg with `multiboot2 /boot/vos3.elf; boot`, and
# verifies the output is a valid ISO 9660 image. See infra/build_iso.sh
# for the full build pipeline + docs/DEPLOYMENT.md for flashing
# instructions.
# --------------------------------------------------------------------------

ISO           := $(BUILD_DIR)/vos3.iso
INSTALLER_ISO ?= ../dist/vos5.iso

# Packaging inputs are distinct from the kernel: bootloader/config changes must
# refresh an existing image even when the kernel's bytes stay the same.
LIMINE_BUILD_DIR ?= build/limine
export LIMINE_BUILD_DIR
ISO_BOOT_INPUTS := $(addprefix $(LIMINE_BUILD_DIR)/bin/,limine limine-bios.sys limine-bios-cd.bin limine-uefi-cd.bin BOOTX64.EFI BOOTIA32.EFI)
export VOS_ISO_LIMINE_BUILD_DIR := $(LIMINE_BUILD_DIR)
export VOS_ISO_XORRISO := $(shell xorriso -version 2>/dev/null | head -n 1)
.PHONY: FORCE_ISO_CONFIG
FORCE_ISO_CONFIG:
$(BUILD_DIR)/iso-config.json: FORCE_ISO_CONFIG | $(BUILD_DIR)
	@python3 tools/build_config.py "$@" VOS_ISO_ ../infra/build_iso.sh $(ISO_BOOT_INPUTS)


# Primary `make iso` — produces the deployment installer ISO via the
# infra/build_iso.sh wrapper. Output: dist/vos3_installer.iso.
iso: $(INSTALLER_ISO)

# A shared installer path may previously contain another build flavor. Its
# output-side stamp binds it to this invocation's exact kernel and boot inputs.
.PHONY: FORCE_INSTALLER_CONFIG
FORCE_INSTALLER_CONFIG:
$(INSTALLER_ISO).build-config.json: $(BUILD_DIR)/$(KERNEL) $(BUILD_DIR)/iso-config.json FORCE_INSTALLER_CONFIG
	@python3 tools/build_config.py "$@" VOS_ISO_ "$(BUILD_DIR)/$(KERNEL)" "$(BUILD_DIR)/iso-config.json"

$(INSTALLER_ISO): $(INSTALLER_ISO).build-config.json
	@echo "=== Building vOS installer ISO ==="
	@KERNEL_ELF="$(CURDIR)/$(BUILD_DIR)/$(KERNEL)" \
	 OUTPUT_ISO="$(INSTALLER_ISO)" \
	 STAGING_DIR="$(CURDIR)/$(BUILD_DIR)/iso_installer" \
	 bash "$(CURDIR)/../infra/build_iso.sh"
	@echo "=== Installer ISO complete: $(INSTALLER_ISO) ==="

# In-tree ISO for qemu-iso target — same kernel, different output path,
# uses the same wrapper script so the build logic stays single-sourced.
$(ISO): $(BUILD_DIR)/$(KERNEL) $(BUILD_DIR)/iso-config.json
	@echo "=== Building in-tree ISO for qemu-iso ==="
	@KERNEL_ELF="$(CURDIR)/$(BUILD_DIR)/$(KERNEL)" \
	 OUTPUT_ISO="$(CURDIR)/$(ISO)" \
	 STAGING_DIR="$(CURDIR)/$(ISO_DIR)" \
	 bash "$(CURDIR)/../infra/build_iso.sh"
	@echo "=== In-tree ISO complete: $(ISO) ==="

# ============================================================================
# QEMU
# ============================================================================

QEMU_FLAGS := -m 1024M \
              -cpu max \
              -smp 2 \
              -serial stdio \
              -no-reboot \
              -no-shutdown \
              -nic user,model=virtio-net-pci

qemu: $(BUILD_DIR)/$(KERNEL)
	@echo "Starting QEMU with direct kernel boot (PVH)..."
	$(QEMU) -kernel $< $(QEMU_FLAGS) -display none

# Run with serial bridge on COM2 (unix socket, explicit chardev mapping)
# COM1 = console (file log), COM2 = bridge (unix socket, persistent connection required)
qemu-bridge: $(BUILD_DIR)/$(KERNEL)
	@echo "Starting QEMU with serial bridge (COM2 -> /tmp/vos3_bridge.sock)..."
	$(QEMU) -kernel $< -m 1024M \
	    -chardev file,id=con,path=/tmp/vos3_console.log \
	    -serial chardev:con \
	    -chardev socket,id=bridge,path=/tmp/vos3_bridge.sock,server=on,wait=off \
	    -serial chardev:bridge \
	    -drive file=disk.img,format=raw,if=none,id=disk0,cache=directsync \
	    -device virtio-blk-pci,drive=disk0 \
	    -nic user,model=virtio-net-pci \
	    -display none

# Run with VBus binary transport (virtio-serial-pci, Phase 4.1)
# COM1 = console (file log), VBus = binary bridge (virtio-serial over unix socket)
qemu-vbus: $(BUILD_DIR)/$(KERNEL)
	@echo "Starting QEMU with VBus binary bridge (virtio-serial-pci)..."
	$(QEMU) -kernel $< -m 1024M -smp 2 \
	    -chardev file,id=con,path=/tmp/vos3_console.log \
	    -serial chardev:con \
	    -chardev socket,id=vbus,path=/tmp/vos3_bridge.sock,server=on,wait=off \
	    -device virtio-serial-pci \
	    -device virtconsole,chardev=vbus \
	    -drive file=disk.img,format=raw,if=none,id=disk0,cache=directsync \
	    -device virtio-blk-pci,drive=disk0 \
	    -nic user,model=virtio-net-pci \
	    -display none -daemonize -pidfile /tmp/vos3_qemu.pid

# ============================================================================
# UEFI BUILD (Phase 8.1 — PE32+ EFI Application)
# ============================================================================

# EFI stub specific settings
EFI_SRC     := $(SRC_DIR)/boot/efi_stub.c
EFI_OBJ_DIR := $(BUILD_DIR)/efi_obj
EFI_ELF     := $(BUILD_DIR)/vos3_efi.elf
EFI_IMAGE   := $(BUILD_DIR)/vos3.efi
EFI_LINKER  := efi_linker.ld

# OVMF firmware (QEMU bundled EDK2 — Homebrew location)
OVMF_CODE   := /opt/homebrew/share/qemu/edk2-x86_64-code.fd

# Extract kernel_main address from built kernel ELF (required for EFI stub)
# The EFI stub jumps to this address after switching to VOS3 page tables.
KERNEL_ENTRY_ADDR = $(shell $(CROSS)nm $(BUILD_DIR)/$(KERNEL) 2>/dev/null \
    | grep " T kernel_main" | awk '{print "0x" $$1}')

# EFI-specific CFLAGS:
#   -mabi=ms        Microsoft x64 calling convention (UEFI requirement)
#   -DVOS3_EFI_STUB Activates the efi_stub.c code (dormant otherwise)
#   -mno-red-zone   Required for kernel/firmware code (interrupts can clobber)
#   -fno-stack-protector  No __stack_chk_fail in freestanding PE32+
#   -fshort-wchar   UEFI uses 16-bit wchar_t (UCS-2)
#   -DVOS3_KERNEL_ENTRY  Virtual address of kernel_main() from kernel ELF
# Stage 14: lazy expansion (`=` not `:=`) so KERNEL_ENTRY_ADDR's
# $(shell nm ...) defers until the efi-stub compile actually runs —
# by which point kernel-linux has produced build/vos3.elf and nm
# can read kernel_main's address. Without this, `make multi-target`
# on a clean tree fails at Makefile-parse time because nm sees nothing.
EFI_CFLAGS = -std=c11 -ffreestanding -fno-stack-protector \
              -fno-pic -fno-pie -fno-common \
              -mno-red-zone -mno-mmx -mno-sse -mno-sse2 \
              -march=x86-64 -mtune=generic $(APX_FLAG) \
              -m64 -mabi=ms -fshort-wchar \
              -I$(INC_DIR) \
              -DVOS3_EFI_STUB \
              -DVOS3_KERNEL_ENTRY=$(KERNEL_ENTRY_ADDR)ULL \
              $(WARNINGS) \
              -O2 -g

EFI_LDFLAGS := -nostdlib -static -z max-page-size=0x1000 \
               -T $(EFI_LINKER)

# Build the PE32+ EFI application
efi: $(EFI_IMAGE)

$(EFI_IMAGE): $(EFI_ELF)
	@echo "  OBJCOPY $@ (PE32+ EFI application)"
	@$(OBJCOPY) -j .text -j .data -j .bss -j .reloc \
		-O pei-x86-64 --subsystem=10 $< $@
	@echo ""
	@echo "EFI application built: $@"
	@ls -lh $@

$(EFI_ELF): $(EFI_OBJ_DIR)/efi_stub.o $(EFI_LINKER) | $(BUILD_DIR)
	@echo "  LD      $@"
	@$(LD) $(EFI_LDFLAGS) -o $@ $(EFI_OBJ_DIR)/efi_stub.o

# EFI stub depends on kernel ELF (for KERNEL_ENTRY_ADDR extraction)
$(EFI_OBJ_DIR)/efi_stub.o: $(EFI_SRC) $(BUILD_DIR)/$(KERNEL) | $(EFI_OBJ_DIR)
	@if [ -z "$(KERNEL_ENTRY_ADDR)" ]; then \
		echo "  [FAIL] Cannot extract kernel_main address from $(BUILD_DIR)/$(KERNEL)"; \
		echo "  Build the kernel first: make"; \
		exit 1; \
	fi
	@echo "  CC [EFI] $< (kernel_main=$(KERNEL_ENTRY_ADDR))"
	@$(CC) $(EFI_CFLAGS) -MMD -MP -c $< -o $@

$(EFI_OBJ_DIR):
	@mkdir -p $@

# Run VOS3 EFI application in QEMU with OVMF firmware
# Creates a FAT disk image with the EFI binary at /EFI/BOOT/BOOTX64.EFI
qemu-efi: $(EFI_IMAGE)
	@echo "=== Launching VOS3 via UEFI (OVMF) ==="
	@if [ ! -f "$(OVMF_CODE)" ]; then \
		echo "  [FAIL] OVMF not found at $(OVMF_CODE)"; \
		echo "  Install: brew install qemu (includes EDK2 firmware)"; \
		exit 1; \
	fi
	@echo "  Creating EFI System Partition disk image..."
	@mkdir -p $(BUILD_DIR)/esp/EFI/BOOT
	@cp $(EFI_IMAGE) $(BUILD_DIR)/esp/EFI/BOOT/BOOTX64.EFI
	@# Also copy the kernel ELF to the ESP (the EFI stub loads it)
	@if [ -f "$(BUILD_DIR)/$(KERNEL)" ]; then \
		cp $(BUILD_DIR)/$(KERNEL) $(BUILD_DIR)/esp/vos3.elf; \
	fi
	@# Create a FAT32 disk image (64MB)
	@dd if=/dev/zero of=$(BUILD_DIR)/efi_disk.img bs=1m count=64 2>/dev/null
	@# Format as FAT32 — use platform-appropriate tool
	@if command -v mkfs.fat >/dev/null 2>&1; then \
		mkfs.fat -F 32 $(BUILD_DIR)/efi_disk.img; \
	elif command -v newfs_msdos >/dev/null 2>&1; then \
		newfs_msdos -F 32 $(BUILD_DIR)/efi_disk.img; \
	else \
		echo "  [WARN] No FAT formatter found — using raw image"; \
	fi
	@# Copy files to FAT image using mtools if available
	@if command -v mcopy >/dev/null 2>&1; then \
		mmd -i $(BUILD_DIR)/efi_disk.img ::/EFI; \
		mmd -i $(BUILD_DIR)/efi_disk.img ::/EFI/BOOT; \
		mcopy -i $(BUILD_DIR)/efi_disk.img $(EFI_IMAGE) ::/EFI/BOOT/BOOTX64.EFI; \
	else \
		echo "  [WARN] mtools not found — install with: brew install mtools"; \
		echo "  Using hda fat approach instead..."; \
	fi
	@echo "  Launching QEMU with OVMF..."
	$(QEMU) \
		-bios $(OVMF_CODE) \
		-drive file=$(BUILD_DIR)/efi_disk.img,format=raw,if=virtio \
		-m 4096M -smp 2 -cpu max \
		-serial stdio \
		-no-reboot -no-shutdown \
		-display none

# Boot from ISO (correct method for Multiboot2)
qemu-iso: $(ISO)
	@echo "Starting QEMU from ISO..."
	$(QEMU) -cdrom $< $(QEMU_FLAGS)

# Debug with GDB
debug: $(BUILD_DIR)/$(KERNEL)
	@echo "Starting QEMU in debug mode..."
	@echo "Connect GDB with: target remote localhost:1234"
	$(QEMU) -kernel $< $(QEMU_FLAGS) -s -S

# Debug from ISO with GDB
debug-iso: $(ISO)
	@echo "Starting QEMU ISO in debug mode..."
	@echo "Connect GDB with: target remote localhost:1234"
	$(QEMU) -cdrom $< $(QEMU_FLAGS) -s -S

# ============================================================================
# BOOT DIAGNOSTICS
# ============================================================================

boot-check: $(BUILD_DIR)/$(KERNEL)
	@echo "=== VOS3 Boot Diagnostics ==="
	@echo ""
	@echo "[1] Checking grub-mkrescue..."
	@GRUB_MKRESCUE="$$(command -v grub-mkrescue 2>/dev/null || command -v x86_64-elf-grub-mkrescue 2>/dev/null)"; \
	if [ -n "$$GRUB_MKRESCUE" ]; then \
		echo "  [OK] Found: $$GRUB_MKRESCUE"; \
		$$GRUB_MKRESCUE --version 2>/dev/null || true; \
	else \
		echo "  [FAIL] grub-mkrescue not found"; \
	fi
	@echo ""
	@echo "[2] Checking Multiboot2 header..."
	@if $(OBJDUMP) -h $< | grep -q "\.multiboot2"; then \
		echo "  [OK] .multiboot2 section exists"; \
		$(OBJDUMP) -h $< | grep "\.multiboot2"; \
	else \
		echo "  [FAIL] .multiboot2 section missing"; \
	fi
	@echo ""
	@echo "[3] Checking Multiboot2 magic (0xE85250D6)..."
	@$(OBJDUMP) -s -j .multiboot2 $< 2>/dev/null | head -20 || echo "  [FAIL] Cannot dump section"
	@echo ""
	@echo "[4] Checking kernel entry point..."
	@$(OBJDUMP) -f $< | grep "start address"
	@echo ""
	@echo "[5] Checking kernel size..."
	@ls -lh $<
	@echo ""
	@echo "[6] Checking QEMU..."
	@if command -v $(QEMU) >/dev/null 2>&1; then \
		echo "  [OK] $(QEMU) found"; \
		$(QEMU) --version | head -1; \
	else \
		echo "  [FAIL] $(QEMU) not found"; \
	fi
	@echo ""
	@echo "=== Diagnostics complete ==="

# ============================================================================
# UTILITIES
# ============================================================================

# Disassemble kernel
dump: $(BUILD_DIR)/$(KERNEL)
	$(OBJDUMP) -d $< | less

# Show kernel info
info: $(BUILD_DIR)/$(KERNEL)
	@echo "=== Kernel Sections ==="
	$(OBJDUMP) -h $<
	@echo ""
	@echo "=== Kernel Symbols ==="
	$(OBJDUMP) -t $< | grep -E "^[0-9a-f]+ [gw]" | sort

# Check for undefined symbols
check: $(BUILD_DIR)/$(KERNEL)
	@echo "Checking for undefined symbols..."
	@nm $< | grep " U " && echo "ERROR: Undefined symbols found!" && exit 1 || \
		echo "OK: No undefined symbols"

# ============================================================================
# HELP
# ============================================================================

help:
	@echo "VOS3 Kernel Build System"
	@echo ""
	@echo "Targets:"
	@echo "  all        - Build kernel ELF (default)"
	@echo "  efi        - Build UEFI PE32+ boot application (vos3.efi)"
	@echo "  clean      - Remove build artifacts"
	@echo "  iso        - Create bootable ISO"
	@echo "  qemu       - Run in QEMU (direct kernel, Linux boot protocol)"
	@echo "  qemu-efi   - Run in QEMU via OVMF (UEFI boot path)"
	@echo "  qemu-iso   - Run in QEMU from ISO (Multiboot2, recommended)"
	@echo "  debug      - Run in QEMU with GDB server"
	@echo "  debug-iso  - Run ISO in QEMU with GDB server"
	@echo "  boot-check - Diagnose boot configuration"
	@echo "  dump       - Disassemble kernel"
	@echo "  info       - Show kernel sections and symbols"
	@echo "  check      - Check for undefined symbols"
	@echo "  release    - Build hardened production binary (Genesis RC1)"
	@echo "  help       - Show this help"

# ============================================================================
# PRODUCTION RELEASE
# ============================================================================
#
# `make release` (orchestrator) — vOS·Adaptive·SHA=aeb3736·Phase=P-deploy
# Composes:
#   1. release-kernel   — production-hardened kernel ELF
#   2. iso              — dist/vos3_installer.iso via infra/build_iso.sh
#   3. release-bundle   — multi-platform dists + dist/release_v1_0.zip
#
# Run from kernel/:  make release
#
# Or run any sub-target standalone:
#   make release-kernel    # production binary only
#   make iso               # installer ISO only
#   make release-bundle    # bundle previously-built artifacts into the zip
# ----------------------------------------------------------------------------

# Distribution artefact paths (also referenced by infra/generate_dist.sh).
DIST_DIR_ABS  := $(CURDIR)/../dist
RELEASE_ZIP   := $(DIST_DIR_ABS)/release_v1_0.zip

# The orchestrator. Order matters: kernel → ISO → multi-platform → zip.
release: release-kernel iso release-bundle
	@echo ""
	@echo "=== vOS Release Complete ==="
	@ls -lh $(RELEASE_ZIP) 2>/dev/null || echo "  (release zip missing — re-run release-bundle)"

# Production-hardened kernel — original release body, renamed so it can
# be invoked standalone or chained from `release`.
release-kernel:
	@echo "=== VOS3 Genesis RC1 — Production Build ==="
	@$(MAKE) clean
	@$(MAKE) PRODUCTION=1
	@echo ""
	@echo "=== Post-Link Hardening ==="
	@$(OBJCOPY) --remove-section=.comment $(BUILD_DIR)/$(KERNEL) 2>/dev/null || true
	@echo "  [OK] Stripped .comment section (compiler metadata)"
	@echo ""
	@echo "=== Production Build Complete ==="
	@echo "  Binary: $(BUILD_DIR)/$(KERNEL)"
	@ls -lh $(BUILD_DIR)/$(KERNEL)
	@echo "  Test symbols (expect 0):"
	@$(CROSS)nm $(BUILD_DIR)/$(KERNEL) | grep -c "test_phase" || echo "  0 test_phase symbols — CLEAN"
	@echo "  Test assertion .rodata strings (expect 0):"
	@$(CROSS)strings $(BUILD_DIR)/$(KERNEL) | grep -c "END_ASSERT\|EXT_ASSERT\|DIV_ASSERT\|VEL_ASSERT\|PASS.*Phase\|FAIL.*Phase\|SOVEREIGN GRADE\|DIVINE GRADE\|TRANSCENDENT" || echo "  0 test strings — CLEAN"

# Multi-platform dists + zip bundle.
# Depends on the installer ISO already existing — usually via `make iso`
# which the `release` orchestrator chains in front of us.
release-bundle: $(INSTALLER_ISO)
	@echo "=== vOS Multi-Platform Distribution ==="
	@bash "$(CURDIR)/../infra/generate_dist.sh"
	@echo ""
	@echo "=== Packaging release zip ==="
	@cd $(DIST_DIR_ABS) && rm -f release_v1_0.zip
	@cd $(DIST_DIR_ABS) && zip -r release_v1_0.zip \
	    vos3_installer.iso \
	    vos3_linux_kvm.qcow2 \
	    vos3_windows_hyperv.vhdx \
	    vos3_macos_portable.utm
	@cd $(DIST_DIR_ABS) && cp ../docs/DEPLOYMENT.md ../docs/PLATFORM_GUIDE.md . 2>/dev/null || true
	@cd $(DIST_DIR_ABS) && zip release_v1_0.zip DEPLOYMENT.md PLATFORM_GUIDE.md 2>/dev/null || true
	@cd $(DIST_DIR_ABS) && rm -f DEPLOYMENT.md PLATFORM_GUIDE.md
	@echo ""
	@echo "  Bundle: $(RELEASE_ZIP)"
	@ls -lh $(RELEASE_ZIP)
	@(shasum -a 256 $(RELEASE_ZIP) 2>/dev/null || sha256sum $(RELEASE_ZIP)) | awk '{print "  SHA256: " $$1}'

# Snapshot after all flag additions. Export values directly rather than placing
# compiler flags (which may contain quotes/spaces) inside shell command text.
export VOS_CONFIG_CC := $(CC)
export VOS_CONFIG_LD := $(LD)
export VOS_CONFIG_CC_VERSION := $(shell $(CC) --version | head -n 1)
export VOS_CONFIG_LD_VERSION := $(shell $(LD) --version | head -n 1)
export VOS_CONFIG_CFLAGS := $(CFLAGS)
export VOS_CONFIG_LDFLAGS := $(LDFLAGS)
export VOS_CONFIG_EPOCH := $(SOURCE_DATE_EPOCH)
export VOS_CONFIG_SOURCES := $(C_SOURCES) $(ASM_SOURCES)
BUILD_CONFIG := $(BUILD_DIR)/build-config.json
.PHONY: FORCE_BUILD_CONFIG
FORCE_BUILD_CONFIG:
$(BUILD_CONFIG): FORCE_BUILD_CONFIG tools/build_config.py | $(BUILD_DIR)
	@python3 tools/build_config.py "$@"
$(OBJECTS): $(BUILD_CONFIG) Makefile
$(BUILD_DIR)/$(KERNEL): $(BUILD_CONFIG)
