"""Pack a trimmed Windows dependency snapshot, or restore it into a fresh checkout.

Unlike upstream win.yml's extension whitelist, retain sources and generated FFI
files. Only omit version-control data and disposable compiler/linker outputs.
The prepared Libraries and ThirdParty trees are never changed by packing.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import zipfile


FORMAT = b'fishgram-dependencies-v3'
# Leave 2 GB for Actions' outer tar/zstd framing and other repository caches.
MAX_ARCHIVE_BYTES = 8_000_000_000
ROOTS = ('Libraries', 'ThirdParty')
INTERMEDIATES = {'.obj', '.o', '.pdb', '.pch', '.ipch', '.ilk', '.idb', '.tlog'}


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & 0x400)  # Windows reparse point


def disposable(parts, is_directory=False):
    lower = tuple(part.lower() for part in parts)
    if any(part in ('.git', '__pycache__') for part in lower):
        return True
    # Installed Qt static plugins can reference individual object files.
    if any(part.startswith('objects-') for part in lower):
        return False
    # cache_keys and patches are preparation inputs, not compiler output.
    if any(part in ('cache_keys', 'patches') for part in lower):
        return False
    return not is_directory and Path(parts[-1]).suffix.lower() in INTERMEDIATES


def pack(root, archive, max_bytes=MAX_ARCHIVE_BYTES):
    root, archive = Path(root).resolve(), Path(archive).resolve()
    if not 0 < max_bytes <= MAX_ARCHIVE_BYTES:
        raise ValueError('Archive budget must be between 1 and 8,000,000,000 bytes.')
    for name in ROOTS:
        dependency = root / name
        if is_link(dependency) or not dependency.is_dir():
            raise ValueError('Dependency root must be a real directory: ' + name)
        if dependency == archive or dependency in archive.parents:
            raise ValueError('Archive must be outside dependency directories.')
    dependency_roots = tuple((root / name).resolve() for name in ROOTS)
    archive.parent.mkdir(parents=True, exist_ok=True)
    count = kept_bytes = omitted_bytes = 0
    # Exclusive creation avoids replacing a cache that a caller is still using.
    with archive.open('xb') as output:
        try:
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED,
                                 compresslevel=1, allowZip64=True) as bundle:
                bundle.comment = FORMAT
                for name in ROOTS:
                    for base, directories, files in os.walk(root / name):
                        base = Path(base)
                        for directory in directories[:]:
                            path = base / directory
                            relative = path.relative_to(root)
                            if disposable(relative.parts, is_directory=True):
                                directories.remove(directory)
                            elif is_link(path):
                                raise ValueError('Linked dependency directory: ' + str(relative))
                        for filename in files:
                            path = base / filename
                            relative = path.relative_to(root)
                            if disposable(relative.parts):
                                omitted_bytes += path.lstat().st_size
                                continue
                            source = path
                            if is_link(path):
                                source = path.resolve(strict=True)
                                if not any(source.is_relative_to(folder) for folder in dependency_roots):
                                    raise ValueError('Dependency file link leaves dependency directories: ' + str(relative))
                            if not source.is_file():
                                raise ValueError('Dependency must be a regular file: ' + str(relative))
                            # Rustup's cargo proxies are in-tree file links. Store
                            # their target bytes as regular files in the snapshot.
                            bundle.write(source, relative.as_posix())
                            kept_bytes += source.stat().st_size
                            count += 1
                            if output.tell() > max_bytes:
                                raise ValueError('Compressed dependency archive exceeds budget; skip upload.')
            size = output.tell()  # Includes the ZIP directory, not an estimate.
            if size > max_bytes:
                raise ValueError('Compressed dependency archive exceeds budget; skip upload.')
        except BaseException:
            output.close()
            archive.unlink()
            raise
    return dict(files=count, retained_bytes=kept_bytes,
                omitted_intermediate_bytes=omitted_bytes,
                archive_bytes=size, budget_bytes=max_bytes)


def member_path(name):
    # ZIP paths are POSIX; also reject Windows aliases, ADS and traversal.
    parts = name.rstrip('/').split('/')
    if (len(parts) < 2 or parts[0] not in ROOTS or
            any(not part or part in ('.', '..') or part.endswith((' ', '.'))
                or PureWindowsPath(part).is_reserved() or any(ord(c) < 32 for c in part)
                or any(c in part for c in '\\:<>"|?*') for part in parts)):
        raise ValueError('Invalid dependency archive path: ' + name)
    return PurePosixPath(*parts)


def restore(root, archive):
    root, archive = Path(root).resolve(), Path(archive).resolve()
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('Dependency archive exceeds upload budget.')
    for name in ROOTS:
        if os.path.lexists(root / name):
            raise ValueError('Restore requires absent dependency directories: ' + name)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.comment != FORMAT:
            raise ValueError('Unknown dependency archive format.')
        members = []
        names = set()
        for info in bundle.infolist():
            relative = member_path(info.filename)
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError('Dependency archive contains a link or special file.')
            folded = str(relative).casefold()
            if folded in names:
                raise ValueError('Duplicate dependency archive path.')
            names.add(folded)
            members.append((info, relative))
        if {relative.parts[0] for _, relative in members} != set(ROOTS):
            raise ValueError('Dependency archive must contain both dependency trees.')
        for info, relative in members:
            destination = root.joinpath(*relative.parts)
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, destination.open('xb') as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
                if os.name != 'nt' and info.external_attr >> 16:
                    destination.chmod(info.external_attr >> 16 & 0o777)
    return dict(files=sum(not info.is_dir() for info, _ in members),
                restored_bytes=sum(info.file_size for info, _ in members))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('pack', 'restore'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    result = (pack if args.command == 'pack' else restore)(args.root, args.archive)
    print(json.dumps(result, sort_keys=True), flush=True)
    if args.github_output and args.command == 'pack':
        with args.github_output.open('a', encoding='utf-8', newline='\n') as output:
            output.write('ready=true\n')
            output.write(f"archive_bytes={result['archive_bytes']}\n")


if __name__ == '__main__':
    main()
