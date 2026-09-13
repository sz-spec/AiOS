"""Boot-time probe for the local Ollama daemon.

Goal: keep the boot path non-blocking and air-gap-friendly. We hit
`OLLAMA_BASE_URL/api/tags` once at startup with a 2-second hard timeout.
The result is cached for the process lifetime — no per-request latency.

The probe never raises. Any network error, DNS failure, or non-2xx
response is folded into ``False`` so the rest of startup can continue
(community profile must boot even when Ollama isn't installed).

Wire-up: ``backend/startup.py`` calls :func:`probe_ollama` after the
LLM factory pre-warm and stores the result at
``services["ollama_available"]``. ``backend/src/efficiency/router.py``
uses that flag to skip ollama-provider entries when the daemon isn't
reachable.
"""

from __future__ import annotations

import logging
import os
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("ollama_probe")

DEFAULT_BASE_URL = "http://localhost:11434"
PROBE_TIMEOUT_SECONDS = 2.0
PROBE_PATH = "/api/tags"

_probe_cache: Optional[bool] = None
_probe_models: tuple[str, ...] = ()


def get_base_url() -> str:
    """Return the configured Ollama base URL.

    Strips a trailing slash so callers can safely append a path.
    """
    return os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def probe_ollama(*, force: bool = False) -> bool:
    """Probe the local Ollama daemon. Returns ``True`` if reachable.

    The result is cached after the first call. Pass ``force=True`` to
    re-probe (useful for tests).

    Never raises — the probe is best-effort and degraded modes are
    expected.
    """
    global _probe_cache, _probe_models
    if _probe_cache is not None and not force:
        return _probe_cache

    url = f"{get_base_url()}{PROBE_PATH}"
    try:
        req = Request(url, headers={"User-Agent": "vos3-ollama-probe/1.0"})
        with urlopen(req, timeout=PROBE_TIMEOUT_SECONDS) as resp:
            if resp.status != 200:
                logger.warning("Ollama probe %s returned HTTP %s", url, resp.status)
                _probe_cache = False
                _probe_models = ()
                return False

            # Parse the model list opportunistically — small payload, never raises
            try:
                import json

                body = resp.read(64 * 1024).decode("utf-8", errors="replace")
                data = json.loads(body)
                models = tuple(m.get("name", "") for m in data.get("models", []))
                _probe_models = tuple(m for m in models if m)
            except Exception as parse_err:  # noqa: BLE001
                logger.debug("Ollama probe model-list parse failed: %s", parse_err)
                _probe_models = ()

        _probe_cache = True
        logger.info(
            "Ollama probe OK at %s (%d local model(s) listed)",
            url,
            len(_probe_models),
        )
        return True
    except URLError as exc:
        # Connection refused, DNS error, timeout — all fold to "not available"
        logger.info(
            "Ollama not reachable at %s (%s) — disabling local-ollama lane",
            url,
            exc.reason,
        )
        _probe_cache = False
        _probe_models = ()
        return False
    except Exception as exc:  # noqa: BLE001
        # Defensive: an exotic exception (proxy misconfig, ssl, etc.) must not
        # crash the boot path. Treat as unavailable.
        logger.warning(
            "Ollama probe raised %s: %s — disabling local-ollama lane",
            type(exc).__name__,
            exc,
        )
        _probe_cache = False
        _probe_models = ()
        return False


def get_local_models() -> tuple[str, ...]:
    """Return the model names announced by the daemon (empty if unprobed)."""
    return _probe_models


def reset_cache() -> None:
    """Clear the probe cache. Test-only."""
    global _probe_cache, _probe_models
    _probe_cache = None
    _probe_models = ()


__all__ = [
    "DEFAULT_BASE_URL",
    "PROBE_TIMEOUT_SECONDS",
    "get_base_url",
    "probe_ollama",
    "get_local_models",
    "reset_cache",
]
