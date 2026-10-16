import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import ctypes
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location('data_recovery', Path(__file__).resolve().parents[1] / 'tools/data_recovery.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class DirectoryHold:
    """Real OS no-share-delete handle; independent of the production gate."""
    def __init__(self, path):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        self.kernel.CreateFileW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.kernel.CreateFileW(str(path), 0x1 | 0x80 | 0x20000,
                                               0x1 | 0x2, None, 3, 0x02000000 | 0x00200000, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class DataRecoveryTests(unittest.TestCase):
    def test_wrong_client_target_is_rejected_before_probe_or_data_changes(self):
        import unittest.mock as mock
        before = recovery._inventory(self.work / 'tdata')
        wrong_dir = self.root / 'wrong-install'
        wrong_dir.mkdir()
        targets = (self.root / 'Updater.exe', self.root / 'ordinary.txt',
                   wrong_dir / 'Telegram.exe')
        for executable in targets:
            executable.write_bytes(b'synthetic wrong target')
            probe = mock.Mock(return_value=False)
            with self.subTest(executable=executable.name):
                for operation in (
                    lambda: recovery.snapshot(self.work, self.snapshots, executable, '7.2.9-r8',
                        installation_dir=self.root, process_probe=probe),
                    lambda: recovery.restore_snapshot(self.work, self.snapshots, executable, 'snapshot-invalid',
                        installation_dir=self.root, confirm_restore=True, process_probe=probe),
                    lambda: recovery.recover_restore(self.work, executable,
                        installation_dir=self.root, confirm_restore=True, process_probe=probe),
                ):
                    with self.assertRaises(recovery.RecoveryError):
                        operation()
                probe.assert_not_called()
                self.assertEqual(recovery._inventory(self.work / 'tdata'), before)
                self.assertFalse(self.snapshots.exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fishgram-data-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
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

    def test_snapshot_marker_interrupted_write_is_not_published_and_can_resume(self):
        real_open = Path.open
        expected = b'FishGram snapshots v1\n'

        class InterruptedWrite:
            def __init__(self, output):
                self.output = output
            def __enter__(self):
                self.output.__enter__()
                return self
            def __exit__(self, *args):
                return self.output.__exit__(*args)
            def write(self, content):
                self.output.write(content[:8])
                self.output.flush()
                raise KeyboardInterrupt('synthetic interrupted marker write')
            def __getattr__(self, name):
                return getattr(self.output, name)

        def interrupt(path, mode='r', *args, **kwargs):
            output = real_open(path, mode, *args, **kwargs)
            if path.parent == self.snapshots and ('x' in mode or 'w' in mode):
                return InterruptedWrite(output)
            return output

        with mock.patch.object(Path, 'open', new=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                recovery._snapshot_root(self.snapshots)
        self.assertFalse((self.snapshots / recovery.MARKER).exists())
        recovery._snapshot_root(self.snapshots)
        self.assertEqual((self.snapshots / recovery.MARKER).read_bytes(), expected)

    def test_snapshot_marker_fsync_interruption_keeps_final_marker_absent(self):
        with mock.patch.object(recovery.os, 'fsync', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                recovery._snapshot_root(self.snapshots)
        self.assertFalse((self.snapshots / recovery.MARKER).exists())
        recovery._snapshot_root(self.snapshots)
        self.assertEqual((self.snapshots / recovery.MARKER).read_bytes(), b'FishGram snapshots v1\n')

    def test_snapshot_marker_unknown_or_damaged_initialization_is_preserved(self):
        for name, content in ((recovery.MARKER, b''), (recovery.MARKER, b'FishGram'),
                              ('.partial-' + 'a' * 32, b'user data'),
                              ('notes.txt', b'user data')):
            with self.subTest(name=name, content=content):
                root = self.root / ('unknown-' + str(len(list(self.root.iterdir()))))
                root.mkdir()
                recovery._private_acl(root)
                entry = root / name
                entry.write_bytes(content)
                recovery._private_acl(entry)
                with self.assertRaises(recovery.RecoveryError):
                    recovery._snapshot_root(root)
                self.assertEqual(entry.read_bytes(), content)
                self.assertEqual({path.name for path in root.iterdir()}, {name})

    def test_snapshot_marker_publication_flushes_and_never_overwrites_racing_target(self):
        real_publish = recovery._publish_snapshot_marker
        real_fsync = recovery.os.fsync
        synced = []

        def fsync(fd):
            real_fsync(fd)
            synced.append(True)

        def race(temporary, marker):
            self.assertTrue(synced)
            self.assertEqual(temporary.parent, marker.parent)
            self.assertEqual(temporary.read_bytes(), b'FishGram snapshots v1\n')
            marker.write_bytes(b'unknown racing contents')
            return real_publish(temporary, marker)

        with mock.patch.object(recovery.os, 'fsync', side_effect=fsync), \
                mock.patch.object(recovery, '_publish_snapshot_marker', side_effect=race):
            with self.assertRaisesRegex(recovery.RecoveryError, 'unrelated'):
                recovery._snapshot_root(self.snapshots)
        self.assertEqual((self.snapshots / recovery.MARKER).read_bytes(), b'unknown racing contents')

    def test_snapshot_marker_valid_racing_publication_is_accepted_without_replacement(self):
        real_publish = recovery._publish_snapshot_marker
        winner = []

        def race(temporary, marker):
            marker.write_bytes(b'FishGram snapshots v1\n')
            recovery._private_acl(marker)
            winner.append(marker.stat().st_ino)
            return real_publish(temporary, marker)

        with mock.patch.object(recovery, '_publish_snapshot_marker', side_effect=race):
            recovery._snapshot_root(self.snapshots)
        marker = self.snapshots / recovery.MARKER
        self.assertEqual(marker.stat().st_ino, winner[0])
        self.assertEqual(marker.read_bytes(), b'FishGram snapshots v1\n')

    def test_snapshot_marker_success_cleans_only_this_initialization_stage(self):
        recovery._snapshot_root(self.snapshots)
        self.assertEqual({path.name for path in self.snapshots.iterdir()}, {recovery.MARKER})
        self.assertEqual((self.snapshots / recovery.MARKER).read_bytes(), b'FishGram snapshots v1\n')

    def test_snapshot_marker_owned_stage_replacement_is_never_deleted(self):
        real_cleanup = recovery._remove_owned_marker_stage
        replacement = []

        def replace_before_cleanup(path, identity):
            path.rename(self.root / 'saved-owned-stage')
            path.write_bytes(b'user data')
            replacement.append(path)
            return real_cleanup(path, identity)

        with mock.patch.object(recovery, '_remove_owned_marker_stage', side_effect=replace_before_cleanup):
            with self.assertRaisesRegex(recovery.RecoveryError, 'identity'):
                recovery._snapshot_root(self.snapshots)
        self.assertEqual(replacement[0].read_bytes(), b'user data')
        self.assertEqual((self.snapshots / recovery.MARKER).read_bytes(), b'FishGram snapshots v1\n')

    def test_snapshot_marker_concurrent_initializers_remain_recoverable(self):
        self.snapshots.mkdir()
        recovery._private_acl(self.snapshots)
        barrier = threading.Barrier(2)
        errors = []

        def initialize():
            try:
                barrier.wait(timeout=5)
                recovery._snapshot_root(self.snapshots)
            except recovery.RecoveryError:
                pass  # A busy initializer may fail closed; a later retry must work.
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=initialize) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        recovery._snapshot_root(self.snapshots)
        self.assertEqual((self.snapshots / recovery.MARKER).read_bytes(), b'FishGram snapshots v1\n')

    def test_snapshot_marker_initialization_reparse_is_rejected_without_following(self):
        self.snapshots.mkdir()
        recovery._private_acl(self.snapshots)
        outside = self.root / 'outside-init'
        outside.mkdir()
        sentinel = outside / 'keep.txt'
        sentinel.write_bytes(b'user data')
        link = self.snapshots / ('.partial-' + 'a' * 32)
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('This Windows token cannot create symlinks')
        with self.assertRaisesRegex(recovery.RecoveryError, 'link|reparse'):
            recovery._snapshot_root(self.snapshots)
        self.assertEqual(sentinel.read_bytes(), b'user data')
        self.assertTrue(link.is_symlink())

    @unittest.skipUnless(os.name == 'nt', 'Real Windows directory sharing')
    def test_snapshot_marker_root_guard_blocks_real_directory_rename(self):
        self.snapshots.mkdir()
        recovery._private_acl(self.snapshots)
        moved = self.root / 'moved-snapshot-root'
        with recovery._snapshot_root_guard(self.snapshots):
            with self.assertRaises(OSError) as blocked:
                os.rename(self.snapshots, moved)
            self.assertIn(blocked.exception.winerror, (5, 32, 33))
            self.assertTrue(self.snapshots.is_dir())
            self.assertFalse(moved.exists())
        os.rename(self.snapshots, moved)
        self.assertTrue(moved.is_dir())

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

    @unittest.skipUnless(os.name == 'nt', 'Production client-session gate is Windows-only')
    def test_snapshot_restore_and_recovery_hold_exclusive_client_gate(self):
        observed = []

        def probe(executable):
            try:
                with recovery._client_session_gate(executable.parent, exclusive=False):
                    observed.append(False)
            except recovery.RecoveryError:
                observed.append(True)
            return False

        name = recovery.snapshot(self.work, self.snapshots, self.exe, '7.2.9-r8', process_probe=probe)
        self.assertEqual(observed, [True, True])
        observed.clear()
        recovery.restore_snapshot(self.work, self.snapshots, self.exe, name,
                                  confirm_restore=True, process_probe=probe)
        self.assertEqual(observed, [True, True])
        observed.clear()
        recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=probe)
        self.assertEqual(observed, [True])

    @unittest.skipUnless(os.name == 'nt', 'Production client-session gate is Windows-only')
    def test_shared_client_lease_blocks_snapshot_before_account_reads_or_writes(self):
        before = recovery._inventory(self.work / 'tdata')
        with recovery._client_session_gate(self.exe.parent, exclusive=False):
            with self.assertRaisesRegex(recovery.RecoveryError, 'busy|lease|客户端|gate'):
                self.snapshot()
        self.assertEqual(recovery._inventory(self.work / 'tdata'), before)
        self.assertFalse(self.snapshots.exists())

    @unittest.skipUnless(os.name == 'nt' and os.environ.get('FISHGRAM_NATIVE_SNAPSHOT_TEST'),
                         'Native snapshot executable is configured by the focused Windows test runner')
    def test_reads_native_snapshot_manifest_and_content(self):
        executable = Path(os.environ['FISHGRAM_NATIVE_SNAPSHOT_TEST']).resolve()
        fixture = subprocess.run(
            [str(executable), '--emit-python-fixture', str(self.root)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(fixture.returncode, 0, fixture.stderr)
        generated = json.loads(fixture.stdout)
        work = Path(generated['workDir'])
        snapshot_root = Path(generated['snapshotRoot'])
        folder, manifest = recovery._read_snapshot(snapshot_root, generated['snapshotName'])
        self.assertEqual(manifest['version'], '7.2.9-r9')
        relative = 'account/map0'
        self.assertIn(relative, manifest['files'])
        self.assertEqual((folder / 'tdata' / relative).read_bytes(), b'native synthetic account data')
        self.assertEqual(manifest['files'][relative]['size'], len(b'native synthetic account data'))
        self.assertEqual(manifest['files']['account/empty']['size'], 0)
        self.assertEqual(manifest['files']['account/empty']['sha256'], recovery.hashlib.sha256(b'').hexdigest())
        self.assertEqual(manifest['files']['empty-directory'], {'directory': True})
        self.assertTrue((folder / 'tdata' / 'empty-directory').is_dir())
        self.assertFalse((work / recovery.JOURNAL).exists())
        # Native root is a sibling of workDir and must match the documented path hash.
        import hashlib
        expected = '.fishgram-snapshots-' + hashlib.sha256(
            str(work.resolve()).replace('\\', '/').encode('utf-8')).hexdigest()[:16]
        self.assertEqual(snapshot_root.name, expected)
        recovery.restore_snapshot(work, snapshot_root, Path(generated['executable']),
                                  generated['snapshotName'], confirm_restore=True,
                                  process_probe=lambda path: False)
        self.assertEqual((work / 'tdata' / relative).read_bytes(), b'native synthetic account data')
        self.assertTrue((work / 'tdata' / 'empty-directory').is_dir())

    def test_private_acl_validation_checks_each_ace_field_and_rejects_extras(self):
        expected = ('S-1-5-21-100', 'S-1-5-18', 'S-1-5-32-544')
        valid = [(sid, 0, 0x03, recovery.FILE_ALL_ACCESS) for sid in expected]
        recovery._validate_private_acl_entries(valid, expected, directory=True,
                                               owner_sid=expected[0], protected=True)
        for replacement in [
            ('S-1-5-21-999', 0, 0x03, recovery.FILE_ALL_ACCESS),
            (expected[0], 1, 0x03, recovery.FILE_ALL_ACCESS),
            (expected[0], 0, 0x03, 0x00120089),
            (expected[0], 0, 0, recovery.FILE_ALL_ACCESS),
        ]:
            with self.subTest(replacement=replacement), self.assertRaises(recovery.RecoveryError):
                recovery._validate_private_acl_entries([replacement, *valid[1:]], expected, directory=True,
                                                       owner_sid=expected[0], protected=True)
        with self.assertRaisesRegex(recovery.RecoveryError, 'permissions'):
            recovery._validate_private_acl_entries([*valid, valid[0]], expected, directory=True,
                                                   owner_sid=expected[0], protected=True)
        file_entries = [(sid, 0, 0, recovery.FILE_ALL_ACCESS) for sid in expected]
        recovery._validate_private_acl_entries(file_entries, expected, directory=False,
                                               owner_sid=expected[0])
        with self.assertRaises(recovery.RecoveryError):
            recovery._validate_private_acl_entries(valid, expected, directory=False,
                                                   owner_sid=expected[0])

    def test_private_acl_accepts_native_elevated_read_only_user_and_trusted_owner(self):
        user, system, admins = ('S-1-5-21-100', 'S-1-5-18', 'S-1-5-32-544')
        native_elevated = [
            (user, 0, 0x03, recovery.FILE_GENERIC_READ_EXECUTE),
            (system, 0, 0x03, recovery.FILE_ALL_ACCESS),
            (admins, 0, 0x03, recovery.FILE_ALL_ACCESS),
        ]
        recovery._validate_private_acl_entries(native_elevated, (user, system, admins),
                                               directory=True, owner_sid=admins, protected=True,
                                               user_writable=False)
        inherited = [(sid, kind, flags | 0x10, mask) for sid, kind, flags, mask in native_elevated]
        recovery._validate_private_acl_entries(inherited, (user, system, admins),
                                               directory=True, owner_sid=system, protected=False,
                                               user_writable=False)
        file_entries = [(sid, 0, 0x10, mask) for sid, _, _, mask in native_elevated]
        recovery._validate_private_acl_entries(file_entries, (user, system, admins),
                                               directory=False, owner_sid=admins, protected=False,
                                               user_writable=False)

    def test_private_acl_rejects_untrusted_owner_writer_and_malformed_native_acl(self):
        user, system, admins = ('S-1-5-21-100', 'S-1-5-18', 'S-1-5-32-544')
        native_elevated = [
            (user, 0, 0x03, recovery.FILE_GENERIC_READ_EXECUTE),
            (system, 0, 0x03, recovery.FILE_ALL_ACCESS),
            (admins, 0, 0x03, recovery.FILE_ALL_ACCESS),
        ]
        invalid = [
            ([(user, 0, 0x03, recovery.FILE_ALL_ACCESS), *native_elevated[1:]], admins, True),
            ([*native_elevated, ('S-1-1-0', 0, 0x03, recovery.FILE_ALL_ACCESS)], admins, True),
            ([('S-1-1-0', 0, 0x03, recovery.FILE_ALL_ACCESS), *native_elevated[1:]], admins, True),
            ([ (sid, 1, flags, mask) if sid == user else (sid, 0, flags, mask)
               for sid, _, flags, mask in native_elevated], admins, True),
            ([ (sid, 0, flags, mask | 0x2) if sid == user else (sid, 0, flags, mask)
               for sid, _, flags, mask in native_elevated], admins, True),
            (native_elevated, user, True),
            (native_elevated, 'S-1-1-0', True),
            (native_elevated, admins, False),
        ]
        for entries, owner, protected in invalid:
            with self.subTest(owner=owner, protected=protected, entries=entries), \
                    self.assertRaises(recovery.RecoveryError):
                recovery._validate_private_acl_entries(entries, (user, system, admins),
                                                       directory=True, owner_sid=owner,
                                                       protected=protected, user_writable=False,
                                                       require_protected=not protected)

    @unittest.skipUnless(os.name == 'nt', 'Windows read-only lock handle semantics')
    def test_empty_read_only_lock_file_locks_and_excludes_second_process(self):
        from ctypes import wintypes
        path = self.root / '.snapshot.lock'
        path.touch()
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.LocalFree.argtypes = [wintypes.HLOCAL]
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
        advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID]
        advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
        token = wintypes.HANDLE()
        self.assertTrue(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)))
        try:
            needed = wintypes.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            data = ctypes.create_string_buffer(needed.value)
            self.assertTrue(advapi.GetTokenInformation(token, 1, data, len(data), ctypes.byref(needed)))
            sid = ctypes.cast(data, ctypes.POINTER(wintypes.LPVOID))[0]
            sid_text = wintypes.LPWSTR()
            self.assertTrue(advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)))
            try:
                descriptor = wintypes.LPVOID()
                # Deny data writes even if this test process has an enabled
                # admin group. This is a lock-access fixture, not a trusted snapshot.
                sddl = (f'D:P(D;;0x00000002;;;{sid_text.value})'
                        f'(A;;0x001200A9;;;{sid_text.value})(A;;FA;;;SY)(A;;FA;;;BA)')
                self.assertTrue(advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl, 1, ctypes.byref(descriptor), None))
                try:
                    self.assertTrue(advapi.SetFileSecurityW(
                        str(path), 4 | 0x80000000, descriptor))
                finally:
                    kernel.LocalFree(descriptor)
            finally:
                kernel.LocalFree(sid_text)
        finally:
            kernel.CloseHandle(token)

        self.addCleanup(recovery._private_acl, path)
        with self.assertRaises(PermissionError):
            with path.open('r+b'):
                pass
        with path.open('rb') as file:
            self.assertFalse(file.writable())
            self.assertEqual(file.read(), b'')
        self.assertEqual(path.stat().st_size, 0)
        helper = '''import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('dr', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    with module._lock(Path(sys.argv[2]).parent, '.snapshot.lock'):
        print('ACQUIRED')
except module.RecoveryError:
    print('BLOCKED')
'''
        command = [sys.executable, '-c', helper, str(Path(recovery.__file__)), str(path)]
        native_helper = '''import ctypes, msvcrt, sys
from ctypes import wintypes
class Overlapped(ctypes.Structure):
    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD),
                ('hEvent', wintypes.HANDLE)]
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                             wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
with open(sys.argv[1], 'rb') as file:
    handle = msvcrt.get_osfhandle(file.fileno())
    if kernel.LockFileEx(handle, 3, 0, 1, 0, ctypes.byref(Overlapped())):
        print('ACQUIRED')
    else:
        print('BLOCKED:' + str(ctypes.get_last_error()))
'''
        native_command = [sys.executable, '-c', native_helper, str(path)]
        with recovery._lock(self.root, path.name):
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'BLOCKED')
            result = subprocess.run(native_command, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'BLOCKED:33')
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ACQUIRED')
        result = subprocess.run(native_command, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ACQUIRED')
        self.assertEqual(path.stat().st_size, 0)

    @unittest.skipUnless(os.name == 'nt', 'Windows security descriptor generation')
    def test_private_acl_elevated_descriptor_explicitly_requests_admin_owner(self):
        from ctypes import wintypes
        real_advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        real_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        real_advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        captured = []

        def token_information(token, info, output, size, required):
            if info == 20:
                ctypes.cast(output, ctypes.POINTER(wintypes.DWORD))[0] = 1
                ctypes.cast(required, ctypes.POINTER(wintypes.DWORD))[0] = ctypes.sizeof(wintypes.DWORD)
                return True
            return real_advapi.GetTokenInformation(token, info, output, size, required)

        def set_security(path, fields, descriptor):
            owner, defaulted = wintypes.LPVOID(), wintypes.BOOL()
            self.assertTrue(real_advapi.GetSecurityDescriptorOwner(
                descriptor, ctypes.byref(owner), ctypes.byref(defaulted)))
            owner_text = wintypes.LPWSTR()
            if owner.value:
                self.assertTrue(real_advapi.ConvertSidToStringSidW(owner, ctypes.byref(owner_text)))
            try:
                captured.append((fields, owner_text.value))
            finally:
                if owner_text:
                    real_kernel.LocalFree(owner_text)
            return False  # Inspect the real descriptor without a privileged filesystem mutation.

        class AdvapiProxy:
            def __getattr__(self, name):
                return getattr(real_advapi, name)
        proxy = AdvapiProxy()
        proxy.GetTokenInformation = token_information
        proxy.SetFileSecurityW = set_security

        with mock.patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kwargs:
                               proxy if name == 'advapi32' else real_kernel):
            with self.assertRaisesRegex(recovery.RecoveryError, 'restrict'):
                recovery._private_acl(self.root)
        self.assertEqual(captured, [(1 | 4 | 0x80000000, 'S-1-5-32-544')])

    def test_oversized_manifest_does_not_publish_or_prune_snapshots(self):
        previous = self.snapshot()
        with mock.patch.object(recovery, 'MAX_MANIFEST', 128):
            with self.assertRaisesRegex(recovery.RecoveryError, 'size limit'):
                self.snapshot()
        self.assertTrue((self.snapshots / previous / 'manifest.json').is_file())
        published = [path.name for path in self.snapshots.iterdir()
                     if recovery.SNAPSHOT.fullmatch(path.name)]
        self.assertEqual(published, [previous])
        self.assertFalse(any(path.name.startswith('.partial-') for path in self.snapshots.iterdir()))

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

    @unittest.skipUnless(os.name == 'nt', 'Real Windows directory sharing')
    def test_snapshot_finalize_retries_real_directory_lock_until_thread_releases(self):
        prior = self.snapshot()
        failures = []
        failed = threading.Event()
        released = threading.Event()
        holders = []
        threads = []
        real_write = recovery._write_json
        real_replace, real_rename = os.replace, os.rename

        def hold_after_manifest(path, value):
            real_write(path, value)
            holder = DirectoryHold(path.parent)
            holders.append(holder)
            def release_later():
                if failed.wait(5):
                    time.sleep(0.15)
                holder.close()
                released.set()
            thread = threading.Thread(target=release_later)
            threads.append(thread)
            thread.start()

        def observe(operation):
            def rename(source, target):
                try:
                    return operation(source, target)
                except OSError as error:
                    if Path(source).name.startswith('.partial-'):
                        failures.append(error.winerror)
                        failed.set()
                    raise
            return rename

        try:
            with mock.patch.object(recovery, '_write_json', side_effect=hold_after_manifest) as manifest, \
                    mock.patch.object(os, 'replace', side_effect=observe(real_replace)), \
                    mock.patch.object(os, 'rename', side_effect=observe(real_rename)), \
                    mock.patch.object(recovery, '_copy_tree', wraps=recovery._copy_tree) as copy:
                name = self.snapshot()
            self.assertTrue(released.wait(1))
            self.assertTrue(failures)
            self.assertTrue(all(code in (5, 32, 33) for code in failures), failures)
            self.assertEqual(copy.call_count, 1)
            self.assertEqual(manifest.call_count, 1)
            self.assertEqual((self.snapshots / name / 'tdata/account/map0').read_bytes(),
                             b'synthetic account state')
            self.assertFalse(any(p.name.startswith('.partial-') for p in self.snapshots.iterdir()))
        finally:
            failed.set()
            for thread in threads:
                thread.join(6)
                self.assertFalse(thread.is_alive())
            for holder in holders:
                holder.close()
            self.assertTrue((self.snapshots / prior / 'manifest.json').is_file())

    @unittest.skipUnless(os.name == 'nt', 'Real Windows directory sharing')
    def test_snapshot_finalize_persistent_lock_preserves_primary_error_and_previous(self):
        prior = self.snapshot()
        holders = []
        real_write = recovery._write_json
        def hold(path, value):
            real_write(path, value)
            holders.append(DirectoryHold(path.parent))
        started = time.monotonic()
        try:
            with mock.patch.object(recovery, '_write_json', side_effect=hold):
                with self.assertRaises(recovery.RecoveryError) as caught:
                    self.snapshot()
            self.assertIn(caught.exception.__cause__.winerror, (5, 32, 33))
            self.assertIn('publication', str(caught.exception).lower())
            self.assertTrue(any('cleanup' in note.lower()
                                for note in getattr(caught.exception, '__notes__', [])))
            self.assertLess(time.monotonic() - started, 4)
            self.assertEqual([p.name for p in self.snapshots.iterdir()
                              if recovery.SNAPSHOT.fullmatch(p.name)], [prior])
        finally:
            for holder in holders:
                holder.close()
        for partial in self.snapshots.glob('.partial-*'):
            recovery._remove_tree(partial, self.snapshots)
        self.assertTrue((self.snapshots / prior / 'manifest.json').is_file())

    @unittest.skipUnless(os.name == 'nt', 'Windows no-clobber rename')
    def test_snapshot_finalize_refuses_injected_target_between_attempts(self):
        partial = self.root / ('.partial-' + 'a' * 32)
        partial.mkdir()
        (partial / 'payload').write_bytes(b'original')
        target = self.root / ('snapshot-20261008T120000000000Z-' + 'b' * 32)
        real_rename = os.rename
        holder = DirectoryHold(partial)
        calls = []
        def inject(source, destination):
            calls.append(source)
            try:
                return real_rename(source, destination)
            except OSError:
                target.mkdir()
                (target / 'keep').write_bytes(b'injected')
                holder.close()
                raise
        try:
            with mock.patch.object(os, 'rename', side_effect=inject):
                with self.assertRaisesRegex(recovery.RecoveryError, 'target|destination'):
                    recovery._finalize_snapshot(partial, self.root, target.name)
        finally:
            holder.close()
        self.assertEqual(len(calls), 1)
        self.assertEqual((target / 'keep').read_bytes(), b'injected')
        self.assertEqual((partial / 'payload').read_bytes(), b'original')

    @unittest.skipUnless(os.name == 'nt', 'Windows error codes')
    def test_snapshot_finalize_does_not_retry_unrelated_errors(self):
        partial = self.root / ('.partial-' + 'a' * 32)
        partial.mkdir()
        name = 'snapshot-20261008T120000000000Z-' + 'b' * 32
        error = ctypes.WinError(112)  # ERROR_DISK_FULL
        with mock.patch.object(os, 'rename', side_effect=error) as rename:
            with self.assertRaises(OSError) as caught:
                recovery._finalize_snapshot(partial, self.root, name)
        self.assertIs(caught.exception, error)
        self.assertEqual(rename.call_count, 1)
        self.assertTrue(partial.is_dir())

    @unittest.skipUnless(os.name == 'nt', 'Windows error code classification')
    def test_snapshot_finalize_retries_only_selected_windows_errors(self):
        for code in (5, 33):  # 32 is exercised by real sharing-conflict handles above.
            with self.subTest(code=code):
                partial = self.root / ('.partial-' + 'a' * 32)
                partial.mkdir()
                name = 'snapshot-20261008T120000000000Z-' + 'b' * 32
                real_rename = os.rename
                attempts = []
                def first_blocked(source, target):
                    attempts.append(source)
                    if len(attempts) == 1:
                        raise ctypes.WinError(code)
                    return real_rename(source, target)
                with mock.patch.object(os, 'rename', side_effect=first_blocked):
                    recovery._finalize_snapshot(partial, self.root, name)
                self.assertEqual(len(attempts), 2)
                self.assertTrue((self.root / name).is_dir())
                (self.root / name).rmdir()

    def test_snapshot_cli_reports_cleanup_failure_without_masking_primary(self):
        import io
        error = recovery.RecoveryError('Snapshot publication failed.')
        error.add_note('Snapshot cleanup failed; private partial may remain.')
        stderr = io.StringIO()
        with mock.patch.object(recovery, 'snapshot', side_effect=error), \
                mock.patch.object(recovery.sys, 'stderr', stderr):
            result = recovery.main(['snapshot', '--work-dir', str(self.work),
                                    '--snapshot-root', str(self.snapshots),
                                    '--installed-executable', str(self.exe), '--installation-dir', str(self.exe.parent),
                                    '--version', '7.2.9-r8'])
        self.assertEqual(result, 1)
        self.assertIn('publication failed', stderr.getvalue())
        self.assertIn('cleanup failed', stderr.getvalue())

    @unittest.skipUnless(os.name == 'nt', 'Windows atomic no-clobber rename')
    def test_snapshot_finalize_racing_empty_target_is_not_overwritten(self):
        partial = self.root / ('.partial-' + 'a' * 32)
        partial.mkdir()
        (partial / 'payload').write_bytes(b'original')
        target = self.root / ('snapshot-20261008T120000000000Z-' + 'b' * 32)
        real_rename = os.rename
        def inject_before_rename(source, destination):
            target.mkdir()  # After validation, before the OS call; it must not be replaced.
            return real_rename(source, destination)
        with mock.patch.object(os, 'rename', side_effect=inject_before_rename) as rename:
            with self.assertRaises(OSError):
                recovery._finalize_snapshot(partial, self.root, target.name)
        self.assertEqual(rename.call_count, 1)
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual((partial / 'payload').read_bytes(), b'original')

    @unittest.skipUnless(os.name == 'nt', 'Real Windows directory sharing')
    def test_snapshot_finalize_retry_rejects_replaced_root_identity(self):
        root = self.root / 'publication-root'
        root.mkdir()
        partial = root / ('.partial-' + 'a' * 32)
        partial.mkdir()
        name = 'snapshot-20261008T120000000000Z-' + 'b' * 32
        holder = DirectoryHold(partial)
        real_rename = os.rename
        def swap_root(source, target):
            try:
                return real_rename(source, target)
            except OSError:
                holder.close()
                real_rename(root, self.root / 'saved-root')
                root.mkdir()
                partial.mkdir()
                raise
        try:
            with mock.patch.object(os, 'rename', side_effect=swap_root):
                with self.assertRaisesRegex(recovery.RecoveryError, 'changed|identity'):
                    recovery._finalize_snapshot(partial, root, name)
        finally:
            holder.close()
        self.assertFalse((root / name).exists())
        self.assertTrue((self.root / 'saved-root' / partial.name).is_dir())

    @unittest.skipUnless(os.name == 'nt', 'Windows symlink/reparse path validation')
    def test_snapshot_finalize_retry_rejects_partial_reparse_substitution(self):
        partial = self.root / ('.partial-' + 'a' * 32)
        partial.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        name = 'snapshot-20261008T120000000000Z-' + 'b' * 32
        holder = DirectoryHold(partial)
        real_rename = os.rename
        def substitute_link(source, target):
            try:
                return real_rename(source, target)
            except OSError:
                holder.close()
                real_rename(partial, self.root / 'saved-partial')
                try:
                    partial.symlink_to(outside, target_is_directory=True)
                except OSError:
                    self.skipTest('This Windows token cannot create symlinks')
                raise
        try:
            with mock.patch.object(os, 'rename', side_effect=substitute_link):
                with self.assertRaisesRegex(recovery.RecoveryError, 'link|reparse'):
                    recovery._finalize_snapshot(partial, self.root, name)
        finally:
            holder.close()
        self.assertFalse((self.root / name).exists())
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'Real Windows directory sharing')
    def test_snapshot_finalize_retry_rejects_replaced_partial_identity(self):
        partial = self.root / ('.partial-' + 'a' * 32)
        partial.mkdir()
        name = 'snapshot-20261008T120000000000Z-' + 'b' * 32
        holder = DirectoryHold(partial)
        real_rename = os.rename
        def swap(source, target):
            try:
                return real_rename(source, target)
            except OSError:
                holder.close()
                real_rename(partial, self.root / 'saved-partial')
                partial.mkdir()
                raise
        try:
            with mock.patch.object(os, 'rename', side_effect=swap):
                with self.assertRaisesRegex(recovery.RecoveryError, 'changed|identity'):
                    recovery._finalize_snapshot(partial, self.root, name)
        finally:
            holder.close()
        self.assertFalse((self.root / name).exists())

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

    def crash_restore_copy(self, name, point):
        script = '''import importlib.util, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('data_recovery', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
real_copy = module._copy_tree
def interrupt_file(source, target, *args, **kwargs):
    with Path(target).open('wb') as output:
        output.write(b'synthetic')
        output.flush()
        os.fsync(output.fileno())
    os._exit(73)
def interrupt_tree(*args, **kwargs):
    real_copy(*args, **kwargs)
    os._exit(74)
if sys.argv[6] == 'during':
    module.shutil.copyfile = interrupt_file
else:
    module._copy_tree = interrupt_tree
module.restore_snapshot(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5],
    confirm_restore=True, process_probe=lambda path: False)
'''
        result = subprocess.run([sys.executable, '-c', script, str(Path(recovery.__file__)),
                                 str(self.work), str(self.snapshots), str(self.exe), name, point],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 73 if point == 'during' else 74)

    def assert_copy_crash_recoverable(self, point):
        name = self.snapshot()
        current = self.work / 'tdata' / 'account' / 'map0'
        current.write_bytes(b'newer state')
        self.crash_restore_copy(name, point)
        stages = list(self.work.glob('.fishgram-data-stage-*'))
        self.assertEqual(len(stages), 1)
        self.assertTrue((stages[0] / 'account' / 'map0').is_file())
        self.assertTrue((self.work / recovery.JOURNAL).is_file(),
                        'A killed copy must leave a durable transaction before account bytes')
        self.assertEqual(current.read_bytes(), b'newer state')
        with self.assertRaisesRegex(recovery.RecoveryError, 'explicit recovery'):
            self.restore(name)
        with self.assertRaisesRegex(recovery.RecoveryError, 'explicit'):
            recovery.recover_restore(self.work, self.exe, process_probe=lambda path: False)
        self.assertTrue(stages[0].is_dir())
        recovery.recover_restore(self.work, self.exe, confirm_restore=True,
                                 process_probe=lambda path: False)
        self.assertFalse(stages[0].exists())
        self.assertFalse((self.work / recovery.JOURNAL).exists())
        self.assertEqual(current.read_bytes(), b'newer state')
        self.assertFalse(list(self.work.glob('.fishgram-data-rollback-*')))

    def test_restore_copy_crashes_during_have_durable_journal_and_safe_cleanup(self):
        self.assert_copy_crash_recoverable('during')

    def test_restore_copy_crashes_after_have_durable_journal_and_safe_cleanup(self):
        self.assert_copy_crash_recoverable('after')

    def test_restore_initial_journal_publication_failure_never_copies_data(self):
        name = self.snapshot()
        real_link = recovery.os.link
        def fail_journal(source, target, **kwargs):
            if Path(target).name == recovery.JOURNAL:
                raise OSError('synthetic journal publication failure')
            return real_link(source, target, **kwargs)
        with mock.patch.object(recovery.os, 'link', side_effect=fail_journal):
            with self.assertRaises(recovery.RecoveryError):
                self.restore(name)
        self.assertFalse(list(self.work.glob('.fishgram-data-stage-*')))
        self.assertFalse((self.work / recovery.JOURNAL).exists())
        self.assertFalse(list(self.work.glob(recovery.JOURNAL + '-*.tmp')))
        self.assertEqual((self.work / 'tdata' / 'account' / 'map0').read_bytes(), b'synthetic account state')

    def test_restore_initial_journal_never_overwrites_racing_record(self):
        name = self.snapshot()
        real_link = recovery.os.link
        def race(source, target, **kwargs):
            if Path(target).name == recovery.JOURNAL:
                Path(target).write_bytes(b'unknown competing transaction')
            return real_link(source, target, **kwargs)
        with mock.patch.object(recovery.os, 'link', side_effect=race):
            with self.assertRaises(recovery.RecoveryError):
                self.restore(name)
        self.assertEqual((self.work / recovery.JOURNAL).read_bytes(), b'unknown competing transaction')
        self.assertFalse(list(self.work.glob('.fishgram-data-stage-*')))

    def test_restore_allocation_crash_before_mkdir_can_be_explicitly_recovered(self):
        name = self.snapshot()
        real_mkdir = Path.mkdir
        def interrupt(path, *args, **kwargs):
            if path.name.startswith('.fishgram-data-stage-'):
                raise KeyboardInterrupt()
            return real_mkdir(path, *args, **kwargs)
        with mock.patch.object(Path, 'mkdir', interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.restore(name)
        self.assertTrue((self.work / recovery.JOURNAL).exists())
        self.assertFalse(list(self.work.glob('.fishgram-data-stage-*')))
        recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=lambda path: False)
        self.assertFalse((self.work / recovery.JOURNAL).exists())

    def test_restore_unrecorded_allocation_or_racing_directory_is_preserved(self):
        name = self.snapshot()
        real_mkdir = Path.mkdir
        def competing_directory(path, *args, **kwargs):
            if path.name.startswith('.fishgram-data-stage-'):
                real_mkdir(path)
                (path / 'user-file').write_bytes(b'unknown directory')
            return real_mkdir(path, *args, **kwargs)
        with mock.patch.object(Path, 'mkdir', competing_directory):
            with self.assertRaisesRegex(recovery.RecoveryError, 'explicit recovery'):
                self.restore(name)
        stage, = self.work.glob('.fishgram-data-stage-*')
        with self.assertRaisesRegex(recovery.RecoveryError, 'ownership'):
            recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=lambda path: False)
        self.assertEqual((stage / 'user-file').read_bytes(), b'unknown directory')
        self.assertTrue((self.work / recovery.JOURNAL).exists())

    def test_restore_crash_before_stage_identity_is_persisted_preserves_empty_stage(self):
        name = self.snapshot()
        real_write = recovery._write_json
        def interrupt(path, value, **kwargs):
            if value.get('state') == 'copying':
                raise KeyboardInterrupt()
            real_write(path, value, **kwargs)
        with mock.patch.object(recovery, '_write_json', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.restore(name)
        stage, = self.work.glob('.fishgram-data-stage-*')
        self.assertEqual(list(stage.iterdir()), [])
        with self.assertRaisesRegex(recovery.RecoveryError, 'ownership'):
            recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=lambda path: False)
        self.assertTrue(stage.is_dir())
        self.assertTrue((self.work / recovery.JOURNAL).exists())

    def test_restore_copy_failure_cleans_only_owned_partial_and_preserves_original(self):
        name = self.snapshot()
        current = self.work / 'tdata' / 'account' / 'map0'
        current.write_bytes(b'newer state')
        def fail(source, target):
            Path(target).write_bytes(b'synthetic')
            raise OSError('synthetic partial copy failure')
        with mock.patch.object(recovery.shutil, 'copyfile', side_effect=fail):
            with self.assertRaisesRegex(recovery.RecoveryError, 'previous account data is preserved'):
                self.restore(name)
        self.assertEqual(current.read_bytes(), b'newer state')
        self.assertFalse(list(self.work.glob('.fishgram-data-stage-*')))
        self.assertFalse((self.work / recovery.JOURNAL).exists())

    def test_restore_recovery_accepts_legacy_prepared_journal(self):
        name = self.snapshot()
        stage = self.work / ('.fishgram-data-stage-' + 'a' * 32)
        manifest = json.loads((self.snapshots / name / 'manifest.json').read_bytes())
        recovery._copy_tree(self.snapshots / name / 'tdata', stage, manifest['files'])
        recovery._write_json(self.work / recovery.JOURNAL, {
            'schema': 1, 'state': 'prepared', 'stage': stage.name,
            'rollback': '.fishgram-data-rollback-' + 'a' * 32,
            'old_files': recovery._inventory(self.work / 'tdata'), 'new_files': manifest['files']})
        recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=lambda path: False)
        self.assertFalse(stage.exists())
        self.assertFalse((self.work / recovery.JOURNAL).exists())
        self.assertEqual((self.work / 'tdata' / 'account' / 'map0').read_bytes(), b'synthetic account state')

    def test_restore_copy_recovery_preserves_unknown_stage_content_and_current_data(self):
        name = self.snapshot()
        self.crash_restore_copy(name, 'during')
        stage, = self.work.glob('.fishgram-data-stage-*')
        (stage / 'user-file').write_bytes(b'unknown data')
        with self.assertRaisesRegex(recovery.RecoveryError, 'Unknown'):
            recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=lambda path: False)
        self.assertEqual((stage / 'user-file').read_bytes(), b'unknown data')
        self.assertEqual((stage / 'account' / 'map0').read_bytes(), b'synthetic')
        self.assertTrue((self.work / recovery.JOURNAL).exists())
        self.assertEqual((self.work / 'tdata' / 'account' / 'map0').read_bytes(), b'synthetic account state')

    def test_restore_copy_recovery_rejects_stage_identity_substitution(self):
        name = self.snapshot()
        self.crash_restore_copy(name, 'during')
        stage, = self.work.glob('.fishgram-data-stage-*')
        preserved = self.work / 'preserved-original-stage'
        stage.rename(preserved)
        stage.mkdir()
        (stage / 'user-file').write_bytes(b'replacement')
        with self.assertRaisesRegex(recovery.RecoveryError, 'identity'):
            recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=lambda path: False)
        self.assertEqual((stage / 'user-file').read_bytes(), b'replacement')
        self.assertEqual((preserved / 'account' / 'map0').read_bytes(), b'synthetic')
        self.assertTrue((self.work / recovery.JOURNAL).exists())

    def test_restore_copy_recovery_rejects_reparse_without_following(self):
        name = self.snapshot()
        self.crash_restore_copy(name, 'during')
        stage, = self.work.glob('.fishgram-data-stage-*')
        outside = self.root / 'unknown-outside'
        outside.mkdir()
        (outside / 'user-file').write_bytes(b'outside')
        link = stage / 'linked'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('This Windows token cannot create symlinks')
        with self.assertRaisesRegex(recovery.RecoveryError, 'link|reparse|Unknown'):
            recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=lambda path: False)
        self.assertEqual((outside / 'user-file').read_bytes(), b'outside')
        self.assertTrue((stage / 'account' / 'map0').exists())
        self.assertTrue((self.work / recovery.JOURNAL).exists())

    def test_restore_copy_recovery_preserves_changed_original_data(self):
        name = self.snapshot()
        self.crash_restore_copy(name, 'during')
        current = self.work / 'tdata' / 'account' / 'map0'
        current.write_bytes(b'changed after crash')
        with self.assertRaisesRegex(recovery.RecoveryError, 'Original data changed'):
            recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=lambda path: False)
        self.assertEqual(current.read_bytes(), b'changed after crash')
        self.assertTrue((self.work / recovery.JOURNAL).exists())

    @unittest.skipUnless(os.name == 'nt', 'Actual Windows staging ownership guard')
    def test_restore_copy_guard_blocks_real_directory_rename(self):
        name = self.snapshot()
        real_copy = recovery._copy_tree
        errors = []
        def try_rename(source, target, files, **kwargs):
            try:
                target.rename(target.with_name('racing-stage'))
            except OSError as error:
                errors.append(error.winerror)
            real_copy(source, target, files, **kwargs)
        with mock.patch.object(recovery, '_copy_tree', side_effect=try_rename):
            self.restore(name)
        self.assertEqual(errors, [32])
        self.assertFalse((self.work / 'racing-stage').exists())

    def test_restore_copy_recovery_keeps_work_lock_against_another_process(self):
        name = self.snapshot()
        self.crash_restore_copy(name, 'during')
        with recovery._lock(self.work, '.fishgram-data.lock'):
            script = '''import importlib.util, sys
spec = importlib.util.spec_from_file_location('data_recovery', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
try:
    module.recover_restore(sys.argv[2], sys.argv[3], confirm_restore=True, process_probe=lambda path: False)
except module.RecoveryError:
    sys.exit(75)
'''
            result = subprocess.run([sys.executable, '-c', script, str(Path(recovery.__file__)),
                                     str(self.work), str(self.exe)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 75)
        self.assertTrue((self.work / recovery.JOURNAL).exists())
        stage, = self.work.glob('.fishgram-data-stage-*')
        self.assertEqual((stage / 'account' / 'map0').read_bytes(), b'synthetic')
        recovery.recover_restore(self.work, self.exe, confirm_restore=True, process_probe=lambda path: False)
        self.assertFalse(stage.exists())

    @unittest.skipUnless(os.name == 'nt', 'Actual Windows journal sharing violation')
    def test_locked_restore_journal_preserves_data_and_explicit_recovery(self):
        name = self.snapshot()
        current = self.work / 'tdata' / 'account' / 'map0'
        current.write_bytes(b'newer state')
        real_write = recovery._write_json
        held = []
        errors = []

        def hold_prepared_journal(path, value, **kwargs):
            try:
                real_write(path, value, **kwargs)
            except OSError as error:
                errors.append(getattr(error, 'winerror', None))
                raise
            if Path(path).name == recovery.JOURNAL and value['state'] == 'prepared':
                held.append(DirectoryHold(path))

        try:
            with mock.patch.object(recovery, '_write_json', side_effect=hold_prepared_journal):
                with self.assertRaisesRegex(recovery.RecoveryError, 'explicit recovery'):
                    self.restore(name)
            self.assertTrue(errors and errors[0] in (5, 32))
            self.assertEqual(current.read_bytes(), b'newer state')
            self.assertTrue((self.work / recovery.JOURNAL).is_file())
            self.assertFalse(list(self.work.glob(recovery.JOURNAL + '-*.tmp')))
        finally:
            for handle in held:
                handle.close()
        recovery.recover_restore(self.work, self.exe, confirm_restore=True,
                                 process_probe=lambda path: False)
        self.assertEqual(current.read_bytes(), b'newer state')
        self.assertFalse((self.work / recovery.JOURNAL).exists())
        self.assertFalse(list(self.work.glob('.fishgram-data-stage-*')))

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
