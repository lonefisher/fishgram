import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from unittest import mock
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location('export_source', Path(__file__).parents[1] / 'tools/export_source.py')
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode().strip()


def init(root):
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    git(root, 'config', 'user.name', 'Synthetic source fixture')


def commit(root):
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Synthetic build input')
    return git(root, 'rev-parse', 'HEAD')


def commit_index(root):
    git(root, 'commit', '-qm', 'Synthetic build input')
    return git(root, 'rev-parse', 'HEAD')


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fishgram-source-export-')
        self.base = Path(self.temp.name)
        self.root = self.base / 'parent'
        init(self.root)
        self.child = self.root / 'tdesktop'
        init(self.child)
        (self.child / 'LICENSE').write_text('Synthetic GPL license placeholder')
        (self.child / 'LEGAL').write_text('Synthetic exception fixture')
        (self.child / 'file.cpp').write_text('committed source')
        self.source_commit = commit(self.child)
        git(self.root, 'update-index', '--add', '--cacheinfo', f'160000,{self.source_commit},tdesktop')
        (self.root / 'fishgram.json').write_text(json.dumps({'product': 'FishGram', 'upstreamVersion': '7.2.9', 'revision': 8}))
        (self.root / 'AGENTS.md').write_text('Synthetic developer entry')
        (self.root / 'tools').mkdir()
        (self.root / 'tools/build-telegram.ps1').write_text('Synthetic build fixture')
        (self.root / 'config').mkdir()
        (self.root / 'config/license-sources.json').write_text(json.dumps({'schemaVersion': 1, 'officialSources': []}))
        self.parent_commit = commit(self.root)
        self.output = self.base / 'source.zip'

    def tearDown(self):
        self.temp.cleanup()

    def test_export_binds_committed_license_supplements_and_ignores_working_copy(self):
        body = 'MIT License\n' + 'Synthetic fixed license text.\n' * 40
        config = {'schemaVersion': 1, 'officialSources': [{
            'name': 'Fixed license fixture', 'repository': 'owner/legal',
            'commit': 'a' * 40, 'path': 'LICENSE',
            'pinnedUrl': 'https://raw.githubusercontent.com/owner/legal/' + 'a' * 40 + '/LICENSE',
            'sha256': hashlib.sha256(body.encode()).hexdigest(),
            'requiredInArchive': True, 'content': body,
            'supplementPath': 'LICENSES/official/fixed-LICENSE.txt', 'coversUrls': [],
        }]}
        path = self.root / 'config/license-sources.json'
        path.write_text(json.dumps(config))
        pin = commit(self.root)
        path.write_text('{}')
        manifest = source.export(self.root, self.output, pin)
        name = config['officialSources'][0]['supplementPath']
        self.assertEqual(manifest['licenseSupplements'][0]['sha256'], config['officialSources'][0]['sha256'])
        self.assertEqual(manifest['files'][name]['sha256'], hashlib.sha256(body.encode()).hexdigest())
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read(name), body.encode())

    def test_missing_license_source_config_is_rejected_before_archive_creation(self):
        (self.root / 'config/license-sources.json').unlink()
        pin = commit(self.root)
        with self.assertRaisesRegex(source.SourceExportError, 'license'):
            source.export(self.root, self.output, pin)
        self.assertFalse(self.output.exists())

    def test_git_objects_exclude_runtime_and_modified_working_files(self):
        (self.child / 'file.cpp').write_text('uncommitted replacement')
        (self.child / 'tdata').mkdir()
        (self.child / 'tdata/key_data').write_bytes(b'synthetic private data')
        manifest = source.export(self.root, self.output, self.parent_commit)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read('tdesktop/file.cpp'), b'committed source')
            self.assertNotIn('tdesktop/tdata/key_data', archive.namelist())
            self.assertIn('tdesktop/LEGAL', archive.namelist())
            self.assertEqual(len(manifest['repositories']), 2)
        with self.assertRaises(source.SourceExportError):
            source.export(self.root, self.output, self.parent_commit)

    def test_gitlink_mismatch_stops_without_an_archive(self):
        (self.child / 'another.cpp').write_text('new revision')
        commit(self.child)
        with self.assertRaisesRegex(source.SourceExportError, 'pinned'):
            source.export(self.root, self.output, self.parent_commit)
        self.assertFalse(self.output.exists())

    def test_committed_private_data_cannot_be_exported(self):
        (self.root / '.private').mkdir()
        (self.root / '.private/api.clixml').write_bytes(b'synthetic credential fixture')
        bad_commit = commit(self.root)
        with self.assertRaisesRegex(source.SourceExportError, 'Private'):
            source.export(self.root, self.output, bad_commit)
        self.assertFalse(self.output.exists())

    def test_recursive_dependency_is_exported_at_its_pin(self):
        dependency = self.child / 'dependency'
        init(dependency)
        (dependency / 'LICENSE').write_text('Synthetic dependency license')
        (dependency / 'file.h').write_text('recursive header')
        pin = commit(dependency)
        git(self.child, 'update-index', '--add', '--cacheinfo', f'160000,{pin},dependency')
        child_pin = commit(self.child)
        git(self.root, 'update-index', '--cacheinfo', f'160000,{child_pin},tdesktop')
        parent_pin = commit(self.root)
        manifest = source.export(self.root, self.output, parent_pin)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read('tdesktop/dependency/file.h'), b'recursive header')
        self.assertEqual(manifest['repositories'][-1], {'path': 'tdesktop/dependency', 'commit': pin})

    def test_zip_creation_race_does_not_delete_competing_file(self):
        competing_bytes = b'created by another process'

        def competing_create(path, *args, **kwargs):
            self.assertEqual(Path(path), self.output)
            self.output.write_bytes(competing_bytes)
            raise FileExistsError(str(self.output))

        with mock.patch.object(source.os, 'open', side_effect=competing_create), \
                mock.patch.object(source.zipfile, 'ZipFile', side_effect=competing_create):
            with self.assertRaises(FileExistsError):
                source.export(self.root, self.output, self.parent_commit)
        self.assertEqual(self.output.read_bytes(), competing_bytes)

    def test_manifest_path_conflict_is_rejected_before_archive_creation(self):
        (self.root / 'source-manifest.json').write_text('committed source file')
        commit_with_conflict = commit(self.root)
        with self.assertRaisesRegex(source.SourceExportError, 'manifest'):
            source.export(self.root, self.output, commit_with_conflict)
        self.assertFalse(self.output.exists())

    def test_recursive_dependency_may_contain_dist_source_directory(self):
        dependency = self.child / 'dependency'
        init(dependency)
        (dependency / 'dist').mkdir()
        (dependency / 'dist' / 'generated_header.h').write_text('committed dependency source')
        pin = commit(dependency)
        git(self.child, 'update-index', '--add', '--cacheinfo', f'160000,{pin},dependency')
        child_pin = commit(self.child)
        git(self.root, 'update-index', '--cacheinfo', f'160000,{child_pin},tdesktop')
        parent_pin = commit(self.root)
        source.export(self.root, self.output, parent_pin)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read('tdesktop/dependency/dist/generated_header.h'), b'committed dependency source')

    def test_repository_private_paths_remain_rejected(self):
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime' / 'local.json').write_text('synthetic runtime data')
        private_commit = commit(self.root)
        with self.assertRaises(source.SourceExportError):
            source.export(self.root, self.output, private_commit)
        self.assertFalse(self.output.exists())

    def test_relative_symlink_is_exported_without_following_it(self):
        target_oid = subprocess.check_output(
            ['git', '-C', str(self.child), 'hash-object', '-w', '--stdin'], input=b'file.cpp'
        ).decode().strip()
        git(self.child, 'update-index', '--add', '--cacheinfo', f'120000,{target_oid},source-link')
        child_pin = commit_index(self.child)
        git(self.root, 'update-index', '--cacheinfo', f'160000,{child_pin},tdesktop')
        parent_pin = commit(self.root)
        source.export(self.root, self.output, parent_pin)
        with zipfile.ZipFile(self.output) as archive:
            info = archive.getinfo('tdesktop/source-link')
            self.assertEqual((info.external_attr >> 16) & 0o170000, 0o120000)
            self.assertEqual(archive.read(info), b'file.cpp')

    def test_escaping_symlink_is_rejected(self):
        target_oid = subprocess.check_output(
            ['git', '-C', str(self.child), 'hash-object', '-w', '--stdin'], input=b'../../outside'
        ).decode().strip()
        git(self.child, 'update-index', '--add', '--cacheinfo', f'120000,{target_oid},source-link')
        child_pin = commit_index(self.child)
        git(self.root, 'update-index', '--cacheinfo', f'160000,{child_pin},tdesktop')
        parent_pin = commit(self.root)
        with self.assertRaisesRegex(source.SourceExportError, 'link'):
            source.export(self.root, self.output, parent_pin)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
