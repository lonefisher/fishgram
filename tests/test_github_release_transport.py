from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from urllib.request import Request
from urllib.parse import urlsplit

MODULE = Path(__file__).resolve().parents[1] / "tools" / "github_release_transport.py"
spec = importlib.util.spec_from_file_location("github_release_transport", MODULE)
transport_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = transport_module
spec.loader.exec_module(transport_module)


class Stub:
    """Small API server: asserts actual methods, paths, auth separation and order."""

    def __init__(self, *, pages=True, fail_upload=None, corrupt_asset=None):
        self.calls = []
        self.pages = pages
        self.files = {}
        self.assets = {}
        self.tags = set()
        self.releases = {}
        self.index = None
        self.next_id = 50
        self.corrupt_public = set()
        self.fail_upload = fail_upload
        self.corrupt_asset = corrupt_asset
        self.blobs = {}
        self.pending_tree = None
        self.fail_atomic_ref = False

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        parsed = urlsplit(url)
        path = parsed.path
        if parsed.hostname == "lonefisher.github.io":
            relative = path.removeprefix("/fishgram/")
            if relative in self.corrupt_public:
                return 200, {}, b"wrong-public-content"
            return (200, {}, self.files[relative]) if relative in self.files else (404, {}, b"")
        if parsed.hostname == "github.com" and "/releases/download/" in path:
            name = path.rsplit("/", 1)[-1]
            data = self.assets.get(name)
            if name == self.corrupt_asset and data is not None:
                data = data + b"tampered"
            return (200, {}, data) if data is not None else (404, {}, b"")
        if parsed.hostname == "uploads.github.com" and method == "POST":
            name = parsed.query.removeprefix("name=")
            if name == self.fail_upload:
                return 500, {}, b"private server detail must not escape"
            self.assets[name] = body
            return 201, {}, json.dumps({"name": name, "size": len(body), "state": "uploaded"}).encode()
        if parsed.hostname != "api.github.com":
            return 400, {}, b"unexpected host"
        repo = "lonefisher/tdesktop" if "/repos/lonefisher/tdesktop/" in path else "lonefisher/fishgram"
        if path.endswith("/pages") and method == "GET":
            if not self.pages:
                return 404, {}, b""
            return 200, {}, json.dumps({"public": True, "html_url": "https://lonefisher.github.io/fishgram",
                                        "source": {"branch": "gh-pages", "path": "/"}}).encode()
        if path.endswith('/git/ref/heads/gh-pages') and method == 'GET':
            return 200, {}, json.dumps({'object': {'type': 'commit', 'sha': 'a' * 40}}).encode()
        if path.endswith('/git/ref/heads/custom/main') and method == 'GET':
            return 200, {}, json.dumps({'object': {'type': 'commit', 'sha': 'e' * 40}}).encode()
        if '/compare/' in path and method == 'GET':
            return 200, {}, json.dumps({'status': 'ahead', 'base_commit': {'sha': 'c' * 40},
                                       'merge_base_commit': {'sha': 'c' * 40}}).encode()
        if path.endswith('/git/commits/' + 'a' * 40) and method == 'GET':
            return 200, {}, json.dumps({'tree': {'sha': 'b' * 40}}).encode()
        if path.endswith('/git/blobs') and method == 'POST':
            payload = json.loads(body)
            data = base64.b64decode(payload['content'])
            sha = hashlib.sha1(data).hexdigest()
            self.blobs[sha] = data
            return 201, {}, json.dumps({'sha': sha}).encode()
        if path.endswith('/git/trees') and method == 'POST':
            payload = json.loads(body)
            assert payload['base_tree'] == 'b' * 40
            self.pending_tree = payload['tree']
            return 201, {}, json.dumps({'sha': 'c' * 40}).encode()
        if path.endswith('/git/commits') and method == 'POST':
            payload = json.loads(body)
            assert payload['parents'] == ['a' * 40] and payload['tree'] == 'c' * 40
            return 201, {}, json.dumps({'sha': 'e' * 40}).encode()
        if path.endswith('/git/refs/heads/gh-pages') and method == 'PATCH':
            assert json.loads(body) == {'sha': 'e' * 40, 'force': False}
            if self.fail_atomic_ref:
                return 422, {}, b'branch advanced'
            self.files.update({item['path']: self.blobs[item['sha']] for item in self.pending_tree})
            return 200, {}, json.dumps({'object': {'sha': 'e' * 40}}).encode()
        if path.endswith("/git/ref/tags/v7.2.9-r1") and method == "GET":
            tag_set = self.tags if repo == transport_module.PRODUCT_REPOSITORY else getattr(self, "source_tags", set())
            exists = "v7.2.9-r1" in tag_set
            target = getattr(self, 'created_product_target', None) if repo == transport_module.PRODUCT_REPOSITORY else None
            value = {'object': {'type': 'commit', 'sha': target}} if target else {'ref': 'refs/tags/v7.2.9-r1'}
            return (200, {}, json.dumps(value).encode()) if exists else (404, {}, b"")
        if path.endswith("/releases/tags/v7.2.9-r1") and method == "GET":
            if "v7.2.9-r1" not in self.releases:
                return 404, {}, b""
            return 200, {}, json.dumps(self.releases["v7.2.9-r1"]).encode()
        if path.endswith("/releases") and method == "POST":
            payload = json.loads(body)
            self.created_product_target = payload['target_commitish']
            self.tags.add(payload["tag_name"])
            rid = self.next_id
            self.next_id += 1
            release = {"id": rid, "tag_name": payload["tag_name"], "draft": True,
                       "prerelease": payload["prerelease"],
                       "upload_url": f"https://uploads.github.com/repos/lonefisher/fishgram/releases/{rid}/assets{{?name,label}}"}
            self.releases[payload["tag_name"]] = release
            return 201, {}, json.dumps(release).encode()
        if "/git/tags" in path and method == "POST":
            return 201, {}, json.dumps({"sha": "d" * 40}).encode()
        if path.endswith("/git/refs") and method == "POST":
            tag_set = self.tags if repo == transport_module.PRODUCT_REPOSITORY else self.source_tags
            tag_set.add("v7.2.9-r1")
            return 201, {}, b'{"ref":"refs/tags/v7.2.9-r1"}'
        if "/releases/50/assets" in path and method == "POST":
            name = parsed.query.removeprefix("name=")
            self.assets[name] = body
            self.releases["v7.2.9-r1"]["assets"] = [
                {"name": name, "size": len(body), "state": "uploaded"}]
            return 201, {}, json.dumps({"name": name, "size": len(body), "state": "uploaded"}).encode()
        if path.endswith("/releases/50") and method == "PATCH":
            self.releases["v7.2.9-r1"]["draft"] = False
            return 200, {}, b'{"draft":false}'
        if "/contents/" in path:
            file_path = path.split("/contents/", 1)[1]
            if method == "GET":
                value = self.files.get(file_path)
                if value is None:
                    return 404, {}, b""
                return 200, {}, json.dumps({"sha": "blob-sha", "type": "file",
                    "encoding": "base64", "content": base64.b64encode(value).decode()}).encode()
            if method == "PUT":
                obj = json.loads(body)
                self.files[file_path] = base64.b64decode(obj["content"])
                return 200, {}, b'{"commit":{"sha":"commit"}}'
        return 404, {}, b""


