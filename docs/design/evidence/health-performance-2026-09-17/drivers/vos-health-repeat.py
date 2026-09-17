import json,subprocess,time
from pathlib import Path
base=Path('/private/tmp/vos-health-final-20260917');source=base/'source';results=[]
for name in ['uefi-1','bios-2','uefi-2']:
 out=base/name
 if name.startswith('uefi'):
  cmd=['python3','/private/tmp/vos-final-uefi-runner.py','--source',str(source),'--iso',str(source/'dist/final-full.iso'),'--output',str(out),'--seconds','500']
 else:
  cmd=['python3','/private/tmp/vos-health-observe.py','--source',str(source),'--output',str(out)]
 print('START',name,flush=True);start=time.monotonic()
 with (base/(name+'-runner.log')).open('wb') as log:
  run=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=530)
 result=json.loads((out/'result.json').read_text())
 results.append({'name':name,'command':cmd,'exit_code':run.returncode,'elapsed_seconds':time.monotonic()-start,'passed':result['passed']})
 (base/'repeat-summary.json').write_text(json.dumps(results,indent=2)+'\n')
 print('DONE',name,run.returncode,result['passed'],flush=True)
 if run.returncode or not result['passed']:raise SystemExit(1)
