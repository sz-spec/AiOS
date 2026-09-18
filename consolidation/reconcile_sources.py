"""Read-only reconciliation of all eleven donor worktrees and canonical VOS.

Produces provenance, local-change and content-coverage ledgers. A hash match
proves retained bytes, never runtime integration or behavioral equivalence.
"""
import ast
import re
import hashlib
import json
import os
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from inventory import ROOT, SOURCE_ROOT, SOURCES, excluded

OUT = ROOT / 'consolidation/reconciliation'
EXTRA_DIRS = {'.claude', '.codex', '.agents', 'logs', 'data', 'test_deploy',
              'htmlcov', 'coverage', '.cache', '.hypothesis', '.git_backup', 'autom4te.cache'}
ARTIFACTS = ('.o', '.a', '.so', '.so.debug', '.elf', '.iso', '.img', '.qcow2',
             '.vhdx', '.log', '.db', '.sqlite', '.sqlite3', '.tsbuildinfo', '.d')

def omission(path, directory=False):
    parts = Path(path).parts
    if any(excluded(p) and p not in {'.env.example', '.env.template'} for p in parts): return 'credential, dependency or standard generated exclusion'
    if any(p in EXTRA_DIRS or p.startswith(('build-', 'build_', '.git.')) for p in (parts if directory else parts[:-1])):
        return 'runtime/session/build directory'
    if any(p.endswith('.dSYM') for p in parts): return 'generated debug symbols'
    if path.startswith(('kernel/boot/limine/bin/', 'kernel/boot/limine/common-bios/', 'kernel/boot/limine/decompressor-build/')): return 'generated Limine output'
    if path in {'kernel/boot/limine/config.h','kernel/boot/limine/config.status','kernel/boot/limine/GNUmakefile'}: return 'generated Limine configuration'
    if path.endswith(ARTIFACTS): return 'generated binary, dependency or runtime artifact'
    return None

def scan(directory):
    result, omissions = {}, []
    for base, dirs, files in os.walk(directory, followlinks=False):
        base = Path(base)
        for name in list(dirs):
            p = base/name; rel = str(p.relative_to(directory)); why = omission(rel, directory=True)
            if why or p.is_symlink():
                dirs.remove(name); omissions.append({'path':rel,'reason':why or 'symlink: not followed'})
        for name in sorted(files):
            p=base/name; rel=str(p.relative_to(directory)); why=omission(rel)
            if why or p.is_symlink():
                omissions.append({'path':rel,'reason':why or 'symlink: not followed'}); continue
            raw=p.read_bytes()
            result[rel]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                         'git_blob':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()}
    return result, sorted(omissions,key=lambda x:x['path'])

def git_snapshot(source, files):
    directory=SOURCE_ROOT/source; metadata=directory/'.git'
    relocated=metadata.is_file()
    if relocated: metadata=SOURCE_ROOT/'vos/vos4/.git/worktrees'/directory.name
    cmd=['git','--git-dir='+str(metadata),'--work-tree='+str(directory)]
    def run(*args): return subprocess.run(cmd+list(args),capture_output=True,check=False)
    head=run('rev-parse','--verify','HEAD')
    if head.returncode:
        return {'head':None,'history_status':'No resolvable HEAD; all content is working-tree-only, not claimed clean',
                'local_changes':[{'path':p,'state':'history-unavailable'} for p in sorted(files)]}
    tree=run('ls-tree','-rz','--full-tree','HEAD')
    if tree.returncode: raise RuntimeError(tree.stderr.decode())
    tracked={}
    for record in tree.stdout.split(b'\0'):
        if not record:continue
        meta,p=record.split(b'\t',1);mode,kind,blob=meta.decode().split();p=p.decode()
        if not omission(p) and kind=='blob': tracked[p]=blob
    changes=[]
    for p in sorted(set(tracked)|set(files)):
        if p not in files: state='deleted-or-unfollowed-symlink'
        elif p not in tracked: state='untracked'
        elif tracked[p]!=files[p]['git_blob']:state='modified-versus-HEAD'
        else:continue
        changes.append({'path':p,'state':state,'head_blob':tracked.get(p),'working_sha256':files.get(p,{}).get('sha256')})
    status=run('diff','--cached','--name-status','HEAD','--')
    return {'head':head.stdout.decode().strip(),'relocated_metadata_read_without_repair':relocated,
            'history_status':'HEAD and working bytes compared; generated/credential exclusions apply',
            'staged_name_status':status.stdout.decode().splitlines() if status.returncode==0 else None,
            'local_changes':changes}