class GithubReleaseTransportTests(unittest.TestCase):
    def test_release_prerelease_reads_and_validates_github_metadata(self):
        for prerelease in (False, True, None, 'false', 0):
            with self.subTest(prerelease=prerelease):
                stub = Stub()
                stub.releases['v7.2.9-r1'] = {'tag_name': 'v7.2.9-r1', 'draft': False}
                if prerelease is not None:
                    stub.releases['v7.2.9-r1']['prerelease'] = prerelease
                api = self.transport(stub)
                if type(prerelease) is bool:
                    self.assertIs(api.release_prerelease('v7.2.9-r1'), prerelease)
                else:
                    with self.assertRaises(transport_module.TransportError):
                        api.release_prerelease('v7.2.9-r1')
                self.assertTrue(all(call[0] == 'GET' for call in stub.calls))
                self.assertTrue(stub.calls[-1][1].endswith('/releases/tags/v7.2.9-r1'))

    def test_release_prerelease_requires_an_existing_release(self):
        stub = Stub()
        with self.assertRaises(transport_module.TransportError):
            self.transport(stub).release_prerelease('v7.2.9-r1')

    def test_rest_reuse_checks_release_channel_before_writing_pages(self):
        from test_publish_release import allowed_context, plan, publish
        for index_channel, prerelease in (('stable', True), ('stable', False), ('beta', True)):
            with self.subTest(index_channel=index_channel, prerelease=prerelease):
                candidate = plan(index_channel=index_channel, reuse_release=True)
                stub = Stub()
                stub.tags.add(candidate.tag)
                stub.source_tags = {candidate.tag}
                stub.assets = {a.name: a.data for a in candidate.assets}
                stub.releases[candidate.tag] = {'tag_name': candidate.tag, 'draft': False,
                    'prerelease': prerelease, 'assets': [{'name': a.name} for a in candidate.assets]}
                def request(method, url, headers, body, timeout):
                    response = stub(method, url, headers, body, timeout)
                    if method == 'GET' and '/git/ref/tags/' in url:
                        commit = 'c' if '/repos/lonefisher/tdesktop/' in url else 'b'
                        return 200, {}, json.dumps({'object': {'type': 'commit', 'sha': commit * 40}}).encode()
                    return response
                api = transport_module.GitHubReleaseTransport(product_token='fixture',
                    source_token='source-fixture', request=request)
                if index_channel == 'stable' and prerelease:
                    with self.assertRaisesRegex(publish.PublishError, 'prerelease'):
                        publish.promote(candidate, api, enabled=True, context=allowed_context())
                    self.assertFalse(any(c[0] in {'POST', 'PATCH', 'PUT'} for c in stub.calls))
                else:
                    publish.promote(candidate, api, enabled=True, context=allowed_context())
                    self.assertEqual(stub.files[candidate.index_path], candidate.index_bytes)
                    self.assertFalse(any(c[0] != 'GET' and '/releases' in c[1] for c in stub.calls))

    def test_source_main_ancestry_rejects_unmerged_or_wrong_merge_base(self):
        for status, merge_base in (('diverged', 'd' * 40), ('behind', 'c' * 40),
                                    ('ahead', 'd' * 40)):
            stub = Stub()
            original = stub.__call__
            def request(method, url, headers, body, timeout):
                if '/compare/' in url:
                    return 200, {}, json.dumps({'status': status, 'base_commit': {'sha': 'c' * 40},
                        'merge_base_commit': {'sha': merge_base}}).encode()
                return original(method, url, headers, body, timeout)
            api = transport_module.GitHubReleaseTransport(product_token='fixture',
                source_token='source-fixture', request=request)
            with self.subTest(status=status, merge_base=merge_base):
                with self.assertRaises(transport_module.TransportError):
                    api.verify_source_merged('c' * 40)
                self.assertTrue(all(call[0] == 'GET' for call in stub.calls))

    def trust_bundle(self, version=2):
        return {"keys/root-public.pem": b"same-root", "keys/issuer-public.pem": b"issuer",
            "keys/manifest.min.json": json.dumps({"format": 1, "manifest_version": version}).encode(),
            "keys/manifest.sig": b"signature-" + str(version).encode()}

    def test_existing_trust_rejects_root_change_or_nonincreasing_manifest_before_writes(self):
        for root_change, version in ((True, 3), (False, 1), (False, 2)):
            stub = Stub()
            stub.files.update(self.trust_bundle())
            target = self.trust_bundle(version)
            if root_change:
                target["keys/root-public.pem"] = b"different-root"
            else:
                target["keys/issuer-public.pem"] = b"changed-issuer"
            original = dict(stub.files)
            with self.subTest(root_change=root_change, version=version):
                with self.assertRaises(transport_module.TransportError):
                    self.transport(stub).put_pages_atomic(target)
                self.assertEqual(stub.files, original)
                self.assertFalse(any(call[0] in ("POST", "PATCH", "PUT") for call in stub.calls))

    def test_promotion_rejects_authoritative_trust_conflict_before_any_release_write(self):
        from dataclasses import replace
        from test_publish_release import allowed_context, plan, publish
        for root_change, version in ((True, 3), (False, 1), (False, 2)):
            with self.subTest(root_change=root_change, version=version):
                stub = Stub()
                stub.files.update(self.trust_bundle())
                target = self.trust_bundle(version)
                target['keys/issuer-public.pem'] = b'new-issuer'
                if root_change:
                    target['keys/root-public.pem'] = b'new-root'
                candidate = replace(plan(), page_files=tuple(
                    publish.PageFile(path, data) for path, data in target.items()))
                with self.assertRaises(publish.PublishError):
                    publish.promote(candidate, self.transport(stub), enabled=True, context=allowed_context())
                self.assertFalse(any(call[0] in ('POST', 'PATCH', 'PUT') for call in stub.calls))

    def test_trust_preflight_is_read_only_for_newer_same_root_bundle(self):
        stub = Stub()
        stub.files.update(self.trust_bundle())
        self.transport(stub).preflight_pages(self.trust_bundle(3))
        self.assertFalse(any(call[0] in ('POST', 'PATCH', 'PUT') for call in stub.calls))

    def test_existing_identical_trust_is_idempotent_and_partial_trust_is_refused(self):
        for partial in (False, True):
            stub = Stub()
            target = self.trust_bundle()
            stub.files.update(target)
            if partial:
                del stub.files["keys/manifest.sig"]
                with self.assertRaises(transport_module.TransportError):
                    self.transport(stub).put_pages_atomic(target)
            else:
                self.transport(stub).put_pages_atomic(target)
            self.assertFalse(any(call[0] in ("POST", "PATCH", "PUT") for call in stub.calls))

    def test_newer_same_root_trust_reads_fixed_parent_before_atomic_update(self):
        stub = Stub()
        stub.files.update(self.trust_bundle())
        target = self.trust_bundle(3)
        self.transport(stub).put_pages_atomic(target)
        self.assertEqual(stub.files, target)
        reads = [call for call in stub.calls if call[0] == "GET" and "/contents/keys/" in call[1]]
        self.assertEqual(len(reads), 4)
        self.assertTrue(all(urlsplit(call[1]).query == "ref=" + "a" * 40 for call in reads))

    def test_index_preflight_reads_authoritative_branch_when_public_page_is_missing(self):
        stub = Stub()
        document = {"schema": 1, "channel": "stable", "platform": "windows-x64",
                    "update": {"version": "30073399661297666"}}
        stub.files["stable.json"] = json.dumps(document).encode()
        original = stub.__call__
        def request(method, url, headers, body, timeout):
            if url.startswith(transport_module.PAGES_ROOT):
                return 404, {}, b""
            return original(method, url, headers, body, timeout)
        api = transport_module.GitHubReleaseTransport(product_token="fixture",
            source_token="source-fixture", request=request)
        self.assertEqual(api.get_index("stable.json"), document)

    def test_index_write_rechecks_branch_version_before_put(self):
        stub = Stub()
        current = {"schema": 1, "channel": "stable", "platform": "windows-x64",
                   "update": {"version": "30073399661297666"}}
        stub.files["stable.json"] = json.dumps(current).encode()
        candidate = {**current, "update": {"version": "30073399661297665"}}
        with self.assertRaisesRegex(transport_module.TransportError, "newer"):
            self.transport(stub).put_page("stable.json", json.dumps(candidate).encode())
        self.assertEqual(json.loads(stub.files["stable.json"]), current)

    def test_tag_commit_resolves_lightweight_and_annotated_tags(self):
        for annotated in (False, True):
            stub = Stub()
            original = stub.__call__
            def request(method, url, headers, body, timeout):
                if '/git/ref/tags/' in url:
                    obj = {"type": "tag" if annotated else "commit", "sha": "a" * 40}
                    return 200, {}, json.dumps({"object": obj}).encode()
                if '/git/tags/' in url:
                    return 200, {}, json.dumps({"object": {"type": "commit", "sha": "b" * 40}}).encode()
                return original(method, url, headers, body, timeout)
            api = transport_module.GitHubReleaseTransport(product_token='fixture',
                source_token='source-fixture', request=request)
            with self.subTest(annotated=annotated):
                self.assertEqual(api.tag_commit(transport_module.PRODUCT_REPOSITORY, 'v1'),
                                 ("b" if annotated else "a") * 40)

    def test_tag_commit_rejects_cycles_noncommits_and_malformed_hashes(self):
        for obj in ({"type": "tag", "sha": "a" * 40},
                    {"type": "blob", "sha": "a" * 40},
                    {"type": "commit", "sha": "invalid"}):
            api = transport_module.GitHubReleaseTransport(product_token='fixture', source_token='source-fixture',
                request=lambda *args: (200, {}, json.dumps({"object": obj}).encode()))
            with self.subTest(obj=obj), self.assertRaises(transport_module.TransportError):
                api.tag_commit(transport_module.SOURCE_REPOSITORY, 'v1')

    def transport(self, stub, **kwargs):
        stub.source_tags = set()
        return transport_module.GitHubReleaseTransport(
            product_token="parent-secret-token", source_token="source-app-token",
            request=stub, **kwargs)

    def test_api_uses_exact_repository_allowlist_and_separate_source_app_token(self):
        stub = Stub()
        api = self.transport(stub)
        self.assertFalse(api.tag_exists(transport_module.PRODUCT_REPOSITORY, "v7.2.9-r1"))
        self.assertFalse(api.source_tag_exists("v7.2.9-r1"))
        self.assertIn("Bearer parent-secret-token", stub.calls[0][2]["Authorization"])
        self.assertIn("Bearer source-app-token", stub.calls[1][2]["Authorization"])
        with self.assertRaisesRegex(transport_module.TransportError, "allowlisted"):
            api.tag_exists("attacker/repo", "v7.2.9-r1")

    def test_pages_404_is_a_preflight_stop_and_does_not_create_tag_or_release(self):
        stub = Stub(pages=False)
        api = self.transport(stub)
        with self.assertRaisesRegex(transport_module.TransportError, "Pages is not configured"):
            api.pages_config()
        self.assertFalse(any(call[0] in {"POST", "PUT", "PATCH"} for call in stub.calls))

    def test_release_is_created_as_draft_upload_uses_returned_url_then_published(self):
        stub = Stub()
        api = self.transport(stub)
        api.create_release("v7.2.9-r1", "a" * 40, "title", "body", prerelease=False)
        api.upload_asset("v7.2.9-r1", "safe.zip", b"asset-data")
        api.publish_release("v7.2.9-r1")
        create = next(call for call in stub.calls if call[0] == "POST" and call[1].endswith("/releases"))
        self.assertTrue(json.loads(create[3])["draft"])
        upload = next(call for call in stub.calls if call[0] == "POST" and "uploads.github.com" in call[1])
        self.assertEqual(urlsplit(upload[1]).hostname, "uploads.github.com")
        self.assertEqual(upload[3], b"asset-data")
        self.assertTrue(stub.releases["v7.2.9-r1"]["draft"] is False)

    def test_upload_url_from_api_cannot_escape_github_allowlist(self):
        class Hostile(Stub):
            def __call__(self, method, url, headers, body, timeout):
                status, response_headers, payload = super().__call__(method, url, headers, body, timeout)
                if method == "POST" and url.endswith("/releases"):
                    value = json.loads(payload)
                    value["upload_url"] = "https://attacker.example/upload{?name}"
                    payload = json.dumps(value).encode()
                return status, response_headers, payload
        api = self.transport(Hostile())
        with self.assertRaisesRegex(transport_module.TransportError, "upload URL"):
            api.create_release("v7.2.9-r1", "a" * 40, "title", "body", prerelease=False)

    def test_public_pages_download_never_sends_api_token(self):
        stub = Stub()
        stub.files["keys/root-public.pem"] = b"public-key"
        api = self.transport(stub)
        self.assertEqual(api.get_page("keys/root-public.pem"), b"public-key")
        call = stub.calls[-1]
        self.assertEqual(urlsplit(call[1]).hostname, "lonefisher.github.io")
        self.assertNotIn("Authorization", call[2])

    def test_full_promotion_uses_rest_requests_and_reads_public_index_only_after_keys(self):
        publisher_file = MODULE.with_name("publish_release.py")
        publisher_spec = importlib.util.spec_from_file_location("publish_release_transport_integration", publisher_file)
        publisher = importlib.util.module_from_spec(publisher_spec)
        sys.modules[publisher_spec.name] = publisher
        publisher_spec.loader.exec_module(publisher)
        from test_publish_release import allowed_context, plan

        stub = Stub()
        api = self.transport(stub)
        candidate = plan()
        publisher.promote(candidate, api, enabled=True, context=allowed_context())

        methods = [(call[0], urlsplit(call[1]).hostname, urlsplit(call[1]).path) for call in stub.calls]
        upload_positions = [i for i, item in enumerate(methods)
                            if item[0] == "POST" and item[1] == "uploads.github.com"]
        publish_pos = next(i for i, item in enumerate(methods)
                           if item[0] == "PATCH" and "/releases/50" in item[2])
        public_downloads = [i for i, item in enumerate(methods)
                            if item[0] == "GET" and item[1] == "github.com" and "/releases/download/" in item[2]]
        key_puts = [i for i, item in enumerate(methods)
                    if item[0] == "PATCH" and item[2].endswith('/git/refs/heads/gh-pages')]
        index_put = next(i for i, item in enumerate(methods)
                         if item[0] == "PUT" and "/contents/stable.json" in item[2])
        index_public_get = max(i for i, item in enumerate(methods)
                               if item[0] == "GET" and item[1] == "lonefisher.github.io"
                               and item[2].endswith("/stable.json"))
        self.assertEqual(len(upload_positions), len(candidate.assets))
        self.assertTrue(max(upload_positions) < publish_pos < min(public_downloads))
        self.assertTrue(max(public_downloads) < min(key_puts) < index_put < index_public_get)
        self.assertTrue(all(stub.calls[i][2].get("Authorization") == "Bearer source-app-token"
                            for i, call in enumerate(stub.calls)
                            if call[1].startswith("https://api.github.com/repos/lonefisher/tdesktop/")))
        source_tag_call = next(call for call in stub.calls
                               if call[0] == "POST" and call[1].endswith("/repos/lonefisher/tdesktop/git/tags"))
        source_tag_body = json.loads(source_tag_call[3])
        self.assertEqual(source_tag_body["object"], "c" * 40)
        self.assertEqual(source_tag_body["type"], "commit")
        self.assertEqual(source_tag_body["tag"], "v7.2.9-r1")

    def test_atomic_trust_bundle_conflict_preserves_old_files_and_stops_index(self):
        from dataclasses import replace
        from test_publish_release import allowed_context, plan, publish
        stub = Stub()
        stub.source_tags = set()
        stub.files = self.trust_bundle(2)
        candidate = replace(plan(), page_files=tuple(
            publish.PageFile(path, data) for path, data in self.trust_bundle(3).items()))
        old = dict(stub.files)
        stub.fail_atomic_ref = True
        with self.assertRaisesRegex(publish.PublishError, 'atomic promotion'):
            publish.promote(candidate, self.transport(stub), enabled=True, context=allowed_context())
        self.assertEqual(stub.files, old)
        self.assertTrue(any(call[0] == 'PATCH' for call in stub.calls))
        self.assertFalse(any(call[0] == 'PUT' for call in stub.calls))

    def test_atomic_trust_bundle_rejects_partial_or_arbitrary_paths_before_requests(self):
        for files in ({'keys/manifest.min.json': b'new'},
                      {'../stable.json': b'new'}, {'stable.json': b'new'}):
            stub = Stub()
            with self.assertRaises(transport_module.TransportError):
                self.transport(stub).put_pages_atomic(files)
            self.assertEqual(stub.calls, [])

    def test_atomic_trust_blob_failure_never_advances_pages_ref(self):
        from test_publish_release import plan
        stub = Stub()
        def request(method, url, headers, body, timeout):
            if method == 'POST' and url.endswith('/git/blobs'):
                return 500, {}, b'private API details'
            return stub(method, url, headers, body, timeout)
        api = transport_module.GitHubReleaseTransport(product_token='product-app-token',
            source_token='source-app-token', request=request)
        with self.assertRaisesRegex(transport_module.TransportError, 'HTTP 500'):
            api.put_pages_atomic({item.path: item.data for item in plan().page_files})
        self.assertEqual(stub.files, {})
        self.assertFalse(any(call[0] == 'PATCH' for call in stub.calls))

    def test_atomic_trust_preserves_pages_docs_prefix_and_existing_tree(self):
        from test_publish_release import plan
        stub = Stub()
        api = self.transport(stub)
        api._pages = {'source': {'branch': 'gh-pages', 'path': '/docs'}}
        files = {item.path: item.data for item in plan().page_files}
        api.put_pages_atomic(files)
        self.assertEqual(stub.files, {'docs/' + path: data for path, data in files.items()})
        mutations = [call for call in stub.calls if call[0] in ('POST', 'PATCH')]
        self.assertEqual(len([call for call in mutations if call[0] == 'PATCH']), 1)
        self.assertTrue(all(call[2]['Authorization'] == 'Bearer parent-secret-token' for call in mutations))

    def test_full_transport_refuses_existing_tag_before_any_write(self):
        from test_publish_release import allowed_context, plan
        stub = Stub()
        stub.tags.add("v7.2.9-r1")
        with self.assertRaisesRegex(Exception, "already exists"):
            p_spec = importlib.util.spec_from_file_location("publish_release_duplicate", MODULE.with_name("publish_release.py"))
            publisher = importlib.util.module_from_spec(p_spec)
            sys.modules[p_spec.name] = publisher
            p_spec.loader.exec_module(publisher)
            publisher.promote(plan(), self.transport(stub), enabled=True, context=allowed_context())
        self.assertFalse(any(call[0] in {"POST", "PUT", "PATCH"} for call in stub.calls))

    def test_transport_first_public_trust_503_recovers_and_index_remains_last(self):
        from test_publish_release import allowed_context, plan
        p_spec = importlib.util.spec_from_file_location("publish_release_transient", MODULE.with_name("publish_release.py"))
        publisher = importlib.util.module_from_spec(p_spec)
        sys.modules[p_spec.name] = publisher
        p_spec.loader.exec_module(publisher)
        stub = Stub()
        failures = [0]
        stub.source_tags = set()
        def request(method, url, headers, body, timeout):
            if url == "https://lonefisher.github.io/fishgram/keys/root-public.pem" and not failures[0]:
                failures[0] += 1
                return 503, {}, b"temporary deployment failure"
            return stub(method, url, headers, body, timeout)
        api = transport_module.GitHubReleaseTransport(product_token="product-app-token",
            source_token="source-app-token", request=request)
        original_read = publisher.read_public_page
        with mock.patch.object(publisher, "read_public_page", side_effect=lambda api, path:
                original_read(api, path, sleep=lambda value: None)):
            publisher.promote(plan(), api, enabled=True, context=allowed_context())
        self.assertEqual(failures[0], 1)
        self.assertEqual(stub.files["stable.json"], plan().index_bytes)
        self.assertTrue(stub.calls[-1][1].endswith("/stable.json"))

    def test_full_transport_upload_failure_does_not_publish_release_or_touch_pages(self):
        from test_publish_release import allowed_context, plan
        p_spec = importlib.util.spec_from_file_location("publish_release_upload_failure", MODULE.with_name("publish_release.py"))
        publisher = importlib.util.module_from_spec(p_spec)
        sys.modules[p_spec.name] = publisher
        p_spec.loader.exec_module(publisher)
        stub = Stub(fail_upload="qa-proof.json")
        with self.assertRaises(publisher.PublishError):
            publisher.promote(plan(), self.transport(stub), enabled=True, context=allowed_context())
        self.assertFalse(any(call[0] == "PATCH" or urlsplit(call[1]).hostname == "lonefisher.github.io"
                             and call[0] == "PUT" for call in stub.calls))

    def test_full_transport_wrong_public_hash_stops_before_pages_mutation(self):
        from test_publish_release import allowed_context, plan
        p_spec = importlib.util.spec_from_file_location("publish_release_bad_download", MODULE.with_name("publish_release.py"))
        publisher = importlib.util.module_from_spec(p_spec)
        sys.modules[p_spec.name] = publisher
        p_spec.loader.exec_module(publisher)
        candidate = plan()
        stub = Stub(corrupt_asset=candidate.assets[0].name)
        with self.assertRaisesRegex(publisher.PublishError, "SHA256"):
            publisher.promote(candidate, self.transport(stub), enabled=True, context=allowed_context())
        self.assertFalse(any(call[0] == "PUT" for call in stub.calls))

    def test_full_transport_old_public_index_stops_before_any_write(self):
        from test_publish_release import allowed_context, plan
        p_spec = importlib.util.spec_from_file_location("publish_release_old_index", MODULE.with_name("publish_release.py"))
        publisher = importlib.util.module_from_spec(p_spec)
        sys.modules[p_spec.name] = publisher
        p_spec.loader.exec_module(publisher)
        candidate = plan()
        stub = Stub()
        old = json.loads(candidate.index_bytes)
        old["update"]["version"] = "30073399661297665"
        stub.files["stable.json"] = json.dumps(old).encode()
        with self.assertRaisesRegex(publisher.PublishError, "newer"):
            publisher.promote(candidate, self.transport(stub), enabled=True, context=allowed_context())
        self.assertFalse(any(call[0] in {"POST", "PUT", "PATCH"} for call in stub.calls))

    def test_publish_manifest_rejects_source_pin_mismatch_before_transport(self):
        p_spec = importlib.util.spec_from_file_location("publish_release_manifest_pin", MODULE.with_name("publish_release.py"))
        publisher = importlib.util.module_from_spec(p_spec)
        sys.modules[p_spec.name] = publisher
        p_spec.loader.exec_module(publisher)
        from test_publish_release import plan

        with tempfile.TemporaryDirectory(dir=MODULE.parent.parent) as directory:
            root = Path(directory)
            candidate = plan()
            (root / "release-assets").mkdir()
            (root / "pages/keys").mkdir(parents=True)
            assets = []
            for item in candidate.assets:
                (root / "release-assets" / item.name).write_bytes(item.data)
                assets.append({"name": item.name, "size": len(item.data), "sha256": item.sha256})
            page_files = []
            for item in candidate.page_files:
                (root / "pages" / item.path).write_bytes(item.data)
                page_files.append({"path": item.path, "size": len(item.data), "sha256": item.sha256})
            (root / "pages" / candidate.index_path).write_bytes(candidate.index_bytes)
            (root / "promotion.json").write_text(json.dumps({
                "schema": 1, "kind": "fishgram-offline-promotion",
                "repository": "lonefisher/fishgram", "sourceRepository": "lonefisher/tdesktop",
                "repositoryInputs": {
                    "lonefisher/fishgram": {"ref": "refs/heads/main", "reviewedCommit": "a" * 40,
                                             "candidateCommit": "b" * 40},
                    "lonefisher/tdesktop": {"ref": "refs/heads/custom/main", "pinnedCommit": "d" * 40},
                },
                "tag": candidate.tag, "targetCommit": candidate.target_commit,
                "indexChannel": candidate.index_channel, "packageChannel": candidate.package_channel,
                "updateVersion": candidate.version,
                "reuseRelease": False,
                "releaseAssets": assets, "pagesFiles": page_files,
                "index": {"path": candidate.index_path, "size": len(candidate.index_bytes),
                          "sha256": hashlib.sha256(candidate.index_bytes).hexdigest()},
            }), encoding="utf-8")
            result = publisher.main(["publish", "--promotion", str(root), "--enable-publication",
                                     "--event", "workflow_dispatch", "--ref", "refs/heads/main",
                                     "--environment", "release", "--approval-marker", "1"])
            self.assertEqual(result, 1)


