#!/usr/bin/env python3
"""Standalone, fail-closed recovery of FishGram executable and DLL files."""

from __future__ import annotations

import argparse
import ctypes
from contextlib import contextmanager
import json
import os
import re
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path


class RecoveryError(RuntimeError):
    pass


BACKUP_ID = re.compile(r"[0-9a-f-]{1,64}\Z")
MAX_JOURNAL = 1024 * 1024
WRITE_BITS = 0x0002 | 0x0004 | 0x0010 | 0x0040 | 0x0100 | 0x00010000 | 0x00040000 | 0x00080000 | 0x40000000 | 0x10000000 | 0x02000000
ACCOUNT_NAMES = {"tdata", "telegramdata", "fishgramdata", "key_data", "map0", "map1", "settings0"}


@dataclass(frozen=True)
class Backup:
    backup_id: str
    target_version: str
    previous_version: str
    files: tuple[str, ...]


def _windows_only():
    if os.name != "nt":
        raise RecoveryError("安全恢复仅支持 Windows；当前平台无法复用生产安装锁和 ACL 校验。")


def _is_reparse(path: Path) -> bool:
    try:
        return bool(path.lstat().st_file_attributes & 0x400)
    except AttributeError:
        return path.is_symlink()
    except FileNotFoundError:
        return False


def _trusted_acl(path: Path, *, all_allowed=False):
    """Mirror production HasTrustedPermissions; uncertainty is a hard failure."""
    _windows_only()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    advapi.GetTokenInformation.restype = ctypes.c_int
    advapi.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
    advapi.GetAclInformation.restype = ctypes.c_int
    advapi.GetAce.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetAce.restype = ctypes.c_int
    advapi.ConvertStringSidToSidW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertStringSidToSidW.restype = ctypes.c_int
    kernel.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
    kernel.OpenProcessToken.restype = ctypes.c_int
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    get_info = advapi.GetNamedSecurityInfoW
    get_info.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint,
                         ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                         ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                         ctypes.POINTER(ctypes.c_void_p)]
    get_info.restype = ctypes.c_ulong
    result = get_info(str(path), 1, 0x1 | 0x4, ctypes.byref(owner), None,
                      ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if result or not owner.value or not dacl.value:
        raise RecoveryError(f"无法验证 trusted ACL：{path}")
    token = ctypes.c_void_p()
    principals = []
    try:
        if not kernel.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise RecoveryError("无法读取当前 Windows 用户 SID")
        needed = ctypes.c_ulong()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buf = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buf, needed, ctypes.byref(needed)):
            raise RecoveryError("无法读取当前 Windows 用户 SID")
        user_sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        principals.append(ctypes.string_at(user_sid, _sid_length(advapi, user_sid)))
        for sid_text in ("S-1-5-18", "S-1-5-32-544"):
            sid = ctypes.c_void_p()
            if not advapi.ConvertStringSidToSidW(sid_text, ctypes.byref(sid)):
                raise RecoveryError("无法构造 Windows trusted principals")
            principals.append(ctypes.string_at(sid, _sid_length(advapi, sid)))
            kernel.LocalFree(sid)
        if ctypes.string_at(owner.value, _sid_length(advapi, owner)) not in principals:
            raise RecoveryError(f"ACL owner 不属于当前用户、SYSTEM 或 Administrators：{path}")
        class AclInfo(ctypes.Structure):
            _fields_ = [("AceCount", ctypes.c_ulong), ("BytesInUse", ctypes.c_ulong),
                        ("BytesFree", ctypes.c_ulong)]
        info = AclInfo()
        if not advapi.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            raise RecoveryError(f"无法读取 ACL：{path}")
        for index in range(info.AceCount):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise RecoveryError(f"无法读取 ACL ACE：{path}")
            raw = ctypes.string_at(ace, 8)
            ace_type, flags, _size, mask = struct.unpack_from("<BBHI", raw)
            if flags & 0x08:  # INHERIT_ONLY_ACE
                continue
            if ace_type == 1:  # ACCESS_DENIED_ACE_TYPE: same treatment as production
                continue
            if ace_type != 0:  # production rejects all non-basic allowed/denied ACEs
                raise RecoveryError(f"ACL 含无法安全判断的 ACE：{path}")
            sid_ptr = ace.value + 8
            if (all_allowed or mask & WRITE_BITS) and ctypes.string_at(sid_ptr, _sid_length(advapi, ctypes.c_void_p(sid_ptr))) not in principals:
                raise RecoveryError(f"ACL 向非 trusted principal 授予写权限：{path}")
    finally:
        if descriptor.value:
            kernel.LocalFree(descriptor)
        if token.value:
            kernel.CloseHandle(token)


