"""Disposable-key tests for the fail-closed FishGram signing entry point."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sign_candidate", ROOT / "tools/sign_candidate.py")
sign = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sign)
gate = sign.gate
key_spec = importlib.util.spec_from_file_location("key_management", ROOT / "tools/key_management.py")
keys = importlib.util.module_from_spec(key_spec)
key_spec.loader.exec_module(keys)

_original_private_mode = keys._private_mode


def _test_private_mode(path):
    if os.name == "nt":
        # The sandbox token cannot change ACLs; CI production still runs the real
        # implementation. Keep generated fixture keys private at the OS temp ACL.
        return None
    return _original_private_mode(path)


keys._private_mode = _test_private_mode


@unittest.skipUnless(os.environ.get("FISHGRAM_OPENSSL") or shutil.which("openssl"), "OpenSSL is required")
class SigningEntryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".private").mkdir(exist_ok=True)
        tempfile.tempdir = str(ROOT / ".private")
        self.temp = tempfile.TemporaryDirectory(prefix="fishgram-sign-candidate-test-")
        self.root = Path(self.temp.name)
        self.password = "disposable-fixture-passphrase"
        self.root_private = self.root / "root-private.pem"
        self.root_public = self.root / "root-public.pem"
        self.issuer_private = self.root / "issuer-private.pem"
        self.issuer_public = self.root / "issuer-public.pem"
        keys.generate_root(self.root_private, self.root_public, self.password)
        keys.generate_issuer(self.issuer_private, self.issuer_public, self.password)
        self.now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        self.pub = keys._public_der(self.issuer_private, self.password)[-32:]
        self.manifest = {
            "format": 1, "manifest_version": 1, "issued": self.now - 10,
            "expires": self.now + 86400,
            "keys": [{"id": "issuer-a", "alg": "Ed25519", "x": keys._b64url(self.pub),
                      "expires": self.now + 86400}],
            "channels": {"stable": [["issuer-a"]], "beta": [["issuer-a"], ["issuer-b"]]},
            "revoked": [],
        }
        self.trust_json, self.root_signature = keys.init_manifest(
            self.root_private, self.root_public, self.issuer_public, "issuer-a", self.password,
            now=self.now - 10)
        self.trust_manifest = keys.verify_manifest(self.trust_json, self.root_signature, self.root_public)

    def tearDown(self):
        self.temp.cleanup()

    def test_public_trust_rejects_appended_pem_blocks_and_extra_content(self):
        trust = self.root / 'config/update-trust'
        trust.mkdir(parents=True)
        (trust / 'manifest.min.json').write_bytes(self.trust_json)
        (trust / 'manifest.sig').write_bytes(self.root_signature)
        originals = {'root-public.pem': self.root_public.read_bytes(),
                     'issuer-public.pem': self.issuer_public.read_bytes()}
        for name, original in originals.items():
            (trust / name).write_bytes(original)
        sign._verify_trust(self.root, trust)
        for name in originals:
            for suffix in (self.issuer_private.read_bytes(), self.issuer_public.read_bytes(), b'extra\n'):
                with self.subTest(name=name, suffix=suffix[:20]):
                    (trust / name).write_bytes(originals[name] + suffix)
                    with self.assertRaises(sign.SigningError):
                        sign._verify_trust(self.root, trust)
                    (trust / name).write_bytes(originals[name])

    def test_signing_root_matches_immutable_candidate_commit_after_qa_changes(self):
        for args in (('init', '-q'), ('config', 'user.name', 'Fixture'),
                     ('config', 'user.email', 'fixture@example.invalid')):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)
        trust = self.root / 'config/update-trust'
        trust.mkdir(parents=True)
        root_path = trust / 'root-public.pem'
        root_path.write_bytes(self.root_public.read_bytes())
        subprocess.run(['git', '-C', str(self.root), 'add', 'config'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'candidate'], check=True)
        candidate = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD']).decode().strip()
        sign._check_candidate_root(self.root, {'parentCommit': candidate}, self.root_public.read_bytes())
        root_path.write_bytes(self.issuer_public.read_bytes())
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qam', 'later QA and root change'], check=True)
        with self.assertRaises(sign.SigningError):
            sign._check_candidate_root(self.root, {'parentCommit': candidate}, root_path.read_bytes())

    def test_candidate_workflow_rejects_inactive_or_unauthorized_trust_before_api_secrets(self):
        workflow = (ROOT / '.github/workflows/product-candidate.yml').read_text()
        lines = [line.strip() for line in workflow.splitlines()
                 if line.strip().startswith('python tools/') and
                 ('key_management.py check ' in line or 'sign_candidate.py verify-trust' in line)]
        self.assertEqual(len(lines), 1)
        command = lines[0]
        self.assertLess(workflow.index(command), workflow.index('secrets.FISHGRAM_API_ID'))
        trust = self.root / 'config/update-trust'
        trust.mkdir(parents=True)
        (trust / 'root-public.pem').write_bytes(self.root_public.read_bytes())
        (trust / 'issuer-public.pem').write_bytes(self.issuer_public.read_bytes())
        # Run the workflow's actual public-trust command in the temporary
        # candidate root, using the real production CLI and real signatures.
        for script in ('key_management.py', 'sign_candidate.py'):
            command = command.replace('python tools/' + script,
                                      'python "' + str(ROOT / 'tools' / script) + '"')
        harness = self.root / 'preflight.ps1'
        harness.write_text(command + '\nexit $LASTEXITCODE\n', encoding='utf-8')
        cases = [('stable', 'valid', True), ('beta', 'valid', True),
                 ('stable', 'expired_manifest', False), ('beta', 'expired_manifest', False),
                 ('stable', 'future_manifest', False), ('stable', 'expired_issuer', False),
                 ('stable', 'revoked', False), ('stable', 'other_public_key', False),
                 ('stable', 'ambiguous_issuer', False), ('stable', 'bad_signature', False),
                 ('beta', 'unauthorized_beta', False), ('stable', 'unauthorized_beta', True),
                 ('stable', 'implicit_issuer_expiry', True), ('other', 'valid', False)]
        for channel, change, accepted in cases:
            with self.subTest(channel=channel, change=change):
                manifest = json.loads(self.trust_json)
                if change == 'expired_manifest': manifest['expires'] = self.now - 1
                if change == 'future_manifest': manifest['issued'] = self.now + 3600
                if change == 'expired_issuer': manifest['keys'][0]['expires'] = self.now - 1
                if change == 'revoked': manifest['revoked'] = ['issuer-a']
                if change == 'other_public_key':
                    manifest['keys'][0]['x'] = keys._b64url(keys._public_der(self.root_private, self.password)[-32:])
                if change == 'ambiguous_issuer':
                    manifest['keys'].append({**manifest['keys'][0], 'id': 'duplicate-public'})
                if change == 'unauthorized_beta':
                    manifest['keys'].append({'id': 'other', 'alg': 'Ed25519',
                        'x': keys._b64url(keys._public_der(self.root_private, self.password)[-32:])})
                    manifest['channels']['beta'] = [['issuer-a', 'other'], ['other']]
                if change == 'implicit_issuer_expiry': manifest['keys'][0].pop('expires')
                data = json.dumps(manifest, separators=(',', ':')).encode()
                (trust / 'manifest.min.json').write_bytes(data)
                signature = keys._sign(self.root_private, self.password, data)
                if change == 'bad_signature': signature = bytes([signature[0] ^ 1]) + signature[1:]
                (trust / 'manifest.sig').write_bytes(signature)
                (self.root / 'fishgram.json').write_text(json.dumps({'channel': channel}))
                result = subprocess.run(['pwsh', '-NoProfile', '-File', str(harness)],
                                        cwd=self.root, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, accepted, result.stdout + result.stderr)

    def test_issuer_must_be_authorized_in_every_channel_group(self):
        sign.check_issuer_authorization(self.manifest, "issuer-a", "stable", self.pub, now=self.now)
        with self.assertRaises(sign.SigningError):
            sign.check_issuer_authorization(self.manifest, "issuer-a", "beta", self.pub, now=self.now)

    def test_wrong_public_key_expiry_and_revocation_fail_closed(self):
        with self.assertRaises(sign.SigningError):
            sign.check_issuer_authorization(self.manifest, "issuer-b", "stable", self.pub, now=self.now)
        with self.assertRaises(sign.SigningError):
            sign.check_issuer_authorization(self.manifest, "issuer-a", "stable", self.pub, now=self.now + 90000)
        revoked = {**self.manifest, "revoked": ["issuer-a"]}
        with self.assertRaises(sign.SigningError):
            sign.check_issuer_authorization(revoked, "issuer-a", "stable", self.pub, now=self.now)
        with self.assertRaises(sign.SigningError):
            sign.check_issuer_authorization(self.manifest, "issuer-a", "stable", bytes(32), now=self.now)

    def test_candidate_run_provenance_binds_repo_workflow_success_branch_and_sha(self):
        run = {"id": 123, "repository": {"full_name": "owner/repo"},
               "path": ".github/workflows/product-candidate.yml", "event": "workflow_dispatch",
               "conclusion": "success", "head_branch": "main", "head_sha": "a" * 40}
        jobs = {"jobs": [{"name": "candidate", "conclusion": "success"}]}
        artifacts = {"artifacts": [{"name": "fishgram-product-candidate-123", "expired": False,
                                    "workflow_run": {"id": 123}}]}
        sign.verify_run_provenance(run, jobs, artifacts, "owner/repo", 123, "a" * 40)
        for changed in ({**run, "conclusion": "failure"}, {**run, "head_branch": "fork"},
                        {**run, "head_sha": "b" * 40}, {**run, "event": "pull_request"}):
            with self.subTest(run=changed), self.assertRaises(sign.SigningError):
                sign.verify_run_provenance(changed, jobs, artifacts, "owner/repo", 123, "a" * 40)
        wrong_artifact = {"artifacts": [{**artifacts["artifacts"][0], "workflow_run": {"id": 122}}]}
        with self.assertRaises(sign.SigningError):
            sign.verify_run_provenance(run, jobs, wrong_artifact, "owner/repo", 123, "a" * 40)

    def test_envelope_signature_roundtrip_and_payload_mutation_rejected(self):
        signed_region = b"TDUP-test-region"
        payload = b"payload hash is covered by SigningInput"
        signing_input = signed_region + __import__("hashlib").sha256(payload).digest()
        signature = keys._sign(self.issuer_private, self.password, signing_input)
        envelope = sign.Envelope(signed_region, self.trust_json, self.root_signature,
                                 [("issuer-a", signature)], payload, 0, (0, 1), 1, self.now)
        sign.verify_envelope(envelope, self.trust_manifest, "issuer-a", self.pub,
                             self.root_public.read_bytes(), "stable")
        with self.assertRaises(sign.SigningError):
            sign.verify_envelope(sign.Envelope(signed_region, self.trust_json, self.root_signature,
                                               [("issuer-a", signature)], payload + b"!", 0,
                                               (0, 1), 1, self.now), self.trust_manifest,
                                 "issuer-a", self.pub, self.root_public.read_bytes(), "stable")

    def test_qa_gate_rejects_false_approval_and_payload_hash_change(self):
        # Exercise the production gate before any issuer secret is requested.
        record = {
            "version": "7.2.9-r1", "parentCommit": "a" * 40, "sourceCommit": "b" * 40,
            "archive": {"name": "candidate.zip", "sha256": "c" * 64, "size": 1},
            "sourceArchive": {"name": "source.zip", "sha256": "d" * 64, "size": 1},
            "files": {"Telegram.exe": {"sha256": "e" * 64, "size": 1}},
        }
        qa = {
            "schema": 2, "kind": "fishgram-candidate-qa",
            "approvedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "candidate": {
                "version": record["version"], "parentCommit": record["parentCommit"],
                "sourceCommit": record["sourceCommit"], "archive": record["archive"],
                "sourceArchive": record["sourceArchive"],
                "files": {"Telegram.exe": {"sha256": "e" * 64}},
            },
            "checks": {name: True for name in gate.REQUIRED_CHECKS},
        }
        gate.verify_qa(record, qa)
        qa["checks"][gate.REQUIRED_CHECKS[0]] = False
        with self.assertRaises(Exception):
            gate.verify_qa(record, qa)
        qa["checks"][gate.REQUIRED_CHECKS[0]] = True
        qa["candidate"]["files"]["Telegram.exe"]["sha256"] = "f" * 64
        with self.assertRaises(Exception):
            gate.verify_qa(record, qa)

    def test_ci_secret_environment_is_consumed_and_private_file_is_cleaned(self):
        pem = self.issuer_private.read_text(encoding="ascii")
        env = {"FISHGRAM_TEST_KEY": pem, "FISHGRAM_TEST_PASSWORD": self.password}
        with mock.patch.dict(os.environ, env, clear=False):
            with sign.secret_key_file("FISHGRAM_TEST_KEY", "FISHGRAM_TEST_PASSWORD") as (path, password):
                self.assertTrue(path.is_file())
                self.assertEqual(path.read_text(encoding="ascii"), pem)
                self.assertEqual(password, self.password)
                self.assertNotIn("FISHGRAM_TEST_KEY", os.environ)
                self.assertNotIn("FISHGRAM_TEST_PASSWORD", os.environ)
                recorded = []
                original = keys._openssl

                def capture(*args, password=None):
                    recorded.append((args, password))
                    return original(*args, password=password)

                with mock.patch.object(keys, "_openssl", side_effect=capture):
                    signature = keys._sign(path, password, b"fixture payload")
                self.assertEqual(len(signature), 64)
                self.assertTrue(all(password not in args for args, _ in recorded))
                self.assertTrue(all("stdin" in args for args, _ in recorded))
                private_path = path
        self.assertFalse(private_path.exists())

    def test_unbound_embedded_manifest_fails_before_ci_secret_is_consumed(self):
        old_manifest = self.root / "build-manifest.json"
        old_manifest.write_text("{}", encoding="utf-8")
        options = SimpleNamespace(
            root=str(self.root), manifest=str(old_manifest), package="unused.zip", source="unused-source.zip",
            qa="qa.json", packer="Packer.exe", trust="trust", commit="a" * 40,
            run_json=None, jobs_json=None, artifacts_json=None, repository=None, run_id=None,
            issuer_id="issuer-a", prepared_dir="prepared", output_dir="output", key_env="FISHGRAM_TEST_KEY",
            passphrase_env="FISHGRAM_TEST_PASSWORD", prepare_only=False,
        )
        with mock.patch.dict(os.environ, {"FISHGRAM_TEST_KEY": "encrypted fixture", "FISHGRAM_TEST_PASSWORD": "fixture"}):
            with self.assertRaises(sign.SigningError):
                sign._sign(options)
            self.assertIn("FISHGRAM_TEST_KEY", os.environ)
            self.assertIn("FISHGRAM_TEST_PASSWORD", os.environ)


@unittest.skipUnless(os.environ.get("FISHGRAM_PACKER"), "Compiled production Packer integration is optional")
class PackerRoundTripTests(unittest.TestCase):
    def test_actual_packer_signing_input_embed_and_python_verification(self):
        tempfile.tempdir = str(ROOT / ".private")
        temp = tempfile.TemporaryDirectory(prefix="fishgram-packer-signing-test-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        trust = root / "trust"
        trust.mkdir()
        password = "disposable-packer-fixture-password"
        root_private, issuer_private = root / "root-private.pem", root / "issuer-private.pem"
        root_public, issuer_public = trust / "root-public.pem", trust / "issuer-public.pem"
        keys.generate_root(root_private, root_public, password)
        keys.generate_issuer(issuer_private, issuer_public, password)
        manifest_bytes, manifest_sig = keys.init_manifest(root_private, root_public, issuer_public,
                                                           "issuer-a", password)
        (trust / "manifest.min.json").write_bytes(manifest_bytes)
        (trust / "manifest.sig").write_bytes(manifest_sig)
        for name in ("Telegram.exe", "Updater.exe"):
            (root / name).write_bytes(("test-program:" + name).encode())
        signing_input = root / "signing-input.bin"
        command = [os.environ["FISHGRAM_PACKER"], "-path", "Telegram.exe", "-path", "Updater.exe",
                   "-version", "7002009", "-target", "win64", "-channel", "stable", "-counter", "9",
                   "-keys-loc", str(trust), "-emit-signing-input", str(signing_input)]
        result = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace")[-1200:])
        unsigned_matches = list(root.glob("*.unsigned"))
        self.assertEqual(len(unsigned_matches), 1)
        unsigned_path = unsigned_matches[0]
        unsigned = sign.parse_envelope(unsigned_path.read_bytes())
        self.assertEqual(signing_input.read_bytes(), unsigned.signed_region + __import__("hashlib").sha256(unsigned.payload).digest())
        signature = keys._sign(issuer_private, password, signing_input.read_bytes())
        parsed_trust = keys.verify_manifest(manifest_bytes, manifest_sig, root_public)
        issuer_raw = keys._public_der(issuer_private, password)[-32:]
        raw_qa = b'{"kind":"fishgram-signing-qa"}'
        qa = {'_file': 'approved.json', '_sha256': sign.sha256(raw_qa),
              '_document': __import__('base64').b64encode(raw_qa).decode(),
              'approvedAt': '2026-10-13T00:00:00Z'}
        manifest = {'version': '7.2.9-r9', 'parentCommit': 'b' * 40, 'sourceCommit': 'c' * 40,
                    'archive': {}, 'sourceArchive': {}}
        auth = {'updateVersion': str(unsigned.version), 'packer': {'channel': 'stable'}}
        package, record = sign._embed_and_record(
            Path(os.environ['FISHGRAM_PACKER']), trust, unsigned_path, signature, 'issuer-a',
            manifest, auth, parsed_trust, issuer_raw, root_public.read_bytes(), unsigned,
            root/'signed-output', qa)
        saved = json.loads(record.read_text())
        self.assertEqual(__import__('base64').b64decode(saved['qa']['document']), raw_qa)
        self.assertEqual(saved['qa']['sha256'], sign.sha256(raw_qa))
        signed = sign.parse_envelope(package.read_bytes())
        sign.verify_envelope(signed, parsed_trust, "issuer-a", issuer_raw,
                             root_public.read_bytes(), "stable", expected_unsigned=unsigned)


if __name__ == "__main__":
    unittest.main()
