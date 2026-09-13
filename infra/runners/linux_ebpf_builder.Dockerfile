# syntax=docker/dockerfile:1
#
# vOS — Isolated Linux eBPF LSM build-gate (Finding B3-1, Phase 19.2)
# =================================================================
#
# Purpose: compile the REAL eBPF LSM write-gate program
# (kernel/src/sec/taint_gate.c) with __VOS3_TAINT_GATE_REAL_BPF_BUILD=1 and
# -target bpf, on a Linux toolchain, so the host-platform compilation barrier
# (macOS dev hosts have no clang-bpf / libbpf / Linux headers) is bridged
# WITHOUT faking the result. This is the build half of closing B3-1; the
# *runtime* half (load + attach + race-under-load) still needs a live Linux
# >= 5.17 BPF-LSM runner (see docs/design/vOS_B31_eBPF_Race_Closure_Spec.md §7).
#
# Kernel-version target: Ubuntu 24.04 ships kernel/libc headers for 6.8, which
# satisfies the >= 5.17 floor that the bpf_loop() helper (per-byte mode) needs.
#
# HONEST BUILD NOTES (read before assuming a clean compile):
#   * LSM BPF programs reference kernel context types (struct file/socket/
#     msghdr). Where those fields are READ, a BTF-generated `vmlinux.h` is
#     required; this skeleton only takes their ADDRESSES (never dereferences
#     fields), so it compiles against opaque forward declarations — no BTF /
#     bpftool / vmlinux.h dependency. That keeps the build-gate runnable on any
#     Docker host (incl. Docker Desktop on macOS) without host kernel BTF. A
#     CO-RE build that reads kernel fields would add vmlinux.h here.
#   * bpf_loop() requires libbpf >= 0.7 (bpf_helper_defs.h) + kernel >= 5.17 at
#     LOAD time; 24.04's libbpf-dev declares it. The compile asserts
#     -Wall -Wextra -Werror so any drift fails the gate.
#
# Multi-stage:
#   Stage `toolchain` — the pinned clang/llvm/libbpf/header stack (cacheable).
#   Stage `build`     — compiles taint_gate.o at container run time.

# ---------------------------------------------------------------------------
# Stage 1 — toolchain base
# ---------------------------------------------------------------------------
FROM ubuntu:24.04 AS toolchain

ENV DEBIAN_FRONTEND=noninteractive

# clang/llvm for -target bpf; libbpf-dev for <bpf/bpf_helpers.h> +
# bpf_helper_defs.h (declares bpf_loop); linux-libc-dev for UAPI
# <linux/bpf.h> / <linux/errno.h>; linux-tools-generic ships the `bpftool`
# binary used by the Phase-19.4 load-simulation harness (`bpftool gen skeleton`
# is static codegen — kernel-version-agnostic — so the versioned binary is
# invoked by path, not via the uname-matching /usr/sbin wrapper).
RUN apt-get update && apt-get install -y --no-install-recommends \
        clang \
        llvm \
        libbpf-dev \
        linux-libc-dev \
        linux-tools-generic \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Record the toolchain versions into the image for provenance.
RUN clang --version > /opt/toolchain_versions.txt 2>&1 \
    && (dpkg -s libbpf-dev | grep -E '^Version' >> /opt/toolchain_versions.txt 2>&1 || true) \
    && (find /usr/lib/linux-tools* -name bpftool -type f 2>/dev/null | head -1 \
        | xargs -r -I{} sh -c '{} version | head -1 >> /opt/toolchain_versions.txt' 2>&1 || true)

# ---------------------------------------------------------------------------
# Stage 2 — build the eBPF object
# ---------------------------------------------------------------------------
FROM toolchain AS build

WORKDIR /work
# The wrapper bind-mounts the repo at /work (read-only) and /sys/kernel/btf.
# Defaults below are overridable via `docker build --build-arg`.
ARG TAINT_SRC=kernel/src/sec/taint_gate.c
ARG OUT_OBJ=/out/taint_gate.o
ARG KERNEL_INCLUDE=kernel/include

COPY build_in_container.sh /usr/local/bin/build_in_container.sh
RUN chmod +x /usr/local/bin/build_in_container.sh

ENV TAINT_SRC=${TAINT_SRC} \
    OUT_OBJ=${OUT_OBJ} \
    KERNEL_INCLUDE=${KERNEL_INCLUDE}

# The actual compile runs at container *run* time (so it can see the bind-
# mounted source + host BTF), not at image-build time.
ENTRYPOINT ["/usr/local/bin/build_in_container.sh"]
