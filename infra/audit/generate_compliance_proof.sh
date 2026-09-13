#!/usr/bin/env bash
#
# vOS — B3-1 compliance EVIDENCE bundler (Phase 21).
# ===========================================================================
#
# WHAT THIS IS — and IS NOT (read before citing the output):
#   This SELF-COLLECTS runtime evidence about the live eBPF LSM write-gate
#   (object hash, BTF struct sizes vs taint_maps.h, loaded maps, active LSM
#   links, kernel/LSM facts) into docs/audit/compliance_payload.json. It is
#   INPUT FOR an external auditor — it is NOT itself an external audit, and it
#   does NOT by itself justify advancing the moat tally. A self-generated proof
#   bundle is self-certification; the moat advance still requires an independent
#   third-party eBPF/LSM audit (cf. the M3 precedent in the master ledger).
#
# Runs the collection inside the privileged vos-ebpf-builder:b31 container
# (needs a live Linux >= 5.17 BPF-LSM kernel). Fails honestly without it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-vos-ebpf-builder:b31}"
OUT_JSON="$REPO_ROOT/docs/audit/compliance_payload.json"
STAMP="${1:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
mkdir -p "$REPO_ROOT/docs/audit"

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "FATAL: Docker daemon unreachable — cannot collect live LSM evidence." >&2
    exit 2
fi
docker image inspect "$IMAGE_TAG" >/dev/null 2>&1 || {
    echo "FATAL: image $IMAGE_TAG not found — build via run_isolated_ebpf_build.sh." >&2; exit 2; }

# Host-side schema hash of the kernel struct contract (stable audit input).
SCHEMA_HASH="$( sed -n '/struct vos3_taint_color_entry/,/};/p;/struct vos3_taint_map_key/,/};/p;/struct vos3_taint_mark_push_arg/,/};/p' \
    "$REPO_ROOT/kernel/include/vos/taint_maps.h" | shasum -a 256 | cut -d' ' -f1)"

# Collect raw "key value" lines from the live container (simple, quote-safe).
EV="$(docker run --rm --privileged \
        -v "$REPO_ROOT:/work:ro" -v "$REPO_ROOT/kernel/build/bpf:/out" -w /work \
        --entrypoint bash "$IMAGE_TAG" -c '
set +e
mount -t bpf bpf /sys/fs/bpf 2>/dev/null
mount -t securityfs none /sys/kernel/security 2>/dev/null
/usr/local/bin/build_in_container.sh >/tmp/b.log 2>&1 || { echo "build_failed 1"; exit 0; }
BPF=$(find /usr/lib/linux-tools* -name bpftool -type f | head -1)
OBJ=/out/taint_gate.o
rm -rf /sys/fs/bpf/tg; mkdir -p /sys/fs/bpf/tg
"$BPF" prog loadall "$OBJ" /sys/fs/bpf/tg autoattach 2>/dev/null; echo "loadrc $?"
echo "kernel $(uname -r)"
echo "lsm $(cat /sys/kernel/security/lsm 2>/dev/null || echo unknown)"
grep -qw bpf /sys/kernel/security/lsm 2>/dev/null && echo "bpf_active true" || echo "bpf_active false"
echo "obj_sha $(sha256sum "$OBJ" | cut -d" " -f1)"
echo "links $("$BPF" link show 2>/dev/null | grep -c "prog_type lsm")"
echo "maps $("$BPF" map show 2>/dev/null | grep -cE "taint_")"
echo "ce $("$BPF" btf dump file "$OBJ" 2>/dev/null | sed -nE "s/.*STRUCT .vos3_taint_color_entry. size=([0-9]+).*/\1/p" | head -1)"
echo "ke $("$BPF" btf dump file "$OBJ" 2>/dev/null | sed -nE "s/.*STRUCT .vos3_taint_map_key. size=([0-9]+).*/\1/p" | head -1)"
' )"

get(){ echo "$EV" | awk -v k="$1" '$1==k{print $2}' | head -1; }
KERNEL="$(get kernel)"; LSM="$(get lsm)"; BPFA="$(get bpf_active)"; OBJSHA="$(get obj_sha)"
LOADRC="$(get loadrc)"; LINKS="$(get links)"; MAPS="$(get maps)"; CE="$(get ce)"; KE="$(get ke)"
G5OK=false; [ "$CE" = "65568" ] && [ "$KE" = "8" ] && G5OK=true

cat > "$OUT_JSON" <<JSON
{
  "_disclaimer": "SELF-COLLECTED runtime evidence for external-audit INPUT. NOT an external audit. Does NOT by itself advance the moat tally.",
  "generated_utc": "${STAMP}",
  "kernel": "${KERNEL:-unknown}",
  "active_lsm_list": "${LSM:-unknown}",
  "bpf_lsm_active": ${BPFA:-false},
  "object_sha256": "${OBJSHA:-null}",
  "verifier_loadall_rc": ${LOADRC:-null},
  "lsm_links_attached": ${LINKS:-0},
  "taint_maps_loaded": ${MAPS:-0},
  "btf_struct_sizes": { "vos3_taint_color_entry": ${CE:-null}, "vos3_taint_map_key": ${KE:-null} },
  "g5_contract_ok": ${G5OK},
  "taint_maps_schema_sha256": "${SCHEMA_HASH}",
  "external_audit_status": "PENDING — no independent third-party audit performed",
  "moat_impact": "none (self-certification does not advance the 49/80 tally)"
}
JSON
echo "wrote $OUT_JSON"
cat "$OUT_JSON"
