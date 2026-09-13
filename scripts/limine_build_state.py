"""Validate the vendored bootloader's source/tool/artifact fingerprint."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ['limine', 'limine-bios.sys', 'limine-bios-cd.bin',
             'limine-uefi-cd.bin', 'BOOTX64.EFI', 'BOOTIA32.EFI']

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def state(output):
    paths = [p for p in (ROOT/'kernel/boot/limine').rglob('*') if p.is_file()]
    paths += [ROOT/'infra/build_limine.sh', Path(__file__).resolve()]
    target = os.environ.get('TOOLCHAIN_FOR_TARGET', 'x86_64-elf')
    versions = {}
    for tool in [target+'-gcc', target+'-ld', 'cc', 'make', 'nasm']:
        result = subprocess.run([tool, '--version'], capture_output=True, text=True, check=True)
        versions[tool] = result.stdout.splitlines()[0]
    return {'sources': {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)},
            'tools': versions, 'target': target,
            'environment': {k: os.environ.get(k, '') for k in
                            ['CC', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS', 'SOURCE_DATE_EPOCH']},
            'artifacts': {name: sha(output/name) for name in ARTIFACTS}}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    manifest = args.output/'build-state.json'
    try:
        current = state(args.output)
        if not args.record:
            if json.loads(manifest.read_text()) != current:
                raise SystemExit(1)
            return
    except (OSError, ValueError, subprocess.CalledProcessError):
        if args.record:
            raise
        raise SystemExit(1)
    temporary = manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(current, indent=2)+'\n')
    temporary.replace(manifest)

if __name__ == '__main__':
    main()
