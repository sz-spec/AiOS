"""
backend/security
=================

Sprint 15 / Item I6 — application-layer security primitives that wrap or
extend the kernel-side security primitives in core/security/.

Modules:
  hf_config_scanner — detects malicious Hugging Face model-repo
                      configuration files (pickle imports, dangerous
                      auto_map remote-code execution, trust_remote_code
                      flags, .py shims in tokenizer configs).

This package is application-layer (used by api/* and services/* routes).
Kernel-attached security (TEE, rotation, cert vault, connectors) stays
in core/security/.
"""

from __future__ import annotations

__all__: list[str] = []
