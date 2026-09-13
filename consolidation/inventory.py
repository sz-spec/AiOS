"""Compare VOS source trees without relying on relocated Git worktree links.

Records paths and hashes only; never copies source content or local credentials.
Run from any directory: python3 consolidation/inventory.py
"""
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['VOS3', 'VOS3-Cyber', 'VOS-Cyber-Standard', 'vos.v1',
           'vos/vos4', 'vos/vos4-ci-triggers', 'vos/vos4-kernel-bugfix',
           'vos/vos4-track-1', 'vos/vos4-track-2', 'vos/vos4-track-5',
           'vos/vos4-track-7']
EXCLUDED = {'.git', 'node_modules', '.next', '__pycache__', '.pytest_cache',
            '.mypy_cache', '.ruff_cache', 'venv', '.venv', '.venv_p312',
            'target', 'build', 'dist', '.DS_Store', '.turbo'}


def excluded(name):
    return (name in EXCLUDED or name.startswith(('.venv', '.env'))
            or name.endswith(('.pyc', '.pem', '.key', '.p12', '.pfx')))


def scan(root):
    files, omitted = {}, []
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        keep = []
        for name in sorted(dirs):
            path = base / name
            if excluded(name) or path.is_symlink():
                omitted.append(str(path.relative_to(root)))
            else:
                keep.append(name)
        dirs[:] = keep
        for name in sorted(names):
            path = base / name
            relative = str(path.relative_to(root))
            if excluded(name) or path.is_symlink():
                omitted.append(relative)
                continue
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            files[relative] = digest.hexdigest()
    return files, omitted


def main():
    paths = defaultdict(dict)
    source_counts, exclusions = {}, {}
    for source in SOURCES:
        directory = ROOT / source
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        files, exclusions[source] = scan(directory)
        source_counts[source] = len(files)
        for path, digest in files.items():
            paths[path][source] = digest
    unique, identical, divergent = {}, {}, {}
    for path, copies in sorted(paths.items()):
        target = (unique if len(copies) == 1 else
                  identical if len(set(copies.values())) == 1 else divergent)
        target[path] = copies
    report = {'sources': source_counts, 'unique': unique,
              'identical': identical, 'divergent': divergent,
              'excluded': exclusions}
    destination = ROOT / 'consolidation' / 'inventory.json'
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'sources': source_counts, 'unique_paths': len(unique),
                      'identical_paths': len(identical),
                      'divergent_paths': len(divergent)}, indent=2))


if __name__ == '__main__':
    main()
