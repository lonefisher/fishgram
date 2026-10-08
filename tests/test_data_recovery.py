import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location('data_recovery', Path(__file__).resolve().parents[1] / 'tools/data_recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class DataRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fishgram-data-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / 'FishGramData'
        (self.work / 'tdata' / 'account').mkdir(parents=True)
        (self.work / 'tdata' / 'account' / 'map0').write_bytes(b'synthetic account state')
        self.exe = self.root / 'Telegram.exe'
        self.exe.write_bytes(b'synthetic executable')
        self.snapshots = self.root / 'private-snapshots'

    def snapshot(self, **kwargs):
        return recovery.snapshot(self.work, self.snapshots, self.exe, '7.2.9-r8',
                                 process_probe=lambda path: False, **kwargs)

    def restore(self, name, **kwargs):
        return recovery.restore_snapshot(self.work, self.snapshots, self.exe, name,
                                         confirm_restore=True, process_probe=lambda path: False, **kwargs)

    def test_snapshot_hashes_and_default_two_copy_retention(self):
        names = []
        for index in range(3):
            (self.work / 'tdata' / 'account' / 'map0').write_bytes(bytes([index]))
            names.append(self.snapshot())
        self.assertFalse((self.snapshots / names[0]).exists())
        self.assertTrue((self.snapshots / names[1]).is_dir())
        self.assertEqual((self.snapshots / names[2] / 'tdata' / 'account' / 'map0').read_bytes(), b'\x02')
        manifest = json.loads((self.snapshots / names[2] / 'manifest.json').read_text())
        self.assertEqual(manifest['version'], '7.2.9-r8')
        self.assertEqual(len(manifest['files']['account/map0']['sha256']), 64)
        self.assertNotIn(str(self.work), json.dumps(manifest))

    def test_running_process_rejected_before_writing(self):
        with self.assertRaisesRegex(recovery.RecoveryError, 'running'):
            recovery.snapshot(self.work, self.snapshots, self.exe, '7.2.9-r8', process_probe=lambda path: True)
        self.assertFalse(self.snapshots.exists())
        self.assertEqual((self.work / 'tdata' / 'account' / 'map0').read_bytes(), b'synthetic account state')

    def test_process_restart_during_copy_abandons_snapshot(self):
        calls = iter([False, True])
        with self.assertRaisesRegex(recovery.RecoveryError, 'running'):
            recovery.snapshot(self.work, self.snapshots, self.exe, '7.2.9-r8', process_probe=lambda path: next(calls))
        self.assertFalse(any(path.name.startswith('snapshot-') for path in self.snapshots.iterdir()))

    def test_copy_failure_keeps_previous_snapshots(self):
        prior = self.snapshot()
        with mock.patch.object(recovery.shutil, 'copyfile', side_effect=OSError('simulated disk full')):
            with self.assertRaises(recovery.RecoveryError):
                self.snapshot()
        self.assertTrue((self.snapshots / prior / 'manifest.json').is_file())

    def test_changed_source_is_not_a_consistent_snapshot(self):
        real_copy = recovery.shutil.copyfile
        def changing_copy(source, target):
            real_copy(source, target)
            Path(source).write_bytes(b'changed during snapshot')
        with mock.patch.object(recovery.shutil, 'copyfile', side_effect=changing_copy):
            with self.assertRaisesRegex(recovery.RecoveryError, 'changed'):
                self.snapshot()
        self.assertFalse(any(path.name.startswith('snapshot-') for path in self.snapshots.iterdir()))

    def test_snapshot_root_overlap_is_rejected(self):
        for root in [self.work, self.work / 'tdata' / 'snapshots', self.root]:
            with self.assertRaisesRegex(recovery.RecoveryError, 'overlap'):
                recovery.snapshot(self.work, root, self.exe, '7.2.9-r8', process_probe=lambda path: False)

    def test_restore_requires_explicit_choice(self):
        name = self.snapshot()
        with self.assertRaisesRegex(recovery.RecoveryError, 'explicit'):
            recovery.restore_snapshot(self.work, self.snapshots, self.exe, name, process_probe=lambda path: False)

    def test_empty_account_directories_survive_snapshot_and_restore(self):
        (self.work / 'tdata' / 'empty-cache').mkdir()
        name = self.snapshot()
        (self.work / 'tdata' / 'empty-cache').rmdir()
        self.restore(name)
        self.assertTrue((self.work / 'tdata' / 'empty-cache').is_dir())

    def test_running_client_blocks_restore_without_changing_data(self):
        name = self.snapshot()
        with self.assertRaisesRegex(recovery.RecoveryError, 'running'):
            recovery.restore_snapshot(self.work, self.snapshots, self.exe, name,
                                      confirm_restore=True, process_probe=lambda path: True)
        self.assertEqual((self.work / 'tdata' / 'account' / 'map0').read_bytes(), b'synthetic account state')

    def test_restore_preserves_previous_data_as_rollback(self):
        name = self.snapshot()
        (self.work / 'tdata' / 'account' / 'map0').write_bytes(b'newer state')
        backup = self.restore(name)
        self.assertEqual((self.work / 'tdata' / 'account' / 'map0').read_bytes(), b'synthetic account state')
        self.assertEqual((self.work / backup / 'account' / 'map0').read_bytes(), b'newer state')
        self.assertFalse((self.work / '.fishgram-data-restore.json').exists())

    def test_tampered_and_extra_snapshot_files_are_rejected(self):
        name = self.snapshot()
        saved = self.snapshots / name / 'tdata' / 'account' / 'map0'
        saved.write_bytes(b'corrupted state')
        with self.assertRaisesRegex(recovery.RecoveryError, 'verification'):
            self.restore(name)
        saved.write_bytes(b'synthetic account state')
        (saved.parent / 'extra').write_bytes(b'extra')
        with self.assertRaisesRegex(recovery.RecoveryError, 'verification'):
            self.restore(name)
        self.assertEqual((self.work / 'tdata' / 'account' / 'map0').read_bytes(), b'synthetic account state')

    def test_manifest_path_traversal_is_rejected(self):
        name = self.snapshot()
        manifest = self.snapshots / name / 'manifest.json'
        value = json.loads(manifest.read_text())
        value['files']['../outside'] = next(iter(value['files'].values()))
        manifest.write_text(json.dumps(value))
        with self.assertRaises(recovery.RecoveryError):
            self.restore(name)
        with self.assertRaises(recovery.RecoveryError):
            self.restore('../outside')

    def test_restore_replacement_failure_recovers_old_data(self):
        name = self.snapshot()
        (self.work / 'tdata' / 'account' / 'map0').write_bytes(b'newer state')
        real_replace = recovery.os.replace
        def fail_stage(source, target):
            if Path(source).name.startswith('.fishgram-data-stage-') and Path(target).name == 'tdata':
                raise OSError('simulated locked destination')
            return real_replace(source, target)
        with mock.patch.object(recovery.os, 'replace', side_effect=fail_stage):
            with self.assertRaises(recovery.RecoveryError):
                self.restore(name)
        self.assertEqual((self.work / 'tdata' / 'account' / 'map0').read_bytes(), b'newer state')

    def test_interrupted_restore_needs_explicit_recovery_and_restores_old_data(self):
        name = self.snapshot()
        (self.work / 'tdata' / 'account' / 'map0').write_bytes(b'newer state')
        def interrupt():
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.restore(name, after_old_moved=interrupt)
        self.assertFalse((self.work / 'tdata').exists())
        with self.assertRaisesRegex(recovery.RecoveryError, 'explicit'):
            recovery.recover_restore(self.work, self.exe, process_probe=lambda path: False)
        recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=lambda path: False)
        self.assertEqual((self.work / 'tdata' / 'account' / 'map0').read_bytes(), b'newer state')

    def test_links_are_rejected_without_following_them(self):
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.work / 'tdata' / 'linked'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('This Windows token cannot create symlinks')
        with self.assertRaisesRegex(recovery.RecoveryError, 'link|reparse'):
            self.snapshot()
        self.assertEqual(list(outside.iterdir()), [])

    def test_space_preflight_does_not_change_data(self):
        with mock.patch.object(recovery.shutil, 'disk_usage', return_value=(100, 100, 0)):
            with self.assertRaisesRegex(recovery.RecoveryError, 'space'):
                self.snapshot()
        self.assertEqual((self.work / 'tdata' / 'account' / 'map0').read_bytes(), b'synthetic account state')

    @unittest.skipUnless(os.name == 'nt', 'Actual Windows process inventory')
    def test_actual_process_probe_matches_current_executable(self):
        import sys
        self.assertTrue(recovery.process_running(Path(sys.executable).resolve()))
        self.assertFalse(recovery.process_running(self.exe))


if __name__ == '__main__':
    unittest.main()
