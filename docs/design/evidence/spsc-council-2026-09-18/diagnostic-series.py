import argparse,json,re,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('phase',choices=['e4','e5','e6','e7','e8']);p.add_argument('--first',type=int,default=1);p.add_argument('--last',type=int,default=5);a=p.parse_args()
root=Path('/Users/sz/Desktop/vos/vos 5/docs/design/evidence/spsc-council-2026-09-18/runs')
base='/private/tmp/vos-sysinfo-qualified-20260918/source'
configs={
'e4':[('control',base,'final-full.iso',''),('yield','/private/tmp/vos-council-e4-yield-20260918/source','final-full.iso','E4-consumer-yield')],
'e5':[('512','/private/tmp/vos-council-e5-512-20260918/source','final-full.iso','E5-capacity-512'),('2048','/private/tmp/vos-council-e5-2048-20260918/source','final-full.iso','E5-capacity-2048')],
'e6':[('early','/private/tmp/vos-council-e6-20260918/source','e6-v2.iso','E6-early-health-instrumented')],
'e7':[('control',base,'final-full.iso',''),('instrument','/private/tmp/vos-council-instrument-20260918/source','e7-v2.iso','E7-scheduling-instrumented')],
'e8':[('phases','/private/tmp/vos-council-e8-20260918/source','final-full.iso','E8-getpid-distributions-phases')]
}
for i in range(a.first,a.last+1):
 for label,source,iso,diagnostic in configs[a.phase]:
  out=root/f'{a.phase}-{label}-{i}'
  cmd=[sys.executable,'/private/tmp/vos-council-run.py','--iso',source+'/dist/'+iso,'--source',source,'--out',str(out),'--seconds','500']
  if diagnostic:cmd+=['--diagnostic',diagnostic]
  r=subprocess.run(cmd)
  if r.returncode:raise SystemExit(r.returncode)
  result=json.loads((out/'result.json').read_text());raw=(out/'serial.log').read_bytes().decode(errors='replace').replace('\r','').replace('\0','')
  fails=re.findall(r'\[FAIL\][^\n]*',raw)
  if result['exit_status']!='suite_observed' or len(result['exits'])!=len(result['expected_programs']) or any(name!='test_health_check' and code!='0' for name,code in result['exits']) or any(not re.match(r'\[FAIL\]\s+spsc_responsive:',line) for line in fails):
   raise SystemExit('Unexpected/incomplete result: stop before another measurement; preserve and review '+str(out))
  if result['iso_sha256']!=result['iso_sha256_after']:raise SystemExit('ISO changed: stop')
  if a.phase in ['e6','e7'] and diagnostic:
   if 'SPSC_DIAG control arm=0 stop=0 dump=0' not in raw:raise SystemExit('Recorder control failed; stop')
   if not re.search(r'events=\d+ lost=0 census=\d+ census_lost=0',raw):raise SystemExit('Recorder overflow/incomplete; stop')
  if a.phase=='e8':
   if raw.count('[COUNCIL-E8-K] n=10000 dropped=0')!=2 or len(re.findall(r'^\[COUNCIL-E8-KR\] ',raw,re.M))!=20000:raise SystemExit('E8 sample count invalid; stop')
   if len(re.findall(r'^\[COUNCIL-E8-U\] ',raw,re.M))!=40000:raise SystemExit('E8 user sample count invalid; stop')
  before=json.loads((out/'host-before.json').read_text());after=json.loads((out/'host-after.json').read_text())
  if before['power']['stdout'].splitlines()[0]!=after['power']['stdout'].splitlines()[0]:raise SystemExit('Power change inside run; stop and stratify')
