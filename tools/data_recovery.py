"""Private, offline FishGram account snapshots and explicitly selected recovery.

The caller must normally exit the client and keep it closed during this tool.
This tool does not terminate clients or promise compatibility with an old exe.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time
import uuid


class RecoveryError(Exception):
    pass


VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+-r[1-9][0-9]*')
SNAPSHOT = re.compile(r'snapshot-[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}')
LOCAL_FOLDER = re.compile(r'\.fishgram-data-(stage|rollback)-[a-f0-9]{32}')
JOURNAL = '.fishgram-data-restore.json'
MARKER = '.fishgram-snapshots'
MAX_MANIFEST = 8 * 1024 * 1024
FILE_ALL_ACCESS = 0x001F01FF


def _validate_private_acl_entries(entries, expected_sids, directory: bool) -> None:
    expected_flags = 0x03 if directory else 0
    expected = {(sid, 0, expected_flags, FILE_ALL_ACCESS) for sid in expected_sids}
    if len(entries) != 3 or len(expected) != 3 or set(entries) != expected:
        raise RecoveryError('Private storage permissions did not read back correctly.')


def _reject_link(path: Path) -> None:
    if os.path.lexists(path):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RecoveryError('Links and reparse points are not permitted.')


def _canonical(path, *, missing=False) -> Path:
    path = Path(os.path.abspath(Path(path).expanduser()))
    for ancestor in [path, *path.parents]:
        _reject_link(ancestor)
    if not path.exists() and not (missing and path.parent.is_dir()):
        raise RecoveryError('Required path or parent directory is missing.')
    return path.resolve(strict=not missing)


def _relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or '\\' in value or ':' in value or any(ord(c) < 32 for c in value):
        raise RecoveryError('Invalid relative data path.')
    path = PurePosixPath(value)
    if not value or path.is_absolute() or path.as_posix() != value:
        raise RecoveryError('Invalid relative data path.')
    for part in path.parts:
        if part in ('.', '..') or part.endswith(('.', ' ')) or re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])', part.split('.')[0]):
            raise RecoveryError('Invalid relative data path.')
    return path


def _private_acl(path: Path) -> None:
    if os.name != 'nt':
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
        return
    from ctypes import wintypes
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
    advapi.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSecurityDescriptorControl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    advapi.GetSecurityDescriptorDacl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
    advapi.GetAce.argtypes = [wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID)]
    advapi.GetAce.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise RecoveryError('Could not obtain private storage identity.')
    sid_text = wintypes.LPWSTR()
    descriptor = wintypes.LPVOID()
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        data = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, data, len(data), ctypes.byref(needed)):
            raise RecoveryError('Could not obtain private storage identity.')
        sid = ctypes.cast(data, ctypes.POINTER(wintypes.LPVOID))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise RecoveryError('Could not obtain private storage identity.')
        flags = 'OICI' if path.is_dir() else ''
        sddl = 'D:P' + ''.join(f'(A;{flags};FA;;;{value})' for value in (sid_text.value, 'SY', 'BA'))
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise RecoveryError('Could not prepare private storage permissions.')
        if not advapi.SetFileSecurityW(str(path), 4 | 0x80000000, descriptor):
            raise RecoveryError('Could not restrict private storage permissions.')
        required = wintypes.DWORD()
        advapi.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(required))
        readback = ctypes.create_string_buffer(required.value)
        control, revision = wintypes.WORD(), wintypes.DWORD()
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
        if (not advapi.GetFileSecurityW(str(path), 4, readback, len(readback), ctypes.byref(required))
                or not advapi.GetSecurityDescriptorControl(readback, ctypes.byref(control), ctypes.byref(revision))
                or not control.value & 0x1000
                or not advapi.GetSecurityDescriptorDacl(readback, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted))
                or not present or not acl):
            raise RecoveryError('Private storage permissions did not read back correctly.')
        ace_count = ctypes.c_ushort.from_address(acl.value + 4).value
        entries = []
        for index in range(ace_count):
            ace = wintypes.LPVOID()
            if not advapi.GetAce(acl, index, ctypes.byref(ace)):
                raise RecoveryError('Private storage permissions did not read back correctly.')
            ace_type = ctypes.c_ubyte.from_address(ace.value).value
            if ace_type != 0:  # ACCESS_ALLOWED_ACE; other layouts have different SID offsets.
                raise RecoveryError('Private storage permissions did not read back correctly.')
            ace_flags = ctypes.c_ubyte.from_address(ace.value + 1).value
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            ace_sid = ctypes.c_void_p(ace.value + 8)
            ace_sid_text = wintypes.LPWSTR()
            if not advapi.ConvertSidToStringSidW(ace_sid, ctypes.byref(ace_sid_text)):
                raise RecoveryError('Private storage permissions did not read back correctly.')
            try:
                entries.append((ace_sid_text.value, ace_type, ace_flags, mask))
            finally:
                kernel.LocalFree(ace_sid_text)
        _validate_private_acl_entries(entries, (sid_text.value, 'S-1-5-18', 'S-1-5-32-544'), path.is_dir())
    finally:
        kernel.CloseHandle(token)
        if sid_text:
            kernel.LocalFree(sid_text)
        if descriptor:
            kernel.LocalFree(descriptor)


def process_running(executable: Path) -> bool:
    if os.name != 'nt':
        raise RecoveryError('Production process checks require Windows.')
    from ctypes import wintypes
    executable = _canonical(executable)
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    inventory = api.CreateToolhelp32Snapshot(2, 0)
    if inventory == ctypes.c_void_p(-1).value:
        raise RecoveryError('Could not check running clients.')
    try:
        item = Entry()
        item.dwSize = ctypes.sizeof(item)
        present = api.Process32FirstW(inventory, ctypes.byref(item))
        while present:
            if item.szExeFile.casefold() == executable.name.casefold():
                handle = api.OpenProcess(0x1000, False, item.th32ProcessID)
                if not handle:
                    if ctypes.get_last_error() != 87:
                        raise RecoveryError('Could not safely check a matching client process.')
                else:
                    try:
                        buffer = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(buffer))
                        if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                            raise RecoveryError('Could not safely check a matching client process.')
                        if os.path.samefile(buffer.value, executable):
                            return True
                    finally:
                        api.CloseHandle(handle)
            present = api.Process32NextW(inventory, ctypes.byref(item))
        if ctypes.get_last_error() != 18:
            raise RecoveryError('Could not complete the process inventory.')
        return False
    finally:
        api.CloseHandle(inventory)


def _closed(executable: Path, probe) -> None:
    if probe(executable):
        raise RecoveryError('The installed client is running; normally exit it first.')


@contextlib.contextmanager
def _client_session_gate(install_dir: Path, *, exclusive=True):
    """Reuse the production client-session.lock lease implementation."""
    if os.name != 'nt':
        # Non-Windows unit tests do not run the Windows client or its gate.
        yield
        return
    import importlib.util
    module_name = '_fishgram_program_recovery_gate'
    module = sys.modules.get(module_name)
    if module is None:
        path = Path(__file__).with_name('program_recovery.py')
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RecoveryError('无法加载生产 client-session lease 实现')
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    try:
        with module._ClientSessionLease(install_dir, exclusive=exclusive):
            yield
    except RecoveryError:
        raise
    except Exception as error:
        raise RecoveryError(f'无法取得生产 client-session lease：{error}') from error


def _inventory(root: Path) -> dict:
    root = _canonical(root)
    if not root.is_dir():
        raise RecoveryError('Data directory is missing.')
    result = {}
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in folders + files:
            path = Path(directory) / name
            _reject_link(path)
            relative = path.relative_to(root).as_posix()
            _relative(relative)
            if name in folders:
                if not path.is_dir():
                    raise RecoveryError('Data contains an unsupported filesystem entry.')
                result[relative] = {'directory': True}
            if name in files:
                if not path.is_file():
                    raise RecoveryError('Data contains an unsupported filesystem entry.')
                digest = hashlib.sha256()
                with path.open('rb') as source:
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(block)
                result[relative] = {'size': path.stat().st_size, 'sha256': digest.hexdigest()}
            if len(result) > 50000:
                raise RecoveryError('Data contains too many entries for this tool.')
    return dict(sorted(result.items()))


def _valid_files(files) -> dict:
    if not isinstance(files, dict) or len(files) > 50000:
        raise RecoveryError('Invalid snapshot file inventory.')
    folded = set()
    for name, entry in files.items():
        _relative(name)
        if name.casefold() in folded:
            raise RecoveryError('Duplicate snapshot file path.')
        folded.add(name.casefold())
        if isinstance(entry, dict) and set(entry) == {'directory'} and entry['directory'] is True:
            continue
        if (not isinstance(entry, dict) or set(entry) != {'size', 'sha256'}
                or type(entry['size']) is not int or entry['size'] < 0
                or not isinstance(entry['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', entry['sha256'])):
            raise RecoveryError('Invalid snapshot file metadata.')
    return files


def _space(root: Path, files: dict) -> None:
    existing = root if root.exists() else root.parent
    if shutil.disk_usage(existing)[2] < sum(item.get('size', 0) for item in files.values()) + 1024 * 1024:
        raise RecoveryError('Insufficient free space for a complete data copy.')


def _copy_tree(source: Path, target: Path, files: dict) -> None:
    target.mkdir()
    _private_acl(target)
    for relative, entry in files.items():
        source_file = source.joinpath(*_relative(relative).parts)
        target_file = target.joinpath(*_relative(relative).parts)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        _reject_link(source_file)
        if entry.get('directory') is True:
            target_file.mkdir(parents=True, exist_ok=True)
            continue
        source_info = source_file.stat()
        shutil.copyfile(source_file, target_file)
        os.utime(target_file, ns=(source_info.st_atime_ns, source_info.st_mtime_ns))
        with target_file.open('r+b') as output:
            output.flush()
            os.fsync(output.fileno())


def _read_json(path: Path) -> dict:
    _reject_link(path)
    if not path.is_file() or path.stat().st_size > MAX_MANIFEST:
        raise RecoveryError('Invalid recovery metadata.')
    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise RecoveryError('Duplicate recovery metadata key.')
            value[key] = item
        return value
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError) as error:
        raise RecoveryError('Invalid recovery metadata.') from error


def _write_json(path: Path, value: dict) -> None:
    _reject_link(path)
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    if len(encoded) > MAX_MANIFEST:
        raise RecoveryError('Recovery metadata exceeds the configured size limit.')
    temporary = path.with_name(path.name + '-' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as output:
            _private_acl(temporary)
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def _lock(root: Path, name: str):
    path = root / name
    _reject_link(path)
    try:
        with path.open('a+b') as file:
            if not path.stat().st_size:
                file.write(b'0')
                file.flush()
            file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
    except OSError as error:
        raise RecoveryError('Private data operation failed or is already locked.') from error


def _remove_tree(path: Path, root: Path) -> None:
    path = _canonical(path)
    root = _canonical(root)
    if path.parent != root:
        raise RecoveryError('Refusing cleanup outside the private operation directory.')
    _inventory(path)
    shutil.rmtree(path)


def _roots(work_dir, snapshot_root, executable):
    work = _canonical(work_dir)
    root = _canonical(snapshot_root, missing=True)
    exe = _canonical(executable)
    if not work.is_dir() or not exe.is_file():
        raise RecoveryError('Working directory or installed executable is invalid.')
    if root == work or root in work.parents or work in root.parents:
        raise RecoveryError('Snapshot and working directories must not overlap.')
    return work, root, exe


def _snapshot_root(root: Path) -> None:
    _reject_link(root / MARKER)
    if root.exists():
        if not root.is_dir():
            raise RecoveryError('Snapshot root is not a directory.')
        entries = list(root.iterdir())
        if entries and (not (root / MARKER).is_file() or (root / MARKER).read_bytes() != b'FishGram snapshots v1\n'):
            raise RecoveryError('Refusing an existing unrelated snapshot directory.')
    else:
        root.mkdir()
    for path in root.iterdir():
        _reject_link(path)
        if path.name not in (MARKER, '.snapshot.lock') and not SNAPSHOT.fullmatch(path.name) and not re.fullmatch(r'\.partial-[a-f0-9]{32}', path.name):
            raise RecoveryError('Snapshot directory contains an unrelated entry.')
    _private_acl(root)
    if not (root / MARKER).exists():
        (root / MARKER).write_bytes(b'FishGram snapshots v1\n')


def _read_snapshot(root: Path, name: str):
    if not isinstance(name, str) or not SNAPSHOT.fullmatch(name):
        raise RecoveryError('Invalid snapshot selection.')
    folder = _canonical(root / name)
    if folder.parent != root or {path.name for path in folder.iterdir()} != {'manifest.json', 'tdata'}:
        raise RecoveryError('Snapshot verification failed.')
    value = _read_json(folder / 'manifest.json')
    if (not isinstance(value, dict) or set(value) != {'schema', 'version', 'createdUtc', 'files'}
            or type(value['schema']) is not int or value['schema'] != 1
            or not isinstance(value['version'], str) or not VERSION.fullmatch(value['version'])
            or not isinstance(value['createdUtc'], str)):
        raise RecoveryError('Invalid snapshot manifest.')
    files = _valid_files(value['files'])
    if _inventory(folder / 'tdata') != files:
        raise RecoveryError('Snapshot verification failed.')
    return folder, value


def _directory_identity(path: Path):
    if _canonical(path) != path or not path.is_dir():
        raise RecoveryError('Snapshot directory path changed.')
    info = path.stat()
    return info.st_dev, info.st_ino


def _finalize_snapshot(partial: Path, root: Path, name: str, *, identities=None) -> None:
    """Retry only publication; Windows rename never replaces an existing target."""
    if (os.name != 'nt' or partial.parent != root
            or not re.fullmatch(r'\.partial-[a-f0-9]{32}', partial.name)
            or not SNAPSHOT.fullmatch(name)):
        raise RecoveryError('Invalid snapshot publication paths.')
    if identities is None:
        identities = (_directory_identity(root), _directory_identity(partial))
    target = root / name
    deadline = time.monotonic() + 1.0
    for attempt in range(11):
        if (_directory_identity(root), _directory_identity(partial)) != identities:
            raise RecoveryError('Snapshot directory identity changed before publication.')
        _reject_link(target)
        if os.path.lexists(target):
            raise RecoveryError('Snapshot destination target already exists; refusing to overwrite it.')
        try:
            os.rename(partial, target)  # Windows MoveFileExW without REPLACE_EXISTING
            return
        except OSError as error:
            remaining = deadline - time.monotonic()
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 10 or remaining <= 0:
                raise
            time.sleep(min(0.1, remaining))


def snapshot(work_dir, snapshot_root, installed_executable, version: str, *, process_probe=process_running) -> str:
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise RecoveryError('Use the complete FishGram version for this snapshot.')
    work, root, exe = _roots(work_dir, snapshot_root, installed_executable)
    with _client_session_gate(exe.parent, exclusive=True):
        _closed(exe, process_probe)
        if (work / JOURNAL).exists():
            raise RecoveryError('An interrupted data restore needs explicit recovery first.')
        files = _inventory(work / 'tdata')
        _space(root, files)
        _snapshot_root(root)
        partial = root / ('.partial-' + uuid.uuid4().hex)
        with _lock(root, '.snapshot.lock'), _lock(work, '.fishgram-data.lock'):
            partial.mkdir()
            _private_acl(partial)
            identities = (_directory_identity(root), _directory_identity(partial))
            try:
                _copy_tree(work / 'tdata', partial / 'tdata', files)
                _closed(exe, process_probe)
                if _inventory(work / 'tdata') != files or _inventory(partial / 'tdata') != files:
                    raise RecoveryError('Account data changed during the snapshot.')
                now = dt.datetime.now(dt.timezone.utc)
                name = 'snapshot-' + now.strftime('%Y%m%dT%H%M%S%fZ-') + uuid.uuid4().hex
                _write_json(partial / 'manifest.json', {'schema': 1, 'version': version, 'createdUtc': now.isoformat(), 'files': files})
                try:
                    _finalize_snapshot(partial, root, name, identities=identities)
                except OSError as error:
                    raise RecoveryError('Snapshot publication failed; previous copies are preserved. '
                                        f'winerror={getattr(error, "winerror", None)}.') from error
                names = sorted(path.name for path in root.iterdir() if SNAPSHOT.fullmatch(path.name))
                for expired in names[:-2]:
                    _read_snapshot(root, expired)
                    _remove_tree(root / expired, root)
                return name
            except OSError as error:
                raise RecoveryError('Could not create a complete snapshot; previous copies are preserved.') from error
            finally:
                primary = sys.exc_info()[1]
                try:
                    if os.path.lexists(partial):
                        if (_directory_identity(root), _directory_identity(partial)) != identities:
                            raise RecoveryError('Snapshot cleanup directory identity changed.')
                        _remove_tree(partial, root)
                except (OSError, RecoveryError) as cleanup_error:
                    if primary is None:
                        raise
                    primary.add_note('Snapshot cleanup failed; private partial may remain. '
                                     f'Cleanup error: {type(cleanup_error).__name__}; '
                                     f'winerror={getattr(cleanup_error, "winerror", None)}.')


def _recover_locked(work: Path) -> str | None:
    path = work / JOURNAL
    if not path.exists():
        return None
    value = _read_json(path)
    if (not isinstance(value, dict) or set(value) != {'schema', 'state', 'stage', 'rollback', 'old_files', 'new_files'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['state'] not in ('prepared', 'moving-old', 'replacing', 'committed')
            or not isinstance(value['stage'], str) or not LOCAL_FOLDER.fullmatch(value['stage'])
            or not value['stage'].startswith('.fishgram-data-stage-')
            or not isinstance(value['rollback'], str) or not LOCAL_FOLDER.fullmatch(value['rollback'])
            or not value['rollback'].startswith('.fishgram-data-rollback-')):
        raise RecoveryError('Invalid restore transaction; manual investigation is required.')
    old_files = _valid_files(value['old_files'])
    new_files = _valid_files(value['new_files'])
    data, stage, old = work / 'tdata', work / value['stage'], work / value['rollback']
    for folder in [data, stage, old]:
        _reject_link(folder)
    if value['state'] == 'committed':
        if _inventory(data) != new_files or _inventory(old) != old_files:
            raise RecoveryError('Committed restore verification failed; existing data is preserved.')
        path.unlink()
        return old.name
    if old.exists():
        if _inventory(old) != old_files:
            raise RecoveryError('Rollback data verification failed; existing data is preserved.')
        if data.exists():
            _inventory(data)
            rejected = work / ('.fishgram-data-rejected-' + uuid.uuid4().hex)
            os.replace(data, rejected)
        os.replace(old, data)
    elif not data.exists() or _inventory(data) != old_files:
        raise RecoveryError('Original data is unavailable; manual recovery is required.')
    if stage.exists():
        _remove_tree(stage, work)
    path.unlink()
    return None


def recover_restore(work_dir, installed_executable, *, confirm_restore=False, process_probe=process_running):
    if not confirm_restore:
        raise RecoveryError('Data recovery requires explicit user selection.')
    work, exe = _canonical(work_dir), _canonical(installed_executable)
    with _client_session_gate(exe.parent, exclusive=True):
        _closed(exe, process_probe)
        with _lock(work, '.fishgram-data.lock'):
            return _recover_locked(work)


def restore_snapshot(work_dir, snapshot_root, installed_executable, name: str, *, confirm_restore=False,
                     process_probe=process_running, after_old_moved=None) -> str:
    if not confirm_restore:
        raise RecoveryError('Data restoration requires explicit user selection.')
    work, root, exe = _roots(work_dir, snapshot_root, installed_executable)
    with _client_session_gate(exe.parent, exclusive=True):
        _closed(exe, process_probe)
        _snapshot_root(root)
        with _lock(root, '.snapshot.lock'), _lock(work, '.fishgram-data.lock'):
            if (work / JOURNAL).exists():
                raise RecoveryError('An interrupted restore needs explicit recovery first.')
            folder, manifest = _read_snapshot(root, name)
            old_files = _inventory(work / 'tdata')
            _space(work, manifest['files'])
            identity = uuid.uuid4().hex
            stage = work / ('.fishgram-data-stage-' + identity)
            old = work / ('.fishgram-data-rollback-' + identity)
            journal = {'schema': 1, 'state': 'prepared', 'stage': stage.name, 'rollback': old.name,
                       'old_files': old_files, 'new_files': manifest['files']}
            try:
                _copy_tree(folder / 'tdata', stage, manifest['files'])
                _closed(exe, process_probe)
                if _inventory(stage) != manifest['files'] or _inventory(work / 'tdata') != old_files:
                    raise RecoveryError('Data changed while preparing the restore.')
                _write_json(work / JOURNAL, journal)
                journal['state'] = 'moving-old'
                _write_json(work / JOURNAL, journal)
                os.replace(work / 'tdata', old)
                if after_old_moved:
                    after_old_moved()
                journal['state'] = 'replacing'
                _write_json(work / JOURNAL, journal)
                os.replace(stage, work / 'tdata')
                if _inventory(work / 'tdata') != manifest['files']:
                    raise RecoveryError('Restored data verification failed.')
                journal['state'] = 'committed'
                _write_json(work / JOURNAL, journal)
                (work / JOURNAL).unlink()
                return old.name
            except Exception as error:
                if (work / JOURNAL).exists():
                    try:
                        _closed(exe, process_probe)
                        _recover_locked(work)
                    except Exception as recovery_error:
                        raise RecoveryError('Restore failed; a saved journal and rollback require explicit recovery.') from recovery_error
                raise RecoveryError('Restore failed; previous account data is preserved.') from error
            finally:
                if stage.exists() and not (work / JOURNAL).exists():
                    _remove_tree(stage, work)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('snapshot', 'restore', 'recover'):
        sub = commands.add_parser(command)
        sub.add_argument('--work-dir', required=True)
        sub.add_argument('--installed-executable', required=True)
        if command != 'recover':
            sub.add_argument('--snapshot-root', required=True)
        if command == 'snapshot':
            sub.add_argument('--version', required=True)
        else:
            sub.add_argument('--confirm-restore', action='store_true')
        if command == 'restore':
            sub.add_argument('--snapshot', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'snapshot':
            result = snapshot(args.work_dir, args.snapshot_root, args.installed_executable, args.version)
        elif args.command == 'restore':
            result = restore_snapshot(args.work_dir, args.snapshot_root, args.installed_executable, args.snapshot,
                                      confirm_restore=args.confirm_restore)
        else:
            result = recover_restore(args.work_dir, args.installed_executable, confirm_restore=args.confirm_restore)
        print('Private data operation completed.' + (' ID: ' + result if result else ''))
        return 0
    except (RecoveryError, OSError) as error:
        message = str(error) if isinstance(error, RecoveryError) else 'Filesystem operation failed; current data and recovery files are preserved.'
        print(message, file=sys.stderr)
        for note in getattr(error, '__notes__', ()):
            print(note, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