def _sid_length(advapi, sid):
    advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
    advapi.GetLengthSid.restype = ctypes.c_ulong
    length = advapi.GetLengthSid(sid)
    if not length:
        raise RecoveryError("无效 Windows SID")
    return length


def _safe_program_name(name: str) -> bool:
    lower = name.lower()
    if (not name or len(name) > 240 or name.startswith(".") or name.endswith((".", " "))
            or any(ord(ch) < 32 or ord(ch) > 126 or ch in '/\\:<>' + '"|?*' for ch in name)):
        return False
    stem = lower.split(".", 1)[0]
    if stem in {"con", "prn", "aux", "nul"} or re.fullmatch(r"(?:com|lpt)[0-9]", stem):
        return False
    return lower in {"telegram.exe", "updater.exe"} or lower.endswith(".dll")


def _validate_tree(path: Path, *, directory: bool):
    if _is_reparse(path):
        raise RecoveryError(f"拒绝 reparse point：{path}")
    if directory != path.is_dir():
        raise RecoveryError(f"路径类型异常：{path}")
    _trusted_acl(path)


def _canonical_install(path: Path) -> Path:
    _windows_only()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetFullPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_void_p]
    kernel.GetFullPathNameW.restype = ctypes.c_ulong
    buf = ctypes.create_unicode_buffer(32768)
    length = kernel.GetFullPathNameW(str(path), len(buf), buf, None)
    if not length or length >= len(buf): raise RecoveryError("安装路径无法规范化")
    value = buf.value.replace("/", "\\")
    if len(value) < 3 or value[1:3] != ":\\" or value.startswith("\\\\"):
        raise RecoveryError("安装路径必须是本地盘符路径")
    return Path(value.rstrip("\\"))


