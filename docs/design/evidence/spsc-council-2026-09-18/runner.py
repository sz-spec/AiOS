#!/usr/bin/env python3
"""Exclusive, append-only native observation; no builds or other agent work."""
import argparse,datetime,hashlib,importlib.util,json,os,re,selectors,shutil,signal,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--iso',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--firmware',choices=['bios','uefi'],default='bios');p.add_argument('--icount',type=int);p.add_argument('--seconds',type=int,default=500);p.add_argument('--diagnostic',default='');p.add_argument('--test-only',default='');a=p.parse_args()
a.out.mkdir(parents=True,exist_ok=False)
q=Path(shutil.which('qemu-system-x86_64')).resolve()
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def command(cmd):
 try:
  r=subprocess.run(cmd,capture_output=True,text=True,timeout=10);return {'command':cmd,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
 except Exception as e:return {'command':cmd,'error':str(e)}
def snapshot():
 return {'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'loadavg':os.getloadavg(),'processes':command(['/bin/ps','-axo','pid,ppid,etime,pcpu,comm']),'power':command(['/usr/bin/pmset','-g','batt']),'power_settings':command(['/usr/bin/pmset','-g','custom']),'thermal':command(['/usr/bin/pmset','-g','therm']),'host':{k:command(['/usr/sbin/sysctl','-n',k]) for k in ['machdep.cpu.brand_string','hw.model','hw.ncpu','kern.boottime']},'os':command(['/usr/bin/sw_vers']),'qemu_version':command([str(q),'--version']),'qemu_sha256':sha(q),'qemu_path':str(q),'iso_sha256':sha(a.iso),'host_work_rule':'All agents idle; only this observer/required telemetry. No claim unrelated user services stopped.'}
def save(name,data):(a.out/name).write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
pre=snapshot();save('host-before.json',pre)
if pre['processes'].get('exit')!=0:raise SystemExit('Process preflight unavailable; no VM launched')
others=[x for x in pre['processes']['stdout'].splitlines() if re.search(r'/(qemu-system-|qemu-system_)',x)]
if others:raise SystemExit('Another QEMU exists; no VM launched: '+repr(others))
cmd=[str(q),'-machine','q35,accel=tcg','-m','3072','-cpu','qemu64,+rdrand,+rdseed','-smp','1','-display','none','-serial','stdio','-monitor','none','-no-reboot','-boot','d','-nic','user,model=virtio-net-pci','-cdrom',str(a.iso),'-snapshot']
if a.icount is not None:cmd+=['-icount',f'shift={a.icount},sleep=off']
if a.firmware=='uefi':
 firmware=Path('/opt/homebrew/share/qemu/edk2-x86_64-code.fd');template=Path('/opt/homebrew/share/qemu/edk2-i386-vars.fd');v=a.out/'OVMF_VARS.fd';shutil.copy2(template,v)
 cmd+=['-drive',f'if=pflash,format=raw,readonly=on,file={firmware}','-drive',f'if=pflash,format=raw,file={v}'];save('firmware.json',{'code_sha256':sha(firmware),'vars_template_sha256':sha(template)})
save('invocation.json',{'command':cmd,'source':str(a.source),'diagnostic':a.diagnostic,'test_only':a.test_only,'seconds_limit':a.seconds,'canonical_gate_eligible':not a.diagnostic and a.icount is None and not a.test_only,'observer_sha256':sha(__file__)})
spec=importlib.util.spec_from_file_location('native_bench',a.source/'scripts/native_bench.py');bench=importlib.util.module_from_spec(spec);spec.loader.exec_module(bench)
expected=bench.workload((a.source/'user/src/init.c').read_text(),a.test_only)
start=time.monotonic();status='timeout';events=[];buf=b'';complete=None
print('START '+a.out.name,flush=True)
with (a.out/'serial.log').open('xb') as log:
 proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True);sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ)
 try:
  while time.monotonic()-start<a.seconds:
   for key,_ in sel.select(timeout=0.5):
    block=os.read(key.fd,65536)
    if not block:sel.unregister(key.fileobj);continue
    log.write(block);log.flush();buf+=block
    while b'\n' in buf:
     line,buf=buf.split(b'\n',1);s=line.decode(errors='replace').replace('\r','').replace('\0','');now=time.monotonic()-start
     if s.startswith(('=== Running:','=== test_health_check:','--- Check 4:','  throughput:','[COUNCIL','[PERF] Null syscall','  Filled ')):
      events.append({'host_seconds':now,'line':s})
      if s.startswith(('=== Running:','  throughput:','[COUNCIL')):print(f'{now:.3f} {s}',flush=True)
     if bench.HALT in s and complete is None:complete=time.monotonic()
   if proc.poll() is not None:status=proc.returncode;break
   if complete is not None and time.monotonic()-complete>=2:status='suite_observed';break
 finally:
  if proc.poll() is None:
   os.killpg(proc.pid,signal.SIGTERM)
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
  tail=proc.stdout.read();log.write(tail);sel.close()
elapsed=time.monotonic()-start
post=snapshot();save('host-after.json',post);save('host-timing.json',{'elapsed_seconds':elapsed,'events':events,'scope':'Host serial receipt timestamps; not exact guest window boundaries'})
raw=(a.out/'serial.log').read_bytes().decode(errors='replace').replace('\r','').replace('\0','');result=bench.classify_serial(raw,expected,status,a.test_only)
result.update({'exit_status':status,'elapsed_seconds':elapsed,'command':cmd,'iso_sha256':pre['iso_sha256'],'iso_sha256_after':post['iso_sha256'],'diagnostic':a.diagnostic,'icount_shift':a.icount,'serial_sha256':sha(a.out/'serial.log'),'spsc_rates':[int(n) for n in re.findall(r'^  throughput:\s+(\d+) msgs/sec',raw,re.M)],'compute_fill':re.findall(r'Filled (\d+) MB in (\d+) cycles',raw),'getpid':re.findall(r'Null syscall \(getpid\): min=(\d+) avg=(\d+) max=(\d+)',raw)})
save('result.json',result)
with (a.out/'SHA256SUMS').open('x') as f:
 for file in sorted(a.out.iterdir()):
  if file.is_file() and file.name!='SHA256SUMS':f.write(f'{sha(file)}  {file.name}\n')
print(json.dumps({k:result[k] for k in ['passed','exit_status','elapsed_seconds','spsc_rates','compute_fill','getpid','failures']}),flush=True)
