"""Candidate-only release helpers. Never reads credentials or running data."""
import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path


def update_version(upstream, revision):
    if not isinstance(revision, int) or isinstance(revision, bool) or not 0 < revision < 2**32:
        raise ValueError('Revision must be a positive uint32.')
    if not re.fullmatch(r'\d+\.\d+\.\d+', upstream):
        raise ValueError('Invalid upstream version.')
    major, minor, patch = map(int, upstream.split('.'))
    if minor >= 1000 or patch >= 1000:
        raise ValueError('Invalid upstream version components.')
    base = major * 1_000_000 + minor * 1000 + patch
    if not 0 < base < 2**32:
        raise ValueError('Invalid upstream version base.')
    return str((base << 32) | revision)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_payload(directory, recipe):
    directory = Path(directory).resolve(strict=True)
    allowed = set(recipe['payloadFiles']) | set(recipe['payloadDlls'])
    if any(Path(name).name != name or ':' in name or name in ('.', '..') for name in allowed):
        raise ValueError('Payload allowlist must contain safe basenames.')
    files = []
    for item in directory.rglob('*'):
        relative = item.relative_to(directory).as_posix()
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise ValueError('Payload cannot contain links.')
        if item.is_dir() or relative not in allowed or not item.is_file():
            raise ValueError('Unapproved payload entry: ' + relative)
        files.append(item)
    if {item.name for item in files} != allowed:
        raise ValueError('Incomplete payload.')
    if any(not item.stat().st_size for item in files):
        raise ValueError('Empty payload file.')
    return sorted(files)


def git(root, *arguments):
    return subprocess.check_output(['git', '-c', 'core.longpaths=true', '-C', str(root), *arguments], text=True).strip()


def verify_built_payload(files, record):
    recorded = record.get('files')
    if not isinstance(recorded, dict) or set(recorded) != {p.name for p in files}:
        raise ValueError('Build record must bind every payload file.')
    for path in files:
        entry = recorded[path.name]
        if not isinstance(entry, dict):
            raise ValueError('Invalid built file record.')
        size, digest = entry.get('size'), entry.get('sha256')
        if (type(size) is not int or size <= 0 or not isinstance(digest, str)
                or not re.fullmatch(r'[a-f0-9]{64}', digest)):
            raise ValueError('Invalid built file record.')
        if path.stat().st_size != size or sha256(path) != digest:
            raise ValueError('Payload differs from the built candidate.')


def make_package(root, payload, output, record_path):
    root, payload, output = Path(root).resolve(), Path(payload).resolve(), Path(output).resolve()
    recipe = json.loads((root / 'fishgram.json').read_text(encoding='utf-8-sig'))
    files = validate_payload(payload, recipe)
    record = json.loads(Path(record_path).read_text(encoding='utf-8-sig'))
    version = f"{recipe['upstreamVersion']}-r{recipe['revision']}"
    source = git(root / 'tdesktop', 'rev-parse', 'HEAD')
    parent = git(root, 'rev-parse', 'HEAD')
    if record.get('version') != version or record.get('sourceCommit') != source or record.get('parentCommit') != parent:
        raise ValueError('Build record does not match this checkout.')
    if record.get('channel') != recipe['channel'] or record.get('autoUpdate') != recipe['autoUpdate']:
        raise ValueError('Build configuration differs from recipe.')
    verify_built_payload(files, record)
    if output.exists():
        raise ValueError('Existing candidate output cannot be replaced.')
    # Never nest the candidate output in a running data or input directory.
    if output == payload or output.is_relative_to(payload):
        raise ValueError('Candidate output must be separate from build input.')
    submodules = git(root / 'tdesktop', 'submodule', 'status', '--recursive').splitlines()
    if not submodules or any(line.startswith(('-', '+', 'U')) for line in submodules):
        raise ValueError('Recursive dependencies are missing or inconsistent.')
    record.update(schema=1, product='FishGram', platform=recipe['platform'],
                  upstreamCommit=recipe['upstreamCommit'], upstreamTag=recipe['upstreamTag'],
                  upstreamVersion=recipe['upstreamVersion'],
                  updateVersion=update_version(recipe['upstreamVersion'], recipe['revision']),
                  releaseReady=False, submodules=submodules,
                  files={p.name: {'size': p.stat().st_size, 'sha256': sha256(p)} for p in files})
    output.mkdir(parents=True)
    archive = output / f'FishGram-{version}-{recipe["platform"]}-candidate.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as package:
        for file in files:
            package.write(file, file.name)
        package.write(root / 'tdesktop' / 'LICENSE', 'LICENSE')
        package.write(root / 'tdesktop' / 'LEGAL', 'LEGAL')
        launcher = '@echo off\r\nsetlocal\r\nif not exist "%~dp0FishGramData" mkdir "%~dp0FishGramData"\r\nstart "" "%~dp0Telegram.exe" -workdir "%~dp0FishGramData"\r\n'
        package.writestr('Start-FishGram.cmd', launcher)
        package.writestr('README.txt', 'FishGram internal candidate; Windows 11 x64. Run Start-FishGram.cmd. Account data belongs in FishGramData, separate from the package staging directory. This ZIP is not a public release. The updater signature is not Windows Authenticode.\r\n')
        package.writestr('build-manifest.json', json.dumps(record, indent=2) + '\n')
    record['archive'] = {'name': archive.name, 'sha256': sha256(archive), 'size': archive.stat().st_size}
    (output / 'build-manifest.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    (output / 'SHA256SUMS').write_text(f'{record["archive"]["sha256"]}  {archive.name}\n', encoding='ascii')
    print(archive)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['package'])
    parser.add_argument('--root', required=True)
    parser.add_argument('--payload', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--record', required=True)
    options = parser.parse_args()
    make_package(options.root, options.payload, options.output, options.record)


if __name__ == '__main__':
    main()
