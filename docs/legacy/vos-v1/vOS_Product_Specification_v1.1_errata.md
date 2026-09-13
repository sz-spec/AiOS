# vOS Product Specification — v1.0 → v1.1 Errata Sheet

**Source:** `vOS_Product_Specification_Long.pdf` Document Version 1.0,
2026-05-07
**Errata version:** v1.1
**Errata date:** 2026-05-19
**Author:** Engineering audit team (Claude Opus 4.7 1M context, 2026-05-19)

---

## Purpose

Side-by-side corrections for the 6 deviations identified in
`COMPLIANCE_REPORT.md` between v1.0 PDF text and the actual code state at
commit `5138bb6` (branch `unified-master-v1`, 2026-05-19).

Apply via copy-paste into the PDF source (InDesign / Word / LaTeX) without
re-flowing pages — every change is a number swap or short phrase replacement.

---

## Errata #1 — Kernel file & subsystem count (Page 3, §2.1)

### v1.0 (PDF, page 3)
> Engineering Discipline — "Zero Panics" as a Design Goal
> - **461 C and header source files** across **22 kernel subsystem directories**

### v1.1 (corrected)
> Engineering Discipline — "Zero Panics" as a Design Goal
> - **325 C and header source files** across **30 kernel subsystem directories** (as of 2026-05-19; growth tracked via `find kernel/src kernel/include -name '*.c' -o -name '*.h' \| wc -l`)

### Why
Live count: `find` returned **325** (`.c` + `.h`), **30** subsystem dirs. The
v1.0 number may have included generated artifacts or counted differently.
Direction-mixed: fewer files but more subsystems — the codebase has been
re-organized.

---

## Errata #2 — ASSERT count (Page 3, §2.1)

### v1.0 (PDF, page 3)
> - **1,340 certified kernel ASSERT statements** with bit-exact build provenance

### v1.1 (corrected — actually an upgrade!)
> - **1,518 certified kernel ASSERT statements** with bit-exact build provenance (verified via `bash infra/audit/count_asserts.sh`; baseline 1,300, grew across phases 7-omega)

### Why
PDF authored 2026-05-07 listed 1,340. Count has grown to **1,518** by 2026-05-19
across phases 7 (Genesis Gate), 8.1 (Velocity Alpha), 8.2 (Endurance), Extreme,
Divine, Omega. The auto-count script is `infra/audit/count_asserts.sh`.

---

## Errata #3 — Wasmtime version (Page 4, §2.4)

### v1.0 (PDF, page 4)
> A single feature-flag dispatcher selects the appropriate runtime backend per
> deployment, with a documented WASM agent-isolation roadmap (**Wasmtime 44.0.0**)
> for the next-generation sandboxing layer.

### v1.1 (corrected)
> A single feature-flag dispatcher selects the appropriate runtime backend per
> deployment, with a documented WASM agent-isolation roadmap (**Wasmtime 44.0.2**)
> for the next-generation sandboxing layer.

### Why
`requirements.txt:179` pins `wasmtime==44.0.2`. The upgrade from 44.0.0 →
44.0.2 was made in May 2026 for **Spectre-V4 mitigation**. Rationale documented
in `docs/SPEC_ERRATA.md:20-34` and `migration_plan.md:218-220`. This is a
**security upgrade** that the PDF should celebrate, not hide.

---

## Errata #4 — Linux syscall arithmetic (Page 1, §1.2)

### v1.0 (PDF, page 1)
> **Linux (Cloud / Datacenter Mode)** — high-performance guest under KVM with
> VirtIO paravirtualized I/O; standard ELF binary, container-friendly; Linux ABI
> compatibility (**74 standard syscalls + 11 vOS-custom**, dynamically linked
> against musl libc 1.2.5).

### v1.1 (corrected)
> **Linux (Cloud / Datacenter Mode)** — high-performance guest under KVM with
> VirtIO paravirtualized I/O; standard ELF binary, container-friendly; Linux ABI
> compatibility (**Linux-ABI aliases in 0-350 range plus vOS-custom syscalls in
> the 400-511 reserved range** — concrete current count is ~7 standard aliases
> + ~47 custom; full enumeration in `kernel/include/vos/syscall.h`), dynamically
> linked against musl libc 1.2.5.

