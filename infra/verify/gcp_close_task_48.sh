#!/usr/bin/env bash
# ============================================================================
# vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C·GCP-escape
#
# GCP-based closing script for engagement task #48 (PROTECTED_FULL KPTI
# verification). Solves the GHA nested-KVM deadlock by renting a real
# bare-metal-virt-capable GCE instance, running the verification on it,
# pulling back the summary artifact, and self-destructing the instance.
#
# Footprint
# ---------
# * Instance type:    n2-standard-2 (2 vCPU, 8 GB, nested-virt supported)
# * Image:            ubuntu-2404-lts
# * Lifetime:         ~5 minutes (create → boot → build → verify → delete)
# * Cost:             ~$0.01 per invocation (n2-standard-2 hourly ÷ 12)
# * Self-destruct:    `gcloud compute instances delete` runs from a TRAP
#                     so the instance is deleted even on script abort.
#
# Preflight
# ---------
# 1. `gcloud auth login` — interactive once, then cached.
# 2. `gcloud config set project <YOUR_PROJECT>` — or pass GCP_PROJECT env.
# 3. Compute Engine API enabled on the project (one-time per project).
# 4. Caller's account has roles/compute.instanceAdmin.v1 + iam.serviceAccountUser
#    OR is the Project Editor / Owner.
#
# Usage
# -----
#   bash infra/verify/gcp_close_task_48.sh
#
#   # Or override defaults:
#   GCP_PROJECT=my-project GCP_ZONE=us-east1-b \
#       bash infra/verify/gcp_close_task_48.sh
#
# Deliverable
# -----------
# On success:  infra/verify/reports/GCP_EVIDENCE_AAA.summary.txt
# Plus printed to stdout. The summary file is then committable as the
# closing artifact for task #48.
#
# Honest scope
# ------------
# * Builds the kernel ON the GCE instance from rsync'd source — does
#   NOT assume the local dev host has the x86_64-elf cross-compiler
#   (Apple Silicon hosts won't).
# * Verifies only the BOOT path, same as the local script. Workload
#   stress is `kernel/run_tests.sh`'s job.
# * The TRAP cleanup is best-effort: if the gcloud delete itself fails
#   (auth expired, quota issue), the operator must clean up manually.
#   The instance name is printed at every step so it's easy to find.
# ============================================================================

set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

GCP_PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
GCP_ZONE="${GCP_ZONE:-us-central1-a}"
GCP_REGION="${GCP_REGION:-${GCP_ZONE%-*}}"
GCP_MACHINE_TYPE="${GCP_MACHINE_TYPE:-n2-standard-2}"
GCP_IMAGE_FAMILY="${GCP_IMAGE_FAMILY:-ubuntu-2404-lts-amd64}"
GCP_IMAGE_PROJECT="${GCP_IMAGE_PROJECT:-ubuntu-os-cloud}"

# Instance name is unique-per-run so two operators can run concurrently
# without colliding. Pattern: vos-kvm-verify-<short-sha>-<utc>.
SHORT_SHA="$(git -C "$REPO_ROOT" rev-parse --short=8 HEAD 2>/dev/null || echo "nogit")"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
INSTANCE_NAME="${INSTANCE_NAME:-vos-kvm-verify-${SHORT_SHA}-${TIMESTAMP,,}}"

REPORTS_DIR="${REPORTS_DIR:-${SCRIPT_DIR}/reports}"
EVIDENCE_PATH="${REPORTS_DIR}/GCP_EVIDENCE_AAA.summary.txt"

# ANSI helpers
if [[ -t 1 ]]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
    BLUE=$'\033[0;34m'; BOLD=$'\033[1m'; NC=$'\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; BLUE=""; BOLD=""; NC=""
fi

step()  { echo "${BLUE}${BOLD}━━━ $* ━━━${NC}"; }
ok()    { echo "${GREEN}ok${NC}   $*"; }
warn()  { echo "${YELLOW}warn${NC} $*"; }
fail()  { echo "${RED}FAIL${NC} $*" >&2; }

# ----------------------------------------------------------------------------
# Self-destruct trap — deletes the instance no matter how we exit
# ----------------------------------------------------------------------------

INSTANCE_CREATED=0

cleanup() {
    local rc=$?
    if (( INSTANCE_CREATED )); then
        echo
        step "Self-destruct"
        if gcloud compute instances delete "$INSTANCE_NAME" \
                --project="$GCP_PROJECT" \
                --zone="$GCP_ZONE" \
                --quiet 2>&1 | tail -3; then
            ok "Deleted $INSTANCE_NAME"
        else
            fail "Delete failed — clean up manually:"
            echo "      gcloud compute instances delete $INSTANCE_NAME --project=$GCP_PROJECT --zone=$GCP_ZONE"
        fi
    fi
    exit $rc
}
trap cleanup EXIT INT TERM

