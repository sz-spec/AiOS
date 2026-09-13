#!/usr/bin/env bash
#
# vOS — host wrapper for the isolated Linux eBPF LSM build-gate (B3-1 / 19.2).
# ===========================================================================
#
# Builds the linux_ebpf_builder image and runs it to compile
# kernel/src/sec/taint_gate.c into a clean taint_gate.o with -target bpf and
# __VOS3_TAINT_GATE_REAL_BPF_BUILD=1. Asserts exit 0 AND zero compiler warnings.
#
# Requirements (the script checks them and fails HONESTLY if unmet):
#   - a running Docker daemon.
# No host kernel BTF is needed: the skeleton compiles against opaque forward
# declarations of the LSM context types (see linux_ebpf_builder.Dockerfile).
#
# Usage:
#   infra/runners/run_isolated_ebpf_build.sh            # build + compile
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKERFILE="$REPO_ROOT/infra/runners/linux_ebpf_builder.Dockerfile"
IMAGE_TAG="vos-ebpf-builder:b31"
OUT_DIR="$REPO_ROOT/kernel/build/bpf"

mkdir -p "$OUT_DIR"

# --- 0. preflight: Docker daemon -------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "FATAL: docker CLI not found on PATH." >&2
    exit 2
fi
if ! docker info >/dev/null 2>&1; then
    echo "FATAL: Docker daemon is not reachable (is Docker Desktop / dockerd running?)." >&2
    echo "       This build-gate CANNOT be certified without it; nothing was compiled." >&2
    exit 2
fi

# --- 1. build the image ----------------------------------------------------
echo "=== building $IMAGE_TAG ==="
# The builder copies build_in_container.sh from the build context.
docker build -f "$DOCKERFILE" -t "$IMAGE_TAG" "$REPO_ROOT/infra/runners"

# --- 2. run the compile ----------------------------------------------------
echo "=== compiling taint_gate.c inside the container ==="
RUN_ARGS=(
    --rm
    -v "$REPO_ROOT:/work:ro"
    -v "$OUT_DIR:/out"
    -w /work
)

if docker run "${RUN_ARGS[@]}" "$IMAGE_TAG"; then
    echo
    echo "BUILD-GATE PASSED: kernel/build/bpf/taint_gate.o compiled clean (exit 0, zero warnings)."
    ls -l "$OUT_DIR/taint_gate.o"
    exit 0
else
    rc=$?
    echo
    echo "BUILD-GATE FAILED (rc=$rc) — taint_gate.o NOT certified. See output above." >&2
    exit "$rc"
fi
