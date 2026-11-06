"""Attachment-only Beta publication, with no updater promotion."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))

class BetaTests(unittest.TestCase):
    def module(self):
        path = TOOLS / 'publish_beta_attachments.py'
        self.assertTrue(path.exists(), 'Beta attachment publisher must exist')
        import publish_beta_attachments
        return publish_beta_attachments

    def test_beta_readme_discloses_untested_and_manual_updates(self):
        beta = self.module()
        for text in ('Beta', 'unsigned', 'Windows UI', 'account', 'Scoop', 'recovery', 'search', 'not promoted'):
            self.assertIn(text, beta.README)

    def test_portable_override_preserves_program_bytes(self):
        beta = self.module()
        source = io.BytesIO()
        with zipfile.ZipFile(source, 'w') as z:
            z.writestr('Telegram.exe', b'exact candidate')
            z.writestr('README.txt', b'old')
        result = beta.portable_package(source.getvalue(), '7.3.5-r12', readme_override=beta.README.encode())
        with zipfile.ZipFile(io.BytesIO(result.data)) as z:
            self.assertEqual(z.read('Telegram.exe'), b'exact candidate')
            self.assertEqual(z.read('README.txt'), beta.README.encode())
        self.assertEqual(result.data, beta.portable_package(source.getvalue(), '7.3.5-r12', readme_override=beta.README.encode()).data)

    def test_safe_extraction_rejects_duplicates_paths_links_and_unknown_files(self):
        beta = self.module()
        for names in (['../evil'], ['Telegram.exe'], ['candidate-record.json', 'candidate-record.json']):
            data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as z:
                for name in names:
                    z.writestr(name, b'bad')
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):
                    beta.extract_candidate(data.getvalue(), Path(tmp) / 'candidate', '7.3.5-r12')

    def test_disabled_publication_does_not_fetch_or_write(self):
        beta = self.module()
        with mock.patch.object(beta, 'prepare') as prepare:
            with self.assertRaises(ValueError):
                beta.publish(None, None, None, enabled=False)
            prepare.assert_not_called()

    def test_workflow_preserves_permissions_and_secret_order(self):
        self.module()
        workflow = (TOOLS.parent / '.github/workflows/publish-beta-attachments.yml').read_text()
        for text in ('workflow_dispatch:', "github.ref == 'refs/heads/main'", 'environment: release', 'group: fishgram-publication', 'contents: read', 'actions: read'):
            self.assertIn(text, workflow)
        self.assertLess(workflow.index('Preflight exact candidate'), workflow.index('secrets.RELEASE_APP_PRIVATE_KEY'))
        for forbidden in ('permission-pages', 'ISSUER_PRIVATE_KEY', 'beta.json', 'stable.json', 'scoop-bucket'):
            self.assertNotIn(forbidden, workflow)

    def test_transport_beta_explicitly_never_latest(self):
        self.module()
        from github_release_transport import GitHubReleaseTransport
        captured = []
        def request(method, url, headers, body, timeout):
            captured.append(json.loads(body))
            return 201, {}, json.dumps({'id': 1, 'tag_name': 'v7.3.5-r12-beta', 'draft': True,
                'upload_url': 'https://uploads.github.com/repos/lonefisher/fishgram/releases/1/assets{?name,label}'}).encode()
        api = GitHubReleaseTransport(product_token='product', source_token='source', request=request)
        api.create_release('v7.3.5-r12-beta', 'a'*40, 'Beta', 'body', prerelease=True, make_latest=False)
        self.assertEqual(captured[0]['make_latest'], 'false')

    def test_provenance_requires_exact_remote_digest_parent_and_unique_artifact(self):
        beta = self.module()
        import copy
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / 'artifact.zip'
            artifact.write_bytes(b'exact archived artifact')
            run = {'id': 12, 'repository': {'full_name': 'lonefisher/fishgram'},
                   'path': '.github/workflows/product-candidate.yml', 'event': 'workflow_dispatch',
                   'conclusion': 'success', 'head_branch': 'main', 'head_sha': 'a'*40}
            jobs = {'total_count': 1, 'jobs': [{'name': 'candidate', 'conclusion': 'success'}]}
            entry = {'id': 34, 'name': 'fishgram-product-candidate-12', 'expired': False,
                     'workflow_run': {'id': 12}, 'digest': 'sha256:' + beta.digest(artifact.read_bytes())}
            artifacts = {'total_count': 1, 'artifacts': [entry]}
            def fetch(path):
                return jobs if '/jobs?' in path else artifacts if '/artifacts?' in path else run
            self.assertEqual(beta.provenance(12, 'a'*40, artifact, fetch)['artifactId'], 34)
            for change in ('digest', 'parent', 'duplicate', 'expired', 'pagination', 'workflow'):
                r, j, a = copy.deepcopy(run), copy.deepcopy(jobs), copy.deepcopy(artifacts)
                if change == 'digest': a['artifacts'][0]['digest'] = 'sha256:' + '0'*64
                if change == 'parent': r['head_sha'] = 'b'*40
                if change == 'duplicate': a['artifacts'].append(copy.deepcopy(entry))
                if change == 'expired': a['artifacts'][0]['expired'] = True
                if change == 'pagination': j['total_count'] = 101
                if change == 'workflow': r['path'] = '.github/workflows/cloud-build.yml'
                with self.subTest(change=change), self.assertRaises(ValueError):
                    beta.provenance(12, 'a'*40, artifact,
                        lambda p: j if '/jobs?' in p else a if '/artifacts?' in p else r)

    def prepared_fixture(self, directory):
        beta = self.module()
        root = Path(directory)
        folder = root / 'prepared'; folder.mkdir()
        asset = b'verified portable bytes'
        plan = {'schema': 1, 'kind': 'fishgram-beta-attachments', 'reviewedCommit': 'd'*40,
                'parentCommit': 'a'*40, 'sourceCommit': 'b'*40, 'tag': 'v7.3.5-r12-beta',
                'version': '7.3.5-r12', 'provenance': {'runId': 12},
                'assets': [{'name': 'program.zip', 'sha256': beta.digest(asset), 'size': len(asset)}]}
        (folder / 'program.zip').write_bytes(asset)
        (folder / 'beta-plan.json').write_bytes(beta.json_bytes(plan))
        def prepare(repo, reviewed, run, archive, output, **kwargs):
            output.mkdir()
            for name in ('program.zip', 'beta-plan.json'):
                (output / name).write_bytes((folder / name).read_bytes())
            return json.loads((output / 'beta-plan.json').read_text())
        return folder, plan, prepare

    def test_publish_order_and_all_public_hashes(self):
        beta = self.module()
        context = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                   'GITHUB_ENVIRONMENT': 'release', 'FISHGRAM_RELEASE_APPROVED': '1'}
        with tempfile.TemporaryDirectory() as tmp:
            folder, plan, prepare = self.prepared_fixture(tmp)
            api = mock.Mock()
            api.any_release_exists.return_value = False
            api.release_exists.return_value = True
            api.release_prerelease.return_value = True
            api.tag_commit.side_effect = lambda repo, tag: plan['sourceCommit'] if repo.endswith('/tdesktop') else plan['parentCommit']
            api.tag_exists.return_value = False
            api.source_tag_exists.return_value = False
            api.download_asset.return_value = (folder / 'program.zip').read_bytes()
            with mock.patch.dict('os.environ', context), mock.patch.object(beta, 'prepare', side_effect=prepare):
                result = beta.publish('.', 'artifact.zip', folder, enabled=True, api=api)
            self.assertEqual(result['sha256'], plan['assets'][0]['sha256'])
            self.assertTrue((folder / 'public-result.json').exists())
            calls = [c[0] for c in api.mock_calls]
            self.assertLess(calls.index('verify_source_merged'), calls.index('create_source_tag'))
            self.assertLess(calls.index('create_source_tag'), calls.index('create_release'))
            self.assertLess(calls.index('upload_asset'), calls.index('publish_release'))
            self.assertLess(calls.index('publish_release'), calls.index('download_asset'))
            self.assertEqual(api.create_release.call_args.kwargs, {'prerelease': True, 'make_latest': False})
            self.assertFalse(any('page' in c.lower() or 'index' in c.lower() for c in calls))

    def test_existing_release_mismatched_tag_and_corrupt_public_download_fail_closed(self):
        beta = self.module()
        context = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                   'GITHUB_ENVIRONMENT': 'release', 'FISHGRAM_RELEASE_APPROVED': '1'}
        for scenario in ('release', 'tag', 'download', 'upload'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                folder, plan, prepare = self.prepared_fixture(tmp)
                api = mock.Mock()
                api.any_release_exists.return_value = scenario == 'release'
                api.tag_exists.return_value = scenario == 'tag'
                api.tag_commit.side_effect = lambda repo, tag: 'f'*40 if scenario == 'tag' else plan['sourceCommit'] if repo.endswith('/tdesktop') else plan['parentCommit']
                api.release_prerelease.return_value = True
                if scenario == 'download': api.release_exists.return_value = True
                api.source_tag_exists.return_value = False
                api.download_asset.return_value = b'wrong'
                if scenario == 'upload': api.upload_asset.side_effect = RuntimeError('private error')
                with mock.patch.dict('os.environ', context), mock.patch.object(beta, 'prepare', side_effect=prepare):
                    with self.assertRaises(ValueError) as error:
                        beta.publish('.', 'artifact.zip', folder, enabled=True, api=api)
                self.assertFalse((folder / 'public-result.json').exists())
                if scenario in ('release', 'tag'):
                    api.create_source_tag.assert_not_called(); api.create_release.assert_not_called()
                else:
                    self.assertIn('https://github.com/lonefisher/fishgram/releases/tag/', str(error.exception))
                    self.assertNotIn('private error', str(error.exception))
                if scenario == 'upload': api.publish_release.assert_not_called()

    def test_prepare_checks_real_artifact_build_manifest_shape(self):
        beta = self.module()
        record = {'version': '7.3.5-r12', 'channel': 'beta', 'parentCommit': 'a'*40,
                  'sourceCommit': 'b'*40, 'files': {},
                  'archive': {'name': 'FishGram-7.3.5-r12-windows-x64-candidate.zip'},
                  'sourceArchive': {'name': 'FishGram-corresponding-source.zip'}}
        package = io.BytesIO()
        with zipfile.ZipFile(package, 'w') as z:
            z.writestr('README.txt', 'old')
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'artifact.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('candidate-record.json', beta.json_bytes(record))
                z.writestr('package/build-manifest.json', beta.json_bytes({k:v for k,v in record.items() if k != 'sourceArchive'}))
                z.writestr('package/' + record['archive']['name'], package.getvalue())
                z.writestr('package/Packer.exe', b'packer')
                z.writestr('FishGram-corresponding-source.zip', b'source')
            with mock.patch.object(beta, 'provenance', return_value={'runId':12}), \
                 mock.patch.multiple(beta.gate, validate_manifest=mock.DEFAULT, check_pins=mock.DEFAULT,
                                     verify_package=mock.DEFAULT, verify_tool=mock.DEFAULT, verify_source_archive=mock.DEFAULT):
                plan = beta.prepare('.', 'd'*40, 12, archive, Path(tmp) / 'out')
            self.assertEqual(len(plan['assets']), 5)

    def test_prepare_rejects_unrelated_root_before_provenance_or_files(self):
        beta = self.module()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(beta, 'provenance') as provenance:
            with self.assertRaisesRegex(ValueError, 'executing publisher'):
                beta.prepare(tmp, 'a'*40, 12, Path(tmp) / 'absent.zip', Path(tmp) / 'out')
            provenance.assert_not_called()

    def test_published_tag_targets_and_prerelease_are_read_back(self):
        beta = self.module()
        context = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                   'GITHUB_ENVIRONMENT': 'release', 'FISHGRAM_RELEASE_APPROVED': '1'}
        with tempfile.TemporaryDirectory() as tmp:
            folder, plan, prepare = self.prepared_fixture(tmp)
            api = mock.Mock()
            api.any_release_exists.return_value = False
            api.tag_exists.return_value = False
            api.source_tag_exists.return_value = False
            api.tag_commit.return_value = 'f'*40
            api.release_prerelease.return_value = True
            api.download_asset.return_value = (folder / 'program.zip').read_bytes()
            with mock.patch.dict('os.environ', context), mock.patch.object(beta, 'prepare', side_effect=prepare):
                with self.assertRaises(ValueError):
                    beta.publish('.', 'artifact.zip', folder, enabled=True, api=api)
            self.assertFalse((folder / 'public-result.json').exists())

    def test_production_transport_detects_draft_when_by_tag_returns_404_before_writes(self):
        beta = self.module()
        from github_release_transport import GitHubReleaseTransport
        calls = []
        def request(method, url, headers, body, timeout):
            calls.append((method, url, headers))
            if '/releases?' in url:
                return 200, {}, json.dumps([{'id': 7, 'tag_name': 'v7.3.5-r12-beta', 'draft': True}]).encode()
            if '/releases/tags/' in url:
                return 404, {}, b'{}'
            raise AssertionError('No writes or unrelated API calls allowed before rejecting draft')
        api = GitHubReleaseTransport(product_token='product', source_token='source', request=request)
        context = {'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
                   'GITHUB_ENVIRONMENT': 'release', 'FISHGRAM_RELEASE_APPROVED': '1'}
        with tempfile.TemporaryDirectory() as tmp:
            folder, plan, prepare = self.prepared_fixture(tmp)
            with mock.patch.dict('os.environ', context), mock.patch.object(beta, 'prepare', side_effect=prepare):
                with self.assertRaisesRegex(ValueError, 'Existing releases'):
                    beta.publish('.', 'artifact.zip', folder, enabled=True, api=api)
        self.assertTrue(any('/releases?' in url for _, url, _ in calls))
        self.assertTrue(all(method == 'GET' and headers['Authorization'] == 'Bearer product'
                            for method, _, headers in calls))

    def test_draft_inclusive_lookup_paginates_and_rejects_malformed_or_unbounded_lists(self):
        self.module()
        from github_release_transport import GitHubReleaseTransport, TransportError
        calls = []
        def request(method, url, headers, body, timeout):
            calls.append(url)
            if 'page=1&' in url:
                return 200, {}, json.dumps([{'id': i+1, 'tag_name': 'other-'+str(i), 'draft': False} for i in range(100)]).encode()
            return 200, {}, json.dumps([{'id': 101, 'tag_name': 'v7.3.5-r12-beta', 'draft': True}]).encode()
        api = GitHubReleaseTransport(product_token='product', source_token='source', request=request)
        self.assertTrue(api.any_release_exists('v7.3.5-r12-beta'))
        self.assertEqual(len(calls), 2)
        for response in ({}, [{'id': 1, 'tag_name': 'tag'}], [{'id': 1, 'tag_name': 'tag', 'draft': 'false'}]):
            api = GitHubReleaseTransport(product_token='product', source_token='source',
                request=lambda *args: (200, {}, json.dumps(response).encode()))
            with self.assertRaises(TransportError): api.any_release_exists('v7.3.5-r12-beta')
        api = GitHubReleaseTransport(product_token='product', source_token='source',
            request=lambda *args: (200, {}, json.dumps([{'id': i+1, 'tag_name':'other', 'draft':False} for i in range(100)]).encode()))
        with self.assertRaises(TransportError): api.any_release_exists('v7.3.5-r12-beta')
        calls.clear()
        def full_pages(method, url, headers, body, timeout):
            calls.append(url)
            page = int(url.split('page=')[1].split('&')[0])
            return 200, {}, json.dumps([{'id': page*100+i, 'tag_name': 'other', 'draft': False} for i in range(100)]).encode()
        api = GitHubReleaseTransport(product_token='product', source_token='source', request=full_pages)
        with self.assertRaisesRegex(TransportError, 'pagination bound'):
            api.any_release_exists('v7.3.5-r12-beta')
        self.assertEqual(len(calls), 100)
        api = GitHubReleaseTransport(product_token='product', source_token='source',
            request=lambda *args: (200, {}, b'[]'))
        self.assertFalse(api.any_release_exists('v7.3.5-r12-beta'))

    def test_workflow_enables_longpaths_before_checkout(self):
        self.module()
        workflow = (TOOLS.parent / '.github/workflows/publish-beta-attachments.yml').read_text()
        self.assertLess(workflow.index('git config --global core.longpaths true'), workflow.index('uses: actions/checkout@'))

    def test_readme_does_not_claim_update_command_installs_scoop_app(self):
        beta = self.module()
        self.assertIn('Update with Scoop: scoop update fishgram-beta.', beta.README)
        self.assertNotIn('Install/update with Scoop', beta.README)
