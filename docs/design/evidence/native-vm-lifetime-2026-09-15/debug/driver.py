from pathlib import Path
import subprocess,socket,json,time
out=Path('/private/tmp/vos5-lifetime-debug');out.mkdir(exist_ok=True)
sock=str(out/'qmp.sock')
cmd=['qemu-system-x86_64','-machine','q35,accel=tcg','-cpu','qemu64','-smp','4','-m','1024','-display','none','-serial','stdio','-monitor','none','-nic','none','-no-reboot','-qmp','unix:'+sock+',server=on,wait=off','-cdrom','/private/tmp/vos5-lifetime-memory-20260915/source/dist/vos5-isolation.iso']
with (out/'serial.log').open('wb') as log:
 p=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
 try:
  for _ in range(100):
   if Path(sock).exists():break
   time.sleep(.05)
  with socket.socket(socket.AF_UNIX) as conn:
   conn.settimeout(3);conn.connect(sock)
   with conn.makefile('rwb',buffering=0) as control:
    control.readline()
    def rpc(name,args=None):
     packet={'execute':name}
     if args is not None:packet['arguments']=args
     control.write((json.dumps(packet)+'\n').encode())
     while True:
      r=json.loads(control.readline())
      if 'return' in r:return r['return']
      if 'error' in r:raise RuntimeError(r)
    rpc('qmp_capabilities')
    for elapsed in range(60):
     time.sleep(1)
     if elapsed in (19,39,59):
      rpc('stop')
      result={'elapsed':elapsed+1,'cpus':rpc('query-cpus-fast')}
      captures=[]
      for cpu in range(4):
       rpc('human-monitor-command',{'command-line':'cpu '+str(cpu)})
       regs=rpc('human-monitor-command',{'command-line':'info registers'})
       captures.append({'cpu':cpu,'registers':regs})
      result['captures']=captures
      (out/('capture-'+str(elapsed+1)+'.json')).write_text(json.dumps(result,indent=2))
      print(json.dumps({'captured':elapsed+1}),flush=True)
      rpc('cont')
    rpc('quit')
 finally:
  if p.poll() is None:p.terminate()
  p.wait(timeout=5)
