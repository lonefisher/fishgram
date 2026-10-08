"""Exercise the workflow's real packager with pinned temporary Git inputs."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location('candidate_gate', Path(__file__).resolve().parents[1] / 'tools/release_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class CandidatePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fishgram-candidate-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        dependency = self.base / 'dependency'
        source = self.base / 'source'
        self.root = self.base / 'root'
        for directory in (dependency, source, self.root):
            directory.mkdir()
            self.git(directory, 'init', '--quiet')
            self.git(directory, 'config', 'user.name', 'FishGram fixture')
            self.git(directory, 'config', 'user.email', 'fixture@example.invalid')
        (dependency / 'dependency.txt').write_text('pinned dependency', encoding='utf-8')
        self.commit(dependency)
        (source / 'LICENSE').write_text('GPL fixture', encoding='utf-8')
        (source / 'LEGAL').write_text('OpenSSL exception fixture', encoding='utf-8')
        self.git(source, 'submodule', 'add', '--quiet', str(dependency), 'deps/example')
        self.commit(source)
        self.recipe = {
            'product': 'FishGram', 'platform': 'windows-x64', 'upstreamVersion': '7.2.9',
            'revision': 8, 'channel': 'stable', 'msvc': '14.44', 'windowsSdk': '10.0.26100.0',
            'qt': '5.15.19', 'autoUpdate': False, 'releaseReady': False,
            'payloadFiles': ['Telegram.exe', 'Updater.exe'], 'payloadDlls': [],
        }
        (self.root / 'fishgram.json').write_text(json.dumps(self.recipe), encoding='utf-8')
        self.git(self.root, 'submodule', 'add', '--quiet', str(source), 'tdesktop')
        self.git(self.root, 'submodule', 'update', '--init', '--recursive', '--quiet')
        self.commit(self.root)
        self.build = self.root / 'build-modified/Release'
        self.build.mkdir(parents=True)
        for name in ('Telegram.exe', 'Updater.exe', 'Packer.exe'):
            (self.build / name).write_bytes(('synthetic ' + name).encode('ascii'))
        self.record = {
            'version': '7.2.9-r8', 'channel': 'stable', 'identity': 'product',
            'productCandidate': True, 'autoUpdate': True, 'recipeAutoUpdate': False,
            'testUpdateTrust': False, 'parentCommit': self.git(self.root, 'rev-parse', 'HEAD'),
            'sourceCommit': self.git(self.root / 'tdesktop', 'rev-parse', 'HEAD'),
            'toolchain': {'msvc': '14.44.35207', 'sdk': '10.0.26100.0', 'qt': '5.15.19'},
            'files': {name: self.entry(self.build / name) for name in self.recipe['payloadFiles']},
            'tools': {'Packer.exe': self.entry(self.build / 'Packer.exe')},
        }
        self.record_path = self.base / 'build-record.json'
        self.output = self.base / 'candidate'

    def git(self, directory, *args):
        return subprocess.check_output(['git', '-c', 'core.longpaths=true', '-c',
                                        'protocol.file.allow=always', '-C', str(directory), *args],
                                       text=True, stderr=subprocess.PIPE).strip()

    def commit(self, directory):
        self.git(directory, 'add', '.')
        self.git(directory, 'commit', '--quiet', '-m', 'Synthetic pinned fixture')

    def entry(self, path):
        return {'size': path.stat().st_size, 'sha256': gate.sha256_file(path)}

    def package(self):
        self.record_path.write_text(json.dumps(self.record), encoding='utf-8')
        return gate.package_candidate(self.root, self.record_path, self.output)

    def test_protected_candidate_keeps_recipe_flag_and_binds_actual_zip(self):
        manifest = self.package()
        self.assertFalse(manifest['recipeAutoUpdate'])
        self.assertTrue(manifest['autoUpdate'])
        self.assertFalse(manifest['releaseReady'])
        archive = self.output / manifest['archive']['name']
        gate.validate_manifest(manifest, self.recipe)
        gate.verify_package(manifest, archive)
        gate.verify_tool(manifest, 'Packer.exe', self.output / 'Packer.exe')
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(bundle.read('Telegram.exe'), (self.build / 'Telegram.exe').read_bytes())
            self.assertEqual(bundle.read('LICENSE'), b'GPL fixture')
            self.assertEqual(bundle.read('LEGAL'), b'OpenSSL exception fixture')
            self.assertNotIn(b'-many', bundle.read('Start-FishGram.cmd'))

    def test_test_identity_or_disposable_trust_never_creates_product_output(self):
        original = dict(self.record)
        for override in ({'identity': 'test'}, {'productCandidate': False},
                         {'testUpdateTrust': True}, {'recipeAutoUpdate': True}):
            with self.subTest(override=override):
                self.record = dict(original, **override)
                with self.assertRaises(gate.GateError):
                    self.package()
                self.assertFalse(self.output.exists())

    def test_changed_program_is_rejected_before_output_creation(self):
        (self.build / 'Telegram.exe').write_bytes(b'changed after build')
        with self.assertRaisesRegex(gate.GateError, 'differs from recorded bytes'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_build_payload_symlink_is_rejected_even_with_matching_hash(self):
        program = self.build / 'Telegram.exe'
        outside = self.base / 'outside-program'
        outside.write_bytes(program.read_bytes())
        program.unlink()
        try:
            program.symlink_to(outside)
        except OSError:
            self.skipTest('This Windows token cannot create symbolic links.')
        with self.assertRaisesRegex(gate.GateError, 'link|reparse'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_packer_symlink_is_rejected_even_with_matching_hash(self):
        packer = self.build / 'Packer.exe'
        outside = self.base / 'outside-packer'
        outside.write_bytes(packer.read_bytes())
        packer.unlink()
        try:
            packer.symlink_to(outside)
        except OSError:
            self.skipTest('This Windows token cannot create symbolic links.')
        with self.assertRaisesRegex(gate.GateError, 'link|reparse'):
            self.package()
        self.assertFalse(self.output.exists())

    def test_linked_build_directory_is_rejected_with_regular_payload_files(self):
        outside = self.base / 'outside-build'
        self.build.rename(outside)
        try:
            self.build.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('This Windows token cannot create symbolic links.')
        with self.assertRaisesRegex(gate.GateError, 'link|reparse'):
            self.package()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
