"""Run the workflows' checkout command against real nested Git fixtures."""
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load_tool('release_gate')
source = load_tool('export_source')


def recursive_checkouts():
    for path in sorted((ROOT / '.github/workflows').glob('*.yml')):
        steps = re.split(r'(?m)^      - ', path.read_text(encoding='utf-8'))[1:]
        for index, step in enumerate(steps):
            if step.startswith('uses: actions/checkout@') and 'submodules: recursive' in step:
                yield path.name, steps[index + 1]


class RecursiveCheckoutTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output(
            ['git', '-c', 'core.longpaths=true', '-c', 'protocol.file.allow=always',
             '-C', str(root), *args], text=True, stderr=subprocess.PIPE).strip()

    def commit(self, root):
        self.git(root, 'add', '.')
        self.git(root, 'commit', '-qm', 'Synthetic checkout fixture')
        return self.git(root, 'rev-parse', 'HEAD')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='fishgram-recursive-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        leaf, quickjs, source_repo, self.parent = [self.base / name for name in
                                                  ('leaf', 'quickjs', 'source', 'parent')]
        for repo in (leaf, quickjs, source_repo, self.parent):
            repo.mkdir()
            self.git(repo, 'init', '-q')
            self.git(repo, 'config', 'user.name', 'Synthetic checkout fixture')
            self.git(repo, 'config', 'user.email', 'fixture@example.invalid')
        (leaf / 'fixture.txt').write_text('pinned test262 input', encoding='utf-8')
        self.leaf_pin = self.commit(leaf)
        self.git(quickjs, 'submodule', 'add', '-q', str(leaf), 'test262')
        self.git(quickjs, 'config', '-f', '.gitmodules', 'submodule.test262.update', 'none')
        self.commit(quickjs)
        for name in ('LICENSE', 'LEGAL'):
            (source_repo / name).write_text('Synthetic license fixture', encoding='utf-8')
        self.git(source_repo, 'submodule', 'add', '-q', str(quickjs), 'deps/quickjs')
        self.commit(source_repo)
        self.git(self.parent, 'submodule', 'add', '-q', str(source_repo), 'tdesktop')
        for name, content in {
            'fishgram.json': json.dumps({'product': 'FishGram', 'upstreamVersion': '7.4.1', 'revision': 1}),
            'AGENTS.md': 'Synthetic developer entry',
            'tools/build-telegram.ps1': 'Synthetic build entry',
            'config/license-sources.json': json.dumps({'schemaVersion': 1, 'officialSources': []}),
        }.items():
            path = self.parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        self.parent_pin = self.commit(self.parent)
        # A newer branch tip must not replace the gitlink's fixed commit.
        (leaf / 'fixture.txt').write_text('unpinned branch tip', encoding='utf-8')
        self.leaf_tip = self.commit(leaf)

    def checkout(self, name):
        root = self.base / name
        self.git(self.base, 'clone', '-q', '--recurse-submodules', str(self.parent), str(root))
        return root

    def run_workflow_checkout(self, root, step):
        # Execute the real workflow command, not a duplicate test-only updater.
        # Requiring the immediate next step also protects ordering before gates.
        self.assertNotRegex(step, r'(?m)^        (?:if|continue-on-error):')
        commands = re.findall(r'^          (git .+)$', step, re.MULTILINE)
        self.assertEqual(len(commands), 1, 'Recursive checkout needs an explicit pinned Git update step')
        self.assertIn('if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }', step)
        return self.git(root, *shlex.split(commands[0])[1:])

    def workflows(self):
        workflows = list(recursive_checkouts())
        self.assertTrue(workflows, 'No recursive workflow checkouts were exercised')
        return workflows

    def test_skipped_recursive_pin_is_required_by_gate_and_export(self):
        for name, step in self.workflows():
            with self.subTest(workflow=name):
                root = self.checkout(name)
                quickjs = root / 'tdesktop/deps/quickjs'
                status = self.git(root, 'submodule', 'status', '--recursive')
                self.assertIn('-' + self.leaf_pin + ' tdesktop/deps/quickjs/test262', status)
                with self.assertRaises(gate.GateError):
                    gate.check_checkout(root, self.parent_pin)
                archive_path = self.base / (name + '.zip')
                with self.assertRaises(source.SourceExportError):
                    source.export(root, archive_path, self.parent_pin)
                self.assertFalse(archive_path.exists())
                self.run_workflow_checkout(root, step)
                self.assertEqual(self.git(quickjs / 'test262', 'rev-parse', 'HEAD'), self.leaf_pin)
                self.assertEqual(self.git(root, 'status', '--porcelain'), '')
                gate.check_checkout(root, self.parent_pin)
                manifest = source.export(root, archive_path, self.parent_pin)
                self.assertIn({'path': 'tdesktop/deps/quickjs/test262', 'commit': self.leaf_pin},
                              manifest['repositories'])
                with zipfile.ZipFile(archive_path) as archive:
                    self.assertEqual(archive.read('tdesktop/deps/quickjs/test262/fixture.txt'),
                                     b'pinned test262 input')

    def test_local_custom_update_command_is_not_executed(self):
        for name, step in self.workflows():
            with self.subTest(workflow=name):
                root = self.checkout(name)
                self.run_workflow_checkout(root, step)
                quickjs = root / 'tdesktop/deps/quickjs'
                self.git(quickjs / 'test262', 'checkout', '-q', self.leaf_tip)
                marker = self.base / (name + '.custom-update-ran')
                self.git(quickjs, 'config', 'submodule.test262.update',
                         '!echo unexpected > "' + marker.as_posix() + '"')
                self.run_workflow_checkout(root, step)
                self.assertFalse(marker.exists(), 'A local custom update command ran')
                self.assertEqual(self.git(quickjs / 'test262', 'rev-parse', 'HEAD'), self.leaf_pin)
                gate.check_checkout(root, self.parent_pin)

    def test_conflicting_local_change_is_not_overwritten(self):
        for name, step in self.workflows():
            with self.subTest(workflow=name):
                root = self.checkout(name)
                self.run_workflow_checkout(root, step)
                leaf = root / 'tdesktop/deps/quickjs/test262'
                self.git(leaf, 'checkout', '-q', self.leaf_tip)
                changed = leaf / 'fixture.txt'
                changed.write_text('preserve local changes', encoding='utf-8')
                with self.assertRaises(subprocess.CalledProcessError):
                    self.run_workflow_checkout(root, step)
                self.assertEqual(changed.read_text(encoding='utf-8'), 'preserve local changes')
                self.assertEqual(self.git(leaf, 'rev-parse', 'HEAD'), self.leaf_tip)
                with self.assertRaises(gate.GateError):
                    gate.check_checkout(root, self.parent_pin)
