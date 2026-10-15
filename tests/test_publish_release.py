import hashlib
import importlib.util
import json
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

    def tag_exists(self, repository, tag):
        self.events.append(("tag_exists", repository, tag))
        return tag in self.tags

    def tag_commit(self, repository, tag):
        return "b" * 40 if repository == publish.PRODUCT_REPOSITORY else "c" * 40

    def source_tag_exists(self, tag):
        self.events.append(("source_tag_exists", tag))
        return tag in self.source_tags

    def release_exists(self, tag):
        self.events.append(("release_exists", tag))
        return tag in self.releases

    def get_index(self, path):
        self.events.append(("get_index", path))
        return self.index

    def create_release(self, tag, target, title, body, *, prerelease=False):
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

    def get_page(self, path):
        self.events.append(("get_page", path))
        return self.pages.get(path)


def plan(*, index_channel="stable", package_channel="stable", reuse_release=False):
    update_name = "fishgram-update-win-x64-7002009-r1" + ("-beta" if package_channel == "beta" else "")
    assets = (
        publish.Asset(update_name, b"signed-update"),
        publish.Asset("FishGram-7.2.9-r1-windows-x64-candidate.zip", b"candidate-zip"),
        publish.Asset("FishGram-corresponding-source.zip", b"source-zip"),
        publish.Asset("candidate-record.json", json.dumps({
            "parentCommit": "b" * 40, "sourceCommit": "c" * 40}).encode()),
        publish.Asset("qa.json", b"approved-qa"),
        publish.Asset("signing-record.json", b"signing-record"),
    )
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
    )


def allowed_context():
    return publish.PublishContext(event="workflow_dispatch", ref="refs/heads/main",
                                  environment="release", approval_gate_passed=True)


def publish_plan(api, candidate=None):
    return publish.promote(plan() if candidate is None else candidate, api,
                           enabled=True, context=allowed_context())


class PublishReleaseTests(unittest.TestCase):
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
                    del api.assets["qa.json"]
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
        candidate = replace(plan(), reviewed_commit="c" * 40)
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
        candidate = replace(plan(), reviewed_commit="e" * 40)
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
        candidate = replace(plan(), reviewed_commit="e" * 40)
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
        api = FakeApi(fail_upload="qa.json")

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
