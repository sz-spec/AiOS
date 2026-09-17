from pathlib import Path
import shutil,hashlib,json,subprocess,os
out=Path('/private/tmp/vos-factorial-e-oldnpu-newfrontier-20260917');out.mkdir(exist_ok=False)
new=Path('/private/tmp/vos-final2-repro-20260917/0/source');old=Path('/private/tmp/vos-goal-final-native-20260917/source');tree=out/'source'
shutil.copytree(new,tree,ignore=shutil.ignore_patterns('build','dist','.git','__pycache__'))
overrides=['user/src/test_npu_direct.c']
for n in overrides:shutil.copyfile(old/n,tree/n)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
records=[]
for p in tree.rglob('*'):
 if p.is_file():
  n=str(p.relative_to(tree));assert sha(p)==sha((old if n in overrides else new)/n);records.append({'path':n,'sha256':sha(p)})
for p in sorted(tree.rglob('*'),key=lambda p:len(p.parts),reverse=True):os.utime(p,(1700000000,1700000000))
os.utime(tree,(1700000000,1700000000))
(out/'source-manifest.json').write_text(json.dumps({'new_source':str(new),'old_source':str(old),'overrides':overrides,'files':records},indent=2)+'\n')
cmd=['docker','run','--rm','--name','vos-factorial-e-20260917','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges:true','--mount',f'type=bind,src={tree},dst=/vos3','-w','/vos3','-e','SOURCE_DATE_EPOCH=1700000000','-e','BUILD_JOBS=4','sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6','make','native','-j4','BENCH_MODE=1','HEADLESS_AUDIT=1','BUILD_DIR=build/native-full-final','INSTALLER_ISO=../dist/final-full.iso']
(out/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n')