# ----------------------------------------------------------------------------
# Preflight
# ----------------------------------------------------------------------------

preflight() {
    step "Preflight"

    if ! command -v gcloud >/dev/null 2>&1; then
        fail "gcloud not in PATH. Install: https://cloud.google.com/sdk/docs/install"
        exit 1
    fi
    ok "gcloud $(gcloud --version 2>/dev/null | head -1)"

    if ! gcloud auth list --format='value(account)' 2>/dev/null | grep -q .; then
        fail "Not authed. Run: gcloud auth login"
        exit 1
    fi
    ok "Authed as $(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -1)"

    if [[ -z "$GCP_PROJECT" ]]; then
        fail "No GCP project set. Either:"
        echo "      gcloud config set project YOUR_PROJECT_ID"
        echo "  or  GCP_PROJECT=YOUR_PROJECT_ID bash $0"
        exit 1
    fi
    ok "Project: $GCP_PROJECT"
    ok "Zone: $GCP_ZONE"
    ok "Machine: $GCP_MACHINE_TYPE (nested-virt enabled)"

    # Sanity-check the Compute API is enabled.
    if ! gcloud services list --project="$GCP_PROJECT" --enabled \
            --filter="name:compute.googleapis.com" --format='value(name)' 2>/dev/null \
            | grep -q compute.googleapis.com; then
        fail "Compute Engine API not enabled on project $GCP_PROJECT."
        echo "      Enable: gcloud services enable compute.googleapis.com --project=$GCP_PROJECT"
        exit 1
    fi
    ok "Compute Engine API enabled"

    # rsync is the transport — confirm it's local.
    if ! command -v rsync >/dev/null 2>&1; then
        fail "rsync not in PATH (needed to ship the source tree)."
        exit 1
    fi
    ok "rsync $(rsync --version | head -1 | awk '{print $3}')"

    mkdir -p "$REPORTS_DIR"
    ok "Reports dir: $REPORTS_DIR"

    # Confirm the kernel source tree is intact.
    if [[ ! -f "$REPO_ROOT/kernel/Makefile" ]] || [[ ! -d "$REPO_ROOT/infra/verify" ]]; then
        fail "Run from the repo root or expected paths don't exist."
        exit 1
    fi
    ok "kernel/Makefile + infra/verify/ present"
}

# ----------------------------------------------------------------------------
# Create the instance with nested virt
# ----------------------------------------------------------------------------

create_instance() {
    step "Create instance"

    echo "  Name:    $INSTANCE_NAME"
    echo "  Image:   $GCP_IMAGE_FAMILY ($GCP_IMAGE_PROJECT)"
    echo

    gcloud compute instances create "$INSTANCE_NAME" \
        --project="$GCP_PROJECT" \
        --zone="$GCP_ZONE" \
        --machine-type="$GCP_MACHINE_TYPE" \
        --image-family="$GCP_IMAGE_FAMILY" \
        --image-project="$GCP_IMAGE_PROJECT" \
        --enable-nested-virtualization \
        --metadata=enable-oslogin=FALSE \
        --boot-disk-size=20GB \
        --boot-disk-type=pd-balanced \
        --tags=vos-kvm-verify \
        --quiet

    INSTANCE_CREATED=1
    ok "Created $INSTANCE_NAME"

    # Wait for the SSH daemon to actually accept connections — `instances
    # create` returns when the VM is provisioned, NOT when ssh-ready.
    echo "  Waiting for SSH to come up..."
    local tries=30
    while (( tries-- > 0 )); do
        if gcloud compute ssh "$INSTANCE_NAME" \
                --project="$GCP_PROJECT" --zone="$GCP_ZONE" \
                --command='echo ready' --quiet 2>/dev/null; then
            ok "SSH up"
            return 0
        fi
        sleep 5
    done
    fail "SSH never came up within 150 s"
    return 1
}

# ----------------------------------------------------------------------------
# Ship source + build + run
# ----------------------------------------------------------------------------

remote_run() {
    local cmd="$1"
    gcloud compute ssh "$INSTANCE_NAME" \
        --project="$GCP_PROJECT" --zone="$GCP_ZONE" \
        --command="$cmd" --quiet
}

