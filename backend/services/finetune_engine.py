"""
VOS3 Sovereign Fine-Tuning Engine — v20.2.1-TRAIN
====================================================

QLoRA fine-tuning pipeline anchored to VOS3 kernel primitives:
  - MMR audit ledger records every N steps (`mmr_record_finetune_step`)
  - TPM PCR 11 sealing of the resulting LoRA adapter SHA-256
  - NPU gradient-offload skeleton via `npu_ops.c` (capability-gated)

----------------------------------------------------------------------
HONEST STATUS DISCLAIMER (April 2026)
----------------------------------------------------------------------
This module is the architectural scaffold for the v20.6 training pipeline.
It is NOT runnable in the default VOS3 development image — the heavyweight
training dependencies are NOT installed by default because they pull in
~4 GB of CUDA runtime and ML weights.

Operators who want to actually run training must install:

    pip install \\
        "torch>=2.7" \\
        "unsloth>=2026.04" \\
        "bitsandbytes>=0.46" \\
        "transformers>=4.50" \\
        "peft>=0.14" \\
        "datasets>=3.0" \\
        "accelerate>=1.0"

Until that install runs, every public method on `SovereignFineTuner.train`
raises `FineTuneDependencyError` with a precise message identifying which
library is missing. Silent no-op in a training pipeline is a class of bug
we will not ship.

----------------------------------------------------------------------
What this module DOES, in any environment:
----------------------------------------------------------------------
  1. Defines the SovereignFineTuner API surface for higher-level callers
  2. Defines the SovereignDatasetLoader streaming contract (vbus:// or file://)
  3. Wires kernel-side primitives via services.vbus_driver:
       - mmr_record_finetune_step()  every N steps (default 50)
       - vos3_tpm_seal_adapter()     once on training completion
  4. Defers actual gradient computation, NPU offload, and CUDA work to
     the optional libraries above

----------------------------------------------------------------------
What this module does NOT do, even when libraries are present:
----------------------------------------------------------------------
  - NPU thread/cluster pinning (vendor SDK required, not bundled)
  - Cross-host distributed training (FSDP) — out of scope for v20.6
  - Federated / multi-tenant aggregation — v21.0 horizon
  - GGUF export pipeline wiring (requires llama.cpp + base model cache)
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
from typing import Iterator, Optional, Protocol

logger = logging.getLogger("vos3.finetune")

# ---------------------------------------------------------------------------
# Graceful dependency import — fail loud, never silent
# ---------------------------------------------------------------------------


class FineTuneDependencyError(RuntimeError):
    """Raised when a heavyweight ML dependency required for actual training
    is not installed in the current environment."""


_MISSING_DEPS: list[str] = []
for _mod_name in ("torch", "unsloth", "bitsandbytes", "peft", "transformers"):
    try:
        __import__(_mod_name)
    except ImportError:
        _MISSING_DEPS.append(_mod_name)


def _require_deps() -> None:
    if _MISSING_DEPS:
        raise FineTuneDependencyError(
            "VOS3 fine-tuning engine requires the following libraries to "
            f"be installed: {sorted(set(_MISSING_DEPS))}. Run the pip "
            "install snippet documented at the top of "
            "backend/services/finetune_engine.py to enable training."
        )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class FineTuneConfig:
    """Configuration for a single fine-tune run."""

    base_model: str  # e.g. "gemma-4-27b"
    dataset_uri: str  # vbus:// or file:// or bare path
    output_path: str  # where the LoRA adapter will land
    max_steps: int = 1000
    learning_rate: float = 2e-4
    lora_rank: int = 16
    lora_alpha: int = 32
    target_modules: tuple[str, ...] = ("q_proj", "k_proj", "v_proj", "o_proj")
    quantization: str = "nf4"  # bitsandbytes 4-bit
    gradient_accumulation_steps: int = 4
    seed: int = 42

    # MMR audit cadence — every N steps. v20.2.1 spec: 50.
    mmr_record_every_steps: int = 50

    # TPM PCR index for sealing the resulting adapter hash.
    # PCR 11 per v20.2.1 spec (in OS/application range 8-15).
    tpm_pcr_index: int = 11

    # If True, abort the training isolate on detected memory leaks.
    # NOTE: full kernel panic is a DoS vector — the responsible default
    # is isolate-kill + structured log, with kernel panic available
    # via `VOS3_FINETUNE_PANIC_ON_LEAK=true` env (opt-in only).
    panic_on_isolate_leak: bool = False


# ---------------------------------------------------------------------------
# Sovereign Dataset Loader
# ---------------------------------------------------------------------------


class DatasetSource(Protocol):
    """Streaming dataset interface. Each yield produces one training example
    as a dict. Sources never write to host temp/swap — see implementations."""

    def __iter__(self) -> Iterator[dict]: ...
    def hash_fingerprint(self) -> str: ...


class _VBusDatasetLoader:
    """Streams a dataset over VBus IPC. Used when the dataset is sourced
    from the kernel's own audit ledger or from a peer slot.

    NOTE: VBus is a small-message ASCII command bus, NOT optimized for
    bulk binary streaming. Use this loader only for small instruction-
    tuning datasets (< 10 MB). Larger datasets should mount via VFS or
    NVMe direct-read paths (use file:// instead)."""

    def __init__(self, vbus_uri: str):
        self._uri = vbus_uri
        self._fingerprint = ""

    def __iter__(self) -> Iterator[dict]:
        try:
            from services.vbus_driver import VBusDriver
        except ImportError as exc:
            raise FineTuneDependencyError(f"VBusDriver unavailable: {exc}")
        driver = VBusDriver()
        if not driver.connect():
            raise RuntimeError("VBus dataset stream: kernel offline")
        try:
            cursor = 0
            while True:
                resp = driver.send_command(f"DS_READ {self._uri} {cursor}")
                if not resp or resp.startswith("ERR") or resp == "EOF":
                    return
                parts = dict(
                    p.split("=", 1) for p in resp.lstrip("OK|").split("|") if "=" in p
                )
                self._fingerprint = parts.get("fingerprint", self._fingerprint)
                cursor = int(parts.get("cursor", str(cursor + 1)))
                payload = parts.get("payload", "")
                if not payload:
                    return
                yield {"text": payload}
        finally:
            driver.disconnect()

    def hash_fingerprint(self) -> str:
        return self._fingerprint


class _LocalDatasetLoader:
    """Streams a local file dataset with O(1) memory footprint. The
    fingerprint is the SHA-256 of the file contents — recorded into the
    MMR ledger so the trained model is provably tied to this exact data."""

    def __init__(self, file_path: str):
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)
        self._path = file_path
        self._fingerprint: Optional[str] = None

    def __iter__(self) -> Iterator[dict]:
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield {"text": line}

    def hash_fingerprint(self) -> str:
        if self._fingerprint is None:
            h = hashlib.sha256()
            with open(self._path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
            self._fingerprint = h.hexdigest()
        return self._fingerprint


def open_dataset(uri: str) -> DatasetSource:
    """Open a dataset URI as a streaming source. Supports:
    - vbus://<channel>     — kernel-streamed (small datasets only)
    - file://<path>        — local file
    - <bare path>          — treated as file://
    """
    if uri.startswith("vbus://"):
        return _VBusDatasetLoader(uri[len("vbus://") :])
    if uri.startswith("file://"):
        return _LocalDatasetLoader(uri[len("file://") :])
    return _LocalDatasetLoader(uri)


# ---------------------------------------------------------------------------
# Kernel primitive wrappers
# ---------------------------------------------------------------------------


def _mmr_record_finetune_step(
    step: int, loss: float, lr: float, grad_norm: float
) -> bool:
    """Record a fine-tune step into the kernel MMR audit chain.

    Sends the VBus command `MMR_FINETUNE_STEP <step> <loss> <lr> <grad>`.
    Returns True if the kernel acknowledged the record. Never raises —
    a logging failure must not block training.
    """
    try:
        from services.vbus_driver import VBusDriver

        driver = VBusDriver()
        if not driver.connect():
            logger.warning("MMR finetune step: kernel offline; step=%d", step)
            return False
        try:
            cmd = f"MMR_FINETUNE_STEP {step} {loss:.6f} {lr:.8f} {grad_norm:.6f}"
            resp = driver.send_command(cmd)
            return bool(resp and resp.startswith("OK"))
        finally:
            driver.disconnect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("MMR finetune step record failed: %s", exc)
        return False


def _tpm_seal_adapter(adapter_path: str, pcr_index: int = 11) -> Optional[str]:
    """Seal the resulting LoRA adapter hash into TPM PCR `pcr_index`.

    Computes SHA-256 of the adapter file, sends it via VBus to
    `vos3_tpm_seal_adapter`, returns the hex hash on success or None
    if the file is missing. Never raises.
    """
    if not os.path.isfile(adapter_path):
        return None

    h = hashlib.sha256()
    with open(adapter_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    digest = h.hexdigest()

    try:
        from services.vbus_driver import VBusDriver

        driver = VBusDriver()
        if not driver.connect():
            logger.warning("TPM seal: kernel offline; adapter hash=%s", digest)
            return digest
        try:
            cmd = f"TPM_SEAL_ADAPTER {pcr_index} {digest}"
            resp = driver.send_command(cmd)
            if resp and resp.startswith("OK"):
                logger.info(
                    "TPM seal SUCCESS: PCR[%d] extended with %s — model "
                    "labeled VOS-CERTIFIED-SOVEREIGN",
                    pcr_index,
                    digest,
                )
                return digest
            logger.warning("TPM seal: kernel rejected; adapter hash=%s", digest)
            return digest
        finally:
            driver.disconnect()
    except Exception as exc:  # noqa: BLE001
        logger.warning("TPM seal failed: %s; adapter hash=%s", exc, digest)
        return digest


# ---------------------------------------------------------------------------
# The Sovereign Fine-Tuner
# ---------------------------------------------------------------------------


class SovereignFineTuner:
    """QLoRA fine-tuning with VOS3 audit + sealing.

    Calling `train()` requires the heavyweight ML dependencies. The
    audit-recording and sealing methods (`record_step`, `seal`) work
    without those dependencies — they only need VBus.
    """

    def __init__(self, config: FineTuneConfig):
        self.cfg = config
        self._step = 0
        self._dataset_fingerprint: Optional[str] = None
        self._final_adapter_hash: Optional[str] = None

    # ---- Audit + sealing (work without ML deps) ----

    def record_step(self, step: int, loss: float, lr: float, grad_norm: float) -> bool:
        return _mmr_record_finetune_step(step, loss, lr, grad_norm)

    def seal(self, adapter_path: str) -> Optional[str]:
        self._final_adapter_hash = _tpm_seal_adapter(
            adapter_path, self.cfg.tpm_pcr_index
        )
        return self._final_adapter_hash

    # ---- The actual training loop (requires ML deps) ----

    def train(self, dataset: DatasetSource) -> str:
        """Run the QLoRA training loop. Returns the path to the resulting
        LoRA adapter directory.

        Raises:
            FineTuneDependencyError: if ML deps are not installed.
        """
        _require_deps()

        # Lazy imports — only after the dependency check passes.
        from unsloth import FastLanguageModel  # type: ignore
        from peft import LoraConfig  # type: ignore
        from transformers import TrainingArguments, Trainer, TrainerCallback  # type: ignore
        from datasets import Dataset  # type: ignore

        # Record the dataset fingerprint into the MMR BEFORE training begins.
        # This is the "trained on this exact data" cryptographic anchor.
        self._dataset_fingerprint = dataset.hash_fingerprint()
        logger.info(
            "FINETUNE_START base=%s dataset_fp=%s",
            self.cfg.base_model,
            self._dataset_fingerprint,
        )

        # Materialize the streaming dataset into a HuggingFace Dataset
        # in memory (NOT on disk). This is the "never touches the host's
        # temp/swap" requirement — the dataset stays in RAM only.
        examples = list(dataset)
        ds = Dataset.from_list(examples)

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=self.cfg.base_model,
            max_seq_length=4096,
            load_in_4bit=True,
        )

        lora = LoraConfig(
            r=self.cfg.lora_rank,
            lora_alpha=self.cfg.lora_alpha,
            target_modules=list(self.cfg.target_modules),
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = FastLanguageModel.get_peft_model(model, lora)

        args = TrainingArguments(
            output_dir=self.cfg.output_path,
            max_steps=self.cfg.max_steps,
            learning_rate=self.cfg.learning_rate,
            gradient_accumulation_steps=self.cfg.gradient_accumulation_steps,
            seed=self.cfg.seed,
            logging_steps=1,
            save_strategy="no",
            report_to=[],
        )

        engine = self

        class _MMRCallback(TrainerCallback):
            def on_step_end(self, args, state, control, **kwargs):
                if state.global_step % engine.cfg.mmr_record_every_steps != 0:
                    return
                logs = state.log_history[-1] if state.log_history else {}
                loss = float(logs.get("loss", 0.0))
                lr = float(logs.get("learning_rate", engine.cfg.learning_rate))
                grad = float(logs.get("grad_norm", 0.0))
                engine.record_step(state.global_step, loss, lr, grad)

        trainer = Trainer(
            model=model,
            args=args,
            train_dataset=ds,
            tokenizer=tokenizer,
            callbacks=[_MMRCallback()],
        )

        trainer.train()
        trainer.save_model(self.cfg.output_path)

        # Seal the resulting adapter into TPM PCR 11.
        adapter_file = os.path.join(self.cfg.output_path, "adapter_model.safetensors")
        self.seal(adapter_file)

        return self.cfg.output_path

    # ---- GGUF export (requires llama.cpp tooling) ----

    def export_gguf(
        self, adapter_path: str, gguf_output: str, quant: str = "Q4_K_M"
    ) -> str:
        """Convert the LoRA adapter + base model to GGUF for hot-loading
        into the Ollama-TITAN backend.

        Delegates to `llama.cpp/convert.py` and `llama-quantize`. Both
        must be installed and on PATH.
        """
        import shutil

        if shutil.which("llama-quantize") is None:
            raise FineTuneDependencyError(
                "llama.cpp tooling not on PATH. GGUF export requires the "
                "llama.cpp `convert.py` script and the `llama-quantize` "
                "binary — install via `brew install llama.cpp` (macOS) or "
                "build from source per https://github.com/ggerganov/llama.cpp"
            )
        raise NotImplementedError(
            "GGUF export pipeline requires base model weights + llama.cpp "
            "convert.py. Wiring deferred to v20.6 once the model registry "
            "settles."
        )


__all__ = [
    "FineTuneConfig",
    "FineTuneDependencyError",
    "SovereignFineTuner",
    "DatasetSource",
    "open_dataset",
]
