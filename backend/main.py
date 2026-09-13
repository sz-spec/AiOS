"""VOS3 — AI Operating System (Thin entry point).

Delegates to the factory in app.py.  Backward-compatible: ``from main import app``
and ``from main import services`` still work for existing tests.

Run with:
    uvicorn main:app --reload --port 8000
    # or factory mode:
    uvicorn backend.app:create_app --factory
"""

import os
import sys

# Ensure backend is on sys.path for bare ``uvicorn main:app``
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
    )
