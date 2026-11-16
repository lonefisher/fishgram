"""Product candidates no longer consume an update-manifest trust bundle."""
import unittest
from pathlib import Path


class ProductCandidatePathTests(unittest.TestCase):
    def test_product_candidate_does_not_require_update_manifest_files(self):
        source = (Path(__file__).parents[1] / 'tools/build-telegram.ps1').read_text(encoding='utf-8')
        start = source.index('    if ($ProductCandidate) {\n        if ($recipe.autoUpdate)')
        end = source.index('    } else {\n        $autoUpdate', start)
        block = source[start:end]
        self.assertNotIn('Updater', block)
        self.assertNotIn('manifest.min.json', block)
        self.assertNotIn('manifest.sig', block)
        self.assertNotIn('root-public.pem', block)
        self.assertIn('cannot enable the in-app updater', block)
