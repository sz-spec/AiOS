"""Observe the native suite with host-monotonic milestones (100ms polling)."""
import argparse, importlib.util, json, subprocess, sys, time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
spec=importlib.util.spec_from_file_location('native_bench',a.source/'scripts/native_bench.py');bench=importlib.util.module_from_spec(spec);spec.loader.exec_module(bench)
def observe(command,log_path,seconds):
 start=time.monotonic();events=[];status='timeout';consumed=0
 with log_path.open('wb') as log:
  proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT);deadline=start+seconds;completed=None
  try:
   while time.monotonic()<deadline:
    raw=log_path.read_text(errors='replace');now=time.monotonic();end=raw.rfind('\n')+1
    for line in raw[consumed:end].splitlines():
     clean=line.replace('\r','').replace('\x00','')
     if clean.startswith(('=== Running:','=== test_health_check:','[HEALTH-DIAG]','--- Check 4:','  throughput:')):events.append({'host_seconds':now-start,'line':clean})
    consumed=end
    if bench.HALT in raw and completed is None:completed=now
    if proc.poll() is not None:status=proc.returncode;break
    if completed is not None and now-completed>=2:status='suite_observed';break
    time.sleep(0.1)
  finally:
   stopped=proc.poll()
   if stopped is not None:status=stopped
   else:
    proc.terminate()
    try:proc.wait(timeout=5)
    except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
   (log_path.parent/'host-timing.json').write_text(json.dumps({'elapsed_seconds':time.monotonic()-start,'resolution':'100ms polling, not per-message arrival timestamps','events':events},indent=2)+'\n')
 return status
bench.observe=observe
sys.argv=['native_bench.py','--iso',str(a.source/'dist/final-full.iso'),'--output',str(a.output),'--seconds','500']
sys.exit(bench.main())
