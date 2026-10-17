"""Create review candidates only. No merge, release, signing or feed promotion."""
import base64
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
    if repo not in (PRODUCT, SOURCE):
        raise ValueError('Only FishGram repositories may receive candidate records.')
    for page in range(1, 101):
        issues = api(f'repos/{repo}/issues?state=all&per_page=100&page={page}')
        existing = next((item for item in issues if item['title'] == title and 'pull_request' not in item), None)
        if existing:
            return existing['html_url']
        if len(issues) < 100:
            break
    else:
        raise RuntimeError('Issue pagination limit reached; refusing a possible duplicate.')
    return api(f'repos/{repo}/issues', 'POST', {'title': title, 'body': body})['html_url']


def record_candidate_failures(tag, source_pr, parent_pr):
    failed = []
    for repo, candidate in ((SOURCE, source_pr), (PRODUCT, parent_pr)):
        for page in range(1, 101):
            checks = api(f'repos/{repo}/commits/{candidate["head"]["sha"]}/check-runs?per_page=100&page={page}')
            entries = checks['check_runs']
            failed += [repo + ': ' + item['name'] for item in entries
                       if item.get('conclusion') in ('failure', 'timed_out', 'cancelled', 'action_required', 'stale')]
            if len(entries) < 100:
                break
        else:
            raise RuntimeError('Candidate check pagination limit reached.')
    if failed:
        issue_once(PRODUCT, f'Upstream {tag}: candidate CI failed',
                   'Candidate remains unpromoted.\n\n' + source_pr['html_url'] + '\n'
                   + parent_pr['html_url'] + '\n\nFailed checks:\n'
                   + '\n'.join('- ' + name for name in failed))


def existing_pr(repo, branch):
    query = urlencode({'state': 'all', 'head': 'lonefisher:' + branch, 'per_page': 100})
    items = api(f'repos/{repo}/pulls?{query}')
    return next(iter(items), None)


def create_pr(repo, branch, base, title, body):
    existing = existing_pr(repo, branch)
    if existing:
        return existing
    return api(f'repos/{repo}/pulls', 'POST', {'head': branch, 'base': base, 'title': title, 'body': body, 'draft': True})


def link_candidates(source_pr, parent_pr):
    body = source_pr.get('body') or ''
    link = 'Parent candidate: ' + parent_pr['html_url']
    if link not in body:
        api(f'repos/{SOURCE}/pulls/{source_pr["number"]}', 'PATCH', {'body': body + '\n\n' + link})


def clone_parent_candidate(temporary):
    parent = Path(temporary) / 'parent'
    subprocess.run(['git', '-c', 'core.longpaths=true', 'clone', '--filter=blob:none',
                    '--no-tags', '--branch', 'main', f'https://github.com/{PRODUCT}.git',
                    str(parent)], check=True)
    return parent


