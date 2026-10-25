import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from dependency_cache_key import cache_key, preparation_files


def workflow_key_step(path):
    jobs = path.read_text(encoding='utf-8').split('jobs:\n', 1)[1]
    for job in re.split(r'(?m)^  [\w-]+:\s*\n', jobs)[1:]:
        for step in re.split(r'(?m)^      - ', job)[1:]:
            if re.search(r'^        id: key$', step, re.MULTILINE):
                runner = re.search(r'^    runs-on: (.+)$', job, re.MULTILINE).group(1)
                script = step.split('        run: |\n', 1)[1]
                return runner, textwrap.dedent(script).strip()
    raise ValueError(f'No dependency key step in {path.name}')


class DependencyCacheKeyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.recipe = json.loads((ROOT / 'fishgram.json').read_text(encoding='utf-8-sig'))
        self.write_recipe(self.recipe)
        for name in preparation_files(ROOT) + ['tools/dependency_cache_key.py']:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def write_recipe(self, recipe):
        (self.root / 'fishgram.json').write_text(json.dumps(recipe), encoding='utf-8')

    def key(self, runner='windows-2022'):
        return cache_key(self.root, runner)

    def test_release_changes_reuse_dependencies(self):
        original = self.key()
        changes = {
            'revision': self.recipe['revision'] + 1,
            'channel': 'stable' if self.recipe['channel'] == 'beta' else 'beta',
            'autoUpdate': not self.recipe['autoUpdate'],
            'releaseReady': not self.recipe['releaseReady'],
            'upstreamVersion': '7.3.4',
            'upstreamTag': 'v7.3.4',
            'upstreamCommit': 'b' * 40,
            'releaseDebugInformation': 'Embedded',
            'payloadFiles': ['different-client.exe'],
            'apiId': 1,
            'apiHash': 'test-identity',
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                updated = dict(self.recipe, **{field: value})
                self.write_recipe(updated)
                self.assertEqual(self.key(), original)
        self.write_recipe(dict(self.recipe, **changes))
        self.assertEqual(self.key(), original)

    def test_toolchain_and_runner_changes_invalidate_dependencies(self):
        original = self.key()
        for field, value in [('platform', 'windows-arm64'), ('msvc', '14.45'),
                             ('windowsSdk', '10.0.26200.0'), ('qt', '6.12.0')]:
            with self.subTest(field=field):
                self.write_recipe(dict(self.recipe, **{field: value}))
                self.assertNotEqual(self.key(), original)
        self.write_recipe(self.recipe)
        self.assertNotEqual(self.key('windows-2025'), original)

    def test_preparation_content_changes_invalidate_dependencies(self):
        original = self.key()
        for name in preparation_files(self.root):
            with self.subTest(input=name):
                path = self.root / name
                content = path.read_bytes()
                path.write_bytes(content + b'\n# changed dependency preparation\n')
                self.assertNotEqual(self.key(), original)
                path.write_bytes(content)
        # This is read outside build/prepare by the real Qt 6 stage.
        angle = self.root / 'tdesktop/cmake/external/angle/qt.cmake'
        angle.write_bytes(angle.read_bytes() + b'\n# changed Qt ANGLE configuration\n')
        self.assertNotEqual(self.key(), original)

    def test_new_preparation_asset_changes_key_but_client_files_do_not(self):
        original = self.key()
        for name in ['tdesktop/Telegram/SourceFiles/core/launcher.cpp',
                     'build-modified/build-record.json',
                     'tdesktop/Telegram/build/prepare/__pycache__/prepare.cpython-313.pyc']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'non-dependency input')
        with mock.patch.dict(os.environ, FISHGRAM_API_ID='1', FISHGRAM_API_HASH='test-identity'):
            self.assertEqual(self.key(), original)
        asset = self.root / 'tdesktop/Telegram/build/prepare/breakpad/new-build-input.cmake'
        asset.write_text('set(NEW_DEPENDENCY_OPTION ON)\n', encoding='utf-8')
        self.assertNotEqual(self.key(), original)

    def test_recipe_format_and_checkout_location_do_not_change_key(self):
        original = self.key()
        (self.root / 'fishgram.json').write_text(
            json.dumps(dict(reversed(list(self.recipe.items()))), indent=4), encoding='utf-8-sig')
        self.assertEqual(self.key(), original)
        with tempfile.TemporaryDirectory() as other:
            shutil.copytree(self.root, Path(other) / 'checkout')
            self.assertEqual(cache_key(Path(other) / 'checkout', 'windows-2022'), original)

    @unittest.skipUnless(shutil.which('pwsh'), 'Windows workflow execution needs pwsh')
    def test_both_workflow_scripts_execute_and_produce_the_same_key(self):
        results = []
        for workflow in ['cloud-build.yml', 'product-candidate.yml']:
            with self.subTest(workflow=workflow):
                runner, script = workflow_key_step(ROOT / '.github/workflows' / workflow)
                output = self.root / (workflow + '.output')
                env = dict(os.environ, GITHUB_OUTPUT=str(output), PYTHONDONTWRITEBYTECODE='1')
                env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env['PATH']
                result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-Command', script],
                                        cwd=self.root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                key = cache_key(self.root, runner)
                self.assertEqual(output.read_text(encoding='utf-8'), f'key={key}\n')
                self.assertEqual(result.stdout.strip(), key)
                results.append(key)
        self.assertEqual(results[0], results[1])


if __name__ == '__main__':
    unittest.main()
