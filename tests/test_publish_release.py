import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

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

    def source_tag_exists(self, tag):
        self.events.append(("source_tag_exists", tag))
        return tag in self.source_tags

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
        tag="v7.2.9-r1", target_commit="a" * 40, index_channel=index_channel,
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


    def test_existing_tag_or_release_is_never_overwritten(self):
        for api in (FakeApi(tags={"v7.2.9-r1"}), FakeApi(releases={"v7.2.9-r1"})):
            with self.assertRaisesRegex(publish.PublishError, "already exists"):
                publish_plan(api)
            self.assertFalse(any(event[0] in {"create_release", "upload", "page"}
                                 for event in api.events))


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
