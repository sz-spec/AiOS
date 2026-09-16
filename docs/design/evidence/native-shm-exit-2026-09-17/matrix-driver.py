from pathlib import Path
import subprocess
import json

repo = Path('/Users/sz/Desktop/vos/vos 5')
revision = '8c23007eed4acd731b98bdd2998c9634f2ba3f75'
results = []
for name, flags in [('memory', ['--memory']), ('normal', []),
                    ('tlb', ['--tlb']), ('isolation', ['--isolation'])]:
    output = Path('/private/tmp/vos5-shm-exit-' + name + '-release-20260915')
    command = ['python3', 'scripts/native_clean_qualification.py',
               '--revision', revision, '--output', str(output), *flags]
    print(json.dumps({'starting': name, 'revision': revision}), flush=True)
    completed = subprocess.run(command, cwd=repo, timeout=1800)
    result = json.loads((output / 'result.json').read_text())
    results.append({'name': name, 'exit': completed.returncode,
                    'passed': result['passed'], 'result': str(output / 'result.json')})
    Path('/private/tmp/vos5-shm-exit-release-matrices-20260915.json').write_text(
        json.dumps(results, indent=2) + '\n')
    if completed.returncode or not result['passed']:
        raise SystemExit(1)
