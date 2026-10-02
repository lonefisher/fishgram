import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('upstream_candidate', Path(__file__).resolve().parents[1] / 'tools/upstream_candidate.py')
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


class UpgradePolicyTests(unittest.TestCase):
    def test_stable_release_only(self):
        self.assertEqual(bot.stable_tag({'tag_name': 'v7.3.0', 'draft': False, 'prerelease': False}), 'v7.3.0')
        for release in [{'tag_name': 'v7.3.0', 'prerelease': True}, {'tag_name': 'v7.3.0', 'draft': True}, {'tag_name': 'v7.3.0-beta'}, {'tag_name': '../main'}]:
            with self.assertRaises(ValueError):
                bot.stable_tag(release)

    def test_newer_stable_and_branch_name(self):
        self.assertTrue(bot.newer('v7.3.0', 'v7.2.9'))
        self.assertFalse(bot.newer('v7.2.9', 'v7.2.9'))
        self.assertFalse(bot.newer('v7.2.8', 'v7.2.9'))
        self.assertEqual(bot.upgrade_branch('v7.3.0'), 'upgrade/v7.3.0')

    def test_mutation_endpoints_have_no_release_or_main_write(self):
        allowed = [('POST', 'repos/lonefisher/fishgram/issues'), ('POST', 'repos/lonefisher/tdesktop/pulls'), ('PATCH', 'repos/lonefisher/fishgram/pulls/1')]
        for method, endpoint in allowed:
            self.assertTrue(bot.allowed_mutation(method, endpoint))
        for endpoint in ['repos/lonefisher/fishgram/releases', 'repos/lonefisher/fishgram/pages', 'repos/lonefisher/fishgram/git/refs/heads/main', 'repos/third/repo/issues', 'repos/lonefisher/tdesktop/pulls/1/merge']:
            self.assertFalse(bot.allowed_mutation('POST', endpoint))
            self.assertFalse(bot.allowed_mutation('PUT', endpoint))

    def test_never_push_protected_branch(self):
        for branch in ['main', 'custom/main', 'gh-pages', 'upgrade/../main', 'upgrade/v7.3.0:main']:
            with self.assertRaises(ValueError):
                bot.push_arguments(branch)
        self.assertEqual(bot.push_arguments('upgrade/v7.3.0')[-1], 'HEAD:refs/heads/upgrade/v7.3.0')


if __name__ == '__main__':
    unittest.main()
