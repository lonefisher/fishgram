import tempfile
import json
import unittest
from pathlib import Path
import sys
import struct
import os
import subprocess
import ctypes
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import program_recovery
from program_recovery import RecoveryError, _ClientSessionLease, _InstallLock, _directory_guard, list_backups, restore_backup


class CanonicalInstallTests(unittest.TestCase):
    def canonical_returned_by_windows(self, value):
        # Never read/write a real drive root; only emulate GetFullPathNameW.
        from unittest.mock import Mock
        def full_path(_input, _size, buffer, _last):
            buffer.value = value
            return len(value)
        kernel = Mock()
        kernel.GetFullPathNameW.side_effect = full_path
        with patch.object(program_recovery.ctypes, 'WinDLL', return_value=kernel):
            return program_recovery._canonical_install(Path('synthetic-input'))

    def test_volume_root_remains_absolute_and_child_does_not_follow_drive_cwd(self):
        for value in ('D:\\', 'D:/', 'C:\\'):
            with self.subTest(value=value):
                actual = self.canonical_returned_by_windows(value)
                self.assertTrue(actual.is_absolute())
                self.assertEqual(str(actual), value[:2] + '\\')
                self.assertEqual(str(actual / 'Telegram.exe'), value[:2] + '\\Telegram.exe')

    def test_directory_trailing_separator_is_normalized_without_losing_anchor(self):
        actual = self.canonical_returned_by_windows('D:\\Apps\\FishGram\\')
        self.assertEqual(str(actual), 'D:\\Apps\\FishGram')
        self.assertTrue(actual.is_absolute())

    def test_unc_and_device_boundaries_remain_rejected(self):
        for value in ('\\\\server\\share\\', '\\\\?\\D:\\', '\\\\.\\D:\\'):
            with self.subTest(value=value), self.assertRaisesRegex(RecoveryError, '本地盘符'):
                self.canonical_returned_by_windows(value)


class ProgramRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fishgram-program-test-")
        trusted_acl = program_recovery._trusted_acl
        self.acl_patch = patch("program_recovery._trusted_acl", lambda path, **kwargs:
                               trusted_acl(path, **kwargs) if path.name == "client-session.lock" else None)
        self.acl_patch.start()
        self.install = Path(self.temp.name).resolve() / "install"
        self.meta = self.install / ".fishgram-update"
        self.versions = self.meta / "versions"
        self.versions.mkdir(parents=True)
        self.exe = self.install / "Telegram.exe"
        self.exe.write_bytes(b"current")
        self.dll = self.install / "core.dll"
        self.dll.write_bytes(b"current-dll")
        self.account = self.install / "tdata" / "key_data"
        self.account.parent.mkdir()
        self.account.write_bytes(b"account-secret")
        self.backup = self.versions / "0123456789abcdef-00000001-0123456789abcdef"
        self.backup.mkdir()
        (self.backup / "Telegram.exe").write_bytes(b"previous")
        (self.backup / "core.dll").write_bytes(b"previous-dll")

    def tearDown(self):
        self.acl_patch.stop()
        self.temp.cleanup()

    def test_list_is_read_only_and_does_not_call_target_version_previous_version(self):
        records = list_backups(self.install)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].backup_id, self.backup.name)
        self.assertEqual(records[0].target_version, "unknown (journal not archived)")
        self.assertEqual(records[0].previous_version, "unknown previous version")
        self.assertEqual(self.exe.read_bytes(), b"current")

    def test_optional_production_journal_keeps_target_and_previous_versions_distinct(self):
        (self.backup / "journal.bin").write_bytes(make_journal(self.backup.name))
        item = list_backups(self.install)[0]
        self.assertEqual(item.target_version, "7002009008")
        self.assertEqual(item.previous_version, "unknown previous version")
        self.assertNotIn("journal.bin", item.files)

    def test_journal_missing_declared_backup_dll_rejects_list_and_restore(self):
        (self.backup / "journal.bin").write_bytes(make_journal(self.backup.name))
        (self.backup / "core.dll").unlink()
        self.assert_invalid_journal_inventory_preserves_install()

    def test_journal_undeclared_backup_dll_rejects_list_and_restore(self):
        (self.backup / "journal.bin").write_bytes(make_journal(self.backup.name))
        (self.backup / "extra.dll").write_bytes(b"undeclared")
        self.assert_invalid_journal_inventory_preserves_install()

    def test_journal_backup_inventory_comparison_is_case_insensitive(self):
        (self.backup / "journal.bin").write_bytes(make_journal(
            self.backup.name, previous=("TELEGRAM.EXE", "CORE.DLL")))
        self.assertEqual(len(list_backups(self.install)), 1)
        restore_backup(self.install, self.backup.name,
                       confirm_program_restore=self.backup.name, process_running=False)
        self.assertEqual(self.exe.read_bytes(), b"previous")
        self.assertEqual(self.dll.read_bytes(), b"previous-dll")

    def assert_invalid_journal_inventory_preserves_install(self):
        with self.assertRaisesRegex(RecoveryError, "journal.*清单"):
            list_backups(self.install)
        with self.assertRaisesRegex(RecoveryError, "journal.*清单"):
            restore_backup(self.install, self.backup.name,
                           confirm_program_restore=self.backup.name, process_running=False)
        self.assertEqual(self.exe.read_bytes(), b"current")
        self.assertEqual(self.dll.read_bytes(), b"current-dll")
        self.assertEqual(self.account.read_bytes(), b"account-secret")
        self.assertFalse((self.meta / "manual").exists())

    def test_restore_requires_explicit_backup_id_confirmation(self):
        with self.assertRaises(RecoveryError):
            restore_backup(self.install, self.backup.name)

    def test_restore_creates_latest_manual_backup_and_preserves_account(self):
        restore_backup(self.install, self.backup.name, confirm_program_restore=self.backup.name,
                       process_running=False)
        self.assertEqual(self.exe.read_bytes(), b"previous")
        self.assertEqual(self.dll.read_bytes(), b"previous-dll")
        self.assertEqual(self.account.read_bytes(), b"account-secret")
        self.assertTrue((self.meta / "manual" / "latestmanualbackup" / "Telegram.exe").is_file())

    @contextmanager
    def hold_backup_rename(self, source_name, *, release_after=None, directory=False):
        """Hold a real Windows handle when the selected backup rename starts."""
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                      ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        move = program_recovery._move_write_through
        handles, timers, failures = [], [], []
        opened = False

        def close_handle():
            if handles:
                kernel.CloseHandle(handles.pop())

        def held_move(source, destination, **kwargs):
            nonlocal opened
            selected = source.name == source_name
            if selected and not opened:
                path = source if directory else source / 'Telegram.exe'
                handle = kernel.CreateFileW(str(path), 0x80000000, 3 if directory else 7,
                                            None, 3, 0x02000000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                handles.append(handle)
                opened = True
            try:
                return move(source, destination, **kwargs)
            except OSError as exc:
                if selected:
                    failures.append(exc.errno)
                    if release_after is not None and not timers:
                        timer = threading.Timer(release_after, close_handle)
                        timers.append(timer)
                        timer.start()
                raise

        try:
            with patch.object(program_recovery, '_move_write_through', held_move):
                yield failures
        finally:
            for timer in timers:
                timer.join()
            close_handle()

    def seed_manual_backup(self, name='latestmanualbackup'):
        latest = self.meta / 'manual' / name
        latest.mkdir(parents=True)
        (latest / 'Telegram.exe').write_bytes(b'older')
        (latest / 'core.dll').write_bytes(b'older-dll')
        return latest

    def test_backup_directory_renames_retry_until_child_handle_is_released(self):
        for source_name in ('latestmanualbackup.new', 'latestmanualbackup',
                            'latestmanualbackup.previous'):
            with self.subTest(source=source_name):
                seed = ('latestmanualbackup.previous' if source_name.endswith('.previous')
                        else 'latestmanualbackup')
                latest = self.meta / 'manual/latestmanualbackup'
                if latest.exists():
                    (latest / 'Telegram.exe').unlink()
                    (latest / 'core.dll').unlink()
                    latest.rmdir()
                self.seed_manual_backup(seed)
                self.exe.write_bytes(b'current')
                self.dll.write_bytes(b'current-dll')
                with self.hold_backup_rename(source_name, release_after=0.1) as failures:
                    restore_backup(self.install, self.backup.name,
                                   confirm_program_restore=self.backup.name, process_running=False)
                self.assertTrue(failures)
                self.assertEqual(set(failures), {5})
                self.assertEqual(self.exe.read_bytes(), b'previous')
                self.assertEqual(self.dll.read_bytes(), b'previous-dll')
                self.assertEqual((latest / 'Telegram.exe').read_bytes(), b'current')
                self.assertEqual((latest / 'core.dll').read_bytes(), b'current-dll')
                self.assertEqual((self.backup / 'Telegram.exe').read_bytes(), b'previous')
                self.assertEqual((self.backup / 'core.dll').read_bytes(), b'previous-dll')
                self.assertEqual(self.account.read_bytes(), b'account-secret')

    def test_backup_directory_sharing_violation_retries_until_release(self):
        latest = self.seed_manual_backup()
        with self.hold_backup_rename(latest.name, release_after=0.1, directory=True) as failures:
            restore_backup(self.install, self.backup.name,
                           confirm_program_restore=self.backup.name, process_running=False)
        self.assertTrue(failures)
        self.assertEqual(set(failures), {32})
        self.assertEqual(self.exe.read_bytes(), b'previous')
        self.assertEqual((latest / 'Telegram.exe').read_bytes(), b'current')
        self.assertEqual(self.account.read_bytes(), b'account-secret')

    def test_persistent_backup_occupation_refuses_and_preserves_program_and_backups(self):
        latest = self.seed_manual_backup()
        for source_name in ('latestmanualbackup.new', 'latestmanualbackup'):
            with self.subTest(source=source_name):
                started = time.monotonic()
                with self.hold_backup_rename(source_name) as failures:
                    with self.assertRaisesRegex(RecoveryError, 'latestmanualbackup'):
                        restore_backup(self.install, self.backup.name,
                                       confirm_program_restore=self.backup.name, process_running=False)
                elapsed = time.monotonic() - started
                self.assertGreaterEqual(elapsed, 0.9)
                self.assertLess(elapsed, 2.5)
                self.assertGreater(len(failures), 1)
                self.assertEqual(set(failures), {5})
                self.assertEqual(self.exe.read_bytes(), b'current')
                self.assertEqual(self.dll.read_bytes(), b'current-dll')
                self.assertEqual((latest / 'Telegram.exe').read_bytes(), b'older')
                self.assertEqual((latest / 'core.dll').read_bytes(), b'older-dll')
                self.assertEqual((self.backup / 'Telegram.exe').read_bytes(), b'previous')
                self.assertEqual((self.backup / 'core.dll').read_bytes(), b'previous-dll')
                self.assertEqual(self.account.read_bytes(), b'account-secret')
                self.assertFalse((self.meta / 'manual/pending').exists())

    def test_backup_rotation_rollback_retries_until_child_handle_is_released(self):
        latest = self.seed_manual_backup()
        with self.hold_backup_rename('latestmanualbackup.previous', release_after=0.1) as failures:
            move = program_recovery._move_write_through
            def fail_promotion(source, destination, **kwargs):
                if source.name == 'latestmanualbackup.new':
                    raise OSError(112, 'disk full')
                return move(source, destination, **kwargs)
            with patch.object(program_recovery, '_move_write_through', fail_promotion):
                with self.assertRaises(RecoveryError) as raised:
                    restore_backup(self.install, self.backup.name,
                                   confirm_program_restore=self.backup.name, process_running=False)
        self.assertEqual(raised.exception.__cause__.errno, 112)
        self.assertTrue(failures)
        self.assertEqual(set(failures), {5})
        self.assertEqual((latest / 'Telegram.exe').read_bytes(), b'older')
        self.assertEqual((latest / 'core.dll').read_bytes(), b'older-dll')
        self.assertEqual(self.exe.read_bytes(), b'current')
        self.assertEqual(self.dll.read_bytes(), b'current-dll')
        self.assertEqual(self.account.read_bytes(), b'account-secret')

    def test_backup_rename_other_errors_are_not_retried(self):
        latest = self.seed_manual_backup()
        move = program_recovery._move_write_through
        for error in (2, 17, 112):
            with self.subTest(errno=error):
                attempts = []
                def fail_promotion(source, destination, **kwargs):
                    if source.name == 'latestmanualbackup.new':
                        attempts.append(source)
                        raise OSError(error, 'rename failed')
                    return move(source, destination, **kwargs)
                with patch.object(program_recovery, '_move_write_through', fail_promotion):
                    with self.assertRaises(RecoveryError) as raised:
                        restore_backup(self.install, self.backup.name,
                                       confirm_program_restore=self.backup.name, process_running=False)
                self.assertEqual(raised.exception.__cause__.errno, error)
                self.assertEqual(len(attempts), 1)
                self.assertEqual((latest / 'Telegram.exe').read_bytes(), b'older')
                self.assertEqual(self.exe.read_bytes(), b'current')
                self.assertEqual(self.account.read_bytes(), b'account-secret')

    def test_shared_client_session_lease_blocks_then_allows_program_restore(self):
        with _ClientSessionLease(self.install, exclusive=False):
            with self.assertRaisesRegex(RecoveryError, "client session lease busy"):
                restore_backup(self.install, self.backup.name,
                               confirm_program_restore=self.backup.name, process_running=False)
            with self.assertRaisesRegex(RecoveryError, "client session lease busy"):
                _ClientSessionLease(self.install, exclusive=True)
            self.assertEqual(self.exe.read_bytes(), b"current")
        restore_backup(self.install, self.backup.name,
                       confirm_program_restore=self.backup.name, process_running=False)
        self.assertEqual(self.exe.read_bytes(), b"previous")

    def test_install_directory_reparse_is_rejected(self):
        alias = self.install.parent / "install-link"
        alias.symlink_to(self.install, target_is_directory=True)
        with self.assertRaises(RecoveryError):
            restore_backup(alias, self.backup.name,
                           confirm_program_restore=self.backup.name, process_running=False)

    def test_client_session_lock_reparse_is_rejected(self):
        link = self.install / "client-session.lock"
        link.symlink_to(self.exe)
        with self.assertRaises(RecoveryError):
            _ClientSessionLease(self.install, exclusive=True)

    def test_gate_allows_child_creation_and_write(self):
        renamed = self.install.with_name(self.install.name + "-renamed")
        with _directory_guard(self.install):
            child = self.install / "guard-child"
            child.mkdir()
            payload = child / "probe.bin"
            payload.write_bytes(b"directory guard write")
            self.assertEqual(payload.read_bytes(), b"directory guard write")
            with self.assertRaises(OSError):
                self.install.rename(renamed)

    def test_failed_copy_restores_original_program(self):
        def fail_after_one(_source, destination):
            if destination.name.lower() == "telegram.exe.restore":
                raise OSError("simulated copy failure")
        with self.assertRaises(RecoveryError):
            restore_backup(self.install, self.backup.name,
                           confirm_program_restore=self.backup.name, copy_hook=fail_after_one)
        self.assertEqual(self.exe.read_bytes(), b"current")
        self.assertEqual(self.dll.read_bytes(), b"current-dll")

    def test_new_dll_rename_failure_cleans_its_partial_stage_and_allows_retry(self):
        (self.backup / 'new.dll').write_bytes(b'new-backup-dll')
        move = program_recovery._move_write_through
        def fail_new_stage(source, destination, **kwargs):
            if source == self.install / 'new.dll.restore':
                raise OSError('new DLL rename interrupted')
            return move(source, destination, **kwargs)
        with patch.object(program_recovery, '_move_write_through', fail_new_stage):
            with self.assertRaisesRegex(RecoveryError, '已从持久事务恢复'):
                restore_backup(self.install, self.backup.name,
                               confirm_program_restore=self.backup.name, process_running=False)
        self.assertEqual(self.exe.read_bytes(), b'current')
        self.assertEqual(self.dll.read_bytes(), b'current-dll')
        self.assertEqual(self.account.read_bytes(), b'account-secret')
        self.assertFalse((self.install / 'new.dll.restore').exists())
        self.assertFalse((self.install / 'new.dll').exists())
        restore_backup(self.install, self.backup.name,
                       confirm_program_restore=self.backup.name, process_running=False)
        self.assertEqual((self.install / 'new.dll').read_bytes(), b'new-backup-dll')

    def test_existing_target_stage_is_preserved_before_transaction_starts(self):
        (self.backup / 'new.dll').write_bytes(b'new-backup-dll')
        staged = self.install / 'new.dll.restore'
        staged.write_bytes(b'unrelated-stage')
        with self.assertRaises(RecoveryError):
            restore_backup(self.install, self.backup.name,
                           confirm_program_restore=self.backup.name, process_running=False)
        self.assertEqual(staged.read_bytes(), b'unrelated-stage')
        self.assertEqual(self.exe.read_bytes(), b'current')
        self.assertFalse((self.meta / 'manual/pending').exists())

    def test_new_dll_partial_copy_failure_cleans_only_transaction_stage(self):
        (self.backup / 'new.dll').write_bytes(b'backup-dll')
        unrelated = self.install / 'unrelated.dll.restore'
        unrelated.write_bytes(b'keep-external-stage')
        def interrupted_copy(source, destination):
            if destination == self.install / 'new.dll.restore':
                destination.write_bytes(b'partial')
                raise OSError('interrupted partial copy')
        with self.assertRaisesRegex(RecoveryError, '已从持久事务恢复'):
            restore_backup(self.install, self.backup.name,
                           confirm_program_restore=self.backup.name,
                           copy_hook=interrupted_copy, process_running=False)
        self.assertFalse((self.install / 'new.dll.restore').exists())
        self.assertEqual(unrelated.read_bytes(), b'keep-external-stage')
        self.assertEqual(self.exe.read_bytes(), b'current')
        self.assertEqual(self.dll.read_bytes(), b'current-dll')

    def test_first_journal_rename_failure_recovers_validated_initialization(self):
        move = program_recovery._move_write_through
        calls = []
        def fail_first_journal(source, destination, **kwargs):
            if source.name == 'transaction.json.new' and not calls:
                calls.append(source)
                raise OSError('first journal rename interrupted')
            return move(source, destination, **kwargs)
        with patch.object(program_recovery, '_move_write_through', fail_first_journal):
            with self.assertRaisesRegex(RecoveryError, '已从持久事务恢复'):
                restore_backup(self.install, self.backup.name,
                               confirm_program_restore=self.backup.name, process_running=False)
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.meta / 'manual/pending').exists())
        self.assertEqual(self.exe.read_bytes(), b'current')
        restore_backup(self.install, self.backup.name,
                       confirm_program_restore=self.backup.name, process_running=False)
        self.assertEqual(self.exe.read_bytes(), b'previous')

    def test_unknown_initial_journal_or_contents_are_preserved(self):
        pending = self.meta / 'manual/pending'
        original = pending / 'original'
        original.mkdir(parents=True)
        staged = pending / 'transaction.json.new'
        state = {'backup_id': self.backup.name, 'phase': 'preparing',
                 'files': ['Telegram.exe', 'core.dll'],
                 'target_files': ['Telegram.exe', 'core.dll']}
        for change in ('broken', 'applying', 'path', 'extra', 'nonempty',
                       'unknown_backup', 'current_inventory', 'target_inventory'):
            with self.subTest(change=change):
                value = dict(state)
                if change == 'applying': value['phase'] = 'applying'
                if change == 'path': value['target_files'] = ['../outside.dll']
                if change == 'unknown_backup': value['backup_id'] = 'abcd'
                if change == 'current_inventory': value['files'] = ['Telegram.exe']
                if change == 'target_inventory': value['target_files'] = ['Telegram.exe']
                staged.write_text('{' if change == 'broken' else json.dumps(value))
                extra = pending / 'unknown.bin'
                saved = original / 'Telegram.exe'
                if change == 'extra': extra.write_bytes(b'unknown')
                if change == 'nonempty': saved.write_bytes(b'saved')
                before = staged.read_bytes()
                with self.assertRaises((RecoveryError, ValueError)):
                    program_recovery._recover_pending(self.install, pending.parent)
                self.assertEqual(staged.read_bytes(), before)
                self.assertTrue(original.is_dir())
                self.assertEqual(self.exe.read_bytes(), b'current')
                extra.unlink(missing_ok=True)
                saved.unlink(missing_ok=True)

    def test_rollback_reparse_new_stage_preserves_external_and_pending(self):
        pending = self.meta / 'manual/pending'
        originals = pending / 'original'
        originals.mkdir(parents=True)
        for name in ('Telegram.exe', 'core.dll'):
            (originals / name).write_bytes((self.install / name).read_bytes())
        (pending / 'transaction.json').write_text(json.dumps({
            'backup_id': self.backup.name, 'phase': 'applying',
            'files': ['Telegram.exe', 'core.dll'],
            'target_files': ['Telegram.exe', 'core.dll', 'new.dll']}))
        outside = self.install.parent / 'outside.dll'
        outside.write_bytes(b'external')
        link = self.install / 'new.dll.restore'
        link.symlink_to(outside)
        with self.assertRaises(RecoveryError):
            program_recovery._recover_pending(self.install, pending.parent)
        self.assertEqual(outside.read_bytes(), b'external')
        self.assertTrue(link.is_symlink())
        self.assertTrue(pending.exists())

    def test_preparing_journal_does_not_authorize_unknown_pending_contents(self):
        pending = self.meta / 'manual/pending'
        original = pending / 'original'
        original.mkdir(parents=True)
        (pending / 'transaction.json').write_text(json.dumps({
            'backup_id': self.backup.name, 'phase': 'preparing',
            'files': ['Telegram.exe', 'core.dll'],
            'target_files': ['Telegram.exe', 'core.dll']}))
        unknown = original / 'unknown.dll'
        unknown.write_bytes(b'not-declared')
        with self.assertRaises(RecoveryError):
            program_recovery._recover_pending(self.install, pending.parent)
        self.assertEqual(unknown.read_bytes(), b'not-declared')

    def test_empty_initialization_without_journal_can_retry(self):
        for with_original in (False, True):
            pending = self.meta / "manual" / "pending"
            pending.mkdir(parents=True)
            if with_original:
                (pending / "original").mkdir()
            program_recovery._recover_pending(self.install, pending.parent)
            self.assertFalse(pending.exists())
            self.assertEqual(self.exe.read_bytes(), b"current")
            self.assertEqual(self.dll.read_bytes(), b"current-dll")

    def test_nonempty_initialization_without_journal_preserves_unknown_files(self):
        pending = self.meta / "manual" / "pending"
        original = pending / "original"
        original.mkdir(parents=True)
        saved = original / "Telegram.exe"
        saved.write_bytes(b"unclassified")
        with self.assertRaises(RecoveryError):
            program_recovery._recover_pending(self.install, pending.parent)
        self.assertEqual(saved.read_bytes(), b"unclassified")

    def test_original_directory_creation_failure_cleans_empty_pending_and_allows_retry(self):
        original_mkdir = Path.mkdir
        def fail_original(path, *args, **kwargs):
            if path == self.meta / "manual" / "pending" / "original":
                raise OSError("fixture initialization failure")
            return original_mkdir(path, *args, **kwargs)
        with patch.object(Path, "mkdir", fail_original):
            with self.assertRaises(RecoveryError):
                restore_backup(self.install, self.backup.name,
                    confirm_program_restore=self.backup.name, process_running=False)
        self.assertFalse((self.meta / "manual" / "pending").exists())
        self.assertEqual(self.exe.read_bytes(), b"current")
        restore_backup(self.install, self.backup.name,
                       confirm_program_restore=self.backup.name, process_running=False)
        self.assertEqual(self.exe.read_bytes(), b"previous")

    def test_failed_restore_with_case_changed_dll_preserves_original_file(self):
        renamed = self.install / "Core.dll"
        self.dll.rename(renamed)
        def fail_after_dll(_source, destination):
            if destination.name.lower() == "telegram.exe.restore":
                raise OSError("simulated later copy failure")
        with self.assertRaises(RecoveryError):
            restore_backup(self.install, self.backup.name,
                           confirm_program_restore=self.backup.name, copy_hook=fail_after_dll,
                           process_running=False)
        self.assertEqual(self.exe.read_bytes(), b"current")
        self.assertEqual(renamed.read_bytes(), b"current-dll")
        self.assertEqual(self.account.read_bytes(), b"account-secret")
        self.assertFalse((self.meta / "manual" / "pending").exists())

    def test_install_lock_is_exclusive_like_production(self):
        lock_path = self.meta / "install.lock"
        with _InstallLock(lock_path):
            with self.assertRaises(RecoveryError):
                _InstallLock(lock_path)

    def test_insufficient_space_refuses_before_changes(self):
        with self.assertRaises(RecoveryError):
            restore_backup(self.install, self.backup.name,
                           confirm_program_restore=self.backup.name, free_space_override=0)
        self.assertEqual(self.exe.read_bytes(), b"current")

    def test_running_program_refuses_restore(self):
        with self.assertRaises(RecoveryError):
            restore_backup(self.install, self.backup.name,
                           confirm_program_restore=self.backup.name, process_running=True)
        self.assertEqual(self.exe.read_bytes(), b"current")

    def test_path_traversal_backup_id_is_rejected(self):
        with self.assertRaises(RecoveryError):
            restore_backup(self.install, "../outside", confirm_program_restore="../outside")

    def test_reparse_point_archive_is_rejected(self):
        link = self.versions / "aaaaaaaaaaaaaaaa-00000001-aaaaaaaaaaaaaaaa"
        link.symlink_to(self.backup, target_is_directory=True)
        with self.assertRaises(RecoveryError):
            list_backups(self.install)

    def test_archive_account_file_is_rejected(self):
        (self.backup / "tdata").mkdir()
        (self.backup / "tdata" / "key_data").write_bytes(b"secret")
        with self.assertRaises(RecoveryError):
            list_backups(self.install)


