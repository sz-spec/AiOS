import json,subprocess,sys
from pathlib import Path
root=Path('/Users/sz/Desktop/vos/vos 5/docs/design/evidence/spsc-council-2026-09-18/runs')
for i in range(2,6):
 for label,source in [('a','/private/tmp/vos-health-final-20260917/source'),('b','/private/tmp/vos-sysinfo-qualified-20260918/source')]:
  out=root/f'e12-{label}-{i}'
  r=subprocess.run([sys.executable,'/private/tmp/vos-council-run.py','--iso',source+'/dist/final-full.iso','--source',source,'--out',str(out)])
  if r.returncode:raise SystemExit(r.returncode)
  x=json.loads((out/'result.json').read_text())
  if len(x['exits'])!=57 or any(name!='test_health_check' and code!='0' for name,code in x['exits']):raise SystemExit('Unexpected result: stopped for review')
  if x['iso_sha256']!=x['iso_sha256_after']:raise SystemExit('ISO changed: stopped')
  for name in ['host-before.json','host-after.json']:
   host=json.loads((out/name).read_text())
   if "Now drawing from 'Battery Power'" not in host['power']['stdout']:raise SystemExit('Power-source change detected; stop and stratify conditions')