@contextmanager
def _directory_guard(install: Path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                   ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.GetFileInformationByHandle.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    class FileInfo(ctypes.Structure):
        _fields_ = [("attributes", ctypes.c_ulong), ("creation_low", ctypes.c_ulong),
                    ("creation_high", ctypes.c_ulong), ("access_low", ctypes.c_ulong),
                    ("access_high", ctypes.c_ulong), ("write_low", ctypes.c_ulong),
                    ("write_high", ctypes.c_ulong), ("volume", ctypes.c_ulong),
                    ("size_high", ctypes.c_ulong), ("size_low", ctypes.c_ulong),
                    ("links", ctypes.c_ulong), ("index_high", ctypes.c_ulong),
                    ("index_low", ctypes.c_ulong)]
    text = str(install)
    handles = []
    try:
        for end in range(3, len(text) + 1):
            if end != len(text) and text[end] != "\\": continue
            part = text[:end]
            # LIST_DIRECTORY makes the handle participate in rename sharing
            # checks; READ_CONTROL matches production HoldDirectoryPath.
            # Do not request DELETE: independent processes may hold guards
            # concurrently, while child creation/writes remain allowed.
            access = 0x80 | 0x1 | 0x20000  # FILE_READ_ATTRIBUTES | FILE_LIST_DIRECTORY | READ_CONTROL
            handle = kernel.CreateFileW(part, access, 0x1 | 0x2, None, 3,
                                        0x02000000 | 0x00200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise RecoveryError(f"无法锁定安装路径目录：{part}")
            handles.append(handle)
            info = FileInfo()
            if (not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
                    or not info.attributes & 0x10 or info.attributes & 0x400):
                raise RecoveryError(f"安装路径含非目录或 reparse point：{part}")
        yield install
    finally:
        for handle in reversed(handles): kernel.CloseHandle(handle)


@contextmanager
def _session_private_security():
    """Build the same protected user/SYSTEM/Administrators DACL as the client gate."""
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                           ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    advapi.GetTokenInformation.restype = ctypes.c_int
    advapi.ConvertStringSidToSidW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertStringSidToSidW.restype = ctypes.c_int
    advapi.SetEntriesInAclW.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_void_p)]
    advapi.SetEntriesInAclW.restype = ctypes.c_ulong
    advapi.InitializeSecurityDescriptor.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    advapi.InitializeSecurityDescriptor.restype = ctypes.c_int
    advapi.SetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    advapi.SetSecurityDescriptorDacl.restype = ctypes.c_int
    advapi.SetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.c_ushort, ctypes.c_ushort]
    advapi.SetSecurityDescriptorControl.restype = ctypes.c_int
    kernel.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
    kernel.OpenProcessToken.restype = ctypes.c_int
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    token = ctypes.c_void_p()
    converted = []
    acl = ctypes.c_void_p()
    try:
        if not kernel.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise RecoveryError("无法读取 client-session lease 当前用户")
        required = ctypes.c_ulong()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(required))
        token_data = ctypes.create_string_buffer(required.value)
        if not advapi.GetTokenInformation(token, 1, token_data, required, ctypes.byref(required)):
            raise RecoveryError("无法读取 client-session lease 当前用户")
        user_sid = ctypes.cast(token_data, ctypes.POINTER(ctypes.c_void_p))[0]
        for sid_text in ("S-1-5-18", "S-1-5-32-544"):
            sid = ctypes.c_void_p()
            if not advapi.ConvertStringSidToSidW(sid_text, ctypes.byref(sid)):
                raise RecoveryError("无法构造 client-session lease trusted principals")
            converted.append(sid)
        class Trustee(ctypes.Structure):
            _fields_ = [("multiple", ctypes.c_void_p), ("operation", ctypes.c_int),
                        ("form", ctypes.c_int), ("kind", ctypes.c_int), ("name", ctypes.c_void_p)]
        class ExplicitAccess(ctypes.Structure):
            _fields_ = [("permissions", ctypes.c_ulong), ("mode", ctypes.c_int),
                        ("inheritance", ctypes.c_ulong), ("trustee", Trustee)]
        entries = (ExplicitAccess * 3)()
        for index, sid in enumerate((user_sid, *converted)):
            entries[index] = ExplicitAccess(0x001F01FF, 2, 0,
                Trustee(None, 0, 0, 0, ctypes.cast(sid, ctypes.c_void_p)))
        if advapi.SetEntriesInAclW(3, entries, None, ctypes.byref(acl)) != 0:
            raise RecoveryError("无法建立 client-session.lock 私有 ACL")
        descriptor = ctypes.create_string_buffer(64)
        if (not advapi.InitializeSecurityDescriptor(descriptor, 1)
                or not advapi.SetSecurityDescriptorDacl(descriptor, 1, acl, 0)
                or not advapi.SetSecurityDescriptorControl(descriptor, 0x1000, 0x1000)):
            raise RecoveryError("无法建立 client-session.lock 受保护安全描述符")
        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("descriptor", ctypes.c_void_p),
                        ("inherit", ctypes.c_int)]
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), ctypes.cast(descriptor, ctypes.c_void_p), 0)
        yield attributes
    finally:
        if acl.value: kernel.LocalFree(acl)
        for sid in converted: kernel.LocalFree(sid)
        if token.value: kernel.CloseHandle(token)