# Specific source directories are explicit component boundaries. Headers are
# retained as a separate ABI component where filenames do not establish ownership.
PREFIXES=[
 ('kernel/src/boot/','native.boot'),('kernel/src/arch/','native.architecture'),
 ('kernel/src/mm/','native.memory'),('kernel/src/sched/','native.scheduler'),
 ('kernel/src/exec/','native.execution'),('kernel/src/init/','native.execution'),
 ('kernel/src/fs/','native.filesystems'),('kernel/src/drivers/','native.drivers'),
 ('kernel/src/net/','native.network'),('kernel/src/ipc/','native.ipc'),
 ('kernel/src/sec/','native.security'),('kernel/src/security/','native.security'),
 ('kernel/src/crypto/','native.crypto'),('kernel/src/ai/','native.ai'),
 ('kernel/src/tests/','native.tests'),('kernel/tests/','native.tests'),
 ('kernel/include/','native.abi'),('kernel/boot/','native.bootloader'),
 ('kernel/','native.build-and-support'),('user/musl/','user.libc'),('user/','user.programs'),
 ('backend/ai/','backend.agents'),('backend/security/','backend.security'),
 ('backend/sandbox/','backend.sandbox'),('backend/services/','backend.services'),
 ('backend/core/','backend.core'),('backend/api/','backend.api'),
 ('backend/tests/','backend.tests'),('backend/mcp-server/','backend.mcp'),
 ('backend/convex/','backend.convex'),('backend/kernel_bridge/','backend.kernel-bridge'),
 ('backend/middleware/','backend.middleware'),('backend/projects/','backend.example-projects'),
 ('backend/','backend.extensions'),('frontend/convex/','frontend.data'),
 ('frontend/','frontend.product'),('desktop/','desktop.host'),('sdk/','sdk.clients'),
 ('infra/security/','infra.security'),('infra/','infra.operations'),
 ('.github/','ci.workflows'),('docs/','documentation'),('tests/','system.tests'),
 ('scripts/','tooling'),('tools/','tooling'),('cli_system/','tooling'),('bin/','tooling'),
 ('LICENSE','licensing'),('SPDX','licensing'),('.reuse/','licensing')]

def symbols(path):
    """Candidate symbol index only: not proof of equivalent behavior."""
    if not path or not path.is_file(): return []
    suffix=path.suffix
    if suffix not in {'.py','.c','.h','.ts','.tsx','.rs','.cs'}: return []
    text=path.read_text(errors='replace')
    if suffix=='.py':
        try:
            tree=ast.parse(text)
            return sorted({n.name for n in ast.walk(tree) if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef))})
        except (SyntaxError,ValueError):return []
    if suffix in {'.ts','.tsx'}:
        return sorted(set(re.findall(r'export\s+(?:async\s+)?(?:const|function|class|interface|type)\s+(\w+)',text)))
    return sorted(set(re.findall(r'(?m)^\s*(?:[\w*]+\s+)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{',text)))

def component(path):
    for prefix,name in PREFIXES:
        if path.startswith(prefix):return name
    if path.lower().endswith(('.md','.txt','.pdf')):return 'documentation'
    return 'root.configuration'

