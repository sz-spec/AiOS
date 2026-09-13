# VOS3 SPDX Software Bill of Materials Summary
## Due-Diligence Ready — v20.3.0 | April 2026

---

## License Summary

**Primary License**: MIT (SPDX: MIT)
**Copyright**: 2026 VOS3 Project
**REUSE Compliant**: Yes (`.reuse/dep5`)

---

## Component License Matrix

| Component | Path | License | Notes |
|-----------|------|---------|-------|
| Kernel (C, x86_64) | `kernel/` | MIT | Freestanding, no GPL dependencies |
| Backend (Python/FastAPI) | `backend/` | MIT | See third-party table below |
| Frontend (Next.js/React) | `frontend/` | MIT | See third-party table below |
| Desktop (Tauri 2.0/Rust) | `desktop/` | MIT | Tauri: MIT/Apache-2.0 |
| Documentation | `docs/` | MIT | Market entry materials |

---

## Third-Party Dependencies (Backend)

| Package | Version | License | SBOM |
|---------|---------|---------|------|
| FastAPI | ≥0.115 | MIT | `docs/sbom-backend.json` |
| Pydantic | ≥2.0 | MIT | `docs/sbom-backend.json` |
| LangChain | ≥0.3 | MIT | `docs/sbom-backend.json` |
| anthropic | ≥0.40 | MIT | `docs/sbom-backend.json` |
| openai | ≥1.0 | MIT | `docs/sbom-backend.json` |
| cryptography | ≥42.0 | Apache-2.0 / BSD | `docs/sbom-backend.json` |
| httpx | ≥0.27 | BSD-3-Clause | `docs/sbom-backend.json` |
| uvicorn | ≥0.30 | BSD-3-Clause | `docs/sbom-backend.json` |
| stripe | ≥9.0 | Apache-2.0 | `docs/sbom-backend.json` |

No GPL, LGPL, or AGPL dependencies in the production backend.

---

## Third-Party Dependencies (Frontend)

| Package | License | SBOM |
|---------|---------|------|
| Next.js 15 | MIT | `docs/sbom-frontend.json` |
| React 19 | MIT | `docs/sbom-frontend.json` |
| Tailwind CSS | MIT | `docs/sbom-frontend.json` |
| Clerk (SDK) | MIT | `docs/sbom-frontend.json` |
| Convex (SDK) | Apache-2.0 | `docs/sbom-frontend.json` |
| shadcn/ui | MIT | `docs/sbom-frontend.json` |

---

## Kernel Third-Party Inclusions

| Component | License | Notes |
|-----------|---------|-------|
| musl libc v1.2.5 | MIT | Dynamic link via `ld-musl-x86_64.so.1`; not statically linked into kernel |
| Limine bootloader | BSD-2-Clause | Boot protocol only; not distributed with kernel binary |
| OVMF (QEMU EFI) | BSD-2-Clause | Test harness only; not distributed |

The kernel itself (`kernel/build/vos3.elf`) contains **only MIT-licensed code** written by the VOS3 Project. No GPL code is compiled into the kernel binary.

---

## Patent Notice

VOS3 does not incorporate any patented algorithms. The Merkle Mountain Range construction is described in public academic literature (Peter Todd, 2012; RFC-style specification). SHA-256 is a NIST standard (FIPS 180-4). PCID is documented in Intel SDM Vol. 3A, Section 4.10.1.

---

## Due-Diligence Checklist

- [x] Primary license: MIT — permissive, compatible with commercial distribution
- [x] No GPL/AGPL/LGPL in production kernel binary
- [x] No GPL/AGPL in production backend (cryptography: Apache-2.0, dual-licensed)
- [x] SPDX identifiers in all new source files (per REUSE 3.0 spec)
- [x] REUSE dep5 covers all files
- [x] SBOM files exist: `docs/sbom-backend.json`, `docs/sbom-frontend.json`, `docs/sbom-kernel.json`
- [x] No proprietary third-party SDKs embedded in distributed binary
- [x] musl libc: MIT, dynamically linked (not statically embedded in kernel ELF)

---

*VOS3 SPDX Summary — v20.3.0 — 2026-04-24*
*SPDX-License-Identifier: MIT*
*SPDX-FileCopyrightText: 2026 VOS3 Project*
