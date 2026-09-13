# =============================================================================
# Sprint 18 / Wave 3.B — eBPF LSM build for Cluster C-2 (taint_gate)
# =============================================================================
#
# This Makefile compiles kernel/src/sec/taint_gate.c with the BPF target
# and produces an object file loadable by `bpftool prog load`. It is
# SEPARATE from the main kernel/Makefile because:
#
#   1. The main kernel Makefile uses CROSS=x86_64-elf- for the vOS
#      microkernel itself — those flags are wrong for eBPF programs.
#   2. eBPF requires clang -target bpf (gcc cannot emit BPF bytecode
#      that the kernel verifier accepts).
#   3. The dependencies (libbpf-dev, clang ≥ 11, bpftool, bpffs mount)
#      are Linux-only and shouldn't gate the macOS dev kernel build.
#
# Build & load workflow (Linux ≥ 5.7 with bpffs mounted at /sys/fs/bpf):
#
#     # From repo root:
#     cd kernel/bpf
#     make
#
#     # Result: kernel/bpf/taint_gate.bpf.o
#
#     # Load + pin (requires root):
#     sudo mkdir -p /sys/fs/bpf/vos3
#     sudo bpftool prog load \
#         kernel/bpf/taint_gate.bpf.o \
#         /sys/fs/bpf/vos3/taint_gate_prog \
#         autoattach
#
#     # Verify load:
#     sudo bpftool prog show pinned /sys/fs/bpf/vos3/taint_gate_prog
#     sudo bpftool map show pinned /sys/fs/bpf/vos3/taint_colors
#
#     # Unload:
#     sudo rm /sys/fs/bpf/vos3/taint_gate_prog
#     sudo rm /sys/fs/bpf/vos3/taint_colors
#     sudo rm /sys/fs/bpf/vos3/taint_audit_ring
#
# Dependencies (Debian/Ubuntu):
#     sudo apt-get install -y clang llvm libbpf-dev linux-headers-$(uname -r) \
#                              linux-tools-$(uname -r) libelf-dev
#
# Dependencies (Fedora/RHEL):
#     sudo dnf install -y clang llvm libbpf-devel kernel-devel bpftool elfutils-libelf-devel
#
# Honest scope ceiling — Sprint 18 status:
#   - The eBPF source file (kernel/src/sec/taint_gate.c) is gated
#     behind __VOS3_TAINT_GATE_REAL_BPF_BUILD so the vOS kernel CI
#     skips it. This Makefile defines that macro on the clang
#     command line, unlocking the real build.
#   - The verifier-clean MVP needs additional bound-annotation work
#     for the per-byte mode (Wave 3.C stretch). Per-fd max-color
#     mode (the Wave 3.B target) is verifier-trivial.
#   - We cannot validate this Makefile from the macOS dev host. CI
#     on the Linux runner (.github/workflows/silicon_battery.yml or
#     a new dedicated eBPF runner — Sprint 19 work) does the
#     end-to-end validation.

CLANG    ?= clang
LLC      ?= llc
BPFTOOL  ?= bpftool

KERNEL_INCLUDE := ../include
SRC            := ../src/sec/taint_gate.c
OBJ            := taint_gate.bpf.o

# clang flags per the libbpf project's recommended build line.
# -O2                        — verifier rejects -O0 (unrolled loops only)
# -g                         — emit BTF for relocatable BPF (CO-RE)
# -target bpf                — BPF bytecode output
# -D__VOS3_TAINT_GATE_REAL_BPF_BUILD — un-gate the C source
# -I ../../include           — pick up <vos/taint_maps.h>
# -Wall -Werror              — strict; verifier rejects sloppy code anyway
# -Wno-unused-function       — bpf_helpers.h declares many
# -Wno-pragma-once-outside-header — libbpf headers triggers
BPF_CFLAGS := \
    -O2 -g -target bpf \
    -D__VOS3_TAINT_GATE_REAL_BPF_BUILD \
    -I$(KERNEL_INCLUDE) \
    -Wall -Werror \
    -Wno-unused-function \
    -Wno-pragma-once-outside-header

.PHONY: all clean verify load unload

all: $(OBJ)

$(OBJ): $(SRC)
	$(CLANG) $(BPF_CFLAGS) -c $(SRC) -o $(OBJ)
	@echo ""
	@echo "Built $(OBJ). Next: sudo $(BPFTOOL) prog load $(OBJ) /sys/fs/bpf/vos3/taint_gate_prog autoattach"

verify: $(OBJ)
	# Dry-run verifier check without committing the load.
	# Requires CAP_BPF / root.
	sudo $(BPFTOOL) prog load $(OBJ) /tmp/__vos3_taint_gate_verify_only \
	    autoattach 2>&1 && sudo rm /tmp/__vos3_taint_gate_verify_only
	@echo "Verifier accepted $(OBJ)."

load: $(OBJ)
	sudo mkdir -p /sys/fs/bpf/vos3
	sudo $(BPFTOOL) prog load $(OBJ) \
	    /sys/fs/bpf/vos3/taint_gate_prog autoattach
	@echo "Loaded. Map at /sys/fs/bpf/vos3/taint_colors should now exist."

unload:
	-sudo rm -f /sys/fs/bpf/vos3/taint_gate_prog
	-sudo rm -f /sys/fs/bpf/vos3/taint_colors
	-sudo rm -f /sys/fs/bpf/vos3/taint_audit_ring

clean:
	rm -f $(OBJ)
