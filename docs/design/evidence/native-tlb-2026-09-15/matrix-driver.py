from pathlib import Path
import subprocess
import json

repo = Path('/Users/sz/Desktop/vos/vos 5')
revision = '8c20f26e442a3525ffbc83a6d4e3ede68040c04f'
results = []
for name, flags in [('final', ['--tlb']), ('normal', []),
                    ('memory', ['--memory']), ('isolation', ['--isolation'])]:
    output = Path('/private/tmp/vos5-tlb-' + name + '-20260915')
    command = ['python3', 'scripts/native_clean_qualification.py',
               '--revision', revision, '--output', str(output), *flags]
    print(json.dumps({'starting': name, 'revision': revision}), flush=True)
    completed = subprocess.run(command, cwd=repo, timeout=1800)
    result = json.loads((output / 'result.json').read_text())
    results.append({'name': name, 'exit': completed.returncode,
                    'passed': result['passed'], 'result': str(output / 'result.json')})
    Path('/private/tmp/vos5-tlb-final-matrices-20260915.json').write_text(
        json.dumps(results, indent=2) + '\n')
    if completed.returncode or not result['passed']:
        raise SystemExit(1)