class _ClientSessionLease:
    def __init__(self, install: Path, *, exclusive=True):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        self.kernel.CreateFileW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel.LockFileEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                           ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        self.kernel.LockFileEx.restype = ctypes.c_int
        self.kernel.UnlockFileEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
                                             ctypes.c_ulong, ctypes.c_void_p]
        self.kernel.UnlockFileEx.restype = ctypes.c_int
        self.directory_context = _directory_guard(install)
        self.directory_context.__enter__()
        self.handle = None
        self.overlapped = None
        self.locked = False
        try:
            path = install / "client-session.lock"
            with _session_private_security() as security:
                self.handle = self.kernel.CreateFileW(str(path), 0xC0000000, 0x3,
                    ctypes.byref(security), 1, 0x80 | 0x00200000, None)
            if self.handle == ctypes.c_void_p(-1).value:
                error = ctypes.get_last_error()
                if error != 80:  # ERROR_FILE_EXISTS
                    raise RecoveryError(f"client session lease denied (WinError {error})")
                self.handle = self.kernel.CreateFileW(str(path), 0xC0000000, 0x3,
                    None, 3, 0x80 | 0x00200000, None)
            if self.handle == ctypes.c_void_p(-1).value:
                raise RecoveryError(f"client session lease denied (WinError {ctypes.get_last_error()})")
            info = _file_info(self.kernel, self.handle)
            if info.attributes & (0x10 | 0x400):
                raise RecoveryError("client-session.lock 是目录或 reparse point")
            _trusted_acl(path, all_allowed=True)
            class Overlapped(ctypes.Structure):
                _fields_ = [("internal", ctypes.c_size_t), ("internal_high", ctypes.c_size_t),
                            ("offset", ctypes.c_ulong), ("offset_high", ctypes.c_ulong),
                            ("event", ctypes.c_void_p)]
            self.overlapped = Overlapped()
            flags = 0x1 | (0x2 if exclusive else 0)  # FAIL_IMMEDIATELY | EXCLUSIVE_LOCK
            if not self.kernel.LockFileEx(self.handle, flags, 0, 1, 0, ctypes.byref(self.overlapped)):
                error = ctypes.get_last_error()
                if error in (32, 33): raise RecoveryError("client session lease busy")
                raise RecoveryError(f"client session lease denied (WinError {error})")
            self.locked = True
        except Exception:
            self.close()
            raise

    def close(self):
        if self.handle not in (None, ctypes.c_void_p(-1).value):
            if self.locked and self.overlapped is not None:
                self.kernel.UnlockFileEx(self.handle, 0, 1, 0, ctypes.byref(self.overlapped))
                self.locked = False
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.directory_context is not None:
            self.directory_context.__exit__(None, None, None)
            self.directory_context = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def _file_info(kernel, handle):
    class FileInfo(ctypes.Structure):
        _fields_ = [("attributes", ctypes.c_ulong), ("creation_low", ctypes.c_ulong),
                    ("creation_high", ctypes.c_ulong), ("access_low", ctypes.c_ulong),
                    ("access_high", ctypes.c_ulong), ("write_low", ctypes.c_ulong),
                    ("write_high", ctypes.c_ulong), ("volume", ctypes.c_ulong),
                    ("size_high", ctypes.c_ulong), ("size_low", ctypes.c_ulong),
                    ("links", ctypes.c_ulong), ("index_high", ctypes.c_ulong),
                    ("index_low", ctypes.c_ulong)]
    info = FileInfo()
    kernel.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(FileInfo)]
    kernel.GetFileInformationByHandle.restype = ctypes.c_int
    if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise RecoveryError("无法验证 client-session.lock 句柄")
    return info


def _decode_journal(path: Path, backup_id: str):
    """Read only the production FGTXN01 v1 binary format."""
    if not path.exists():
        return "unknown (journal not archived)", "unknown previous version", ()
    _validate_tree(path, directory=False)
    raw = path.read_bytes()
    if not 32 <= len(raw) <= MAX_JOURNAL or raw[:8] != b"FGTXN01\0":
        raise RecoveryError(f"无效 journal：{path}")
    pos = 8
    def u32():
        nonlocal pos
        if pos + 4 > len(raw): raise RecoveryError("journal 截断")
        value = struct.unpack_from("<I", raw, pos)[0]; pos += 4; return value
    def u64():
        nonlocal pos
        if pos + 8 > len(raw): raise RecoveryError("journal 截断")
        value = struct.unpack_from("<Q", raw, pos)[0]; pos += 8; return value
    def text(maximum):
        nonlocal pos
        size = u32()
        if not size or size > maximum or pos + size > len(raw): raise RecoveryError("journal 文本长度无效")
        value = raw[pos:pos + size].decode("ascii"); pos += size; return value
    schema, state, target, channel = u32(), u32(), u64(), u32()
    record_id = text(64)
    if schema != 1 or state not in (1, 2) or not target or channel > 1 or record_id != backup_id:
        raise RecoveryError("journal 字段与 archive id 不匹配")
    lists = []
    for _ in range(2):
        count = u32()
        if count > 4096: raise RecoveryError("journal 文件数量超限")
        names = tuple(text(240) for _ in range(count))
        if len({n.lower() for n in names}) != len(names): raise RecoveryError("journal 文件名重复")
        lists.append(names)
    if raw[pos:] not in (b"", b"CMIT") or "Telegram.exe" not in lists[0]:
        raise RecoveryError("journal 结尾或程序清单无效")
    return str(target), "unknown previous version", lists[1]


