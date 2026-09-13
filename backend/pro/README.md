# VOS3 Backend Pro — Sovereign Enterprise Components

This directory contains backend components classified as **PRO**
(Sovereign Enterprise edition) under the v20.5.2 open-core split.

## Files in this directory

- `finetune_engine.py` — QLoRA fine-tuning pipeline with MMR audit
  trail and TPM PCR-11 sealing. Was at `backend/services/finetune_engine.py`
  in v20.2.1; physically relocated here in v20.5.2.

## Import path note

Code that previously did:
```python
from services.finetune_engine import SovereignFineTuner
```

Now must use:
```python
from pro.finetune_engine import SovereignFineTuner
```

The `backend/` root is on `sys.path` for tests and the running FastAPI
process, so `pro.*` imports resolve naturally.

## Why this directory IS physically separated (vs. kernel/pro/)

Python imports are dynamic and path-based — relocating a Python module
is a simple `git mv` plus an import-path update. There is no analog to
the kernel's Makefile pattern-rule constraint that forced the kernel
PRO files to stay under `kernel/src/`.

## Legal note

The "VOS3 Sovereign Enterprise — Proprietary" headers in this
directory are a **proposed licensing scheme**. The repo-wide `LICENSE`
file remains MIT pending legal review. Until that review completes,
all VOS3 code is governed by the existing MIT license.
