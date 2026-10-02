"""Create review candidates only. No merge, release, signing or feed promotion."""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlencode

PRODUCT = 'lonefisher/fishgram'
SOURCE = 'lonefisher/tdesktop'
UPSTREAM = 'telegramdesktop/tdesktop'


def stable_tag(release):
    tag = release.get('tag_name', '')
    if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Not a stable official tag.')
    return tag


def newer(target, current):
    parse = lambda tag: tuple(map(int, stable_tag({'tag_name': tag})[1:].split('.')))
    return parse(target) > parse(current)


def upgrade_branch(tag):
    return 'upgrade/' + stable_tag({'tag_name': tag})


def allowed_mutation(method, endpoint):
    return method in ('POST', 'PATCH') and bool(re.fullmatch(r'repos/lonefisher/(?:fishgram|tdesktop)/(?:issues|pulls)(?:/\d+)?', endpoint))


def push_arguments(branch):
    if not re.fullmatch(r'upgrade/v\d+\.\d+\.\d+', branch):
        raise ValueError('Only upgrade candidate refs may be pushed.')
    return ['push', 'origin', 'HEAD:refs/heads/' + branch]


def api(endpoint, method='GET', body=None):
    if method != 'GET' and not allowed_mutation(method, endpoint):
        raise ValueError('Denied automation endpoint.')
    arguments = ['gh', 'api', endpoint, '--method', method]
    if body is not None:
        arguments += ['--input', '-']
    result = subprocess.run(arguments, input=json.dumps(body) if body is not None else None, text=True, capture_output=True)
    if result.returncode:
        # Never dump CLI output or environment; errors can contain authentication context.
        raise RuntimeError(f'GitHub request failed ({result.returncode}): {method} {endpoint.split("?")[0]}')
    return json.loads(result.stdout) if result.stdout.strip() else None


def git(root, *args, check=True):
    result = subprocess.run(['git', '-c', 'core.longpaths=true', '-C', str(root), *args], text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError('Git candidate operation failed: ' + args[0])
    return result


def issue_once(repo, title, body):
    issues = api(f'repos/{repo}/issues?state=all&per_page=100')
    existing = next((item for item in issues if item['title'] == title and 'pull_request' not in item), None)
    if existing:
        return existing['html_url']
    return api(f'repos/{repo}/issues', 'POST', {'title': title, 'body': body})['html_url']


def existing_pr(repo, branch):
    query = urlencode({'state': 'all', 'head': 'lonefisher:' + branch, 'per_page': 100})
    items = api(f'repos/{repo}/pulls?{query}')
    return next(iter(items), None)


def create_pr(repo, branch, base, title, body):
    existing = existing_pr(repo, branch)
    if existing:
        return existing
    return api(f'repos/{repo}/pulls', 'POST', {'head': branch, 'base': base, 'title': title, 'body': body, 'draft': True})


def main():
    # /installation/repositories is specific to installation tokens, not a PAT.
    installed = api('installation/repositories')
    if {item['full_name'] for item in installed['repositories']} != {PRODUCT, SOURCE}:
        raise RuntimeError('Upgrade App must be scoped to exactly the two FishGram repositories.')
    root = Path(__file__).resolve().parents[1]
    recipe = json.loads((root / 'fishgram.json').read_text(encoding='utf-8-sig'))
    tag = stable_tag(api(f'repos/{UPSTREAM}/releases/latest'))
    if not newer(tag, recipe['upstreamTag']):
        print('No new official stable release.')
        return
    branch = upgrade_branch(tag)
    source_pr = existing_pr(SOURCE, branch)
    parent_pr = existing_pr(PRODUCT, branch)
    if source_pr and parent_pr:
        checks = api(f'repos/{SOURCE}/commits/{source_pr["head"]["sha"]}/check-runs')
        failed = [item['name'] for item in checks['check_runs'] if item.get('conclusion') in ('failure', 'timed_out', 'cancelled', 'action_required')]
        if failed:
            issue_once(PRODUCT, f'Upstream {tag}: candidate CI failed', 'Candidate remains unpromoted.\n\n' + source_pr['html_url'] + '\n' + parent_pr['html_url'] + '\n\nFailed checks:\n' + '\n'.join('- ' + name for name in failed))
        print('Existing linked candidates retained.')
        return
    with tempfile.TemporaryDirectory(prefix='fishgram-upgrade-') as temporary:
        source = Path(temporary) / 'source'
        subprocess.run(['git', 'clone', '--filter=blob:none', '--no-tags', '--branch', 'custom/main', f'https://github.com/{SOURCE}.git', str(source)], check=True)
        git(source, 'config', 'user.name', 'FishGram upgrade candidate')
        git(source, 'config', 'user.email', 'fishgram-upgrade@users.noreply.github.com')
        git(source, 'remote', 'add', 'upstream', f'https://github.com/{UPSTREAM}.git')
        git(source, 'fetch', '--filter=blob:none', 'upstream', f'refs/tags/{tag}')
        target = git(source, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()
        if source_pr:
            candidate = source_pr['head']['sha']
        else:
            git(source, 'switch', '-c', branch)
            merged = git(source, 'merge', '--no-ff', '--no-commit', target, check=False)
            if merged.returncode:
                conflicts = git(source, 'diff', '--name-only', '--diff-filter=U').stdout.splitlines()
                if not conflicts:
                    raise RuntimeError('Merge failed before conflict classification.')
                issue_once(SOURCE, f'Upstream {tag}: conflicts need review', f'Official target: `{target}`. No customized code was overwritten.\n\nConflicting files:\n' + '\n'.join('- `' + item + '`' for item in conflicts))
                print('Conflicts recorded; candidate promotion stopped.')
                return
            git(source, 'commit', '-m', f'Merge official {tag} for FishGram review')
            git(source, *push_arguments(branch))
            candidate = git(source, 'rev-parse', 'HEAD').stdout.strip()
            source_pr = create_pr(SOURCE, branch, 'custom/main', f'Upgrade FishGram source to {tag}', f'Official target `{target}`. Candidate source `{candidate}`. Review customizations and toolchain; do not release. Parent candidate follows separately.')
        git(root, 'switch', '-c', branch)
        recipe.update(upstreamTag=tag, upstreamCommit=target, upstreamVersion=tag[1:], revision=recipe['revision'] + 1, releaseReady=False)
        (root / 'fishgram.json').write_text(json.dumps(recipe, indent=2) + '\n', encoding='utf-8')
        git(root, 'update-index', '--cacheinfo', f'160000,{candidate},tdesktop')
        git(root, 'add', '--', 'fishgram.json')
        git(root, 'config', 'user.name', 'FishGram upgrade candidate')
        git(root, 'config', 'user.email', 'fishgram-upgrade@users.noreply.github.com')
        git(root, 'commit', '-m', f'Propose FishGram build input for {tag}')
        git(root, *push_arguments(branch))
        parent_pr = create_pr(PRODUCT, branch, 'main', f'Upgrade FishGram recipe to {tag}', 'Depends on ' + source_pr['html_url'] + f'.\n\nSource candidate `{candidate}`. Merge source first, then refresh this pointer and revalidate. No release or update feed promotion is performed.')
        api(f'repos/{SOURCE}/pulls/{source_pr["number"]}', 'PATCH', {'body': source_pr['body'] + '\n\nParent candidate: ' + parent_pr['html_url']})
        print('Linked draft upgrade candidates created.')


if __name__ == '__main__':
    main()
