from pathlib import Path
import hashlib,json,re,shutil,subprocess
REPO=Path('/Users/sz/Desktop/vos/vos 5');BASE=Path('/private/tmp/vos-sysinfo-qualified-20260918')
E=REPO/'docs/design/evidence/sysinfo-abi-2026-09-18';E.mkdir(exist_ok=True)
def copy(src,dst):
    dst=E/dst;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
for name in ['source-manifest.json','dirty.patch','build.log','host-tests.log','musl-symbols.txt','independent-review.json','runtime-review.json','reconstruction-review.json','failure-report-data.json']:
    copy(BASE/name,'final/'+name)
for p in (BASE/'frozen-additions').rglob('*'):
    if p.is_file():copy(p,p.relative_to(BASE))
for case in ['bios','uefi']:
    for name in ['result.json','serial.log','host-timing.json','invocation.json']:
        p=BASE/case/name
        if p.exists():copy(p,'runs/'+case+'/'+name)
    assert (E/'runs'/case/'result.json').exists()
    copy(BASE/(case+'-runner.log'),'runs/'+case+'/runner.log')
control=Path('/private/tmp/vos-sysinfo-control-20260918')
for name in ['result.json','serial.log','host-timing.json']:
    copy(control/name,'runs/old-iso-control/'+name)
for label,folder in [('first-build','/private/tmp/vos-sysinfo-20260918'),('before-caller-review','/private/tmp/vos-sysinfo-final-20260918')]:
    folder=Path(folder)
    for name in ['source-manifest.json','dirty.patch','build.log','build-approved.log','host-tests.log','focused-tests.log']:
        p=folder/name
        if p.exists():copy(p,'intermediate/'+label+'/'+name)
    if (folder/'bios').exists():
        for name in ['result.json','serial.log','host-timing.json']:copy(folder/'bios'/name,'intermediate/'+label+'/bios/'+name)
for name in ['vos-health-observe.py','vos-final-uefi-runner.py','vos-create-failure-report.py','vos-render-failure-pdf.py','vos-preserve-sysinfo.py']:
    copy(Path('/private/tmp')/name,'drivers/'+name)
assert re.search(r'Ran 110 tests in [0-9.]+s\s+OK', (BASE/'host-tests.log').read_text())
m=json.loads((BASE/'source-manifest.json').read_text())
assert all(digest(BASE/'source'/e['path'])==e['sha256'] for e in m['files'])
assert all(digest(REPO/e['path'])==e['sha256'] for e in m['files'])
artifacts={name:digest(BASE/'source'/name) for name in ['dist/final-full.iso','kernel/build/native-full-final/vos3.elf','kernel/build/native-full-final/user/bin/test_posix_core','kernel/build/native-full-final/user/bin/test_env_musl','kernel/build/native-full-final/user/bin/test_health_check','kernel/build/native-full-final/user/musl/lib/libc.so']}
results={}
for name in ['bios','uefi','old-iso-control']:
    d=json.loads((E/'runs'/name/'result.json').read_text());s=(E/'runs'/name/'serial.log').read_text(errors='replace').replace('\x00','').replace('\r','')
    exits=re.findall(r'^=== ([^\n:]+): exit=(-?\d+) ===',s,re.M);assert len(exits)==57 and len({n for n,c in exits})==57
    h=s[s.index('--- Check 4: spsc_responsive ---'):]
    nums={k:int(re.search(r'^\s*'+k+r':\s*(\d+)',h,re.M).group(1)) for k in ['pushed','consumed','elapsed','throughput']}
    assert nums['pushed']==nums['consumed'] and nums['throughput']==nums['consumed']*1000//nums['elapsed']
    results[name]={'full_suite_passed':d['passed'],'zero_exits':sum(c=='0' for n,c in exits),'exits':exits,'health':nums,'iso_sha256':d['iso_sha256']}
    if name!='old-iso-control':
        results[name]['sysinfo_native_passed']='[PASS] sysinfo_versioned_abi' in s and ('test_posix_core','0') in exits
        results[name]['musl_errno_passed']='[PASS] test_env: sysinfo_errno_no_write' in s and ('test_env_musl','0') in exits
        assert results[name]['sysinfo_native_passed'] and results[name]['musl_errno_passed']
        assert d['iso_sha256']==artifacts['dist/final-full.iso']
summary={'base_commit':m['commit'],'source_patch_sha256':m['dirty_diff_sha256'],'source_files':len(m['files']),'sources_unchanged':True,'build_exit':0,'host_tests':110,'host_tests_passed':True,'full_suite_passed':all(results[n]['full_suite_passed'] for n in ['bios','uefi']),'sysinfo_checks_passed':True,'artifacts_sha256':artifacts,'runs':results,'builder':'sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6','build_command':['make','native','-j4','BENCH_MODE=1','HEADLESS_AUDIT=1','BUILD_DIR=build/native-full-final','INSTALLER_ISO=../dist/final-full.iso'],'build_environment':{'SOURCE_DATE_EPOCH':1700000000,'BUILD_JOBS':4,'container_path':'/vos3','network':'none','capabilities':'ALL dropped','no_new_privileges':True},'scope':'Single-vCPU QEMU TCG, 3072 MiB. SYSINFO security repair validated; performance gate remains open. No physical-hardware or full-SMP qualification.'}
(E/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
paths=sorted(p for p in E.rglob('*') if p.is_file() and p.name!='SHA256SUMS')
(E/'SHA256SUMS').write_text(''.join(digest(p)+'  '+str(p.relative_to(E))+'\n' for p in paths))
pub=REPO/'dist/qualification-sysinfo-20260918';pub.mkdir(exist_ok=True)
shutil.copy2(BASE/'source/dist/final-full.iso',pub/'vos5-sysinfo-validation.iso');(pub/'artifacts.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({'evidence_files':len(paths),'full_suite_passed':summary['full_suite_passed'],'sysinfo_checks_passed':True,'runs':{k:{'rate':v['health']['throughput'],'passed':v['zero_exits']} for k,v in results.items()}},indent=2))
