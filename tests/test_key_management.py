import importlib.util
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

MODULE = Path(__file__).resolve().parents[1] / "tools" / "key_management.py"
spec = importlib.util.spec_from_file_location("key_management", MODULE)
keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keys)


class KeyManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (os.environ.get("FISHGRAM_OPENSSL") or shutil.which("openssl")):
            raise unittest.SkipTest("OpenSSL CLI is required for real signature tests")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fishgram-key-test-")
        self.root = Path(self.temp.name)
        self.root_private = self.root / "root-private.pem"
        self.root_public = self.root / "root-public.pem"
        self.issuer_private = self.root / "issuer-private.pem"
        self.issuer_public = self.root / "issuer-public.pem"
        self.password = "test-only-passphrase-9251"
        keys.generate_root(self.root_private, self.root_public, self.password)
        self.assertIn(b"ENCRYPTED PRIVATE KEY", self.root_private.read_bytes()[:80])
        before = int(time.time())
        _, self.generated_expiry = keys.generate_issuer(
            self.issuer_private, self.issuer_public, self.password
        )
        after = int(time.time())
        self.assertGreaterEqual(self.generated_expiry, before + keys.YEAR_SECONDS)
        self.assertLessEqual(self.generated_expiry, after + keys.YEAR_SECONDS)
        self.assertIn(b"ENCRYPTED PRIVATE KEY", self.issuer_private.read_bytes()[:80])
        timestamp = 1_800_000_000
        raw_pub = keys._public_der(self.issuer_private, self.password)[-32:]
        self.initial = {
            "format": 1,
            "manifest_version": 1,
            "issued": timestamp,
            "expires": timestamp + keys.YEAR_SECONDS,
            "keys": [{"id": "issuer-a", "alg": "Ed25519", "x": keys._b64url(raw_pub), "expires": timestamp + 90 * 86400}],
            "channels": {"stable": [["issuer-a"]], "beta": [["issuer-a"]]},
            "revoked": [],
        }
        self.initial_bytes, self.initial_sig = self._signed(self.initial)

    def tearDown(self):
        self.temp.cleanup()

    def _signed(self, obj):
        data = json.dumps(obj, separators=(",", ":")).encode()
        signature = keys._sign(self.root_private, self.password, data)
        return data, signature

    def _update(self, **kwargs):
        values = dict(
            previous_data=self.initial_bytes,
            previous_signature=self.initial_sig,
            root_public=self.root_public,
            root_private=self.root_private,
            password=self.password,
            version=2,
            add_keys=[],
            renew_ids=[],
            revoke_ids=[],
            channel_groups=[],
            now=1_900_000_000,
        )
        values.update(kwargs)
        return keys.update_manifest(**values)

    def test_root_signature_is_real_and_bad_signature_is_rejected(self):
        parsed = keys.verify_manifest(self.initial_bytes, self.initial_sig, self.root_public)
        self.assertEqual(parsed["manifest_version"], 1)
        with self.assertRaises(keys.KeyManagementError):
            keys.verify_manifest(self.initial_bytes + b" ", self.initial_sig, self.root_public)

    def test_manifest_version_must_increase_and_generated_version_verifies(self):
        with self.assertRaisesRegex(keys.KeyManagementError, "greater"):
            self._update(version=1)
        data, signature = self._update()
        parsed = keys.verify_manifest(data, signature, self.root_public)
        self.assertEqual(parsed["manifest_version"], 2)

    def test_revocation_removes_authority_and_records_revoked_id(self):
        data, signature = self._update(revoke_ids=["issuer-a"])
        parsed = keys.verify_manifest(data, signature, self.root_public)
        self.assertIn("issuer-a", parsed["revoked"])
        self.assertEqual(parsed["channels"], {})

    def test_revocation_cannot_leave_an_empty_required_group(self):
        changed = dict(self.initial)
        changed["channels"] = {
            "stable": [["issuer-a"], ["issuer-a", "issuer-b"]],
            "beta": [["issuer-a"]],
        }
        self.initial_bytes, self.initial_sig = self._signed(changed)
        with self.assertRaisesRegex(keys.KeyManagementError, "without an authorized"):
            self._update(revoke_ids=["issuer-a"])

    def test_renewal_extends_expiry_by_one_year(self):
        data, signature = self._update(renew_ids=["issuer-a"])
        parsed = keys.verify_manifest(data, signature, self.root_public)
        issuer = next(key for key in parsed["keys"] if key["id"] == "issuer-a")
        self.assertEqual(issuer["expires"], 1_900_000_000 + keys.YEAR_SECONDS)

    def test_new_issuer_is_added_as_one_year_ed25519_key(self):
        data, signature = self._update(
            add_keys=[("issuer-b", self.issuer_public)],
            channel_groups=["stable=issuer-a,issuer-b"],
        )
        parsed = keys.verify_manifest(data, signature, self.root_public)
        issuer = next(key for key in parsed["keys"] if key["id"] == "issuer-b")
        self.assertEqual(issuer["alg"], "Ed25519")
        self.assertEqual(issuer["expires"], 1_900_000_000 + keys.YEAR_SECONDS)

    def test_45_day_maintenance_window_and_expired_keys(self):
        now = 1_900_000_000
        data = json.dumps({"keys": [
            {"id": "soon", "expires": now + 44 * 86400},
            {"id": "edge", "expires": now + 45 * 86400},
            {"id": "later", "expires": now + 46 * 86400},
            {"id": "old", "expires": now - 1},
        ]}).encode()
        self.assertEqual(
            keys.maintenance_report(data, now=now),
            [("old", -1), ("soon", 44), ("edge", 45)],
        )

    def test_backup_copies_only_encrypted_root_and_refuses_overwrite(self):
        backup = self.root / "offline-root.pem"
        keys.backup_encrypted_root(self.root_private, backup)
        self.assertEqual(backup.read_bytes(), self.root_private.read_bytes())
        with self.assertRaisesRegex(keys.KeyManagementError, "overwrite"):
            keys.backup_encrypted_root(self.root_private, backup)
        plaintext = self.root / "plain.pem"
        plaintext.write_text("-----BEGIN PRIVATE KEY-----\\nnot encrypted", encoding="ascii")
        with self.assertRaisesRegex(keys.KeyManagementError, "not encrypted PKCS#8"):
            keys.backup_encrypted_root(plaintext, self.root / "plain-backup.pem")

    def test_init_manifest_bootstraps_parser_compatible_public_fixture(self):
        now = 1_900_000_000
        data, signature = keys.init_manifest(
            self.root_private, self.root_public, self.issuer_public,
            "issuer-a", self.password, now=now,
        )
        parsed = keys.verify_manifest(data, signature, self.root_public)
        self.assertEqual(parsed["format"], 1)
        self.assertEqual(parsed["manifest_version"], 1)
        self.assertEqual(parsed["issued"], now)
        self.assertEqual(parsed["expires"], now + keys.YEAR_SECONDS)
        self.assertEqual(parsed["channels"], {"stable": [["issuer-a"]], "beta": [["issuer-a"]]})
        self.assertEqual(parsed["revoked"], [])
        self.assertEqual(parsed["keys"][0]["expires"], now + keys.YEAR_SECONDS)
        cli_output = self.root / "cli-output"
        cli_output.mkdir()
        fixture = self.root / "public-fixture"
        fixture.mkdir()
        args = type("Args", (), {
            "root_private": str(self.root_private),
            "root_public": str(self.root_public),
            "issuer_public": str(self.issuer_public),
            "issuer_id": "issuer-cli",
            "output": str(cli_output / "manifest.min.json"),
            "signature_output": str(cli_output / "manifest.sig"),
            "public_fixture_dir": str(fixture),
        })()
        with mock.patch.object(keys.getpass, "getpass", return_value=self.password):
            with contextlib.redirect_stdout(io.StringIO()):
                keys._cmd_init_manifest(args)
        cli_manifest = (fixture / "manifest.min.json").read_bytes()
        cli_signature = (fixture / "manifest.sig").read_bytes()
        self.assertEqual(
            keys.verify_manifest(cli_manifest, cli_signature, self.root_public)["manifest_version"],
            1,
        )
        # This public-only fixture can be supplied to the C++ core verifier;
        # neither private key is copied into the fixture directory.
        self.assertEqual(
            {item.name for item in fixture.iterdir()},
            {"root-public.pem", "issuer-public.pem", "manifest.min.json", "manifest.sig"},
        )
        self.assertEqual(len(signature), 64)

    def test_init_manifest_rejects_root_public_mismatch(self):
        other_private = self.root / "other-root.pem"
        other_public = self.root / "other-root-public.pem"
        keys.generate_root(other_private, other_public, self.password)
        with self.assertRaisesRegex(keys.KeyManagementError, "does not match"):
            keys.init_manifest(
                self.root_private, other_public, self.issuer_public,
                "issuer-a", self.password, now=1_900_000_000,
            )

    def test_password_rejects_line_delimiters_and_nul(self):
        for password in ("long-passphrase\rline", "long-passphrase\nline", "long-passphrase\0x"):
            with self.subTest(password=repr(password)):
                with self.assertRaisesRegex(keys.KeyManagementError, "CR, LF, or NUL"):
                    keys._openssl("version", password=password)
                with mock.patch.object(keys.getpass, "getpass", return_value=password):
                    with self.assertRaisesRegex(keys.KeyManagementError, "CR, LF, or NUL"):
                        keys._password()

    def test_generation_failure_and_acl_failure_remove_partial_private_key(self):
        partial = self.root / "partial-private.pem"

        def write_then_fail(*args, **kwargs):
            partial.write_bytes(b"partial secret")
            raise keys.KeyManagementError("simulated OpenSSL failure")

        with mock.patch.object(keys, "_openssl", side_effect=write_then_fail):
            with self.assertRaisesRegex(keys.KeyManagementError, "simulated"):
                keys._generate_private(partial, self.password)
        self.assertFalse(partial.exists())

        protected = self.root / "acl-private.pem"
        with mock.patch.object(keys, "_private_mode", side_effect=keys.KeyManagementError("ACL denied")):
            with self.assertRaisesRegex(keys.KeyManagementError, "ACL denied"):
                keys.generate_issuer(protected, self.root / "acl-public.pem", self.password)
        self.assertFalse(protected.exists())

    def test_root_bootstrap_failure_removes_generated_private_and_public_files(self):
        private = self.root / "failed-root.pem"
        public = self.root / "failed-root-public.pem"
        backup_directory = self.root / "backup"
        backup_directory.mkdir()
        args = type("Args", (), {
            "private": str(private),
            "public": str(public),
            "backup": str(backup_directory / "root.pem"),
        })()
        with mock.patch.object(keys.getpass, "getpass", return_value=self.password):
            with mock.patch.object(
                keys, "backup_encrypted_root", side_effect=keys.KeyManagementError("backup failed")
            ):
                with self.assertRaisesRegex(keys.KeyManagementError, "backup failed"):
                    keys._cmd_init_root(args)
        self.assertFalse(private.exists())
        self.assertFalse(public.exists())

    def test_new_path_rejects_symlink_parent_and_dangling_target(self):
        actual = self.root / "actual"
        actual.mkdir()
        linked = self.root / "linked"
        try:
            linked.symlink_to(actual, target_is_directory=True)
            (self.root / "dangling").symlink_to(self.root / "absent")
        except OSError:
            self.skipTest("Symlink creation is unavailable for this Windows token.")
        with self.assertRaisesRegex(keys.KeyManagementError, "redirected|reparse-point"):
            keys._require_new_path(linked / "secret.pem")
        with self.assertRaisesRegex(keys.KeyManagementError, "overwrite"):
            keys._require_new_path(self.root / "dangling")


if __name__ == "__main__":
    unittest.main()