class RedirectBoundaryTests(unittest.TestCase):
    def test_authenticated_redirect_is_rejected_before_forwarding(self):
        handler = transport_module.PublicAssetRedirects()
        request = Request('https://api.github.com/repos/lonefisher/fishgram/releases',
                          headers={'Authorization': 'Bearer disposable-fixture'})
        with self.assertRaises(transport_module.TransportError):
            handler.redirect_request(request, None, 302, 'Found', {},
                                     'https://unapproved.invalid/collect')

    def test_only_public_asset_redirects_are_allowed(self):
        handler = transport_module.PublicAssetRedirects()
        request = Request('https://github.com/lonefisher/fishgram/releases/download/v7.2.9-r1/file.zip')
        redirected = handler.redirect_request(request, None, 302, 'Found', {},
                                              'https://release-assets.githubusercontent.com/file.zip')
        self.assertIsNotNone(redirected)
        self.assertFalse(redirected.has_header('Authorization'))
        for url in ('http://release-assets.githubusercontent.com/file.zip',
                    'https://unapproved.invalid/file.zip',
                    'https://release-assets.githubusercontent.com:8443/file.zip'):
            with self.subTest(url=url), self.assertRaises(transport_module.TransportError):
                handler.redirect_request(request, None, 302, 'Found', {}, url)

    def test_non_object_public_index_does_not_become_empty_history(self):
        transport = transport_module.GitHubReleaseTransport(
            product_token='product-fixture', source_token='source-fixture',
            request=Stub())
        transport.get_branch_page = lambda path: b'[]'
        with self.assertRaises(transport_module.TransportError):
            transport.get_index('stable.json')


if __name__ == "__main__":
    unittest.main()
