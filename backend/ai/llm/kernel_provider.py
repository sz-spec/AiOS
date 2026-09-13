"""Kernel-resident LLM provider.

W2.1e — bridges the VOS3 Kernel Inference Manager (KIM) into the Smart
Router's provider matrix. A kernel-attested GGUF slot becomes selectable
like any cloud route once it's registered here.

Lifecycle
---------
1. Operator (or test harness) loads a GGUF blob into a kernel slot via
   ``services.model_manager.ModelManager.load_to_kernel(model_id, slot_id)``.
2. On success, the loader calls :func:`register_kernel_slot` to bind a
   logical alias (e.g. ``"kernel-default"``) to the freshly populated slot.
3. The Smart Router (``backend/src/efficiency/router.py``) consults
   :func:`is_kernel_default_ready` when ``VOS3_LOCALITY_PREFERENCE`` is
   ``local-first`` or ``auto``. If a kernel slot is bound, the router
   selects ``"kernel-default"`` and the LLM factory dispatches through
   :class:`KernelProvider`.
4. :class:`KernelProvider.invoke` issues ``KIM_GENERATE`` over VBus and
   collects streamed tokens via the existing ``vbus_driver.drain_tokens``
   path. Tokens are HMAC-chained per slot — provenance verification is
   already handled by the driver.

Air-gap behaviour
-----------------
This module never reaches the network. The VBus connection is a local
Unix socket (default ``/tmp/vos3_bridge.sock``). If the socket is
unreachable, ``invoke`` raises ``KernelProviderUnavailable`` and the
router falls through to its next preference.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional

logger = logging.getLogger("kernel_provider")

DEFAULT_KERNEL_ALIAS = "kernel-default"
DEFAULT_SOCKET_PATH = "/tmp/vos3_bridge.sock"
DEFAULT_TOKEN_DRAIN_INTERVAL_S = 0.05
DEFAULT_TOKEN_TIMEOUT_S = 30.0


# ---------------------------------------------------------------------------
# Sprint 19 / Wave 2 — E6 perf-counter lockdown gate wiring.
#
# astream() launches inference on a slot-resident model on a shared
# accelerator host. A co-tenant that can read CPU/GPU performance counters
# can fingerprint this model via timing/counter side-channels. We gate the
# launch on PerfCounterLockdown.require_lockdown_for_multitenant_inference()
# — fail-closed unless the counters are provably locked down.
#
# Default ON. Single-tenant / air-gapped deployments set
# VOS3_PERF_SINGLE_TENANT=1 (no co-tenant to leak to → gate skipped). The
# whole wiring can be disabled with VOS3_DISABLE_PERF_GUARD=1. A real-but-
# unverifiable host uses the guard's VOS3_PERF_DEV_OVERRIDE=1 escape hatch.
# See backend/security/perf_counter_lockdown.py.
# ---------------------------------------------------------------------------

ENV_DISABLE_PERF_GUARD = "VOS3_DISABLE_PERF_GUARD"


def _perf_guard_disabled_via_env() -> bool:
    return os.environ.get(ENV_DISABLE_PERF_GUARD, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _enforce_perf_lockdown_for_inference(alias: str, slot_id: int) -> None:
    """Fail-closed E6 gate run before launching inference on a shared
    accelerator. Raises PerfCounterExposed when unprivileged perf-counter
    access is open. Import errors degrade gracefully; a present guard that
    REFUSES propagates."""
    if _perf_guard_disabled_via_env():
        logger.warning(
            "[kernel_provider] E6 perf-counter gate DISABLED via %s for "
            "alias=%s slot=%d — inference launch proceeds unguarded.",
            ENV_DISABLE_PERF_GUARD,
            alias,
            slot_id,
        )
        return
    try:
        from security.perf_counter_lockdown import PerfCounterLockdown
    except ImportError:
        try:
            from backend.security.perf_counter_lockdown import PerfCounterLockdown
        except ImportError:
            logger.warning(
                "[kernel_provider] perf_counter_lockdown module not "
                "importable — inference launch for alias=%s slot=%d proceeds "
                "without the E6 gate.",
                alias,
                slot_id,
            )
            return
    report = PerfCounterLockdown().require_lockdown_for_multitenant_inference()
    logger.info(
        "[kernel_provider] E6 perf-counter gate passed for alias=%s slot=%d (%s)",
        alias,
        slot_id,
        report.reason,
    )


# ---------------------------------------------------------------------------
# Slot registry — tracks which kernel slots back which router aliases.
# ---------------------------------------------------------------------------


@dataclass
class _SlotBinding:
    alias: str
    slot_id: int
    model_id: str
    bound_at: float
    metadata: dict = field(default_factory=dict)


_lock = threading.Lock()
_slots: dict[str, _SlotBinding] = {}


def register_kernel_slot(
    alias: str,
    slot_id: int,
    *,
    model_id: str = "",
    metadata: Optional[dict] = None,
) -> None:
    """Bind a router alias (e.g. ``kernel-default``) to a kernel slot.

    Called from ``model_manager.load_to_kernel`` after a successful
    SLOT_FINISH handshake. Subsequent calls overwrite the binding (the
    most-recently-loaded slot wins, mirroring AI_KIM behaviour).
    """
    if not 1 <= slot_id <= 7:
        raise ValueError(f"slot_id must be 1-7 (slot 0 reserved); got {slot_id}")
    with _lock:
        _slots[alias] = _SlotBinding(
            alias=alias,
            slot_id=slot_id,
            model_id=model_id,
            bound_at=time.time(),
            metadata=dict(metadata or {}),
        )
    logger.info(
        "kernel_provider: bound alias=%r → slot=%d model=%r",
        alias,
        slot_id,
        model_id,
    )


def unregister_kernel_slot(alias: str) -> bool:
    """Drop the binding for ``alias``. Returns ``True`` if it existed."""
    with _lock:
        existed = _slots.pop(alias, None) is not None
    if existed:
        logger.info("kernel_provider: unbound alias=%r", alias)
    return existed


def get_kernel_slot(alias: str = DEFAULT_KERNEL_ALIAS) -> Optional[_SlotBinding]:
    """Return the binding for ``alias`` or ``None`` if unbound."""
    with _lock:
        return _slots.get(alias)


def list_kernel_slots() -> dict[str, _SlotBinding]:
    """Return a snapshot of every registered binding."""
    with _lock:
        return dict(_slots)


def is_kernel_default_ready() -> bool:
    """Whether the canonical ``kernel-default`` alias is bound to a slot.

    Consumed by ``backend/src/efficiency/router.py::_kernel_slot_available``.
    """
    return get_kernel_slot(DEFAULT_KERNEL_ALIAS) is not None


# ---------------------------------------------------------------------------
# LLM provider — issues KIM_GENERATE over VBus and assembles streamed tokens.
# ---------------------------------------------------------------------------


class KernelProviderUnavailable(RuntimeError):
    """Raised when the VBus bridge cannot be reached or no slot is bound."""


class KernelProvider:
    """Dispatches inference to a kernel-resident GGUF slot via VBus.

    The provider does NOT keep a persistent VBus connection — each call
    opens a fresh ``VBusDriver`` and tears it down afterwards. This keeps
    the surface stateless from the caller's point of view and lets
    ``model_manager.load_to_kernel`` continue to own its own connection
    during loads.
    """

    def __init__(
        self,
        alias: str = DEFAULT_KERNEL_ALIAS,
        *,
        socket_path: str = DEFAULT_SOCKET_PATH,
        token_drain_interval_s: float = DEFAULT_TOKEN_DRAIN_INTERVAL_S,
        token_timeout_s: float = DEFAULT_TOKEN_TIMEOUT_S,
    ) -> None:
        self.alias = alias
        self.socket_path = socket_path
        self.token_drain_interval_s = token_drain_interval_s
        self.token_timeout_s = token_timeout_s

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def invoke(
        self,
        prompt: str,
        *,
        max_tokens: int = 256,
        temperature: float = 1.0,
    ) -> str:
        """Run inference on the kernel slot. Returns the assembled string."""
        return "".join(
            self.astream(prompt, max_tokens=max_tokens, temperature=temperature)
        )

    def astream(
        self,
        prompt: str,
        *,
        max_tokens: int = 256,
        temperature: float = 1.0,
    ) -> Iterator[str]:
        """Yield decoded tokens as the kernel emits them.

        The first token may take noticeably longer than subsequent ones —
        the kernel allocates the KV-cache lazily on first call. After that
        each token frame arrives every ~5 ms on reference hardware.
        """
        binding = get_kernel_slot(self.alias)
        if binding is None:
            raise KernelProviderUnavailable(
                f"No kernel slot bound to alias {self.alias!r}. "
                "Run model_manager.load_to_kernel(...) first."
            )

        # E6 (Sprint 19 W2): fail-closed perf-counter lockdown gate. Refuse
        # to launch inference on a shared accelerator host where co-tenants
        # can read perf counters and fingerprint this model. Raises
        # PerfCounterExposed (fail-closed) unless locked / single-tenant.
        _enforce_perf_lockdown_for_inference(self.alias, binding.slot_id)

        # The kernel does not currently consume a prompt argument over the
        # KIM_GENERATE command (Phase 6 v2.0 — token streaming uses the
        # slot-resident KV cache). The prompt is consumed by sys_inference_hint
        # callers; KIM_GENERATE is a continuation primitive. We log the prompt
        # length so the caller can confirm wiring without leaking content.
        logger.debug(
            "kernel_provider.astream: alias=%s slot=%d prompt_chars=%d max_tokens=%d temp=%.2f",
            self.alias,
            binding.slot_id,
            len(prompt),
            max_tokens,
            temperature,
        )

        # Lazy import — ``vbus_driver`` pulls in heavy crypto deps that we
        # don't want loaded at module import time.
        from services.vbus_driver import VBusDriver, VBusError

        driver = VBusDriver(socket_path=self.socket_path)
        if not driver.connect():
            raise KernelProviderUnavailable(
                f"VBus bridge at {self.socket_path} is unreachable"
            )

        try:
            temperature_fp = max(1, int(round(temperature * 100)))
            try:
                resp = driver.kim_generate(
                    slot_id=binding.slot_id,
                    max_tokens=max_tokens,
                    temperature=temperature_fp,
                )
            except VBusError as exc:
                raise KernelProviderUnavailable(
                    f"KIM_GENERATE rejected by kernel: {exc}"
                ) from exc

            logger.debug("kernel_provider.astream: kernel ack=%s", resp)

            # Drain streamed tokens. The kernel sends VBUS_TYPE_TOKEN_STREAM
            # frames; vbus_driver buffers them in `_token_queue[slot_id]`.
            deadline = time.monotonic() + self.token_timeout_s
            tokens_yielded = 0
            while tokens_yielded < max_tokens and time.monotonic() < deadline:
                buffered = driver.drain_tokens(slot_id=binding.slot_id)
                if not buffered:
                    time.sleep(self.token_drain_interval_s)
                    continue
                for frame in buffered:
                    text = self._decode_token_frame(frame)
                    if text is None:
                        continue
                    yield text
                    tokens_yielded += 1
                    if tokens_yielded >= max_tokens:
                        break
                    if frame.get("flags", 0) & 0x01:  # EOS bit
                        return
        finally:
            try:
                driver.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_token_frame(frame: dict) -> Optional[str]:
        """Best-effort decode of a token-stream frame to its surface form.

        The frame layout (see ``vbus_driver._parse_token_frame``):
            slot_id, token_id, seq, flags, timestamp_ms, [text]

        Phase 6 carries an optional UTF-8 text suffix in the payload. If
        present we surface it; otherwise we return the token id as a
        stringified placeholder so callers can still stream something.
        """
        text = frame.get("text")
        if isinstance(text, str) and text:
            return text
        token_id = frame.get("token_id")
        if token_id is None:
            return None
        return f"<{token_id}>"


__all__ = [
    "DEFAULT_KERNEL_ALIAS",
    "DEFAULT_SOCKET_PATH",
    "KernelProvider",
    "KernelProviderUnavailable",
    "register_kernel_slot",
    "unregister_kernel_slot",
    "get_kernel_slot",
    "list_kernel_slots",
    "is_kernel_default_ready",
]
