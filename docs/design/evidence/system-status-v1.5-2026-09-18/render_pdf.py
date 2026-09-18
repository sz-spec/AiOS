from pathlib import Path
import os
import signal
import subprocess
import tempfile

from pypdf import PdfReader

repo = Path("/Users/sz/Desktop/vos/vos 5")
html = repo / "docs/reports/vos-system-unification-status-2026-09-18-v1.5.html"
pdf = repo / "docs/reports/vos-system-unification-status-2026-09-18-v1.5.pdf"
chrome = "/Users/sz/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-arm64/chrome-headless-shell"

with tempfile.TemporaryDirectory(prefix="vos-status-v15-chrome-", dir="/private/tmp") as profile:
    tmp_pdf = Path(profile) / "status-v1.5.pdf"
    cmd = [
        chrome,
        "--headless",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-component-update",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile}",
        "--no-pdf-header-footer",
        f"--print-to-pdf={tmp_pdf}",
        html.as_uri(),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        raise
    (repo / 'docs/design/evidence/system-status-v1.5-2026-09-18/render.log').write_bytes(output)
    if proc.returncode != 0:
        raise SystemExit(output.decode(errors="replace"))
    reader = PdfReader(tmp_pdf)
    if len(reader.pages) != 6:
        raise SystemExit(f"expected 6 pages, got {len(reader.pages)}")
    os.replace(tmp_pdf, pdf)

print(pdf)
print(f"pages={len(PdfReader(pdf).pages)} bytes={pdf.stat().st_size}")
