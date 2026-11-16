"""Product-candidate runner choice and persistent build trees.

The workflow expression is the source of truth. select_build_runner mirrors it
so the truth table can run without GitHub.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/product-candidate.yml'
PWSH = shutil.which('pwsh')
HOSTED = 'windows-2022'
SELF_HOSTED = ['self-hosted', 'Windows', 'X64', 'fishgram-builder']
RUNS_ON = (
    "${{ ((github.event_name == 'workflow_dispatch' && inputs.use_github_hosted == true) "
    "|| (github.event.pull_request.head.repo.full_name != '' "
    "&& github.event.pull_request.head.repo.full_name != github.repository)) "
    "&& 'windows-2022' || fromJSON('[\"self-hosted\",\"Windows\",\"X64\",\"fishgram-builder\"]') }}"
)


def select_build_runner(event_name, repository, use_github_hosted, head_repository):
    force_hosted = (
        (event_name == 'workflow_dispatch' and use_github_hosted is True)
        or (head_repository != '' and head_repository != repository)
    )
    if force_hosted:
        return HOSTED
    return list(SELF_HOSTED)


class RunnerSelectionTests(unittest.TestCase):
    def test_expression_matches_the_workflow_and_the_truth_table(self):
        workflow = WORKFLOW.read_text(encoding='utf-8')
        self.assertIn('runs-on: ' + RUNS_ON, workflow)
        header = workflow.split('permissions:', 1)[0]
        self.assertNotIn('pull_request:', header)
        self.assertIn('use_github_hosted:', workflow)
        self.assertIn('type: boolean', workflow)
        self.assertIn('default: false', workflow)
        self.assertNotIn('RUNNER_PAT', workflow)
        self.assertNotIn('actions/runners', workflow)
        repository = 'lonefisher/fishgram'
        cases = (
            ('workflow_dispatch', False, '', SELF_HOSTED),
            ('workflow_dispatch', True, '', HOSTED),
            ('push', False, '', SELF_HOSTED),
            ('push', True, '', SELF_HOSTED),
            ('pull_request', False, 'someone/fishgram', HOSTED),
            ('pull_request', False, repository, SELF_HOSTED),
            ('pull_request_target', False, 'someone/fishgram', HOSTED),
            ('workflow_dispatch', False, 'someone/fishgram', HOSTED),
        )
        for event, hosted, head, expected in cases:
            with self.subTest(event=event, hosted=hosted, head=head):
                self.assertEqual(select_build_runner(event, repository, hosted, head), expected)

    def test_hosted_cache_path_is_unchanged_and_self_hosted_keeps_trees(self):
        workflow = WORKFLOW.read_text(encoding='utf-8')
        self.assertIn("--runner-label windows-2022", workflow)
        self.assertIn('./tools/build-telegram.ps1 -ProductCandidate -Parallel 4', workflow)
        self.assertNotIn('Updater', workflow)
        for phrase in (
            "runner.environment != 'self-hosted'",
            '-Direction Rescue',
            './tools/persist-build-tree.ps1 -Direction Attach',
            '-Direction Detach',
            'Prepare dependencies on the build PC',
            r"FISHGRAM_PERSISTENT_ROOT: 'C:\fishgram-ci\fishgram\persistent'",
        ):
            self.assertIn(phrase, workflow)
        hosted_only = (
            'Restore exact trusted dependencies',
            'Prepare dependencies when exact trusted cache is absent',
            'Pack dependencies and check compressed size before upload',
            'Save cache only from maintainer-reviewed main',
        )
        for name in hosted_only:
            step = workflow.split(f'name: {name}', 1)[1].split('\n      - ', 1)[0]
            self.assertIn("runner.environment != 'self-hosted'", step, name)
        common = (ROOT / 'tools/common.ps1').read_text(encoding='utf-8')
        self.assertNotIn('-latest', common)
        self.assertIn("-vcvars_ver=' + $Recipe.msvc", common)
        self.assertIn('Select-FishGramVisualStudio', common)


@unittest.skipUnless(PWSH, 'PowerShell tests need pwsh')
class PersistentTreeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'ws'
        self.persistent = self.root / 'keep'
        self.workspace.mkdir()
        self.env = dict(os.environ)
        self.env['GITHUB_WORKSPACE'] = str(self.workspace)
        self.env['FISHGRAM_PERSISTENT_ROOT'] = str(self.persistent)
        self.env['PYTHONDONTWRITEBYTECODE'] = '1'

    def run_ps(self, script, env=None, check=True):
        result = subprocess.run(
            [PWSH, '-NoProfile', '-NonInteractive', '-Command', script],
            cwd=ROOT, env=env or self.env, capture_output=True, text=True)
        if check and result.returncode != 0:
            self.fail(result.stderr or result.stdout)
        return result

    def test_scripts_parse(self):
        script = textwrap.dedent('''
            $ErrorActionPreference = 'Stop'
            foreach ($name in @('tools/common.ps1', 'tools/persist-build-tree.ps1')) {
                $errors = $null
                $tokens = $null
                [void][System.Management.Automation.Language.Parser]::ParseFile(
                    (Join-Path $env:ROOT $name), [ref]$tokens, [ref]$errors)
                if ($errors) { throw ($name + ' ' + $errors[0].ToString()) }
            }
        ''')
        env = dict(self.env)
        env['ROOT'] = str(ROOT)
        self.run_ps(script, env)

    def test_newer_visual_studio_without_14_44_is_skipped(self):
        vs2026 = self.root / 'VS2026'
        vs2022 = self.root / 'VS2022'
        (vs2026 / 'VC/Tools/MSVC/14.50.35717').mkdir(parents=True)
        (vs2022 / 'VC/Tools/MSVC/14.44.35207').mkdir(parents=True)
        (vs2026 / 'VC/Tools/MSVC/14.44.99999').mkdir(parents=True)
        script = textwrap.dedent('''
            $ErrorActionPreference = 'Stop'
            . (Join-Path $env:ROOT 'tools/common.ps1')
            $skipped = Select-FishGramVisualStudio -ToolsetPrefix '14.44' -Installations @(
                $env:VS2026ONLY, $env:VS2022)
            if ($null -eq $skipped -or $skipped.Installation -ne $env:VS2022) {
                throw 'Install without 14.44 was not skipped.'
            }
            if (@($skipped.Versions | ForEach-Object { $_.Name }) -notcontains '14.44.35207') {
                throw '14.44.35207 was not selected.'
            }
            $first = Select-FishGramVisualStudio -ToolsetPrefix '14.44' -Installations @(
                $env:VS2026, $env:VS2022)
            if ($first.Installation -ne $env:VS2026) { throw 'Install that has 14.44 was skipped.' }
            $none = Select-FishGramVisualStudio -ToolsetPrefix '14.44' -Installations @($env:VS2026ONLY)
            if ($null -ne $none) { throw 'Missing toolset returned an install.' }
        ''')
        only = self.root / 'VS2026-only'
        (only / 'VC/Tools/MSVC/14.50.35717').mkdir(parents=True)
        env = dict(self.env)
        env.update(ROOT=str(ROOT), VS2026=str(vs2026), VS2022=str(vs2022), VS2026ONLY=str(only))
        self.run_ps(script, env)

    def test_trees_survive_a_workspace_clean(self):
        libraries = self.workspace / 'Libraries'
        libraries.mkdir()
        (libraries / 'marker.txt').write_text('qt', encoding='utf-8')
        build = self.workspace / 'build-modified'
        build.mkdir()
        (build / 'Telegram.exe.standin').write_bytes(b'exe')
        self.run_ps("& ./tools/persist-build-tree.ps1 -Direction Rescue")
        self.assertFalse(libraries.exists())
        self.assertTrue((self.persistent / 'Libraries/marker.txt').is_file())
        # checkout clean removes anything left in the workspace
        for child in self.workspace.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        self.run_ps("& ./tools/persist-build-tree.ps1 -Direction Attach")
        self.assertEqual((self.workspace / 'Libraries/marker.txt').read_text(encoding='utf-8'), 'qt')
        self.assertEqual((self.workspace / 'build-modified/Telegram.exe.standin').read_bytes(), b'exe')
        self.assertFalse((self.persistent / 'Libraries').exists())
        self.run_ps("& ./tools/persist-build-tree.ps1 -Direction Detach")
        self.assertFalse((self.workspace / 'Libraries').exists())
        self.assertTrue((self.persistent / 'build-modified/Telegram.exe.standin').is_file())

    def test_refuses_links_conflicts_and_a_persistent_root_inside_the_workspace(self):
        target = self.root / 'real'
        target.mkdir()
        (self.workspace / 'Libraries').symlink_to(target, target_is_directory=True)
        refused = self.run_ps("& ./tools/persist-build-tree.ps1 -Direction Detach", check=False)
        self.assertNotEqual(refused.returncode, 0, refused.stdout)
        self.assertTrue((self.workspace / 'Libraries').is_symlink())
        self.assertTrue(target.is_dir())
        (self.workspace / 'Libraries').unlink()
        (self.workspace / 'ThirdParty').mkdir()
        (self.persistent / 'ThirdParty').mkdir(parents=True)
        conflict = self.run_ps("& ./tools/persist-build-tree.ps1 -Direction Detach", check=False)
        self.assertNotEqual(conflict.returncode, 0, conflict.stdout)
        self.assertTrue((self.workspace / 'ThirdParty').is_dir())
        self.assertTrue((self.persistent / 'ThirdParty').is_dir())
        nested = dict(self.env)
        nested['FISHGRAM_PERSISTENT_ROOT'] = str(self.workspace / 'persistent')
        inside = self.run_ps("& ./tools/persist-build-tree.ps1 -Direction Attach", nested, check=False)
        self.assertNotEqual(inside.returncode, 0, inside.stdout)
        self.assertIn('must not contain', inside.stderr + inside.stdout)


if __name__ == '__main__':
    unittest.main()
