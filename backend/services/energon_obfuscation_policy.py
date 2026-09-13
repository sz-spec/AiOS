"""
backend/services/energon_obfuscation_policy.py
================================================

Sprint 17 / Prototype 1 — Energon software-side mitigation policy.

What this is
------------

From the 80-problem agent-era catalog, E5:
  "Energon: power/thermal side channels recover transformer weights —
   GPU power-draw + thermal signals reveal hyperparameters; 89% accuracy
   on family ID, 100% on hyperparameter class. EM emissions readable
   from 100 cm through glass."

Per the Energon paper (arxiv 2508.01768) §6 "Mitigation strategies",
the defense is two-layer:

  (a) Hardware-side — restrict GPU sensor access to admin users
      (NVIDIA driver patch — vendor-coordination work, deferred).
  (b) Software-side — emit OBFUSCATED transformer variants via:
        * structured pruning of attention heads (per-layer)
        * temperature-jittered knowledge distillation
        * randomized layer-order at inference time when admissible

This module is the (b) policy decision layer. It consumes a model's
architecture spec and emits a (pruning_recipe, distillation_recipe,
inference_jitter) triple that downstream model-loading code applies
before the weights ever touch the GPU.

Public surface
--------------

  EnergonPolicy.evaluate(arch_spec) -> ObfuscationDecision
  ObfuscationDecision.{should_obfuscate, pruning_recipe,
                       distillation_recipe, inference_jitter,
                       reason, residual_risk_class}
  EnergonPolicyStats / snapshot_stats()

Honest scope ceiling
--------------------

  - This module makes the POLICY DECISION. Actually applying the
    obfuscation to weights at load time requires a model-runtime
    hook (not in scope for the policy prototype). The Python twin
    lets backend services program against the API surface today;
    the runtime integration happens when the GPU-host environment
    is provisioned (Sprint 17 Cluster A procurement gate).
  - The pruning + distillation recipes are deterministic given the
    seed; reproducibility is a requirement so two operators see
    the same obfuscation given identical inputs.
  - Residual risk: even with software-side obfuscation, the paper
    reports family-ID accuracy can drop only to ~40-60% (vs 89%
    baseline). Software-side is NECESSARY but NOT SUFFICIENT;
    pair with the (a) hardware-side driver patch for full defense.
  - We refuse to obfuscate architectures not on the support list
    (rather than silently passing through). Operators get an
    explicit `UNSUPPORTED` outcome to triage.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import os
import threading

# ---------------------------------------------------------------------------
# Supported architecture families (May-2026 horizon Energon paper §3 table)
# ---------------------------------------------------------------------------


class TransformerFamily(str, enum.Enum):
    """Architectures the Energon paper specifically demonstrated leakage on,
    plus the obvious mainline families. Operator override env:
    VOS3_ENERGON_FORCE_ARCH lets an admin bypass the support list."""

    LLAMA = "llama"
    QWEN = "qwen"
    GEMMA = "gemma"
    PHI = "phi"
    MISTRAL = "mistral"
    DEEPSEEK = "deepseek"
    VIT = "vit"  # vision transformer
    CLIP = "clip"  # vision-language


_SUPPORTED_FAMILIES = frozenset(TransformerFamily)


# ---------------------------------------------------------------------------
# Sensitivity tiers — operator declares per-model how aggressively to obfuscate
# ---------------------------------------------------------------------------


class SensitivityTier(str, enum.Enum):
    PUBLIC = "public"  # off-the-shelf weights; no obfuscation needed
    PROPRIETARY = "proprietary"  # fine-tunes worth protecting; medium obfuscation
    CLASSIFIED = "classified"  # state-level secret; max obfuscation


# ---------------------------------------------------------------------------
# Residual-risk class — what's still leakable AFTER our obfuscation
# ---------------------------------------------------------------------------


class ResidualRiskClass(str, enum.Enum):
    LOW = "low"  # ~10-20% family-ID accuracy (per paper estimates)
    MEDIUM = "medium"  # ~40-60% family-ID accuracy — pair with hw mitigation
    HIGH = "high"  # ~70-89% — software alone insufficient


# ---------------------------------------------------------------------------
# Input spec — what the operator/loader passes us
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ArchSpec:
    """Minimal architecture spec needed to drive the obfuscation recipes."""

    family: TransformerFamily
    layer_count: int
    head_count: int  # attention heads per layer
    head_dim: int  # dim per head
    hidden_dim: int
    vocab_size: int
    sensitivity: SensitivityTier
    deterministic_seed: int = 0  # 0 = derive from arch hash


# ---------------------------------------------------------------------------
# Output recipes
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PruningRecipe:
    """Structured pruning instructions. Layer indices + head indices to drop.

    Empty tuples = no pruning (PUBLIC tier).
    """

    layers_to_prune_partially: tuple[int, ...]
    heads_per_layer_to_drop: dict[int, tuple[int, ...]]
    keep_ratio: float  # fraction of original capacity retained (0.5 - 1.0)


@dataclasses.dataclass(frozen=True)
class DistillationRecipe:
    """Knowledge-distillation parameters for the obfuscated student."""

    temperature: float  # softmax temperature (jitter source)
    temperature_jitter_pct: float  # +/- pct randomness per batch
    student_layer_ratio: float  # student/teacher layer count ratio
    epochs: int  # short distillation


@dataclasses.dataclass(frozen=True)
class InferenceJitter:
    """Runtime jitter applied to break per-layer timing fingerprint."""

    enabled: bool
    layer_order_perm_rate: float  # 0-1; fraction of admissible layer reorders
    dummy_op_insert_rate: float  # 0-1; rate of decoy GEMM insertions


# ---------------------------------------------------------------------------
# Final decision
# ---------------------------------------------------------------------------


class ObfuscationKind(str, enum.Enum):
    PASS_THROUGH = "pass_through"  # PUBLIC sensitivity; no obfuscation
    APPLIED = "applied"  # recipes will be run
    UNSUPPORTED = "unsupported"  # arch family not on support list
    DEFERRED = "deferred"  # caller asked to compute but not apply


@dataclasses.dataclass(frozen=True)
class ObfuscationDecision:
    kind: ObfuscationKind
    should_obfuscate: bool
    pruning_recipe: PruningRecipe
    distillation_recipe: DistillationRecipe
    inference_jitter: InferenceJitter
    residual_risk_class: ResidualRiskClass
    reason: str
    arch_hash: str  # SHA-256 of the input ArchSpec (audit linkability)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class EnergonPolicyStats:
    evaluations: int = 0
    applied_count: int = 0
    pass_through_count: int = 0
    unsupported_count: int = 0
    deferred_count: int = 0
    by_residual_risk: dict[str, int] = dataclasses.field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_arch(spec: ArchSpec) -> str:
    """Deterministic SHA-256 over the fields of an ArchSpec.

    Used as the seed source when spec.deterministic_seed == 0, and as
    the audit linkability tag returned in the decision.
    """
    payload = "|".join(
        [
            spec.family.value,
            str(spec.layer_count),
            str(spec.head_count),
            str(spec.head_dim),
            str(spec.hidden_dim),
            str(spec.vocab_size),
            spec.sensitivity.value,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _seed_for(spec: ArchSpec) -> int:
    """Choose the deterministic seed. spec.deterministic_seed if non-zero,
    otherwise the low 31 bits of the arch hash."""
    if spec.deterministic_seed != 0:
        return spec.deterministic_seed & 0x7FFFFFFF
    h = _hash_arch(spec)
    return int(h[:8], 16) & 0x7FFFFFFF


def _pruning_for_tier(spec: ArchSpec) -> PruningRecipe:
    """Per-tier pruning aggressiveness, deterministic per arch.

    PROPRIETARY: drop ~25% of attention heads in even-indexed layers.
    CLASSIFIED: drop ~50% of heads, in BOTH even and odd layers.
    PUBLIC: no pruning.
    """
    if spec.sensitivity == SensitivityTier.PUBLIC:
        return PruningRecipe(
            layers_to_prune_partially=(),
            heads_per_layer_to_drop={},
            keep_ratio=1.0,
        )
    drop_pct = 0.25 if spec.sensitivity == SensitivityTier.PROPRIETARY else 0.50
    keep_ratio = 1.0 - drop_pct
    seed = _seed_for(spec)

    target_layers = tuple(
        i
        for i in range(spec.layer_count)
        if (spec.sensitivity == SensitivityTier.PROPRIETARY and i % 2 == 0)
        or spec.sensitivity == SensitivityTier.CLASSIFIED
    )
    heads_per: dict[int, tuple[int, ...]] = {}
    heads_to_drop = max(1, int(spec.head_count * drop_pct))
    for layer_idx in target_layers:
        # Deterministic head selection — rotate-by-seed pattern so the same
        # spec always picks the same heads.
        start = (seed + layer_idx) % spec.head_count
        dropped = tuple((start + k) % spec.head_count for k in range(heads_to_drop))
        heads_per[layer_idx] = dropped
    return PruningRecipe(
        layers_to_prune_partially=target_layers,
        heads_per_layer_to_drop=heads_per,
        keep_ratio=keep_ratio,
    )


def _distillation_for_tier(spec: ArchSpec) -> DistillationRecipe:
    """Temperature-jittered distillation per sensitivity."""
    if spec.sensitivity == SensitivityTier.PUBLIC:
        return DistillationRecipe(
            temperature=1.0,
            temperature_jitter_pct=0.0,
            student_layer_ratio=1.0,
            epochs=0,
        )
    if spec.sensitivity == SensitivityTier.PROPRIETARY:
        return DistillationRecipe(
            temperature=2.5,
            temperature_jitter_pct=0.10,
            student_layer_ratio=0.85,
            epochs=3,
        )
    # CLASSIFIED
    return DistillationRecipe(
        temperature=4.0,
        temperature_jitter_pct=0.20,
        student_layer_ratio=0.70,
        epochs=5,
    )


def _jitter_for_tier(spec: ArchSpec) -> InferenceJitter:
    if spec.sensitivity == SensitivityTier.PUBLIC:
        return InferenceJitter(
            enabled=False,
            layer_order_perm_rate=0.0,
            dummy_op_insert_rate=0.0,
        )
    if spec.sensitivity == SensitivityTier.PROPRIETARY:
        return InferenceJitter(
            enabled=True,
            layer_order_perm_rate=0.10,
            dummy_op_insert_rate=0.05,
        )
    return InferenceJitter(
        enabled=True,
        layer_order_perm_rate=0.25,
        dummy_op_insert_rate=0.15,
    )


def _residual_risk_for(spec: ArchSpec) -> ResidualRiskClass:
    """Per Energon paper §6 evaluation table — software-only obfuscation
    is medium-effective on PROPRIETARY (drops accuracy from 89% to ~45%)
    and approaches LOW on CLASSIFIED (drops to ~15%)."""
    if spec.sensitivity == SensitivityTier.PUBLIC:
        return ResidualRiskClass.HIGH  # un-mitigated = full leakage
    if spec.sensitivity == SensitivityTier.PROPRIETARY:
        return ResidualRiskClass.MEDIUM
    return ResidualRiskClass.LOW


# ---------------------------------------------------------------------------
# EnergonPolicy
# ---------------------------------------------------------------------------


class EnergonPolicy:
    """Operator-facing policy engine. Stateless; one instance per host.

    The constructor accepts a `force_arch` env override (VOS3_ENERGON_FORCE_ARCH)
    that lets an admin bypass the support-list check for novel architectures.
    """

    _FORCE_ENV = "VOS3_ENERGON_FORCE_ARCH"

    def __init__(self):
        self._stats = EnergonPolicyStats()
        self._lock = threading.Lock()
        self._force_arch = os.environ.get(self._FORCE_ENV, "").strip().lower()

    # -- Evaluation ----------------------------------------------------------

    def evaluate(
        self, spec: ArchSpec, *, defer_application: bool = False
    ) -> ObfuscationDecision:
        if not isinstance(spec, ArchSpec):
            raise TypeError("spec must be ArchSpec")
        if spec.layer_count <= 0:
            raise ValueError("layer_count must be > 0")
        if spec.head_count <= 0:
            raise ValueError("head_count must be > 0")
        if spec.head_dim <= 0:
            raise ValueError("head_dim must be > 0")
        if spec.hidden_dim <= 0:
            raise ValueError("hidden_dim must be > 0")
        if spec.vocab_size <= 0:
            raise ValueError("vocab_size must be > 0")

        arch_hash = _hash_arch(spec)

        # Support-list check.
        if (
            spec.family not in _SUPPORTED_FAMILIES
            and spec.family.value.lower() != self._force_arch
        ):
            with self._lock:
                self._stats.evaluations += 1
                self._stats.unsupported_count += 1
            return ObfuscationDecision(
                kind=ObfuscationKind.UNSUPPORTED,
                should_obfuscate=False,
                pruning_recipe=PruningRecipe((), {}, 1.0),
                distillation_recipe=DistillationRecipe(1.0, 0.0, 1.0, 0),
                inference_jitter=InferenceJitter(False, 0.0, 0.0),
                residual_risk_class=ResidualRiskClass.HIGH,
                reason=f"family {spec.family.value!r} not in support list "
                f"(operator override: set env "
                f"{self._FORCE_ENV}={spec.family.value})",
                arch_hash=arch_hash,
            )

        # PUBLIC tier: pass-through.
        if spec.sensitivity == SensitivityTier.PUBLIC:
            with self._lock:
                self._stats.evaluations += 1
                self._stats.pass_through_count += 1
                self._stats.by_residual_risk[ResidualRiskClass.HIGH.value] = (
                    self._stats.by_residual_risk.get(ResidualRiskClass.HIGH.value, 0)
                    + 1
                )
            return ObfuscationDecision(
                kind=ObfuscationKind.PASS_THROUGH,
                should_obfuscate=False,
                pruning_recipe=PruningRecipe((), {}, 1.0),
                distillation_recipe=DistillationRecipe(1.0, 0.0, 1.0, 0),
                inference_jitter=InferenceJitter(False, 0.0, 0.0),
                residual_risk_class=ResidualRiskClass.HIGH,
                reason="public_sensitivity_no_obfuscation_required",
                arch_hash=arch_hash,
            )

        # Build recipes.
        pruning = _pruning_for_tier(spec)
        distillation = _distillation_for_tier(spec)
        jitter = _jitter_for_tier(spec)
        residual = _residual_risk_for(spec)

        kind = (
            ObfuscationKind.DEFERRED if defer_application else ObfuscationKind.APPLIED
        )
        with self._lock:
            self._stats.evaluations += 1
            if defer_application:
                self._stats.deferred_count += 1
            else:
                self._stats.applied_count += 1
            self._stats.by_residual_risk[residual.value] = (
                self._stats.by_residual_risk.get(residual.value, 0) + 1
            )

        return ObfuscationDecision(
            kind=kind,
            should_obfuscate=(not defer_application),
            pruning_recipe=pruning,
            distillation_recipe=distillation,
            inference_jitter=jitter,
            residual_risk_class=residual,
            reason=(
                "deferred_for_operator_review"
                if defer_application
                else f"applying_{spec.sensitivity.value}_tier_obfuscation"
            ),
            arch_hash=arch_hash,
        )

    # -- Determinism check ---------------------------------------------------

    def evaluate_twice_check(self, spec: ArchSpec) -> bool:
        """Returns True iff evaluating the same spec twice produces the
        same recipes (i.e. the policy is deterministic for this spec).
        Audit helper for operator verification."""
        d1 = self.evaluate(spec)
        d2 = self.evaluate(spec)
        return (
            d1.pruning_recipe == d2.pruning_recipe
            and d1.distillation_recipe == d2.distillation_recipe
            and d1.inference_jitter == d2.inference_jitter
            and d1.arch_hash == d2.arch_hash
        )

    # -- Stats ---------------------------------------------------------------

    def snapshot_stats(self) -> EnergonPolicyStats:
        with self._lock:
            return EnergonPolicyStats(
                evaluations=self._stats.evaluations,
                applied_count=self._stats.applied_count,
                pass_through_count=self._stats.pass_through_count,
                unsupported_count=self._stats.unsupported_count,
                deferred_count=self._stats.deferred_count,
                by_residual_risk=dict(self._stats.by_residual_risk),
            )


__all__ = [
    "TransformerFamily",
    "SensitivityTier",
    "ResidualRiskClass",
    "ArchSpec",
    "PruningRecipe",
    "DistillationRecipe",
    "InferenceJitter",
    "ObfuscationKind",
    "ObfuscationDecision",
    "EnergonPolicyStats",
    "EnergonPolicy",
]
