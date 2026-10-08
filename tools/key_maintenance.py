"""Verify public update trust and create a deduplicated 45-day maintenance issue."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import time

SPEC = importlib.util.spec_from_file_location('fishgram_maintenance_keys', Path(__file__).with_name('key_management.py'))
keys = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(keys)
TITLE = 'FishGram update signing trust requires renewal'
REPOSITORY = 'lonefisher/fishgram'


class MaintenanceError(ValueError):
    pass


def load_trust(root):
    root = Path(root)
    paths = [root / name for name in ('manifest.min.json', 'manifest.sig', 'root-public.pem')]
    if not all(path.is_file() for path in paths):
        raise MaintenanceError('Production public update trust is missing; configure it before enabling maintenance.')
    data = paths[0].read_bytes()
    keys.verify_manifest(data, paths[1].read_bytes(), paths[2])
    return keys._strict_json(data)


def due_items(record, now=None):
    now = int(time.time()) if now is None else now
    items = []
    revoked = set(record.get('revoked', []))
    candidates = [('root-manifest', record['expires'])]
    candidates += [(key['id'], key['expires']) for key in record['keys']
                   if key['id'] not in revoked and 'expires' in key]
    for identity, expiry in candidates:
        if expiry - now <= 45 * 86400:
            items.append({'id': identity, 'expires': expiry, 'days': (expiry - now) // 86400})
    return sorted(items, key=lambda item: (item['expires'], item['id']))


def issue_body(due, version):
    lines = ['Root-signed public manifest version: ' + str(version), '',
             'Renew the following trust before expiry; no private key is needed for this check.', '']
    # Use exact expiry, not changing day counts, to avoid daily notification churn.
    lines += ['- ' + item['id'] + ': expires at UTC epoch ' + str(item['expires']) for item in due]
    lines += ['', 'Follow [key maintenance](https://github.com/lonefisher/fishgram/blob/main/docs/KEY-MANAGEMENT.md).',
              'Root authorization must increase monotonically. Keep the root private key offline.']
    return '\n'.join(lines)


def api(method, endpoint, body=None):
    if method not in ('GET', 'POST', 'PATCH') or not re.fullmatch(
            r'repos/lonefisher/fishgram/issues(?:/[1-9][0-9]*|\?state=open&per_page=100&page=[1-9][0-9]*)?', endpoint):
        raise MaintenanceError('Maintenance automation may only manage FishGram issues.')
    arguments = ['gh', 'api', endpoint, '--method', method]
    if body is not None:
        arguments += ['--input', '-']
    result = subprocess.run(arguments, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True)
    if result.returncode:
        raise MaintenanceError('GitHub issue operation failed; inspect workflow permissions.')
    return json.loads(result.stdout) if result.stdout.strip() else None


def remind(due, version):
    if not due:
        return
    body = issue_body(due, version)
    for page in range(1, 101):
        records = api('GET', f'repos/{REPOSITORY}/issues?state=open&per_page=100&page={page}')
        existing = next((item for item in records if item.get('title') == TITLE and 'pull_request' not in item), None)
        if existing:
            if existing['body'] != body:
                api('PATCH', f'repos/{REPOSITORY}/issues/{existing["number"]}', {'body': body})
            return
        if len(records) < 100:
            api('POST', f'repos/{REPOSITORY}/issues', {'title': TITLE, 'body': body})
            return
    raise MaintenanceError('Issue pagination exceeded limit; refusing to create a possible duplicate.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trust', default='config/update-trust')
    parser.add_argument('--create-issue', action='store_true')
    args = parser.parse_args()
    try:
        record = load_trust(args.trust)
        due = due_items(record)
        if args.create_issue:
            remind(due, record['manifest_version'])
        print(json.dumps({'manifestVersion': record['manifest_version'], 'due': due}))
    except (MaintenanceError, keys.KeyManagementError, OSError, ValueError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
