import subprocess,json,hashlib,tarfile,io
from pathlib import Path
root=Path('/Users/sz/Desktop/vos/vos 5');out=Path('/private/tmp/vos5-smp-final');out.mkdir(exist_ok=False);src=out/'source';src.mkdir()
tracked=subprocess.check_output(['git','ls-files','-z'],cwd=root).split(b'\0');untracked=subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z'],cwd=root).split(b'\0')
files=[]
for raw in tracked+untracked:
 if not raw:continue
 p=root/raw.decode();rel=p.relative_to(root)
 if not p.is_file():continue
 dest=src/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(p.read_bytes());dest.chmod(p.stat().st_mode&0o777)
 files.append({'path':str(rel),'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
diff=subprocess.check_output(['git','diff','HEAD','--binary'],cwd=root);(out/'dirty.patch').write_bytes(diff)
manifest={'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'dirty_diff_sha256':hashlib.sha256(diff).hexdigest(),'files':files};(out/'source-manifest.json').write_text(json.dumps(manifest,indent=2))

import time
started=time.monotonic()
image='sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6'
iso=src/'dist/vos5-smp.iso'
cmd=['docker','run','--rm','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges:true','--mount','type=bind,src='+str(src)+',dst=/vos3','-w','/vos3','-e','SOURCE_DATE_EPOCH=1700000000','-e','BUILD_JOBS=4',image,'make','native','-j4','HEADLESS_AUDIT=1','NATIVE_SMP_WORKLOAD=1','NATIVE_SMP_TEST=1','BUILD_DIR=build/native-smp-final','INSTALLER_ISO=../dist/vos5-smp.iso']
result={'passed':False,'builder':image,'command':cmd,'commit':manifest['commit'],'dirty_diff_sha256':manifest['dirty_diff_sha256'],'scope':'diagnostic SMP only','cases':{}}
try:
 with (out/'build.log').open('wb') as log:r=subprocess.run(cmd,cwd=src,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 result['build_exit']=r.returncode
 if r.returncode:raise RuntimeError('build failed')
 for key,path in [('iso',iso),('kernel',src/'kernel/build/native-smp-final/vos3.elf'),('manifest',out/'source-manifest.json')]:result[key+'_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
 for firmware in ('bios','uefi'):
  for cpus in (1,4):
   case=firmware+str(cpus);cmd=['python3','scripts/native_smp_smoke.py','--iso',str(iso),'--output',str(out/case),'--smp',str(cpus),'--seconds','45']
   if firmware=='uefi':cmd+=['--firmware-code','/opt/homebrew/share/qemu/edk2-x86_64-code.fd','--firmware-vars','/opt/homebrew/share/qemu/edk2-i386-vars.fd']
   with (out/(case+'-runner.log')).open('wb') as log:r=subprocess.run(cmd,cwd=src,stdout=log,stderr=subprocess.STDOUT,timeout=80)
   result['cases'][case]={'exit':r.returncode,'evidence':json.loads((out/case/'result.json').read_text())};print(case,r.returncode,flush=True)
 result['passed']=all(c['exit']==0 and c['evidence']['passed'] for c in result['cases'].values())
except Exception as e:result['error']=repr(e)
finally:
 changes=[f['path'] for f in files if not (src/f['path']).exists() or hashlib.sha256((src/f['path']).read_bytes()).hexdigest()!=f['sha256']]
 result['source_unchanged']=not changes;result['source_changes']=changes;result['elapsed_seconds']=time.monotonic()-started
 (out/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='cases'}),flush=True)
