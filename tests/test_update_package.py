"""Real Python root manifest -> production Packer -> production C++ verifier."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("keys", Path(__file__).resolve().parents[1] / "tools/key_management.py")
keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keys)


@unittest.skipUnless(os.environ.get("FISHGRAM_PACKER") and os.environ.get("FISHGRAM_PACKAGE_VERIFY"),
                     "Run tools/test-update-verify.ps1 for compiled production integration")
class UpdatePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fishgram-package-test-")
        self.root = Path(self.temp.name)
        self.packer = os.environ["FISHGRAM_PACKER"]
        self.verifier = os.environ["FISHGRAM_PACKAGE_VERIFY"]
        self.password = "disposable-test-only-password"
        self.trust = self.root / "trust"
        self.trust.mkdir()
        self.private = self.root / "root-private.pem"
        self.issuer = self.root / "issuer-private.pem"
        keys.generate_root(self.private, self.trust / "root-public.pem", self.password)
        keys.generate_issuer(self.issuer, self.trust / "issuer-public.pem", self.password)
        self.manifest, self.signature = keys.init_manifest(
            self.private, self.trust / "root-public.pem", self.trust / "issuer-public.pem",
            "test-issuer", self.password)
        self._write_manifest(self.manifest, self.signature)
        for name in ("Telegram.exe", "Updater.exe"):
            (self.root / name).write_bytes(("test-program:" + name).encode())

    def tearDown(self):
        self.temp.cleanup()

    def _write_manifest(self, data, signature):
        (self.trust / "manifest.min.json").write_bytes(data)
        (self.trust / "manifest.sig").write_bytes(signature)

    def _run_packer(self, args, success=True):
        result = subprocess.run([self.packer, *map(str, args)], cwd=self.root,
                                stdin=subprocess.DEVNULL, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout.decode(errors="replace")[-1500:])

    def _pack(self, base=7002009, revision=9, channel="stable", target="win64"):
        signing = self.root / "signing.bin"
        self._run_packer(["-path", "Telegram.exe", "-path", "Updater.exe", "-version", base,
                          "-target", target, "-channel", channel, "-counter", revision,
                          "-keys-loc", self.trust, "-emit-signing-input", signing])
        unsigned = next(self.root.glob("*.unsigned"))
        signature_file = self.root / "package.sig"
        signature_file.write_bytes(keys._sign(self.issuer, self.password, signing.read_bytes()))
        self._run_packer(["-unsigned", unsigned, "-keys-loc", self.trust,
                          "-embed-signatures", f"test-issuer:{signature_file}"])
        return Path(str(unsigned)[:-len(".unsigned")])

    def _verify(self, package, success=True, running=(7002009 << 32) | 8, channel="stable", beta=False):
        result = subprocess.run([self.verifier, str(package), str(self.trust), str(running),
                                 channel, "beta" if beta else "stable"], capture_output=True)
        self.assertEqual(result.returncode, 0 if success else 1)

    def test_same_baseline_revision_and_exact_payload_roundtrip(self):
        package = self._pack()
        self.assertEqual(package.name, "fishgram-update-win-x64-7002009-r9")
        self._verify(package)
        self._verify(package, success=False, running=(7002009 << 32) | 9)
        self._verify(package, success=False, running=(7002009 << 32) | 10)

    def test_new_baseline_accepts_lower_revision(self):
        self._verify(self._pack(base=7002010, revision=1))

    def test_beta_requires_opt_in(self):
        package = self._pack(channel="beta")
        self._verify(package, success=False)
        self._verify(package, beta=True)
        self._verify(package, channel="beta")

    def test_wrong_architecture(self):
        self._verify(self._pack(target="winarm"), success=False)

    def test_corruption_and_truncation(self):
        package = self._pack()
        original = package.read_bytes()
        package.write_bytes(original[:-1])
        self._verify(package, success=False)
        corrupt = bytearray(original)
        corrupt[-1] ^= 1
        package.write_bytes(corrupt)
        self._verify(package, success=False)
        package.write_bytes(b"official-legacy-rsa-package")
        self._verify(package, success=False)

    def test_existing_signed_artifact_cannot_be_overwritten(self):
        package = self._pack()
        original = package.read_bytes()
        self._run_packer(["-unsigned", str(package) + ".unsigned", "-keys-loc", self.trust,
                          "-embed-signatures", f"test-issuer:{self.root / 'package.sig'}"], success=False)
        self.assertEqual(package.read_bytes(), original)

    def test_wrong_issuer_signature_cannot_produce_package(self):
        package = self._pack()
        package.unlink()
        (self.root / "package.sig").write_bytes(bytes(64))
        self._run_packer(["-unsigned", str(package) + ".unsigned", "-keys-loc", self.trust,
                          "-embed-signatures", f"test-issuer:{self.root / 'package.sig'}"], success=False)
        self.assertFalse(package.exists())

    def test_root_signed_revocation_and_manifest_rollback(self):
        package = self._pack()
        changed = json.loads(self.manifest)
        changed["manifest_version"] = 2
        changed["revoked"] = ["test-issuer"]
        data = json.dumps(changed, separators=(",", ":")).encode()
        self._write_manifest(data, keys._sign(self.private, self.password, data))
        self._verify(package, success=False)

    def test_invalid_manifest_root_signature(self):
        self._write_manifest(self.manifest, bytes(64))
        self._run_packer(["-path", "Telegram.exe", "-version", 7002009,
                          "-target", "win64", "-channel", "stable", "-counter", 9,
                          "-keys-loc", self.trust, "-emit-signing-input", "unused.bin"], success=False)

    def test_packer_rejects_account_data_duplicates_and_zero_revision(self):
        (self.root / "tdata").mkdir()
        (self.root / "tdata" / "data.dll").write_bytes(b"private-account-data-test")
        common = ["-version", 7002009, "-target", "win64", "-channel", "stable",
                  "-keys-loc", self.trust, "-emit-signing-input", "unused.bin"]
        for paths in (["-path", "tdata/data.dll"],
                      ["-path", "Telegram.exe", "-path", "Telegram.exe"]):
            self._run_packer([*common, "-counter", 9, *paths], success=False)
        self._run_packer([*common, "-counter", 0, "-path", "Telegram.exe"], success=False)


if __name__ == "__main__":
    unittest.main()
