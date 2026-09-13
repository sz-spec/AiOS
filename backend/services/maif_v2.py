"""
backend/services/maif_v2.py
===========================

Sprint 22 / Item I2 — MAIF v2 model-provenance chain + SLOT_START gate
(fail-closed) (NEW row).

What this is
------------

From the 80-problem agent-era catalog, I2:
  "No end-to-end model provenance — when vOS loads a model into an
   inference slot, it has no verifiable account of WHERE that model came
   from: which base checkpoint, which fine-tune steps, on which training
   data. A swapped or silently-poisoned checkpoint loads exactly like a
   clean one."

MAIF v2 (Model AI BOM, v2) is a tamper-evident **hash chain** over a
model's lineage: each link records a build step (pretrain / finetune /
merge / quantize / distill), the SHA-256 of the artifact it produced,
and the SHA-256 of the training data that step consumed — bound to its
parent link by the parent's link hash. The head link's artifact hash is
the model that will actually be loaded.

The **SLOT_START gate** refuses to start an inference slot when the
provenance chain is missing, broken, doesn't terminate at the model
being loaded, isn't signed by a trusted builder (if the policy requires
it), or consumed a blocklisted (known-poisoned) / non-allowlisted
dataset. Refuse the unsafe load = the fail-closed contract.

This is the provenance layer the I4 (training-data poisoning) research
row depends on for its `vos_model_provenance_audit_coverage_ratio`
metric: I2 makes "is this model's full lineage hash-chained and signed?"
a checkable property.

Enforcement contract
--------------------

    gate = ProvenanceGate(policy=SlotStartPolicy(require_provenance=True))
    gate.require_slot_start(
        model_id="llama-4-ft-corp",
        model_sha256=<sha of the .safetensors being loaded>,
        provenance=prov)        # raises SlotStartRefused on a bad chain

Composition: this runs at SLOT_START, before model_manager.load_to_kernel()
hands bytes to the warp DMA (alongside E1/E3/E6/O4 on the GPU side).

Honest scope ceilings
--------------------

  - The chain proves INTEGRITY + LINEAGE (this artifact came from these
    steps on these datasets), not training-data CLEANLINESS — detecting a
    sleeper-agent in the data is the I4 research row (uncloseable). I2's
    contribution is making the lineage auditable + gating on a
    blocklist/allowlist of dataset hashes the operator curates.
  - The builder signature (optional) uses Ed25519 over the chain head.
    Binding to a transparency log (Sigstore/Rekor, as in
    infra/security/) is a follow-up; the hash chain + signature here is
    the in-tree primitive.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def _sha256_hex(*chunks: bytes) -> str:
    h = hashlib.sha256()
    for c in chunks:
        h.update(len(c).to_bytes(8, "big"))
        h.update(c)
    return h.hexdigest()


def _verify_ed25519(public_bytes: bytes, signature: bytes, message: bytes) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey,
    )
    from cryptography.exceptions import InvalidSignature

    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, message)
        return True
    except InvalidSignature:
        return False
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Provenance model
# ---------------------------------------------------------------------------


class ProvenanceStep(str, enum.Enum):
    PRETRAIN = "pretrain"
    FINETUNE = "finetune"
    MERGE = "merge"
    QUANTIZE = "quantize"
    DISTILL = "distill"


@dataclass(frozen=True)
class ProvenanceLink:
    step: ProvenanceStep
    artifact_sha256: str  # SHA-256 of the artifact this step produced
    training_data_sha256: (
        str  # SHA-256 of the data this step consumed ("" if N/A, e.g. quantize)
    )
    parent_link_hash: str  # link_hash of the previous link, "" for root
    description: str = ""

    def link_hash(self) -> str:
        return _sha256_hex(
            b"vos3-maif-v2-link",
            self.step.value.encode("utf-8"),
            bytes.fromhex(self.artifact_sha256) if self.artifact_sha256 else b"",
            (
                bytes.fromhex(self.training_data_sha256)
                if self.training_data_sha256
                else b""
            ),
            self.parent_link_hash.encode("utf-8"),
            self.description.encode("utf-8"),
        )


@dataclass(frozen=True)
class ModelProvenance:
    model_id: str
    links: tuple  # tuple[ProvenanceLink, ...]
    builder_pub: bytes = b""  # Ed25519 pubkey of the builder (optional)
    head_signature: bytes = b""  # Ed25519 sig over the head link_hash (optional)

    def head_hash(self) -> str:
        if not self.links:
            return ""
        return self.links[-1].link_hash()

    def head_artifact_sha256(self) -> str:
        if not self.links:
            return ""
        return self.links[-1].artifact_sha256

    def data_hashes(self) -> frozenset:
        return frozenset(
            link.training_data_sha256
            for link in self.links
            if link.training_data_sha256
        )


def build_provenance(
    model_id: str,
    steps: Iterable[tuple],
    *,
    builder_private: Optional[bytes] = None,
    builder_public: bytes = b"",
) -> ModelProvenance:
    """Build a provenance chain from an iterable of
    (ProvenanceStep, artifact_sha256, training_data_sha256, description)
    tuples, hash-chaining each link to its parent. If a builder key is
    supplied, signs the head link hash."""
    links: list[ProvenanceLink] = []
    parent = ""
    for step, artifact, data, *rest in steps:
        desc = rest[0] if rest else ""
        link = ProvenanceLink(
            step=step,
            artifact_sha256=artifact,
            training_data_sha256=data,
            parent_link_hash=parent,
            description=desc,
        )
        links.append(link)
        parent = link.link_hash()

    signature = b""
    if builder_private is not None and links:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        sk = Ed25519PrivateKey.from_private_bytes(builder_private)
        signature = sk.sign(links[-1].link_hash().encode("utf-8"))

    return ModelProvenance(
        model_id=model_id,
        links=tuple(links),
        builder_pub=builder_public,
        head_signature=signature,
    )


# ---------------------------------------------------------------------------
# Policy + gate
# ---------------------------------------------------------------------------


class SlotStartRefused(Exception):
    """Raised by require_slot_start() when the model's provenance does not
    satisfy the slot-start policy. Fail-closed."""


@dataclass(frozen=True)
class SlotStartPolicy:
    require_provenance: bool = True
    require_signed: bool = False
    trusted_builder_keys: frozenset = frozenset()  # frozenset[bytes]
    blocked_data_hashes: frozenset = frozenset()  # known-poisoned datasets
    allowed_data_hashes: Optional[frozenset] = None  # allowlist (None = any)


@dataclass
class ProvenanceStats:
    checks: int = 0
    allowed: int = 0
    refused: int = 0


@dataclass
class ProvenanceGate:
    """Verifies MAIF v2 chains and gates SLOT_START."""

    policy: SlotStartPolicy = field(default_factory=SlotStartPolicy)
    stats: ProvenanceStats = field(default_factory=ProvenanceStats)

    # ------------------------------------------------------------------
    # Chain verification
    # ------------------------------------------------------------------

    @staticmethod
    def verify_chain(provenance: ModelProvenance) -> tuple:
        """Return (ok: bool, reason: str). Checks the hash chain links to a
        well-formed root and each parent binding holds. Does NOT enforce
        policy (that's require_slot_start)."""
        if provenance is None or not provenance.links:
            return (False, "empty provenance chain")
        parent = ""
        for i, link in enumerate(provenance.links):
            if link.parent_link_hash != parent:
                return (
                    False,
                    f"link #{i} ({link.step.value}) parent hash does not "
                    f"bind to link #{i-1}",
                )
            if not link.artifact_sha256 and link.step is not ProvenanceStep.MERGE:
                return (False, f"link #{i} missing artifact_sha256")
            parent = link.link_hash()
        return (True, "chain intact")

    # ------------------------------------------------------------------
    # SLOT_START gate (fail-closed)
    # ------------------------------------------------------------------

    def require_slot_start(
        self,
        *,
        model_id: str,
        model_sha256: str,
        provenance: Optional[ModelProvenance],
    ) -> ModelProvenance:
        """Allow SLOT_START iff the provenance satisfies the policy.
        Raises SlotStartRefused otherwise (fail-closed)."""
        self.stats.checks += 1
        p = self.policy

        # B7-1 fix: under a provenance-requiring policy, an empty / whitespace /
        # null model_sha256 must NOT be accepted — it would silently skip the
        # "chain terminates at the loaded artifact" anti-swap binding below
        # (the binding is gated on `model_sha256` being truthy). A load with no
        # artifact hash to bind to is a caller contract violation; fail loud.
        if p.require_provenance and (
            model_sha256 is None or not str(model_sha256).strip()
        ):
            raise ValueError(
                "model_sha256 is required when policy.require_provenance is set: "
                "an empty hash would bypass the anti-swap binding (B7-1)"
            )

        def _refuse(reason: str):
            self.stats.refused += 1
            logger.error(
                "[maif_v2] SLOT_START REFUSED (fail-closed) model=%s — %s",
                model_id,
                reason,
            )
            raise SlotStartRefused(f"model {model_id!r}: {reason}")

        if provenance is None:
            if p.require_provenance:
                _refuse("no provenance chain provided (policy requires it)")
            self.stats.allowed += 1
            return ModelProvenance(model_id=model_id, links=())

        ok, reason = self.verify_chain(provenance)
        if not ok:
            _refuse(f"provenance chain invalid: {reason}")

        # The chain must terminate at the artifact actually being loaded.
        head_artifact = provenance.head_artifact_sha256()
        if (
            head_artifact
            and model_sha256
            and not hmac.compare_digest(head_artifact, model_sha256)
        ):
            _refuse(
                f"provenance head artifact {head_artifact[:16]}… != loaded "
                f"model {model_sha256[:16]}… (chain is for a different model)"
            )

        # Signature policy.
        if p.require_signed or p.trusted_builder_keys:
            if not provenance.head_signature or not provenance.builder_pub:
                _refuse("policy requires a signed provenance head but none present")
            if (
                p.trusted_builder_keys
                and provenance.builder_pub not in p.trusted_builder_keys
            ):
                _refuse("provenance signed by an untrusted builder key")
            if not _verify_ed25519(
                provenance.builder_pub,
                provenance.head_signature,
                provenance.head_hash().encode("utf-8"),
            ):
                _refuse("provenance head signature does not verify")

        # Dataset blocklist / allowlist.
        data_hashes = provenance.data_hashes()
        blocked = data_hashes & p.blocked_data_hashes
        if blocked:
            _refuse(
                f"provenance consumed blocklisted (known-poisoned) dataset(s): "
                f"{sorted(h[:16] + '…' for h in blocked)}"
            )
        if p.allowed_data_hashes is not None:
            disallowed = data_hashes - p.allowed_data_hashes
            if disallowed:
                _refuse(
                    f"provenance consumed dataset(s) outside the allowlist: "
                    f"{sorted(h[:16] + '…' for h in disallowed)}"
                )

        self.stats.allowed += 1
        logger.info(
            "[maif_v2] SLOT_START allowed model=%s (%d-link chain, signed=%s)",
            model_id,
            len(provenance.links),
            bool(provenance.head_signature),
        )
        return provenance


__all__ = [
    "ProvenanceStep",
    "ProvenanceLink",
    "ModelProvenance",
    "build_provenance",
    "SlotStartPolicy",
    "SlotStartRefused",
    "ProvenanceGate",
    "ProvenanceStats",
]
