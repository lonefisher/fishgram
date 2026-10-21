import hashlib
import base64
import importlib.util
import json
import io
import zipfile
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

MODULE = Path(__file__).resolve().parents[1] / "tools" / "publish_release.py"
spec = importlib.util.spec_from_file_location("publish_release", MODULE)
publish = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = publish
spec.loader.exec_module(publish)


class FakeApi:
    def __init__(self, *, index=None, tags=(), releases=(), corrupt_download=None,
                 fail_upload=None):
        self.index = index
        self.tags = set(tags)
        self.source_tags = set()
        self.releases = set(releases)
        self.corrupt_download = corrupt_download
        self.fail_upload = fail_upload
        self.assets = {}
        self.events = []
        self.pages = {}
        self.prerelease = False

    def tag_exists(self, repository, tag):
        self.events.append(("tag_exists", repository, tag))
        return tag in self.tags

    def tag_commit(self, repository, tag):
        return "b" * 40 if repository == publish.PRODUCT_REPOSITORY else "c" * 40

    def verify_source_merged(self, commit):
        self.events.append(('source_merged', commit))

    def source_tag_exists(self, tag):
        self.events.append(("source_tag_exists", tag))
        return tag in self.source_tags

    def release_exists(self, tag):
        self.events.append(("release_exists", tag))
        return tag in self.releases

    def release_prerelease(self, tag):
        return self.prerelease

    def get_index(self, path):
        self.events.append(("get_index", path))
        return self.index

    def create_release(self, tag, target, title, body, *, prerelease=False):
        self.prerelease = prerelease
        self.release_body = body
        self.events.append(("create_release", tag, target))
        self.tags.add(tag)
        self.releases.add(tag)

    def create_source_tag(self, tag, target):
        if tag in self.source_tags:
            raise RuntimeError("Source tag must never be recreated")
        self.events.append(("source_tag", tag, target))
        self.source_tags.add(tag)

    def asset_names(self, tag):
        return set(self.assets)

    def pages_config(self):
        self.events.append(("pages_config",))
        return {"public": True}

    def publish_release(self, tag):
        self.events.append(("publish_release", tag))

    def upload_asset(self, tag, name, data):
        self.events.append(("upload", name))
        if name == self.fail_upload:
            raise RuntimeError("simulated upload failure")
        self.assets[name] = bytes(data)

    def download_asset(self, tag, name):
        self.events.append(("download", name))
        data = self.assets[name]
        if name == self.corrupt_download:
            return data + b"corrupt"
        return data

    def put_page(self, path, data):
        self.events.append(("page", path))
        self.pages[path] = bytes(data)

    def put_pages_atomic(self, files):
        self.events.append(("atomic_pages", tuple(files)))
        self.pages.update(files)

    def preflight_pages(self, files):
        self.events.append(('trust_preflight', tuple(files)))

    def get_page(self, path):
        self.events.append(("get_page", path))
        return self.pages.get(path)


def zip_bytes(entries):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        for name, data in entries:
            archive.writestr(name if isinstance(name, zipfile.ZipInfo) else zipfile.ZipInfo(name), data)
    return result.getvalue()


def replace_portable(candidate, data):
    from dataclasses import replace
    portable = publish.Asset(candidate.assets[1].name, data)
    record = json.loads(next(a.data for a in candidate.assets if a.name == 'candidate-record.json'))
    distribution = publish.distribution_record(portable, record, candidate.reviewed_commit)
    return replace(candidate, assets=tuple(portable if a.name == portable.name else
        distribution if a.name == distribution.name else a for a in candidate.assets))


def plan(*, index_channel="stable", package_channel="stable", reuse_release=False,
         reviewed_commit="b" * 40, deferred_search=False, separate_approval=False):
    import test_release_gate as fixture
    update_name = "fishgram-update-win-x64-7002009-r1" + ("-beta" if package_channel == "beta" else "")
    payload = {'Telegram.exe': b'client', 'Updater.exe': b'updater'}
    manifest = fixture.manifest(**{
        "version": "7.2.9-r1", "channel": package_channel, "updateVersion": "30073399661297665",
        "parentCommit": "b" * 40, "sourceCommit": "c" * 40,
        "sourceArchive": {"name": "FishGram-corresponding-source.zip", "size": len(b"source-zip"),
                          "sha256": hashlib.sha256(b"source-zip").hexdigest()},
        "files": {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                  for name, data in payload.items()},
    })
    embedded = {k: v for k, v in manifest.items() if k not in ('archive', 'sourceArchive')}
    candidate_zip = zip_bytes([*payload.items(), ('LICENSE', b'gpl'), ('LEGAL', b'openssl exception'),
        ('Start-FishGram.cmd', b'run'), ('README.txt', b'internal candidate'),
        ('build-manifest.json', json.dumps(embedded).encode())])
    manifest['archive'] = {'name': 'FishGram-7.2.9-r1-windows-x64-candidate.zip',
        'size': len(candidate_zip), 'sha256': hashlib.sha256(candidate_zip).hexdigest()}
    qa = fixture.qa_for(manifest)
    qa['candidate']['sourceArchive'] = dict(manifest['sourceArchive'])
    if deferred_search:
        qa['kind'] = 'fishgram-beta-qa'
        qa['deferredSearch'] = ['largeScalePerformance', 'nativeOmissionControlSamples']
        qa['checks']['searchEightAcceptance'] = False
        qa['checks']['remainingSearchAcceptance'] = True
    raw = json.dumps(qa).encode()
    private_signing = {
        'schema': 1, 'kind': 'fishgram-signed-candidate', 'channel': package_channel,
        'qa': {'file': 'qa.json', 'sha256': hashlib.sha256(raw).hexdigest(), 'approvedAt': qa['approvedAt']},
        'signedAt': qa['approvedAt'],
    }
    if separate_approval:
        preliminary = fixture.qa_for(manifest, approvedAt='2026-10-06T00:00:00Z')
        preliminary['candidate']['sourceArchive'] = dict(manifest['sourceArchive'])
        preliminary['kind'] = 'fishgram-signing-qa'
        preliminary['checks']['searchEightAcceptance'] = preliminary['checks']['updaterFullMatrix'] = False
        preliminary_bytes = json.dumps(preliminary).encode()
        private_signing['qa'].update(sha256=hashlib.sha256(preliminary_bytes).hexdigest(),
            approvedAt=preliminary['approvedAt'], document=base64.b64encode(preliminary_bytes).decode())
        private_signing['signedAt'] = '2026-10-07T00:00:00Z'
        assert publish.verify_signing_approval(manifest, qa, private_signing, 'qa.json', raw)
    proof, signing = publish.public_acceptance_records(raw, private_signing, separate_approval=separate_approval)
    assets = (
        publish.Asset(update_name, b"signed-update"),
        publish.portable_package(candidate_zip, manifest['version'], beta=index_channel == 'beta',
                                 deferred_search=deferred_search),
        publish.Asset("FishGram-corresponding-source.zip", b"source-zip"),
        publish.Asset("candidate-record.json", json.dumps(manifest).encode()),
        proof,
        signing,
    )
    assets += (publish.distribution_record(assets[1], manifest, reviewed_commit),)
    pages = (
        publish.PageFile("keys/root-public.pem", b"root-public"),
        publish.PageFile("keys/issuer-public.pem", b"issuer-public"),
        publish.PageFile("keys/manifest.min.json", b"key-manifest"),
        publish.PageFile("keys/manifest.sig", b"root-signature"),
    )
    entry = {
        "version": "30073399661297665",
        "size": str(len(assets[0].data)),
        "sha256": hashlib.sha256(assets[0].data).hexdigest(),
        "url": f"https://github.com/lonefisher/fishgram/releases/download/v7.2.9-r1/{update_name}",
        "channel": package_channel,
    }
    index = json.dumps({"schema": 1, "channel": index_channel, "platform": "windows-x64",
                        "update": entry}, sort_keys=True, separators=(",", ":")).encode()
    return publish.PromotionPlan(
        tag="v7.2.9-r1", target_commit="b" * 40, index_channel=index_channel,
        package_channel=package_channel,
        version="30073399661297665", assets=assets, page_files=pages,
        index_path=f"{index_channel}.json", index_bytes=index, reuse_release=reuse_release,
        reviewed_commit=reviewed_commit,
    )


def allowed_context():
    return publish.PublishContext(event="workflow_dispatch", ref="refs/heads/main",
                                  environment="release", approval_gate_passed=True)


def publish_plan(api, candidate=None):
    return publish.promote(plan() if candidate is None else candidate, api,
                           enabled=True, context=allowed_context())