def _archive(install: Path):
    install = _canonical_install(install)
    if not install.is_dir() or _is_reparse(install): raise RecoveryError("安装目录无效或为 reparse point")
    _validate_tree(install, directory=True)
    meta = install / ".fishgram-update"
    versions = meta / "versions"
    if not versions.exists(): raise RecoveryError("找不到 .fishgram-update/versions")
    _validate_tree(meta, directory=True); _validate_tree(versions, directory=True)
    records = []
    for entry in versions.iterdir():
        if not BACKUP_ID.fullmatch(entry.name): raise RecoveryError(f"versions 含未知条目，拒绝操作：{entry.name}")
        _validate_tree(entry, directory=True)
        files = []
        journal_path = entry / "journal.bin"
        for child in entry.iterdir():
            if child.name == "journal.bin":
                _validate_tree(child, directory=False)
                continue
            if not _safe_program_name(child.name) or child.name.lower() in ACCOUNT_NAMES:
                raise RecoveryError(f"archive 含非程序文件，拒绝操作：{child.name}")
            _validate_tree(child, directory=False)
            files.append(child.name)
        if not any(name.lower() == "telegram.exe" for name in files): raise RecoveryError(f"archive 缺少 Telegram.exe：{entry.name}")
        target, previous, _ = _decode_journal(journal_path, entry.name)
        records.append(Backup(entry.name, target, previous, tuple(sorted(files, key=str.lower))))
    records.sort(key=lambda item: item.backup_id, reverse=True)
    return install, meta, versions, tuple(records)


def list_backups(install_dir):
    install = _canonical_install(Path(install_dir))
    with _directory_guard(install):
        return _archive(install)[3]


class _InstallLock:
    def __init__(self, path: Path):
        _windows_only()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                                            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        self.kernel.CreateFileW.restype = ctypes.c_void_p
        self.kernel.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        self.kernel.GetFileAttributesW.restype = ctypes.c_ulong
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.kernel.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0x00200000, None)
        if self.handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            raise RecoveryError("安装锁忙" if error in (32, 33) else f"无法打开生产安装锁（WinError {error}）")
        attrs = self.kernel.GetFileAttributesW(str(path))
        if attrs == 0xFFFFFFFF or attrs & 0x40000000 or attrs & 0x400:
            self.close(); raise RecoveryError("install.lock 不是普通文件")
        class FileInfo(ctypes.Structure):
            _fields_ = [("attributes", ctypes.c_ulong), ("creation_low", ctypes.c_ulong),
                        ("creation_high", ctypes.c_ulong), ("access_low", ctypes.c_ulong),
                        ("access_high", ctypes.c_ulong), ("write_low", ctypes.c_ulong),
                        ("write_high", ctypes.c_ulong), ("volume", ctypes.c_ulong),
                        ("size_high", ctypes.c_ulong), ("size_low", ctypes.c_ulong),
                        ("links", ctypes.c_ulong), ("index_high", ctypes.c_ulong),
                        ("index_low", ctypes.c_ulong)]
        info = FileInfo()
        self.kernel.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(FileInfo)]
        self.kernel.GetFileInformationByHandle.restype = ctypes.c_int
        if not self.kernel.GetFileInformationByHandle(self.handle, ctypes.byref(info)) or info.attributes & (0x40000000 | 0x400):
            self.close(); raise RecoveryError("无法验证生产 install.lock 文件句柄")
    def close(self):
        if getattr(self, "handle", None) not in (None, ctypes.c_void_p(-1).value):
            self.kernel.CloseHandle(self.handle); self.handle = None
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def _target_process_running(exe: Path):
    _windows_only()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                    ("th32ProcessID", ctypes.c_ulong), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                    ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260)]
    kernel.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry)]
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                                   ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.QueryFullProcessImageNameW.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        raise RecoveryError("无法枚举进程，拒绝恢复")
    target = os.path.normcase(os.path.normpath(str(exe.resolve())))
    try:
        entry = ProcessEntry(); entry.dwSize = ctypes.sizeof(entry)
        okay = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while okay:
            if entry.szExeFile.lower() == "telegram.exe":
                process = kernel.OpenProcess(0x1000, 0, entry.th32ProcessID)
                if not process:
                    return True  # match production: inaccessible same-name process is busy
                try:
                    image = ctypes.create_unicode_buffer(32768)
                    length = ctypes.c_ulong(len(image))
                    if not kernel.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(length)):
                        return True
                    path = os.path.normcase(os.path.normpath(image.value[:length.value]))
                    if path == target:
                        return True
                finally:
                    kernel.CloseHandle(process)
            okay = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        error = ctypes.get_last_error()
        if error not in (0, 18):  # ERROR_NO_MORE_FILES
            raise RecoveryError("进程枚举未完整结束，拒绝恢复")
        return False
    finally:
        kernel.CloseHandle(snapshot)


