#!/usr/bin/env python3
"""Archive committed sources into a fresh directory, build once, boot one ISO four ways."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--builder', default='vos5-builder:dependency-upgrade')
    parser.add_argument('--revision', default='HEAD')
    parser.add_argument('--isolation', action='store_true', help='build and run the gated native process-isolation image')
    parser.add_argument('--seconds', type=int, default=35)
    parser.add_argument('--firmware-code', type=Path, default=Path('/opt/homebrew/share/qemu/edk2-x86_64-code.fd'))
    parser.add_argument('--firmware-vars', type=Path, default=Path('/opt/homebrew/share/qemu/edk2-i386-vars.fd'))
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('seconds must be positive')
    repo = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    work = out / 'source'
    work.mkdir()
    started = time.monotonic()
    container = 'vos5-native-clean-' + secrets.token_hex(6)
    result = {'passed': False, 'build_container_name': container, 'build_command': ['make', 'native', '-j4'],
              'scope': 'fresh committed application sources with a prebuilt pinned compiler environment; emulator boot only',
              'network': 'none', 'source_date_epoch': 1700000000}
    if args.isolation:
        result['build_command'] += ['HEADLESS_AUDIT=1', 'NATIVE_ISOLATION_TEST=1',
                                    'BUILD_DIR=build/native-isolation', 'INSTALLER_ISO=../dist/vos5-isolation.iso']
        result['scope'] = 'fresh-source diagnostic image; four direct CPU isolation attempts in each emulator configuration'
    result['harness_sha256'] = digest(Path(__file__))

    def call(cmd, timeout=30):
        return subprocess.check_output(cmd, cwd=repo, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()

    try:
        commit = call(['git', 'rev-parse', args.revision + '^{commit}'])
        result['commit'] = commit
        result['git_tree'] = call(['git', 'rev-parse', commit + '^{tree}'])
        archive = out / 'source.tar'
        subprocess.run(['git', 'archive', '--format=tar', '--output=' + str(archive), commit], cwd=repo, check=True, timeout=60)
        result['archive_sha256'] = digest(archive)
        # Only our immutable Git archive is extracted, after checking traversal.
        with tarfile.open(archive) as source:
            for entry in source.getmembers():
                target = (work / entry.name).resolve()
                assert target.is_relative_to(work), entry.name
                if entry.issym() or entry.islnk():
                    assert (target.parent / entry.linkname).resolve().is_relative_to(work), entry.name
            source.extractall(work)
        inputs = {str(p.relative_to(work)): digest(p) for p in sorted(work.rglob('*')) if p.is_file()}
        forbidden = [name for name in inputs if Path(name).suffix in {'.o', '.a', '.elf', '.iso'}]
        assert not forbidden, 'tracked compiled inputs: ' + repr(forbidden)
        for directory in ('kernel/build', 'user/build', 'build', 'dist'):
            assert not (work / directory).exists(), 'preexisting output: ' + directory
        result['initial_generated_outputs_absent'] = True
        result['source_files'] = len(inputs)
        result['source_manifest_sha256'] = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        (out / 'source-manifest.json').write_text(json.dumps(inputs, indent=2) + '\n')
        image = call(['docker', 'image', 'inspect', args.builder, '--format', '{{.Id}}'])
        assert image.startswith('sha256:')
        result['builder_image'] = image
        tool_command = ['docker', 'run', '--rm', '--network', 'none', image, 'sh', '-c',
                        'x86_64-elf-gcc --version && x86_64-elf-ld --version && make --version && xorriso -version']
        (out / 'tool-versions.txt').write_text(call(tool_command))
        command = ['docker', 'run', '--rm', '--name', container, '--label', 'vos.native-clean=' + container,
                   '--network', 'none', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                   '--mount', 'type=bind,src=' + str(work) + ',dst=/vos3', '-w', '/vos3',
                   '-e', 'SOURCE_DATE_EPOCH=1700000000', '-e', 'BUILD_JOBS=4', image, *result['build_command']]
        with (out / 'build.log').open('w') as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        result['build_exit'] = completed.returncode
        assert completed.returncode == 0, 'clean build failed; see build.log'
        assert all(digest(work / name) == value for name, value in inputs.items()), 'build mutated source inputs'
        result['source_inputs_unchanged'] = True
        iso = work / ('dist/vos5-isolation.iso' if args.isolation else 'dist/vos5.iso')
        assert iso.is_file()
        iso.chmod(0o444)
        result['iso_sha256'] = digest(iso)
        result['iso_bytes'] = iso.stat().st_size
        build_dir = 'kernel/build/native-isolation' if args.isolation else 'kernel/build/native-unified'
        result['kernel_sha256'] = digest(work / build_dir / 'vos3.elf')
        if args.isolation:
            result['user_test_sha256'] = digest(work / build_dir / 'user/bin/test_native_isolation')
        classifier = repo / ('scripts/native_isolation_smoke.py' if args.isolation else 'scripts/native_boot_smoke.py')
        result['classifier_sha256'] = digest(classifier)
        result['qemu_version'] = call(['qemu-system-x86_64', '--version']).splitlines()[0]
        result['firmware_sha256'] = {'code': digest(args.firmware_code), 'vars_template': digest(args.firmware_vars)}

        def boot(case):
            firmware, cpus = case
            dest = out / (firmware + str(cpus))
            cmd = [sys.executable, str(classifier), '--iso', str(iso), '--output', str(dest),
                   '--smp', str(cpus), '--seconds', str(args.seconds)]
            if firmware == 'uefi':
                cmd += ['--firmware-code', str(args.firmware_code), '--firmware-vars', str(args.firmware_vars)]
            with (out / (dest.name + '-runner.log')).open('w') as log:
                run = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=args.seconds + 30)
            evidence = json.loads((dest / 'result.json').read_text())
            assert run.returncode == 0 and evidence['passed'], dest.name
            assert evidence['iso_sha256'] == result['iso_sha256'], 'matrix ISO mismatch'
            print(json.dumps({'passed': dest.name, 'iso_sha256': evidence['iso_sha256']}), flush=True)
            return evidence

        # Two VMs at a time avoid four-way TCG contention changing observation coverage.
        with ThreadPoolExecutor(max_workers=2) as pool:
            result['boots'] = list(pool.map(boot, [('bios', 1), ('bios', 4), ('uefi', 1), ('uefi', 4)]))
        assert digest(iso) == result['iso_sha256'] and digest(classifier) == result['classifier_sha256']
        result['passed'] = True
    except Exception as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        # A client timeout must not leave its owned build container running.
        try:
            query = ['docker', 'container', 'ls', '--all', '--filter', 'name=^/' + container + '$', '--format', '{{.ID}}']
            if call(query):
                owner = call(['docker', 'inspect', '--format', '{{index .Config.Labels "vos.native-clean"}}', container])
                assert owner == container, 'unconfirmed cleanup ownership'
                subprocess.run(['docker', 'rm', '-f', container], capture_output=True, check=True, timeout=20)
            assert not call(query), 'build container remains after cleanup'
            result['build_container_removed'] = True
        except Exception as error:
            result['cleanup_error'] = type(error).__name__ + ': ' + str(error)
            result['passed'] = False
        result['elapsed_seconds'] = round(time.monotonic() - started, 2)
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'passed': result['passed'], 'result': str(out / 'result.json'), 'error': result.get('error')}), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
