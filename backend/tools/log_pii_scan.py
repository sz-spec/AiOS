# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 VOS3 Project
"""
backend/tools/log_pii_scan.py — re-export shim for the canonical PII scanner.

Why this file exists
--------------------

The canonical implementation lives at the repo-root ``tools/log_pii_scan.py``
(run by ``.github/workflows/ci.yml`` as ``python tools/log_pii_scan.py``).

``backend/tools/`` is a *regular* package (it ships ``__init__.py`` with real
exports), so whenever ``backend/`` is on ``sys.path`` — which the test
conftest guarantees via ``sys.path.insert(0, backend/)`` — ``import tools``
resolves to ``backend/tools`` and *shadows* the repo-root namespace ``tools``.
That made ``from tools.log_pii_scan import ...`` raise ``ModuleNotFoundError``
in the test process even though the file exists at the repo root.

Rather than rename the long-standing ``backend/tools`` package (huge blast
radius) or reorder ``sys.path`` (fragile, sys.modules-cache dependent), this
shim re-exports the canonical scanner's public API by loading the repo-root
file directly. Single source of truth: the repo-root file. No divergence.
"""

from __future__ import annotations

import importlib.util as _ilu
from pathlib import Path as _Path

# backend/tools/log_pii_scan.py -> parents[2] == repo root.
_CANONICAL = _Path(__file__).resolve().parents[2] / "tools" / "log_pii_scan.py"

_spec = _ilu.spec_from_file_location("_canonical_log_pii_scan", _CANONICAL)
if _spec is None or _spec.loader is None:  # pragma: no cover - defensive
    raise ImportError(f"cannot load canonical log_pii_scan from {_CANONICAL}")
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Re-export the public API the tests + CI rely on.
luhn_check = _mod.luhn_check
israeli_id_check = _mod.israeli_id_check
load_allowlist = _mod.load_allowlist
scan_text = _mod.scan_text
scan_path = _mod.scan_path
main = _mod.main

EMAIL_RE = _mod.EMAIL_RE
PHONE_RE = _mod.PHONE_RE
SSN_RE = _mod.SSN_RE
CC_RE = _mod.CC_RE

__all__ = [
    "luhn_check",
    "israeli_id_check",
    "load_allowlist",
    "scan_text",
    "scan_path",
    "main",
    "EMAIL_RE",
    "PHONE_RE",
    "SSN_RE",
    "CC_RE",
]


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    _sys.exit(main())