def _copy_durable(source: Path, destination: Path, hook=None):
    if hook: hook(source, destination)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
        dst.flush(); os.fsync(dst.fileno())


def _move_write_through(source: Path, destination: Path, *, replace=False):
    _windows_only()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    kernel.MoveFileExW.restype = ctypes.c_int
    flags = 0x00000008 | (0x00000001 if replace else 0)  # WRITE_THROUGH | REPLACE_EXISTING
    if not kernel.MoveFileExW(str(source), str(destination), flags):
        raise OSError(ctypes.get_last_error(), "MoveFileExW WRITE_THROUGH failed", str(destination))


def _write_json(path: Path, value):
    temp = path.with_name(path.name + ".new")
    with temp.open("xb") as stream:
        stream.write(json.dumps(value, sort_keys=True).encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
    _move_write_through(temp, path, replace=path.exists())


def _refresh_latest_manual_backup(install: Path, manual: Path, files, copy_hook=None):
    latest = manual / "latestmanualbackup"
    previous = manual / "latestmanualbackup.previous"
    staging = manual / "latestmanualbackup.new"
    if previous.exists():
        _validate_tree(previous, directory=True)
        if latest.exists():
            _validate_tree(latest, directory=True)
            shutil.rmtree(previous)
        else:
            _move_write_through(previous, latest)
    if staging.exists():
        _validate_tree(staging, directory=True)
        shutil.rmtree(staging)
    staging.mkdir()
    _validate_tree(staging, directory=True)
    try:
        for name in files:
            _copy_durable(install / name, staging / name, copy_hook)
        if latest.exists():
            _validate_tree(latest, directory=True)
            _move_write_through(latest, previous)
        try:
            _move_write_through(staging, latest)
        except Exception:
            if previous.exists() and not latest.exists(): _move_write_through(previous, latest)
            raise
        if previous.exists(): shutil.rmtree(previous)
    except Exception:
        if staging.exists(): shutil.rmtree(staging)
        raise


def _recover_pending(install: Path, manual: Path):
    pending = manual / "pending"
    journal = pending / "transaction.json"
    if not pending.exists(): return
    _validate_tree(pending, directory=True)
    if not journal.is_file() or _is_reparse(journal): raise RecoveryError("持久事务缺失或不可信；停止以保留现场")
    _validate_tree(journal, directory=False)
    if journal.stat().st_size > MAX_JOURNAL: raise RecoveryError("持久事务日志过大")
    state = json.loads(journal.read_text(encoding="utf-8"))
    if state.get("phase") == "preparing":
        shutil.rmtree(pending)
        return
    if state.get("phase") != "applying": raise RecoveryError("持久事务阶段未知；保留现场")
    originals = pending / "original"
    _validate_tree(originals, directory=True)
    if not isinstance(state.get("files"), list) or not isinstance(state.get("target_files"), list):
        raise RecoveryError("持久事务文件清单无效")
    if any(not isinstance(name, str) or not _safe_program_name(name)
           for name in state["files"] + state["target_files"]):
        raise RecoveryError("持久事务含非法程序路径")
    for name in state["files"]:
        if not _safe_program_name(name): raise RecoveryError("事务含非法路径")
        saved = originals / name
        if not saved.is_file() or _is_reparse(saved): raise RecoveryError("事务原件缺失；停止")
    for name in state["files"]:
        restore = install / (name + ".restore")
        if restore.exists(): restore.unlink()
        _copy_durable(originals / name, restore)
        _move_write_through(restore, install / name, replace=True)
    for name in state["target_files"]:
        if name not in state["files"]:
            added = install / name
            if added.exists(): added.unlink()
    shutil.rmtree(pending)


def restore_backup(install_dir, backup_id, *, confirm_program_restore=None,
                   copy_hook=None, free_space_override=None, process_running=None):
    if confirm_program_restore != backup_id:
        raise RecoveryError("必须通过 --confirm-program-restore <backupid> 明确确认")
    if not BACKUP_ID.fullmatch(backup_id): raise RecoveryError("backupid 格式无效")
    install = _canonical_install(Path(install_dir))
    with _directory_guard(install):
        install, meta, versions, records = _archive(install)
        record = next((item for item in records if item.backup_id == backup_id), None)
        if record is None: raise RecoveryError("未找到指定程序备份")
        manual = meta / "manual"
        _validate_tree(meta, directory=True)
        lock_path = meta / "install.lock"
        if _is_reparse(lock_path): raise RecoveryError("install.lock 是 reparse point")
        with _InstallLock(lock_path), _ClientSessionLease(install, exclusive=True):
            install, meta, versions, records = _archive(install)
            record = next((item for item in records if item.backup_id == backup_id), None)
            if record is None: raise RecoveryError("指定程序备份在加锁后不可用")
            if not manual.exists(): manual.mkdir()
            _validate_tree(manual, directory=True)
            running = _target_process_running(install / "Telegram.exe") if process_running is None else process_running
            if running: raise RecoveryError("目标安装目录中的 Telegram.exe 仍在运行")
            _recover_pending(install, manual)
            running = _target_process_running(install / "Telegram.exe") if process_running is None else process_running
            if running: raise RecoveryError("目标安装目录中的 Telegram.exe 仍在运行")
            files = tuple(sorted((p.name for p in install.iterdir() if p.is_file() and _safe_program_name(p.name)), key=str.lower))
            if not any(name.lower() == "telegram.exe" for name in files): raise RecoveryError("安装目录缺少 Telegram.exe")
            for name in files:
                _validate_tree(install / name, directory=False)
                if install.joinpath(name).stat().st_file_attributes & 0x1:
                    raise RecoveryError(f"程序文件为只读属性，拒绝恢复：{name}")
            size = sum((install / n).stat().st_size for n in files) + sum((versions / backup_id / n).stat().st_size for n in record.files)
            free = shutil.disk_usage(install).free if free_space_override is None else free_space_override
            if free < size * 2 + 1024 * 1024: raise RecoveryError("磁盘空间不足，恢复前未修改程序文件")
            try:
                _refresh_latest_manual_backup(install, manual, files, copy_hook)
            except Exception as exc:
                raise RecoveryError(f"保存当前程序到 latestmanualbackup 失败：{exc}") from exc
            pending = manual / "pending"
            pending.mkdir()
            originals = pending / "original"; originals.mkdir()
            try:
                target_files = record.files
                _write_json(pending / "transaction.json", {"backup_id": backup_id,
                    "files": files, "target_files": target_files, "phase": "preparing"})
                for name in files: _copy_durable(install / name, originals / name, copy_hook)
                (pending / "transaction.json.new").unlink(missing_ok=True)
                _write_json(pending / "transaction.json", {"backup_id": backup_id,
                    "files": files, "target_files": target_files, "phase": "applying"})
                for name in record.files:
                    source = versions / backup_id / name
                    staged = install / (name + ".restore")
                    if staged.exists() or _is_reparse(staged):
                        raise RecoveryError(f"拒绝覆盖已有恢复暂存路径：{staged}")
                    _copy_durable(source, staged, copy_hook)
                    _move_write_through(staged, install / name, replace=True)
                target_names = {name.lower() for name in record.files}
                for name in files:
                    if name.lower() not in target_names: (install / name).unlink()
                shutil.rmtree(pending)
            except Exception as exc:
                try:
                    _recover_pending(install, manual)
                except Exception as rollback_exc:
                    raise RecoveryError(f"程序恢复失败，自动回滚也失败；持久事务保留在 {pending}: {rollback_exc}") from exc
                raise RecoveryError(f"程序恢复失败，已从持久事务恢复当前程序：{exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="FishGram 独立手动程序回退工具（不恢复账户数据）")
    parser.add_argument("--install", required=True, help="FishGram 安装目录")
    parser.add_argument("--confirm-program-restore", metavar="BACKUPID", help="显式确认恢复指定程序备份")
    args = parser.parse_args(argv)
    try:
        if args.confirm_program_restore:
            restore_backup(args.install, args.confirm_program_restore,
                           confirm_program_restore=args.confirm_program_restore)
            print(f"程序文件已恢复；账户数据未改动。最新当前程序备份：{args.install}\\.fishgram-update\\manual\\latestmanualbackup")
        else:
            for item in list_backups(args.install):
                print(f"{item.backup_id}\ttarget={item.target_version}\tprevious={item.previous_version}\t{', '.join(item.files)}")
            print("恢复账户数据需单独由用户选择；本工具不操作 tdata。")
        return 0
    except (RecoveryError, OSError, ValueError) as exc:
        print(f"拒绝操作：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