def remote_candidate(root, branch):
    push_arguments(branch)  # Validate before using a ref supplied to Git.
    result = git(root, 'ls-remote', '--exit-code', '--heads', 'origin', 'refs/heads/' + branch, check=False)
    if result.returncode == 2:
        return None
    if result.returncode:
        raise RuntimeError('Cannot inspect existing candidate branch.')
    git(root, 'fetch', 'origin', 'refs/heads/' + branch)
    return git(root, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()


def resume_source_candidate(root, branch, target, expected_head=None):
    candidate = remote_candidate(root, branch)
    if expected_head is not None and candidate != expected_head:
        raise RuntimeError('Existing source PR head changed; manual review required.')
    if candidate:
        for ancestor in (target, 'origin/custom/main'):
            if git(root, 'merge-base', '--is-ancestor', ancestor, candidate, check=False).returncode:
                raise RuntimeError('Existing source candidate requires manual review.')
    return candidate


def resume_parent_candidate(root, branch, recipe, candidate):
    existing = remote_candidate(root, branch)
    if existing:
        stored = json.loads(git(root, 'show', existing + ':fishgram.json').stdout)
        pointer = git(root, 'rev-parse', existing + ':tdesktop').stdout.strip()
        if stored != recipe or pointer != candidate:
            raise RuntimeError('Existing parent candidate inputs differ; manual review required.')
    return existing


def main():
    # /installation/repositories is specific to installation tokens, not a PAT.
    installed = api('installation/repositories')
    if {item['full_name'] for item in installed['repositories']} != {PRODUCT, SOURCE}:
        raise RuntimeError('Upgrade App must be scoped to exactly the two FishGram repositories.')
    public_recipe = api(f'repos/{PRODUCT}/contents/fishgram.json?ref=main')
    if public_recipe.get('encoding') != 'base64':
        raise RuntimeError('Public main recipe encoding is unsupported.')
    recipe = json.loads(base64.b64decode(public_recipe['content']).decode('utf-8-sig'))
    tag = stable_tag(api(f'repos/{UPSTREAM}/releases/latest'))
    if not newer(tag, recipe['upstreamTag']):
        print('No new official stable release.')
        return
    branch = upgrade_branch(tag)
    source_pr = existing_pr(SOURCE, branch)
    parent_pr = existing_pr(PRODUCT, branch)
    if any(pr and pr.get('state') != 'open' for pr in (source_pr, parent_pr)):
        issue_once(PRODUCT, f'Upstream {tag}: closed candidate needs review',
                   'A previous candidate was closed or merged. Reconcile source and parent pointers manually; '
                   'the bot will not recreate or promote it.\n\n'
                   + '\n'.join(pr['html_url'] for pr in (source_pr, parent_pr) if pr))
        print('Closed candidate retained for manual review.')
        return
    if source_pr and parent_pr:
        link_candidates(source_pr, parent_pr)
        record_candidate_failures(tag, source_pr, parent_pr)
        print('Existing linked candidates retained.')
        return
    with tempfile.TemporaryDirectory(prefix='fishgram-upgrade-') as temporary:
        root = clone_parent_candidate(temporary)
        cloned_recipe = json.loads((root / 'fishgram.json').read_text(encoding='utf-8-sig'))
        if cloned_recipe != recipe:
            raise RuntimeError('Public main recipe changed during candidate setup; retry without promotion.')
        source = Path(temporary) / 'source'
        subprocess.run(['git', 'clone', '--filter=blob:none', '--no-tags', '--branch', 'custom/main', f'https://github.com/{SOURCE}.git', str(source)], check=True)
        git(source, 'config', 'user.name', 'FishGram upgrade candidate')
        git(source, 'config', 'user.email', 'fishgram-upgrade@users.noreply.github.com')
        git(source, 'remote', 'add', 'upstream', f'https://github.com/{UPSTREAM}.git')
        git(source, 'fetch', '--filter=blob:none', 'upstream', f'refs/tags/{tag}')
        target = git(source, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()
        if source_pr:
            candidate = resume_source_candidate(source, branch, target, source_pr['head']['sha'])
        else:
            candidate = resume_source_candidate(source, branch, target)
            if not candidate:
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
        recipe.update(upstreamTag=tag, upstreamCommit=target, upstreamVersion=tag[1:], revision=recipe['revision'] + 1, releaseReady=False)
        if not resume_parent_candidate(root, branch, recipe, candidate):
            git(root, 'switch', '-c', branch)
            (root / 'fishgram.json').write_text(json.dumps(recipe, indent=2) + '\n', encoding='utf-8')
            git(root, 'update-index', '--cacheinfo', f'160000,{candidate},tdesktop')
            git(root, 'add', '--', 'fishgram.json')
            git(root, 'config', 'user.name', 'FishGram upgrade candidate')
            git(root, 'config', 'user.email', 'fishgram-upgrade@users.noreply.github.com')
            git(root, 'commit', '-m', f'Propose FishGram build input for {tag}')
            git(root, *push_arguments(branch))
        parent_pr = create_pr(PRODUCT, branch, 'main', f'Upgrade FishGram recipe to {tag}', 'Depends on ' + source_pr['html_url'] + f'.\n\nSource candidate `{candidate}`. Merge source first, then refresh this pointer and revalidate. No release or update feed promotion is performed.')
        link_candidates(source_pr, parent_pr)
        print('Linked draft upgrade candidates created.')


if __name__ == '__main__':
    main()
