"""Negative and positive tests for the FishGram release gate."""
import datetime as dt
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / 'tools' / 'release_gate.py'
spec = importlib.util.spec_from_file_location('release_gate', MODULE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

PARENT = 'a' * 40
SOURCE = 'b' * 40
SUB1 = 'c' * 40
SUB2 = 'd' * 40
PACKER = 'e' * 64


def digest(data):
    return hashlib.sha256(data).hexdigest()


PAYLOAD = {'Telegram.exe': b'candidate telegram bytes', 'Updater.exe': b'candidate updater bytes'}
RECIPE = {'product': 'FishGram', 'platform': 'windows-x64', 'upstreamVersion': '7.2.9',
          'revision': 8, 'channel': 'stable', 'msvc': '14.44', 'windowsSdk': '10.0.26100.0',
          'qt': '5.15.19', 'autoUpdate': False, 'payloadFiles': ['Telegram.exe', 'Updater.exe'],
          'payloadDlls': []}


def manifest(files=None, **overrides):
    record = {
        'schema': 1, 'product': 'FishGram', 'version': '7.2.9-r8',
        'updateVersion': str((7002009 << 32) | 8), 'platform': 'windows-x64', 'channel': 'stable',
        'identity': 'product', 'productCandidate': True, 'autoUpdate': True,
        'recipeAutoUpdate': False, 'testUpdateTrust': False, 'releaseReady': False,
        'parentCommit': PARENT, 'sourceCommit': SOURCE,
        'toolchain': {'msvc': '14.44.35207', 'sdk': '10.0.26100.0', 'qt': '5.15.19'},
        'files': files if files is not None else {
            name: {'size': len(data), 'sha256': digest(data)} for name, data in PAYLOAD.items()},
        'submodules': [' ' + SUB1 + ' tdesktop/ThirdParty/one (v1.0)', ' ' + SUB2 + ' tdesktop/lib_x'],
        'tools': {'Packer.exe': {'size': 7, 'sha256': PACKER}},
    }
    record['archive'] = {'name': 'FishGram-7.2.9-r8-windows-x64-candidate.zip',
                         'sha256': 'f' * 64, 'size': 100}
    record.update(overrides)
    return record


def make_zip(directory, record, payload=None, extra=None, embedded=None):
    payload = PAYLOAD if payload is None else payload
    embedded = embedded if embedded is not None else {k: v for k, v in record.items() if k != 'archive'}
    path = Path(directory) / record['archive']['name']
    with zipfile.ZipFile(path, 'w') as bundle:
        for name, data in payload.items():
            bundle.writestr(name, data)
        bundle.writestr('LICENSE', 'gpl')
        bundle.writestr('LEGAL', 'openssl exception')
        bundle.writestr('Start-FishGram.cmd', 'run')
        bundle.writestr('README.txt', 'note')
        bundle.writestr('build-manifest.json', json.dumps(embedded))
        for name, data in (extra or {}).items():
            bundle.writestr(name, data)
    return path


def bound_zip(directory, record, **kwargs):
    path = make_zip(directory, record, **kwargs)
    record['archive']['sha256'] = digest(path.read_bytes())
    record['archive']['size'] = path.stat().st_size
    return path


def qa_for(record, **overrides):
    qa = {
        'schema': 1, 'kind': 'fishgram-candidate-qa',
        'approver': 'lonefisher',
        'approvedAt': '2026-10-08T00:00:00Z',
        'environment': 'Clean Windows 11 24H2 VM, no prior install.',
        'candidate': {
            'version': record['version'],
            'parentCommit': record['parentCommit'],
            'sourceCommit': record['sourceCommit'],
            'archive': dict(record['archive']),
            'files': {name: {'sha256': entry['sha256']} for name, entry in record['files'].items()},
        },
        'checks': {name: True for name in gate.REQUIRED_CHECKS},
        'notes': '',
    }
    qa.update(overrides)
    return qa


class ManifestTests(unittest.TestCase):
    def test_valid_product_manifest(self):
        info = gate.validate_manifest(manifest(), RECIPE)
        self.assertEqual(info['versionBase'], 7002009)
        self.assertEqual(info['revision'], 8)

    def test_rejects_test_or_tampered_identity(self):
        for override in ({'identity': 'test'}, {'productCandidate': False},
                         {'testUpdateTrust': True}, {'autoUpdate': False},
                         {'releaseReady': True}):
            with self.subTest(override=override):
                with self.assertRaises(gate.GateError):
                    gate.validate_manifest(manifest(**override), RECIPE)

    def test_candidate_keeps_parent_recipe_flags_and_requires_product_identity(self):
        record = manifest()
        self.assertFalse(record['recipeAutoUpdate'])
        self.assertFalse(record['releaseReady'])
        for override in ({'identity': 'test'}, {'productCandidate': False},
                         {'testUpdateTrust': True}, {'autoUpdate': False}):
            with self.subTest(override=override), self.assertRaises(gate.GateError):
                gate.validate_manifest(manifest(**override), RECIPE)

    def test_rejects_version_and_recipe_mismatch(self):
        for override in ({'version': '7.2.9-r9'}, {'updateVersion': '1'},
                         {'channel': 'beta'}, {'platform': 'linux'},
                         {'toolchain': {'msvc': '14.43.0', 'sdk': '10.0.26100.0', 'qt': '5.15.19'}}):
            with self.subTest(override=override):
                with self.assertRaises(gate.GateError):
                    gate.validate_manifest(manifest(**override), RECIPE)

    def test_rejects_missing_and_extra_payload_records(self):
        only_client = {'Telegram.exe': manifest()['files']['Telegram.exe']}
        with self.assertRaises(gate.GateError):
            gate.validate_manifest(manifest(files=only_client), RECIPE)
        tampered = dict(manifest()['files'])
        tampered['extra.dll'] = {'size': 1, 'sha256': 'a' * 64}
        with self.assertRaises(gate.GateError):
            gate.validate_manifest(manifest(files=tampered), RECIPE)
        bad = dict(manifest()['files'])
        bad['Telegram.exe'] = {'size': len(PAYLOAD['Telegram.exe']), 'sha256': 'a' * 64, 'extra': 1}
        with self.assertRaises(gate.GateError):
            gate.validate_manifest(manifest(files=bad), RECIPE)

    def test_rejects_bad_commits_submodules_and_archive(self):
        for override in ({'parentCommit': 'abc'}, {'sourceCommit': SOURCE.upper()},
                         {'submodules': []}, {'submodules': ['+' + SUB1 + ' moved']},
                         {'submodules': ['-' + SUB1 + ' missing']},
                         {'archive': {'name': 'other.zip', 'sha256': 'f' * 64, 'size': 1}}):
            with self.subTest(override=override):
                with self.assertRaises(gate.GateError):
                    gate.validate_manifest(manifest(**override), RECIPE)


class PackageTests(unittest.TestCase):
    def test_verified_package_and_extracted_payload(self):
        record = manifest()
        with tempfile.TemporaryDirectory() as temp:
            package = bound_zip(temp, record)
            gate.verify_package(record, package)
            out = Path(temp) / 'payload'
            self.assertEqual(gate.extract_payload(record, package, out), ['Telegram.exe', 'Updater.exe'])
            self.assertEqual((out / 'Telegram.exe').read_bytes(), PAYLOAD['Telegram.exe'])
            with self.assertRaises(gate.GateError):
                gate.extract_payload(record, package, out)

    def test_tampered_payload_inside_archive_fails(self):
        record = manifest()
        with tempfile.TemporaryDirectory() as temp:
            package = bound_zip(temp, record, payload={'Telegram.exe': b'tampered', 'Updater.exe': PAYLOAD['Updater.exe']})
            with self.assertRaisesRegex(gate.GateError, 'payload differs'):
                gate.verify_package(record, package)

    def test_manifest_hash_mismatch_and_wrong_name_fail(self):
        record = manifest()
        with tempfile.TemporaryDirectory() as temp:
            package = bound_zip(temp, record)
            record['archive']['sha256'] = '0' * 64
            with self.assertRaises(gate.GateError):
                gate.verify_package(record, package)
            record['archive']['sha256'] = digest(package.read_bytes())
            with self.assertRaises(gate.GateError):
                gate.verify_package(record, package.with_name('renamed.zip'))

    def test_extra_missing_and_unsafe_entries_fail(self):
        record = manifest()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(gate.GateError):
                gate.verify_package(record, bound_zip(temp, record, extra={'notes.txt': 'x'}))
            with self.assertRaises(gate.GateError):
                gate.verify_package(record, bound_zip(temp, record, payload={'Telegram.exe': PAYLOAD['Telegram.exe']}))
            with self.assertRaises(gate.GateError):
                gate.verify_package(record, bound_zip(temp, record, extra={'..\\evil.exe': 'x'}))

    def test_symlink_entry_is_rejected_even_when_its_name_is_safe(self):
        record = manifest()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / record['archive']['name']
            with zipfile.ZipFile(path, 'w') as bundle:
                for name, data in PAYLOAD.items():
                    bundle.writestr(name, data)
                for name, data in {'LICENSE': 'gpl', 'LEGAL': 'openssl exception',
                                   'Start-FishGram.cmd': 'run', 'README.txt': 'note',
                                   'build-manifest.json': json.dumps({k: v for k, v in record.items() if k != 'archive'})}.items():
                    info = zipfile.ZipInfo(name)
                    if name == 'README.txt':
                        info.create_system = 3
                        info.external_attr = (0o120777 << 16)
                    bundle.writestr(info, data)
            record['archive']['size'] = path.stat().st_size
            record['archive']['sha256'] = digest(path.read_bytes())
            with self.assertRaisesRegex(gate.GateError, 'link or special'):
                gate.verify_package(record, path)

    def test_corresponding_source_archive_must_match_recursive_candidate_pins(self):
        record = manifest()
        full_license = (b'MIT License\nCopyright 2026 Fixture Authors\n'
                        b'Permission is hereby granted, free of charge.\n'
                        b'THE SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTY.\n' + b'Synthetic license terms.\n' * 40)
        source_files = {name: full_license for name in (
            'LICENSE', 'tdesktop/LICENSE', 'tdesktop/ThirdParty/one/LICENSE', 'tdesktop/lib_x/LICENSE')}
        source_files['config/license-sources.json'] = b'{"schemaVersion":1,"officialSources":[]}'
        source_manifest = {
            'schema': 1, 'product': 'FishGram', 'parentCommit': PARENT,
            'version': record['version'],
            'repositories': [
                {'path': '.', 'commit': PARENT},
                {'path': 'tdesktop', 'commit': SOURCE},
                {'path': 'tdesktop/ThirdParty/one', 'commit': SUB1},
                {'path': 'tdesktop/lib_x', 'commit': SUB2},
            ],
            'files': {name: {'size': len(data), 'sha256': digest(data)} for name, data in source_files.items()},
        }
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / 'source.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                for name, data in source_files.items():
                    bundle.writestr(name, data)
                bundle.writestr('source-manifest.json', json.dumps(source_manifest))
            gate.verify_source_archive(record, archive)
            source_manifest['repositories'][2]['commit'] = 'f' * 40
            with zipfile.ZipFile(archive, 'w') as bundle:
                for name, data in source_files.items():
                    bundle.writestr(name, data)
                bundle.writestr('source-manifest.json', json.dumps(source_manifest))
            with self.assertRaisesRegex(gate.GateError, 'recursive dependencies'):
                gate.verify_source_archive(record, archive)

    def test_embedded_manifest_tampering_fails(self):
        record = manifest()
        with tempfile.TemporaryDirectory() as temp:
            embedded = {k: v for k, v in record.items() if k != 'archive'}
            embedded['files'] = dict(embedded['files'])
            embedded['files']['Telegram.exe'] = {'size': 1, 'sha256': 'a' * 64}
            with self.assertRaisesRegex(gate.GateError, 'Embedded build manifest'):
                gate.verify_package(record, bound_zip(temp, record, embedded=embedded))

    def test_source_archive_without_license_configuration_is_rejected(self):
        record = manifest()
        files = {'tdesktop/LICENSE': b'short license notice'}
        value = {'schema': 1, 'product': 'FishGram', 'parentCommit': PARENT, 'version': record['version'],
                 'repositories': [{'path': '.', 'commit': PARENT}, {'path': 'tdesktop', 'commit': SOURCE},
                                  {'path': 'tdesktop/ThirdParty/one', 'commit': SUB1}, {'path': 'tdesktop/lib_x', 'commit': SUB2}],
                 'files': {name: {'size': len(data), 'sha256': digest(data)} for name, data in files.items()}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'source.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
                archive.writestr('source-manifest.json', json.dumps(value))
            with self.assertRaisesRegex(gate.GateError, 'license'):
                gate.verify_source_archive(record, path)


class QaTests(unittest.TestCase):
    def test_template_binds_candidate_and_rejects_overwrite(self):
        record = manifest()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'qa.json'
            gate.qa_template(record, target)
            data = json.loads(target.read_text())
            self.assertEqual(data['candidate']['archive']['sha256'], record['archive']['sha256'])
            self.assertEqual(set(data['candidate']['files']), set(PAYLOAD))
            self.assertTrue(all(v is False for v in data['checks'].values()))
            with self.assertRaises(gate.GateError):
                gate.qa_template(record, target)

    def test_valid_qa_passes(self):
        record = manifest()
        gate.verify_qa(record, qa_for(record))

    def test_qa_for_different_candidate_is_rejected(self):
        record = manifest()
        other = manifest(archive={'name': 'FishGram-7.2.9-r8-windows-x64-candidate.zip',
                                  'sha256': '9' * 64, 'size': 5})
        with self.assertRaisesRegex(gate.GateError, 'different candidate archive'):
            gate.verify_qa(record, qa_for(other))
        qa = qa_for(record)
        qa['candidate']['files']['Telegram.exe']['sha256'] = '9' * 64
        with self.assertRaisesRegex(gate.GateError, 'different candidate payload'):
            gate.verify_qa(record, qa)
        qa = qa_for(record)
        qa['candidate']['parentCommit'] = 'f' * 40
        with self.assertRaisesRegex(gate.GateError, 'version or commits'):
            gate.verify_qa(record, qa)

    def test_incomplete_or_forged_qa_fails(self):
        record = manifest()
        qa = qa_for(record, checks={'cleanWindows11': True})
        with self.assertRaises(gate.GateError):
            gate.verify_qa(record, qa)
        qa = qa_for(record)
        qa['checks']['updaterFullMatrix'] = False
        with self.assertRaises(gate.GateError):
            gate.verify_qa(record, qa)
        for name in ('searchEightAcceptance', 'updaterFullMatrix', 'sameCandidateCloudConclusion'):
            qa = qa_for(record)
            qa['checks'][name] = False
            with self.subTest(check=name), self.assertRaises(gate.GateError):
                gate.verify_qa(record, qa)
        for bad in ({'approver': ''}, {'approver': 'not a user!'},
                    {'approvedAt': 'tomorrow'}, {'environment': '  '},
                    {'extra': 'field'}):
            with self.subTest(bad=bad):
                with self.assertRaises(gate.GateError):
                    gate.verify_qa(record, qa_for(record, **bad))
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with self.assertRaises(gate.GateError):
            gate.verify_qa(record, qa_for(record, approvedAt=future))


class AuthorizeTests(unittest.TestCase):
    def test_authorization_binds_signing_inputs(self):
        record = manifest()
        qa = qa_for(record)
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'release-authorization.json'
            result = gate.authorize(record, qa, 'qa.json', json.dumps(qa).encode(), out)
            self.assertEqual(result['allowedActions'], ['sign-update-package'])
            self.assertEqual(result['packer']['target'], 'win64')
            self.assertEqual(result['packer']['versionBase'], 7002009)
            self.assertEqual(result['packer']['counter'], 8)
            self.assertEqual(result['packer']['files'], ['Telegram.exe', 'Updater.exe'])
            self.assertEqual(result['qa']['sha256'], hashlib.sha256(json.dumps(qa).encode()).hexdigest())
            self.assertIn('indexPublish', result['blocked'])
            self.assertTrue(out.is_file())
            saved = out.read_bytes()
            with self.assertRaises(gate.GateError):
                gate.authorize(record, qa, 'qa.json', b'{}', out)
            self.assertEqual(out.read_bytes(), saved)

    def test_missing_packer_or_wrong_platform_blocks_signing(self):
        record = manifest(tools={})
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(gate.GateError, 'Packer'):
                gate.authorize(record, qa_for(record), 'qa.json', b'{}', Path(temp) / 'a.json')
        record = manifest(platform='linux')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(gate.GateError):
                gate.authorize(record, qa_for(record), 'qa.json', b'{}', Path(temp) / 'a.json')


def fake_git_factory(parent_overrides=None, source_overrides=None):
    parent_data = {
        ('rev-parse', 'HEAD'): PARENT,
        ('status', '--porcelain', '--untracked-files=no'): '',
        ('ls-files', '--stage', '--', 'tdesktop'): '160000 ' + SOURCE + ' 0\ttdesktop',
        ('ls-tree', PARENT, '--', 'tdesktop'): '160000 commit ' + SOURCE + '\ttdesktop',
        ('show', PARENT + ':fishgram.json'): json.dumps(RECIPE),
    }
    source_data = {
        ('rev-parse', 'HEAD'): SOURCE,
        ('status', '--porcelain', '--untracked-files=no'): '',
        ('submodule', 'status', '--recursive'): ' ' + SUB1 + ' tdesktop/one (v1.0)\n ' + SUB2 + ' tdesktop/two',
    }
    parent_data.update(parent_overrides or {})
    source_data.update(source_overrides or {})

    def fake(root, *args):
        data = source_data if str(root).endswith('tdesktop') else parent_data
        if tuple(args) in data:
            return data[tuple(args)]
        raise gate.GateError('unexpected git call: ' + ' '.join(args))
    return fake


def fake_git_ok_factory(answers):
    def fake(root, *args):
        return answers.get(tuple(args), False)
    return fake


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        self.original_git, self.original_ok = gate.git, gate.git_ok

    def tearDown(self):
        gate.git, gate.git_ok = self.original_git, self.original_ok

    def test_checkout_accepts_pinned_recursive_tree(self):
        gate.git = fake_git_factory()
        gate.check_checkout('/repo', PARENT)

    def test_checkout_rejects_wrong_head_dirty_or_moved_submodules(self):
        parent_cases = (
            {('rev-parse', 'HEAD'): 'f' * 40},
            {('status', '--porcelain', '--untracked-files=no'): ' M tools/x'},
            {('ls-files', '--stage', '--', 'tdesktop'): '160000 ' + 'f' * 40 + ' 0\ttdesktop'},
        )
        source_cases = (
            {('rev-parse', 'HEAD'): 'f' * 40},
            {('status', '--porcelain', '--untracked-files=no'): ' M src/x'},
            {('submodule', 'status', '--recursive'): '+' + SUB1 + ' tdesktop/one'},
            {('submodule', 'status', '--recursive'): '-' + SUB1 + ' tdesktop/one'},
            {('submodule', 'status', '--recursive'): 'U' + SUB1 + ' tdesktop/one'},
            {('submodule', 'status', '--recursive'): ''},
        )
        for overrides in parent_cases:
            with self.subTest(overrides=overrides):
                gate.git = fake_git_factory(parent_overrides=overrides)
                with self.assertRaises(gate.GateError):
                    gate.check_checkout('/repo', PARENT)
        for overrides in source_cases:
            with self.subTest(overrides=overrides):
                gate.git = fake_git_factory(source_overrides=overrides)
                with self.assertRaises(gate.GateError):
                    gate.check_checkout('/repo', PARENT)

    def test_pins_bind_manifest_to_reviewed_history(self):
        gate.git = fake_git_factory({('rev-parse', 'HEAD'): 'f' * 40})
        gate.git_ok = fake_git_ok_factory({
            ('cat-file', '-e', PARENT + '^{commit}'): True,
            ('merge-base', '--is-ancestor', PARENT, 'HEAD'): True,
            ('ls-files', '--error-unmatch', '--', 'release/qa/7.2.9-r8.json'): True,
        })
        gate.check_pins('/repo', 'f' * 40, manifest(), ['release/qa/7.2.9-r8.json'])

    def test_pins_reject_foreign_or_unreviewed_history(self):
        for answers in (
            {('cat-file', '-e', PARENT + '^{commit}'): True,
             ('merge-base', '--is-ancestor', PARENT, 'HEAD'): False},
            {('cat-file', '-e', PARENT + '^{commit}'): False,
             ('merge-base', '--is-ancestor', PARENT, 'HEAD'): True},
            {('cat-file', '-e', PARENT + '^{commit}'): True,
             ('merge-base', '--is-ancestor', PARENT, 'HEAD'): True,
             ('ls-files', '--error-unmatch', '--', 'release/qa/7.2.9-r8.json'): False},
        ):
            with self.subTest(answers=answers):
                gate.git = fake_git_factory({('rev-parse', 'HEAD'): 'f' * 40})
                gate.git_ok = fake_git_ok_factory(answers)
                with self.assertRaises(gate.GateError):
                    gate.check_pins('/repo', 'f' * 40, manifest(), ['release/qa/7.2.9-r8.json'])

    def test_pins_reject_gitlink_mismatch(self):
        gate.git = fake_git_factory({('rev-parse', 'HEAD'): 'f' * 40,
                                     ('ls-tree', PARENT, '--', 'tdesktop'): '160000 commit ' + 'a' * 40 + '\ttdesktop'})
        gate.git_ok = fake_git_ok_factory({
            ('cat-file', '-e', PARENT + '^{commit}'): True,
            ('merge-base', '--is-ancestor', PARENT, 'HEAD'): True,
        })
        with self.assertRaises(gate.GateError):
            gate.check_pins('/repo', 'f' * 40, manifest())

    def test_check_private_workspace_rejects_untracked_and_private_material(self):
        original = gate.git
        try:
            gate.git = lambda root, *args: '?? .private/api-secret.cmake' if args == ('status', '--porcelain', '--untracked-files=all') else ''
            with self.assertRaisesRegex(gate.GateError, 'untracked'):
                gate.check_private_workspace('/repo')
            gate.git = lambda root, *args: '.private/api-secret.cmake\0' if args == ('ls-files', '-z') else ''
            with self.assertRaisesRegex(gate.GateError, 'committed'):
                gate.check_private_workspace('/repo')
        finally:
            gate.git = original


class CliTests(unittest.TestCase):
    def test_actual_git_preserves_clean_submodule_status_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = root / 'tdesktop'
            child.mkdir()
            for repository in (root, child):
                subprocess.run(['git', '-C', str(repository), 'init', '-q'], check=True)
                subprocess.run(['git', '-C', str(repository), 'config', 'user.name', 'Synthetic gate'], check=True)
                subprocess.run(['git', '-C', str(repository), 'config', 'user.email', 'fixture@example.invalid'], check=True)
            (child / 'file.cpp').write_text('synthetic source')
            subprocess.run(['git', '-C', str(child), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(child), 'commit', '-qm', 'fixture'], check=True)
            subprocess.run(['git', '-C', str(root), 'add', 'tdesktop'], check=True, capture_output=True)
            (root / '.gitmodules').write_text('[submodule "tdesktop"]\n\tpath = tdesktop\n\turl = ./tdesktop\n')
            subprocess.run(['git', '-C', str(root), 'add', '.gitmodules'], check=True)
            subprocess.run(['git', '-C', str(root), 'commit', '-qm', 'fixture'], check=True)
            subprocess.run(['git', '-C', str(root), 'submodule', 'init'], check=True, capture_output=True)
            line = gate.git(root, 'submodule', 'status', '--recursive')
            self.assertTrue(line.startswith(' '), repr(line))
            self.assertRegex(line, gate.SUBMODULE_LINE)

    def test_cli_fails_closed_on_bad_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            bad = Path(temp) / 'bad.json'
            bad.write_text(json.dumps(manifest(identity='test')))
            result = subprocess.run(
                [sys.executable, str(MODULE), 'verify-manifest', '--manifest', str(bad)],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('GATE FAILED', result.stderr)


if __name__ == '__main__':
    unittest.main()
