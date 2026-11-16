"""The product source no longer defines an Updater target for FishGram builds."""
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductBuildTargetTests(unittest.TestCase):
    def test_source_forces_autoupdate_off_before_an_updater_target_can_be_defined(self):
        root = (ROOT / 'tdesktop/CMakeLists.txt').read_text(encoding='utf-8')
        telegram = (ROOT / 'tdesktop/Telegram/CMakeLists.txt').read_text(encoding='utf-8')
        forced = root.index('set(DESKTOP_APP_DISABLE_AUTOUPDATE ON CACHE BOOL "Disable autoupdate." FORCE)')
        self.assertNotIn('FISHGRAM_BUILD_PACKER', root)
        self.assertNotIn('FISHGRAM_BUILD_PACKER', telegram)
        updater = telegram.index('    add_executable(Updater WIN32)')
        guard = telegram.rfind('if (NOT DESKTOP_APP_DISABLE_AUTOUPDATE', 0, updater)
        self.assertGreater(guard, 0)
        self.assertLess(forced, len(root))

    def test_product_candidate_does_not_build_updater_or_an_update_package(self):
        script = (ROOT / 'tools/build-telegram.ps1').read_text(encoding='utf-8')
        candidate = script.index('if ($ProductCandidate) {')
        following = script.index('    $disabled = if ($autoUpdate)', candidate)
        block = script[candidate:following]
        self.assertNotIn('Updater', block)
        self.assertNotIn('Packer', block)
        self.assertNotIn('manifest.min.json', block)
        self.assertIn("$autoUpdate = $false", block)
        targets = script.index("$targets = @('Telegram')")
        target_block = script[targets:script.index('& $toolchain.cmake --build', targets)]
        self.assertIn('if (-not $ProductCandidate -and $autoUpdate)', target_block)
        self.assertNotIn("+= 'Packer'", target_block)

    def test_recipe_is_v744_r14_without_updater_payload(self):
        recipe = json.loads((ROOT / 'fishgram.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(recipe['upstreamTag'], 'v7.4.4')
        self.assertEqual(recipe['upstreamVersion'], '7.4.4')
        self.assertEqual(recipe['upstreamCommit'], 'bf4f714036f21c750d535dab199c811b3f9bfdd4')
        self.assertEqual(recipe['revision'], 14)
        self.assertFalse(recipe['autoUpdate'])
        self.assertEqual(recipe['payloadFiles'], ['Telegram.exe'])
        self.assertNotIn('Updater.exe', recipe['payloadFiles'])
        self.assertEqual(recipe['product'], 'FishGram')

    def test_build_workflows_do_not_run_update_package_signing_or_manifest_steps(self):
        cloud = (ROOT / '.github/workflows/cloud-build.yml').read_text(encoding='utf-8')
        candidate = (ROOT / '.github/workflows/product-candidate.yml').read_text(encoding='utf-8')
        self.assertNotIn('-TestUpdateSystem', cloud)
        self.assertNotIn('test-update-verify.ps1', cloud)
        self.assertNotIn('test-update-policy.ps1', cloud)
        self.assertNotIn('test-update-transaction.ps1', cloud)
        self.assertIn('./tools/build-telegram.ps1 -TestIdentity -Parallel 4', cloud)
        self.assertNotIn('verify-trust', candidate)
        self.assertNotIn('manifest.min.json', candidate)
        self.assertNotIn('Updater', candidate)
        self.assertIn('./tools/build-telegram.ps1 -ProductCandidate -Parallel 4', candidate)
