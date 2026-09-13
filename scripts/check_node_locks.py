"""Check every hosted Node manifest against its lock without installing packages."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ['frontend/package.json', 'desktop/package.json', 'backend/package.json',
             'sdk/typescript/package.json', 'backend/mcp-server/package.json',
             'backend/code_review/package.json',
             'backend/projects/OmniStock_Enterprise/frontend/package.json',
             'backend/projects/OmniStock_Enterprise/backend/package.json']

def main():
    errors = []
    for name in MANIFESTS:
        manifest = json.loads((ROOT/name).read_text())
        lock_path = (ROOT/name).with_name('package-lock.json')
        if not lock_path.exists():
            errors.append(f'{name}: missing package-lock.json')
            continue
        lock = json.loads(lock_path.read_text())
        package = lock.get('packages', {}).get('', {})
        for key in ['name', 'version', 'dependencies', 'devDependencies', 'optionalDependencies', 'engines']:
            if manifest.get(key) != package.get(key):
                errors.append(f'{name}: lock root {key} differs')
        if lock.get('lockfileVersion', 0) < 3:
            errors.append(f'{name}: expected lockfileVersion 3')
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Checked {len(MANIFESTS)} Node manifests/locks; runtime and peer checks are separate.')

if __name__ == '__main__':
    main()
