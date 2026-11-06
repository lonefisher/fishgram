#!/usr/bin/env python3
"""Protected attachment-only Beta publishing. Never signs or promotes updater feeds.

The artifact ZIP digest is obtained directly from GitHub on every invocation.
Local provenance JSON is deliberately not an accepted input.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

import release_gate as gate
from sign_candidate import verify_run_provenance, SigningError
from publish_release import Asset, portable_package, distribution_record
from github_release_transport import GitHubReleaseTransport, PRODUCT_REPOSITORY, SOURCE_REPOSITORY

PUBLISHER_ROOT = Path(__file__).resolve().parents[1]

README = '''FishGram Beta - Windows 11 x64 portable
Extract all files, then run Start-FishGram.cmd.
Accounts and settings are in FishGramData beside the program.
Close FishGram before replacing program files; preserve FishGramData.
Windows executables are unsigned (no Windows Authenticode).
Windows UI and actual account testing have not been completed for this candidate.
Full search coverage, large-source performance and native omission comparisons remain deferred.
Update installation, network retry and recovery testing remain deferred.
Update with Scoop: scoop update fishgram-beta.
Portable users must manually download updates from this release.
The client built-in updater is not promoted by this attachment-only release.
Corresponding source, build records and SHA256 checksums are separate release attachments.
'''

def digest(data):
    return hashlib.sha256(data).hexdigest()

def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()

def gh_json(path):
    """Read authoritative GitHub metadata using only the ordinary read token."""
    token = os.environ.get('GH_TOKEN', '')
    if not token or token in (os.environ.get('PRODUCT_GITHUB_APP_TOKEN'), os.environ.get('SOURCE_GITHUB_APP_TOKEN')):
        raise ValueError('A distinct read-only GH_TOKEN is required for provenance.')
    env = {'PATH': os.environ.get('PATH', ''), 'GH_TOKEN': token,
           'GH_HOST': 'github.com', 'SYSTEMROOT': os.environ.get('SYSTEMROOT', ''),
           'HOME': os.environ.get('HOME', ''), 'USERPROFILE': os.environ.get('USERPROFILE', '')}
    result = subprocess.run(['gh', 'api', path], capture_output=True, env=env)
    if result.returncode:
        raise ValueError('Authoritative GitHub provenance lookup failed.')
    return json.loads(result.stdout)

def provenance(run_id, expected_parent, artifact_path, fetch=gh_json):
    if type(run_id) is not int or run_id <= 0:
        raise ValueError('Candidate run ID must be a positive integer.')
    base = f'repos/{PRODUCT_REPOSITORY}/actions/runs/{run_id}'
    run = fetch(base)
    jobs = fetch(base + '/jobs?per_page=100')
    artifacts = fetch(base + '/artifacts?per_page=100')
    if jobs.get('total_count', 101) > 100 or artifacts.get('total_count', 101) > 100:
        raise ValueError('Paginated provenance is unsupported; refusing incomplete metadata.')
    try:
        verify_run_provenance(run, jobs, artifacts, PRODUCT_REPOSITORY, run_id, expected_sha=expected_parent)
    except SigningError as error:
        raise ValueError(str(error)) from error
    artifact = next(a for a in artifacts['artifacts'] if a['name'] == f'fishgram-product-candidate-{run_id}')
    if type(artifact.get('id')) is not int or artifact['id'] <= 0:
        raise ValueError('Candidate artifact ID missing.')
    if artifact.get('digest') != 'sha256:' + gate.sha256_file(artifact_path):
        raise ValueError('Exact GitHub artifact archive digest mismatch or missing.')
    return {'runId': run_id, 'artifactId': artifact['id'], 'artifactDigest': artifact['digest']}

def extract_candidate(data, output, version):
    """Bounded flat allowlist extraction, independent of host path semantics."""
    allowed = {'candidate-record.json', 'FishGram-corresponding-source.zip',
               'package/build-manifest.json', 'package/Packer.exe',
               f'package/FishGram-{version}-windows-x64-candidate.zip'}
    output = Path(output)
    if output.exists():
        raise ValueError('Candidate extraction output already exists.')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        files = [i for i in infos if not i.is_dir()]
        names = [i.filename for i in files]
        if len(names) != len(set(names)) or set(names) != allowed:
            raise ValueError('Unexpected, missing or duplicate candidate artifact files.')
        for info in infos:
            mode = (info.external_attr >> 16) & 0o170000
            if (info.filename not in allowed | {'package/'} or info.flag_bits & 1
                    or mode not in (0, 0o100000, 0o040000)):
                raise ValueError('Unsafe artifact entry.')
        output.mkdir()
        for info in files:
            target = output / info.filename
            target.parent.mkdir(exist_ok=True)
            with archive.open(info) as source, target.open('xb') as dest:
                import shutil
                shutil.copyfileobj(source, dest)
    return output

def prepare(root, reviewed, run_id, artifact_zip, output, *, fetch=gh_json):
    root, artifact_zip, output = Path(root), Path(artifact_zip), Path(output)
    if root.resolve() != PUBLISHER_ROOT:
        raise ValueError('Candidate root must be the executing publisher checkout.')
    if output.exists() or output.is_symlink():
        raise ValueError('Preparation output must not already exist.')
    if artifact_zip.is_symlink() or not artifact_zip.is_file():
        raise ValueError('Exact downloaded artifact archive is required.')
    # Read only the record until authenticated ZIP bytes and provenance are checked.
    with zipfile.ZipFile(artifact_zip) as archive:
        raw_record = archive.read('candidate-record.json')
    manifest = json.loads(raw_record.decode('utf-8-sig'))
    gate.validate_manifest(manifest)
    if manifest['channel'] != 'beta' or not isinstance(manifest.get('sourceArchive'), dict):
        raise ValueError('Only a source-bound Beta product candidate is allowed.')
    identity = provenance(run_id, manifest['parentCommit'], artifact_zip, fetch)
    gate.check_pins(root, reviewed, manifest)
    with tempfile.TemporaryDirectory(prefix='fishgram-beta-candidate-') as tmp:
        folder = extract_candidate(artifact_zip.read_bytes(), Path(tmp) / 'candidate', manifest['version'])
        package = folder / 'package' / manifest['archive']['name']
        source = folder / 'FishGram-corresponding-source.zip'
        gate.verify_package(manifest, package)
        gate.verify_tool(manifest, 'Packer.exe', folder / 'package/Packer.exe')
        if json.loads((folder / 'package/build-manifest.json').read_text(encoding='utf-8-sig')) != {
                k: v for k, v in manifest.items() if k != 'sourceArchive'}:
            raise ValueError('Artifact build manifest differs from the candidate record.')
        gate.verify_source_archive(manifest, source)
        portable = portable_package(package.read_bytes(), manifest['version'], readme_override=README.encode())
        assets = [portable, Asset(source.name, source.read_bytes()),
                  Asset('candidate-record.json', raw_record), distribution_record(portable, manifest, reviewed)]
    checksums = ''.join(f'{a.sha256}  {a.name}\n' for a in sorted(assets, key=lambda a: a.name)).encode()
    assets.append(Asset('SHA256SUMS', checksums))
    plan = {'schema': 1, 'kind': 'fishgram-beta-attachments', 'reviewedCommit': reviewed,
            'parentCommit': manifest['parentCommit'], 'sourceCommit': manifest['sourceCommit'],
            'tag': 'v' + manifest['version'] + '-beta', 'version': manifest['version'],
            'provenance': identity, 'assets': [{'name': a.name, 'size': len(a.data), 'sha256': a.sha256} for a in assets]}
    output.mkdir(parents=True)
    for asset in assets:
        (output / asset.name).write_bytes(asset.data)
    (output / 'beta-plan.json').write_bytes(json_bytes(plan))
    return plan

def publish(root, artifact_zip, prepared, *, enabled=False, api=None, fetch=gh_json):
    if not enabled or any(os.environ.get(k) != v for k, v in {
            'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
            'GITHUB_ENVIRONMENT': 'release', 'FISHGRAM_RELEASE_APPROVED': '1'}.items()):
        raise ValueError('Explicit protected main release approval is required.')
    prepared = Path(prepared)
    plan = json.loads((prepared / 'beta-plan.json').read_text())
    # Recompute everything from remotely authenticated immutable candidate bytes.
    with tempfile.TemporaryDirectory(prefix='fishgram-beta-recheck-') as tmp:
        fresh = Path(tmp) / 'prepared'
        verified = prepare(root, plan['reviewedCommit'], plan['provenance']['runId'], artifact_zip, fresh, fetch=fetch)
        if verified != plan or {p.name for p in prepared.iterdir()} != {p.name for p in fresh.iterdir()}:
            raise ValueError('Prepared publication plan changed.')
        for path in fresh.iterdir():
            target = prepared / path.name
            if target.is_symlink() or not target.is_file() or gate.sha256_file(target) != gate.sha256_file(path):
                raise ValueError('Prepared publication bytes changed.')
        api = api or GitHubReleaseTransport.from_environment()
        tag = plan['tag']
        if api.any_release_exists(tag):
            raise ValueError('Existing releases, including drafts and partial releases, cannot be reused.')
        api.verify_source_merged(plan['sourceCommit'])
        for repo, target in ((SOURCE_REPOSITORY, plan['sourceCommit']), (PRODUCT_REPOSITORY, plan['parentCommit'])):
            if api.tag_exists(repo, tag) and api.tag_commit(repo, tag) != target:
                raise ValueError('Immutable tag points to a different candidate.')
        objects = []
        try:
            if not api.source_tag_exists(tag):
                objects.append(f'https://github.com/{SOURCE_REPOSITORY}/tree/{tag}')
                api.create_source_tag(tag, plan['sourceCommit'])
            objects.append(f'https://github.com/{PRODUCT_REPOSITORY}/releases/tag/{tag}')
            api.create_release(tag, plan['parentCommit'], f'FishGram {plan["version"]} Beta', README,
                               prerelease=True, make_latest=False)
            for asset in plan['assets']:
                api.upload_asset(tag, asset['name'], (fresh / asset['name']).read_bytes())
            api.publish_release(tag)
            for repo, target in ((SOURCE_REPOSITORY, plan['sourceCommit']),
                                 (PRODUCT_REPOSITORY, plan['parentCommit'])):
                if api.tag_commit(repo, tag) != target:
                    raise ValueError('Published immutable tag target read-back failed.')
            if not api.release_exists(tag) or api.release_prerelease(tag) is not True:
                raise ValueError('Published prerelease read-back failed.')
            for asset in plan['assets']:
                data = api.download_asset(tag, asset['name'])
                if len(data) != asset['size'] or digest(data) != asset['sha256']:
                    raise ValueError('Anonymous public attachment hash verification failed.')
            program = plan['assets'][0]
            result = {'kind': 'fishgram-beta-public-downloads-verified', 'tag': tag,
                      'url': f'https://github.com/{PRODUCT_REPOSITORY}/releases/download/{tag}/{program["name"]}',
                      'sha256': program['sha256'], 'size': program['size'], 'assets': plan['assets']}
            (prepared / 'public-result.json').write_bytes(json_bytes(result))
            return result
        except Exception as error:
            raise ValueError('Publication stopped; objects may have been created (no repair attempted): '
                             + ', '.join(objects)) from error

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'publish'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--artifact-zip', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reviewed-commit')
    parser.add_argument('--candidate-run-id', type=int)
    parser.add_argument('--enable-publication', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.root, args.reviewed_commit, args.candidate_run_id, args.artifact_zip, args.output)
        else:
            result = publish(args.root, args.artifact_zip, args.output, enabled=args.enable_publication)
        print(json.dumps(result, sort_keys=True))
    except Exception as error:
        print('Beta attachment publication refused: ' + str(error))
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
