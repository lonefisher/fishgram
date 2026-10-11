import importlib.util
import subprocess
import tempfile
import unittest
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location('upstream_candidate', Path(__file__).resolve().parents[1] / 'tools/upstream_candidate.py')
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


@contextmanager
def temporary_git_workspace(prefix):
    temporary = tempfile.TemporaryDirectory(prefix=prefix)
    try:
        yield temporary.name
    finally:
        for attempt in range(20):
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.25)


class UpgradePolicyTests(unittest.TestCase):
    def test_link_patch_failure_can_be_retried_without_duplicates(self):
        source = {'number': 3, 'body': None}
        parent = {'html_url': 'https://github.com/lonefisher/fishgram/pull/4'}
        with mock.patch.object(bot, 'api', side_effect=RuntimeError('request failed')):
            with self.assertRaises(RuntimeError):
                bot.link_candidates(source, parent)
        with mock.patch.object(bot, 'api') as api:
            bot.link_candidates(source, parent)
            source['body'] = api.call_args.args[2]['body']
            bot.link_candidates(source, parent)
        api.assert_called_once()

    def test_resume_pushed_source_without_rewriting_commit(self):
        with temporary_git_workspace(prefix='fishgram-upgrade-resume-') as directory:
            origin = Path(directory) / 'origin'
            origin.mkdir()
            def git(*args):
                return bot.git(origin, *args).stdout.strip()
            git('init', '--initial-branch=custom/main')
            git('config', 'user.name', 'FishGram fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (origin / 'base').write_text('base')
            git('add', '.')
            git('commit', '-m', 'base')
            base = git('rev-parse', 'HEAD')
            git('switch', '-c', 'official')
            (origin / 'upstream').write_text('official')
            git('add', '.')
            git('commit', '-m', 'official')
            target = git('rev-parse', 'HEAD')
            git('switch', '-c', 'upgrade/v7.3.0', base)
            git('merge', '--no-ff', target, '-m', 'pushed candidate')
            candidate = git('rev-parse', 'HEAD')
            checkout = Path(directory) / 'retry'
            subprocess.run(['git', 'clone', '--branch', 'custom/main', str(origin), str(checkout)],
                           check=True, capture_output=True)
            self.assertEqual(bot.resume_source_candidate(checkout, 'upgrade/v7.3.0', target), candidate)
            self.assertEqual(bot.git(checkout, 'branch', '--show-current').stdout.strip(), 'custom/main')
            self.assertEqual(bot.resume_source_candidate(checkout, 'upgrade/v7.4.0', target), None)
            with self.assertRaises(RuntimeError):
                bot.resume_source_candidate(checkout, 'upgrade/v7.3.0', 'f' * 40)

    def test_resume_pushed_parent_with_real_gitlink(self):
        import json
        recipe = {'upstreamTag': 'v7.3.0', 'revision': 9, 'releaseReady': False}
        with temporary_git_workspace(prefix='fishgram-upgrade-parent-') as directory:
            origin = Path(directory) / 'origin'
            origin.mkdir()
            def git(*args):
                return bot.git(origin, *args).stdout.strip()
            git('init', '--initial-branch=main')
            git('config', 'user.name', 'FishGram fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (origin / 'fishgram.json').write_text('{}')
            git('add', '.')
            git('commit', '-m', 'public main')
            source = git('rev-parse', 'HEAD')
            git('switch', '-c', 'upgrade/v7.3.0')
            (origin / 'fishgram.json').write_text(json.dumps(recipe))
            git('add', 'fishgram.json')
            git('update-index', '--add', '--cacheinfo', f'160000,{source},tdesktop')
            git('commit', '-m', 'pushed parent before PR failure')
            pushed = git('rev-parse', 'HEAD')
            checkout = Path(directory) / 'retry'
            subprocess.run(['git', 'clone', '--branch', 'main', str(origin), str(checkout)],
                           check=True, capture_output=True)
            self.assertEqual(bot.resume_parent_candidate(checkout, 'upgrade/v7.3.0', recipe, source), pushed)
            self.assertEqual(bot.git(checkout, 'branch', '--show-current').stdout.strip(), 'main')
            with self.assertRaises(RuntimeError):
                bot.resume_parent_candidate(checkout, 'upgrade/v7.3.0', dict(recipe, revision=10), source)
            with self.assertRaises(RuntimeError):
                bot.resume_parent_candidate(checkout, 'upgrade/v7.3.0', recipe, 'f' * 40)

    def test_parent_resume_requires_exact_recipe_and_source(self):
        import json
        recipe = {'upstreamTag': 'v7.3.0', 'revision': 9, 'releaseReady': False}
        with mock.patch.object(bot, 'remote_candidate', return_value='a' * 40), \
                mock.patch.object(bot, 'git', side_effect=[
                    subprocess.CompletedProcess([], 0, json.dumps(recipe)),
                    subprocess.CompletedProcess([], 0, 'b' * 40)]):
            self.assertEqual(bot.resume_parent_candidate('.', 'upgrade/v7.3.0', recipe, 'b' * 40), 'a' * 40)
        for stored, pointer in (({}, 'b' * 40), (recipe, 'c' * 40)):
            with mock.patch.object(bot, 'remote_candidate', return_value='a' * 40), \
                    mock.patch.object(bot, 'git', side_effect=[
                        subprocess.CompletedProcess([], 0, json.dumps(stored)),
                        subprocess.CompletedProcess([], 0, pointer)]):
                with self.assertRaises(RuntimeError):
                    bot.resume_parent_candidate('.', 'upgrade/v7.3.0', recipe, 'b' * 40)

    def test_closed_source_does_not_create_parent_or_check_ci(self):
        import base64
        import json
        responses = [
            {'repositories': [{'full_name': bot.PRODUCT}, {'full_name': bot.SOURCE}]},
            {'encoding': 'base64', 'content': base64.b64encode(json.dumps({'upstreamTag': 'v7.2.9'}).encode()).decode()},
            {'tag_name': 'v7.3.0'},
            [{'state': 'closed', 'html_url': 'source'}], []]
        with mock.patch.object(bot, 'api', side_effect=responses), \
                mock.patch.object(bot, 'issue_once') as issue, \
                mock.patch.object(bot, 'clone_parent_candidate') as clone, \
                mock.patch.object(bot, 'record_candidate_failures') as checks:
            bot.main()
        issue.assert_called_once()
        clone.assert_not_called()
        checks.assert_not_called()

    def test_parent_checkout_is_isolated_and_based_on_public_main(self):
        actual_run = subprocess.run
        with temporary_git_workspace(prefix='fishgram-upgrade-clone-') as directory:
            temporary = Path(directory)
            origin = temporary / 'origin'
            origin.mkdir()
            def git(*args):
                return actual_run(['git', '-C', str(origin), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
            git('init', '--initial-branch=main')
            git('config', 'user.name', 'FishGram fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (origin / 'fishgram.json').write_text('{"revision":8}')
            git('add', 'fishgram.json')
            git('commit', '-m', 'Public main fixture')
            git('switch', '-c', 'local-development')
            (origin / 'fishgram.json').write_text('{"revision":999}')
            def local_clone(arguments, **kwargs):
                arguments = list(arguments)
                index = arguments.index('https://github.com/lonefisher/fishgram.git')
                arguments[index] = str(origin)
                return actual_run(arguments, capture_output=True, **kwargs)
            with mock.patch.object(bot.subprocess, 'run', side_effect=local_clone):
                parent = bot.clone_parent_candidate(temporary)
            self.assertEqual((parent / 'fishgram.json').read_text(), '{"revision":8}')
            self.assertEqual(git('branch', '--show-current'), 'local-development')
            self.assertEqual((origin / 'fishgram.json').read_text(), '{"revision":999}')

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
