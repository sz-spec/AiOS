import json,hashlib,shutil,subprocess,time
from pathlib import Path
out=Path('/private/tmp/vos-factorial-d-oldkernel-newuser-20260917');out.mkdir(exist_ok=False);src=out/'source';src.mkdir()
old=Path('/private/tmp/vos-goal-final-native-20260917');new=Path('/private/tmp/vos-final2-repro-20260917/0/source')
m=json.loads((old/'source-manifest.json').read_text());files={f['path']:f['sha256'] for f in m['files']};overrides=['user/src/test_npu_direct.c','user/src/bench_2026_frontier.c']
for name,digest in files.items():
 p=old/'source'/name;assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
 q=src/name;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
for name in overrides:shutil.copy2(new/name,src/name)
actual={name:hashlib.sha256((src/name).read_bytes()).hexdigest() for name in files};changed=[n for n in files if files[n]!=actual[n]];assert set(changed)==set(overrides),changed
(out/'source-manifest.json').write_text(json.dumps({'old_commit':m['commit'],'old_dirty_patch':m['dirty_diff_sha256'],'old_manifest_sha256':hashlib.sha256((old/'source-manifest.json').read_bytes()).hexdigest(),'overrides':overrides,'files':actual},indent=2))
image='sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6'
cmd=['docker','run','--rm','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges:true','--mount','type=bind,src='+str(src)+',dst=/vos3','-w','/vos3','-e','SOURCE_DATE_EPOCH=1700000000','-e','BUILD_JOBS=4',image,'make','native','-j4','BENCH_MODE=1','HEADLESS_AUDIT=1','BUILD_DIR=build/native-full-final','INSTALLER_ISO=../dist/final-full.iso']
r={'scope':'factorial D old kernel and all old source except two new user programs','builder':image,'build_command':cmd,'changed_inputs':changed,'passed':False};t=time.monotonic()
try:
 with (out/'build.log').open('wb') as log:p=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
 r['build_exit']=p.returncode;assert p.returncode==0
 iso=src/'dist/final-full.iso';r['iso_sha256']=hashlib.sha256(iso.read_bytes()).hexdigest();r['kernel_sha256']=hashlib.sha256((src/'kernel/build/native-full-final/vos3.elf').read_bytes()).hexdigest()
 (out/'build-ready').write_text('Waiting for isolated-run authorization from coordinator\n');print('BUILD_READY',flush=True)
 while not (out/'run-go').exists():time.sleep(1)
 start=time.monotonic()
 with (out/'runner.log').open('wb') as log:p=subprocess.run(['python3','scripts/native_bench.py','--iso',str(iso),'--output',str(out/'run'),'--seconds','900'],cwd=src,stdout=log,stderr=subprocess.STDOUT,timeout=940)
 r['runner_exit']=p.returncode;r['run_elapsed_seconds']=time.monotonic()-start;r['guest']=json.loads((out/'run/result.json').read_text());r['passed']=r['guest']['passed']
except Exception as e:r['error']=repr(e)
finally:
 r['source_changes']=[n for n,h in actual.items() if hashlib.sha256((src/n).read_bytes()).hexdigest()!=h];r['source_unchanged']=not r['source_changes'];r['elapsed_seconds']=time.monotonic()-t;(out/'result.json').write_text(json.dumps(r,indent=2));print('DONE',r.get('runner_exit'),flush=True)
