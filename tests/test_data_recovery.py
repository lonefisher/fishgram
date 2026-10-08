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
                                    '--installed-executable', str(self.exe), '--version', '7.2.9-r8'])
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