class PublishReleaseTests(unittest.TestCase):
    def test_rehashed_portable_must_contain_only_the_verified_candidate_files(self):
        original = plan()
        with zipfile.ZipFile(io.BytesIO(original.assets[1].data)) as archive:
            entries = [(info.filename, archive.read(info)) for info in archive.infolist()]
        for change in ('bytes', 'program', 'size', 'missing', 'extra', 'duplicate', 'path',
                       'backslash', 'alias', 'directory', 'symlink', 'embedded', 'readme'):
            with self.subTest(change=change):
                modified = list(entries)
                if change in ('program', 'size', 'embedded', 'readme'):
                    name = {'program': 'Telegram.exe', 'size': 'Updater.exe',
                            'embedded': 'build-manifest.json', 'readme': 'README.txt'}[change]
                    modified = [(key, b'evil!!' if change == 'program' else b'changed'
                                 if key == name else data) if key == name else (key, data)
                                for key, data in modified]
                elif change == 'missing':
                    modified = [(key, data) for key, data in modified if key != 'LEGAL']
                else:
                    name = {'extra': 'evil.exe', 'duplicate': 'Telegram.exe', 'path': '../evil.exe',
                            'backslash': 'sub\\evil.exe', 'alias': 'telegram.exe',
                            'directory': 'extra/', 'symlink': 'link'} .get(change, 'extra')
                    if change == 'symlink':
                        name = zipfile.ZipInfo(name)
                        name.create_system = 3
                        name.external_attr = 0o120777 << 16
                    modified.append((name, b'evil'))
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', UserWarning)
                    data = b'portable-zip' if change == 'bytes' else zip_bytes(modified)
                api = FakeApi()
                with self.assertRaises(publish.PublishError):
                    publish_plan(api, replace_portable(original, data))
                self.assertEqual(api.events, [])

    def test_rehashed_proof_original_hash_is_checked_for_both_approval_flows(self):
        from dataclasses import replace
        for separate in (False, True):
            for reuse in (False, True):
                with self.subTest(separate=separate, reuse=reuse):
                    candidate = plan(separate_approval=separate, reuse_release=reuse)
                    proof = next(a for a in candidate.assets if a.name == 'qa-proof.json')
                    signing = next(a for a in candidate.assets if a.name == 'signing-record.json')
                    value = json.loads(proof.data)
                    value['originalSha256'] = 'd' * 64
                    proof = publish.Asset(proof.name, json.dumps(value).encode())
                    record = json.loads(signing.data)
                    record['qa']['publicProofSha256'] = proof.sha256
                    signing = publish.Asset(signing.name, json.dumps(record).encode())
                    assets = tuple(proof if a.name == proof.name else signing if a.name == signing.name
                                   else a for a in candidate.assets)
                    api = FakeApi()
                    with self.assertRaises(publish.PublishError):
                        publish_plan(api, replace(candidate, assets=assets))
                    self.assertEqual(api.events, [])

    def test_source_zip_must_match_candidate_before_release_writes(self):
        from dataclasses import replace
        original = plan()
        for change in ('missing', 'name', 'size', 'same-size-content'):
            with self.subTest(change=change):
                assets = []
                for asset in original.assets:
                    if asset.name == 'FishGram-corresponding-source.zip':
                        if change == 'missing':
                            continue
                        asset = publish.Asset('unrelated.zip' if change == 'name' else asset.name,
                            b'other--zip' if change == 'same-size-content' else
                            asset.data + b'changed' if change == 'size' else asset.data)
                    assets.append(asset)
                api = FakeApi()
                with self.assertRaisesRegex(publish.PublishError, '[Ss]ource archive'):
                    publish_plan(api, replace(original, assets=tuple(assets)))
                self.assertFalse(any(e[0] in {'source_tag', 'create_release', 'upload',
                    'publish_release', 'atomic_pages', 'page'} for e in api.events))

    def test_proof_is_required_and_bound_before_publication(self):
        from dataclasses import replace
        for reuse in (False, True):
            original = plan(reuse_release=reuse)
            for change in ('missing', 'duplicate', 'json', 'object', 'hash', 'approvedAt',
                           'checks', 'candidate', 'channel', 'signing-json', 'signing-approval'):
                with self.subTest(reuse=reuse, change=change):
                    assets = list(original.assets)
                    proof = next(a for a in assets if a.name == 'qa-proof.json')
                    signing = next(a for a in assets if a.name == 'signing-record.json')
                    value = json.loads(proof.data)
                    record = json.loads(signing.data)
                    if change == 'missing':
                        assets.remove(proof)
                    elif change == 'duplicate':
                        assets.append(proof)
                    elif change.startswith('signing-'):
                        if change == 'signing-approval':
                            record['qa'] = None
                        assets[assets.index(signing)] = publish.Asset(signing.name,
                            b'{' if change == 'signing-json' else json.dumps(record).encode())
                    else:
                        if change == 'json':
                            data = b'{'
                        elif change == 'object':
                            data = b'[]'
                        else:
                            if change == 'hash':
                                value['originalSha256'] = 'd' * 64
                            elif change == 'approvedAt':
                                value['approvedAt'] = '2026-10-09T00:00:00Z'
                            elif change == 'checks':
                                value['checks']['updaterFullMatrix'] = False
                            elif change == 'candidate':
                                value['candidate']['sourceCommit'] = 'd' * 40
                            else:
                                value['releaseChannel'] = 'nightly'
                            data = json.dumps(value).encode()
                            if change != 'hash':
                                record['qa']['publicProofSha256'] = hashlib.sha256(data).hexdigest()
                                assets[assets.index(signing)] = publish.Asset(signing.name, json.dumps(record).encode())
                        assets[assets.index(proof)] = publish.Asset(proof.name, data)
                    api = FakeApi(tags={original.tag} if reuse else (),
                                  releases={original.tag} if reuse else ())
                    api.source_tags.add(original.tag)
                    api.assets = {a.name: a.data for a in assets}
                    with self.assertRaises(publish.PublishError):
                        publish_plan(api, replace(original, assets=tuple(assets)))
                    self.assertFalse(any(e[0] in {'source_tag', 'create_release', 'upload',
                        'publish_release', 'atomic_pages', 'page'} for e in api.events))

    def test_offline_handoff_cannot_drop_proof_or_rehash_changed_proof(self):
        for change in ('missing', 'changed'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)/'promotion'
                publish.write_promotion(plan(), output)
                metadata_path = output/'promotion.json'
                metadata = json.loads(metadata_path.read_bytes())
                entry = next(a for a in metadata['releaseAssets'] if a['name'] == 'qa-proof.json')
                if change == 'missing':
                    metadata['releaseAssets'].remove(entry)
                else:
                    path = output/'release-assets/qa-proof.json'
                    proof = json.loads(path.read_bytes())
                    proof['approvedAt'] = '2026-10-09T00:00:00Z'
                    data = json.dumps(proof).encode()
                    path.write_bytes(data)
                    entry.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
                metadata_path.write_text(json.dumps(metadata))
                api = FakeApi()
                with mock.patch.dict(sys.modules, {'github_release_transport': SimpleNamespace(
                        GitHubReleaseTransport=SimpleNamespace(from_environment=lambda: api))}):
                    self.assertEqual(publish.main(['publish', '--promotion', str(output),
                        '--enable-publication', '--event', 'workflow_dispatch', '--ref', 'refs/heads/main',
                        '--environment', 'release', '--approval-marker', '1']), 1)
                self.assertFalse(api.events)

    def test_two_stage_public_proof_can_publish_after_signing(self):
        from dataclasses import replace
        original = plan()
        proof = next(a for a in original.assets if a.name == 'qa-proof.json')
        signing = next(a for a in original.assets if a.name == 'signing-record.json')
        record = json.loads(signing.data)
        record['qa']['publicProofApprovedAt'] = json.loads(proof.data)['approvedAt']
        record['qa']['approvedAt'] = '2026-10-06T00:00:00Z'
        record['signedAt'] = '2026-10-07T00:00:00Z'
        for signed_at, accepted in (('2026-10-07T00:00:00Z', True),
                                    ('2026-10-09T00:00:00Z', False)):
            with self.subTest(signedAt=signed_at):
                record['signedAt'] = signed_at
                candidate = replace(original, assets=tuple(publish.Asset(a.name, json.dumps(record).encode())
                    if a.name == signing.name else a for a in original.assets))
                api = FakeApi()
                if accepted:
                    publish_plan(api, candidate)
                    self.assertIn('stable.json', api.pages)
                else:
                    with self.assertRaises(publish.PublishError):
                        publish_plan(api, candidate)
                    self.assertFalse(api.events)

    def test_full_qa_on_beta_feed_does_not_claim_deferred_search(self):
        for package_channel in ('stable', 'beta'):
            with self.subTest(package_channel=package_channel):
                api = FakeApi()
                publish_plan(api, plan(index_channel='beta', package_channel=package_channel))
                self.assertTrue(api.prerelease)
                self.assertNotIn('remain pending', api.release_body)
                self.assertNotIn('has not reached', api.release_body)
                self.assertIn('Beta', api.release_body)

    def test_full_qa_beta_portable_readme_does_not_claim_deferred_search(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('README.txt', b'candidate')
        asset = publish.portable_package(data.getvalue(), '7.3.1-r9', beta=True)
        with zipfile.ZipFile(io.BytesIO(asset.data)) as archive:
            readme = archive.read('README.txt')
        self.assertIn(b'Beta', readme)
        self.assertNotIn(b'remain pending', readme)

    def test_two_stage_public_record_binds_final_proof_time(self):
        import test_release_gate as fixture
        qa = fixture.qa_for(fixture.manifest(), approvedAt='2026-10-10T00:00:00Z')
        proof, signing = publish.public_acceptance_records(json.dumps(qa).encode(), {
            'qa': {'sha256': 'a' * 64, 'approvedAt': '2026-10-08T00:00:00Z'},
            'signedAt': '2026-10-09T00:00:00Z',
        }, separate_approval=True)
        record = json.loads(signing.data)
        self.assertEqual(record['qa']['approvedAt'], '2026-10-08T00:00:00Z')
        self.assertEqual(record['qa']['publicProofApprovedAt'], json.loads(proof.data)['approvedAt'])

    def test_default_beta_channel_uses_existing_qa_gates(self):
        import contextlib
        import test_release_gate as fixture
        sys.path.insert(0, str(MODULE.parent))
        import sign_candidate as sign
        gate = sign.gate
        for package_channel, index_channel, deferred, accepted in (
                ('beta', None, True, True), ('beta', 'beta', True, True),
                ('stable', 'beta', True, True), ('stable', None, True, False),
                ('stable', 'stable', True, False), ('beta', 'stable', True, False),
                ('beta', None, False, True), ('stable', 'beta', False, True),
                ('stable', None, False, True)):
            with self.subTest(package=package_channel, index=index_channel, deferred=deferred), \
                    tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
                root = Path(temp)
                stack.enter_context(mock.patch.dict(sys.modules, {'release_gate': gate}))
                record = fixture.manifest(channel=package_channel)
                record['sourceArchive'] = {'name': 'source.zip', 'size': 6, 'sha256': 'e' * 64}
                qa = fixture.qa_for(record)
                qa['candidate']['sourceArchive'] = record['sourceArchive']
                if deferred:
                    qa['kind'] = 'fishgram-beta-qa'
                    qa['deferredSearch'] = ['largeScalePerformance', 'nativeOmissionControlSamples']
                    qa['checks']['searchEightAcceptance'] = False
                    qa['checks']['remainingSearchAcceptance'] = True
                raw = json.dumps(qa).encode()
                record_path, qa_path, signed = root/'candidate-record.json', root/'qa.json', root/'signed'
                record_path.write_text(json.dumps(record))
                qa_path.write_bytes(raw)
                signed.write_bytes(b'signed-package')
                (root/'source.zip').write_bytes(b'source')
                candidate = root/'candidate.zip'
                with zipfile.ZipFile(candidate, 'w') as archive:
                    archive.writestr('README.txt', b'candidate')
                signing = {'schema': 1, 'kind': 'fishgram-signed-candidate',
                    'candidateVersion': record['version'], 'updateVersion': record['updateVersion'],
                    'channel': package_channel, 'parentCommit': record['parentCommit'],
                    'sourceCommit': record['sourceCommit'], 'candidateArchive': record['archive'],
                    'sourceArchive': record['sourceArchive'],
                    'qa': {'file': qa_path.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'approvedAt': qa['approvedAt']},
                    'issuer': {'id': 'issuer-a', 'alg': 'Ed25519', 'publicKeySha256': hashlib.sha256(b'issuer').hexdigest()},
                    'package': {'name': signed.name, 'size': signed.stat().st_size, 'sha256': gate.sha256_file(signed)},
                    'signedAt': qa['approvedAt']}
                signing_path = root/'signing.json'
                signing_path.write_text(json.dumps(signing))
                for name in ('root-public.pem', 'issuer-public.pem', 'manifest.min.json', 'manifest.sig'):
                    (root/name).write_bytes(b'public-trust')
                recipe = {**fixture.RECIPE, 'channel': package_channel}
                stack.enter_context(mock.patch.object(gate, '_recipe_at', return_value=recipe))
                for name in ('check_pins', 'verify_package', 'verify_tool', 'verify_source_archive'):
                    stack.enter_context(mock.patch.object(gate, name))
                stack.enter_context(mock.patch.object(sign, '_verify_trust', return_value=({}, b'issuer', b'root', b'trust')))
                for name in ('_check_candidate_root', '_check_record_trust', 'verify_envelope'):
                    stack.enter_context(mock.patch.object(sign, name))
                stack.enter_context(mock.patch.object(sign, 'parse_envelope', return_value=SimpleNamespace(
                    version=int(record['updateVersion']), channel=1 if package_channel == 'beta' else 0, target=(0, 1))))
                stack.enter_context(mock.patch.object(publish, 'supports_split_channel_feed', return_value=True))
                args = (root, 'b'*40, index_channel, False, record_path, qa_path, signing_path,
                        candidate, root/'source.zip', signed, root/'Packer.exe', root, 'issuer-a')
                if accepted:
                    prepared = publish.validate_inputs(*args)
                    self.assertEqual(prepared.index_channel, index_channel or package_channel)
                    portable = next(a for a in prepared.assets if a.name.startswith('FishGram-') and a.name.endswith('windows-x64.zip'))
                    with zipfile.ZipFile(io.BytesIO(portable.data)) as archive:
                        self.assertEqual(b'remain pending' in archive.read('README.txt'), deferred)
                        self.assertEqual(b'Beta' in archive.read('README.txt'), prepared.index_channel == 'beta')
                else:
                    with self.assertRaises((gate.GateError, publish.PublishError)):
                        publish.validate_inputs(*args)

    def test_distribution_record_is_required_and_bound_before_any_write(self):
        from dataclasses import replace
        original = plan()
        for change in ('missing', 'malformed', 'archive-name', 'archive-size', 'archive-hash',
                       'candidateArchive', 'parentCommit', 'sourceCommit', 'files',
                       'reviewedPublisherCommit', 'portable-bytes'):
            with self.subTest(change=change):
                assets = list(original.assets)
                distribution = json.loads(assets[-1].data)
                if change == 'missing':
                    assets.pop()
                elif change == 'malformed':
                    assets[-1] = publish.Asset(assets[-1].name, b'[]')
                elif change == 'portable-bytes':
                    assets[1] = publish.Asset(assets[1].name, b'changed-portable')
                else:
                    if change.startswith('archive-'):
                        key = {'archive-name': 'name', 'archive-size': 'size', 'archive-hash': 'sha256'}[change]
                        distribution['archive'][key] = {'name': 'another.zip', 'size': 1, 'sha256': '0' * 64}[key]
                    elif change in ('candidateArchive', 'files'):
                        distribution[change] = {}
                    else:
                        distribution[change] = 'f' * 40
                    assets[-1] = publish.Asset(assets[-1].name, json.dumps(distribution).encode())
                api = FakeApi()
                with self.assertRaisesRegex(publish.PublishError, '[Dd]istribution'):
                    publish_plan(api, replace(original, assets=tuple(assets)))
                self.assertFalse(any(e[0] in {'source_tag', 'create_release', 'upload',
                    'publish_release', 'atomic_pages', 'page'} for e in api.events))

    def test_reuse_rejects_changed_remote_distribution_bindings_before_pages(self):
        candidate = plan(reuse_release=True, reviewed_commit='e' * 40)
        for field in ('archive', 'candidateArchive', 'files', 'parentCommit',
                      'sourceCommit', 'reviewedPublisherCommit'):
            with self.subTest(field=field):
                api = FakeApi(tags={candidate.tag}, releases={candidate.tag})
                api.source_tags.add(candidate.tag)
                api.assets = {a.name: a.data for a in candidate.assets}
                previous = json.loads(api.assets['distribution-record.json'])
                previous[field] = {} if field in ('archive', 'candidateArchive', 'files') else 'invalid'
                api.assets['distribution-record.json'] = json.dumps(previous).encode()
                with self.assertRaisesRegex(publish.PublishError, '[Dd]istribution'):
                    publish_plan(api, candidate)
                self.assertFalse(any(e[0] in {'atomic_pages', 'page', 'upload'} for e in api.events))

    def test_stable_reuse_rejects_prerelease_even_with_a_stable_signed_package(self):
        candidate = plan(reuse_release=True)
        api = FakeApi(tags={candidate.tag}, releases={candidate.tag})
        api.source_tags.add(candidate.tag)
        api.assets = {a.name: a.data for a in candidate.assets}
        api.prerelease = True
        with self.assertRaisesRegex(publish.PublishError, 'prerelease'):
            publish_plan(api, candidate)
        self.assertFalse(any(e[0] in {'source_tag', 'create_release', 'upload',
            'publish_release', 'atomic_pages', 'page'} for e in api.events))

    def test_beta_reuse_rejects_stable_release_before_pages_writes(self):
        candidate = plan(index_channel='beta', package_channel='stable', reuse_release=True)
        api = FakeApi(tags={candidate.tag}, releases={candidate.tag})
        api.source_tags.add(candidate.tag)
        api.assets = {a.name: a.data for a in candidate.assets}
        with self.assertRaisesRegex(publish.PublishError, 'prerelease'):
            publish_plan(api, candidate)
        self.assertFalse(any(e[0] in {'source_tag', 'create_release', 'upload',
            'publish_release', 'atomic_pages', 'page'} for e in api.events))

    def test_two_stage_final_qa_must_follow_signing_for_stable_and_beta(self):
        import test_release_gate as fixture
        manifest = fixture.manifest()
        preliminary = fixture.qa_for(manifest, kind='fishgram-signing-qa',
                                     approvedAt='2026-10-08T23:59:58Z')
        preliminary['checks']['searchEightAcceptance'] = False
        preliminary['checks']['updaterFullMatrix'] = False
        raw = json.dumps(preliminary).encode()
        signing = {'qa': {'file': 'qa.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                         'approvedAt': preliminary['approvedAt'],
                         'document': base64.b64encode(raw).decode()},
                   'signedAt': '2026-10-09T00:00:00Z'}
        for beta in (False, True):
            for stamp in ('2026-10-08T23:59:59Z', '2026-10-09T00:00:00Z', '2026-10-09T00:00:01Z'):
                final = fixture.qa_for(manifest, approvedAt=stamp)
                if beta:
                    final['kind'] = 'fishgram-beta-qa'
                    final['deferredSearch'] = ['largeScalePerformance', 'nativeOmissionControlSamples']
                    final['checks']['searchEightAcceptance'] = False
                    final['checks']['remainingSearchAcceptance'] = True
                with self.subTest(beta=beta, approvedAt=stamp):
                    if stamp < signing['signedAt']:
                        with self.assertRaises(publish.PublishError):
                            publish.verify_signing_approval(manifest, final, signing, 'qa.json', json.dumps(final).encode())
                    else:
                        self.assertTrue(publish.verify_signing_approval(
                            manifest, final, signing, 'qa.json', json.dumps(final).encode()))

    def test_same_complete_qa_can_be_approved_before_signing(self):
        import test_release_gate as fixture
        manifest = fixture.manifest()
        final = fixture.qa_for(manifest)
        raw = json.dumps(final).encode()
        signing = {'qa': {'file': 'qa.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                         'approvedAt': final['approvedAt']}, 'signedAt': '2026-10-09T00:00:00Z'}
        self.assertFalse(publish.verify_signing_approval(manifest, final, signing, 'qa.json', raw))

    def test_portable_bytes_are_equal_across_host_creator_defaults(self):
        source = io.BytesIO()
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('README.txt', b'candidate')
            archive.writestr('Telegram.exe', b'verified-cloud-client')
        original_init = zipfile.ZipInfo.__init__
        packages = []
        for creator in (0, 3):
            def host_init(info, *args, **kwargs):
                original_init(info, *args, **kwargs)
                info.create_system = creator
            with mock.patch.object(zipfile.ZipInfo, '__init__', host_init):
                packages.append(publish.portable_package(source.getvalue(), '7.3.1-r9').data)
        self.assertEqual(packages[0], packages[1])
        with zipfile.ZipFile(io.BytesIO(packages[0])) as archive:
            self.assertEqual(archive.read('Telegram.exe'), b'verified-cloud-client')
            self.assertIn(b'Start-FishGram.cmd', archive.read('README.txt'))

    def test_beta_acceptance_proof_cannot_be_promoted_to_stable(self):
        candidate = plan(deferred_search=True)
        api = FakeApi()
        with self.assertRaisesRegex(publish.PublishError, 'Beta acceptance'):
            publish_plan(api, candidate)
        self.assertFalse(any(e[0] in {'source_tag', 'create_release', 'upload', 'page'} for e in api.events))

    def test_internal_signing_request_can_complete_as_beta_for_same_candidate(self):
        import test_release_gate as fixture
        manifest = fixture.manifest()
        final = fixture.qa_for(manifest)
        original = json.loads(json.dumps(final)); original['kind'] = 'fishgram-signing-qa'
        original['checks']['searchEightAcceptance'] = False
        original['checks']['updaterFullMatrix'] = False
        final['kind'] = 'fishgram-beta-qa'
        final['deferredSearch'] = ['largeScalePerformance', 'nativeOmissionControlSamples']
        final['checks']['searchEightAcceptance'] = False
        final['checks']['remainingSearchAcceptance'] = True
        raw = json.dumps(original).encode()
        signing = {'qa': {'file': 'qa.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                         'approvedAt': original['approvedAt'], 'document': base64.b64encode(raw).decode()},
                   'signedAt': original['approvedAt']}
        self.assertTrue(publish.verify_signing_approval(manifest, final, signing, 'qa.json', json.dumps(final).encode()))
        proof, _ = publish.public_acceptance_records(json.dumps(final).encode(), signing, separate_approval=True)
        self.assertEqual(json.loads(proof.data)['deferredSearch'], final['deferredSearch'])
        self.assertEqual(json.loads(proof.data)['releaseChannel'], 'beta')
        final['checks']['updaterFullMatrix'] = False
        with self.assertRaises(publish.PublishError):
            publish.verify_signing_approval(manifest, final, signing, 'qa.json', json.dumps(final).encode())

    def test_beta_index_for_stable_signed_package_is_prerelease_with_limitations(self):
        api = FakeApi()
        publish_plan(api, plan(index_channel='beta', package_channel='stable', deferred_search=True))
        self.assertTrue(api.prerelease)
        self.assertIn('search performance', api.release_body)
        self.assertIn('control samples', api.release_body)
        self.assertNotIn('stable.json', api.pages)

    def test_beta_readme_discloses_search_gaps_without_modifying_payload(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            z.writestr('README.txt', b'candidate'); z.writestr('Telegram.exe', b'cloud')
        asset = publish.portable_package(data.getvalue(), '7.3.1-r9', beta=True, deferred_search=True)
        with zipfile.ZipFile(io.BytesIO(asset.data)) as z:
            self.assertEqual(z.read('Telegram.exe'), b'cloud')
            self.assertIn(b'Beta', z.read('README.txt'))
            self.assertIn(b'control samples', z.read('README.txt'))

    def test_signing_approval_is_bound_before_final_installation_acceptance(self):
        import test_release_gate as fixture
        manifest = fixture.manifest()
        final = fixture.qa_for(manifest)
        preliminary = fixture.qa_for(manifest)
        preliminary['kind'] = 'fishgram-signing-qa'
        preliminary['checks']['searchEightAcceptance'] = False
        preliminary['checks']['updaterFullMatrix'] = False
        raw = json.dumps(preliminary).encode()
        signing = {'qa': {'file': 'approved.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                         'approvedAt': preliminary['approvedAt'],
                         'document': base64.b64encode(raw).decode()},
                   'signedAt': preliminary['approvedAt']}
        self.assertTrue(publish.verify_signing_approval(manifest, final, signing, 'approved.json', b'final'))
        changed = json.loads(json.dumps(final))
        changed['candidate']['sourceCommit'] = 'f' * 40
        with self.assertRaises(publish.PublishError):
            publish.verify_signing_approval(manifest, changed, signing, 'approved.json', b'final')
        earlier = json.loads(json.dumps(final))
        earlier['approvedAt'] = '2026-10-01T00:00:00Z'
        with self.assertRaises(publish.PublishError):
            publish.verify_signing_approval(manifest, earlier, signing, 'approved.json', b'final')
        corrupt = json.loads(json.dumps(signing))
        corrupt['qa']['document'] = 'invalid-base64'
        with self.assertRaises(publish.PublishError):
            publish.verify_signing_approval(manifest, final, corrupt, 'approved.json', b'final')
        signing['qa']['sha256'] = '0' * 64
        with self.assertRaises(publish.PublishError):
            publish.verify_signing_approval(manifest, final, signing, 'approved.json', b'final')

    def test_portable_package_preserves_programs_and_replaces_internal_readme(self):
        source = io.BytesIO()
        files = {'Telegram.exe': b'tested-client', 'Updater.exe': b'tested-updater',
                 'Start-FishGram.cmd': b'launcher', 'LICENSE': b'license',
                 'LEGAL': b'exceptions', 'build-manifest.json': b'{}',
                 'README.txt': b'internal candidate; not a public release'}
        with zipfile.ZipFile(source, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        asset = publish.portable_package(source.getvalue(), '7.3.1-r9')
        self.assertEqual(asset.name, 'FishGram-7.3.1-r9-windows-x64.zip')
        with zipfile.ZipFile(io.BytesIO(asset.data)) as archive:
            self.assertEqual(set(archive.namelist()), set(files))
            for name in set(files) - {'README.txt'}:
                self.assertEqual(archive.read(name), files[name])
            self.assertIn(b'Start-FishGram.cmd', archive.read('README.txt'))
            self.assertNotIn(b'internal candidate', archive.read('README.txt'))
        self.assertEqual(asset.data, publish.portable_package(source.getvalue(), '7.3.1-r9').data)
        # Stored entries make the final bytes independent of zlib implementations.
        with mock.patch('zipfile.zlib', None):
            self.assertEqual(asset.data, publish.portable_package(source.getvalue(), '7.3.1-r9').data)

    def test_public_distribution_record_binds_final_zip_to_candidate_and_publisher(self):
        candidate = plan(reviewed_commit='d' * 40)
        portable = candidate.assets[1]
        manifest = json.loads(next(a.data for a in candidate.assets if a.name == 'candidate-record.json'))
        asset = publish.distribution_record(portable, manifest, 'd' * 40)
        public = json.loads(asset.data)
        self.assertEqual(public['archive']['sha256'], hashlib.sha256(portable.data).hexdigest())
        self.assertEqual(public['archive']['size'], len(portable.data))
        self.assertEqual(public['candidateArchive'], manifest['archive'])
        self.assertEqual(public['reviewedPublisherCommit'], 'd' * 40)
        api = FakeApi()
        publish_plan(api, candidate)
        self.assertEqual(api.assets[asset.name], asset.data)
        self.assertIn(('download', asset.name), api.events)

    def test_reuse_preserves_original_distribution_when_reviewed_main_advances(self):
        from dataclasses import replace
        candidate = plan(index_channel='beta', package_channel='stable', reviewed_commit='d' * 40)
        portable = candidate.assets[1]
        manifest = json.loads(next(a.data for a in candidate.assets if a.name == 'candidate-record.json'))
        original = publish.distribution_record(portable, manifest, 'd' * 40)
        api = FakeApi()
        publish_plan(api, candidate)
        before = dict(api.assets)
        current = publish.distribution_record(portable, manifest, 'e' * 40)
        current_assets = tuple(current if a.name == current.name else a for a in candidate.assets)
        publish_plan(api, replace(candidate, assets=current_assets, reviewed_commit='e' * 40, reuse_release=True))
        self.assertEqual(api.assets, before)
        self.assertEqual(json.loads(api.assets[original.name])['reviewedPublisherCommit'], 'd' * 40)

    def test_unmerged_source_stops_before_any_publication_write(self):
        api = FakeApi()
        api.verify_source_merged = mock.Mock(side_effect=RuntimeError('unmerged source'))
        with self.assertRaises(publish.PublishError):
            publish_plan(api)
        self.assertFalse(any(event[0] in {'source_tag', 'create_release', 'upload',
                                          'publish_release', 'atomic_pages', 'page'} for event in api.events))

    def test_racing_product_tag_target_stops_before_upload_or_publish(self):
        api = FakeApi()
        api.tag_commit = mock.Mock(return_value='d' * 40)
        with self.assertRaises(publish.PublishError):
            publish_plan(api)
        self.assertFalse(any(event[0] in {'upload', 'publish_release', 'atomic_pages', 'page'}
                             for event in api.events))

    def test_public_download_description_distinguishes_updater_and_authenticode_signatures(self):
        api = FakeApi()
        publish_plan(api)
        self.assertIn('not Windows Authenticode', api.release_body)
        self.assertIn('update packages', api.release_body)

    def test_reuse_resumes_beta_index_after_pages_failure_without_release_writes(self):
        from dataclasses import replace
        for channel in ('beta', 'stable'):
            with self.subTest(package_channel=channel):
                candidate = plan(index_channel='beta', package_channel=channel)
                api = FakeApi()
                atomic = api.put_pages_atomic
                api.put_pages_atomic = mock.Mock(side_effect=RuntimeError('Pages unavailable'))
                with self.assertRaisesRegex(publish.PublishError, 'atomic promotion'):
                    publish_plan(api, candidate)
                self.assertIn(candidate.tag, api.releases)
                self.assertNotIn('beta.json', api.pages)
                api.put_pages_atomic = atomic
                api.events.clear()
                publish_plan(api, replace(candidate, reuse_release=True))
                self.assertEqual(api.pages['beta.json'], candidate.index_bytes)
                self.assertFalse(any(e[0] in {'upload', 'create_release', 'source_tag',
                                              'publish_release'} for e in api.events))

    def test_reuse_same_index_is_idempotent_but_rejects_conflict_and_downgrade(self):
        candidate = plan(index_channel='beta', package_channel='beta', reuse_release=True)
        for change in ('same', 'hash', 'newer', 'tag', 'asset'):
            with self.subTest(change=change):
                current = json.loads(candidate.index_bytes)
                if change == 'hash':
                    current['update']['sha256'] = 'f' * 64
                elif change == 'newer':
                    current['update']['version'] = '30073399661297666'
                    current['update']['url'] = current['update']['url'].replace('-r1', '-r2')
                api = FakeApi(index=current, tags={candidate.tag}, releases={candidate.tag})
                api.prerelease = True
                api.pages[candidate.index_path] = candidate.index_bytes
                api.source_tags.add(candidate.tag)
                api.assets = {a.name: a.data for a in candidate.assets}
                if change == 'tag':
                    api.tag_commit = lambda *args: 'd' * 40
                if change == 'asset':
                    api.assets[candidate.assets[0].name] += b'tampered'
                if change == 'same':
                    publish_plan(api, candidate)
                    self.assertFalse(any(e[0] == 'page' for e in api.events))
                else:
                    with self.assertRaises(publish.PublishError):
                        publish_plan(api, candidate)
                    self.assertFalse(any(e[0] in {'page', 'atomic_pages', 'upload'} for e in api.events))

    def test_reuse_equal_branch_index_waits_for_public_deployment(self):
        candidate = plan(reuse_release=True)
        api = FakeApi(index=json.loads(candidate.index_bytes),
                      tags={candidate.tag}, releases={candidate.tag})
        api.source_tags.add(candidate.tag)
        api.assets = {a.name: a.data for a in candidate.assets}
        reads = []
        def public(path):
            if path != 'stable.json':
                return next(p.data for p in candidate.page_files if p.path == path)
            reads.append(path)
            return None if len(reads) == 1 else candidate.index_bytes
        api.get_page = public
        wait = publish.wait_public_page
        with mock.patch.object(publish, 'wait_public_page',
                               side_effect=lambda a, p, b: wait(a, p, b, interval=0)):
            publish_plan(api, candidate)
        self.assertEqual(reads, ['stable.json', 'stable.json'])
        self.assertFalse(any(e[0] in {'page', 'upload'} for e in api.events))

    def test_reuse_equal_index_still_requires_authoritative_trust_validation(self):
        candidate = plan(reuse_release=True)
        api = FakeApi(index=json.loads(candidate.index_bytes),
                      tags={candidate.tag}, releases={candidate.tag})
        api.source_tags.add(candidate.tag)
        api.assets = {a.name: a.data for a in candidate.assets}
        api.pages[candidate.index_path] = candidate.index_bytes
        api.put_pages_atomic = mock.Mock(side_effect=RuntimeError('newer authoritative trust'))
        with self.assertRaisesRegex(publish.PublishError, 'atomic promotion'):
            publish_plan(api, candidate)
        self.assertFalse(any(e[0] == 'page' for e in api.events))

    def test_matching_public_trust_still_requires_authoritative_validation(self):
        api = FakeApi()
        candidate = plan()
        api.pages = {item.path: item.data for item in candidate.page_files}
        api.put_pages_atomic = mock.Mock(side_effect=RuntimeError('authoritative newer trust'))
        with self.assertRaisesRegex(publish.PublishError, 'atomic promotion'):
            publish_plan(api, candidate)
        api.put_pages_atomic.assert_called_once()
        self.assertNotIn('stable.json', api.pages)

    def test_public_proof_rejects_nested_or_invalid_time_fields(self):
        qa = {'approvedAt': '2026-10-13T00:00:00Z', 'candidate': {}, 'checks': {}}
        for field, value in (('signedAt', {'notes': 'private-time-marker'}),
                             ('approvedAt', {'notes': 'private-time-marker'}),
                             ('signedAt', '2026-99-99T00:00:00Z'),
                             ('approvedAt', '2026-10-12T00:00:00Z')):
            signing = {'qa': {'sha256': 'a' * 64, 'approvedAt': qa['approvedAt']},
                       'signedAt': '2026-10-13T01:00:00Z'}
            if field == 'signedAt':
                signing[field] = value
            else:
                signing['qa'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(publish.PublishError):
                publish.public_acceptance_records(json.dumps(qa).encode(), signing)

    def test_public_qa_proof_and_signing_record_omit_identity_and_raw_notes(self):
        qa = {'schema': 1, 'kind': 'fishgram-candidate-qa', 'approver': 'private-identity',
              'approvedAt': '2026-10-13T00:00:00Z', 'environment': 'private-machine-description',
              'candidate': {'version': '7.2.9-r8'}, 'checks': {'clean': True},
              'notes': 'private-account-query'}
        signing = {'schema': 1, 'kind': 'fishgram-signed-candidate',
                   'qa': {'file': 'private-qa-name.json', 'sha256': 'a' * 64,
                          'approver': qa['approver'], 'approvedAt': qa['approvedAt']},
                   'package': {'name': 'update', 'sha256': 'b' * 64},
                   'issuer': {'id': 'issuer-a', 'alg': 'Ed25519',
                              'publicKeySha256': 'c' * 64, 'notes': 'private-issuer-note'},
                   'signedAt': '2026-10-13T01:00:00Z'}
        raw = json.dumps(qa).encode()
        proof, record = publish.public_acceptance_records(raw, signing)
        self.assertEqual(proof.name, 'qa-proof.json')
        self.assertEqual(record.name, 'signing-record.json')
        parsed = json.loads(proof.data)
        self.assertEqual(parsed['originalSha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(parsed['candidate'], qa['candidate'])
        self.assertEqual(parsed['checks'], qa['checks'])
        for secret in (qa['approver'], qa['environment'], qa['notes'], 'private-qa-name.json',
                       'private-issuer-note'):
            self.assertNotIn(secret.encode(), proof.data + record.data)
        self.assertEqual(json.loads(record.data)['qa'], {
            'originalSha256': 'a' * 64, 'approvedAt': qa['approvedAt'],
            'publicProofSha256': proof.sha256})
        self.assertEqual(signing['qa']['approver'], 'private-identity')

    def test_offline_handoff_rejects_colliding_or_unsafe_paths_before_staging(self):
        from dataclasses import replace
        original = plan()
        variants = (
            replace(original, assets=original.assets + (publish.Asset('candidate-record.json', b'other'),)),
            replace(original, assets=original.assets + (publish.Asset('SIGNING-RECORD.JSON', b'other'),)),
            replace(original, assets=original.assets + (publish.Asset('../escape.json', b'other'),)),
            replace(original, page_files=original.page_files + (original.page_files[0],)),
            replace(original, page_files=original.page_files + (publish.PageFile('keys/../../escape', b'other'),)),
        )
        for candidate in variants:
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / 'promotion'
                with self.assertRaises(publish.PublishError):
                    publish.write_promotion(candidate, output)
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_public_asset_transient_failure_retries_same_published_beta(self):
        candidate = plan(index_channel="beta", package_channel="beta")
        api = FakeApi()
        original = api.download_asset
        calls = []
        def download(tag, name):
            calls.append((tag, name))
            if len(calls) == 1:
                raise TimeoutError("temporary CDN timeout")
            return original(tag, name)
        api.download_asset = download
        with mock.patch.object(publish.time, "sleep"):
            publish_plan(api, candidate)
        self.assertEqual(calls[:2], [(candidate.tag, candidate.assets[0].name)] * 2)
        self.assertEqual(sum(event[0] == "create_release" for event in api.events), 1)
        self.assertEqual(api.pages["beta.json"], candidate.index_bytes)

    def test_public_asset_permanent_failure_has_bounded_retries_and_no_index(self):
        api = FakeApi()
        api.download_asset = mock.Mock(side_effect=TimeoutError("private CDN detail"))
        with mock.patch.object(publish.time, "sleep") as sleep:
            with self.assertRaisesRegex(publish.PublishError, "Public release asset download failed"):
                publish_plan(api)
        self.assertEqual(api.download_asset.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(api.pages)

    def test_reuse_rejects_extra_or_missing_assets_before_any_publication(self):
        candidate = plan(reuse_release=True)
        for mismatch in ("extra", "missing"):
            with self.subTest(mismatch=mismatch):
                api = FakeApi(tags={candidate.tag}, releases={candidate.tag})
                api.source_tags.add(candidate.tag)
                api.assets = {asset.name: asset.data for asset in candidate.assets}
                if mismatch == "extra":
                    api.assets["unreviewed.exe"] = b"unreviewed"
                else:
                    del api.assets["qa-proof.json"]
                with self.assertRaisesRegex(publish.PublishError, "asset.*set"):
                    publish_plan(api, candidate)
                self.assertFalse(any(event[0] in {"source_tag", "create_release", "upload",
                    "download", "atomic_pages", "page"} for event in api.events))

    def test_reuse_asset_inventory_failure_stops_before_any_publication(self):
        candidate = plan(reuse_release=True)
        api = FakeApi(tags={candidate.tag}, releases={candidate.tag})
        api.source_tags.add(candidate.tag)
        api.assets = {asset.name: asset.data for asset in candidate.assets}
        api.asset_names = mock.Mock(side_effect=RuntimeError("private transport detail"))
        with self.assertRaisesRegex(publish.PublishError, "asset.*set"):
            publish_plan(api, candidate)
        self.assertFalse(any(event[0] in {"download", "atomic_pages", "page"}
                             for event in api.events))

    def test_reused_tags_must_match_both_candidate_commits_before_pages_writes(self):
        candidate = plan(reuse_release=True)
        for repository in (publish.PRODUCT_REPOSITORY, publish.SOURCE_REPOSITORY):
            api = FakeApi(tags={candidate.tag}, releases={candidate.tag})
            api.source_tags.add(candidate.tag)
            api.tag_commit = lambda repo, tag: "d" * 40 if repo == repository else (
                "b" * 40 if repo == publish.PRODUCT_REPOSITORY else "c" * 40)
            with self.subTest(repository=repository), self.assertRaisesRegex(publish.PublishError, "tag"):
                publish_plan(api, candidate)
            self.assertFalse(any(event[0] == "page" for event in api.events))

    def test_candidate_tag_and_review_commit_survive_offline_publication(self):
        from dataclasses import replace
        candidate = plan(reviewed_commit="c" * 40)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "promotion"
            publish.write_promotion(candidate, output)
            metadata = json.loads((output / "promotion.json").read_text())
            self.assertEqual(metadata["targetCommit"], "b" * 40)
            self.assertEqual(metadata["repositoryInputs"][publish.PRODUCT_REPOSITORY]["reviewedCommit"], "c" * 40)
            api = FakeApi()
            api.create_release = mock.Mock(wraps=api.create_release)
            with mock.patch.dict(sys.modules, {"github_release_transport": SimpleNamespace(
                    GitHubReleaseTransport=SimpleNamespace(from_environment=lambda: api))}):
                self.assertEqual(publish.main(["publish", "--promotion", str(output),
                    "--enable-publication", "--event", "workflow_dispatch", "--ref", "refs/heads/main",
                    "--environment", "release", "--approval-marker", "1"]), 0)
            self.assertEqual(api.create_release.call_args.args[1], "b" * 40)

    def test_offline_promotion_rejects_tag_pointing_at_later_review_commit(self):
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(publish.PublishError, "candidate"):
                publish.write_promotion(replace(plan(), target_commit="c" * 40), Path(temp) / "promotion")

    def test_publication_rejects_tampered_tag_before_requesting_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "promotion"
            publish.write_promotion(plan(), output)
            record = output / "promotion.json"
            metadata = json.loads(record.read_text())
            metadata["targetCommit"] = "d" * 40
            record.write_text(json.dumps(metadata))
            factory = mock.Mock(side_effect=AssertionError("Token access must not occur"))
            with mock.patch.dict(sys.modules, {"github_release_transport": SimpleNamespace(
                    GitHubReleaseTransport=SimpleNamespace(from_environment=factory))}):
                self.assertEqual(publish.main(["publish", "--promotion", str(output),
                    "--enable-publication", "--event", "workflow_dispatch", "--ref", "refs/heads/main",
                    "--environment", "release", "--approval-marker", "1"]), 1)
            factory.assert_not_called()

    def test_release_workflow_serializes_channels_and_exposes_reuse(self):
        workflow = (MODULE.parent.parent / ".github/workflows/publish-release.yml").read_text()
        group = next(line.strip() for line in workflow.splitlines() if line.strip().startswith("group:"))
        self.assertEqual(group, "group: fishgram-publication")
        self.assertIn("      reuse_release:", workflow)
        self.assertIn("REUSE_RELEASE: ${{ inputs.reuse_release }}", workflow)
        self.assertIn("$args += '--reuse-release'", workflow)

    def test_candidate_and_signer_have_history_for_baseline_and_review_gates(self):
        for name in ("product-candidate.yml", "sign-release-candidate.yml", "publish-release.yml"):
            with self.subTest(workflow=name):
                workflow = (MODULE.parent.parent / ".github/workflows" / name).read_text()
                self.assertIn("fetch-depth: 0", workflow)

    def test_first_trust_read_recovers_before_promoting_index(self):
        api = FakeApi()
        original_get = api.get_page
        calls = [0]
        def get_page(path):
            calls[0] += 1
            if calls[0] == 1:
                raise OSError("temporary 503")
            return original_get(path)
        api.get_page = get_page
        ticks = [0]
        original_read = publish.wait_public_page
        with mock.patch.object(publish, "wait_public_page", side_effect=lambda api, path, expected:
                original_read(api, path, expected, clock=lambda: ticks[0],
                              sleep=lambda value: ticks.__setitem__(0, ticks[0] + value))):
            publish_plan(api)
        self.assertEqual(api.pages["stable.json"], plan().index_bytes)
        self.assertEqual(ticks[0], 5)

    def test_signed_windows_x64_identity_and_mismatched_platform_version_channel(self):
        publish.verify_package_identity(SimpleNamespace(channel=0, version=9, target=(0, 1)), "9", "stable")
        for channel, version, target in ((0, 9, (1, 0)), (0, 8, (0, 1)), (1, 9, (0, 1))):
            with self.assertRaises(publish.PublishError):
                publish.verify_package_identity(SimpleNamespace(channel=channel, version=version, target=target), "9", "stable")

    def test_public_pages_retries_old_bytes_http_failure_and_then_matches(self):
        api = FakeApi()
        api.get_page = mock.Mock(side_effect=[b"old", OSError("deployment pending"), b"new"])
        ticks = [0]
        publish.wait_public_page(api, "stable.json", b"new", timeout=10, interval=1,
                                 clock=lambda: ticks[0], sleep=lambda value: ticks.__setitem__(0, ticks[0] + value))
        self.assertEqual(api.get_page.call_count, 3)

    def test_public_pages_timeout_is_bounded(self):
        api = FakeApi()
        ticks = [0]
        with self.assertRaisesRegex(publish.PublishError, "timeout"):
            publish.wait_public_page(api, "stable.json", b"new", timeout=3, interval=1,
                                     clock=lambda: ticks[0], sleep=lambda value: ticks.__setitem__(0, ticks[0] + value))
        self.assertEqual(ticks[0], 3)

    def test_signer_qa_commit_can_follow_candidate_but_must_be_in_reviewed_history(self):
        with tempfile.TemporaryDirectory(dir=MODULE.parent.parent) as temp:
            root = Path(temp)
            def git(*args):
                return subprocess.check_output(["git", "-C", temp, *args], stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-b", "main")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "user.name", "Fixture")
            git("commit", "--allow-empty", "-m", "candidate")
            candidate = git("rev-parse", "HEAD")
            qa = root / "release/qa/approved.json"
            qa.parent.mkdir(parents=True)
            qa.write_text('{}')
            git("add", "release/qa/approved.json")
            git("commit", "-m", "QA")
            signer = git("rev-parse", "HEAD")
            git("commit", "--allow-empty", "-m", "publish")
            reviewed = git("rev-parse", "HEAD")
            publish.verify_signing_history(root, signer, candidate, reviewed, "release/qa/approved.json")
            with self.assertRaises(publish.PublishError):
                publish.verify_signing_history(root, reviewed, candidate, signer, "release/qa/approved.json")
            qa.write_text('{"changed":true}')
            git("add", "release/qa/approved.json")
            git("commit", "-m", "changed QA")
            with self.assertRaisesRegex(publish.PublishError, "QA differs"):
                publish.verify_signing_history(root, signer, candidate, git("rev-parse", "HEAD"), "release/qa/approved.json")

    def test_signer_history_allows_only_completion_of_same_candidate_signing_request(self):
        import test_release_gate as fixture
        with tempfile.TemporaryDirectory(dir=MODULE.parent.parent) as temp:
            root = Path(temp)
            def git(*args):
                return subprocess.check_output(['git', '-C', temp, *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init', '-b', 'main'); git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'user.name', 'Fixture'); git('commit', '--allow-empty', '-m', 'candidate')
            candidate = git('rev-parse', 'HEAD')
            final = fixture.qa_for(fixture.manifest())
            preliminary = json.loads(json.dumps(final)); preliminary['kind'] = 'fishgram-signing-qa'
            preliminary['checks']['updaterFullMatrix'] = False
            preliminary['checks']['searchEightAcceptance'] = False
            qa = root/'release/qa/approved.json'; qa.parent.mkdir(parents=True)
            qa.write_text(json.dumps(preliminary)); git('add', '.'); git('commit', '-m', 'signing request')
            signer = git('rev-parse', 'HEAD')
            qa.write_text(json.dumps(final)); git('commit', '-am', 'completed installation acceptance')
            publish.verify_signing_history(root, signer, candidate, git('rev-parse', 'HEAD'), 'release/qa/approved.json')
            final['candidate']['sourceCommit'] = 'f' * 40
            qa.write_text(json.dumps(final)); git('commit', '-am', 'different candidate')
            with self.assertRaises(publish.PublishError):
                publish.verify_signing_history(root, signer, candidate, git('rev-parse', 'HEAD'), 'release/qa/approved.json')

    def test_reuse_release_survives_offline_handoff_and_cleans_staging(self):
        with tempfile.TemporaryDirectory(dir=MODULE.parent.parent) as temp:
            output = Path(temp) / "promotion"
            publish.write_promotion(plan(reuse_release=True), output)
            record = json.loads((output / "promotion.json").read_text())
            self.assertIs(record["reuseRelease"], True)
            self.assertEqual(list(Path(temp).glob(".fishgram-promotion-*")), [])
            transport_spec = importlib.util.spec_from_file_location(
                "github_release_transport", MODULE.with_name("github_release_transport.py"))
            github_release_transport = importlib.util.module_from_spec(transport_spec)
            sys.modules[transport_spec.name] = github_release_transport
            transport_spec.loader.exec_module(github_release_transport)
            api = FakeApi(tags={plan().tag}, releases={plan().tag})
            api.source_tags.add(plan().tag)
            api.assets = {asset.name: asset.data for asset in plan().assets}
            with mock.patch.object(github_release_transport.GitHubReleaseTransport,
                                   "from_environment", return_value=api):
                self.assertEqual(publish.main(["publish", "--promotion", str(output),
                    "--enable-publication", "--event", "workflow_dispatch", "--ref", "refs/heads/main",
                    "--environment", "release", "--approval-marker", "1"]), 0)
            self.assertFalse(any(event[0] in {"create_release", "upload"} for event in api.events))
            self.assertEqual(api.pages["stable.json"], plan().index_bytes)

    def test_publisher_pin_set_includes_publisher_transport_and_license_inventory(self):
        self.assertTrue({
            "tools/publish_release.py",
            "tools/github_release_transport.py",
            "tools/license_inventory.py",
            "config/license-sources.json",
            ".github/workflows/publish-release.yml",
        }.issubset(set(publish.PUBLISH_TRACKED)))

    def test_signing_run_provenance_binds_exact_manual_successful_run_and_artifact(self):
        run = {"repository": {"full_name": publish.PRODUCT_REPOSITORY}, "id": 200,
               "path": ".github/workflows/sign-release-candidate.yml", "event": "workflow_dispatch",
               "conclusion": "success", "head_branch": "main", "head_sha": "a" * 40}
        jobs = {"jobs": [{"name": "sign-candidate", "conclusion": "success"}]}
        artifacts = {"artifacts": [{"name": "fishgram-signed-candidate-100", "expired": False,
                                    "workflow_run": {"id": 200}}]}
        publish.verify_signing_run_provenance(run, jobs, artifacts, publish.PRODUCT_REPOSITORY,
                                               200, 100, "a" * 40)
        for mutate in (lambda value: value.update(conclusion="failure"),
                       lambda value: value.update(head_branch="feature"),
                       lambda value: value.update(path=".github/workflows/product-candidate.yml")):
            bad = dict(run)
            mutate(bad)
            with self.assertRaises(publish.PublishError):
                publish.verify_signing_run_provenance(bad, jobs, artifacts,
                                                       publish.PRODUCT_REPOSITORY, 200, 100, "a" * 40)

    def test_first_publish_verifies_each_public_asset_and_promotes_index_last(self):
        api = FakeApi()

        publish_plan(api)

        expected_assets = [asset.name for asset in plan().assets]
        uploaded = [event[1] for event in api.events if event[0] == "upload"]
        downloaded = [event[1] for event in api.events if event[0] == "download"]
        self.assertEqual(uploaded, expected_assets)
        self.assertEqual(downloaded, expected_assets)
        self.assertEqual(api.events[-2:], [("page", "stable.json"), ("get_page", "stable.json")])
        self.assertEqual(api.pages["stable.json"], plan().index_bytes)
        self.assertEqual([event for event in api.events if event[0] == 'atomic_pages'],
                         [('atomic_pages', tuple(item.path for item in plan().page_files))])
        self.assertFalse(any(event[0] == 'page' and event[1].startswith('keys/') for event in api.events))

    def test_transport_without_atomic_trust_capability_stops_before_writes(self):
        api = FakeApi()
        api.put_pages_atomic = None
        with self.assertRaisesRegex(publish.PublishError, 'atomically'):
            publish_plan(api)
        self.assertFalse(any(event[0] in ('source_tag', 'create_release', 'upload', 'page') for event in api.events))

    def test_unchanged_public_trust_still_checks_authoritative_transport(self):
        api = FakeApi()
        api.pages = {item.path: item.data for item in plan().page_files}
        publish_plan(api)
        self.assertEqual(sum(event[0] == 'atomic_pages' for event in api.events), 1)
        self.assertEqual([event for event in api.events if event[0] == 'page'], [('page', 'stable.json')])


    def test_existing_release_is_never_overwritten(self):
        for tags in ((), {"v7.2.9-r1"}):
            api = FakeApi(tags=tags, releases={"v7.2.9-r1"})
            with self.assertRaisesRegex(publish.PublishError, "already exists"):
                publish_plan(api)
            self.assertFalse(any(event[0] in {"create_release", "upload", "page"}
                                 for event in api.events))

    def test_rerun_after_release_creation_failure_preserves_exact_tags(self):
        from dataclasses import replace
        candidate = plan(reviewed_commit="e" * 40)
        for product_tag_created in (False, True):
            with self.subTest(product_tag_created=product_tag_created):
                api = FakeApi()
                create_release = api.create_release

                def interrupted_create(tag, target, title, body, *, prerelease):
                    if product_tag_created:
                        api.tags.add(tag)
                    raise RuntimeError("temporary release creation failure")

                api.create_release = interrupted_create
                with self.assertRaisesRegex(RuntimeError, "temporary release"):
                    publish_plan(api, candidate)
                self.assertEqual(api.source_tags, {candidate.tag})
                self.assertFalse(api.releases or api.assets or api.pages)
                api.create_release = create_release

                publish_plan(api, candidate)

                self.assertEqual(api.source_tags, {candidate.tag})
                self.assertEqual([event for event in api.events if event[0] == "source_tag"],
                                 [("source_tag", candidate.tag, "c" * 40)])
                self.assertIn(("create_release", candidate.tag, "b" * 40), api.events)
                self.assertEqual(api.assets, {asset.name: asset.data for asset in candidate.assets})
                self.assertEqual(api.pages["stable.json"], candidate.index_bytes)
                self.assertEqual(api.events[-2:], [("page", "stable.json"), ("get_page", "stable.json")])

    def test_matching_product_tag_without_source_tag_can_resume(self):
        candidate = plan()
        api = FakeApi(tags={candidate.tag})

        publish_plan(api, candidate)

        self.assertIn(("source_tag", candidate.tag, "c" * 40), api.events)
        self.assertIn(("create_release", candidate.tag, "b" * 40), api.events)
        self.assertEqual(api.pages["stable.json"], candidate.index_bytes)

    def test_partial_tag_resume_through_rest_transport_requires_exact_target(self):
        from test_github_release_transport import Stub, transport_module
        from dataclasses import replace
        candidate = plan(reviewed_commit="e" * 40)
        for repository in (publish.PRODUCT_REPOSITORY, publish.SOURCE_REPOSITORY):
            for matching in (False, True):
                with self.subTest(repository=repository, matching=matching):
                    stub = Stub()
                    stub.source_tags = {candidate.tag} if repository == publish.SOURCE_REPOSITORY else set()
                    if repository == publish.PRODUCT_REPOSITORY:
                        stub.tags.add(candidate.tag)

                    def request(method, url, headers, body, timeout):
                        if method == "GET" and f"/repos/{repository}/git/ref/tags/" in url:
                            commit = ("b" if repository == publish.PRODUCT_REPOSITORY else "c") * 40
                            return 200, {}, json.dumps({"object": {
                                "type": "commit", "sha": commit if matching else "e" * 40}}).encode()
                        return stub(method, url, headers, body, timeout)

                    api = transport_module.GitHubReleaseTransport(
                        product_token="fixture", source_token="source-fixture", request=request)
                    if not matching:
                        with self.assertRaisesRegex(publish.PublishError, "exact candidate commits"):
                            publish_plan(api, candidate)
                        self.assertFalse(any(call[0] in {"POST", "PUT", "PATCH", "DELETE"}
                                             for call in stub.calls))
                        continue

                    publish_plan(api, candidate)

                    self.assertEqual(stub.tags, {candidate.tag})
                    self.assertEqual(stub.source_tags, {candidate.tag})
                    self.assertEqual(stub.assets, {asset.name: asset.data for asset in candidate.assets})
                    self.assertEqual(stub.files["stable.json"], candidate.index_bytes)
                    created = next(json.loads(call[3]) for call in stub.calls
                                   if call[0] == "POST" and call[1].endswith("/releases"))
                    self.assertEqual(created["target_commitish"], "b" * 40)
                    if repository == publish.SOURCE_REPOSITORY:
                        self.assertFalse(any(call[0] != "GET" and f"/repos/{repository}/git/" in call[1]
                                             for call in stub.calls))

    def test_partial_tags_must_match_candidate_commits_before_any_write(self):
        candidate = plan()
        for repository, product, source in (
                (publish.PRODUCT_REPOSITORY, True, False),
                (publish.SOURCE_REPOSITORY, False, True),
                (publish.PRODUCT_REPOSITORY, True, True),
                (publish.SOURCE_REPOSITORY, True, True)):
            with self.subTest(repository=repository, product=product, source=source):
                api = FakeApi(tags={candidate.tag} if product else ())
                if source:
                    api.source_tags.add(candidate.tag)
                api.tag_commit = lambda repo, tag: "e" * 40 if repo == repository else (
                    "b" * 40 if repo == publish.PRODUCT_REPOSITORY else "c" * 40)
                with self.assertRaisesRegex(publish.PublishError, "tag"):
                    publish_plan(api, candidate)
                self.assertFalse(any(event[0] in {"source_tag", "create_release", "upload", "page"}
                                     for event in api.events))

    def test_unresolvable_partial_tag_is_refused_before_any_write(self):
        candidate = plan()
        for repository in (publish.PRODUCT_REPOSITORY, publish.SOURCE_REPOSITORY):
            with self.subTest(repository=repository):
                api = FakeApi(tags={candidate.tag} if repository == publish.PRODUCT_REPOSITORY else ())
                if repository == publish.SOURCE_REPOSITORY:
                    api.source_tags.add(candidate.tag)
                api.tag_commit = mock.Mock(side_effect=RuntimeError("private API error detail"))
                with self.assertRaisesRegex(publish.PublishError, "could not be verified"):
                    publish_plan(api, candidate)
                self.assertFalse(any(event[0] in {"source_tag", "create_release", "upload", "page"}
                                     for event in api.events))

    def test_resume_rejects_invalid_candidate_pins_and_review_commit_as_target(self):
        from dataclasses import replace
        for record in ({"parentCommit": "e" * 40, "sourceCommit": "c" * 40},
                       {"parentCommit": "b" * 40, "sourceCommit": None},
                       {"parentCommit": "b" * 40, "sourceCommit": "c" * 39},
                       {"parentCommit": "b" * 40}, []):
            with self.subTest(record=record):
                candidate = plan()
                candidate = replace(candidate, assets=tuple(
                    publish.Asset(asset.name, json.dumps(record).encode())
                    if asset.name == "candidate-record.json" else asset for asset in candidate.assets))
                api = FakeApi(tags={candidate.tag})
                api.source_tags.add(candidate.tag)
                with self.assertRaises(publish.PublishError):
                    publish_plan(api, candidate)
                self.assertFalse(any(event[0] in {"source_tag", "create_release", "upload", "page"}
                                     for event in api.events))

    def test_resumed_product_tag_cannot_use_review_commit_instead_of_candidate(self):
        from dataclasses import replace
        candidate = replace(plan(), target_commit="e" * 40, reviewed_commit="e" * 40)
        api = FakeApi(tags={candidate.tag})
        api.tag_commit = lambda repo, tag: "e" * 40

        with self.assertRaisesRegex(publish.PublishError, "exact candidate"):
            publish_plan(api, candidate)

        self.assertFalse(any(event[0] in {"source_tag", "create_release", "upload", "page"}
                             for event in api.events))

    def test_reuse_release_still_requires_all_three_objects(self):
        candidate = plan(reuse_release=True)
        for missing in ("product", "source", "release"):
            with self.subTest(missing=missing):
                api = FakeApi(tags=() if missing == "product" else {candidate.tag},
                              releases=() if missing == "release" else {candidate.tag})
                if missing != "source":
                    api.source_tags.add(candidate.tag)
                with self.assertRaisesRegex(publish.PublishError, "both immutable"):
                    publish_plan(api, candidate)
                self.assertFalse(any(event[0] in {"source_tag", "create_release", "upload", "page"}
                                     for event in api.events))

    def test_resumed_public_download_mismatch_leaves_index_unpromoted(self):
        api = FakeApi(tags={plan().tag}, corrupt_download="candidate-record.json")
        api.source_tags.add(plan().tag)

        with self.assertRaisesRegex(publish.PublishError, "SHA256"):
            publish_plan(api)

        self.assertFalse(any(event[0] in {"source_tag", "page"} for event in api.events))


    def test_upload_failure_leaves_index_unpromoted(self):
        api = FakeApi(fail_upload="qa-proof.json")

        with self.assertRaisesRegex(publish.PublishError, "upload"):
            publish_plan(api)

        self.assertNotIn("stable.json", api.pages)


    def test_wrong_public_download_hash_leaves_index_unpromoted(self):
        api = FakeApi(corrupt_download="candidate-record.json")

        with self.assertRaisesRegex(publish.PublishError, "SHA256"):
            publish_plan(api)

        self.assertNotIn("stable.json", api.pages)


    def test_older_or_equal_version_is_refused_before_release_creation(self):
        current = plan()
        current_update = json.loads(current.index_bytes)["update"]
        api = FakeApi(index={"schema": 1, "channel": "stable", "platform": "windows-x64",
                             "update": current_update})

        with self.assertRaisesRegex(publish.PublishError, "newer"):
            publish_plan(api)

        self.assertFalse(any(event[0] == "create_release" for event in api.events))

    def test_legacy_current_index_defaults_package_channel_to_index_channel(self):
        api = FakeApi(index={
            "schema": 1, "channel": "stable", "platform": "windows-x64",
            "update": {"version": "30073399661297665", "size": "13",
                       "sha256": hashlib.sha256(b"signed-update").hexdigest(),
                       "url": "https://github.com/lonefisher/fishgram/releases/download/v7.2.9-r1/fishgram-update-win-x64-7002009-r1"},
        })

        with self.assertRaisesRegex(publish.PublishError, "newer"):
            publish_plan(api)

    def test_tampered_index_is_rejected_before_any_api_call(self):
        candidate = plan()
        tampered = publish.PromotionPlan(
            candidate.tag, candidate.target_commit, candidate.index_channel,
            candidate.package_channel, candidate.version, candidate.assets,
            candidate.page_files, candidate.index_path, b"{}", candidate.reuse_release)
        api = FakeApi()

        with self.assertRaises(publish.PublishError):
            publish.promote(tampered, api, enabled=True, context=allowed_context())

        self.assertFalse(api.events)

    def test_stable_index_cannot_be_bound_to_a_beta_signed_package(self):
        candidate = plan()
        beta_name = "fishgram-update-win-x64-7002009-r1-beta"
        beta_asset = publish.Asset(beta_name, b"beta-signed-update")
        assets = (beta_asset,) + candidate.assets[1:]
        index = json.dumps({
            "schema": 1, "channel": "stable", "platform": "windows-x64",
            "update": {"version": candidate.version, "size": str(len(beta_asset.data)),
                       "sha256": beta_asset.sha256,
                       "url": f"https://github.com/lonefisher/fishgram/releases/download/{candidate.tag}/{beta_name}",
                       "channel": "beta"},
        }, sort_keys=True, separators=(",", ":")).encode()
        unsafe = publish.PromotionPlan(
            candidate.tag, candidate.target_commit, "stable", "beta", candidate.version,
            assets, candidate.page_files, "stable.json", index)
        api = FakeApi()

        with self.assertRaises(publish.PublishError):
            publish.promote(unsafe, api, enabled=True, context=allowed_context())

        self.assertFalse(api.events)


    def test_publish_requires_manual_reviewed_main_and_approved_release_environment(self):
        contexts = (
            publish.PublishContext("push", "refs/heads/main", "release", True),
            publish.PublishContext("workflow_dispatch", "refs/heads/feature", "release", True),
            publish.PublishContext("workflow_dispatch", "refs/heads/main", "", True),
            publish.PublishContext("workflow_dispatch", "refs/heads/main", "release", False),
        )
        for context in contexts:
            api = FakeApi()

            with self.assertRaises(publish.PublishError):
                publish.promote(plan(), api, enabled=True, context=context)

            self.assertFalse(api.events)


    def test_publish_requires_explicit_enable_switch(self):
        api = FakeApi()

        with self.assertRaisesRegex(publish.PublishError, "explicit"):
            publish.promote(plan(), api, enabled=False, context=allowed_context())

        self.assertFalse(api.events)


    def test_beta_index_can_publish_a_stable_signed_candidate(self):
        candidate = plan(index_channel="beta", package_channel="stable")
        api = FakeApi()

        publish.promote(candidate, api, enabled=True, context=allowed_context())

        document = json.loads(api.pages["beta.json"])
        self.assertEqual(document["channel"], "beta")
        self.assertEqual(document["update"]["channel"], "stable")
        self.assertNotIn("-beta", document["update"]["url"])
        self.assertTrue(any(event[0] == "upload" for event in api.events))
        self.assertEqual(api.events[-2:], [("page", "beta.json"), ("get_page", "beta.json")])

    def test_stable_index_can_reuse_existing_stable_release_after_public_readback(self):
        candidate = plan(reuse_release=True)
        api = FakeApi(tags={candidate.tag}, releases={candidate.tag})
        api.source_tags.add(candidate.tag)
        api.assets = {asset.name: asset.data for asset in candidate.assets}

        publish.promote(candidate, api, enabled=True, context=allowed_context())

        self.assertFalse(any(event[0] in {"create_release", "upload"} for event in api.events))
        self.assertEqual(api.events[-2:], [("page", "stable.json"), ("get_page", "stable.json")])

    def test_index_builder_separates_index_and_signed_package_channel(self):
        index = publish.build_index(
            "beta", "stable", "30073399661297666", 321, "a" * 64,
            "https://github.com/lonefisher/fishgram/releases/download/v7.2.9-r2/fishgram-update-win-x64-7002009-r2")

        document = json.loads(index)
        self.assertEqual(document["channel"], "beta")
        self.assertEqual(document["update"]["channel"], "stable")

    def test_split_channel_contract_matches_current_production_parsefeed(self):
        root = MODULE.parents[1]

        self.assertTrue(publish.supports_split_channel_feed(root))


    def test_index_rejects_non_parsefeed_urls_and_numeric_versions(self):
        with self.assertRaises(publish.PublishError):
            publish.build_index("stable", "stable", "0123", 5, "a" * 64,
                                "https://example.com/update")

    def test_offline_promotion_contains_fixed_repository_pins_and_exact_hashes(self):
        with tempfile.TemporaryDirectory(dir=MODULE.parent.parent) as temp:
            output = Path(temp) / "promotion"

            publish.write_promotion(plan(), output)

            record = json.loads((output / "promotion.json").read_text(encoding="utf-8"))
            self.assertEqual(record["repositoryInputs"]["lonefisher/fishgram"]["ref"], "refs/heads/main")
            self.assertEqual(record["repositoryInputs"]["lonefisher/tdesktop"]["ref"], "refs/heads/custom/main")
            self.assertEqual(record["repositoryInputs"]["lonefisher/tdesktop"]["pinnedCommit"], "c" * 40)
            self.assertTrue(record["publicationStatus"].startswith("prepared-only"))
            asset = record["releaseAssets"][0]
            data = (output / "release-assets" / asset["name"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), asset["sha256"])

    def test_offline_promotion_refuses_to_overwrite_existing_directory(self):
        with tempfile.TemporaryDirectory(dir=MODULE.parent.parent) as temp:
            output = Path(temp) / "existing"
            output.mkdir()

            with self.assertRaisesRegex(publish.PublishError, "already exists"):
                publish.write_promotion(plan(), output)


if __name__ == "__main__":
    unittest.main()
