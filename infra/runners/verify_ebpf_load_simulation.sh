#!/usr/bin/env bash
#
# vOS — Phase 19.4 eBPF LOAD-SIMULATION harness (Finding B3-1).
# ===========================================================================
#
# WHAT THIS IS — and IS NOT:
#   This is a STATIC load-SIMULATION / pre-flight, NOT the kernel verifier.
#   The real eBPF verifier (pointer/register safety, callback safety, the
#   complexity budget) runs ONLY at BPF_PROG_LOAD time on a live Linux >= 5.17
#   kernel with CONFIG_BPF_LSM=y + `lsm=...,bpf`. That is NOT performed here;
#   `bpftool gen skeleton` and the ELF checks below run with NO kernel load.
#   A clean run here is necessary, not sufficient, to close B3-1 — the moat
#   stays UNCHANGED. Do not read a green result as "verifier-clean".
#
# WHAT IT CHECKS (all inside the vos-ebpf-builder:b31 container):
#   1. Compiles kernel/src/sec/taint_gate.c (reuses build_in_container.sh).
#   2. ELF/section sanity: BPF ELF, lsm program sections present, .maps + BTF
#      present, section alignments sane (8-byte for .maps/programs).
#   3. `bpftool gen skeleton`: proves libbpf can statically consume the object —
#      all maps + programs resolve and map into a userspace skeleton with NO
#      relocation collisions (the gen step fails otherwise).
#   4. BTF contract: the in-object map value/key type sizes match taint_maps.h
#      (vos3_taint_color_entry == 65568, vos3_taint_map_key == 8) — the G5
#      byte-for-byte guarantee, cross-checked against the host B3-MARK-PUSH probe.
#
# Exit 0 + zero warnings on success; non-zero with a clear message otherwise.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-vos-ebpf-builder:b31}"
OUT_DIR="$REPO_ROOT/kernel/build/bpf"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "FATAL: Docker daemon not reachable — cannot run the eBPF load-simulation." >&2
    exit 2
fi
if ! docker image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
    echo "FATAL: image $IMAGE_TAG not found — build it first via run_isolated_ebpf_build.sh." >&2
    exit 2
fi

mkdir -p "$OUT_DIR"

docker run --rm \
    -v "$REPO_ROOT:/work:ro" \
    -v "$OUT_DIR:/out" \
    -w /work \
    --entrypoint bash \
    "$IMAGE_TAG" -c '
set -euo pipefail
OBJ=/out/taint_gate.o
FAILS=0
chk() { if eval "$2"; then echo "  [PASS] $1"; else echo "  [FAIL] $1"; FAILS=$((FAILS+1)); fi; }

echo "== Phase 19.4 load-simulation (STATIC; NOT the kernel verifier) =="

echo "-- 0. compile --"
/usr/local/bin/build_in_container.sh >/tmp/build.log 2>&1 || { echo "BUILD FAILED"; cat /tmp/build.log; exit 1; }
echo "  object: $(stat -c%s "$OBJ") bytes"

echo "-- 1. ELF + section sanity --"
HDR="$(llvm-readelf -h "$OBJ")"
chk "ELF machine is BPF"                 "echo \"\$HDR\" | grep -qiE \"Machine:.*(BPF|Linux BPF)\""
SECS="$(llvm-readelf -S "$OBJ")"
chk "lsm/file_permission section present" "echo \"\$SECS\" | grep -q \"lsm/file_permission\""
chk "lsm/socket_sendmsg section present"  "echo \"\$SECS\" | grep -q \"lsm/socket_sendmsg\""
chk ".maps section present"               "echo \"\$SECS\" | grep -qE \"\] .maps\""
chk ".BTF section present"                "echo \"\$SECS\" | grep -qE \"\] .BTF\""
# .maps and program sections must be 8-byte aligned for libbpf load.
MAPS_AL="$(echo "$SECS" | awk "/\] .maps/{print \$NF}")"
chk ".maps alignment == 8 (got ${MAPS_AL:-?})" "[ \"\${MAPS_AL:-0}\" = \"8\" ]"

echo "-- 2. bpftool gen skeleton (static libbpf consumption; no kernel) --"
BPFTOOL="$(find /usr/lib/linux-tools* -name bpftool -type f 2>/dev/null | head -1)"
chk "bpftool binary located"             "[ -n \"$BPFTOOL\" ]"
"$BPFTOOL" gen skeleton "$OBJ" > /tmp/skel.h 2>/tmp/skel.err
chk "gen skeleton exit 0"                "[ -s /tmp/skel.h ]"
chk "gen skeleton emitted NO stderr"     "[ ! -s /tmp/skel.err ]"
[ -s /tmp/skel.err ] && { echo "    stderr:"; sed "s/^/      /" /tmp/skel.err; }
for M in taint_colors taint_marks taint_audit_ring; do
    chk "map \"$M\" maps into userspace skeleton" "grep -q \"struct bpf_map \*$M;\" /tmp/skel.h"
done
for P in vos3_taint_gate_file_perm vos3_taint_gate_sendmsg; do
    chk "program \"$P\" present in skeleton"       "grep -q \"struct bpf_program \*$P;\" /tmp/skel.h"
done

echo "-- 3. BTF contract vs taint_maps.h (G5) --"
# Dump to a FILE and grep the file: the CO-RE build embeds far more BTF, and
# eval-grepping a multi-KB shell variable is fragile. (The authoritative G5
# check is the host B3-MARK-PUSH sizeof probe; this corroborates it in the
# compiled object.)
"$BPFTOOL" btf dump file "$OBJ" > /tmp/vos3_obj.btf 2>/dev/null || true
chk "vos3_taint_color_entry BTF size == 65568" "grep -qE \"STRUCT .vos3_taint_color_entry. size=65568\" /tmp/vos3_obj.btf"
chk "vos3_taint_map_key BTF size == 8"         "grep -qE \"STRUCT .vos3_taint_map_key. size=8 \" /tmp/vos3_obj.btf"

echo
if [ "$FAILS" -eq 0 ]; then
    echo "== B3-LOAD-SIM PASSED (static pre-flight; verifier/load on a live >=5.17 BPF-LSM kernel still required) =="
    exit 0
fi
echo "== B3-LOAD-SIM FAILED ($FAILS check(s)) =="
exit 1
'
