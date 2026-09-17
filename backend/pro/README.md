# VOS3 Backend Pro — Sovereign Enterprise Components

This directory contains backend components classified as **PRO**
(Sovereign Enterprise edition) under the v20.5.2 open-core split.

## Files in this directory

- `finetune_engine.py` — PRO control-plane wrapper around the retained CORE
  engine in `backend/services/finetune_engine.py`. The wrapper checks the current
  environment-based license gate and exposes organization quota metadata.
  It does not implement cryptographic license validation or hard quota enforcement.

## Import and configuration

The CORE engine remains available for private training:

```python
from services.finetune_engine import FineTuneConfig, SovereignFineTuner
```

The PRO entry point requires the organization and the same explicit configuration:

```python
from pro.finetune_engine import SovereignFineTunerPro
trainer = SovereignFineTunerPro(org_id="example", config=config)
```

Both the backend-directory `pro.*` and repository-root `backend.pro.*` package
layouts resolve the corresponding CORE engine. Heavy training dependencies remain
lazy requirements of the CORE training operation.

## Legal note

The "VOS3 Sovereign Enterprise — Proprietary" headers in this
directory are a **proposed licensing scheme**. The repo-wide `LICENSE`
file remains MIT pending legal review. Until that review completes,
all VOS3 code is governed by the existing MIT license.
