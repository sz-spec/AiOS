"""Resolve Python 3.12 deployment locks or check their recorded inputs offline."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / 'dependencies/python-locks.json'
JOBS = [
    ('backend/requirements-full.txt', 'backend/requirements-full.lock', 'x86_64-unknown-linux-gnu'),
    ('requirements.txt', 'requirements.lock', 'x86_64-unknown-linux-gnu'),
    ('backend/requirements.txt', 'backend/requirements.lock', 'x86_64-unknown-linux-gnu'),
    ('requirements_win.txt', 'requirements_win.lock', 'x86_64-pc-windows-msvc'),
    ('requirements.txt', 'dependencies/requirements-macos-arm64.lock', 'aarch64-apple-darwin'),
]
INPUTS = ['dependencies/python-dependencies.json', 'backend/constraints.txt',
          'scripts/sync_python_requirements.py', 'scripts/lock_python_dependencies.py',
          *dict.fromkeys(job[0] for job in JOBS)]

def digest(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()

def snapshot():
    return {'python': '3.12', 'macos_deployment_target': '14.0', 'inputs': {p: digest(p) for p in INPUTS},
            'locks': {out: {'input': src, 'platform': platform, 'sha256': digest(out)}
                      for src, out, platform in JOBS}}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    subprocess.run(['python3', 'scripts/sync_python_requirements.py', '--check'], cwd=ROOT, check=True)
    if args.check:
        if not RECORD.exists() or json.loads(RECORD.read_text()) != snapshot():
            raise SystemExit('Dependency inputs or locks changed; run make python-lock.')
        print('Verified 5 Python locks and their source fingerprints (offline).')
        return
    for index, (src, out, platform) in enumerate(JOBS):
        command = ['uv', 'pip', 'compile', src, '--python-version', '3.12',
                   '--python-platform', platform, '--generate-hashes', '--no-header',
                   '--quiet', '--upgrade', '--output-file', out]
        if index:
            command += ['-c', 'backend/requirements-full.lock']
        environment = dict(os.environ, MACOSX_DEPLOYMENT_TARGET='14.0')
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
    RECORD.write_text(json.dumps(snapshot(), indent=2) + '\n')
    print('Resolved and recorded 5 Python deployment locks.')

if __name__ == '__main__':
    main()
