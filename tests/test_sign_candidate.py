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
            "schema": 1, "kind": "fishgram-candidate-qa", "approver": "maintainer",
            "approvedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "environment": "clean-windows-11", "candidate": {
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
        sig_path = root / "issuer.sig"
        sig_path.write_bytes(signature)
        result = subprocess.run([os.environ["FISHGRAM_PACKER"], "-unsigned", str(unsigned_path),
                                 "-keys-loc", str(trust), "-embed-signatures", f"issuer-a:{sig_path}"],
                                cwd=root, stdin=subprocess.DEVNULL, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace")[-1200:])
        package = Path(str(unsigned_path)[:-len(".unsigned")])
        signed = sign.parse_envelope(package.read_bytes())
        parsed_trust = keys.verify_manifest(manifest_bytes, manifest_sig, root_public)
        issuer_raw = keys._public_der(issuer_private, password)[-32:]
        sign.verify_envelope(signed, parsed_trust, "issuer-a", issuer_raw,
                             root_public.read_bytes(), "stable", expected_unsigned=unsigned)


if __name__ == "__main__":
    unittest.main()
