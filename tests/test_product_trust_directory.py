import subprocess
import tempfile
import unittest
from pathlib import Path


class ProductTrustDirectoryTests(unittest.TestCase):
    def test_product_build_accepts_public_files_and_rejects_private_material(self):
        source = (Path(__file__).parents[1] / 'tools/build-telegram.ps1').read_text()
        start = source.index('        foreach ($name', source.index('    if ($ProductCandidate) {'))
        end = source.index('\n    }', start)
        block = source[start:end]
        with tempfile.TemporaryDirectory(prefix='fishgram-public-trust-') as directory:
            trust = Path(directory)
            for name in ('root-public.pem', 'issuer-public.pem', 'manifest.min.json', 'manifest.sig'):
                (trust / name).write_text('public fixture')
            harness = trust.parent / (trust.name + '.ps1')
            try:
                harness.write_text("param($trust)\n$ErrorActionPreference='Stop'\n"
                                   "Set-StrictMode -Version Latest\n" + block)
                def run():
                    return subprocess.run(['pwsh', '-NoProfile', '-File', str(harness), str(trust)],
                                          capture_output=True, text=True)
                accepted = run()
                self.assertEqual(accepted.returncode, 0, accepted.stderr)
                for name in ('private.pem', 'fixture.json'):
                    with self.subTest(name=name):
                        forbidden = trust / name
                        forbidden.write_text('synthetic forbidden file')
                        rejected = run()
                        self.assertNotEqual(rejected.returncode, 0)
                        self.assertIn('forbidden test or private material', rejected.stderr)
                        forbidden.unlink()
                (trust / 'manifest.sig').unlink()
                missing = run()
                self.assertNotEqual(missing.returncode, 0)
                self.assertIn('Product update trust is incomplete', missing.stderr)
            finally:
                harness.unlink(missing_ok=True)
