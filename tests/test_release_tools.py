import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / 'tools' / 'release_tools.py'
spec = importlib.util.spec_from_file_location('release_tools', MODULE)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class PackageSafetyTests(unittest.TestCase):
    def test_payload_must_match_hashes_recorded_at_build_time(self):
        with tempfile.TemporaryDirectory() as temp:
            program = Path(temp) / 'Telegram.exe'
            program.write_bytes(b'cloud candidate')
            record = {'files': {program.name: {'size': program.stat().st_size,
                                               'sha256': release.sha256(program)}}}
            release.verify_built_payload([program], record)
            program.write_bytes(b'changed payload')
            with self.assertRaisesRegex(ValueError, 'differs from the built candidate'):
                release.verify_built_payload([program], record)

    def test_missing_or_extra_build_hashes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            program = Path(temp) / 'Telegram.exe'
            program.write_bytes(b'candidate')
            entry = {'size': program.stat().st_size, 'sha256': release.sha256(program)}
            for files in (None, {}, {program.name: entry, 'extra.dll': entry},
                          {program.name: {'size': True, 'sha256': entry['sha256']}},
                          {program.name: {'size': entry['size'], 'sha256': 'bad'}}):
                with self.assertRaises(ValueError):
                    release.verify_built_payload([program], {'files': files})

    def test_numeric_version_handles_same_baseline_and_large_integers(self):
        first = release.update_version('7.2.9', 8)
        second = release.update_version('7.2.9', 9)
        self.assertLess(int(first), int(second))
        self.assertIsInstance(first, str)
        self.assertEqual(int(first), (7002009 << 32) | 8)
        for invalid in [-1, 0, 2**32, True, 1.5]:
            with self.assertRaises(ValueError):
                release.update_version('7.2.9', invalid)

    def test_requires_complete_payload_and_no_account_data(self):
        recipe = {'payloadFiles': ['Telegram.exe', 'Updater.exe'], 'payloadDlls': []}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'Telegram.exe').write_bytes(b'candidate')
            with self.assertRaises(ValueError):
                release.validate_payload(directory, recipe)
            (directory / 'Updater.exe').write_bytes(b'updater')
            self.assertEqual(len(release.validate_payload(directory, recipe)), 2)
            for invalid in ['tdata/key_data', 'FishGramData/log.txt', '.private/key.pem', 'account.json', 'extra.dll']:
                target = directory / invalid
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'private')
                with self.assertRaises(ValueError):
                    release.validate_payload(directory, recipe)
                target.unlink()
                for parent in list(target.parents):
                    if parent == directory:
                        break
                    if parent.exists() and not list(parent.iterdir()):
                        parent.rmdir()

    def test_rejects_symlink_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            actual = directory / 'actual'
            actual.write_bytes(b'x')
            try:
                (directory / 'Telegram.exe').symlink_to(actual)
            except OSError:
                self.skipTest('Symlink creation is unavailable for this Windows token.')
            with self.assertRaises(ValueError):
                release.validate_payload(directory, {'payloadFiles': ['Telegram.exe'], 'payloadDlls': []})


if __name__ == '__main__':
    unittest.main()