### Why
The "74 + 11 = 85" arithmetic in v1.0 does not match the implementation in
`kernel/include/vos/syscall.h`. The design intent (Linux-ABI compat + vOS-custom
range) is preserved; the specific arithmetic should be replaced with the actual
allocation strategy plus current counts. Future-proof: as more syscalls land in
the 400-511 range, the manifesto language remains valid.

---

## Errata #5 — Windows installer (Page 1, §1.2 + Page 4, §2.4)

### v1.0 (PDF, page 1)
> **Microsoft Windows (Corporate Workstation Mode)** — native integration via
> Hyper-V synthetic drivers; **MSI installer**, Tauri 2.0 desktop shell.

### v1.1 (corrected)
> **Microsoft Windows (Corporate Workstation Mode)** — native integration via
> Hyper-V synthetic drivers (**`dist/vos3_windows_hyperv.vhdx`** ready for
> import); Tauri 2.0 desktop shell. **MSI installer roadmapped for v1.0.1**
> following productionization on a Windows-hosted build pipeline (cross-compile
> from macOS not supported by the WiX Toolset). Operators who require MSI today
> can build from source per `scripts/build_msi_on_windows.ps1`.

### Why
The v1.0.0-dp release ships **VHDX only** (`dist/release_v1_0.zip` contains
`vos3_windows_hyperv.vhdx` but no `.msi`). MSI requires WiX Toolset, which is
Windows-only — cross-compile from macOS / Linux is not supported by the Tauri
build pipeline. Honest scope:

- **v1.0.0 (Developer Preview)**: VHDX + Hyper-V PowerShell import = first-class Windows path
- **v1.0.1 (planned)**: native MSI installer signed with Authenticode

### v1.1 update — Sprint 14.2 (Gap 4) — 2026-05-20: gap CLOSED at the pipeline level

The MSI gap is resolved in the build pipeline. Remaining operator work is
secret provisioning (a one-time DigiCert PFX import).

**What landed in this sprint:**

- `.github/workflows/release.yml::build-windows` runs on a `windows-2022`
  runner. `cargo tauri build --bundles msi` produces an MSI natively — WiX
  Toolset is preinstalled on the GitHub Actions Windows image, so no
  cross-compile is needed.
- A new step **Authenticode-signs** the MSI via `signtool.exe` against the
  `WINDOWS_CERTIFICATE` + `WINDOWS_CERTIFICATE_PASSWORD` repository secrets
  (DigiCert OV/EV PFX, base64-encoded). Timestamp via DigiCert TSA
  (`http://timestamp.digicert.com`). Signature is re-verified after the
  copy-rename step.
- `scripts/sign_msi_osslsigncode.sh` is the **sidecar** path that lets a
  Linux/macOS host sign a CI-produced MSI without a Windows runner — uses
  `osslsigncode` (`brew install osslsigncode` / `apt-get install
  osslsigncode`). Useful for emergency re-signs and HSM-backed signing.
- `scripts/build_installers.sh` has been cleaned up to remove the misleading
  "MSI workaround" framing for non-Windows hosts. The script now fails loudly
  with a pointer to the CI workflow.

**Operator action remaining for v1.0.1 / v1.1 GA:**

1. Provision a 1-year DigiCert OV (or EV) code-signing certificate (~$400/yr).
   EV certs are recommended — SmartScreen reputation propagates faster.
2. Upload the PFX as `WINDOWS_CERTIFICATE` (base64-encoded) and the password
   as `WINDOWS_CERTIFICATE_PASSWORD` in the repo Settings → Secrets and
   variables → Actions.
3. Tag a release `v1.0.1` — `release.yml` will produce + sign +
   notarize-equivalent MSI as `dist/vOS_v1_Setup.msi`.
4. Verify locally with `signtool verify /pa /v dist/vOS_v1_Setup.msi`
   OR on macOS/Linux with `osslsigncode verify -in dist/vOS_v1_Setup.msi`.

**Note on the 1-year cert lifespan limit** (industry change effective
2026-02-15): the previous 3-year code-signing cert option is no longer
available. Every renewal cycle requires a re-tag + re-sign run.

