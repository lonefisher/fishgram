import importlib.util
import unittest
from pathlib import Path
from unittest import mock

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

    def test_parent_and_source_ci_failures_are_recorded(self):
        source = {'head': {'sha': 'a' * 40}, 'html_url': 'https://github.com/lonefisher/tdesktop/pull/3'}
        parent = {'head': {'sha': 'b' * 40}, 'html_url': 'https://github.com/lonefisher/fishgram/pull/4'}
        responses = [
            {'check_runs': [{'name': 'source compile', 'conclusion': 'success'}]},
            {'check_runs': [{'name': 'parent compile', 'conclusion': 'failure'}]}]
        with mock.patch.object(bot, 'api', side_effect=responses), mock.patch.object(bot, 'issue_once') as issue:
            bot.record_candidate_failures('v7.3.0', source, parent)
        issue.assert_called_once()
        self.assertIn('parent compile', issue.call_args.args[2])
        self.assertIn(source['html_url'], issue.call_args.args[2])
        self.assertIn(parent['html_url'], issue.call_args.args[2])

    def test_successful_candidates_have_no_failure_issue(self):
        source = {'head': {'sha': 'a' * 40}, 'html_url': 'source'}
        parent = {'head': {'sha': 'b' * 40}, 'html_url': 'parent'}
        with mock.patch.object(bot, 'api', return_value={'check_runs': []}), mock.patch.object(bot, 'issue_once') as issue:
            bot.record_candidate_failures('v7.3.0', source, parent)
        issue.assert_not_called()

    def test_issue_deduplication_reads_subsequent_pages(self):
        first = [{'title': 'unrelated', 'html_url': 'ignored'} for _ in range(100)]
        wanted = {'title': 'Upstream v7.3.0: conflicts need review', 'html_url': 'existing'}
        with mock.patch.object(bot, 'api', side_effect=[first, [wanted]]) as api:
            self.assertEqual(bot.issue_once(bot.SOURCE, wanted['title'], 'new body'), 'existing')
        self.assertEqual(api.call_count, 2)
        self.assertTrue(all(call.args[0].startswith('repos/lonefisher/tdesktop/issues?') for call in api.call_args_list))


if __name__ == '__main__':
    unittest.main()
