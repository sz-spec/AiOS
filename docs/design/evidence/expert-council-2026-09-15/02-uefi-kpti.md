# UEFI and address-space transitions

Reviewer: `/root/rust_upgrade` (one actual agent, architecture/build/hardware council track). Source baseline: `32187957757ab57d7820d0a63fa62c409003b126`. These are seven specialized reviews by one reviewer, not seven independent agents.

**P1 — Full KPTI is not established.** [kernel/src/arch/x86_64/kpti.c](../../../../kernel/src/arch/x86_64/kpti.c) explicitly retains broad kernel-image/direct-map entries. `vos3_kpti_prepare_user_return` validates distinct process roots, synchronizes them and binds CPU-local transition state, but its own comment requires dedicated entry/descriptor/IST mappings before claiming full Meltdown isolation. Preserve the current explicit limitation; qualify the reduced mapping set and nested interrupt/NMI transitions separately.

**P1 — UEFI boot is not Secure Boot qualification.** [infra/build_iso.sh](../../../../infra/build_iso.sh) packages IA32 and x64 EFI executables and validates the boot catalog. The reviewed evidence runs BIOS and UEFI QEMU configurations; it does not document an authenticated firmware trust chain with enrollment, unsigned-image rejection and revocation. Do not infer those properties from EFI artifact presence. A 32-bit firmware loader also does not turn this kernel into a 32-bit CPU implementation ([README.md](../../../../README.md)).

**P2 — Keep direct access, translation invalidation and lifetime evidence distinct.** The isolation, memory-transition and TLB reports qualify different boundaries. Lifetime changes are under fresh qualification and must retain their own exact artifact identities. Reviewed KPTI root preparation and reports, not every assembly entry path, firmware implementation or speculative side channel.