---

## Errata #6 — Sigstore "production code-signing pipeline" (Page 5, §3.3)

### v1.0 (PDF, page 5)
> **Sigstore + Rekor v2 production code-signing pipeline**, with an in-tree
> mock for CI/CD verification

### v1.1 (corrected — honest scope)
> **Sigstore-shaped + Rekor-v2-shaped code-signing pipeline** with cryptograph-
> ically real ECDSA P-256 signatures + RFC-6962 Merkle inclusion proofs.
> Currently uses a self-signed dev-tier signer (`CN=VOS3-DEV-NOT-FULCIO`) and a
> local transparency log (`infra/security/rekor_v2.jsonl`); **migration to
> public Fulcio + public Rekor v2** (which reached GA upstream in 2026 per
> `blog.sigstore.dev/rekor-v2-ga`) is planned for v1.0.1. Honest scope and
> migration runbook are in `docs/SIGSTORE_V3_GAP.md`.

### Why
The v1.0 phrasing "production code-signing pipeline" is stronger than the
implementation. The cryptography is real and standards-compliant; the
infrastructure is self-hosted (dev tier) until v1.0.1 cuts over to public
Sigstore. This is the only PDF gap that materially affects regulator/auditor
review — soften now, deliver real Fulcio in v1.0.1.

---

## Additional non-erratum updates (positive deltas to surface in v1.1)

These are **achievements the v1.0 PDF didn't claim** but the system already
delivers. Consider adding them to v1.1:

### Bootable installer ISO — Q1 2027 roadmap EXCEEDED

v1.0 PDF page 5 §3.2:
> "roadmapped to a bootable installer ISO by Q1 2027 for direct hardware
> deployment"

**v1.1 add:**
> "Bootable installer ISO **shipped 2026-05-17** (`dist/vos3_installer.iso`,
> 27 MB hybrid BIOS+UEFI bootable image), **~8 months ahead of original
> Q1 2027 roadmap**. Produced via reproducible `infra/build_iso.sh` pipeline."

### Kernel SHA-256 — sealed and stripped post-launch
v1.1 add a line in §3.2:
> "Production binary `kernel/build/vos3.elf` SHA-256 (stripped, signed):
> `63a9b5405e9f1dcec0950fce16061c71108272495fb8e7c8650197c5bce0f0ba`. Verify
> via `bash scripts/verify_release.sh`."

### Release artifact bundle SHAs
v1.1 add to §3.2:
> "Release archive `release_v1_0.zip` SHA-256:
> `1a575af891f336cbcb42252d30186bf882149265e18106851ecfd02b8394ef89`. Signed
> Sigstore bundle at `infra/security/release_artifacts/release_v1_0_zip.bundle.json`.
> Full inventory in `dist/RELEASE_HASHES.txt`."

---

## Change log

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-07 | Strategic Engineering | Initial publication |
| 1.1 | 2026-05-19 | Engineering audit | 6 errata applied based on post-launch system audit; 3 positive deltas added |

---

## Apply checklist

- [ ] Errata #1 — replace "461" → "325", "22" → "30" on page 3
- [ ] Errata #2 — replace "1,340" → "1,518" on page 3
- [ ] Errata #3 — replace "Wasmtime 44.0.0" → "Wasmtime 44.0.2" on page 4
- [ ] Errata #4 — replace "74 standard syscalls + 11 vOS-custom" with the longer
      future-proof phrasing on page 1
- [ ] Errata #5 — replace "MSI installer, Tauri 2.0 desktop shell" with the VHDX-
      ready + MSI-roadmapped phrasing on page 1 (and analog on page 4)
- [ ] Errata #6 — soften "Sigstore + Rekor v2 production code-signing pipeline"
      on page 5
- [ ] Add positive delta paragraphs on page 5 §3.2 (ISO shipped early, kernel
      SHA, zip SHA)
- [ ] Update footer "Document Version 1.0" → "1.1"
- [ ] Update header date "2026-05-07" → "2026-05-19"
- [ ] Re-export PDF, re-sign with publisher GPG, distribute to investor list

---

*End of errata.*
