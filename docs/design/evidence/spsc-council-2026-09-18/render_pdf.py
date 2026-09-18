from pathlib import Path
import json,os,signal,subprocess,tempfile,hashlib,shutil
from pypdf import PdfReader
repo=Path('/Users/sz/Desktop/vos/vos 5')
packet=repo/'docs/design/evidence/spsc-council-2026-09-18'
source=repo/'docs/reports/vos-performance-failure-2026-09-18-v1.1.html'
work=Path(tempfile.mkdtemp(prefix='vos-council-pdf-v11-',dir='/private/tmp'))
output=work/'report.pdf'; profile=work/'profile'
cmd=['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','--headless','--disable-gpu','--disable-background-networking','--disable-component-update','--no-first-run','--no-default-browser-check','--user-data-dir='+str(profile),'--no-pdf-header-footer','--print-to-pdf='+str(output),source.as_uri()]
status=None
with (work/'renderer.log').open('xb') as log:
 proc=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 try: status=proc.wait(timeout=25)
 except subprocess.TimeoutExpired:
  status='timeout_after_25s'
  os.killpg(proc.pid,signal.SIGTERM)
  try:proc.wait(timeout=5)
  except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
assert output.exists(),str(work/'renderer.log')
pdf=PdfReader(output);text='\n'.join(p.extract_text() for p in pdf.pages)
assert len(pdf.pages)>=6,len(pdf.pages)
ids=[f'{c}{i}' for c,n in [('T',5),('S',7),('K',6),('Q',6),('H',6),('M',6),('V',6),('A',6),('G',6)] for i in range(1,n+1)]
missing=[i for i in ids if '['+i+']' not in text]
assert not missing,missing
for value in ['80,000','41,054','99,450','102,400','42,409','0324521a2494b25c29a01b055ff8b38029819fb9']:
 assert value in text,value
out=source.with_suffix('.pdf');shutil.copy2(output,out)
record={'work':str(work),'command':cmd,'renderer_exit':status,'pdf':str(out),'pages':len(pdf.pages),'questions':len(ids),'html_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'pdf_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'log':str(work/'renderer.log'),'text_checks':'54 IDs and key data present; visual review pending'}
(packet/'pdf-render.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
(packet/'pdf-render.log').write_bytes((work/'renderer.log').read_bytes())
print(json.dumps(record,ensure_ascii=False))
