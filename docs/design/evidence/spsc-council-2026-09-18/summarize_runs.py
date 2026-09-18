#!/usr/bin/env python3
"""Read preserved packets only; never run guests or alter raw evidence."""
import argparse, hashlib, json, re, statistics
from pathlib import Path

def load(p):
    if not p.exists(): return {}
    return json.loads(p.read_text())
def power(d):
    m=re.search(r"Now drawing from '(AC|Battery) Power'",d.get('power',{}).get('stdout',''))
    return m.group(1) if m else 'unknown'
def profiles(d):
    # Preserve all pmset custom profile keys, not only current source/powermode.
    result={}; section=None
    for line in d.get('power_settings',{}).get('stdout','').splitlines():
        if line.endswith(':'):
            section=line[:-1].strip(); result[section]={}
        elif section and line.strip():
            fields=line.strip().split(None,1)
            if len(fields)==2: result[section][fields[0]]=fields[1]
    return result

def stats(v):
    return {'n':len(v),'values':v,'min':min(v) if v else None,'median':statistics.median(v) if v else None,'max':max(v) if v else None}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--packet',type=Path,default=Path(__file__).resolve().parent);a=ap.parse_args()
    rows=[];errors=[];groups={}
    for f in sorted((a.packet/'runs').glob('*/result.json')):
        try:
            p=f.parent;r=load(f);before=load(p/'host-before.json');after=load(p/'host-after.json');inv=load(p/'invocation.json')
            raw=(p/'serial.log').read_bytes();s=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',raw.decode('utf-8',errors='replace'))
            health=[dict(zip(('pushed','consumed','elapsed_ms','rate'),map(int,m))) for m in re.findall(r'  pushed:\s*(\d+)\s+consumed:\s*(\d+)\s+elapsed:\s*(\d+) ms\s+throughput:\s*(\d+) msgs/sec',s)]
            for h in health:h['floor_math_matches']=h['elapsed_ms']>0 and h['rate']==h['consumed']*1000//h['elapsed_ms']
            gp=[dict(zip(('min','avg','max'),map(int,m))) for m in re.findall(r'Null syscall \(getpid\): min=(\d+) avg=(\d+) max=(\d+)',s)]
            compute=list(map(int,re.findall(r'Filled 64 MB in (\d+) cycles',s)))
            pb,pa=power(before),power(after);pc='mixed:'+pb+'->'+pa if pb!=pa and 'unknown' not in (pb,pa) else (pb if pb==pa else 'unknown')
            name=re.sub(r'-\d+$','',p.name)
            prb,pra=profiles(before),profiles(after)
            profile_changed=prb!=pra
            profile_changes=[{'profile':q,'key':k,'before':prb.get(q,{}).get(k),'after':pra.get(q,{}).get(k)} for q in sorted(set(prb)|set(pra)) for k in sorted(set(prb.get(q,{}))|set(pra.get(q,{}))) if prb.get(q,{}).get(k)!=pra.get(q,{}).get(k)]
            iso=r.get('iso_sha256');qa=before.get('qemu_sha256');qb=after.get('qemu_sha256')
            row={'run':p.name,'series':name,'power_before':pb,'power_after':pa,'power_class':pc,'power_settings_changed':profile_changed,'power_profiles_before':prb,'power_profiles_after':pra,'power_profile_changes':profile_changes,'passed':r.get('passed'),'failures':r.get('failures',[]),'health':health,'getpid_tsc':gp,'compute_fill_tsc':compute,'iso_sha256':iso,'iso_sha256_after':r.get('iso_sha256_after'),'qemu_sha256_before':qa,'qemu_sha256_after':qb,'iso_unchanged':iso is not None and iso==r.get('iso_sha256_after'),'qemu_unchanged':qa is not None and qa==qb,'serial_sha256':hashlib.sha256(raw).hexdigest(),'serial_hash_matches':hashlib.sha256(raw).hexdigest()==r.get('serial_sha256'),'diagnostic':inv.get('diagnostic'),'command':inv.get('command'),'health_sample_valid':len(health)==1 and health[0]['floor_math_matches']}
            rows.append(row)
            # Names explicitly identify experiment and variant; also segregate artifact identities.
            key=json.dumps([name,pc,iso,qa,qb,prb,pra],ensure_ascii=False)
            g=groups.setdefault(key,{'series':name,'power':pc,'power_settings_changed':profile_changed,'power_profiles_before':prb,'power_profiles_after':pra,'power_profile_changes':profile_changes,'iso_sha256':iso,'qemu_sha256_before':qa,'qemu_sha256_after':qb,'runs':[],'rates':[],'compute':[],'getpid_avg':[]})
            g['runs'].append(p.name)
            if row['health_sample_valid']:g['rates'].append(health[0]['rate'])
            if len(compute)==1:g['compute'].append(compute[0])
            if len(gp)==1:g['getpid_avg'].append(gp[0]['avg'])
        except (OSError,ValueError,KeyError,TypeError) as e:errors.append({'file':str(f),'error':str(e)})
    for g in groups.values():
        for k in ('rates','compute','getpid_avg'):g[k]=stats(g[k])
        g['n_packets']=len(g['runs']);g['conclusions_allowed']=False
        g['status']='פחות מחמש דגימות: תיאור בלבד' if g['rates']['n']<5 else 'חמש ומעלה: תיאור בלבד; לא מבחן סיבתי או שקילות'
    out={'schema':1,'method':'Preserved result/serial/host/invocation only; endpoint power observations, not continuous monitoring. Missing/duplicate health is not zero throughput. Groups retain explicit series, artifact identities and complete parsed before/after power profiles; any profile value change separates groups. No n<5 conclusions.','runs':rows,'groups':list(groups.values()),'errors':errors}
    (a.packet/'measured-summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    md=['# סיכום מדידות שמורות','', 'נוצר באמצעות `summarize_runs.py`. כל הערכים והכשלים נשמרים; אין הסקה סיבתית ואין מסקנות מקבוצות n<5. סיווג מתח מבוסס שתי דגימות קצה בלבד, ואינו מבטיח יציבות לאורך הריצה. קבוצות mixed נפרדות; חסר SPSC אינו קצב אפס. כל פרופילי pmset לפני ואחרי נכללים בקיבוץ; שינוי בכל מפתח, גם displaysleep, מסומן ומופרד. זהויות ISO/QEMU מפורטות ב־JSON ומפרידות קבוצות.','', '| סדרה | מתח | n מדידות SPSC / חבילות | כל הקצבים | מינימום / חציון / מקסימום |','|---|---|---|---|---|']
    for g in groups.values():
        t=g['rates'];md.append(f"| {g['series']} | {g['power']} {'PROFILE_CHANGED' if g['power_settings_changed'] else ''} | {t['n']} / {g['n_packets']} | {t['values']} | {t['min']} / {t['median']} / {t['max']} |")
    md+=['','## כל הריצות','', '| ריצה | מתח לפני→אחרי | קצבים | pushed/consumed/ms | compute TSC | GETPID min/avg/max | תוצאת observer |','|---|---|---|---|---|---|---|']
    for r in rows:
        h=r['health'];md.append(f"| {r['run']} | {r['power_before']}→{r['power_after']} {'PROFILE_CHANGED' if r['power_settings_changed'] else ''} | {[x['rate'] for x in h]} | {[(x['pushed'],x['consumed'],x['elapsed_ms']) for x in h]} | {r['compute_fill_tsc']} | {r['getpid_tsc']} | {'PASS' if r['passed'] else 'FAIL'} |")
    md+=['','Compute ו־GETPID מוצגים ביחידות TSC שדווחו, לא במיקרושניות או במחזורי חומרה מכוילים. מינימום/חציון/מקסימום ורשימות מלאות לכל מדד זמינים ב־JSON. ספירת חבילות אינה בהכרח מספר דגימות ברות השוואה.','',f'שגיאות קריאה: {len(errors)}. פירוט ב־JSON. קבצים גולמיים לא שונו.']
    (a.packet/'measured-summary.md').write_text('\n'.join(md)+'\n');print(f'{len(rows)} packets, {len(groups)} groups, {len(errors)} errors')
if __name__=='__main__':main()
