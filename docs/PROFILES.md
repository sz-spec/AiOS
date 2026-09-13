# vOS Profiles

`VOS_PROFILE` selects which security and compliance gates are enforced at
runtime. There are three values: `community`, `enterprise`, `fortress`.

## Capability matrix

| Capability                                | community | enterprise | fortress |
|-------------------------------------------|:---------:|:----------:|:--------:|
| Engine + V-Core + smart router            | ✅        | ✅          | ✅        |
| Convex audit + EDR/SPM/firewall connectors|           | ✅          | ✅        |
| ECDSA P-256 attestation                   | ✅        | ✅          | ✅        |
| Hybrid P-256 + P-521 attestation          |           | ✅          | ✅        |
| ML-DSA-65 post-quantum signature          |           |            | ✅        |
| Sigstore v3 bundle verify (mandatory)     |           |            | ✅        |
| Intel TDX RTMR ladder (mandatory)         |           |            | ✅        |
| SQLCipher encrypted compliance store      |           |            | ✅        |
| Hyper-V kernel image (vos3-hyperv.elf)    |           |            | ✅        |

`enterprise` and `community` MAY opt into Sigstore-verify and TDX
attestation but the runtime does not refuse to start without them. Under
`fortress`, every fortress-row gate is **fail-closed**: missing input
halts startup with a clear error, never a silent fallback.

## Setting the profile

Backend / Python:

```bash
export VOS_PROFILE=fortress
.venv/bin/uvicorn main:app
```

Kernel build (compile-time):

```bash
make -C kernel kernel-community    # default
make -C kernel kernel-enterprise
make -C kernel kernel-fortress     # also passes -DVOS3_TARGET_HYPERV
```

The kernel side enforces the profile at compile time so the running
image cannot diverge from the manifested profile under a misconfigured
env var.

## Source-of-truth files

- `backend/vos_profile.py` — runtime profile dispatcher (`get_profile()`,
  `is_fortress()`, `Profile.requires_*` predicates). Named `vos_profile`
  to avoid collision with the Python stdlib `profile` module.
- `kernel/include/vos/profile.h` — compile-time profile selector and
  capability macros (`VOS3_REQUIRES_*`).
- `kernel/Makefile` — `kernel-community` / `kernel-enterprise` /
  `kernel-fortress` composite targets.
- `backend/tests/test_profile_dispatch.py` — coverage for each profile.

## Verification

```bash
VOS_PROFILE=community  pytest backend/tests/test_profile_dispatch.py -v
VOS_PROFILE=enterprise pytest backend/tests/test_profile_dispatch.py -v
VOS_PROFILE=fortress   pytest backend/tests/test_profile_dispatch.py -v

make -C kernel clean && make -C kernel kernel-fortress
sha256sum kernel/build/vos3-hyperv.elf
```

A green test run on all three profiles + a reproducible Hyper-V kernel
SHA = profile gating is live.