ship_and_verify() {
    step "Ship source tree"

    # Use gcloud's built-in rsync over SSH wrapper. We ship only the
    # subdirs we actually need:
    #   kernel/         — source + Makefile
    #   infra/verify/   — the verification script
    # Anything else (backend, frontend, docs) would inflate the upload.
    local ssh_args
    ssh_args="$(gcloud compute ssh "$INSTANCE_NAME" \
        --project="$GCP_PROJECT" --zone="$GCP_ZONE" \
        --dry-run 2>&1 | tail -1)"

    # Make a remote scratch dir.
    remote_run 'mkdir -p ~/vos-verify/kernel ~/vos-verify/infra'

    gcloud compute scp --recurse \
        --project="$GCP_PROJECT" --zone="$GCP_ZONE" \
        --compress \
        "$REPO_ROOT/kernel" "$REPO_ROOT/infra" \
        "${INSTANCE_NAME}:~/vos-verify/" \
        2>&1 | tail -3
    ok "Source tree shipped (~/vos-verify on instance)"

    step "Install build deps + QEMU on instance"
    remote_run "set -e
        sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
        sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            build-essential nasm xorriso mtools wget \
            gcc-x86-64-linux-gnu binutils-x86-64-linux-gnu \
            qemu-system-x86 qemu-utils
        qemu-system-x86_64 --version | head -1
        gcc-x86-64-linux-gnu --version | head -1
    "
    ok "Toolchain ready"

    step "Build kernel (BENCH_MODE) on instance"
    remote_run "set -e
        cd ~/vos-verify/kernel
        SKIP_USER_BINS=1 make clean
        SKIP_USER_BINS=1 make CROSS=x86_64-linux-gnu- BENCH_MODE=1 EXTRA_CFLAGS='-DBENCH_MODE=1'
        ls -lh build/vos3.elf
    "
    ok "Kernel built on instance"

    step "Run verification (bare-metal-grade KVM)"
    # The script preflights /dev/kvm — confirm it's present on the GCE
    # instance with nested-virt enabled.
    remote_run "ls -l /dev/kvm && sudo chmod 666 /dev/kvm 2>/dev/null; true"

    # Run the verification. Don't `set -e` here because we want to grab
    # the summary even on partial failure (the WARN-only PROTECTED_PCID_ONLY
    # case still produces a useful artifact).
    remote_run "cd ~/vos-verify && BOOT_TIMEOUT_S=90 bash infra/verify/kvm_protected_full.sh || true" \
        2>&1 | tail -30 || true

    step "Pull evidence back"
    local remote_summary
    remote_summary="$(remote_run 'ls -t ~/vos-verify/infra/verify/reports/*.summary.txt 2>/dev/null | head -1' || true)"

    if [[ -z "$remote_summary" ]]; then
        fail "No summary file produced on instance. The verification did not generate evidence."
        return 1
    fi

    gcloud compute scp \
        --project="$GCP_PROJECT" --zone="$GCP_ZONE" \
        "${INSTANCE_NAME}:${remote_summary}" \
        "$EVIDENCE_PATH" \
        2>&1 | tail -3

    # Add a header line so the file's purpose is self-describing in a
    # later git log / blame.
    {
        echo "# vOS·Adaptive·SHA=aeb3736·Phase=P1.2-follow-up-C"
        echo "# GCP_EVIDENCE_AAA — closing artifact for engagement task #48"
        echo "# Captured: $TIMESTAMP UTC"
        echo "# Source instance: $INSTANCE_NAME ($GCP_MACHINE_TYPE in $GCP_ZONE)"
        echo "# Engagement anchor: $SHORT_SHA"
        echo "# ----- script output below -----"
        cat "$EVIDENCE_PATH"
    } > "${EVIDENCE_PATH}.tmp" && mv "${EVIDENCE_PATH}.tmp" "$EVIDENCE_PATH"

    ok "Evidence saved: $EVIDENCE_PATH"
}

# ----------------------------------------------------------------------------
# Print the closing summary
# ----------------------------------------------------------------------------

print_closing() {
    echo
    step "Closing summary"
    echo
    cat "$EVIDENCE_PATH"
    echo
    if grep -qE "^PASS  Mitigation tier = PROTECTED_(FULL|PCID_ONLY)" "$EVIDENCE_PATH" 2>/dev/null; then
        echo "${GREEN}${BOLD}✓ Task #48 evidence produced.${NC}"
        echo
        echo "  Next: git add ${EVIDENCE_PATH#$REPO_ROOT/}"
        echo "        git commit -m 'evidence(P1.2-C): GCP KVM PROTECTED verification'"
        echo "        git push"
    else
        warn "Evidence file produced but no PROTECTED tier confirmed — review $EVIDENCE_PATH"
    fi
}

# ----------------------------------------------------------------------------
# Wire it together
# ----------------------------------------------------------------------------

main() {
    preflight
    create_instance
    ship_and_verify
    print_closing
}

main "$@"