class ProgramRecoveryAclTests(unittest.TestCase):
    def test_child_only_untrusted_write_is_rejected_but_read_is_allowed(self):
        with tempfile.TemporaryDirectory(prefix="fishgram-acl-fixture-") as temp:
            for mask, rejected in (("GW", True), ("GR", False)):
                directory = Path(temp) / mask
                directory.mkdir()
                command = """
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$acl=[Security.AccessControl.DirectorySecurity]::new()
$acl.SetSecurityDescriptorSddlForm("O:${sid}G:SYD:P(A;OICI;FA;;;${sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OIIO;$env:FISHGRAM_FIXTURE_MASK;;;WD)")
Set-Acl -LiteralPath $env:FISHGRAM_FIXTURE_DIRECTORY -AclObject $acl -ErrorAction Stop
"""
                subprocess.run(["pwsh", "-NoProfile", "-Command", command],
                    env={**os.environ, "FISHGRAM_FIXTURE_MASK": mask,
                         "FISHGRAM_FIXTURE_DIRECTORY": str(directory)},
                    capture_output=True, check=True)
                if rejected:
                    with self.assertRaisesRegex(RecoveryError, "ACL"):
                        program_recovery._trusted_acl(directory)
                else:
                    program_recovery._trusted_acl(directory)


def make_journal(backup_id, previous=("Telegram.exe", "core.dll")):
    data = bytearray(b"FGTXN01\0")
    data += struct.pack("<IIQI", 1, 2, 7002009008, 0)
    def text(value):
        encoded = value.encode("ascii")
        return struct.pack("<I", len(encoded)) + encoded
    data += text(backup_id)
    for names in (("Telegram.exe", "core.dll"), previous):
        data += struct.pack("<I", len(names))
        for name in names: data += text(name)
    return data + b"CMIT"


if __name__ == "__main__":
    unittest.main()
