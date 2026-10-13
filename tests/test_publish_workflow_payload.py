import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class PublishWorkflowPayloadTests(unittest.TestCase):
    def run_selection(self, name, *, exists=True):
        workflow = (Path(__file__).parents[1] / '.github/workflows/publish-release.yml').read_text()
        start = workflow.index("          $packageDir =")
        end = workflow.index("          $args = @('prepare'", start)
        block = '\n'.join(line[10:] for line in workflow[start:end].splitlines())
        with tempfile.TemporaryDirectory(prefix='fishgram-publish-selection-') as temp:
            root = Path(temp)
            package = root / 'candidate/package'
            signed = root / 'signed'
            package.mkdir(parents=True)
            signed.mkdir()
            (package / 'FishGram-7.2.9-r8-windows-x64-candidate.zip').write_bytes(b'fixture')
            (signed / 'signing-record.json').write_text(json.dumps({'package': {'name': name}}))
            if exists and '/' not in name and '\\' not in name:
                (signed / name).write_bytes(b'fixture')
            harness = root / 'select.ps1'
            harness.write_text("$ErrorActionPreference='Stop'\n" + block +
                               "\nWrite-Output $signedPackages[0].Name\n")
            result = subprocess.run(['pwsh', '-NoProfile', '-File', str(harness)],
                                    env={**os.environ, 'RUNNER_TEMP': str(root)},
                                    capture_output=True, text=True)
            return result.returncode, result.stdout.strip()

    def test_production_stable_and_beta_extensionless_package_is_selected(self):
        for suffix in ('', '-beta'):
            name = 'fishgram-update-win-x64-7002009-r8' + suffix
            with self.subTest(name=name):
                self.assertEqual(self.run_selection(name), (0, name))

    def test_invalid_or_missing_recorded_package_is_rejected(self):
        for name in ('../escape', 'signed.tupdate', 'fishgram-update-win-x64-7002009-r0'):
            with self.subTest(name=name):
                self.assertNotEqual(self.run_selection(name)[0], 0)
        self.assertNotEqual(self.run_selection('fishgram-update-win-x64-7002009-r8', exists=False)[0], 0)
