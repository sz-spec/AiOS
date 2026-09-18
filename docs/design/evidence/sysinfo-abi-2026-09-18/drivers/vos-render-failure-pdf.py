from pathlib import Path
import os,signal,subprocess,time
from pypdf import PdfReader
repo=Path('/Users/sz/Desktop/vos/vos 5')
profile='/private/tmp/vos-failure-pdf-chrome-20260918'
# Shut down only the renderer spawned for the first PDF attempt, if still owned.
p=subprocess.run(['ps','-p','27169','-o','command='],capture_output=True,text=True)
if p.returncode==0 and '--user-data-dir='+profile in p.stdout:
    os.kill(27169,signal.SIGTERM)
output=Path('/private/tmp/vos-performance-failure-final-20260918.pdf')
cmd=['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--headless','--disable-gpu','--disable-background-networking','--disable-component-update','--no-first-run','--no-default-browser-check','--user-data-dir=/private/tmp/vos-failure-pdf-chrome-final-20260918','--no-pdf-header-footer','--print-to-pdf='+str(output),(repo/'docs/reports/vos-performance-failure-2026-09-18.html').as_uri()]
with Path('/private/tmp/vos-failure-pdf-final-render.log').open('wb') as log:
    proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid,signal.SIGTERM)
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
assert len(PdfReader(output).pages)==3
os.replace(output,repo/'docs/reports/vos-performance-failure-2026-09-18.pdf')
print('Published verified three-page PDF; owned renderer closed.')
