from pathlib import Path
import hashlib,json,re,shutil,subprocess
repo=Path('/Users/sz/Desktop/vos/vos 5');tmp=Path('/private/tmp');final=tmp/'vos-health-final-20260917'
evidence=repo/'docs/design/evidence/health-performance-2026-09-17';evidence.mkdir(exist_ok=True)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def copy(src,rel):
 dst=evidence/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)

def metrics(path):
 result=json.loads((path/'result.json').read_text())
 serial=(path/'serial.log').read_text(errors='replace').replace('\r','').replace('\0','')
 blocks=re.findall(r'=== Running: test_health_check ===(.*?)=== test_health_check: exit=(-?\d+) ===',serial,re.S)
 assert len(blocks)==1,str(path)
 block,exit_code=blocks[0]
 def field(label):
  found=re.findall(r'^\s*'+re.escape(label)+r'\s+(\d+)',block,re.M)
  assert len(found)==1,(path,label,found)
  return int(found[0])
 row={'passed':result['passed'],'completed_programs':len(result['exits']),'zero_exits':sum(status=='0' for _,status in result['exits']), 'health_exit':int(exit_code),'pushed':field('pushed:'),'consumed':field('consumed:'),'elapsed_ms':field('elapsed:'),'messages_per_second':field('throughput:'),'iso_sha256':result['iso_sha256']}
 assert row['messages_per_second']==row['consumed']*1000//row['elapsed_ms']
 assert row['pushed']==row['consumed']
 row['early_getpid_average_tsc']=int(re.search(r'Null syscall \(getpid\): min=\d+ avg=(\d+)',serial).group(1))
 if (path/'host-timing.json').exists():row['host_elapsed_seconds']=json.loads((path/'host-timing.json').read_text())['elapsed_seconds']
 elif 'elapsed_seconds' in result:row['host_elapsed_seconds']=result['elapsed_seconds']
 return row

cases=[
 ('rejected-compare',tmp/'vos-health-kpti-20260917/bios','Rejected compare-before-store'),
 ('diagnostic-current',tmp/'vos-health-diag-current-20260917/bios','Instrumented current; diagnostic only'),
 ('diagnostic-old',tmp/'vos-health-diag-old-20260917/bios','Instrumented old; diagnostic only'),
 ('baseline-replay',tmp/'vos-health-baseline-replay-20260917','Unmodified current ISO replay'),
 ('pending-only',tmp/'vos-health-deferred-20260917/bios','Pending early load only'),
]+[(name,final/name,'Final '+name) for name in ['bios-1','uefi-1','bios-2','uefi-2']]
rows=[]
for name,path,label in cases:
 row=metrics(path);row.update(name=name,label=label);rows.append(row)
 for filename in ['result.json','serial.log','host-timing.json','invocation.json']:
  if (path/filename).exists():copy(path/filename,Path('runs')/name/filename)
assert all(r['passed'] and r['messages_per_second']>=80000 and r['completed_programs']==57 for r in rows[-4:])
for filename in ['source-manifest.json','dirty.patch','build.log','host-tests.log','kpti-tests.log','reproducibility.json','assembly-review.json','runtime-review.json','kpti-roots.disassembly.txt','deferred.disassembly.txt','deferred-part.disassembly.txt','repeat-summary.json','repeat-driver.log']:
 copy(final/filename,Path('final')/filename)
copy(tmp/'vos-health-final-rebuild-20260917/build.log',Path('final/rebuild.log'))
for name,directory in [('rejected-compare','vos-health-kpti-20260917'),('diagnostic-current','vos-health-diag-current-20260917'),('diagnostic-old','vos-health-diag-old-20260917'),('pending-only','vos-health-deferred-20260917')]:
 root=tmp/directory
 for filename in ['source-manifest.json','dirty.patch','candidate.patch','kpti-tests.log','build.log','build-approved.log']:
  if (root/filename).exists():copy(root/filename,Path('experiments')/name/filename)
for name in ['vos-health-observe.py','vos-health-repeat.py','vos-final-uefi-runner.py','vos-health-diag-user.patch','vos-health-diag-user.README.md','vos-preserve-health.py']:
 copy(tmp/name,Path('drivers')/name)
# Preserve untracked-at-freeze additions so the 6321-file snapshot is reconstructable.
manifest=json.loads((final/'source-manifest.json').read_text())
base_paths=set(subprocess.check_output(['git','ls-tree','-r','--name-only',manifest['commit']],cwd=repo,text=True).splitlines())
for record in manifest['files']:
 if record['path'] not in base_paths:copy(final/'source'/record['path'],Path('frozen-additions')/record['path'])
