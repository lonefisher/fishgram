import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('fishgram_key_maintenance', Path(__file__).resolve().parents[1] / 'tools/key_maintenance.py')
maintenance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = maintenance
SPEC.loader.exec_module(maintenance)


class MaintenanceTests(unittest.TestCase):
    def manifest(self):
        return {'manifest_version': 4, 'expires': 1000 + 90 * 86400,
                'keys': [{'id': 'issuer-a', 'expires': 1000 + 45 * 86400},
                         {'id': 'issuer-b', 'expires': 1000 + 46 * 86400},
                         {'id': 'revoked', 'expires': 500}], 'revoked': ['revoked']}

    def test_exact_45_day_boundary_and_revoked_key(self):
        due = maintenance.due_items(self.manifest(), now=1000)
        self.assertEqual([item['id'] for item in due], ['issuer-a'])
        self.assertEqual(due[0]['days'], 45)

    def test_root_manifest_expiry_also_requires_renewal(self):
        record = self.manifest()
        record['expires'] = 999
        due = maintenance.due_items(record, now=1000)
        self.assertEqual(due[0]['id'], 'root-manifest')
        self.assertEqual(due[0]['days'], -1)

    def test_open_issue_is_reused_and_changes_are_updated(self):
        due = maintenance.due_items(self.manifest(), now=1000)
        body = maintenance.issue_body(due, 4)
        with mock.patch.object(maintenance, 'api', side_effect=[
                [{'number': 9, 'title': maintenance.TITLE, 'body': body, 'pull_request': {'url': 'ignored'}},
                 {'number': 7, 'title': maintenance.TITLE, 'body': body}]]) as api:
            maintenance.remind(due, 4)
        api.assert_called_once()
        with mock.patch.object(maintenance, 'api', side_effect=[
                [{'number': 7, 'title': maintenance.TITLE, 'body': body}], {}]) as api:
            maintenance.remind(due, 5)
        self.assertEqual(api.call_args.args[:2], ('PATCH', 'repos/lonefisher/fishgram/issues/7'))

    def test_missing_open_issue_creates_one(self):
        due = maintenance.due_items(self.manifest(), now=1000)
        with mock.patch.object(maintenance, 'api', side_effect=[[], {}]) as api:
            maintenance.remind(due, 4)
        self.assertEqual(api.call_args.args[:2], ('POST', 'repos/lonefisher/fishgram/issues'))

    def test_no_due_keys_has_no_external_effect(self):
        with mock.patch.object(maintenance, 'api') as api:
            maintenance.remind([], 4)
        api.assert_not_called()

    def test_missing_trust_is_a_visible_blocker(self):
        with self.assertRaises(maintenance.MaintenanceError):
            maintenance.load_trust(Path('missing-fishgram-public-trust'))

    def test_api_scope_rejects_release_source_and_external_repo(self):
        for path in ('repos/lonefisher/fishgram/releases', 'repos/lonefisher/tdesktop/issues',
                     'repos/other/fishgram/issues', 'repos/lonefisher/fishgram/issues/7/comments'):
            with self.subTest(path=path), self.assertRaises(maintenance.MaintenanceError):
                maintenance.api('POST', path, {})


if __name__ == '__main__':
    unittest.main()
