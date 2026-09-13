# Sovereign Boot Shim — Scaffolding

**Stage:** 14
**Status:** structural scaffolding mapping the canonical `rhboot/shim` interface; awaiting an authoritative spec from the user.

---

## Honest scope ceiling

The user's planning briefs introduced the name "Sovereign Boot" as a verified-by-2026-audit secure-shim baseline. **Stage 14 web-search (2026-05-09) found no published project / spec / audit report by that name** — see `docs/STAGE_14_RESEARCH_FINDINGS.md` §2.

What does exist in 2026 Secure Boot space:

- **Microsoft's 2011 signing certificate** expires June 27, 2026. Every distro shim must be re-signed against the new key by then.
- **`rhboot/shim`** (canonical Linux first-stage shim, used by every major distribution) — well-documented, audit-trail-rich, ~10 kLOC mature C.
- **NSA's UEFI Secure Boot guidance** (December 2025) for managing the cert rollover.

In the absence of a real "Sovereign Boot" spec, this directory ships **structural scaffolding mapped to the canonical `rhboot/shim` interface** so that:

1. The vos.v1 kernel build can produce a UEFI-bootable artefact today (via the Stage 14 multi-target Makefile — see `kernel/Makefile` `kernel-baremetal` target).
2. When the user supplies an actual Sovereign Boot specification, the in-tree code is a single-layer swap to that spec — file structure, build steps, and verification chain all stay the same.
3. A diligence reviewer reading "we ship a Sovereign Boot shim" sees exactly what we ship: a structural skeleton, not a fabricated implementation.

## Design — `rhboot/shim` interface alignment

The canonical shim's responsibility chain:

```
Firmware (UEFI) → loads shim.efi (signed by Microsoft cert)
                                         │
                                         ▼
                                 verifies grubx64.efi
                                 (or any next-stage)
                                 against the shim's
                                 embedded vendor key
                                         │
                                         ▼
                                 chain-loads it
```

Stage-14 vOS shim follows the same chain:

```
Firmware (UEFI) → loads vos_shim.efi (Stage 14 — to be signed)
                                         │
                                         ▼
                                 verifies vos3.efi
                                 against the shim's
                                 embedded VOS dev key
                                 (infra/security/keys/
                                  vos3_dev_signing.pub)
                                         │
                                         ▼
                                 chain-loads vos3.efi
                                 (extends RTMR[0] with shim → kernel
                                  transition for measured boot)
```

## Files in this directory

| File | Purpose | Status |
|------|---------|--------|
| `README.md` | this document | ✅ |
| `shim_main.c` | EFI entry point + chain-load logic | 🟡 stub (compiles; calls `chain_load_kernel()` which is not yet wired to a real PE/COFF loader) |
| `verify_signature.c` | ECDSA P-256 verify of `vos3.efi`'s signature against the embedded VOS dev key | 🟡 stub (header + return-EFI_NOT_READY) |
| `Makefile.shim` | minimal Makefile producing `sovereign_shim.efi` via `objcopy --target=efi-app-x86_64` | ✅ structural |

## What works today

- **Directory exists** with the right structure (Stage 14.D Makefile target points here).
- **Public API surface** is declared so callers (the multi-target build pipeline) can reference `sovereign_shim_chain_load()` without a forward-decl conflict.
- **README documents the gap** so a reviewer doesn't assume more than is shipped.

## What needs the user-supplied spec

| Question | Stage 14 default | What spec would specify |
|----------|------------------|-------------------------|
| Signing key | `infra/security/keys/vos3_dev_signing.pub` (dev-tier, Stage 11) | A real Fulcio / Sovereign-Boot-CA-issued cert |
| Trust anchor | self-signed dev cert | The 2026 cert authority |
| Audit chain extension format | RTMR[0] += SHA-384(shim‖kernel) (vOS convention) | Spec-defined shim → kernel measurement scheme |
| Cert rollover policy | static at-build-time | Dynamic per the cert-rollover advisory cited above |
| Fallback boot path | none | Spec usually mandates a recovery/fallback ESP entry |

## Integration with Stage 14 multi-target build

The `kernel/Makefile` `kernel-baremetal` target produces `vos3.efi` (the kernel) and `sovereign_shim.efi` (the shim) as separate artefacts. The Stage-11 sigstore pipeline can sign each independently; the audit trail captures BOTH SHAs.

When the real Sovereign Boot spec lands, the swap points are:

1. `verify_signature.c` — replace the stub with the spec's verification routine.
2. `Makefile.shim` — adjust `LDFLAGS` to match the spec's ABI requirements (some shims require `-Wl,--dll`, etc.).
3. `infra/security/keys/` — replace dev key with the production key the spec mandates.

No call site in the kernel itself needs to change.