def generate():
    OUT.mkdir(parents=True,exist_ok=True)
    disposition_file=ROOT/'consolidation/native-source-dispositions.json'
    path_dispositions={}
    if disposition_file.exists():
        disposition_doc=json.loads(disposition_file.read_text())
        for group in disposition_doc['dispositions']:
            for decided_path in group['paths']:
                if decided_path in path_dispositions:
                    raise ValueError('duplicate source disposition: '+decided_path)
                path_dispositions[decided_path]={
                    'id':group['id'], 'disposition':group['disposition'],
                    'state':group['state'], 'rationale':group['rationale'],
                    'validation':group['validation']}
    canonical,_=scan(ROOT)
    canonical={p:v for p,v in canonical.items() if not p.startswith(('consolidation/','docs/handoff/'))}
    by_hash=defaultdict(list)
    for p,v in canonical.items():by_hash[v['sha256']].append(p)
    baseline=json.loads((ROOT/'consolidation/baseline.json').read_text())
    renamed={x['source_path']:x['path'] for x in baseline['imported']}
    sources={}; paths=defaultdict(dict)
    for source in SOURCES:
        if not (SOURCE_ROOT/source).is_dir():raise FileNotFoundError(source)
        files,omitted=scan(SOURCE_ROOT/source)
        git=git_snapshot(source,files)
        sources[source]={'included_files':len(files),'excluded_entries':omitted,**git}
        for p,v in files.items():paths[p][source]=v
    rows=[]; groups=defaultdict(list)
    for p,variants in sorted(paths.items()):
        target=renamed.get(p,p); current=canonical.get(target)
        matches=[s for s,v in variants.items() if current and v['sha256']==current['sha256']]
        alt={s:by_hash[v['sha256']] for s,v in variants.items() if v['sha256'] in by_hash}
        missing_variants=[s for s in variants if s not in alt]
        if matches: status='selected-content-retained'
        elif current:status='canonical-diverged-review'
        elif alt:status='content-retained-at-other-path'
        else:status='missing-from-canonical'
        row={'path':p,'component':component(p),'canonical_path':target if current else None,
             'canonical_sha256':current['sha256'] if current else None,
             'status':status,'exact_source_matches':matches,'exact_matches_elsewhere':alt,
             'unretained_source_variants':missing_variants,'sources':variants,
             'decision':'retain canonical implementation; review unretained variants' if current else
                        'retain relocated content; validate references' if alt else
                        'open: evaluate donor implementation before integration'}
        if p in path_dispositions:
            row['path_disposition']=path_dispositions[p]
            row['decision']=path_dispositions[p]['disposition']+': '+path_dispositions[p]['state']
        rows.append(row);groups[row['component']].append(row)
    lookup={r['path']:r for r in rows}
    for source,g in sources.items():
        for change in g['local_changes']:
            row=lookup.get(change['path'])
            change['canonical_content_status']=row['status'] if row else 'absent-or-excluded'
            change['working_bytes_retained']=bool(row and source in row['exact_matches_elsewhere'])
    candidates=[]
    for row in rows:
        if not row['unretained_source_variants']:continue
        # Third-party source is tracked in the byte ledger, not misrepresented
        # as hundreds of first-party feature additions.
        if row['path'].startswith(('user/musl/','kernel/boot/limine/')):continue
        seen=set(); current=symbols(ROOT/row['canonical_path']) if row['canonical_path'] else []
        for source in row['unretained_source_variants']:
            digest=row['sources'][source]['sha256']
            if digest in seen:continue
            seen.add(digest)
            declared=symbols(SOURCE_ROOT/source/row['path'])
            candidates.append({'path':row['path'],'component':row['component'],
                               'representative_source':source,'sha256':digest,
                               'equivalent_source_copies':[s for s,v in row['sources'].items() if v['sha256']==digest],
                               'declared_symbols':declared,'symbols_not_in_selected_file':sorted(set(declared)-set(current)),
                               'scope':'Symbol absence is a review lead, not proof that a capability is missing elsewhere'})
    (OUT/'variant-review.json').write_text(json.dumps(candidates,indent=2)+'\n')
    catalog=json.loads((ROOT/'consolidation/component-decisions.json').read_text())
    assert set(groups)<=set(catalog),set(groups)-set(catalog)
    summary={k:dict(Counter(r['status'] for r in v)) for k,v in sorted(groups.items())}
    report={'scope':'All eleven working trees, filtered exclusions; content identity is not semantic equivalence or runtime validation',
            'sources':sources,'file_status_counts':dict(Counter(r['status'] for r in rows)),
            'component_counts':summary,'files':rows,'canonical_only_files':sorted(set(canonical)-{r['canonical_path'] for r in rows})}
    (OUT/'file-ledger.json').write_text(json.dumps(report,indent=2)+'\n')
    lines=['# Component selection and reconciliation ledger','',
           'Generated by `python3 consolidation/reconcile_sources.py`. Decisions are in `component-decisions.json`.',
           'Byte matches establish retention only. Unretained variants remain explicit review work; no automatic semantic merge is claimed.','',
           '| Component | Selected implementation | Retained | Diverged | Elsewhere | Missing | Decision and remaining evidence |',
           '|---|---|---:|---:|---:|---:|---|']
    for k,c in summary.items():
        d=catalog[k];lines.append('| '+k+' | '+d['selected']+' | '+' | '.join(str(c.get(x,0)) for x in ['selected-content-retained','canonical-diverged-review','content-retained-at-other-path','missing-from-canonical'])+' | '+d['decision']+' |')
    (OUT/'COMPONENTS.md').write_text('\n'.join(lines)+'\n')
    gaps=['# Source files absent from canonical content','',
          'Excludes files retained under another path. Each item remains an integration decision, not an instruction to copy it blindly.','']
    for r in rows:
        if r['status']=='missing-from-canonical':
            decided=r.get('path_disposition')
            suffix=(' — **'+decided['disposition']+' / '+decided['state']+'**') if decided else ''
            gaps.append('- `'+r['path']+'` — '+', '.join(r['sources'])+' — '+r['component']+suffix)
    (OUT/'MISSING_FILES.md').write_text('\n'.join(gaps)+'\n')
    local=['# Local source changes','', 'Compared working bytes with each source HEAD without altering donor metadata. Exclusions are recorded in file-ledger.json.','']
    for source,g in sources.items():
        local+=['## '+source,'','HEAD: `'+str(g['head'])+'`. '+g['history_status'], '']
        for change in g['local_changes']:local.append('- `'+change['path']+'`: '+change['state'])
        if not g['local_changes']:local.append('No included working-tree content changes relative to HEAD.')
        local.append('')
    (OUT/'LOCAL_CHANGES.md').write_text('\n'.join(local).rstrip()+'\n')
    print(json.dumps({'sources':len(sources),'components':len(groups),'paths':len(rows),
                      'status':report['file_status_counts'],
                      'local_changes':{s:dict(Counter(c['state'] for c in g['local_changes'])) for s,g in sources.items()}},indent=2))

if __name__=='__main__':generate()
