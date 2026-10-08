import tempfile
import unittest
from pathlib import Path
import sys
import struct
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import program_recovery
from program_recovery import RecoveryError, _ClientSessionLease, _InstallLock, _directory_guard, list_backups, restore_backup


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


def make_journal(backup_id):
    data = bytearray(b"FGTXN01\0")
    data += struct.pack("<IIQI", 1, 2, 7002009008, 0)
    def text(value):
        encoded = value.encode("ascii")
        return struct.pack("<I", len(encoded)) + encoded
    data += text(backup_id)
    for names in (("Telegram.exe", "core.dll"), ("Telegram.exe", "core.dll")):
        data += struct.pack("<I", len(names))
        for name in names: data += text(name)
    return data + b"CMIT"


if __name__ == "__main__":
    unittest.main()
