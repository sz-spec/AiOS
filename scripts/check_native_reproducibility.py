"""Build two clean manifest snapshots and compare complete native artifacts.

Explicit integration check (requires Docker), not part of host-only discovery.
The manifest contains ``files: [{path, sha256}]``; unlisted outputs are never
copied. Both builds use the same pinned builder and internal paths, but fresh
host paths and private Limine scratch directories. Source timestamps are
normalized to the specified epoch before building.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_epoch(value):
    """Require a nonnegative epoch representable by the ISO date format."""
    try:
        epoch = int(value)
        if epoch < 0:
            raise ValueError
        datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise argparse.ArgumentTypeError('epoch must be a nonnegative UTC timestamp before year 10000')
    return epoch


def normalize_tree_times(tree, epoch):
    # Set directories last; creating children otherwise changes directory mtime.
    for path in sorted(tree.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        os.utime(path, ns=(epoch * 1000000000, epoch * 1000000000))
    os.utime(tree, ns=(epoch * 1000000000, epoch * 1000000000))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--builder', required=True)
    parser.add_argument('--epoch', type=source_epoch, default=1700000000)
    args = parser.parse_args()
    if not args.builder.startswith('sha256:') or len(args.builder) != 71:
        parser.error('--builder must be an immutable sha256 image ID')
    source = args.source.resolve()
    records = json.loads(args.manifest.read_text())['files']
    for item in records:
        relative = Path(item['path'])
        if relative.is_absolute() or '..' in relative.parts:
            parser.error('manifest path escapes source')
        if not (source / relative).resolve().is_relative_to(source):
            parser.error('manifest symlink escapes source')
        if digest(source / relative) != item['sha256']:
            parser.error('source hash mismatch: ' + str(relative))
    out = args.output.resolve()
    out.mkdir(exist_ok=False, parents=True)
    result = {'passed': False, 'manifest_sha256': digest(args.manifest),
              'builder': args.builder, 'epoch': args.epoch, 'runs': []}
    artifacts = ['dist/final-full.iso',
                 'kernel/build/native-full-final/vos3.elf']
    artifacts += ['kernel/build/limine/bin/' + name for name in
                  ['limine', 'limine-bios.sys', 'limine-bios-cd.bin',
                   'limine-uefi-cd.bin', 'BOOTX64.EFI', 'BOOTIA32.EFI']]
    try:
        for index in range(2):
            tree = out / str(index) / 'source'
            tree.mkdir(parents=True)
            for item in records:
                target = tree / item['path']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / item['path'], target)
                shutil.copymode(source / item['path'], target)
            normalize_tree_times(tree, args.epoch)
            for relative in ('kernel/build', 'dist/final-full.iso'):
                if (tree / relative).exists():
                    raise RuntimeError('manifest includes generated output: ' + relative)
            name = 'vos-repro-' + uuid.uuid4().hex
            command = ['docker', 'run', '--rm', '--name', name, '--network', 'none',
                       '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                       '--mount', f'type=bind,src={tree},dst=/vos3', '-w', '/vos3',
                       '-e', f'SOURCE_DATE_EPOCH={args.epoch}', '-e', 'BUILD_JOBS=4',
                       args.builder, 'make', 'native', '-j4', 'BENCH_MODE=1',
                       'HEADLESS_AUDIT=1', 'BUILD_DIR=build/native-full-final',
                       'INSTALLER_ISO=../dist/final-full.iso']
            run = {'command': command}
            result['runs'].append(run)
            try:
                with (tree.parent / 'build.log').open('wb') as log:
                    subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                   check=True, timeout=1500)
            except BaseException:
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
                raise
            run['source_unchanged'] = all(digest(tree / r['path']) == r['sha256']
                                          for r in records)
            if not run['source_unchanged']:
                raise RuntimeError('build changed source inputs')
            run['artifacts'] = {p: digest(tree / p) for p in artifacts}
        result['passed'] = result['runs'][0]['artifacts'] == result['runs'][1]['artifacts']
        if not result['passed']:
            raise RuntimeError('native artifacts are not byte-identical')
    finally:
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