summary={'base_commit':manifest['commit'],'source_manifest_sha256':sha(final/'source-manifest.json'),'source_files':len(manifest['files']),'health_elf_sha256':'28cac8eb9131f7f9c9bcac756542f5eb3d6af79d51de51a200085b239aa7b56a','threshold_messages_per_second':80000,'qemu_version':subprocess.check_output(['qemu-system-x86_64','--version'],text=True).splitlines()[0],'runs':rows}
(evidence/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
repro=json.loads((final/'reproducibility.json').read_text());iso=repro['first_artifacts']['dist/final-full.iso'];kernel=repro['first_artifacts']['kernel/build/native-full-final/vos3.elf']
lines=['# Native health performance repair — 2026-09-17','','The original health gate now passes in two BIOS and two UEFI complete-suite runs of the same final ISO. Each run executes all 57 programs with zero exit status. This is bounded single-vCPU QEMU TCG qualification, not a physical-hardware or complete-SMP performance claim.','','## Changes and preserved checks','','- `vos3_sched_process_deferred()` observes the pending bit before taking the active guard. An empty observation does not consume concurrent publication. The nonempty drain, exclusion guard, deferred work, signals and reclamation remain.','- `vos3_kpti_sync_root()` partitions the existing mapping policy into contiguous loops. It still assigns every one of the 512 entries, reads the same 258 source entries, propagates revocations and clears forbidden slots. Root binding, locks and CR3/TLB transitions remain.','- The health source and ELF, one-second duration, 1024-slot ring and 80,000 messages/s threshold are unchanged. No MMIO permission or isolation check was relaxed.','','## Results','','| Run | Programs exiting zero | Messages | Elapsed guest ms | Messages/s | Full suite |','|---|---:|---:|---:|---:|---|']
for r in rows:lines.append(f"| {r['label']} | {r['zero_exits']}/{r['completed_programs']} | {r['consumed']:,} | {r['elapsed_ms']} | {r['messages_per_second']:,} | {'PASS' if r['passed'] else 'FAIL'} |")
lines+=['','The instrumented programs are diagnostic evidence only. The rejected compare-before-store patch and its failing run are retained. The pending-only sample establishes a passing narrow candidate; one sample does not quantify its effect independently of host/layout variation. The final repeated runs qualify the combined production changes.','','The same unmodified ISO previously measured 37,923 messages/s and measured 74,227 in the adjacent replay. Earlier GETPID/context-switch metrics also varied, including before the diagnostic health code executed. Consequently, prior attribution solely to Frontier was not justified, and these data do not establish one complete cause for the historical 98K-to-38K gap. The identified fixed syscall costs are reduced; observed throughput also depends on host execution and guest scheduling phase.','', 'A 1024-slot ring can cross a scheduling boundary when producer fill time approaches the 10ms tick. Filling before the tick permits an early yield; otherwise the empty consumer can spend the next service interval spinning. This explains sensitivity qualitatively, not as a calibrated causal proof. No emulated TSC value is presented as physical CPU cycles or independently calibrated microseconds.','','## Validation and reproducibility','','- 107 host regression tests passed (`final/host-tests.log`). The new actual-source drain test exercises deterministic publication/reentry cases in both UP and `NATIVE_SMP_TEST` branches with UBSan.','- 27 actual-source KPTI tests passed (`final/kpti-tests.log`), including randomized roots, mapping revocation and rejected storage aliasing.','- Pinned-builder assembly confirms the empty pending path has no locked read-modify-write and the nonempty path retains both exchanges. Root sync retains 512 writes/258 reads with 384 loop-branch executions; see the independent assembly review.','- Two fresh manifest-only builds produced identical ISO, kernel and six bootloader artifacts. All 6,321 frozen source files remained unchanged in each build. The second input tree had normalized timestamps; neither build reused project outputs.','- Compiler warnings remain recorded, including existing upstream/vendor diagnostics and clock-skew warnings. Passing checks do not mean warning-free builds.','',f'ISO SHA-256: `{iso}`',f'Kernel SHA-256: `{kernel}`',f'Original/final health ELF SHA-256: `{summary["health_elf_sha256"]}`','',f'Immutable builder: `{repro["builder"]}`; `SOURCE_DATE_EPOCH=1700000000`; container path `/vos3`; four build jobs; offline Docker with dropped capabilities and no-new-privileges. Both builds use:','','```sh','make native -j4 BENCH_MODE=1 HEADLESS_AUDIT=1 BUILD_DIR=build/native-full-final INSTALLER_ISO=../dist/final-full.iso','```','','## Evidence and review','','The evidence directory contains manifests, the source patch, frozen untracked additions, build logs, raw serial logs, full workload classifiers, exact VM commands, firmware hashes, diagnostic/repeat drivers, static assembly inspection and `SHA256SUMS`. Reconstruct the final frozen tree from the recorded base commit, `final/dirty.patch`, and `frozen-additions/`; the manifest is the authoritative file set. Review documents were completed after freezing; the compiled native sources match the workspace. Drivers retain absolute experiment paths as provenance and require path adaptation on another host.','','The BIOS observer uses 100ms host-monotonic milestone polling; these timestamps are not per-byte arrival times. VM runs were sequential. Final qualification runs had no concurrent project builds or test suites. Exploratory runs were not a fully controlled host A/B study; other host activity and CPU/core/power allocation were not controlled or certified. Failed samples have not been discarded.','','[Research limited to 2026-07-19 through 2026-09-17](health-performance-research-2026-09-17.md), [mathematical review](health-performance-invariants-2026-09-17.md), and [security review](health-performance-security-2026-09-17.md).','','## Remaining limits','','The existing partial KPTI boundary, remote TLB/cancellation safety, physical PC compatibility and Secure Boot remain outside this qualification. The separate SYSINFO ABI defect (kernel copies 40 bytes into legacy 32-byte caller structures) existed in both compared baselines; inspected health frames placed the excess bytes in padding, which does not make the ABI safe. It is documented for follow-up, not presented as repaired or as the proven throughput cause. Unsupported NPU/MMIO hardware remains unavailable and denied. This ISO is a benchmark qualification artifact, not a production installation release.','']
(repo/'docs/design/evidence/health-performance-2026-09-17.md').write_text('\n'.join(lines))
files=sorted(p for p in evidence.rglob('*') if p.is_file() and p.name!='SHA256SUMS')
(evidence/'SHA256SUMS').write_text(''.join(sha(p)+'  '+str(p.relative_to(evidence))+'\n' for p in files))
print(json.dumps({'evidence_files':len(files),'final_runs':[r['messages_per_second'] for r in rows[-4:]],'iso_sha256':iso},indent=2))
