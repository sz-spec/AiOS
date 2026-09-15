from pathlib import Path
import subprocess
import json

repo = Path('/Users/sz/Desktop/vos/vos 5')
revision = '3ce56da73271db2e30ac4b226db88bb72a979d50'
results = []
for name, flags in [('memory', ['--memory']), ('normal', []),
                    ('tlb', ['--tlb']), ('isolation', ['--isolation'])]:
    output = Path('/private/tmp/vos5-lifetime-' + name + '-final-20260915')
    command = ['python3', 'scripts/native_clean_qualification.py',
               '--revision', revision, '--output', str(output), *flags]
    print(json.dumps({'starting': name, 'revision': revision}), flush=True)
    completed = subprocess.run(command, cwd=repo, timeout=1800)
    result = json.loads((output / 'result.json').read_text())
    results.append({'name': name, 'exit': completed.returncode,
                    'passed': result['passed'], 'result': str(output / 'result.json')})
    Path('/private/tmp/vos5-lifetime-final-matrices-20260915.json').write_text(
        json.dumps(results, indent=2) + '\n')
    if completed.returncode or not result['passed']:
        raise SystemExit(1)
