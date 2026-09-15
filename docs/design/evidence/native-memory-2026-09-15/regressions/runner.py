import subprocess, json, hashlib, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

repo=Path('/Users/sz/Desktop/vos/vos 5')
work=Path('/private/tmp/vos5-memory-dev-20260914/source')
out=Path('/private/tmp/vos5-memory-regressions-20260915');out.mkdir(exist_ok=False)
image='sha256:3a76dfcadbe157be8757d4f0655c53b2fc717fbe3720b457f474027d61ee32c6'
results={'passed':False,'scope':'regression builds in separate output directories; existing bootloader reused','builder':image,'builds':[],'boots':[]}
try:
    for flavor in ('normal','isolation'):
        name='vos5-memory-regression-'+flavor
        make=['make','native','-j4','BUILD_DIR=build/memory-regression-'+flavor,
              'INSTALLER_ISO=../dist/memory-regression-'+flavor+'.iso',
              'HEADLESS_AUDIT='+('1' if flavor=='isolation' else '0'),
              'NATIVE_ISOLATION_TEST='+('1' if flavor=='isolation' else '0')]
        cmd=['docker','run','--rm','--name',name,'--label','vos.memory-regression='+name,
             '--network','none','--cap-drop','ALL','--security-opt','no-new-privileges:true',
             '--mount','type=bind,src='+str(work)+',dst=/vos3','-w','/vos3',
             '-e','SOURCE_DATE_EPOCH=1700000000','-e','BUILD_JOBS=4',image,*make]
        with (out/(flavor+'-build.txt')).open('w') as log:
            proc=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=1200)
        assert proc.returncode==0, flavor+' build failed'
        iso=work/('dist/memory-regression-'+flavor+'.iso');iso.chmod(0o444)
        results['builds'].append({'flavor':flavor,'command':make,'exit':proc.returncode,
                                 'iso_sha256':hashlib.sha256(iso.read_bytes()).hexdigest(),
                                 'kernel_sha256':hashlib.sha256((work/('kernel/build/memory-regression-'+flavor+'/vos3.elf')).read_bytes()).hexdigest()})
        print(flavor+' built',flush=True)
    deadline=time.monotonic()+1200
    while not Path('/private/tmp/vos5-memory-clean-20260915/result.json').exists():
        assert time.monotonic()<deadline,'primary matrix wait timed out'
        time.sleep(1)
    def boot(case):
        flavor,firmware,cpus=case;dest=out/(flavor+'-'+firmware+str(cpus))
        script='native_isolation_smoke.py' if flavor=='isolation' else 'native_boot_smoke.py'
        cmd=['python3',str(repo/'scripts'/script),'--iso',str(work/('dist/memory-regression-'+flavor+'.iso')),
             '--output',str(dest),'--smp',str(cpus),'--seconds','35']
        if firmware=='uefi':cmd+=['--firmware-code','/opt/homebrew/share/qemu/edk2-x86_64-code.fd','--firmware-vars','/opt/homebrew/share/qemu/edk2-i386-vars.fd']
        with (out/(dest.name+'-runner.txt')).open('w') as log:
            run=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=65)
        result=json.loads((dest/'result.json').read_text());result['flavor']=flavor
        assert run.returncode==0 and result['passed'],dest.name
        assert result['iso_sha256']==next(b['iso_sha256'] for b in results['builds'] if b['flavor']==flavor)
        print(dest.name+' passed',flush=True)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results['boots']=list(pool.map(boot,[(f,w,c) for f in ('normal','isolation') for w in ('bios','uefi') for c in (1,4)]))
    results['passed']=True
except Exception as error:
    results['error']=type(error).__name__+': '+str(error)
finally:
    for flavor in ('normal','isolation'):
        name='vos5-memory-regression-'+flavor
        query=['docker','container','ls','--all','--filter','name=^/'+name+'$','--format','{{.ID}}']
        try:
            if subprocess.check_output(query,text=True,timeout=20).strip():
                owner=subprocess.check_output(['docker','inspect','--format','{{index .Config.Labels "vos.memory-regression"}}',name],text=True,timeout=20).strip()
                assert owner==name
                subprocess.run(['docker','rm','-f',name],check=True,capture_output=True,timeout=20)
            assert not subprocess.check_output(query,text=True,timeout=20).strip()
        except Exception as error:
            results['passed']=False;results['cleanup_error']=str(error)
    (out/'result.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps({'passed':results['passed'],'result':str(out/'result.json')}),flush=True)
raise SystemExit(0 if results['passed'] else 1)
